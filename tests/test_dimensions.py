"""
The 2D financial-health dimension overview.

This is the flat companion to the 3D Financial Core, and it aggregates the
same scored rules. The risk it carries is that aggregation quietly destroys
the distinctions the rules were careful to make - a set-aside bank rule
becoming a zero, an unmeasurable dimension looking like a failing one, or an
unscored rule dragging down a score it was explicitly excluded from. These
tests pin those distinctions.
"""
from backend.app.dimensions import build_dimensions


def _rule(category, name, status, earned=0, available=0, rule_type="universal", **kw):
    r = {"category": category, "name": name, "status": status,
         "points_earned": earned, "points_available": available,
         "rule_type": rule_type, "value": kw.get("value"),
         "not_applicable_reason": kw.get("not_applicable_reason")}
    return r


# Shaped after JPMorgan's real rule set: the generic liquidity and leverage
# rules are set aside for a bank, and sector rules carry the score instead.
BANK_RULES = [
    _rule("Liquidity", "Liquidity", "NOT_APPLICABLE", value="-$63.57B",
          not_applicable_reason="Banks do not report current assets or current liabilities."),
    _rule("Liquidity", "Deposit Funding", "PASS", 15, 15, "sector", value="54.1%"),
    _rule("Debt & Leverage", "Debt & Leverage", "NOT_APPLICABLE", value="12.39x",
          not_applicable_reason="High leverage is the normal structure of a healthy bank."),
    _rule("Capital Structure", "Capital Structure", "WATCH", 2.5, 5, value="$8.15B"),
    _rule("Capital Structure", "Capital Strength (Equity / Assets)", "WATCH", 10, 20, "sector", value="7.5%"),
    _rule("Cash Generation", "Cash Generation", "UNAVAILABLE", value="Not reported"),
    _rule("Profitability", "Return on Equity", "PASS", 20, 20, "sector", value="15.2%"),
]


def _by_name(res):
    return {d["name"]: d for d in res["dimensions"]}


# --- The three distinctions ---------------------------------------------

def test_a_set_aside_generic_rule_does_not_drag_its_dimension_down():
    """The bank's generic liquidity rule is not applicable, and the sector
    rule that replaced it passes - so Liquidity must read as passing, not as
    half marks for a rule that was deliberately excluded."""
    liq = _by_name(build_dimensions({"rules": BANK_RULES}))["Liquidity"]
    assert liq["status"] == "PASS"
    assert liq["score"] == 100          # 15/15, the NOT_APPLICABLE rule excluded
    assert liq["points_available"] == 15
    assert liq["sector_specific"] is True


def test_a_dimension_with_only_a_set_aside_rule_is_not_applicable_not_zero():
    lev = _by_name(build_dimensions({"rules": BANK_RULES}))["Debt & Leverage"]
    assert lev["status"] == "NOT_APPLICABLE"
    assert lev["score"] is None         # never 0
    assert lev["scored"] is False
    assert "normal structure of a healthy bank" in lev["interpretation"]


def test_an_unmeasurable_dimension_is_unavailable_not_a_failure():
    cash = _by_name(build_dimensions({"rules": BANK_RULES}))["Cash Generation"]
    assert cash["status"] == "UNAVAILABLE"
    assert cash["score"] is None        # never 0
    assert "could not be measured" in cash["interpretation"]


def test_not_applicable_and_unavailable_are_never_conflated():
    d = _by_name(build_dimensions({"rules": BANK_RULES}))
    assert d["Debt & Leverage"]["status"] == "NOT_APPLICABLE"
    assert d["Cash Generation"]["status"] == "UNAVAILABLE"
    assert d["Debt & Leverage"]["interpretation"] != d["Cash Generation"]["interpretation"]


# --- Scoring -------------------------------------------------------------

def test_score_is_out_of_points_actually_available_in_that_dimension():
    cap = _by_name(build_dimensions({"rules": BANK_RULES}))["Capital Structure"]
    assert cap["points_earned"] == 12.5
    assert cap["points_available"] == 25
    assert cap["score"] == 50


def test_dimension_takes_the_most_serious_scored_status():
    rules = [
        _rule("Mixed", "A", "PASS", 10, 10),
        _rule("Mixed", "B", "WATCH", 5, 10),
        _rule("Mixed", "C", "FAIL", 0, 10),
    ]
    assert _by_name(build_dimensions({"rules": rules}))["Mixed"]["status"] == "FAIL"


def test_a_watch_outranks_a_pass_but_not_a_fail():
    passing = [_rule("X", "A", "PASS", 10, 10), _rule("X", "B", "WATCH", 5, 10)]
    assert _by_name(build_dimensions({"rules": passing}))["X"]["status"] == "WATCH"


def test_the_primary_metric_is_the_most_heavily_weighted_scored_rule():
    prof = _by_name(build_dimensions({"rules": BANK_RULES}))["Profitability"]
    assert prof["primary_metric"]["name"] == "Return on Equity"
    assert prof["primary_metric"]["value"] == "15.2%"


def test_an_unscored_dimension_still_names_a_representative_rule():
    lev = _by_name(build_dimensions({"rules": BANK_RULES}))["Debt & Leverage"]
    assert lev["primary_metric"]["name"] == "Debt & Leverage"
    assert lev["primary_metric"]["value"] == "12.39x"


# --- Shape ---------------------------------------------------------------

def test_no_rules_yields_an_honest_empty_state():
    res = build_dimensions({"rules": []})
    assert res["available"] is False
    assert res["dimensions"] == []
    assert res["reason"]


def test_dimensions_are_ordered_for_reading_not_alphabetically():
    names = [d["name"] for d in build_dimensions({"rules": BANK_RULES})["dimensions"]]
    assert names.index("Liquidity") < names.index("Profitability")
    assert names != sorted(names)


def test_every_rule_lands_in_exactly_one_dimension():
    res = build_dimensions({"rules": BANK_RULES})
    assert sum(d["rule_count"] for d in res["dimensions"]) == len(BANK_RULES)


def test_scored_count_excludes_unscoreable_dimensions():
    res = build_dimensions({"rules": BANK_RULES})
    assert res["scored_count"] == 3          # Liquidity, Capital Structure, Profitability
    assert res["total_count"] == 5


# --- Direction is borrowed, not recomputed -------------------------------

def test_direction_of_travel_comes_from_the_snapshot_when_present():
    snap = {"metrics": [
        {"key": "roe", "available": True, "direction": "up", "sentiment": "good",
         "change_display": "+2.3 pts"},
    ]}
    prof = _by_name(build_dimensions({"rules": BANK_RULES}, snap))["Profitability"]
    assert prof["direction"] == "up"
    assert prof["direction_sentiment"] == "good"
    assert prof["direction_label"] == "+2.3 pts"


def test_no_direction_is_claimed_when_the_snapshot_has_none():
    prof = _by_name(build_dimensions({"rules": BANK_RULES}))["Profitability"]
    assert prof["direction"] is None
    assert prof["direction_label"] is None


def test_an_unavailable_snapshot_metric_contributes_no_direction():
    snap = {"metrics": [{"key": "roe", "available": False, "direction": "up"}]}
    prof = _by_name(build_dimensions({"rules": BANK_RULES}, snap))["Profitability"]
    assert prof["direction"] is None


# --- Wording cannot contradict the data ----------------------------------

def test_interpretation_counts_match_the_underlying_rule_statuses():
    cap = _by_name(build_dimensions({"rules": BANK_RULES}))["Capital Structure"]
    assert "2 on watch" in cap["interpretation"]
    assert "passing" not in cap["interpretation"]


def test_interpretation_mentions_rules_that_were_set_aside():
    liq = _by_name(build_dimensions({"rules": BANK_RULES}))["Liquidity"]
    assert "set aside" in liq["interpretation"]
    assert "not applicable to this business model" in liq["interpretation"]
