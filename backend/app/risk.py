"""
Risk Center calculations: Altman Z-Score (with explicit model selection)
plus the supporting risk metrics (net debt, interest coverage, lease
exposure, liquidity, equity trend). Uses only SEC XBRL data; the
market-value variant of Altman Z is only computed when real market-cap
data (from market_data.py) is supplied - it is never estimated.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import NOT_REPORTED
from .normalize import annual_series, fact_from_entry


def _two_year(company_facts: dict[str, Any], tags: list[str], duration: bool):
    series = annual_series(company_facts, tags, duration=duration)
    cur = fact_from_entry(series[0]) if len(series) > 0 else None
    return cur


def compute_altman_z_score(company_facts: dict[str, Any], market_cap: Optional[float]) -> dict[str, Any]:
    assets = _two_year(company_facts, ["Assets"], duration=False)
    cur_assets = _two_year(company_facts, ["AssetsCurrent"], duration=False)
    cur_liab = _two_year(company_facts, ["LiabilitiesCurrent"], duration=False)
    retained_earnings = _two_year(company_facts, ["RetainedEarningsAccumulatedDeficit"], duration=False)
    op_income = _two_year(company_facts, ["OperatingIncomeLoss"], duration=True)
    total_liabilities = _two_year(company_facts, ["Liabilities"], duration=False)
    equity = _two_year(company_facts, ["StockholdersEquity"], duration=False)
    revenue = _two_year(company_facts, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                          "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], duration=True)

    def ok(*facts):
        return all(f is not None and f.available for f in facts)

    if not ok(assets, total_liabilities) or not assets.value or not total_liabilities.value:
        return {
            "model": "Altman Z″-Score (non-manufacturer / private-firm variant)",
            "available": False,
            "reason": "Total assets and/or total liabilities not available from standardized SEC data.",
        }

    working_capital = None
    if ok(cur_assets, cur_liab):
        working_capital = cur_assets.value - cur_liab.value

    x1 = (working_capital / assets.value) if working_capital is not None else 0.0
    x2 = (retained_earnings.value / assets.value) if ok(retained_earnings) else 0.0
    x3 = (op_income.value / assets.value) if ok(op_income) else 0.0
    x4 = (equity.value / total_liabilities.value) if ok(equity) and total_liabilities.value else 0.0

    missing = []
    if working_capital is None:
        missing.append("current assets/liabilities (working capital)")
    if not ok(retained_earnings):
        missing.append("retained earnings")
    if not ok(op_income):
        missing.append("operating income")
    if not ok(equity):
        missing.append("total equity")

    z_double_prime = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

    if z_double_prime > 2.6:
        zone, zone_label = "SAFE", "Safe Zone"
    elif z_double_prime >= 1.1:
        zone, zone_label = "GREY", "Grey Zone"
    else:
        zone, zone_label = "DISTRESS", "Distress Zone"

    result = {
        "model": "Altman Z″-Score (non-manufacturer / private-firm variant, no market value required)",
        "model_note": (
            "This variant is used because it does not require classifying the company as a "
            "manufacturer and works without assuming a specific industry structure. It is more "
            "broadly appropriate across sectors than the original 1968 Z-Score, which was "
            "calibrated on public manufacturers."
        ),
        "available": True,
        "score": round(z_double_prime, 2),
        "zone": zone,
        "zone_label": zone_label,
        "components": {
            "x1_working_capital_to_assets": round(x1, 4),
            "x2_retained_earnings_to_assets": round(x2, 4),
            "x3_ebit_to_assets": round(x3, 4),
            "x4_equity_to_liabilities": round(x4, 4),
        },
        "missing_components": missing,
    }

    # Classic (market-value) Altman Z-Score, only if we have real market cap.
    if market_cap and ok(total_liabilities) and total_liabilities.value and ok(revenue) and ok(assets) and assets.value:
        x1c = x1
        x2c = x2
        x3c = x3
        x4c = market_cap / total_liabilities.value
        x5c = revenue.value / assets.value
        z_classic = 1.2 * x1c + 1.4 * x2c + 3.3 * x3c + 0.6 * x4c + 1.0 * x5c
        if z_classic > 2.99:
            czone, czone_label = "SAFE", "Safe Zone"
        elif z_classic >= 1.81:
            czone, czone_label = "GREY", "Grey Zone"
        else:
            czone, czone_label = "DISTRESS", "Distress Zone"
        result["classic_model"] = {
            "model": "Altman Z-Score (original, public-company market-value variant)",
            "model_note": "Shown because live market capitalization is available; most appropriate for publicly traded manufacturers.",
            "score": round(z_classic, 2),
            "zone": czone,
            "zone_label": czone_label,
        }

    return result
