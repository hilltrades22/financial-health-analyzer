import httpx
import pytest
import respx

from backend.app.main import app, _cache


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

FAKE_COMPANY_FACTS = {
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "CashAndCashEquivalentsAtCarryingValue": {
                "units": {
                    "USD": [
                        {"end": "2026-06-30", "val": 30000000000, "form": "10-Q", "filed": "2026-07-25", "fy": 2026, "fp": "Q3", "accn": "0001"},
                    ]
                }
            },
            "Liabilities": {
                "units": {
                    "USD": [
                        {"end": "2026-06-30", "val": 280000000000, "form": "10-Q", "filed": "2026-07-25", "fy": 2026, "fp": "Q3", "accn": "0001"},
                    ]
                }
            },
            "StockholdersEquity": {
                "units": {
                    "USD": [
                        {"end": "2026-06-30", "val": 70000000000, "form": "10-Q", "filed": "2026-07-25", "fy": 2026, "fp": "Q3", "accn": "0001"},
                    ]
                }
            },
            "RetainedEarningsAccumulatedDeficit": {
                "units": {
                    "USD": [
                        {"end": "2025-09-30", "val": 10000000000, "form": "10-K", "filed": "2025-11-01", "fy": 2025, "fp": "FY", "accn": "0002"},
                        {"end": "2024-09-30", "val": 8000000000, "form": "10-K", "filed": "2024-11-01", "fy": 2024, "fp": "FY", "accn": "0003"},
                    ]
                }
            },
        }
    },
}


@pytest.mark.asyncio
async def test_health_returns_200():
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_invalid_ticker_returns_404():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=TICKER_MAP)
    )
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/INVALIDTICKER123")
    assert resp.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_valid_ticker_returns_normalized_data():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=TICKER_MAP)
    )
    # No concept resolves, so the analysis must fall back to the full
    # company-facts payload - the path these tests exercise.
    respx.get(url__regex=r"https://data\.sec\.gov/api/xbrl/companyconcept/.*").mock(
        return_value=httpx.Response(404))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json=FAKE_COMPANY_FACTS)
    )
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(
        return_value=httpx.Response(200, json={"name": "Apple Inc."})
    )
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/AAPL")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticker"] == "AAPL"
    assert data["company_name"] == "Apple Inc."
    assert data["score"]["overall_score"] is not None
    assert "retained_earnings_growth" in data["score"]["passed_rules"]


@pytest.mark.asyncio
@respx.mock
async def test_sec_unavailable_returns_503():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(
        return_value=httpx.Response(200, json=TICKER_MAP)
    )
    # No concept resolves, so the analysis must fall back to the full
    # company-facts payload - the path these tests exercise.
    respx.get(url__regex=r"https://data\.sec\.gov/api/xbrl/companyconcept/.*").mock(
        return_value=httpx.Response(404))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(
        return_value=httpx.Response(503)
    )
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/AAPL")
    assert resp.status_code == 503
