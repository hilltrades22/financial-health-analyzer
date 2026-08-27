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
from typing import Any, Optional

import httpx

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "").strip()

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

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


class SecClient:
    """Async client wrapping the three SEC EDGAR endpoints we use."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient()

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
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR submissions API: {exc}") from exc
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
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR filing index: {exc}") from exc
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR filing index returned {resp.status_code}")
        return resp.json()

    async def get_filing_file(self, cik: int, accession_nodash: str, filename: str) -> bytes:
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{filename}"
        headers = _headers().copy()
        headers["Host"] = "www.sec.gov"
        try:
            resp = await self._client.get(url, headers=headers, timeout=httpx.Timeout(30.0, connect=10.0))
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR filing file: {exc}") from exc
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR filing file returned {resp.status_code}")
        return resp.content

    async def get_company_facts(self, cik: int) -> dict[str, Any]:
        cik10 = str(cik).zfill(10)
        url = COMPANY_FACTS_URL.format(cik10=cik10)
        headers = _headers().copy()
        headers["Host"] = "data.sec.gov"
        try:
            resp = await self._client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except httpx.HTTPError as exc:
            raise SecUnavailableError(f"Could not reach SEC EDGAR company facts API: {exc}") from exc
        if resp.status_code == 404:
            raise TickerNotFoundError(f"No XBRL company facts found for CIK {cik10}")
        if resp.status_code >= 500:
            raise SecUnavailableError(f"SEC EDGAR company facts API returned {resp.status_code}")
        if resp.status_code != 200:
            raise SecUnavailableError(f"SEC EDGAR company facts API returned unexpected {resp.status_code}")
        return resp.json()
