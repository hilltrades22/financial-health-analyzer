import httpx
import pytest
import respx

from backend.app.main import app, _cache


@pytest.fixture(autouse=True)
def clear_cache():
    _cache.clear()
    yield
    _cache.clear()


TICKER_MAP = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}


def series(tag_end_vals, form="10-K"):
    return {"units": {"USD": [
        {"end": e, "val": v, "form": form, "filed": f"{e[:4]}-11-01", "fy": int(e[:4]), "fp": "FY", "accn": f"acc-{e}"}
        for e, v in tag_end_vals
    ]}}


def dur_series(tag_start_end_vals, form="10-K"):
    return {"units": {"USD": [
        {"start": s, "end": e, "val": v, "form": form, "filed": f"{e[:4]}-11-01", "fy": int(e[:4]), "fp": "FY", "accn": f"acc-{e}"}
        for s, e, v in tag_start_end_vals
    ]}}


FAKE_FACTS = {
    "entityName": "Apple Inc.",
    "facts": {"us-gaap": {
        "CashAndCashEquivalentsAtCarryingValue": series([("2024-09-30", 3e10), ("2025-09-30", 3.2e10)], form="10-Q"),
        "Liabilities": series([("2024-09-30", 2.7e11), ("2025-09-30", 2.8e11)]),
        "StockholdersEquity": series([("2024-09-30", 6e10), ("2025-09-30", 7e10)]),
        "AssetsCurrent": series([("2024-09-30", 1.2e11), ("2025-09-30", 1.3e11)]),
        "LiabilitiesCurrent": series([("2024-09-30", 1.0e11), ("2025-09-30", 9e10)]),
        "Assets": series([("2024-09-30", 3.3e11), ("2025-09-30", 3.5e11)]),
        "RetainedEarningsAccumulatedDeficit": series([("2023-09-30", 5e9), ("2024-09-30", 8e9), ("2025-09-30", 1e10)]),
        "LongTermDebtNoncurrent": series([("2024-09-30", 9e10), ("2025-09-30", 8.5e10)]),
        "CommonStockSharesOutstanding": series([("2024-09-30", 1.5e10), ("2025-09-30", 1.48e10)]),
        "Revenues": dur_series([("2023-10-01", "2024-09-30", 3.8e11), ("2024-10-01", "2025-09-30", 4.0e11)]),
        "NetIncomeLoss": dur_series([("2023-10-01", "2024-09-30", 9e10), ("2024-10-01", "2025-09-30", 1.0e11)]),
        "GrossProfit": dur_series([("2023-10-01", "2024-09-30", 1.6e11), ("2024-10-01", "2025-09-30", 1.75e11)]),
        "OperatingIncomeLoss": dur_series([("2023-10-01", "2024-09-30", 1.0e11), ("2024-10-01", "2025-09-30", 1.1e11)]),
        "InterestExpense": dur_series([("2024-10-01", "2025-09-30", 3e9)]),
        "NetCashProvidedByUsedInOperatingActivities": dur_series([("2023-10-01", "2024-09-30", 1.05e11), ("2024-10-01", "2025-09-30", 1.15e11)]),
        "PaymentsToAcquirePropertyPlantAndEquipment": dur_series([("2024-10-01", "2025-09-30", -1.0e10)]),
        "PaymentsForRepurchaseOfCommonStock": dur_series([("2024-10-01", "2025-09-30", -8e10)]),
    }},
}


FAKE_YAHOO_CHART_5D = {
    "chart": {"result": [{
        "meta": {"regularMarketPrice": 224.5, "regularMarketTime": 1755720000,
                  "chartPreviousClose": 222.0, "currency": "USD", "fullExchangeName": "NASDAQ"},
        "timestamp": [], "indicators": {"quote": [{}]},
    }]}
}

FAKE_YAHOO_CHART_HISTORY = {
    "chart": {"result": [{
        "meta": {"regularMarketPrice": 224.5},
        "timestamp": [1750000000, 1750086400, 1750172800],
        "indicators": {"quote": [{
            "open": [210.0, 212.0, 215.0], "high": [213.0, 214.0, 218.0],
            "low": [208.0, 211.0, 214.0], "close": [212.0, 213.5, 217.0],
            "volume": [1000000, 1200000, 900000],
        }]},
    }]}
}

FAKE_YAHOO_KEY_STATS = {
    "quoteSummary": {"result": [{"defaultKeyStatistics": {"forwardPE": {"raw": 27.4}}}]}
}


def _mock_market_data():
    respx.get(url__regex=r"https://query1\.finance\.yahoo\.com/v8/finance/chart/AAPL(\?.*)?$").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=FAKE_YAHOO_CHART_HISTORY if "range=1y" in str(request.url) or "range=3mo" in str(request.url)
            or "range=6mo" in str(request.url) or "ytd" in str(request.url) or "3y" in str(request.url)
            or "5y" in str(request.url) or "10y" in str(request.url) or "max" in str(request.url)
            else FAKE_YAHOO_CHART_5D,
        )
    )
    respx.get(url__regex=r"https://query1\.finance\.yahoo\.com/v10/finance/quoteSummary/AAPL.*").mock(
        return_value=httpx.Response(200, json=FAKE_YAHOO_KEY_STATS)
    )
    respx.get(url__regex=r"https://stooq\.com/.*").mock(return_value=httpx.Response(200, text="Symbol,Date,Time,Open,High,Low,Close,Volume\naapl.us,2026-08-21,16:00:00,220,225,219,224.5,5000000\n"))


@pytest.mark.asyncio
@respx.mock
async def test_full_forge_payload_smoke():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=httpx.Response(200, json=TICKER_MAP))
    respx.get(url__regex=r"https://data\.sec\.gov/api/xbrl/companyconcept/.*").mock(
        return_value=httpx.Response(404))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(return_value=httpx.Response(200, json=FAKE_FACTS))
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(return_value=httpx.Response(200, json={"sicDescription": "Electronic Computers", "sic": "3571"}))
    _mock_market_data()

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/AAPL")
    assert resp.status_code == 200
    data = resp.json()

    assert data["forge"]["forge_score"] is not None
    assert data["piotroski"]["scored_out_of"] > 0
    assert data["altman"]["available"] is True
    assert data["valuation"]["market_cap"]["available"] is True
    assert data["valuation"]["pe_ratio"]["available"] is True
    assert data["valuation"]["forward_pe_ratio"]["available"] is True
    assert data["valuation"]["price_source_short"] == "Yahoo Finance"
    assert len(data["timeline"]) >= 1
    assert data["industry"] == "Electronic Computers"

    # Grading system
    grading = data["grading"]
    assert grading["letter_grade"] in {"A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F"}
    assert grading["health_classification"] in {"Exceptional", "Strong", "Healthy", "Watch", "Weak", "Critical"}
    for key in ("financial_health", "financial_quality", "valuation", "risk"):
        p = grading["pillars"][key]
        assert "letter_grade" in p and "contribution_pct" in p and "key_reasons" in p

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp2 = await client.get("/api/compare?tickers=AAPL")
    assert resp2.status_code == 200
    assert len(resp2.json()["companies"]) == 1


@pytest.mark.asyncio
@respx.mock
async def test_price_history_ranges():
    _mock_market_data()
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        for rng in ["3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "MAX"]:
            resp = await client.get(f"/api/price-history/AAPL?range={rng}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["available"] is True, f"range {rng} should be available: {body}"
            assert len(body["points"]) == 3
            assert body["points"][0]["close"] == 212.0


@pytest.mark.asyncio
@respx.mock
async def test_price_history_unknown_range():
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/price-history/AAPL?range=BOGUS")
    assert resp.status_code == 200
    assert resp.json()["available"] is False
