"""
End-to-end API test for a foreign private issuer (IFRS / 20-F / non-USD).

This drives the real FastAPI route with SEC and market-data responses shaped
like TSM's actual ones, so the whole pipeline - normalization, scoring,
quality, risk, timeline, story, grading, classification - is exercised for a
filer that previously produced an empty analysis.
"""
import httpx
import pytest
import respx

from backend.app import sec_client
from backend.app.main import app, _cache
from .test_foreign_filers import IFRS_FACTS

TICKER_MAP = {"0": {"cik_str": 1046179, "ticker": "TSM",
                    "title": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"}}

SUBMISSIONS = {
    "name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD",
    "sic": "3674",
    "sicDescription": "Semiconductors & Related Devices",
    "exchanges": ["NYSE", "OTC"],
    "fiscalYearEnd": "1231",
    "entityType": "other",
    "addresses": {"business": {"stateOrCountry": "F5"}},
    "filings": {"recent": {"form": [], "accessionNumber": [], "primaryDocument": [], "filingDate": []}},
}

CHART = {"chart": {"result": [{"meta": {"regularMarketPrice": 413.71, "currency": "USD",
                                        "fullExchangeName": "NYSE", "regularMarketTime": 1756000000,
                                        "chartPreviousClose": 410.0},
                               "timestamp": [1735603200],
                               "indicators": {"quote": [{"close": [413.71], "open": [410.0],
                                                          "high": [415.0], "low": [409.0],
                                                          "volume": [1000000]}]}}], "error": None}}


@pytest.fixture(autouse=True)
def clear_cache():
    # The ticker -> CIK map is cached module-wide for 6h. Other test modules
    # populate it with their own (AAPL-only) fixture, so it must be reset here
    # or this test resolves TSM against a stale map and 404s.
    def _reset():
        _cache.clear()
        sec_client._ticker_cache._data = None
        sec_client._ticker_cache._fetched_at = 0.0
    _reset()
    yield
    _reset()


@pytest.mark.asyncio
@respx.mock
async def test_foreign_private_issuer_returns_a_real_analysis():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=httpx.Response(200, json=TICKER_MAP))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0001046179.json").mock(
        return_value=httpx.Response(200, json=IFRS_FACTS))
    respx.get("https://data.sec.gov/submissions/CIK0001046179.json").mock(
        return_value=httpx.Response(200, json=SUBMISSIONS))
    respx.get(url__regex=r"https://query1\.finance\.yahoo\.com/v8/finance/chart/TSM.*").mock(
        return_value=httpx.Response(200, json=CHART))
    respx.get(url__regex=r"https://query1\.finance\.yahoo\.com/v10/.*").mock(return_value=httpx.Response(401))
    respx.get(url__regex=r"https://stooq\.com/.*").mock(return_value=httpx.Response(404))
    respx.get(url__regex=r"https://www\.sec\.gov/Archives/.*").mock(return_value=httpx.Response(404))

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/TSM")

    assert resp.status_code == 200
    data = resp.json()

    # The core failure this fixes: real financial data, not an empty shell.
    assert data["latest_quarter"]["period_end"] is not None
    assert data["latest_annual"]["period_end"] == "2024-12-31"
    assert data["quarterly_facts"]["total_liabilities"]["available"] is True
    assert data["quarterly_facts"]["cash_and_equivalents"]["available"] is True
    assert data["annual_facts"]["operating_cash_flow"]["available"] is True
    assert len(data["timeline"]) > 0
    assert data["score"]["overall_score"] is not None
    assert data["financial_story"]

    # Classification is populated from SEC's own metadata.
    assert data["sector"] == "Manufacturing"
    assert data["industry"] == "Semiconductors & Related Devices"
    assert data["exchange"] == "NYSE, OTC"
    assert data["classification"]["peer_group"] == "semiconductor"

    # Currency discipline: statements are in TWD, the quote is in USD.
    assert data["reporting_currency"] == "TWD"
    assert data["valuation"]["price_currency"] == "USD"
    assert "currency_note" in data["valuation"]
    # P/E can be computed because the filer itself reported USD net income;
    # P/B cannot, because equity was reported only in TWD - and it must be
    # marked unavailable rather than converted.
    assert data["valuation"]["pe_ratio"]["available"] is True
    assert data["valuation"]["pb_ratio"]["available"] is False

    # Nothing in the payload crashed on missing analyst/segment data.
    assert data["business_mix"]["available"] is False
