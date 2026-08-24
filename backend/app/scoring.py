"""
Explainable financial-health scoring engine.

Every rule below returns a dict describing: the metric, its value, a
PASS / WATCH / FAIL / UNAVAILABLE status, a plain-English explanation, the
exact formula used, the SEC source, the rule's point weight, and the
points actually earned. Nothing here ever fabricates a number - a rule
whose required facts are missing from SEC's XBRL data is marked
UNAVAILABLE and contributes zero points to both the earned and the
available totals, so it can never inflate or accidentally "pass" the
score.
"""
from __future__ import annotations

from typing import Any, Optional

from .models import CompanyFinancials, FactValue, NOT_REPORTED

PASS, WATCH, FAIL, UNAVAILABLE = "PASS", "WATCH", "FAIL", "UNAVAILABLE"

# Rule weights sum to 100 - this is the exact, disclosed scoring formula.
WEIGHTS = {
    "liquidity": 15,
    "debt_to_equity": 15,
    "preferred_stock": 5,
    "retained_earnings": 10,
    "retained_earnings_growth": 10,
    "treasury_stock": 5,
    "free_cash_flow": 15,
    "interest_coverage": 15,
    "lease_obligations": 10,
}


def _fmt_usd(value: Optional[float]) -> str:
    if value is None:
        return NOT_REPORTED
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:,.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:,.2f}M"
    return f"{sign}${v:,.0f}"


def _fmt_ratio(value: Optional[float]) -> str:
    if value is None:
        return NOT_REPORTED
    return f"{value:.2f}x"


def _val(fv: FactValue) -> Optional[float]:
    return fv.value if fv.available else None


def _source(*facts: FactValue) -> str:
    parts = []
    for f in facts:
        if f.available and f.concept:
            parts.append(f"us-gaap:{f.concept} ({f.form}, period end {f.period_end}, filed {f.filed})")
    if not parts:
        return "SEC EDGAR XBRL Company Facts (data.sec.gov) - concept not found in filer's data"
    return "SEC EDGAR XBRL Company Facts (data.sec.gov): " + "; ".join(parts)


def _rule(
    rule_id: str,
    name: str,
    category: str,
    status: str,
    metric_value: str,
    explanation: str,
    formula: str,
    source: str,
    weight_key: str,
) -> dict[str, Any]:
    weight = WEIGHTS[weight_key]
    if status == PASS:
        earned = weight
    elif status == WATCH:
        earned = round(weight * 0.5, 2)
    else:  # FAIL or UNAVAILABLE
        earned = 0
    available = 0 if status == UNAVAILABLE else weight
    return {
        "id": rule_id,
        "name": name,
        "category": category,
        "status": status,
        "value": metric_value,
        "explanation": explanation,
        "formula": formula,
        "source": source,
        "points_available": available,
        "points_earned": earned,
    }


def rule_liquidity(cf: CompanyFinancials) -> dict[str, Any]:
    q = cf.quarterly
    cash = q.cash_and_equivalents
    st_inv = q.short_term_investments
    lt_inv = q.long_term_investments
    st_debt = q.short_term_debt
    lt_debt = q.long_term_debt

    if not cash.available:
        return _rule(
            "liquidity", "Liquidity", "Liquidity", UNAVAILABLE, NOT_REPORTED,
            "Cash and cash equivalents were not found in this company's standardized "
            "SEC XBRL data, so liquidity cannot be reliably calculated.",
            "Cash + Marketable Securities - Short-Term Debt - Long-Term Debt",
            _source(cash), "liquidity",
        )

    liquid_assets = (cash.value or 0) + (st_inv.value or 0) + (lt_inv.value or 0)
    debt = (st_debt.value or 0) + (lt_debt.value or 0)
    net = liquid_assets - debt
    status = PASS if net > 0 else FAIL
    notes = []
    if not st_inv.available and not lt_inv.available:
        notes.append("no marketable securities reported (treated as $0)")
    if not st_debt.available and not lt_debt.available:
        notes.append("no short- or long-term debt reported (treated as $0)")
    note_txt = f" ({'; '.join(notes)})" if notes else ""
    explanation = (
        f"After combining cash, marketable securities, and subtracting reported debt, the "
        f"company has {_fmt_usd(net)} in net liquid resources{note_txt}. "
        f"{'This is a healthy cash cushion.' if status == PASS else 'Debt exceeds readily available cash and securities.'}"
    )
    return _rule(
        "liquidity", "Liquidity", "Liquidity", status, _fmt_usd(net), explanation,
        "Cash + Marketable Securities - Short-Term Debt - Long-Term Debt",
        _source(cash, st_inv, lt_inv, st_debt, lt_debt), "liquidity",
    )


def rule_debt_to_equity(cf: CompanyFinancials) -> dict[str, Any]:
    q = cf.quarterly
    liab, equity = q.total_liabilities, q.total_equity
    if not liab.available or not equity.available or equity.value == 0:
        return _rule(
            "debt_to_equity", "Debt & Leverage", "Debt & Leverage", UNAVAILABLE, NOT_REPORTED,
            "Total liabilities and/or total stockholders' equity were not available from "
            "SEC XBRL data, so this ratio cannot be calculated.",
            "Total Liabilities / Total Equity (target < 0.80x)",
            _source(liab, equity), "debt_to_equity",
        )
    ratio = liab.value / equity.value
    if equity.value < 0:
        status = FAIL
        explanation = (
            f"Total equity is negative ({_fmt_usd(equity.value)}), meaning liabilities exceed assets. "
            f"This is a significant leverage concern regardless of the raw ratio ({_fmt_ratio(ratio)})."
        )
    elif ratio < 0.80:
        status = PASS
        explanation = f"Liabilities are {_fmt_ratio(ratio)} of equity, below the 0.80x target - a conservative capital structure."
    elif ratio < 1.00:
        status = WATCH
        explanation = f"Liabilities are {_fmt_ratio(ratio)} of equity, above the 0.80x target but still less than total equity."
    else:
        status = FAIL
        explanation = f"Liabilities are {_fmt_ratio(ratio)} of equity - the company owes more than its equity base, a highly leveraged position."

    treasury_note = ""
    if q.treasury_stock.available and q.treasury_stock.value:
        treasury_note = (
            f" Note: reported treasury stock of {_fmt_usd(q.treasury_stock.value)} already reduces "
            f"total equity in this figure, which can make the ratio look worse than it would without buybacks."
        )
    lease_total = 0.0
    lease_available = False
    for lf in (q.operating_lease_current, q.operating_lease_noncurrent, q.finance_lease_current, q.finance_lease_noncurrent):
        if lf.available:
            lease_available = True
            lease_total += lf.value or 0
    lease_note = ""
    if lease_available:
        lease_note = (
            f" Lease liabilities of {_fmt_usd(lease_total)} are already included within total liabilities above "
            f"(not added twice) - see the Lease Obligations section for the breakdown."
        )

    return _rule(
        "debt_to_equity", "Debt & Leverage", "Debt & Leverage", status, _fmt_ratio(ratio),
        explanation + treasury_note + lease_note,
        "Total Liabilities / Total Equity (target < 0.80x)",
        _source(liab, equity), "debt_to_equity",
    )


def rule_preferred_stock(cf: CompanyFinancials) -> dict[str, Any]:
    ps = cf.quarterly.preferred_stock
    if not ps.available:
        return _rule(
            "preferred_stock", "Capital Structure", "Capital Structure", UNAVAILABLE, NOT_REPORTED,
            "Preferred stock was not found in this company's standardized SEC XBRL data. "
            "This does not prove no preferred stock exists - it simply was not reported under a "
            "recognized XBRL tag.",
            "Presence and magnitude of Preferred Stock reported on the balance sheet",
            _source(ps), "preferred_stock",
        )
    if not ps.value or ps.value == 0:
        return _rule(
            "preferred_stock", "Capital Structure", "Capital Structure", PASS, _fmt_usd(0),
            "No meaningful preferred stock is reported. Common shareholders do not face preferred "
            "claims ahead of them.",
            "Presence and magnitude of Preferred Stock reported on the balance sheet",
            _source(ps), "preferred_stock",
        )
    return _rule(
        "preferred_stock", "Capital Structure", "Capital Structure", WATCH, _fmt_usd(ps.value),
        f"The company reports {_fmt_usd(ps.value)} of preferred stock. Preferred shareholders "
        f"typically have priority over common shareholders for dividends and in liquidation - "
        f"this is not automatically bad, but it is a claim ahead of common equity worth understanding.",
        "Presence and magnitude of Preferred Stock reported on the balance sheet",
        _source(ps), "preferred_stock",
    )


def rule_retained_earnings(cf: CompanyFinancials) -> dict[str, Any]:
    re = cf.annual.retained_earnings
    if not re.available:
        return _rule(
            "retained_earnings", "Retained Earnings", "Retained Earnings", UNAVAILABLE, NOT_REPORTED,
            "Retained earnings were not available from the latest annual (10-K) SEC XBRL data.",
            "Retained Earnings (latest fiscal year-end, positive = PASS)",
            _source(re), "retained_earnings",
        )
    status = PASS if re.value > 0 else FAIL
    explanation = (
        f"Retained earnings (accumulated profits kept in the business) stand at {_fmt_usd(re.value)} "
        f"as of fiscal year {re.fiscal_year or ''} ({re.period_end}). "
        f"{'This reflects a history of cumulative profitability.' if status == PASS else 'This is an accumulated deficit, meaning the company has cumulatively lost more than it has earned.'}"
    )
    return _rule(
        "retained_earnings", "Retained Earnings", "Retained Earnings", status, _fmt_usd(re.value), explanation,
        "Retained Earnings (latest fiscal year-end, positive = PASS)",
        _source(re), "retained_earnings",
    )


def rule_retained_earnings_growth(cf: CompanyFinancials) -> dict[str, Any]:
    re, prior = cf.annual.retained_earnings, cf.annual.prior_retained_earnings
    if not re.available or not prior.available:
        return _rule(
            "retained_earnings_growth", "Retained Earnings", "Retained Earnings", UNAVAILABLE, NOT_REPORTED,
            "At least two consecutive annual (10-K) retained-earnings figures are required to assess "
            "growth, and one or both were not available from SEC XBRL data.",
            "Latest Annual Retained Earnings vs. Previous Annual Retained Earnings (increasing = PASS)",
            _source(re, prior), "retained_earnings_growth",
        )
    change = re.value - prior.value
    status = PASS if change > 0 else FAIL
    explanation = (
        f"Retained earnings moved from {_fmt_usd(prior.value)} (FY{prior.fiscal_year}) to "
        f"{_fmt_usd(re.value)} (FY{re.fiscal_year}), a change of {_fmt_usd(change)}. "
        f"{'Cumulative profitability is growing year over year.' if status == PASS else 'Cumulative retained earnings declined versus the prior fiscal year.'}"
    )
    return _rule(
        "retained_earnings_growth", "Retained Earnings", "Retained Earnings", status, _fmt_usd(change), explanation,
        "Latest Annual Retained Earnings vs. Previous Annual Retained Earnings (increasing = PASS)",
        _source(re, prior), "retained_earnings_growth",
    )


def rule_treasury_stock(cf: CompanyFinancials) -> dict[str, Any]:
    treasury = cf.quarterly.treasury_stock if cf.quarterly.treasury_stock.available else cf.annual.treasury_stock
    repurchases = cf.annual.repurchases_of_stock
    if not treasury.available and not repurchases.available:
        return _rule(
            "treasury_stock", "Treasury Stock", "Treasury Stock", UNAVAILABLE, NOT_REPORTED,
            "Neither treasury stock nor annual share repurchases were found in this company's "
            "standardized SEC XBRL data.",
            "Treasury Stock (balance sheet) and Share Repurchases (latest annual cash flow statement)",
            _source(treasury, repurchases), "treasury_stock",
        )
    parts = []
    if treasury.available and treasury.value:
        parts.append(f"treasury stock of {_fmt_usd(treasury.value)}")
    if repurchases.available and repurchases.value:
        parts.append(f"{_fmt_usd(repurchases.value)} of share repurchases in fiscal year {cf.annual.fiscal_year}")
    if not parts:
        return _rule(
            "treasury_stock", "Treasury Stock", "Treasury Stock", PASS, _fmt_usd(0),
            "No meaningful treasury stock or share repurchases are reported.",
            "Treasury Stock (balance sheet) and Share Repurchases (latest annual cash flow statement)",
            _source(treasury, repurchases), "treasury_stock",
        )
    explanation = (
        "The company reports " + " and ".join(parts) + ". Treasury stock generally represents shares "
        "the company has repurchased from the market; it reduces reported total equity. Buybacks can "
        "return capital to shareholders, but they are not automatically a positive signal - they also "
        "consume cash that could otherwise fund operations, debt reduction, or investment."
    )
    value_str = _fmt_usd(treasury.value) if treasury.available else _fmt_usd(repurchases.value)
    return _rule(
        "treasury_stock", "Treasury Stock", "Treasury Stock", WATCH, value_str, explanation,
        "Treasury Stock (balance sheet) and Share Repurchases (latest annual cash flow statement)",
        _source(treasury, repurchases), "treasury_stock",
    )


def rule_free_cash_flow(cf: CompanyFinancials) -> dict[str, Any]:
    ocf, capex = cf.annual.operating_cash_flow, cf.annual.capital_expenditures
    if not ocf.available or not capex.available:
        return _rule(
            "free_cash_flow", "Cash Generation", "Cash Generation", UNAVAILABLE, NOT_REPORTED,
            "Annual operating cash flow and/or capital expenditures were not available from the "
            "latest 10-K SEC XBRL data.",
            "Operating Cash Flow - Capital Expenditures (latest fiscal year)",
            _source(ocf, capex), "free_cash_flow",
        )
    fcf = ocf.value - abs(capex.value)
    status = PASS if fcf > 0 else FAIL
    explanation = (
        f"In fiscal year {cf.annual.fiscal_year}, operating cash flow of {_fmt_usd(ocf.value)} less "
        f"capital expenditures of {_fmt_usd(abs(capex.value))} leaves free cash flow of {_fmt_usd(fcf)}. "
        f"{'The business generates more cash than it spends on capital investment.' if status == PASS else 'The business is consuming more cash than its operations generate after capital spending.'}"
    )
    return _rule(
        "free_cash_flow", "Cash Generation", "Cash Generation", status, _fmt_usd(fcf), explanation,
        "Operating Cash Flow - Capital Expenditures (latest fiscal year)",
        _source(ocf, capex), "free_cash_flow",
    )


def rule_interest_coverage(cf: CompanyFinancials) -> dict[str, Any]:
    oi, ie = cf.annual.operating_income, cf.annual.interest_expense
    if not oi.available or not ie.available:
        return _rule(
            "interest_coverage", "Debt Service", "Debt Service", UNAVAILABLE, NOT_REPORTED,
            "Annual operating income and/or interest expense were not available from the latest "
            "10-K SEC XBRL data.",
            "Operating Income / Interest Expense (latest fiscal year)",
            _source(oi, ie), "interest_coverage",
        )
    if ie.value == 0:
        return _rule(
            "interest_coverage", "Debt Service", "Debt Service", PASS, "N/A (no interest expense)",
            f"The company reported no interest expense in fiscal year {cf.annual.fiscal_year}, "
            f"indicating minimal or no interest-bearing debt burden.",
            "Operating Income / Interest Expense (latest fiscal year)",
            _source(oi, ie), "interest_coverage",
        )
    ratio = oi.value / ie.value
    if ratio >= 3:
        status = PASS
        status_note = "Comfortable coverage of debt-service obligations."
    elif ratio >= 1.5:
        status = WATCH
        status_note = "Coverage is thin; a downturn in operating income could pressure the company's ability to service debt."
    else:
        status = FAIL
        status_note = "Operating income barely covers or falls short of interest obligations - a significant debt-service risk."
    explanation = (
        f"Operating income covers interest expense {_fmt_ratio(ratio)} in fiscal year {cf.annual.fiscal_year} "
        f"(operating income {_fmt_usd(oi.value)} / interest expense {_fmt_usd(ie.value)}). "
        f"{status_note}"
    )
    return _rule(
        "interest_coverage", "Debt Service", "Debt Service", status, _fmt_ratio(ratio), explanation,
        "Operating Income / Interest Expense (latest fiscal year)",
        _source(oi, ie), "interest_coverage",
    )


def rule_lease_obligations(cf: CompanyFinancials) -> dict[str, Any]:
    q = cf.quarterly
    fields = {
        "Current operating lease liabilities": q.operating_lease_current,
        "Long-term operating lease liabilities": q.operating_lease_noncurrent,
        "Current finance lease liabilities": q.finance_lease_current,
        "Long-term finance lease liabilities": q.finance_lease_noncurrent,
    }
    if not any(f.available for f in fields.values()):
        return _rule(
            "lease_obligations", "Lease Obligations", "Lease Obligations", UNAVAILABLE, NOT_REPORTED,
            "No lease liability data was found in this company's standardized SEC XBRL data. This "
            "may mean the company has no material leases, or that they were not tagged under a "
            "recognized concept.",
            "Sum of current + long-term operating and finance lease liabilities (shown separately, "
            "not added to Total Liabilities again - they are already included there)",
            _source(*fields.values()), "lease_obligations",
        )
    total = sum((f.value or 0) for f in fields.values() if f.available)
    breakdown = "; ".join(f"{label}: {_fmt_usd(f.value) if f.available else NOT_REPORTED}" for label, f in fields.items())
    explanation = (
        f"Total lease liabilities of {_fmt_usd(total)} ({breakdown}). These are already counted within "
        f"Total Liabilities in the Debt & Leverage section above and are shown here separately for "
        f"transparency, not added a second time."
    )
    return _rule(
        "lease_obligations", "Lease Obligations", "Lease Obligations", WATCH, _fmt_usd(total), explanation,
        "Sum of current + long-term operating and finance lease liabilities (shown separately, "
        "not added to Total Liabilities again - they are already included there)",
        _source(*fields.values()), "lease_obligations",
    )


ALL_RULES = [
    rule_liquidity,
    rule_debt_to_equity,
    rule_preferred_stock,
    rule_retained_earnings,
    rule_retained_earnings_growth,
    rule_treasury_stock,
    rule_free_cash_flow,
    rule_interest_coverage,
    rule_lease_obligations,
]


def score_company(cf: CompanyFinancials) -> dict[str, Any]:
    rules = [r(cf) for r in ALL_RULES]
    points_available = sum(r["points_available"] for r in rules)
    points_earned = sum(r["points_earned"] for r in rules)
    overall_pct = round((points_earned / points_available) * 100) if points_available > 0 else None

    if points_available < 50 or overall_pct is None:
        label = "Insufficient Data"
    elif overall_pct >= 80:
        label = "Strong"
    elif overall_pct >= 60:
        label = "Healthy"
    else:
        label = "Needs Review"

    passed = [r["id"] for r in rules if r["status"] == PASS]
    failed = [r["id"] for r in rules if r["status"] == FAIL]
    watch = [r["id"] for r in rules if r["status"] == WATCH]
    unavailable = [r["id"] for r in rules if r["status"] == UNAVAILABLE]

    return {
        "overall_score": overall_pct,
        "label": label,
        "points_earned": points_earned,
        "points_available_total": 100,
        "points_available_scored": points_available,
        "passed_rules": passed,
        "watch_rules": watch,
        "failed_rules": failed,
        "unavailable_rules": unavailable,
        "scoring_formula": (
            "Each of the 9 rules has a fixed point weight (Liquidity 15, Debt & Leverage 15, "
            "Preferred Stock 5, Retained Earnings 10, Retained Earnings Growth 10, Treasury Stock 5, "
            "Free Cash Flow 15, Interest Coverage 15, Lease Obligations 10 = 100 total). "
            "PASS earns the full weight, WATCH earns half, FAIL earns zero. A rule marked "
            "UNAVAILABLE (required SEC data missing) earns zero points AND is removed from the "
            "points-available denominator, so missing data can never automatically help or hurt "
            "the score. Overall Score = (points earned / points available) x 100."
        ),
        "rules": rules,
    }
