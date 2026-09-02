"""
Ticker-coverage regression tests.

These lock in the fix for foreign private issuers such as TSM (Taiwan
Semiconductor), which resolve to a real SEC CIK but file 20-F/6-K under the
IFRS taxonomy in a non-USD reporting currency. Before the fix every fact
lookup was hardcoded to the us-gaap namespace and 10-K/10-Q forms, so these
companies returned a valid response with no financial data at all.

The shapes below mirror the real SEC payload for CIK 1046179 (verified
against data.sec.gov): ifrs-full concepts, form 20-F, TWD primary currency
with a smaller set of filer-reported USD convenience-translation facts.
"""
from backend.app import normalize as N
from backend.app.classification import build_classification


def _dur(vals, unit="TWD", form="20-F"):
    return [{"start": s, "end": e, "val": v, "form": form, "filed": f"{e[:4]}-04-17",
             "fy": int(e[:4]), "fp": "FY", "accn": "acc-1"} for s, e, v in vals]


def _inst(vals, unit="TWD", form="20-F"):
    return [{"end": e, "val": v, "form": form, "filed": f"{e[:4]}-04-17",
             "fy": int(e[:4]), "fp": "FY", "accn": "acc-1"} for e, v in vals]


IFRS_FACTS = {
    "entityName": "Taiwan Semiconductor Manufacturing Company Limited",
    "facts": {
        "ifrs-full": {
            "Revenue": {"units": {
                "TWD": _dur([("2023-01-01", "2023-12-31", 2.16e12), ("2024-01-01", "2024-12-31", 2.894e12)]),
                "USD": _dur([("2024-01-01", "2024-12-31", 8.8268e10)]),
            }},
            "ProfitLoss": {"units": {
                "TWD": _dur([("2024-01-01", "2024-12-31", 1.17e12)]),
                "USD": _dur([("2024-01-01", "2024-12-31", 3.6e10)]),
            }},
            "Assets": {"units": {"TWD": _inst([("2023-12-31", 5.53e12), ("2024-12-31", 6.69e12)])}},
            "Liabilities": {"units": {"TWD": _inst([("2023-12-31", 2.0e12), ("2024-12-31", 2.4e12)])}},
            "Equity": {"units": {"TWD": _inst([("2023-12-31", 3.5e12), ("2024-12-31", 4.2e12)])}},
            "CurrentAssets": {"units": {"TWD": _inst([("2024-12-31", 2.9e12)])}},
            "CurrentLiabilities": {"units": {"TWD": _inst([("2024-12-31", 1.2e12)])}},
            "CashAndCashEquivalents": {"units": {"TWD": _inst([("2024-12-31", 2.13e12)])}},
            "RetainedEarnings": {"units": {"TWD": _inst([("2023-12-31", 2.9e12), ("2024-12-31", 3.5e12)])}},
            "ProfitLossFromOperatingActivities": {"units": {"TWD": _dur([("2024-01-01", "2024-12-31", 1.32e12)])}},
            "CashFlowsFromUsedInOperatingActivities": {"units": {"TWD": _dur([("2024-01-01", "2024-12-31", 1.83e12)])}},
            "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": {
                "units": {"TWD": _dur([("2024-01-01", "2024-12-31", -9.56e11)])}},
            "NoncurrentPortionOfLongtermBorrowings": {"units": {"TWD": _inst([("2024-12-31", 8.9e11)])}},
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": _inst([("2024-12-31", 2.59e10)])}}},
    },
}


def test_reporting_currency_detected_from_the_filers_own_facts():
    assert N.reporting_currency(dict(IFRS_FACTS)) == "TWD"
    assert "USD" in N.available_currencies(dict(IFRS_FACTS))


def test_ifrs_concepts_resolve_in_the_reporting_currency():
    facts = dict(IFRS_FACTS)
    cash = N.latest_instant(facts, N.CASH_TAGS, N.QUARTERLY_ELIGIBLE_FORMS)
    assert cash.available and cash.value == 2.13e12
    assert cash.unit == "TWD"
    assert cash.concept == "ifrs-full:CashAndCashEquivalents"
    assert cash.form == "20-F"


def test_twenty_f_is_treated_as_an_annual_filing():
    facts = dict(IFRS_FACTS)
    series = N.annual_series(facts, N.REVENUE_TAGS, duration=True)
    assert [e["end"] for e in series] == ["2024-12-31", "2023-12-31"]


def test_usd_figures_are_only_taken_when_the_filer_reported_them():
    """A price-based multiple must use the listing currency, and must not be
    computed at all when the filer reported no figure in that currency."""
    facts = dict(IFRS_FACTS)
    usd_revenue = N.annual_series(facts, N.REVENUE_TAGS, duration=True, currency="USD")
    assert usd_revenue and usd_revenue[0]["val"] == 8.8268e10
    # Equity was reported ONLY in TWD - so a USD request must come back empty
    # rather than silently returning the TWD number or converting it.
    usd_equity = N.annual_series(facts, N.TOTAL_EQUITY_TAGS, duration=False, currency="USD")
    assert usd_equity == []


def test_full_snapshot_builds_for_an_ifrs_filer():
    cf = N.build_company_financials("TSM", 1046179, "Taiwan Semiconductor", dict(IFRS_FACTS))
    assert cf.quarterly.total_liabilities.available
    assert cf.quarterly.total_equity.available
    assert cf.quarterly.cash_and_equivalents.available
    assert cf.annual.operating_cash_flow.available
    assert cf.annual.capital_expenditures.available
    assert cf.annual.period_end == "2024-12-31"


def test_us_gaap_filers_are_unaffected_by_the_multi_taxonomy_lookup():
    us_facts = {"facts": {"us-gaap": {
        "Liabilities": {"units": {"USD": _inst([("2025-09-30", 2.8e11)], form="10-K")}},
    }}}
    fv = N.latest_instant(us_facts, N.TOTAL_LIABILITIES_TAGS, N.QUARTERLY_ELIGIBLE_FORMS)
    assert fv.available and fv.value == 2.8e11
    assert fv.concept == "us-gaap:Liabilities"
    assert N.reporting_currency(us_facts) == "USD"


def test_classification_maps_sic_to_sector_and_peer_group():
    tsm = build_classification({"sic": "3674", "sicDescription": "Semiconductors & Related Devices",
                                "exchanges": ["NYSE", "OTC"]}, market_cap=4.2e11)
    assert tsm["sector"]["value"] == "Manufacturing"
    assert tsm["sub_industry"]["value"] == "Semiconductors & Related Devices"
    assert tsm["peer_group"] == "semiconductor"
    assert tsm["exchange"]["value"] == "NYSE, OTC"
    assert tsm["market_cap"]["available"] is True

    bank = build_classification({"sic": "6022", "sicDescription": "State Commercial Banks"})
    assert bank["peer_group"] == "bank"
    assert "deposits" in bank["peer_group_note"]


def test_classification_reports_unavailable_rather_than_guessing():
    empty = build_classification({})
    assert empty["sector"]["available"] is False
    assert empty["sector"]["value"] is None
    assert empty["market_cap"]["available"] is False
    assert empty["peer_group"] == "general"
