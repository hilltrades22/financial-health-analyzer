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


@pytest.mark.asyncio
@respx.mock
async def test_full_forge_payload_smoke():
    respx.get("https://www.sec.gov/files/company_tickers.json").mock(return_value=httpx.Response(200, json=TICKER_MAP))
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").mock(return_value=httpx.Response(200, json=FAKE_FACTS))
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").mock(return_value=httpx.Response(200, json={"sicDescription": "Electronic Computers", "sic": "3571"}))
    respx.get(url__regex=r"https://stooq\.com/.*").mock(return_value=httpx.Response(200, text="Symbol,Date,Time,Open,High,Low,Close,Volume\naapl.us,2026-08-21,16:00:00,220,225,219,224.5,5000000\n"))

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.get("/api/analyze/AAPL")
    assert resp.status_code == 200
    data = resp.json()

    assert data["forge"]["forge_score"] is not None
    assert data["piotroski"]["scored_out_of"] > 0
    assert data["altman"]["available"] is True
    assert data["valuation"]["market_cap"]["available"] is True
    assert data["valuation"]["pe_ratio"]["available"] is True
    assert len(data["timeline"]) >= 1
    assert data["industry"] == "Electronic Computers"

    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        resp2 = await client.get("/api/compare?tickers=AAPL")
    assert resp2.status_code == 200
    assert len(resp2.json()["companies"]) == 1
