"""
Turns a raw SEC XBRL "company facts" payload into the normalized
CompanyFinancials model. No values are ever fabricated: if a concept
isn't present in the filer's XBRL facts, the corresponding FactValue
stays unavailable and the API/scoring layer must say so explicitly.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable, Optional

from .models import (
    AnnualSnapshot,
    CompanyFinancials,
    FactValue,
    QuarterlySnapshot,
)

# --- XBRL us-gaap concept candidates, in priority order -------------------
# Filers tag the same economic concept under different names. We try each
# candidate in order and use the first one that has usable data.

CASH_TAGS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
]
SHORT_TERM_INVESTMENT_TAGS = [
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesCurrent",
]
LONG_TERM_INVESTMENT_TAGS = [
    "LongTermInvestments",
    "MarketableSecuritiesNoncurrent",
    "AvailableForSaleSecuritiesNoncurrent",
]

DEBT_CURRENT_AGGREGATE_TAGS = ["DebtCurrent", "ShortTermDebt"]
DEBT_CURRENT_COMPONENT_TAGS = ["ShortTermBorrowings", "LongTermDebtCurrent", "CommercialPaper"]
LONG_TERM_DEBT_TAGS = ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable"]

TOTAL_LIABILITIES_TAGS = ["Liabilities"]
TOTAL_EQUITY_TAGS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]
TREASURY_STOCK_TAGS = ["TreasuryStockValue", "TreasuryStockCommonValue"]
PREFERRED_STOCK_TAGS = ["PreferredStockValue", "PreferredStockValueOutstanding"]
RETAINED_EARNINGS_TAGS = ["RetainedEarningsAccumulatedDeficit"]

OPERATING_LEASE_CURRENT_TAGS = ["OperatingLeaseLiabilityCurrent"]
OPERATING_LEASE_NONCURRENT_TAGS = ["OperatingLeaseLiabilityNoncurrent"]
FINANCE_LEASE_CURRENT_TAGS = ["FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityCurrentAdditional"]
FINANCE_LEASE_NONCURRENT_TAGS = ["FinanceLeaseLiabilityNoncurrent"]

OPERATING_CASH_FLOW_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquireProductiveAssets",
]
OPERATING_INCOME_TAGS = ["OperatingIncomeLoss"]
INTEREST_EXPENSE_TAGS = ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"]
REPURCHASE_TAGS = ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"]

ANNUAL_FORMS = {"10-K", "10-K/A"}
QUARTERLY_ELIGIBLE_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _units_for(company_facts: dict[str, Any], tag: str) -> list[dict[str, Any]]:
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    concept = us_gaap.get(tag)
    if not concept:
        return []
    units = concept.get("units", {})
    # USD is the overwhelmingly common unit for these concepts.
    entries = units.get("USD", [])
    out = []
    for e in entries:
        e = dict(e)
        e["_concept"] = tag
        out.append(e)
    return out


def _latest_instant(company_facts: dict[str, Any], tags: Iterable[str], forms: set[str]) -> FactValue:
    """Most recent instant (point-in-time) value for the first tag that has data,
    restricted to the given filing forms, picking the latest period end
    (ties broken by latest filed date)."""
    for tag in tags:
        entries = _units_for(company_facts, tag)
        candidates = [e for e in entries if e.get("form") in forms and e.get("end") and "start" not in e]
        if not candidates:
            continue
        candidates.sort(
            key=lambda e: (_parse_date(e.get("end")) or date.min, e.get("filed") or ""),
            reverse=True,
        )
        best = candidates[0]
        return FactValue(
            value=float(best["val"]),
            available=True,
            period_end=best.get("end"),
            fiscal_year=best.get("fy"),
            fiscal_period=best.get("fp"),
            form=best.get("form"),
            concept=tag,
            filed=best.get("filed"),
            accn=best.get("accn"),
        )
    return FactValue.missing()


def _annual_series(company_facts: dict[str, Any], tags: Iterable[str], duration: bool) -> list[dict[str, Any]]:
    """All annual (10-K) datapoints for the first tag with usable data, sorted by
    period end descending, deduplicated by fiscal year (keeping latest filed)."""
    for tag in tags:
        entries = _units_for(company_facts, tag)
        candidates = []
        for e in entries:
            if e.get("form") not in ANNUAL_FORMS:
                continue
            end = _parse_date(e.get("end"))
            if end is None:
                continue
            if duration:
                start = _parse_date(e.get("start"))
                if start is None:
                    continue
                span_days = (end - start).days
                if span_days < 300 or span_days > 400:
                    continue  # skip partial-year / quarterly-tagged-as-10K oddities
            else:
                if "start" in e:
                    continue
            candidates.append(e)
        if not candidates:
            continue
        # Dedup by fiscal year end date, keeping the latest-filed value.
        by_end: dict[str, dict[str, Any]] = {}
        for e in candidates:
            key = e.get("end")
            prev = by_end.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_end[key] = e
        out = list(by_end.values())
        out.sort(key=lambda e: _parse_date(e.get("end")) or date.min, reverse=True)
        return out
    return []


def _fact_from_entry(entry: Optional[dict[str, Any]]) -> FactValue:
    if entry is None:
        return FactValue.missing()
    return FactValue(
        value=float(entry["val"]),
        available=True,
        period_end=entry.get("end"),
        period_start=entry.get("start"),
        fiscal_year=entry.get("fy"),
        fiscal_period=entry.get("fp"),
        form=entry.get("form"),
        concept=entry.get("_concept"),
        filed=entry.get("filed"),
        accn=entry.get("accn"),
    )


def _sum_facts(a: FactValue, b: FactValue) -> FactValue:
    """Sum two facts; result is available only if at least one is available
    (missing components are treated as 0, but we note when both are missing)."""
    if not a.available and not b.available:
        return FactValue.missing()
    total = (a.value or 0.0) + (b.value or 0.0)
    basis = a if a.available else b
    return FactValue(
        value=total,
        available=True,
        period_end=basis.period_end,
        fiscal_year=basis.fiscal_year,
        fiscal_period=basis.fiscal_period,
        form=basis.form,
        concept=f"{a.concept or '(unavailable)'}+{b.concept or '(unavailable)'}",
        filed=basis.filed,
        accn=basis.accn,
    )


def build_quarterly_snapshot(company_facts: dict[str, Any]) -> QuarterlySnapshot:
    snap = QuarterlySnapshot()

    cash = _latest_instant(company_facts, CASH_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.cash_and_equivalents = cash
    snap.short_term_investments = _latest_instant(company_facts, SHORT_TERM_INVESTMENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.long_term_investments = _latest_instant(company_facts, LONG_TERM_INVESTMENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)

    debt_agg = _latest_instant(company_facts, DEBT_CURRENT_AGGREGATE_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    if debt_agg.available:
        snap.short_term_debt = debt_agg
    else:
        st_borrow = _latest_instant(company_facts, ["ShortTermBorrowings", "CommercialPaper"], QUARTERLY_ELIGIBLE_FORMS)
        lt_debt_current = _latest_instant(company_facts, ["LongTermDebtCurrent"], QUARTERLY_ELIGIBLE_FORMS)
        snap.short_term_debt = _sum_facts(st_borrow, lt_debt_current)

    snap.long_term_debt = _latest_instant(company_facts, LONG_TERM_DEBT_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.total_liabilities = _latest_instant(company_facts, TOTAL_LIABILITIES_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.total_equity = _latest_instant(company_facts, TOTAL_EQUITY_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.treasury_stock = _latest_instant(company_facts, TREASURY_STOCK_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.preferred_stock = _latest_instant(company_facts, PREFERRED_STOCK_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.retained_earnings = _latest_instant(company_facts, RETAINED_EARNINGS_TAGS, QUARTERLY_ELIGIBLE_FORMS)

    snap.operating_lease_current = _latest_instant(company_facts, OPERATING_LEASE_CURRENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.operating_lease_noncurrent = _latest_instant(company_facts, OPERATING_LEASE_NONCURRENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.finance_lease_current = _latest_instant(company_facts, FINANCE_LEASE_CURRENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)
    snap.finance_lease_noncurrent = _latest_instant(company_facts, FINANCE_LEASE_NONCURRENT_TAGS, QUARTERLY_ELIGIBLE_FORMS)

    # Pin the snapshot's headline period to whichever balance-sheet concept
    # actually resolved (total liabilities is reported by virtually everyone).
    anchor = snap.total_liabilities if snap.total_liabilities.available else cash
    snap.period_end = anchor.period_end
    snap.form = anchor.form
    snap.filed = anchor.filed
    return snap


def build_annual_snapshot(company_facts: dict[str, Any]) -> AnnualSnapshot:
    ann = AnnualSnapshot()

    re_series = _annual_series(company_facts, RETAINED_EARNINGS_TAGS, duration=False)
    if re_series:
        ann.retained_earnings = _fact_from_entry(re_series[0])
        ann.fiscal_year = re_series[0].get("fy")
        ann.period_end = re_series[0].get("end")
        ann.filed = re_series[0].get("filed")
        if len(re_series) > 1:
            ann.prior_retained_earnings = _fact_from_entry(re_series[1])

    ocf_series = _annual_series(company_facts, OPERATING_CASH_FLOW_TAGS, duration=True)
    if ocf_series:
        ann.operating_cash_flow = _fact_from_entry(ocf_series[0])
        if ann.period_start is None:
            ann.period_start = ocf_series[0].get("start")

    capex_series = _annual_series(company_facts, CAPEX_TAGS, duration=True)
    if capex_series:
        ann.capital_expenditures = _fact_from_entry(capex_series[0])

    oi_series = _annual_series(company_facts, OPERATING_INCOME_TAGS, duration=True)
    if oi_series:
        ann.operating_income = _fact_from_entry(oi_series[0])

    ie_series = _annual_series(company_facts, INTEREST_EXPENSE_TAGS, duration=True)
    if ie_series:
        ann.interest_expense = _fact_from_entry(ie_series[0])

    rep_series = _annual_series(company_facts, REPURCHASE_TAGS, duration=True)
    if rep_series:
        ann.repurchases_of_stock = _fact_from_entry(rep_series[0])

    treasury_series = _annual_series(company_facts, TREASURY_STOCK_TAGS, duration=False)
    if treasury_series:
        ann.treasury_stock = _fact_from_entry(treasury_series[0])

    if ann.period_end is None:
        # Fall back to whichever series gave us a fiscal year end.
        for series in (ocf_series, oi_series):
            if series:
                ann.period_end = series[0].get("end")
                ann.period_start = series[0].get("start")
                ann.fiscal_year = series[0].get("fy")
                ann.filed = series[0].get("filed")
                break

    return ann


# --- Public re-exports for other modules (quality/risk/history/market_data) ---
latest_instant = _latest_instant
annual_series = _annual_series
fact_from_entry = _fact_from_entry
QUARTERLY_FORMS = QUARTERLY_ELIGIBLE_FORMS


def build_company_financials(ticker: str, cik: int, name: str, company_facts: dict[str, Any]) -> CompanyFinancials:
    return CompanyFinancials(
        ticker=ticker,
        cik=cik,
        name=name,
        quarterly=build_quarterly_snapshot(company_facts),
        annual=build_annual_snapshot(company_facts),
    )
