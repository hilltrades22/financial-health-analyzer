"""
Derived values and timeline robustness.

Two real defects found on the live site:
  * Amazon reports total assets and total equity but never tags a total
    "Liabilities" line, so total liabilities showed as unavailable.
  * Realty Income returned a completely empty financial timeline, because
    the timeline anchored every period on retained earnings - which a REIT
    distributing its income may not tag at all.
"""
from backend.app import normalize as N
from backend.app.history import build_financial_timeline


def _inst(vals, form="10-K"):
    return {"units": {"USD": [{"end": e, "val": v, "form": form, "filed": e,
                                "fy": int(e[:4]), "fp": "FY", "accn": "a"} for e, v in vals]}}


def _dur(vals, form="10-K"):
    return {"units": {"USD": [{"start": s, "end": e, "val": v, "form": form, "filed": e,
                                "fy": int(e[:4]), "fp": "FY", "accn": "a"} for s, e, v in vals]}}


# --- Derived total liabilities ------------------------------------------

def test_total_liabilities_derived_when_filer_does_not_report_it():
    facts = {"facts": {"us-gaap": {
        "Assets": _inst([("2026-06-30", 7.0e11)], form="10-Q"),
        "StockholdersEquity": _inst([("2026-06-30", 3.2e11)], form="10-Q"),
    }}}
    cf = N.build_company_financials("T", 1, "Test", facts)
    tl = cf.quarterly.total_liabilities
    assert tl.available is True
    assert tl.value == 7.0e11 - 3.2e11
    assert tl.derived is True
    assert "Assets = Liabilities + Equity" in tl.derivation
    assert tl.concept.startswith("derived:")


def test_reported_total_liabilities_always_wins_over_derivation():
    facts = {"facts": {"us-gaap": {
        "Assets": _inst([("2026-06-30", 7.0e11)], form="10-Q"),
        "StockholdersEquity": _inst([("2026-06-30", 3.2e11)], form="10-Q"),
        "Liabilities": _inst([("2026-06-30", 3.9e11)], form="10-Q"),
    }}}
    cf = N.build_company_financials("T", 1, "Test", facts)
    tl = cf.quarterly.total_liabilities
    assert tl.value == 3.9e11        # the filer's own number, not 3.8e11
    assert tl.derived is False


def test_no_derivation_across_mismatched_period_ends():
    """Assets from one date minus equity from another is not a real figure."""
    facts = {"facts": {"us-gaap": {
        "Assets": _inst([("2026-06-30", 7.0e11)], form="10-Q"),
        "StockholdersEquity": _inst([("2025-12-31", 3.2e11)], form="10-K"),
    }}}
    cf = N.build_company_financials("T", 1, "Test", facts)
    assert cf.quarterly.total_liabilities.available is False


def test_no_derivation_without_both_inputs():
    facts = {"facts": {"us-gaap": {"Assets": _inst([("2026-06-30", 7.0e11)], form="10-Q")}}}
    cf = N.build_company_financials("T", 1, "Test", facts)
    assert cf.quarterly.total_liabilities.available is False


# --- Timeline no longer depends on one concept ---------------------------

def test_timeline_built_for_filer_that_reports_no_retained_earnings():
    facts = {"facts": {"us-gaap": {
        "Revenues": _dur([("2023-01-01", "2023-12-31", 4.0e9), ("2024-01-01", "2024-12-31", 5.0e9)]),
        "NetIncomeLoss": _dur([("2023-01-01", "2023-12-31", 8.7e8), ("2024-01-01", "2024-12-31", 9.0e8)]),
        "Assets": _inst([("2023-12-31", 4.7e10), ("2024-12-31", 5.0e10)]),
        "StockholdersEquity": _inst([("2023-12-31", 2.5e10), ("2024-12-31", 2.6e10)]),
    }}}
    tl = build_financial_timeline(facts)
    assert len(tl) == 2
    assert [r["period_end"] for r in tl] == ["2023-12-31", "2024-12-31"]   # oldest first, unchanged
    assert [r["fiscal_year"] for r in tl] == [2023, 2024]
    assert tl[-1]["revenue"] == 5.0e9
    assert tl[-1]["retained_earnings"] is None      # absent, not invented


def test_timeline_still_works_when_retained_earnings_is_reported():
    facts = {"facts": {"us-gaap": {
        "RetainedEarningsAccumulatedDeficit": _inst([("2023-12-31", 5e9), ("2024-12-31", 8e9)]),
        "Revenues": _dur([("2023-01-01", "2023-12-31", 4.0e9), ("2024-01-01", "2024-12-31", 5.0e9)]),
    }}}
    tl = build_financial_timeline(facts)
    assert len(tl) == 2
    assert tl[-1]["retained_earnings"] == 8e9


def test_timeline_empty_only_when_nothing_is_reported():
    assert build_financial_timeline({"facts": {"us-gaap": {}}}) == []


# --- Bounded analysis ----------------------------------------------------

def test_analysis_timeout_returns_a_clear_error_not_a_hang():
    """A filer whose SEC dataset is too large to process must produce an
    explicit, honest error rather than hanging the service."""
    import asyncio
    import httpx
    import pytest
    from backend.app import main as main_mod

    async def _run():
        async def _never_finishes(*args, **kwargs):
            await asyncio.sleep(30)

        original_timeout = main_mod._ANALYSIS_TIMEOUT_S
        original_analyze = main_mod._analyze_ticker
        main_mod._ANALYSIS_TIMEOUT_S = 0.05
        main_mod._analyze_ticker = _never_finishes
        try:
            async with httpx.AsyncClient(app=main_mod.app, base_url="http://t") as client:
                return await client.get("/api/analyze/SLOW")
        finally:
            main_mod._ANALYSIS_TIMEOUT_S = original_timeout
            main_mod._analyze_ticker = original_analyze

    resp = asyncio.run(_run())
    assert resp.status_code == 504
    detail = resp.json()["detail"]
    assert "did not complete" in detail
    assert "No partial or estimated result" in detail


def test_sec_requests_are_rate_limited():
    """SEC asks automated clients to stay within a modest request rate.
    Every call must pass through the limiter, so adding a feature that makes
    more requests cannot silently breach it."""
    import asyncio
    import time as _time
    from backend.app.sec_client import _RateLimiter

    async def _run():
        limiter = _RateLimiter(requests_per_second=20.0)   # 50ms apart
        start = _time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        return _time.monotonic() - start

    elapsed = asyncio.run(_run())
    assert elapsed >= 0.15 - 0.02      # 3 gaps of 50ms between 4 requests


def test_sec_rate_limit_response_is_reported_as_such():
    """A 429 from SEC must be surfaced as throttling, not as a missing
    company or a generic application failure."""
    import asyncio
    import httpx
    import pytest
    import respx
    from backend.app.sec_client import SecClient, SecUnavailableError

    @respx.mock
    async def _run():
        respx.get(url__regex=r"https://data\.sec\.gov/api/xbrl/companyfacts/.*").mock(
            return_value=httpx.Response(429))
        client = SecClient()
        try:
            with pytest.raises(SecUnavailableError) as exc:
                await client.get_company_facts(320193)
            return str(exc.value)
        finally:
            await client.aclose()

    message = asyncio.run(_run())
    assert "rate-limiting" in message
    assert "429" in message
