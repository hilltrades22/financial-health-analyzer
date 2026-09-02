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

# --- XBRL concept candidates, in priority order ---------------------------
# Filers tag the same economic concept under different names. We try each
# candidate in order and use the first one that has usable data.
#
# Two taxonomies are supported, because not every SEC registrant is a
# domestic us-gaap filer:
#   * us-gaap  - domestic filers (10-K / 10-Q).
#   * ifrs-full - foreign private issuers such as Taiwan Semiconductor (TSM)
#     and other ADRs, which file 20-F / 40-F / 6-K under IFRS.
# Both taxonomies' tag names are listed together in each list below; a given
# filer only ever uses one of them, so there is no ambiguity, and us-gaap
# names stay first so domestic behaviour is completely unchanged.
TAXONOMIES = ("us-gaap", "ifrs-full")

CASH_TAGS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    # IFRS
    "CashAndCashEquivalents",
]
SHORT_TERM_INVESTMENT_TAGS = [
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesCurrent",
    # IFRS
    "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss",
    "OtherCurrentFinancialAssets",
]
LONG_TERM_INVESTMENT_TAGS = [
    "LongTermInvestments",
    "MarketableSecuritiesNoncurrent",
    "AvailableForSaleSecuritiesNoncurrent",
    # IFRS
    "OtherNoncurrentFinancialAssets",
    "NoncurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome",
]

DEBT_CURRENT_AGGREGATE_TAGS = ["DebtCurrent", "ShortTermDebt", "ShorttermBorrowings"]
DEBT_CURRENT_COMPONENT_TAGS = ["ShortTermBorrowings", "LongTermDebtCurrent", "CommercialPaper",
                               "CurrentPortionOfLongtermBorrowings"]
LONG_TERM_DEBT_TAGS = ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable",
                       # IFRS
                       "NoncurrentPortionOfLongtermBorrowings", "LongtermBorrowings", "Borrowings"]

TOTAL_LIABILITIES_TAGS = ["Liabilities"]  # same tag name in us-gaap and IFRS
TOTAL_EQUITY_TAGS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    # IFRS
    "EquityAttributableToOwnersOfParent",
    "Equity",
]
TREASURY_STOCK_TAGS = ["TreasuryStockValue", "TreasuryStockCommonValue"]
PREFERRED_STOCK_TAGS = ["PreferredStockValue", "PreferredStockValueOutstanding"]
RETAINED_EARNINGS_TAGS = ["RetainedEarningsAccumulatedDeficit", "RetainedEarnings"]

OPERATING_LEASE_CURRENT_TAGS = ["OperatingLeaseLiabilityCurrent", "CurrentLeaseLiabilities"]
OPERATING_LEASE_NONCURRENT_TAGS = ["OperatingLeaseLiabilityNoncurrent", "NoncurrentLeaseLiabilities"]
FINANCE_LEASE_CURRENT_TAGS = ["FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityCurrentAdditional"]
FINANCE_LEASE_NONCURRENT_TAGS = ["FinanceLeaseLiabilityNoncurrent"]

OPERATING_CASH_FLOW_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    # IFRS
    "CashFlowsFromUsedInOperatingActivities",
]
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsForCapitalImprovements",
    "PaymentsToAcquireProductiveAssets",
    # IFRS
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
]
OPERATING_INCOME_TAGS = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"]
INTEREST_EXPENSE_TAGS = ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating",
                         "FinanceCosts"]
REPURCHASE_TAGS = ["PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity",
                   "PaymentsToAcquireOrRedeemEntitysShares"]

# Shared income-statement / balance-sheet lists used by quality.py, risk.py
# and market_data.py, kept here so there is a single source of truth for
# every concept the app knows how to read (and so IFRS coverage only has to
# be maintained in one place).
REVENUE_TAGS = [
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
    # IFRS
    "Revenue",
    "RevenueFromContractsWithCustomers",
]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"]
ASSETS_TAGS = ["Assets"]  # same tag name in us-gaap and IFRS
ASSETS_CURRENT_TAGS = ["AssetsCurrent", "CurrentAssets"]
LIABILITIES_CURRENT_TAGS = ["LiabilitiesCurrent", "CurrentLiabilities"]
LIABILITIES_NONCURRENT_TAGS = ["LiabilitiesNoncurrent", "NoncurrentLiabilities"]
GROSS_PROFIT_TAGS = ["GrossProfit"]  # same tag name in us-gaap and IFRS
COST_OF_REVENUE_TAGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfSales"]
DILUTED_EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
                    "DilutedEarningsLossPerShare"]
BASIC_EPS_TAGS = ["EarningsPerShareBasic", "BasicEarningsLossPerShare"]
DEPRECIATION_TAGS = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                     "DepreciationAndAmortisationExpense"]
DIVIDENDS_PAID_TAGS = ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
                       "DividendsPaidClassifiedAsFinancingActivities"]
DIVIDENDS_PER_SHARE_TAGS = ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid",
                            "DividendsPerShareDeclared"]
INVESTING_CASH_FLOW_TAGS = ["NetCashProvidedByUsedInInvestingActivities",
                            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
                            "CashFlowsFromUsedInInvestingActivities"]
FINANCING_CASH_FLOW_TAGS = ["NetCashProvidedByUsedInFinancingActivities",
                            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
                            "CashFlowsFromUsedInFinancingActivities"]

# Annual report forms across filer types: 10-K (domestic), 20-F (foreign
# private issuer), 40-F (Canadian MJDS). Interim: 10-Q (domestic) and 6-K
# (foreign private issuer). Without the foreign forms, a legitimate SEC
# registrant like TSM resolves to a CIK but yields zero usable facts.
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
QUARTERLY_ELIGIBLE_FORMS = ANNUAL_FORMS | {"10-Q", "10-Q/A", "6-K", "6-K/A"}


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_currency_unit(unit: str) -> bool:
    """True for an ISO-4217 currency code ("USD", "TWD", "EUR"), false for
    XBRL's non-monetary units ("shares", "pure", "USD/shares")."""
    return len(unit) == 3 and unit.isalpha() and unit.isupper()


def reporting_currency(company_facts: dict[str, Any]) -> str:
    """The currency this filer actually reports in, determined from its own
    XBRL facts rather than assumed. Domestic filers report in USD; a foreign
    private issuer such as Taiwan Semiconductor reports in TWD (while also
    tagging a smaller set of USD convenience-translation facts). Picking the
    dominant currency and then reading every concept in that same currency
    is what keeps a balance sheet internally consistent instead of silently
    mixing two currencies together."""
    cached = company_facts.get("_forge_reporting_currency")
    if cached:
        return cached
    counts: dict[str, int] = {}
    for ns in TAXONOMIES:
        for concept in (company_facts.get("facts", {}).get(ns) or {}).values():
            for unit, entries in (concept.get("units") or {}).items():
                if _is_currency_unit(unit):
                    counts[unit] = counts.get(unit, 0) + len(entries)
    currency = max(counts, key=lambda k: counts[k]) if counts else "USD"
    company_facts["_forge_reporting_currency"] = currency
    return currency


def available_currencies(company_facts: dict[str, Any]) -> list[str]:
    """Every currency this filer has tagged facts in, most-used first."""
    counts: dict[str, int] = {}
    for ns in TAXONOMIES:
        for concept in (company_facts.get("facts", {}).get(ns) or {}).values():
            for unit, entries in (concept.get("units") or {}).items():
                if _is_currency_unit(unit):
                    counts[unit] = counts.get(unit, 0) + len(entries)
    return sorted(counts, key=lambda k: -counts[k])


def _units_for(company_facts: dict[str, Any], tag: str, namespace: Optional[str] = None,
               currency: Optional[str] = None) -> list[dict[str, Any]]:
    """All datapoints for one concept, searched across the supported
    taxonomies (us-gaap first, then ifrs-full) and read in a single,
    explicit currency.

    Every returned entry is stamped with the namespace-qualified concept
    name and the unit it is denominated in, so the audit trail shows exactly
    which taxonomy and currency a number came from.
    """
    namespaces = [namespace] if namespace else list(TAXONOMIES)
    for ns in namespaces:
        concept = (company_facts.get("facts", {}).get(ns) or {}).get(tag)
        if not concept:
            continue
        units = concept.get("units") or {}
        if currency is not None:
            chosen = currency if currency in units else None
        else:
            # Monetary concepts are read in the filer's own reporting
            # currency (falling back to USD); share-count concepts are
            # tagged in "shares" instead, so the same helpers work for both
            # without every caller needing to know which unit applies.
            primary = reporting_currency(company_facts)
            chosen = next((u for u in (primary, "USD", "shares") if u in units), None)
        if chosen is None:
            continue
        out = []
        for e in units[chosen]:
            e = dict(e)
            e["_concept"] = f"{ns}:{tag}"
            e["_unit"] = chosen
            out.append(e)
        if out:
            return out
    return []


def latest_shares_outstanding(company_facts: dict[str, Any]) -> FactValue:
    """Most recent shares-outstanding figure from SEC XBRL, checking the
    us-gaap balance-sheet concept first (any 10-K/10-Q), then falling back
    to the dei cover-page concept every filer must report. Never fabricated -
    returns FactValue.missing() if neither is present."""
    for tag in ("CommonStockSharesOutstanding", "CommonStockSharesIssued",
                "NumberOfSharesOutstanding", "IssuedCapitalNumberOfShares"):
        # No currency constraint: these concepts are share counts by
        # definition, and filers tag them under "shares" (and, rarely, other
        # unit keys) - the unit actually used is recorded on the FactValue.
        entries = _units_for(company_facts, tag)
        candidates = [e for e in entries if e.get("form") in QUARTERLY_ELIGIBLE_FORMS and e.get("end") and "start" not in e]
        if candidates:
            candidates.sort(key=lambda e: (_parse_date(e.get("end")) or date.min, e.get("filed") or ""), reverse=True)
            best = candidates[0]
            return FactValue(
                value=float(best["val"]), available=True, period_end=best.get("end"),
                fiscal_year=best.get("fy"), fiscal_period=best.get("fp"), form=best.get("form"),
                concept=best.get("_concept", tag), filed=best.get("filed"), accn=best.get("accn"),
                unit=best.get("_unit"),
            )
    dei_entries = _units_for(company_facts, "EntityCommonStockSharesOutstanding", namespace="dei")
    candidates = [e for e in dei_entries if e.get("end")]
    if candidates:
        candidates.sort(key=lambda e: (_parse_date(e.get("end")) or date.min, e.get("filed") or ""), reverse=True)
        best = candidates[0]
        return FactValue(
            value=float(best["val"]), available=True, period_end=best.get("end"),
            fiscal_year=best.get("fy"), fiscal_period=best.get("fp"), form=best.get("form"),
            concept="dei:EntityCommonStockSharesOutstanding", filed=best.get("filed"), accn=best.get("accn"),
            unit=best.get("_unit"),
        )
    return FactValue.missing()


def _latest_instant(company_facts: dict[str, Any], tags: Iterable[str], forms: set[str],
                    currency: Optional[str] = None) -> FactValue:
    """Most recent instant (point-in-time) value for the first tag that has data,
    restricted to the given filing forms, picking the latest period end
    (ties broken by latest filed date)."""
    for tag in tags:
        entries = _units_for(company_facts, tag, currency=currency)
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
            concept=best.get("_concept", tag),
            filed=best.get("filed"),
            accn=best.get("accn"),
            unit=best.get("_unit"),
        )
    return FactValue.missing()


def _annual_series(company_facts: dict[str, Any], tags: Iterable[str], duration: bool,
                   currency: Optional[str] = None) -> list[dict[str, Any]]:
    """All annual (10-K) datapoints across every candidate tag, merged by
    fiscal year end and sorted descending. Filers often rename a concept
    partway through their filing history (e.g. Apple's "Revenues" through
    FY2018, then "RevenueFromContractWithCustomerExcludingAssessedTax" from
    FY2019 on) - merging across tags instead of stopping at the first tag
    with ANY data is what keeps the "latest" datapoint from silently being a
    stale one under an abandoned tag while a newer tag has current data."""
    by_end: dict[str, dict[str, Any]] = {}
    for tag in tags:
        entries = _units_for(company_facts, tag, currency=currency)
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
            key = e.get("end")
            prev = by_end.get(key)
            # Prefer whichever tag/filing gives this fiscal year end the most
            # recently-filed value - lets a newer tag "win" a year that an
            # older, superseded tag also happens to cover.
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_end[key] = e
    if not by_end:
        return []
    out = list(by_end.values())
    out.sort(key=lambda e: _parse_date(e.get("end")) or date.min, reverse=True)
    return out


def _quarterly_series(company_facts: dict[str, Any], tags: Iterable[str], duration: bool,
                      currency: Optional[str] = None) -> list[dict[str, Any]]:
    """Every individual fiscal quarter's datapoint (not just the latest),
    merged across candidate tags the same way _annual_series is, so the
    Financial Timeline's Quarterly view has one real point per quarter
    instead of one point per year. A duration concept is restricted to a
    ~90-day span (single quarter) - this deliberately EXCLUDES the
    cumulative 6-/9-month YTD duration facts some filers also report
    alongside their quarterly ones, so a quarterly revenue series isn't
    accidentally a mix of single-quarter and multi-quarter totals."""
    by_end: dict[str, dict[str, Any]] = {}
    for tag in tags:
        entries = _units_for(company_facts, tag, currency=currency)
        for e in entries:
            if e.get("form") not in QUARTERLY_ELIGIBLE_FORMS:
                continue
            end = _parse_date(e.get("end"))
            if end is None:
                continue
            if duration:
                start = _parse_date(e.get("start"))
                if start is None:
                    continue
                span_days = (end - start).days
                if span_days < 75 or span_days > 100:
                    continue  # single fiscal quarter only, not a YTD cumulative duration
            else:
                if "start" in e:
                    continue
            key = e.get("end")
            prev = by_end.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_end[key] = e
    if not by_end:
        return []
    out = list(by_end.values())
    out.sort(key=lambda e: _parse_date(e.get("end")) or date.min, reverse=True)
    return out


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
        unit=entry.get("_unit"),
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


def annual_shares_outstanding_series(company_facts: dict[str, Any]) -> list[FactValue]:
    """Shares outstanding at each fiscal year end (10-K only), most recent
    first - used to compute real historical market cap / valuation
    multiples rather than applying today's share count to past years."""
    for tag in ("CommonStockSharesOutstanding", "CommonStockSharesIssued",
                "NumberOfSharesOutstanding", "IssuedCapitalNumberOfShares"):
        # No currency constraint: these concepts are share counts by
        # definition, and filers tag them under "shares" (and, rarely, other
        # unit keys) - the unit actually used is recorded on the FactValue.
        entries = _units_for(company_facts, tag)
        candidates = [e for e in entries if e.get("form") in ANNUAL_FORMS and e.get("end") and "start" not in e]
        if not candidates:
            continue
        by_end: dict[str, dict[str, Any]] = {}
        for e in candidates:
            key = e.get("end")
            prev = by_end.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_end[key] = e
        out = list(by_end.values())
        out.sort(key=lambda e: _parse_date(e.get("end")) or date.min, reverse=True)
        return [_fact_from_entry(e) for e in out]
    # Foreign private issuers often tag no share-count concept in their
    # accounting taxonomy at all - fall back to the dei cover-page concept
    # every SEC filer must report.
    dei_entries = _units_for(company_facts, "EntityCommonStockSharesOutstanding", namespace="dei")
    annual = [e for e in dei_entries if e.get("form") in ANNUAL_FORMS and e.get("end")]
    if annual:
        by_end = {}
        for e in annual:
            key = e.get("end")
            prev = by_end.get(key)
            if prev is None or (e.get("filed") or "") > (prev.get("filed") or ""):
                by_end[key] = e
        out = sorted(by_end.values(), key=lambda e: _parse_date(e.get("end")) or date.min, reverse=True)
        return [_fact_from_entry(e) for e in out]
    return []


# --- Public re-exports for other modules (quality/risk/history/market_data) ---
units_for = _units_for
latest_instant = _latest_instant
annual_series = _annual_series
quarterly_series = _quarterly_series
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
