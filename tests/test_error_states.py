"""
Error states, and the contracts the UI keys on.

The frontend decides which explanation to show a reader by inspecting the
status code and the wording of the error detail. That makes the wording a
contract, not a cosmetic string: rephrasing the throttling message so it no
longer says "rate-limiting" would silently downgrade an honest "SEC is
limiting requests, no data is missing" into a generic failure, and nothing
would fail until a user hit it.

These tests hold that contract, and the related invariant that unavailable
data never arrives as a zero.
"""
import httpx
import pytest
import respx

from backend.app import concepts as C
from backend.app.dimensions import build_dimensions
from backend.app.peers import build_peer_context
from backend.app.periods import build_period_options
from backend.app.snapshot import build_snapshot


# --- The throttling contract between backend and UI ---------------------

def test_the_throttling_message_says_rate_limiting_because_the_ui_matches_on_it():
    """frontend/app.js classifies a 503 as throttling via /rate.?limit/i. If
    this phrase changes, the reader gets a generic error instead of the
    accurate one."""
    assert "rate-limiting" in C._THROTTLED_MESSAGE


def test_the_throttling_message_says_no_analysis_rather_than_no_data():
    """The distinction Phase 4 established: SEC refusing to answer is not the
    company reporting nothing."""
    msg = C._THROTTLED_MESSAGE.lower()
    assert "no financial data was retrieved" in msg
    assert "wrongly read as unreported" in msg


@respx.mock
def test_a_throttled_concept_request_surfaces_as_throttling_not_a_missing_concept():
    from backend.app.sec_client import SecClient, SecUnavailableError
    import asyncio

    respx.get(url__regex=r".*companyconcept.*").mock(
        return_value=httpx.Response(429, text="rate limited"))
    client = SecClient()
    with pytest.raises(SecUnavailableError) as exc:
        asyncio.run(C.build_facts_bundle(client, 320193, "Throttled Co"))
    assert "rate-limiting" in str(exc.value)


# --- Unavailable never arrives as zero ----------------------------------

def test_no_section_reports_zero_when_it_means_unavailable():
    """Across every derived section, an absent figure must be None with a
    reason - never 0, which a reader would take as a reported value."""
    empty_timeline: list = []

    snap = build_snapshot(empty_timeline, [])
    for m in snap["metrics"]:
        assert m["available"] is False
        assert m["value"] is None, m["key"]
        assert m["reason"], m["key"]

    peers = build_peer_context(empty_timeline)
    for c in peers["comparisons"]:
        assert c["available"] is False
        assert "current" not in c, c["key"]
        assert c["reason"], c["key"]

    dims = build_dimensions({"rules": []})
    assert dims["available"] is False
    assert dims["dimensions"] == []
    assert dims["reason"]

    periods = build_period_options(empty_timeline, empty_timeline)
    assert periods["any_available"] is False
    assert all(o["reason"] for o in periods["options"])


def test_an_unscoreable_dimension_carries_no_score_of_zero():
    rules = [{"category": "Cash Generation", "name": "Cash Generation",
              "status": "UNAVAILABLE", "points_earned": 0, "points_available": 0,
              "rule_type": "universal", "value": None, "not_applicable_reason": None}]
    d = build_dimensions({"rules": rules})["dimensions"][0]
    assert d["score"] is None
    assert d["points_earned"] is None
    assert d["status"] == "UNAVAILABLE"


# --- Unavailable and not-applicable never read as failure ---------------

def test_not_applicable_and_unavailable_are_distinct_from_fail_everywhere():
    rules = [
        {"category": "A", "name": "A", "status": "NOT_APPLICABLE", "points_earned": 0,
         "points_available": 0, "rule_type": "universal", "value": None,
         "not_applicable_reason": "Not how this business works."},
        {"category": "B", "name": "B", "status": "UNAVAILABLE", "points_earned": 0,
         "points_available": 0, "rule_type": "universal", "value": None,
         "not_applicable_reason": None},
        {"category": "C", "name": "C", "status": "FAIL", "points_earned": 0,
         "points_available": 10, "rule_type": "universal", "value": "1.0",
         "not_applicable_reason": None},
    ]
    by = {d["name"]: d for d in build_dimensions({"rules": rules})["dimensions"]}
    assert by["A"]["status"] == "NOT_APPLICABLE"
    assert by["B"]["status"] == "UNAVAILABLE"
    assert by["C"]["status"] == "FAIL"
    # Only the genuine failure is scored at zero out of something.
    assert by["C"]["score"] == 0 and by["C"]["points_available"] == 10
    assert by["A"]["score"] is None and by["B"]["score"] is None


def test_a_sector_replacement_keeps_the_set_aside_rule_from_reading_as_failure():
    """The JPMorgan case: the generic liquidity rule is set aside and a bank
    measure scores in its place. The dimension must read as passing."""
    rules = [
        {"category": "Liquidity", "name": "Liquidity", "status": "NOT_APPLICABLE",
         "points_earned": 0, "points_available": 0, "rule_type": "universal",
         "value": "-$63.57B", "not_applicable_reason": "Banks fund with deposits."},
        {"category": "Liquidity", "name": "Deposit Funding", "status": "PASS",
         "points_earned": 15, "points_available": 15, "rule_type": "sector",
         "value": "54.1%", "not_applicable_reason": None},
    ]
    d = build_dimensions({"rules": rules})["dimensions"][0]
    assert d["status"] == "PASS"
    assert d["score"] == 100


# --- Insufficient history never becomes a flat trend --------------------

def test_insufficient_history_is_refused_rather_than_flattened():
    one_period = [{"fiscal_year": 2025, "fiscal_period": "FY",
                   "period_end": "2025-09-30", "revenue": 400.0}]
    rev = next(m for m in build_snapshot(one_period)["metrics"] if m["key"] == "revenue")
    assert rev["available"] is True        # the figure is real
    assert rev["has_trend"] is False       # the trend is not claimed
    assert rev["change_pct"] is None

    periods = build_period_options(one_period, [])
    assert periods["options"][0]["available"] is False


def test_a_quarterly_view_with_no_quarters_is_refused_not_drawn_empty():
    annual = [{"fiscal_year": y, "fiscal_period": "FY", "period_end": f"{y}-09-30",
               "revenue": 100.0} for y in (2025, 2024, 2023)]
    opts = {o["key"]: o for o in build_period_options(annual, [])["options"]}
    assert opts["annual"]["available"] is True
    assert opts["quarterly"]["available"] is False
    assert opts["quarterly"]["reason"]


# --- Foreign filers and currency ----------------------------------------

def test_a_non_usd_filer_is_never_labelled_in_dollars():
    timeline = [{"fiscal_year": y, "fiscal_period": "FY", "period_end": f"{y}-12-31",
                 "revenue": 2.894e12} for y in (2024, 2023, 2022)]
    rev = next(m for m in build_snapshot(timeline, [], currency="TWD")["metrics"]
               if m["key"] == "revenue")
    assert "TWD" in rev["display"]
    assert "$" not in rev["display"]
