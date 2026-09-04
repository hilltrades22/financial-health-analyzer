"""
Thin async client for SEC EDGAR / data.sec.gov.

The browser never talks to SEC directly - this module is the only place
that makes outbound requests to sec.gov / data.sec.gov. It requires a
real contact User-Agent, configured via the SEC_USER_AGENT environment
variable (never hardcoded), per SEC's fair-access guidelines:
https://www.sec.gov/os/webmaster-faq#developers
"""
from __future__ import annotations

import os
import time
import asyncio
from collections import OrderedDict
from typing import Any, Optional

import httpx

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
COMPANY_CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/{taxonomy}/{concept}.json"

# Financial facts change only when a filing is published, so they can be
# cached far longer than a market price. The cache is bounded: SEC data is a
# convenience here, not a store of record, and an unbounded dict on a small
# instance is a memory leak waiting to happen.
_CONCEPT_TTL_SECONDS = int(os.environ.get("SEC_CONCEPT_CACHE_TTL", str(6 * 3600)))
_CONCEPT_CACHE_MAX = int(os.environ.get("SEC_CONCEPT_CACHE_MAX", "4000"))

_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


class SecUnavailableError(Exception):
    """Raised when SEC EDGAR cannot be reached or returns a server error."""


class TickerNotFoundError(Exception):
    """Raised when a ticker does not resolve to a CIK in SEC's mapping."""


def _headers() -> dict[str, str]:
    if not SEC_USER_AGENT:
        # We still send *a* User-Agent so requests don't get an immediate
        # reject, but real deployments must set SEC_USER_AGENT.
        ua = "FinancialHealthAnalyzer (SEC_USER_AGENT not configured)"
    else:
        ua = SEC_USER_AGENT
    return {
        "User-Agent": ua,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }


class _TickerCache:
    """In-memory cache of the ticker -> CIK map, refreshed periodically."""

    def __init__(self, ttl_seconds: int = 6 * 3600):
        self._ttl = ttl_seconds
        self._data: Optional[dict[str, dict[str, Any]]] = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        now = time.time()
        if self._data is not None and (now - self._fetched_at) < self._ttl:
            return self._data
        async with self._lock:
            now = time.time()
            if self._data is not None and (now - self._fetched_at) < self._ttl:
                return self._data
            headers = _headers().copy()
            headers["Host"] = "www.sec.gov"
            try:
                await _rate_limiter.acquire()
                resp = await client.get(TICKER_MAP_URL, headers=headers, timeout=_DEFAULT_TIMEOUT)
            except httpx.HTTPError as exc:
                raise SecUnavailableError(f"Could not reach SEC EDGAR ticker map: {exc}") from exc
            if resp.status_code >= 500:
                raise SecUnavailableError(f"SEC EDGAR ticker map returned {resp.status_code}")
            if resp.status_code != 200:
                raise SecUnavailableError(f"SEC EDGAR ticker map returned unexpected {resp.status_code}")
            raw = resp.json()
            by_ticker: dict[str, dict[str, Any]] = {}
            for entry in raw.values():
                t = str(entry.get("ticker", "")).upper()
                if t:
                    by_ticker[t] = {
                        "cik": int(entry["cik_str"]),
                        "ticker": t,
                        "title": entry.get("title", ""),
                    }
            self._data = by_ticker
            self._fetched_at = now
            return self._data


_ticker_cache = _TickerCache()


class _RateLimiter:
    """Caps outbound request rate to SEC.

    SEC's fair-access policy asks automated clients to stay within a modest
    requests-per-second rate and to identify themselves. Exceeding it gets an
    IP temporarily blocked, which shows up as requests that hang or fail for
    reasons that look like application bugs. Every SEC call in this module
    goes through here, so adding a feature that makes more requests (insider
    Form 4 filings, for example) cannot accidentally breach the limit.
    """

    def __init__(self, requests_per_second: float = 5.0):
        self._min_interval = 1.0 / requests_per_second
        # An asyncio.Lock binds to the loop it was first awaited on, so it is
        # created lazily per running loop rather than at import time. In
        # production there is only ever one loop; this also keeps the limiter
        # usable from any short-lived loop (tests, scripts, workers).
        self._locks: "dict[Any, asyncio.Lock]" = {}
        self._last = 0.0

    def _lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[loop] = lock
            # Drop locks for loops that have gone away so this cannot grow.
            for dead in [l for l in self._locks if l.is_closed()]:
                self._locks.pop(dead, None)
        return lock

    async def acquire(self) -> None:
        async with self._lock_for_loop():
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# Shared across every SecClient instance and every request in the process.
_rate_limiter = _RateLimiter(float(os.environ.get("SEC_REQUESTS_PER_SECOND", "5")))


class _ConceptCache:
    """Bounded TTL cache for per-concept payloads.

    A miss is cached too (as None): a filer that does not report a concept
    will never start reporting it mid-session, and re-asking SEC for a known
    404 on every analysis is exactly the kind of avoidable traffic the rate
    limiter exists to prevent.
    """

    def __init__(self, ttl: int, max_entries: int):
        self._ttl = ttl
        self._max = max_entries
        self._data: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()

    def get(self, key: tuple) -> tuple[bool, Any]:
        entry = self._data.get(key)
        if entry is None:
            return False, None
        stamp, value = entry
        if (time.time() - stamp) > self._ttl:
            self._data.pop(key, None)
            return False, None
        self._data.move_to_end(key)
        return True, value

    def put(self, key: tuple, value: Any) -> None:
        self._data[key] = (time.time(), value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._data), "max_entries": self._max, "ttl_seconds": self._ttl}


_concept_cache = _ConceptCache(_CONCEPT_TTL_SECONDS, _CONCEPT_CACHE_MAX)
# In-flight requests, so two analyses needing the same concept at the same
# moment produce one SEC request rather than two.
_inflight: dict[tuple, "asyncio.Future"] = {}


class SecClient:
    """Async client wrapping the SEC EDGAR endpoints we use."""

    def __init__(self) -> None:
        # Bound connection reuse so a burst of concurrent calls (insider
        # filings, for instance) cannot open an unreasonable number of
        # sockets against SEC.
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=3))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve_ticker(self, ticker: str) -> dict[str, Any]:
        ticker = ticker.strip().upper()
        if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
            raise TickerNotFoundError(f"'{ticker}' is not a valid ticker symbol")
        mapping = await _ticker_cache.get(self._client)
        entry = mapping.get(ticker)
        if entry is None:
            raise TickerNotFoundError(f"No SEC-registered company found for ticker '{ticker}'")
        return entry

    async def get_submissions(self, cik: int) -> dict[str, Any]:
        cik10 = str(cik).zfill(10)
        url = SUBMISSIONS_URL.format(cik10=cik10)
        headers = _headers().copy()
        headers["Host"] = "data.sec.gov"
        try:
            await _rate_limiter.acquire()
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR submissions API: {exc}") from exc
        if resp.status_code == 429:
            raise SecUnavailableError(
                "SEC EDGAR is rate-limiting this service (HTTP 429). SEC asks automated clients to stay within "
                "a modest request rate; please wait a moment and try again.")
        if resp.status_code == 404:
            raise TickerNotFoundError(f"No SEC filings found for CIK {cik10}")
        if resp.status_code >= 500:
            raise SecUnavailableError(f"SEC EDGAR submissions API returned {resp.status_code}")
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR submissions API returned unexpected {resp.status_code}")
        return resp.json()

    async def get_filing_index(self, cik: int, accession_nodash: str) -> dict[str, Any]:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/index.json"
        headers = _headers().copy()
        headers["Host"] = "www.sec.gov"
        try:
            await _rate_limiter.acquire()
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR filing index: {exc}") from exc
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR filing index returned {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SecUnavailableError(f"SEC EDGAR filing index returned non-JSON content: {exc}") from exc

    async def get_filing_file(self, cik: int, accession_nodash: str, filename: str) -> bytes:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"
        headers = _headers().copy()
        headers["Host"] = "www.sec.gov"
        try:
            await _rate_limiter.acquire()
            resp = await self._client.get(url, headers=headers, timeout=httpx.Timeout(30.0, connect=10.0))
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR filing file: {exc}") from exc
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR filing file returned {resp.status_code}")
        return resp.content

    async def get_company_concept(self, cik: int, taxonomy: str, concept: str) -> Optional[dict[str, Any]]:
        """One concept's full reported history.

        Returns None when the filer does not report it - a normal outcome,
        not an error. Results (including misses) are cached, and concurrent
        callers asking for the same concept share a single request.
        """
        key = (cik, taxonomy, concept)
        hit, cached = _concept_cache.get(key)
        if hit:
            return cached

        existing = _inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)

        loop = asyncio.get_running_loop()
        future: "asyncio.Future" = loop.create_future()
        _inflight[key] = future
        try:
            result = await self._fetch_company_concept(cik, taxonomy, concept)
            _concept_cache.put(key, result)
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                # The caller that raised is the one reporting this failure. If
                # no other coroutine happened to be waiting on the shared
                # future, nobody retrieves its exception and asyncio logs a
                # spurious "Future exception was never retrieved" for every
                # concept - dozens of lines per throttled analysis. Marking it
                # retrieved here keeps the real error on the caller's path and
                # the noise out of the logs.
                future.add_done_callback(
                    lambda f: None if f.cancelled() else f.exception())
            raise
        finally:
            _inflight.pop(key, None)

    async def _fetch_company_concept(self, cik: int, taxonomy: str,
                                     concept: str) -> Optional[dict[str, Any]]:
        cik10 = str(cik).zfill(10)
        url = COMPANY_CONCEPT_URL.format(cik10=cik10, taxonomy=taxonomy, concept=concept)
        headers = _headers().copy()
        headers["Host"] = "data.sec.gov"
        try:
            await _rate_limiter.acquire()
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR company concept API: {exc}") from exc
        if resp.status_code == 404:
            return None  # this filer simply does not report this concept
        if resp.status_code == 429:
            raise SecUnavailableError(
                "SEC EDGAR is rate-limiting this service (HTTP 429). SEC asks automated clients to stay within "
                "a modest request rate; please wait a moment and try again.")
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR company concept API returned {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SecUnavailableError(f"SEC EDGAR company concept API returned non-JSON content: {exc}") from exc

    @staticmethod
    def cache_stats() -> dict[str, Any]:
        return {**_concept_cache.stats(), "in_flight": len(_inflight)}

    async def get_company_facts(self, cik: int) -> dict[str, Any]:
        cik10 = str(cik).zfill(10)
        url = COMPANY_FACTS_URL.format(cik10=cik10)
        headers = _headers().copy()
        headers["Host"] = "data.sec.gov"
        try:
            await _rate_limiter.acquire()
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR company facts API: {exc}") from exc
        if resp.status_code == 429:
            raise SecUnavailableError(
                "SEC EDGAR is rate-limiting this service (HTTP 429). SEC asks automated clients to stay within "
                "a modest request rate; please wait a moment and try again.")
        if resp.status_code == 404:
            raise TickerNotFoundError(f"No XBRL company facts found for CIK {cik10}")
        if resp.status_code >= 500:
            raise SecUnavailableError(f"SEC EDGAR company facts API returned {resp.status_code}")
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR company facts API returned unexpected {resp.status_code}")
        # A large filer's company-facts payload can be tens of megabytes.
        # json.loads on that blocks the event loop for seconds, which on a
        # single-worker deployment makes every other request - including
        # /api/health - hang until it finishes. Parse it off the loop.
        return await asyncio.to_thread(resp.json)
