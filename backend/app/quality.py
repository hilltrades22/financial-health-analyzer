"""
Financial Quality metrics (profitability, growth, cash generation) and the
Piotroski F-Score. All figures come from SEC XBRL annual (10-K) facts.
Any criterion that can't be computed because SEC data is missing is
excluded from both the Piotroski numerator and denominator - it is never
silently scored as a pass or a fail.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import NOT_REPORTED
from .normalize import annual_series, fact_from_entry

# Concept lists live in normalize.py so us-gaap and IFRS coverage is
# maintained in exactly one place (see normalize.TAXONOMIES).
from .normalize import (  # noqa: E402
    ASSETS_CURRENT_TAGS,
    ASSETS_TAGS,
    COST_OF_REVENUE_TAGS,
    DILUTED_EPS_TAGS,
    GROSS_PROFIT_TAGS,
    LIABILITIES_CURRENT_TAGS,
    NET_INCOME_TAGS,
    REVENUE_TAGS,
)

SHARES_OUTSTANDING_TAGS = ["CommonStockSharesOutstanding", "NumberOfSharesOutstanding"]


def _fmt_usd(v: Optional[float]) -> str:
    if v is None:
        return NOT_REPORTED
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1_000_000_000:
        return f"{sign}${a / 1_000_000_000:,.2f}B"
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:,.2f}M"
    return f"{sign}${a:,.0f}"


def _pct(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v * 100, 2)


def _two_year_annual(company_facts: dict[str, Any], tags: list[str], duration: bool):
    series = annual_series(company_facts, tags, duration=duration)
    cur = fact_from_entry(series[0]) if len(series) > 0 else None
    prior = fact_from_entry(series[1]) if len(series) > 1 else None
    return cur, prior


def compute_financial_quality(company_facts: dict[str, Any], annual_snapshot) -> dict[str, Any]:
    """Revenue, margins, ROE/ROA, FCF - current + prior year for growth."""
    revenue, revenue_prior = _two_year_annual(company_facts, REVENUE_TAGS, duration=True)
    net_income, net_income_prior = _two_year_annual(company_facts, NET_INCOME_TAGS, duration=True)
    assets, assets_prior = _two_year_annual(company_facts, ASSETS_TAGS, duration=False)
    equity, _ = _two_year_annual(company_facts, ["StockholdersEquity"], duration=False)

    ocf = annual_snapshot.operating_cash_flow
    capex = annual_snapshot.capital_expenditures
    op_income = annual_snapshot.operating_income

    metrics: dict[str, Any] = {}

    metrics["revenue"] = {
        "value": revenue.value if revenue and revenue.available else None,
        "display": _fmt_usd(revenue.value) if revenue and revenue.available else NOT_REPORTED,
    }
    if revenue and revenue.available and revenue_prior and revenue_prior.available and revenue_prior.value:
        growth = (revenue.value - revenue_prior.value) / abs(revenue_prior.value)
        metrics["revenue_growth"] = {"value": _pct(growth), "display": f"{_pct(growth):+.2f}%"}
    else:
        metrics["revenue_growth"] = {"value": None, "display": NOT_REPORTED}

    metrics["net_income"] = {
        "value": net_income.value if net_income and net_income.available else None,
        "display": _fmt_usd(net_income.value) if net_income and net_income.available else NOT_REPORTED,
    }
    metrics["operating_income"] = {
        "value": op_income.value if op_income.available else None,
        "display": _fmt_usd(op_income.value) if op_income.available else NOT_REPORTED,
    }

    if op_income.available and revenue and revenue.available and revenue.value:
        opm = op_income.value / revenue.value
        metrics["operating_margin"] = {"value": _pct(opm), "display": f"{_pct(opm):.2f}%"}
    else:
        metrics["operating_margin"] = {"value": None, "display": NOT_REPORTED}

    if net_income and net_income.available and revenue and revenue.available and revenue.value:
        nm = net_income.value / revenue.value
        metrics["net_margin"] = {"value": _pct(nm), "display": f"{_pct(nm):.2f}%"}
    else:
        metrics["net_margin"] = {"value": None, "display": NOT_REPORTED}

    if net_income and net_income.available and equity and equity.available and equity.value:
        roe = net_income.value / equity.value
        metrics["roe"] = {"value": _pct(roe), "display": f"{_pct(roe):.2f}%"}
    else:
        metrics["roe"] = {"value": None, "display": NOT_REPORTED}

    if net_income and net_income.available and assets and assets.available and assets.value:
        roa = net_income.value / assets.value
        metrics["roa"] = {"value": _pct(roa), "display": f"{_pct(roa):.2f}%"}
    else:
        metrics["roa"] = {"value": None, "display": NOT_REPORTED}

    metrics["operating_cash_flow"] = {
        "value": ocf.value if ocf.available else None,
        "display": _fmt_usd(ocf.value) if ocf.available else NOT_REPORTED,
    }

    if ocf.available and capex.available:
        fcf = ocf.value - abs(capex.value)
        metrics["free_cash_flow"] = {"value": fcf, "display": _fmt_usd(fcf)}
        if revenue and revenue.available and revenue.value:
            fcfm = fcf / revenue.value
            metrics["fcf_margin"] = {"value": _pct(fcfm), "display": f"{_pct(fcfm):.2f}%"}
        else:
            metrics["fcf_margin"] = {"value": None, "display": NOT_REPORTED}
    else:
        metrics["free_cash_flow"] = {"value": None, "display": NOT_REPORTED}
        metrics["fcf_margin"] = {"value": None, "display": NOT_REPORTED}

    return metrics


def compute_piotroski_f_score(company_facts: dict[str, Any]) -> dict[str, Any]:
    """9-point Piotroski F-Score. Each criterion is scored PASS/FAIL/UNAVAILABLE
    from two consecutive fiscal years of SEC XBRL data. Unavailable criteria
    are excluded from both the score and the denominator shown to the user."""
    net_income, net_income_prior = _two_year_annual(company_facts, NET_INCOME_TAGS, duration=True)
    assets, assets_prior = _two_year_annual(company_facts, ASSETS_TAGS, duration=False)
    assets_2prior_series = annual_series(company_facts, ASSETS_TAGS, duration=False)
    assets_2prior = fact_from_entry(assets_2prior_series[2]) if len(assets_2prior_series) > 2 else None
    ocf, ocf_prior = _two_year_annual(company_facts, ["NetCashProvidedByUsedInOperatingActivities",
                                                        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"], duration=True)
    lt_debt, lt_debt_prior = _two_year_annual(company_facts, ["LongTermDebtNoncurrent", "LongTermDebt"], duration=False)
    cur_assets, cur_assets_prior = _two_year_annual(company_facts, ASSETS_CURRENT_TAGS, duration=False)
    cur_liab, cur_liab_prior = _two_year_annual(company_facts, LIABILITIES_CURRENT_TAGS, duration=False)
    shares, shares_prior = _two_year_annual(company_facts, SHARES_OUTSTANDING_TAGS, duration=False)
    revenue, revenue_prior = _two_year_annual(company_facts, REVENUE_TAGS, duration=True)
    gross_profit, gross_profit_prior = _two_year_annual(company_facts, GROSS_PROFIT_TAGS, duration=True)

    def avail(*facts):
        return all(f is not None and f.available for f in facts)

    criteria = []

    def add(key: str, label: str, ok: Optional[bool], detail: str):
        criteria.append({
            "key": key, "label": label,
            "status": "UNAVAILABLE" if ok is None else ("PASS" if ok else "FAIL"),
            "detail": detail,
        })

    # 1. Positive ROA (net income / assets > 0)
    if avail(net_income, assets):
        roa = net_income.value / assets.value if assets.value else None
        add("positive_roa", "Positive Return on Assets", roa is not None and roa > 0,
            f"Net income {_fmt_usd(net_income.value)} on assets {_fmt_usd(assets.value)}")
    else:
        add("positive_roa", "Positive Return on Assets", None, NOT_REPORTED)

    # 2. Positive operating cash flow
    if avail(ocf):
        add("positive_cfo", "Positive Operating Cash Flow", ocf.value > 0, _fmt_usd(ocf.value))
    else:
        add("positive_cfo", "Positive Operating Cash Flow", None, NOT_REPORTED)

    # 3. ROA improved YoY
    if avail(net_income, net_income_prior, assets, assets_prior) and assets.value and assets_prior.value:
        roa_cur = net_income.value / assets.value
        roa_prior = net_income_prior.value / assets_prior.value
        add("roa_improved", "Return on Assets Improved", roa_cur > roa_prior,
            f"ROA {roa_cur*100:.2f}% vs {roa_prior*100:.2f}% prior year")
    else:
        add("roa_improved", "Return on Assets Improved", None, NOT_REPORTED)

    # 4. CFO > Net Income (earnings quality)
    if avail(ocf, net_income):
        add("cfo_exceeds_ni", "Cash Flow Exceeds Net Income", ocf.value > net_income.value,
            f"CFO {_fmt_usd(ocf.value)} vs Net Income {_fmt_usd(net_income.value)}")
    else:
        add("cfo_exceeds_ni", "Cash Flow Exceeds Net Income", None, NOT_REPORTED)

    # 5. Leverage (LT debt / assets) decreased
    if avail(lt_debt, lt_debt_prior, assets, assets_prior) and assets.value and assets_prior.value:
        lev_cur = lt_debt.value / assets.value
        lev_prior = lt_debt_prior.value / assets_prior.value
        add("leverage_decreased", "Long-Term Leverage Decreased", lev_cur < lev_prior,
            f"LT debt/assets {lev_cur*100:.2f}% vs {lev_prior*100:.2f}% prior year")
    else:
        add("leverage_decreased", "Long-Term Leverage Decreased", None, NOT_REPORTED)

    # 6. Current ratio improved
    if avail(cur_assets, cur_assets_prior, cur_liab, cur_liab_prior) and cur_liab.value and cur_liab_prior.value:
        cr_cur = cur_assets.value / cur_liab.value
        cr_prior = cur_assets_prior.value / cur_liab_prior.value
        add("current_ratio_improved", "Current Ratio Improved", cr_cur > cr_prior,
            f"Current ratio {cr_cur:.2f}x vs {cr_prior:.2f}x prior year")
    else:
        add("current_ratio_improved", "Current Ratio Improved", None, NOT_REPORTED)

    # 7. No new shares issued (no dilution)
    if avail(shares, shares_prior):
        add("no_dilution", "No New Share Dilution", shares.value <= shares_prior.value,
            f"Shares outstanding {shares.value:,.0f} vs {shares_prior.value:,.0f} prior year")
    else:
        add("no_dilution", "No New Share Dilution", None, NOT_REPORTED)

    # 8. Gross margin improved
    gp_cur = gross_profit if avail(gross_profit) else None
    gp_prior = gross_profit_prior if avail(gross_profit_prior) else None
    if avail(gp_cur, gp_prior, revenue, revenue_prior) and revenue.value and revenue_prior.value:
        gm_cur = gp_cur.value / revenue.value
        gm_prior = gp_prior.value / revenue_prior.value
        add("gross_margin_improved", "Gross Margin Improved", gm_cur > gm_prior,
            f"Gross margin {gm_cur*100:.2f}% vs {gm_prior*100:.2f}% prior year")
    else:
        add("gross_margin_improved", "Gross Margin Improved", None, NOT_REPORTED)

    # 9. Asset turnover improved
    if avail(revenue, revenue_prior, assets, assets_prior) and assets.value and assets_prior.value:
        at_cur = revenue.value / assets.value
        at_prior = revenue_prior.value / assets_prior.value
        add("asset_turnover_improved", "Asset Turnover Improved", at_cur > at_prior,
            f"Asset turnover {at_cur:.3f}x vs {at_prior:.3f}x prior year")
    else:
        add("asset_turnover_improved", "Asset Turnover Improved", None, NOT_REPORTED)

    scored = [c for c in criteria if c["status"] != "UNAVAILABLE"]
    passed = [c for c in scored if c["status"] == "PASS"]
    return {
        "score": len(passed),
        "scored_out_of": len(scored),
        "max_possible": 9,
        "criteria": criteria,
        "unavailable_count": 9 - len(scored),
    }
