"""
Sector-aware rule engine tests.

The shapes here mirror what these filers actually report to SEC (verified
against data.sec.gov): JPMorgan tags Deposits, InterestIncomeExpenseNet and
ProvisionForLoanLeaseAndOtherLosses but reports NO AssetsCurrent or
LiabilitiesCurrent, and abandoned TierOneRiskBasedCapitalToRiskWeightedAssets
after 2009.
"""
import pytest

from backend.app import normalize as N
from backend.app.classification import build_classification
from backend.app.scoring import score_company
from backend.app.sector_rules import (
    FAIL, NOT_APPLICABLE, PASS, UNAVAILABLE, WATCH,
    evaluate_with_sector, is_stale,
)


def _inst(vals, form="10-K"):
    return [{"end": e, "val": v, "form": form, "filed": f"{e[:4]}-02-15",
             "fy": int(e[:4]), "fp": "FY", "accn": "a"} for e, v in vals]


def _dur(vals, form="10-K"):
    return [{"start": s, "end": e, "val": v, "form": form, "filed": f"{e[:4]}-02-15",
             "fy": int(e[:4]), "fp": "FY", "accn": "a"} for s, e, v in vals]


def _usd(entries):
    return {"units": {"USD": entries}}


BANK_FACTS = {
    "facts": {"us-gaap": {
        "Assets": _usd(_inst([("2024-12-31", 4.0e12), ("2025-12-31", 4.36e12)])),
        "Liabilities": _usd(_inst([("2024-12-31", 3.66e12), ("2025-12-31", 4.0e12)])),
        "StockholdersEquity": _usd(_inst([("2024-12-31", 3.45e11), ("2025-12-31", 3.6e11)])),
        "Deposits": _usd(_inst([("2024-12-31", 2.4e12), ("2025-12-31", 2.5e12)])),
        "NetIncomeLoss": _usd(_dur([("2023-01-01", "2023-12-31", 4.95e10),
                                     ("2024-01-01", "2024-12-31", 5.8e10),
                                     ("2025-01-01", "2025-12-31", 5.4e10)])),
        "InterestIncomeExpenseNet": _usd(_dur([("2025-01-01", "2025-12-31", 9.2e10)])),
        "ProvisionForLoanLeaseAndOtherLosses": _usd(_dur([("2025-01-01", "2025-12-31", 1.1e10)])),
        "RetainedEarningsAccumulatedDeficit": _usd(_inst([("2024-12-31", 3.1e11), ("2025-12-31", 3.4e11)])),
        "LongTermDebtNoncurrent": _usd(_inst([("2025-12-31", 3.9e11)])),
        "CashAndCashEquivalentsAtCarryingValue": _usd(_inst([("2025-12-31", 4.7e11)])),
        # Abandoned in 2009, exactly as JPMorgan's real filings show.
        "TierOneRiskBasedCapitalToRiskWeightedAssets": {"units": {"pure": _inst([("2009-12-31", 0.1076)])}},
    }}
}

BANK_CLASSIFICATION = build_classification({"sic": "6022", "sicDescription": "State Commercial Banks",
                                            "exchanges": ["NYSE"]})


def _evaluate(facts, classification, ticker="TEST"):
    cf = N.build_company_financials(ticker, 1, "Test Co", dict(facts))
    base = score_company(cf)
    return base, evaluate_with_sector(base, cf, dict(facts), classification)


# --- Staleness guard -----------------------------------------------------

def test_stale_facts_are_detected():
    assert is_stale("2009-12-31", "2025-12-31") is True
    assert is_stale("2025-09-30", "2025-12-31") is False
    assert is_stale(None, "2025-12-31") is False


def test_abandoned_regulatory_ratio_is_never_presented_as_current():
    """JPMorgan stopped tagging Tier 1 capital in 2009. It must not surface."""
    _, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    blob = repr(result)
    assert "0.1076" not in blob
    assert "2009" not in blob


# --- Bank behaviour ------------------------------------------------------

def test_bank_generic_rules_become_not_applicable_not_failures():
    base, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    by_id = {r["id"]: r for r in result["rules"]}
    for rule_id in ("liquidity", "debt_to_equity"):
        assert by_id[rule_id]["status"] == NOT_APPLICABLE, rule_id
        assert by_id[rule_id]["points_available"] == 0
        assert by_id[rule_id]["points_earned"] == 0
        assert by_id[rule_id]["not_applicable_reason"]
    # The rule is still shown to the user, never silently dropped.
    assert "deposits" in by_id["debt_to_equity"]["explanation"].lower()


def test_bank_gets_bank_specific_rules_from_real_concepts():
    _, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    by_id = {r["id"]: r for r in result["rules"]}
    assert by_id["bank_capital_strength"]["status"] in (PASS, WATCH, FAIL)
    assert by_id["bank_deposit_funding"]["status"] == PASS       # 2.5T/4.36T = 57%
    assert by_id["bank_roe"]["status"] == PASS                    # 54B/360B = 15%
    assert by_id["bank_credit_cost"]["status"] == PASS            # 11B/92B = 12%
    # Capital strength must be labelled as NOT a regulatory ratio.
    assert "not a regulatory" in by_id["bank_capital_strength"]["formula"].lower()


def test_bank_score_improves_once_inappropriate_rules_stop_counting():
    """The core Phase 2 fix: a sound bank should not be dragged down by
    rules that describe an operating company's balance sheet."""
    base, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    assert result["overall_score"] > base["overall_score"]
    assert result["peer_group"] == "bank"
    assert "liquidity" in result["not_applicable_rules"]


def test_not_applicable_and_unavailable_are_excluded_from_the_denominator():
    _, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    scored = [r for r in result["rules"] if r["points_available"] > 0]
    excluded = [r for r in result["rules"] if r["points_available"] == 0]
    assert result["points_available_scored"] == sum(r["points_available"] for r in scored)
    for r in excluded:
        assert r["status"] in (NOT_APPLICABLE, UNAVAILABLE, PASS)  # PASS only for 0-weight context rules
        assert r["points_earned"] == 0


def test_unavailable_is_distinct_from_not_applicable():
    """Two different statements: 'we have no data' vs 'this does not apply'."""
    facts = {"facts": {"us-gaap": {
        "Assets": _usd(_inst([("2025-12-31", 4.0e12)])),
        "StockholdersEquity": _usd(_inst([("2025-12-31", 3.6e11)])),
    }}}
    _, result = _evaluate(facts, BANK_CLASSIFICATION)
    by_id = {r["id"]: r for r in result["rules"]}
    # No deposits reported -> UNAVAILABLE (data gap), with an explanation.
    assert by_id["bank_deposit_funding"]["status"] == UNAVAILABLE
    assert "unavailable" in by_id["bank_deposit_funding"]["explanation"].lower()
    # Liquidity is inapplicable regardless of data -> NOT_APPLICABLE.
    assert by_id["liquidity"]["status"] in (NOT_APPLICABLE, UNAVAILABLE)


# --- Other peer groups ---------------------------------------------------

REIT_FACTS = {
    "facts": {"us-gaap": {
        "Assets": _usd(_inst([("2025-12-31", 5.0e10)])),
        "StockholdersEquity": _usd(_inst([("2025-12-31", 2.6e10)])),
        "LongTermDebtNoncurrent": _usd(_inst([("2025-12-31", 2.0e10)])),
        "NetIncomeLoss": _usd(_dur([("2023-01-01", "2023-12-31", 8.7e8),
                                     ("2024-01-01", "2024-12-31", 8.6e8),
                                     ("2025-01-01", "2025-12-31", 9.0e8)])),
        "OperatingIncomeLoss": _usd(_dur([("2025-01-01", "2025-12-31", 1.6e9)])),
        "InterestExpense": _usd(_dur([("2025-01-01", "2025-12-31", 5.5e8)])),
        "NetCashProvidedByUsedInOperatingActivities": _usd(_dur([("2025-01-01", "2025-12-31", 3.3e9)])),
        "PaymentsOfDividends": _usd(_dur([("2025-01-01", "2025-12-31", 2.6e9)])),
        "DepreciationDepletionAndAmortization": _usd(_dur([("2025-01-01", "2025-12-31", 1.7e9)])),
        "RetainedEarningsAccumulatedDeficit": _usd(_inst([("2024-12-31", -3.0e9), ("2025-12-31", -3.6e9)])),
    }}
}


def test_reit_retained_earnings_rules_are_not_applicable():
    """A REIT must distribute its income, so negative retained earnings is
    the required payout structure - not evidence of losses."""
    clf = build_classification({"sic": "6798", "sicDescription": "REIT", "exchanges": ["NYSE"]})
    base, result = _evaluate(REIT_FACTS, clf)
    by_id = {r["id"]: r for r in result["rules"]}
    assert result["peer_group"] == "reit"
    assert by_id["retained_earnings"]["status"] == NOT_APPLICABLE
    assert by_id["retained_earnings_growth"]["status"] == NOT_APPLICABLE
    assert "distribute" in by_id["retained_earnings"]["explanation"].lower()
    assert by_id["reit_distribution_coverage"]["status"] == PASS   # 3.3B/2.6B = 1.27x
    assert result["overall_score"] > base["overall_score"]


def test_reit_partial_ffo_is_labelled_as_partial_and_unscored():
    clf = build_classification({"sic": "6798", "sicDescription": "REIT"})
    _, result = _evaluate(REIT_FACTS, clf)
    ffo = {r["id"]: r for r in result["rules"]}["reit_ffo"]
    assert "partial" in ffo["name"].lower()
    assert ffo["points_available"] == 0          # context only, never scored
    assert "does not report" in ffo["explanation"] or "partial FFO" in ffo["explanation"]


UTILITY_FACTS = {
    "facts": {"us-gaap": {
        "Assets": _usd(_inst([("2025-12-31", 1.0e11)])),
        "StockholdersEquity": _usd(_inst([("2025-12-31", 3.0e10)])),
        "LongTermDebtNoncurrent": _usd(_inst([("2025-12-31", 4.2e10)])),
        "OperatingIncomeLoss": _usd(_dur([("2025-01-01", "2025-12-31", 6.0e9)])),
        "InterestExpense": _usd(_dur([("2025-01-01", "2025-12-31", 1.7e9)])),
        "NetCashProvidedByUsedInOperatingActivities": _usd(_dur([("2025-01-01", "2025-12-31", 1.0e10)])),
        "PaymentsToAcquirePropertyPlantAndEquipment": _usd(_dur([("2025-01-01", "2025-12-31", -9.0e9)])),
        "NetIncomeLoss": _usd(_dur([("2023-01-01", "2023-12-31", 3.9e9),
                                     ("2024-01-01", "2024-12-31", 4.0e9),
                                     ("2025-01-01", "2025-12-31", 4.1e9)])),
    }}
}


def test_utility_leverage_uses_utility_thresholds_not_tech_thresholds():
    """42% debt/assets is normal for a regulated utility and must not be
    scored against a technology company's expectations."""
    clf = build_classification({"sic": "4911", "sicDescription": "Electric Services"})
    _, result = _evaluate(UTILITY_FACTS, clf)
    by_id = {r["id"]: r for r in result["rules"]}
    assert result["peer_group"] == "utility"
    assert by_id["sector_leverage"]["status"] == PASS
    assert by_id["sector_interest_coverage"]["status"] == PASS    # 6.0/1.7 = 3.5x
    assert by_id["earnings_stability"]["status"] == PASS

    # The very same balance sheet is judged more strictly for a software
    # company, where 42% debt/assets is not the norm it is for a utility.
    tech = build_classification({"sic": "7372", "sicDescription": "Software"})
    _, tech_result = _evaluate(UTILITY_FACTS, tech)
    tech_leverage = {r["id"]: r for r in tech_result["rules"]}["sector_leverage"]
    assert tech_leverage["status"] in (WATCH, FAIL)
    assert tech_leverage["status"] != by_id["sector_leverage"]["status"]


def test_semiconductor_gets_technology_rules_and_rd_is_context_only():
    facts = dict(UTILITY_FACTS)
    facts = {"facts": {"us-gaap": dict(UTILITY_FACTS["facts"]["us-gaap"], **{
        "Revenues": _usd(_dur([("2025-01-01", "2025-12-31", 6.0e10)])),
        "GrossProfit": _usd(_dur([("2025-01-01", "2025-12-31", 4.2e10)])),
        "ResearchAndDevelopmentExpense": _usd(_dur([("2025-01-01", "2025-12-31", 1.2e10)])),
    })}}
    clf = build_classification({"sic": "3674", "sicDescription": "Semiconductors"})
    _, result = _evaluate(facts, clf)
    by_id = {r["id"]: r for r in result["rules"]}
    assert result["peer_group"] == "semiconductor"
    assert by_id["sector_gross_margin"]["status"] == PASS         # 70%
    rd = by_id["sector_rd_intensity"]
    assert rd["points_available"] == 0                             # context, not a verdict
    assert "reinvestment" in rd["explanation"]


def test_unknown_peer_group_keeps_the_universal_rules_unchanged():
    """A company we have no specific model for must behave exactly as before."""
    clf = build_classification({"sic": "2000", "sicDescription": "Food"})
    base, result = _evaluate(UTILITY_FACTS, clf)
    assert result["peer_group"] == "general"
    assert result["sector_rule_count"] == 0
    assert result["overall_score"] == base["overall_score"]
    assert not result["not_applicable_rules"]


def test_every_rule_carries_provenance_and_confidence_is_reported():
    _, result = _evaluate(BANK_FACTS, BANK_CLASSIFICATION)
    for r in result["rules"]:
        assert r["status"] in (PASS, WATCH, FAIL, NOT_APPLICABLE, UNAVAILABLE)
        assert r["explanation"]
        assert r["source"]
        assert "points_available" in r and "points_earned" in r
    assert 0 <= result["data_confidence_pct"] <= 100
    assert result["data_confidence_note"]
