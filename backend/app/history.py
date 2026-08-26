"""
Financial health scored over time. For each fiscal year found in SEC's
10-K XBRL history, we build the same normalized snapshot used for the
current-period score (models.QuarterlySnapshot/AnnualSnapshot) from that
year's own year-end balance sheet and annual flow figures, then run it
through the identical scoring engine used for "today". Nothing here is
estimated - a year with insufficient XBRL coverage is simply omitted
rather than filled in.
"""
from __future__ import annotations

from typing import Any

from . import normalize as N
from .models import AnnualSnapshot, CompanyFinancials, QuarterlySnapshot
from .scoring import score_company


def _series_by_period(company_facts: dict[str, Any], tags: list[str], duration: bool) -> dict[str, Any]:
    series = N.annual_series(company_facts, tags, duration=duration)
    return {e.get("end"): N.fact_from_entry(e) for e in series}


def build_historical_scores(company_facts: dict[str, Any], max_years: int = 10) -> list[dict[str, Any]]:
    re_series = N.annual_series(company_facts, N.RETAINED_EARNINGS_TAGS, duration=False)
    if not re_series:
        return []
    fiscal_years = [e.get("end") for e in re_series][:max_years]
    re_by_period = {e.get("end"): N.fact_from_entry(e) for e in re_series}

    cash_by = _series_by_period(company_facts, N.CASH_TAGS, False)
    st_inv_by = _series_by_period(company_facts, N.SHORT_TERM_INVESTMENT_TAGS, False)
    lt_inv_by = _series_by_period(company_facts, N.LONG_TERM_INVESTMENT_TAGS, False)
    st_debt_by = _series_by_period(company_facts, N.DEBT_CURRENT_AGGREGATE_TAGS, False)
    lt_debt_by = _series_by_period(company_facts, N.LONG_TERM_DEBT_TAGS, False)
    liab_by = _series_by_period(company_facts, N.TOTAL_LIABILITIES_TAGS, False)
    equity_by = _series_by_period(company_facts, N.TOTAL_EQUITY_TAGS, False)
    treasury_by = _series_by_period(company_facts, N.TREASURY_STOCK_TAGS, False)
    preferred_by = _series_by_period(company_facts, N.PREFERRED_STOCK_TAGS, False)
    op_lease_c_by = _series_by_period(company_facts, N.OPERATING_LEASE_CURRENT_TAGS, False)
    op_lease_nc_by = _series_by_period(company_facts, N.OPERATING_LEASE_NONCURRENT_TAGS, False)
    fin_lease_c_by = _series_by_period(company_facts, N.FINANCE_LEASE_CURRENT_TAGS, False)
    fin_lease_nc_by = _series_by_period(company_facts, N.FINANCE_LEASE_NONCURRENT_TAGS, False)

    ocf_by = _series_by_period(company_facts, N.OPERATING_CASH_FLOW_TAGS, True)
    capex_by = _series_by_period(company_facts, N.CAPEX_TAGS, True)
    op_income_by = _series_by_period(company_facts, N.OPERATING_INCOME_TAGS, True)
    interest_by = _series_by_period(company_facts, N.INTEREST_EXPENSE_TAGS, True)
    repurch_by = _series_by_period(company_facts, N.REPURCHASE_TAGS, True)

    from .models import FactValue

    def get(d: dict, key: str) -> "FactValue":
        return d.get(key, FactValue.missing())

    results = []
    for i, period_end in enumerate(fiscal_years):
        q = QuarterlySnapshot(
            period_end=period_end, form="10-K",
            cash_and_equivalents=get(cash_by, period_end),
            short_term_investments=get(st_inv_by, period_end),
            long_term_investments=get(lt_inv_by, period_end),
            short_term_debt=get(st_debt_by, period_end),
            long_term_debt=get(lt_debt_by, period_end),
            total_liabilities=get(liab_by, period_end),
            total_equity=get(equity_by, period_end),
            treasury_stock=get(treasury_by, period_end),
            preferred_stock=get(preferred_by, period_end),
            retained_earnings=re_by_period.get(period_end, FactValue.missing()),
            operating_lease_current=get(op_lease_c_by, period_end),
            operating_lease_noncurrent=get(op_lease_nc_by, period_end),
            finance_lease_current=get(fin_lease_c_by, period_end),
            finance_lease_noncurrent=get(fin_lease_nc_by, period_end),
        )
        prior_period = fiscal_years[i + 1] if i + 1 < len(fiscal_years) else None
        a = AnnualSnapshot(
            fiscal_year=re_series[i].get("fy"),
            period_end=period_end,
            filed=re_series[i].get("filed"),
            retained_earnings=re_by_period.get(period_end, FactValue.missing()),
            prior_retained_earnings=re_by_period.get(prior_period, FactValue.missing()) if prior_period else FactValue.missing(),
            operating_cash_flow=get(ocf_by, period_end),
            capital_expenditures=get(capex_by, period_end),
            operating_income=get(op_income_by, period_end),
            interest_expense=get(interest_by, period_end),
            repurchases_of_stock=get(repurch_by, period_end),
            treasury_stock=get(treasury_by, period_end),
        )
        cf = CompanyFinancials(ticker="", cik=0, name="", quarterly=q, annual=a)
        score = score_company(cf)
        if score["points_available_scored"] < 20:
            continue  # too little data this year to produce a meaningful score
        results.append({
            "fiscal_year": a.fiscal_year,
            "period_end": period_end,
            "overall_score": score["overall_score"],
            "label": score["label"],
            "points_earned": score["points_earned"],
            "points_available_scored": score["points_available_scored"],
        })

    results.sort(key=lambda r: r["period_end"] or "")
    return results


def build_financial_timeline(company_facts: dict[str, Any], max_years: int = 10, frequency: str = "annual") -> list[dict[str, Any]]:
    """Per-period series for the Financial Timeline chart: revenue, net
    income, cash, total debt, equity, free cash flow, retained earnings,
    buybacks, operating cash flow, capex, EPS, and margins. Any period
    missing a given figure simply omits that field - nothing is
    interpolated or estimated.

    frequency="annual" (default) uses one point per fiscal year (10-K).
    frequency="quarterly" uses one point per individual fiscal quarter
    (10-Q duration facts only - cumulative YTD facts are excluded so a
    "quarterly" revenue series is genuinely single-quarter, not a mix)."""
    revenue_tags = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
    net_income_tags = ["NetIncomeLoss", "ProfitLoss"]
    gross_profit_tags = ["GrossProfit"]

    series_fn = N.quarterly_series if frequency == "quarterly" else N.annual_series
    max_points = max_years * 4 if frequency == "quarterly" else max_years

    re_series = series_fn(company_facts, N.RETAINED_EARNINGS_TAGS, duration=False)
    if not re_series:
        return []
    periods = [e.get("end") for e in re_series][:max_points]

    def by_period(tags, duration):
        s = series_fn(company_facts, tags, duration=duration)
        return {e.get("end"): N.fact_from_entry(e) for e in s}

    revenue_by = by_period(revenue_tags, True)
    net_income_by = by_period(net_income_tags, True)
    gross_profit_by = by_period(gross_profit_tags, True)
    cash_by = by_period(N.CASH_TAGS, False)
    st_debt_by = by_period(N.DEBT_CURRENT_AGGREGATE_TAGS, False)
    lt_debt_by = by_period(N.LONG_TERM_DEBT_TAGS, False)
    equity_by = by_period(N.TOTAL_EQUITY_TAGS, False)
    ocf_by = by_period(N.OPERATING_CASH_FLOW_TAGS, True)
    capex_by = by_period(N.CAPEX_TAGS, True)
    re_by = {e.get("end"): N.fact_from_entry(e) for e in re_series}
    buyback_by = by_period(N.REPURCHASE_TAGS, True)
    shares_series = N.annual_shares_outstanding_series(company_facts)
    # Shares outstanding is a single point-in-time figure per filing, not a
    # per-period series, so EPS uses the shares count nearest each period's
    # own filing rather than pretending a quarterly shares series exists.
    shares_sorted = sorted(shares_series, key=lambda f: f.period_end or "")

    def nearest_shares(period_end):
        best = None
        for sf in shares_sorted:
            if sf.period_end and sf.period_end <= period_end:
                best = sf
        return best.value if best else (shares_sorted[0].value if shares_sorted else None)

    def v(d, key):
        f = d.get(key)
        return f.value if f and f.available else None

    timeline = []
    for i, period_end in enumerate(periods):
        cash = v(cash_by, period_end)
        st_debt = v(st_debt_by, period_end)
        lt_debt = v(lt_debt_by, period_end)
        total_debt = None
        if st_debt is not None or lt_debt is not None:
            total_debt = (st_debt or 0) + (lt_debt or 0)
        ocf = v(ocf_by, period_end)
        capex = v(capex_by, period_end)
        fcf = (ocf - abs(capex)) if (ocf is not None and capex is not None) else None
        revenue = v(revenue_by, period_end)
        net_income = v(net_income_by, period_end)
        gross_profit = v(gross_profit_by, period_end)
        shares = nearest_shares(period_end)
        eps = (net_income / shares) if (net_income is not None and shares) else None
        net_margin = (net_income / revenue * 100) if (net_income is not None and revenue) else None
        gross_margin = (gross_profit / revenue * 100) if (gross_profit is not None and revenue) else None
        timeline.append({
            "fiscal_year": re_series[i].get("fy"),
            "fiscal_period": re_series[i].get("fp"),
            "period_end": period_end,
            "revenue": revenue,
            "net_income": net_income,
            "cash": cash,
            "total_debt": total_debt,
            "equity": v(equity_by, period_end),
            "free_cash_flow": fcf,
            "retained_earnings": v(re_by, period_end),
            "buybacks": v(buyback_by, period_end),
            "operating_cash_flow": ocf,
            "capital_expenditures": capex,
            "eps": round(eps, 2) if eps is not None else None,
            "net_margin_pct": round(net_margin, 1) if net_margin is not None else None,
            "gross_margin_pct": round(gross_margin, 1) if gross_margin is not None else None,
        })
    timeline.sort(key=lambda r: r["period_end"] or "")
    return timeline


def explain_score_trend(history: list[dict[str, Any]]) -> str:
    if len(history) < 2:
        return "Not enough historical SEC data to describe a trend."
    first, last = history[0], history[-1]
    if first["overall_score"] is None or last["overall_score"] is None:
        return "Not enough historical SEC data to describe a trend."
    delta = last["overall_score"] - first["overall_score"]
    direction = "improved" if delta > 0 else "declined" if delta < 0 else "held steady"
    return (
        f"FORGE's financial-health score {direction} from {first['overall_score']} "
        f"(FY{first['fiscal_year']}) to {last['overall_score']} (FY{last['fiscal_year']}) "
        f"based on SEC-reported figures across {len(history)} fiscal years. Expand each rule "
        f"in the sections above to see exactly which factors (liquidity, leverage, cash flow, "
        f"retained earnings, etc.) drove the change."
    )
