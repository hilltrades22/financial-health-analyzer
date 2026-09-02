"""
Sector-aware financial analysis.

The universal rules in scoring.py encode what "financially healthy" means
for a typical operating company. Several of them are economically
meaningless for certain business models - a bank funds itself with customer
deposits, so its liabilities are enormous and it reports no current
assets/liabilities at all; a REIT is designed to hold property against
mortgage debt and distribute its income rather than retain it. Applying the
generic rule anyway does not make the analysis conservative, it makes it
wrong.

This module therefore does two things, both keyed off the SEC-derived peer
group in classification.py (never off a hardcoded ticker):

  1. Marks a universal rule NOT_APPLICABLE, with a plain-English reason,
     when it does not describe the economics of that business model. A
     NOT_APPLICABLE rule earns no points AND is removed from the
     points-available denominator, exactly like UNAVAILABLE - so it can
     neither help nor hurt the score.
  2. Adds rules that DO describe that business model, computed only from
     facts the filer actually reports.

Nothing here invents a metric. Regulatory ratios in particular are only
ever read from a filer's own XBRL, never reconstructed - and every fact is
checked for staleness first, because filers abandon tags: JPMorgan's last
TierOneRiskBasedCapitalToRiskWeightedAssets datapoint is from 2009, and
presenting that as a current capital ratio would be worse than showing
nothing.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from . import normalize as N
from .models import CompanyFinancials, NOT_REPORTED

PASS, WATCH, FAIL = "PASS", "WATCH", "FAIL"
UNAVAILABLE = "UNAVAILABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"

# A fact more than this many days older than the company's own latest
# reported period is treated as abandoned rather than current.
_STALE_AFTER_DAYS = 550


def _parse(d: Optional[str]) -> Optional[date]:
    try:
        return datetime.strptime(d, "%Y-%m-%d").date() if d else None
    except ValueError:
        return None


def is_stale(period_end: Optional[str], latest_period_end: Optional[str]) -> bool:
    """True when a concept's most recent datapoint is far behind the
    company's own latest filing - i.e. the filer stopped reporting it."""
    a, b = _parse(period_end), _parse(latest_period_end)
    if a is None or b is None:
        return False
    return (b - a).days > _STALE_AFTER_DAYS


# --- Universal rules that do not describe certain business models --------
# rule_id -> reason shown to the user. Only rules that are genuinely
# economically inappropriate are listed; a rule that is merely unflattering
# stays in force.
NOT_APPLICABLE_RULES: dict[str, dict[str, str]] = {
    "bank": {
        "liquidity": "Banks do not report current assets or current liabilities, and holding customer "
                     "deposits as liabilities is their business model rather than a funding shortfall. "
                     "Bank liquidity is assessed through deposit funding and capital strength instead.",
        "debt_to_equity": "A bank's liabilities are mostly customer deposits, so a high liabilities-to-equity "
                          "ratio is the normal structure of a functioning bank rather than a sign of distress. "
                          "Capital strength (equity to assets) is the meaningful measure and is scored below.",
        "lease_obligations": "Lease obligations are immaterial to a bank's balance sheet relative to its "
                             "deposit and lending activity.",
    },
    "insurance": {
        "liquidity": "Insurers hold large reserves against future claims and do not present a conventional "
                     "current assets/liabilities split, so a current-ratio style test does not apply.",
        "debt_to_equity": "Insurance liabilities are policyholder reserves rather than borrowings, so a "
                          "liabilities-to-equity ratio does not measure financial leverage here.",
    },
    "financial": {
        "liquidity": "Financial firms carry large balance sheets of financial instruments on both sides, so a "
                     "cash-minus-debt liquidity test does not describe their position.",
        "debt_to_equity": "Gross leverage is inherent to a financial firm's business model and is not "
                          "comparable to an operating company's debt load.",
    },
    "reit": {
        "retained_earnings": "REITs must distribute most of their taxable income to maintain REIT status, so "
                             "they accumulate little retained earnings by design. A low or negative balance "
                             "reflects the required payout, not losses.",
        "retained_earnings_growth": "REITs distribute their income rather than retaining it, so growth in "
                                    "retained earnings is not a meaningful health signal for them.",
    },
}


def _fmt_usd(v: Optional[float], currency: str = "USD") -> str:
    if v is None:
        return NOT_REPORTED
    sign = "-" if v < 0 else ""
    a = abs(v)
    sym = "$" if currency == "USD" else f"{currency} "
    if a >= 1_000_000_000:
        return f"{sign}{sym}{a/1_000_000_000:,.2f}B"
    if a >= 1_000_000:
        return f"{sign}{sym}{a/1_000_000:,.2f}M"
    return f"{sign}{sym}{a:,.0f}"


def _pct(v: Optional[float]) -> str:
    return NOT_REPORTED if v is None else f"{v*100:.1f}%"


def make_rule(rule_id: str, name: str, category: str, status: str, value: str,
              explanation: str, formula: str, source: str, weight: int,
              threshold: Optional[str] = None, period: Optional[str] = None,
              currency: Optional[str] = None) -> dict[str, Any]:
    """One rule result, in the same shape the universal engine emits so the
    API and frontend treat universal and sector rules identically."""
    if status == PASS:
        earned = weight
    elif status == WATCH:
        earned = round(weight * 0.5, 2)
    else:  # FAIL, UNAVAILABLE, NOT_APPLICABLE
        earned = 0
    # Neither missing data nor an inapplicable rule may move the score.
    available = 0 if status in (UNAVAILABLE, NOT_APPLICABLE) else weight
    return {
        "id": rule_id, "name": name, "category": category, "status": status,
        "value": value, "explanation": explanation, "formula": formula,
        "source": source, "threshold": threshold, "period": period,
        "currency": currency,
        "points_available": available, "points_earned": earned,
        "rule_type": "sector",
    }


class _Facts:
    """Thin accessor that records provenance for every fact it returns."""

    def __init__(self, company_facts: dict[str, Any], latest_period_end: Optional[str]):
        self.cf = company_facts
        self.latest = latest_period_end
        self.currency = N.reporting_currency(company_facts)

    def instant(self, tags: list[str]):
        fv = N.latest_instant(self.cf, tags, N.QUARTERLY_ELIGIBLE_FORMS)
        if fv.available and is_stale(fv.period_end, self.latest):
            return None, f"{fv.concept} was last reported for {fv.period_end}; the filer has stopped tagging it"
        return (fv, None) if fv.available else (None, None)

    def annual(self, tags: list[str], duration: bool = True):
        series = N.annual_series(self.cf, tags, duration=duration)
        if not series:
            return None, None
        fv = N.fact_from_entry(series[0])
        if is_stale(fv.period_end, self.latest):
            return None, f"{fv.concept} was last reported for {fv.period_end}; the filer has stopped tagging it"
        return fv, None

    def annual_series(self, tags: list[str], duration: bool = True, limit: int = 5):
        return [N.fact_from_entry(e) for e in N.annual_series(self.cf, tags, duration=duration)[:limit]]


def _src(*facts) -> str:
    parts = [f"{f.concept} ({f.form}, period end {f.period_end}, filed {f.filed})"
             for f in facts if f is not None and getattr(f, "available", False)]
    return ("SEC EDGAR XBRL Company Facts (data.sec.gov): " + "; ".join(parts)) if parts \
        else "SEC EDGAR XBRL Company Facts (data.sec.gov) - required concept not reported by this filer"


def _unavailable(rule_id, name, category, formula, weight, note: Optional[str] = None) -> dict[str, Any]:
    detail = f" ({note})" if note else ""
    return make_rule(
        rule_id, name, category, UNAVAILABLE, NOT_REPORTED,
        f"Unavailable - SEC data does not provide the facts required for this measure{detail}.",
        formula, "SEC EDGAR XBRL Company Facts (data.sec.gov) - concept not reported", weight,
    )


# --- Concept lists used only by sector rules -----------------------------
DEPOSITS_TAGS = ["Deposits", "InterestBearingDepositLiabilities", "DepositsDomestic"]
NET_INTEREST_INCOME_TAGS = ["InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss"]
CREDIT_PROVISION_TAGS = ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses",
                         "ProvisionForCreditLosses", "ProvisionForDoubtfulAccounts"]
RENTAL_REVENUE_TAGS = ["OperatingLeaseLeaseIncome", "OperatingLeasesIncomeStatementLeaseRevenue",
                       "RealEstateRevenueNet", "OperatingLeaseLeaseIncomeLeasePayments"]
RD_TAGS = ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]


def _ratio_rule(rule_id, name, category, value, thresholds, higher_is_better,
                explanation_fn, formula, source, weight, period, currency,
                display_fn=_pct) -> dict[str, Any]:
    """Scores a computed ratio against (pass, watch) thresholds."""
    good, ok = thresholds
    if higher_is_better:
        status = PASS if value >= good else WATCH if value >= ok else FAIL
        thr = f"PASS at or above {display_fn(good)}, WATCH at or above {display_fn(ok)}"
    else:
        status = PASS if value <= good else WATCH if value <= ok else FAIL
        thr = f"PASS at or below {display_fn(good)}, WATCH at or below {display_fn(ok)}"
    return make_rule(rule_id, name, category, status, display_fn(value),
                     explanation_fn(status, value), formula, source, weight,
                     threshold=thr, period=period, currency=currency)


def _earnings_stability(f: _Facts, weight: int, peer_note: str) -> dict[str, Any]:
    """How steady annual net income has been. Uses the company's own history
    only - no peer or market comparison is invented."""
    series = f.annual_series(N.NET_INCOME_TAGS, duration=True, limit=5)
    vals = [s.value for s in series if s.available and s.value is not None]
    if len(vals) < 3:
        return _unavailable("earnings_stability", "Earnings Stability", "Profitability",
                            "Standard deviation of annual net income / average annual net income", weight,
                            "fewer than three years of annual net income reported")
    avg = sum(vals) / len(vals)
    if avg <= 0:
        return make_rule("earnings_stability", "Earnings Stability", "Profitability", FAIL,
                         "Average annual net income is not positive",
                         "The company has not been reliably profitable across the reported years, so earnings "
                         "stability cannot be treated as a strength.",
                         "Average of annual net income over the reported years", _src(*series), weight,
                         period=f"{series[-1].period_end} to {series[0].period_end}", currency=f.currency)
    var = sum((v - avg) ** 2 for v in vals) / len(vals)
    cv = (var ** 0.5) / abs(avg)
    losses = [v for v in vals if v < 0]

    def explain(status, value):
        base = (f"Across the last {len(vals)} reported years net income averaged {_fmt_usd(avg, f.currency)} "
                f"and varied by about {value*100:.0f}% of that average. ")
        if losses:
            base += f"{len(losses)} of those years were loss-making. "
        if status == PASS:
            base += "Earnings have been steady, which makes the rest of the analysis more reliable. "
        elif status == WATCH:
            base += "Earnings move around a fair amount year to year, so any single year overstates precision. "
        else:
            base += "Earnings swing widely between years, so a single year's figures should not be read as the norm. "
        return base + peer_note

    return _ratio_rule("earnings_stability", "Earnings Stability", "Profitability", cv, (0.25, 0.50),
                       higher_is_better=False, explanation_fn=explain,
                       formula="Standard deviation of annual net income / average annual net income (lower is steadier)",
                       source=_src(*series), weight=weight,
                       period=f"{series[-1].period_end} to {series[0].period_end}", currency=f.currency)


def _bank_rules(f: _Facts) -> list[dict[str, Any]]:
    rules = []

    equity, eq_note = f.instant(N.TOTAL_EQUITY_TAGS)
    assets, as_note = f.instant(N.ASSETS_TAGS)
    if equity and assets and assets.value:
        ratio = equity.value / assets.value

        def explain(status, value):
            base = (f"Shareholders' equity of {_fmt_usd(equity.value, f.currency)} supports total assets of "
                    f"{_fmt_usd(assets.value, f.currency)}, so equity funds {value*100:.1f}% of the balance sheet. "
                    "This is the bank's own loss-absorbing cushion: the higher it is, the more the bank can "
                    "lose before depositors and lenders are at risk. ")
            if status == PASS:
                base += "That is a solid capital position for a bank."
            elif status == WATCH:
                base += "That is within the normal range for a large bank but leaves a thinner cushion."
            else:
                base += "That is a thin cushion and leaves little room to absorb losses."
            return base

        rules.append(_ratio_rule(
            "bank_capital_strength", "Capital Strength (Equity / Assets)", "Capital Structure",
            ratio, (0.08, 0.05), True, explain,
            "Shareholders' equity / total assets. This is a simple leverage-capital measure computed from "
            "reported balance-sheet facts - it is NOT a regulatory Tier 1 or CET1 ratio, which depends on "
            "risk-weighted assets that SEC XBRL does not currently provide.",
            _src(equity, assets), 20, period=equity.period_end, currency=f.currency))
    else:
        rules.append(_unavailable("bank_capital_strength", "Capital Strength (Equity / Assets)",
                                  "Capital Structure", "Shareholders' equity / total assets", 20,
                                  eq_note or as_note))

    deposits, dep_note = f.instant(DEPOSITS_TAGS)
    if deposits and assets and assets.value:
        ratio = deposits.value / assets.value

        def explain(status, value):
            base = (f"Customer deposits of {_fmt_usd(deposits.value, f.currency)} fund {value*100:.0f}% of total "
                    "assets. Deposits are generally the cheapest and stickiest way a bank can fund itself; "
                    "a bank that leans instead on wholesale borrowing is more exposed if funding markets tighten. ")
            if status == PASS:
                base += "This bank is primarily deposit-funded."
            elif status == WATCH:
                base += "Deposits are a moderate share of funding, with the remainder from other sources."
            else:
                base += "Deposits fund a relatively small share of the balance sheet, so funding depends more "
                base += "on market-based sources."
            return base

        rules.append(_ratio_rule(
            "bank_deposit_funding", "Deposit Funding", "Liquidity", ratio, (0.50, 0.30), True, explain,
            "Total deposits / total assets", _src(deposits, assets), 15,
            period=deposits.period_end, currency=f.currency))
    else:
        rules.append(_unavailable("bank_deposit_funding", "Deposit Funding", "Liquidity",
                                  "Total deposits / total assets", 15, dep_note or as_note))

    ni, ni_note = f.annual(N.NET_INCOME_TAGS)
    if ni and equity and equity.value:
        roe = ni.value / equity.value

        def explain(status, value):
            base = (f"The bank earned {_fmt_usd(ni.value, f.currency)} in its latest reported year against "
                    f"{_fmt_usd(equity.value, f.currency)} of shareholders' equity, a return on equity of "
                    f"{value*100:.1f}%. For a bank this is the core profitability measure - it shows how much "
                    "profit the bank generates from the capital its shareholders have committed. ")
            if status == PASS:
                base += "That is a strong return."
            elif status == WATCH:
                base += "That is a modest return."
            else:
                base += "That is a weak return on the capital employed."
            return base

        rules.append(_ratio_rule("bank_roe", "Return on Equity", "Profitability", roe, (0.10, 0.05), True,
                                 explain, "Annual net income / shareholders' equity", _src(ni, equity), 20,
                                 period=ni.period_end, currency=f.currency))
    else:
        rules.append(_unavailable("bank_roe", "Return on Equity", "Profitability",
                                  "Annual net income / shareholders' equity", 20, ni_note))

    prov, prov_note = f.annual(CREDIT_PROVISION_TAGS)
    nii, nii_note = f.annual(NET_INTEREST_INCOME_TAGS)
    if prov and nii and nii.value:
        ratio = prov.value / nii.value

        def explain(status, value):
            base = (f"The bank set aside {_fmt_usd(prov.value, f.currency)} for expected credit losses against "
                    f"{_fmt_usd(nii.value, f.currency)} of net interest income, or {value*100:.0f}% of it. "
                    "Provisions are management's own estimate of loans that may not be repaid, so this shows "
                    "how much of the bank's core lending income is being consumed by credit risk. ")
            if status == PASS:
                base += "Credit costs are currently light relative to lending income."
            elif status == WATCH:
                base += "Credit costs are taking a noticeable share of lending income."
            else:
                base += "Credit costs are consuming a large share of lending income, which points to strain in "
                base += "the loan book."
            return base

        rules.append(_ratio_rule("bank_credit_cost", "Credit Cost", "Financial Risk", ratio, (0.15, 0.30),
                                 False, explain,
                                 "Provision for credit losses / net interest income", _src(prov, nii), 15,
                                 period=prov.period_end, currency=f.currency))
    else:
        rules.append(_unavailable("bank_credit_cost", "Credit Cost", "Financial Risk",
                                  "Provision for credit losses / net interest income", 15,
                                  prov_note or nii_note))

    rules.append(_earnings_stability(
        f, 15, "For a bank, steady earnings through a credit cycle matter more than any single year's profit."))
    return rules


def _leverage_rule(f: _Facts, thresholds, weight, peer_note) -> dict[str, Any]:
    """Debt / assets, scored against peer-appropriate thresholds. Used where
    the generic debt-to-equity test would mislead but leverage still matters."""
    assets, as_note = f.instant(N.ASSETS_TAGS)
    st, _ = f.instant(N.DEBT_CURRENT_AGGREGATE_TAGS)
    lt, lt_note = f.instant(N.LONG_TERM_DEBT_TAGS)
    debt = (st.value if st else 0) + (lt.value if lt else 0)
    if not assets or not assets.value or (st is None and lt is None):
        return _unavailable("sector_leverage", "Leverage (Debt / Assets)", "Capital Structure",
                            "(Short-term debt + long-term debt) / total assets", weight, as_note or lt_note)
    ratio = debt / assets.value

    def explain(status, value):
        base = (f"Total borrowings of {_fmt_usd(debt, f.currency)} sit against {_fmt_usd(assets.value, f.currency)} "
                f"of assets, so debt funds {value*100:.0f}% of the balance sheet. ")
        if status == PASS:
            base += "That is comfortable for this kind of business. "
        elif status == WATCH:
            base += "That is meaningful leverage and worth watching. "
        else:
            base += "That is heavy borrowing relative to the asset base. "
        return base + peer_note

    return _ratio_rule("sector_leverage", "Leverage (Debt / Assets)", "Capital Structure", ratio,
                       thresholds, False, explain,
                       "(Short-term debt + long-term debt) / total assets", _src(assets, st, lt), weight,
                       period=assets.period_end, currency=f.currency)


def _interest_coverage_rule(f: _Facts, thresholds, weight, peer_note) -> dict[str, Any]:
    op, op_note = f.annual(N.OPERATING_INCOME_TAGS)
    ie, ie_note = f.annual(N.INTEREST_EXPENSE_TAGS)
    if not op or not ie or not ie.value:
        return _unavailable("sector_interest_coverage", "Interest Coverage", "Debt Service",
                            "Operating income / interest expense", weight, op_note or ie_note)
    cover = op.value / abs(ie.value)

    def explain(status, value):
        base = (f"Operating profit of {_fmt_usd(op.value, f.currency)} covers interest costs of "
                f"{_fmt_usd(abs(ie.value), f.currency)} about {value:.1f} times over. This is the margin of "
                "safety on the debt: how far profits could fall before interest payments become a problem. ")
        if status == PASS:
            base += "That is a comfortable margin. "
        elif status == WATCH:
            base += "That is an adequate but not generous margin. "
        else:
            base += "That is a thin margin, leaving the company exposed if profits weaken. "
        return base + peer_note

    return _ratio_rule("sector_interest_coverage", "Interest Coverage", "Debt Service", cover, thresholds,
                       True, explain, "Operating income / interest expense", _src(op, ie), weight,
                       period=op.period_end, currency=f.currency,
                       display_fn=lambda v: f"{v:.1f}x")


def _cash_generation_rule(f: _Facts, weight, peer_note, capex_context=False) -> dict[str, Any]:
    ocf, ocf_note = f.annual(N.OPERATING_CASH_FLOW_TAGS)
    capex, _ = f.annual(N.CAPEX_TAGS)
    if not ocf:
        return _unavailable("sector_cash_generation", "Cash Generation", "Cash Generation",
                            "Operating cash flow, compared with capital expenditure", weight, ocf_note)
    capex_val = abs(capex.value) if capex and capex.value else None
    if capex_val:
        ratio = ocf.value / capex_val

        def explain(status, value):
            base = (f"The business generated {_fmt_usd(ocf.value, f.currency)} of cash from operations and spent "
                    f"{_fmt_usd(capex_val, f.currency)} on capital investment, covering its investment "
                    f"{value:.1f} times over. ")
            if status == PASS:
                base += "It funds its own investment from operations with cash left over. "
            elif status == WATCH:
                base += "It roughly funds its own investment, leaving little spare cash. "
            else:
                base += "Its investment spending exceeds the cash it generates, so the shortfall must come from "
                base += "debt, asset sales or shareholders. "
            return base + peer_note

        return _ratio_rule("sector_cash_generation", "Cash Generation vs Investment", "Cash Generation",
                           ratio, (1.5, 1.0), True, explain,
                           "Operating cash flow / capital expenditure", _src(ocf, capex), weight,
                           period=ocf.period_end, currency=f.currency,
                           display_fn=lambda v: f"{v:.1f}x")
    status = PASS if ocf.value > 0 else FAIL
    return make_rule("sector_cash_generation", "Cash Generation", "Cash Generation", status,
                     _fmt_usd(ocf.value, f.currency),
                     (f"The business generated {_fmt_usd(ocf.value, f.currency)} of cash from operations in its "
                      f"latest reported year. " + ("Capital expenditure was not separately reported, so the "
                      "comparison against investment spending is not shown. " if capex_context else "") + peer_note),
                     "Operating cash flow (capital expenditure not reported separately)", _src(ocf), weight,
                     period=ocf.period_end, currency=f.currency)


def _margin_rule(f: _Facts, weight, peer_note) -> dict[str, Any]:
    gp, gp_note = f.annual(N.GROSS_PROFIT_TAGS)
    rev, rev_note = f.annual(N.REVENUE_TAGS)
    if not gp or not rev or not rev.value:
        return _unavailable("sector_gross_margin", "Gross Margin", "Profitability",
                            "Gross profit / revenue", weight, gp_note or rev_note)
    margin = gp.value / rev.value

    def explain(status, value):
        base = (f"Of {_fmt_usd(rev.value, f.currency)} in revenue, {_fmt_usd(gp.value, f.currency)} remained after "
                f"direct costs - a gross margin of {value*100:.0f}%. This is the pricing power and cost advantage "
                "of the product itself, before overheads. ")
        if status == PASS:
            base += "That is a strong margin. "
        elif status == WATCH:
            base += "That is a moderate margin. "
        else:
            base += "That is a thin margin, leaving little room to absorb cost increases. "
        return base + peer_note

    return _ratio_rule("sector_gross_margin", "Gross Margin", "Profitability", margin, (0.40, 0.25), True,
                       explain, "Gross profit / revenue", _src(gp, rev), weight,
                       period=rev.period_end, currency=f.currency)


def _rd_intensity_rule(f: _Facts, weight) -> dict[str, Any]:
    rd, rd_note = f.annual(RD_TAGS)
    rev, rev_note = f.annual(N.REVENUE_TAGS)
    if not rd or not rev or not rev.value:
        return _unavailable("sector_rd_intensity", "R&D Intensity", "Business Quality",
                            "Research and development expense / revenue", weight, rd_note or rev_note)
    ratio = rd.value / rev.value
    # Deliberately not scored as good/bad: heavy R&D is a strategic choice,
    # not a health verdict. Reported as context with a neutral PASS.
    return make_rule("sector_rd_intensity", "R&D Intensity", "Business Quality", PASS, _pct(ratio),
                     (f"The company spent {_fmt_usd(rd.value, f.currency)} on research and development, "
                      f"{ratio*100:.0f}% of its {_fmt_usd(rev.value, f.currency)} revenue. For a technology "
                      "business this is reinvestment in future products rather than a cost problem, so it is "
                      "reported as context and does not count against the score."),
                     "Research and development expense / revenue", _src(rd, rev), weight,
                     period=rev.period_end, currency=f.currency)


def _reit_rules(f: _Facts) -> list[dict[str, Any]]:
    peer = ("REITs are built to hold property with mortgage debt and pay out most of their income, so leverage "
            "is expected to be higher here than for an operating company.")
    rules = [
        _leverage_rule(f, (0.55, 0.70), 20, peer),
        _interest_coverage_rule(f, (2.5, 1.5), 20,
                                "Because REITs carry structural debt, the ability to service it out of "
                                "property income is the key solvency question."),
        _cash_generation_rule(f, 20,
                              "For a REIT, cash from operations is the source of the distributions investors "
                              "buy the shares for, so it matters more than reported net income - which is "
                              "depressed by large non-cash property depreciation."),
    ]

    # Distribution coverage, only when both facts are actually reported.
    div, div_note = f.annual(N.DIVIDENDS_PAID_TAGS)
    ocf, ocf_note = f.annual(N.OPERATING_CASH_FLOW_TAGS)
    if div and ocf and div.value:
        ratio = ocf.value / abs(div.value)

        def explain(status, value):
            base = (f"Cash from operations of {_fmt_usd(ocf.value, f.currency)} covers the "
                    f"{_fmt_usd(abs(div.value), f.currency)} paid out to shareholders {value:.2f} times. ")
            if status == PASS:
                base += "Distributions are comfortably funded by the cash the properties actually generate."
            elif status == WATCH:
                base += "Distributions absorb most of the cash generated, leaving a narrow margin."
            else:
                base += ("Distributions exceed the cash generated from operations, which cannot continue "
                         "indefinitely without borrowing or asset sales.")
            return base

        rules.append(_ratio_rule("reit_distribution_coverage", "Distribution Coverage", "Cash Generation",
                                 ratio, (1.2, 1.0), True, explain,
                                 "Operating cash flow / dividends paid", _src(ocf, div), 20,
                                 period=ocf.period_end, currency=f.currency,
                                 display_fn=lambda v: f"{v:.2f}x"))
    else:
        rules.append(_unavailable("reit_distribution_coverage", "Distribution Coverage", "Cash Generation",
                                  "Operating cash flow / dividends paid", 20, div_note or ocf_note))

    # FFO requires depreciation and property gains/losses. Only compute when
    # the inputs are genuinely reported - never approximate it.
    ni, _ = f.annual(N.NET_INCOME_TAGS)
    dep, dep_note = f.annual(N.DEPRECIATION_TAGS)
    if ni and dep:
        ffo = ni.value + dep.value
        rules.append(make_rule(
            "reit_ffo", "Funds From Operations (partial)", "Cash Generation", PASS,
            _fmt_usd(ffo, f.currency),
            (f"Net income of {_fmt_usd(ni.value, f.currency)} plus depreciation and amortisation of "
             f"{_fmt_usd(dep.value, f.currency)} gives {_fmt_usd(ffo, f.currency)}. REIT earnings look weak on "
             "paper because property depreciation is a large non-cash charge, so this add-back is the standard "
             "way to see the underlying cash earnings. Note this is a partial FFO: the NAREIT definition also "
             "removes gains and losses on property sales, which this filer does not report as a separate "
             "standardised XBRL fact, so it is shown as context and does not affect the score."),
            "Net income + depreciation and amortisation (partial FFO - excludes property sale gains/losses)",
            _src(ni, dep), 0, period=ni.period_end, currency=f.currency))
    else:
        rules.append(_unavailable("reit_ffo", "Funds From Operations", "Cash Generation",
                                  "Net income + depreciation and amortisation", 0, dep_note))
    return rules


def _utility_rules(f: _Facts) -> list[dict[str, Any]]:
    peer = ("Utilities fund long-lived regulated infrastructure with debt against highly predictable revenue, "
            "so leverage that would concern a technology company is normal and sustainable here.")
    return [
        _leverage_rule(f, (0.45, 0.60), 20, peer),
        _interest_coverage_rule(f, (3.0, 2.0), 25,
                                "With structurally high debt, the ability to cover interest from operating "
                                "profit is the single most important solvency measure for a utility."),
        _cash_generation_rule(f, 25,
                              "Utilities invest heavily in infrastructure, so persistent investment above cash "
                              "generation is normal in a build cycle - but it must be funded, which is why "
                              "leverage and interest cover are weighted heavily alongside it.", capex_context=True),
        _earnings_stability(f, 20,
                            "Regulated utilities should show steady earnings; unusual volatility suggests "
                            "regulatory or operational disruption."),
    ]


def _technology_rules(f: _Facts, semiconductor: bool) -> list[dict[str, Any]]:
    peer = ("Technology businesses are typically asset-light and cash-generative, so they are held to a higher "
            "standard on margins and cash generation than a capital-intensive industrial company.")
    if semiconductor:
        peer = ("Semiconductor companies run heavy, cyclical capital-expenditure programmes, so a capex-heavy "
                "year is part of the build cycle rather than a weakness - but it must be funded from cash flow "
                "or a strong balance sheet.")
    rules = [
        _margin_rule(f, 25, peer),
        _cash_generation_rule(f, 25, peer, capex_context=True),
        _rd_intensity_rule(f, 0),
        _earnings_stability(f, 20,
                            "Semiconductor demand is cyclical, so swings between years are expected and are "
                            "context rather than a red flag." if semiconductor else
                            "Steady earnings make a technology company's valuation and debt capacity more reliable."),
        _leverage_rule(f, (0.25, 0.45), 20, peer),
    ]
    return rules


def _energy_rules(f: _Facts) -> list[dict[str, Any]]:
    peer = ("Energy producers' revenue and cash flow move with commodity prices, so a single year can "
            "misrepresent the underlying position and the balance sheet matters more than any one year's profit.")
    return [
        _leverage_rule(f, (0.35, 0.50), 25, peer),
        _interest_coverage_rule(f, (4.0, 2.0), 20,
                                "Because energy earnings swing with commodity prices, a wide interest-cover "
                                "margin in good years is what carries the business through weak ones."),
        _cash_generation_rule(f, 25, peer, capex_context=True),
        _earnings_stability(f, 20,
                            "Volatile earnings are inherent to commodity exposure. This measures how much, so "
                            "it can be read as cyclicality rather than mismanagement."),
    ]


# Peer group -> builder. Adding a new business model means adding one entry
# here plus its rule builder; nothing else in the engine changes.
SECTOR_RULE_BUILDERS = {
    "bank": _bank_rules,
    "reit": _reit_rules,
    "utility": _utility_rules,
    "semiconductor": lambda f: _technology_rules(f, semiconductor=True),
    "software": lambda f: _technology_rules(f, semiconductor=False),
    "hardware": lambda f: _technology_rules(f, semiconductor=False),
    "energy": _energy_rules,
}


def _apply_not_applicable(rules: list[dict[str, Any]], peer_group: str) -> list[dict[str, Any]]:
    """Rewrites universal rules that do not describe this business model.
    The rule stays visible with its reason - it is never silently dropped."""
    reasons = NOT_APPLICABLE_RULES.get(peer_group, {})
    out = []
    for rule in rules:
        r = dict(rule)
        r.setdefault("rule_type", "universal")
        reason = reasons.get(r["id"])
        # An UNAVAILABLE rule stays UNAVAILABLE: "we have no data" and "this
        # does not apply here" are different statements and both are useful.
        if reason and r["status"] != UNAVAILABLE:
            r["status"] = NOT_APPLICABLE
            r["explanation"] = reason
            r["not_applicable_reason"] = reason
            r["points_available"] = 0
            r["points_earned"] = 0
        out.append(r)
    return out


def evaluate_with_sector(base_score: dict[str, Any], cf: CompanyFinancials,
                         company_facts: dict[str, Any], classification: dict[str, Any]) -> dict[str, Any]:
    """Universal rules + business-model-specific rules, rescored together.

    The universal engine's output is passed through unchanged except where a
    rule is economically inappropriate for this peer group, and sector rules
    are appended. Score = points earned / points available, where both
    NOT_APPLICABLE and UNAVAILABLE rules are excluded from the denominator,
    so neither an inapplicable test nor missing data can move the score.
    """
    peer_group = (classification or {}).get("peer_group") or "general"
    rules = _apply_not_applicable(list(base_score.get("rules", [])), peer_group)

    builder = SECTOR_RULE_BUILDERS.get(peer_group)
    sector_rules: list[dict[str, Any]] = []
    if builder is not None:
        latest = cf.quarterly.period_end or cf.annual.period_end
        try:
            sector_rules = builder(_Facts(company_facts, latest))
        except Exception as exc:  # noqa: BLE001 - one filer's data quirk must not break the analysis
            sector_rules = [make_rule(
                "sector_rules_error", "Sector Analysis", "Financial Risk", UNAVAILABLE, NOT_REPORTED,
                f"Unavailable - the sector-specific analysis could not be completed for this company "
                f"({type(exc).__name__}). The universal analysis above is unaffected.",
                "n/a", "SEC EDGAR XBRL Company Facts (data.sec.gov)", 0)]

    all_rules = rules + sector_rules
    points_available = sum(r["points_available"] for r in all_rules)
    points_earned = sum(r["points_earned"] for r in all_rules)
    overall = round((points_earned / points_available) * 100) if points_available > 0 else None

    by_status = lambda s: [r["id"] for r in all_rules if r["status"] == s]  # noqa: E731
    n_scored = len([r for r in all_rules if r["points_available"] > 0])
    n_total = len(all_rules)
    confidence = round(n_scored / n_total * 100) if n_total else 0

    if overall is None:
        label = "Insufficient Data"
    elif overall >= 80:
        label = "Strong"
    elif overall >= 60:
        label = "Healthy"
    else:
        label = "Needs Review"

    peer_note = (classification or {}).get("peer_group_note") or ""
    return {
        "overall_score": overall,
        "label": label,
        "points_earned": round(points_earned, 2),
        "points_available_scored": points_available,
        "peer_group": peer_group,
        "peer_group_note": peer_note,
        "passed_rules": by_status(PASS),
        "watch_rules": by_status(WATCH),
        "failed_rules": by_status(FAIL),
        "unavailable_rules": by_status(UNAVAILABLE),
        "not_applicable_rules": by_status(NOT_APPLICABLE),
        "rules": all_rules,
        "universal_rule_count": len(rules),
        "sector_rule_count": len(sector_rules),
        "data_confidence_pct": confidence,
        "data_confidence_note": (
            f"{n_scored} of {n_total} rules could be scored from this company's SEC data. Rules marked "
            "NOT_APPLICABLE (the measure does not describe this business model) and UNAVAILABLE (the filer "
            "does not report the required facts) are excluded from the score rather than counted as failures."
        ),
        "scoring_formula": (
            "Each rule carries a point weight. PASS earns the full weight, WATCH earns half, FAIL earns zero. "
            "NOT_APPLICABLE and UNAVAILABLE rules earn zero AND are removed from the points-available "
            "denominator, so neither an inapplicable measure nor missing data can raise or lower the score. "
            f"Overall Score = points earned / points available x 100. This company is analysed as: {peer_group}."
        ),
    }
