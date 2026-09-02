"""Normalized financial-data model shared between normalize.py, scoring.py and the API."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


NOT_REPORTED = "Not reported / unavailable from standardized SEC data"


@dataclass
class FactValue:
    """A single normalized fact pulled from XBRL, or a marker that it's unavailable."""

    value: Optional[float] = None
    available: bool = False
    period_end: Optional[str] = None
    period_start: Optional[str] = None
    fiscal_year: Optional[int] = None
    fiscal_period: Optional[str] = None
    form: Optional[str] = None
    concept: Optional[str] = None
    filed: Optional[str] = None
    accn: Optional[str] = None
    # XBRL unit the value is denominated in ("USD", "TWD", "shares", ...).
    # Carried so that no calculation can silently mix two currencies - a
    # foreign private issuer reporting in TWD must never have its figures
    # combined with a USD market price without saying so.
    unit: Optional[str] = None
    # Set when a value was computed from other reported facts rather than
    # read directly from the filing. A derived value is always labelled as
    # such and never silently replaces a figure the filer did report.
    derived: bool = False
    derivation: Optional[str] = None

    @staticmethod
    def missing() -> "FactValue":
        return FactValue(available=False)


@dataclass
class QuarterlySnapshot:
    """Latest-available balance-sheet snapshot (from the most recent 10-Q or 10-K)."""

    period_end: Optional[str] = None
    form: Optional[str] = None
    filed: Optional[str] = None

    cash_and_equivalents: FactValue = field(default_factory=FactValue.missing)
    short_term_investments: FactValue = field(default_factory=FactValue.missing)
    long_term_investments: FactValue = field(default_factory=FactValue.missing)

    short_term_debt: FactValue = field(default_factory=FactValue.missing)
    long_term_debt: FactValue = field(default_factory=FactValue.missing)

    total_liabilities: FactValue = field(default_factory=FactValue.missing)
    total_equity: FactValue = field(default_factory=FactValue.missing)
    treasury_stock: FactValue = field(default_factory=FactValue.missing)
    preferred_stock: FactValue = field(default_factory=FactValue.missing)
    retained_earnings: FactValue = field(default_factory=FactValue.missing)

    operating_lease_current: FactValue = field(default_factory=FactValue.missing)
    operating_lease_noncurrent: FactValue = field(default_factory=FactValue.missing)
    finance_lease_current: FactValue = field(default_factory=FactValue.missing)
    finance_lease_noncurrent: FactValue = field(default_factory=FactValue.missing)


@dataclass
class AnnualSnapshot:
    """Latest full fiscal-year (10-K) figures, plus the prior year for growth comparisons."""

    fiscal_year: Optional[int] = None
    period_end: Optional[str] = None
    period_start: Optional[str] = None
    filed: Optional[str] = None

    retained_earnings: FactValue = field(default_factory=FactValue.missing)
    prior_retained_earnings: FactValue = field(default_factory=FactValue.missing)

    operating_cash_flow: FactValue = field(default_factory=FactValue.missing)
    capital_expenditures: FactValue = field(default_factory=FactValue.missing)

    operating_income: FactValue = field(default_factory=FactValue.missing)
    interest_expense: FactValue = field(default_factory=FactValue.missing)

    repurchases_of_stock: FactValue = field(default_factory=FactValue.missing)
    treasury_stock: FactValue = field(default_factory=FactValue.missing)


@dataclass
class CompanyFinancials:
    ticker: str
    cik: int
    name: str
    quarterly: QuarterlySnapshot
    annual: AnnualSnapshot
    facts_source_note: str = "SEC EDGAR XBRL Company Facts (data.sec.gov)"
