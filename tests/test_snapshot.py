"""
The visual financial snapshot.

The snapshot is the first thing a reader sees, which makes it the easiest
place to accidentally tell them something untrue: a missing figure drawn as
zero, two points drawn as a trend, or a rising number coloured as good news
when for this company it is neither. These tests pin all three.
"""
from backend.app.snapshot import (
    MIN_TREND_POINTS, SERIES_POINTS, build_snapshot, _pct_change, _consecutive_rises,
)


def _row(fy, **kw):
    row = {"fiscal_year": fy, "fiscal_period": "FY", "period_end": f"{fy}-09-30"}
    row.update(kw)
    return row


# Newest first, matching the timeline the pipeline produces.
GROWING = [
    _row(2025, revenue=400.0, net_income=100.0, equity=60.0, total_debt=90.0,
         free_cash_flow=110.0, operating_cash_flow=120.0, net_margin_pct=25.0),
    _row(2024, revenue=380.0, net_income=94.0, equity=58.0, total_debt=95.0,
         free_cash_flow=100.0, operating_cash_flow=112.0, net_margin_pct=24.7),
    _row(2023, revenue=360.0, net_income=88.0, equity=56.0, total_debt=99.0,
         free_cash_flow=92.0, operating_cash_flow=104.0, net_margin_pct=24.4),
    _row(2022, revenue=340.0, net_income=80.0, equity=54.0, total_debt=105.0,
         free_cash_flow=85.0, operating_cash_flow=97.0, net_margin_pct=23.5),
    _row(2021, revenue=300.0, net_income=70.0, equity=50.0, total_debt=110.0,
         free_cash_flow=75.0, operating_cash_flow=86.0, net_margin_pct=23.3),
]

SCORES = [
    {"fiscal_year": 2022, "period_end": "2022-09-30", "overall_score": 71},
    {"fiscal_year": 2023, "period_end": "2023-09-30", "overall_score": 76},
    {"fiscal_year": 2024, "period_end": "2024-09-30", "overall_score": 81},
    {"fiscal_year": 2025, "period_end": "2025-09-30", "overall_score": 87},
]


def _by_key(snap):
    return {m["key"]: m for m in snap["metrics"]}


# --- Shape ---------------------------------------------------------------

def test_snapshot_answers_one_question_per_metric():
    snap = build_snapshot(GROWING, SCORES)
    assert snap["available"] is True
    for m in snap["metrics"]:
        assert m["question"].endswith("?"), m["key"]
        assert m["label"]


def test_series_are_bounded_and_read_oldest_to_newest():
    long_timeline = [_row(2025 - i, revenue=100.0 + i) for i in range(12)]
    snap = build_snapshot(long_timeline)
    rev = _by_key(snap)["revenue"]
    assert len(rev["series"]) == SERIES_POINTS
    ends = [p["period_end"] for p in rev["series"]]
    assert ends == sorted(ends), "series must read left to right in time"


# --- Nothing invented ----------------------------------------------------

def test_a_metric_with_no_data_is_unavailable_with_a_reason_not_zero():
    snap = build_snapshot([_row(2025, revenue=100.0)])
    m = _by_key(snap)
    assert m["cash_generation"]["available"] is False
    assert m["cash_generation"]["value"] is None      # never 0
    assert m["cash_generation"]["reason"]
    assert m["leverage"]["available"] is False
    assert m["leverage"]["reason"]


def test_an_empty_timeline_produces_no_fabricated_metrics():
    snap = build_snapshot([], [])
    assert snap["available"] is False
    assert snap["available_count"] == 0
    assert all(m["available"] is False for m in snap["metrics"])
    assert all(m["value"] is None for m in snap["metrics"])


def test_periods_missing_a_figure_are_dropped_not_zero_filled():
    timeline = [_row(2025, revenue=400.0), _row(2024), _row(2023, revenue=360.0)]
    rev = _by_key(build_snapshot(timeline))["revenue"]
    assert [p["value"] for p in rev["series"]] == [360.0, 400.0]
    assert 0 not in [p["value"] for p in rev["series"]]


# --- Two points are not a trend -----------------------------------------

def test_two_periods_do_not_claim_a_trend():
    timeline = [_row(2025, revenue=400.0), _row(2024, revenue=380.0)]
    rev = _by_key(build_snapshot(timeline))["revenue"]
    assert rev["available"] is True          # the value is still real
    assert rev["has_trend"] is False         # but it is not a trend
    assert len(rev["series"]) < MIN_TREND_POINTS


def test_three_periods_is_enough_for_a_trend():
    timeline = [_row(2025, revenue=400.0), _row(2024, revenue=380.0), _row(2023, revenue=360.0)]
    assert _by_key(build_snapshot(timeline))["revenue"]["has_trend"] is True


def test_a_single_period_says_so_rather_than_implying_direction():
    rev = _by_key(build_snapshot([_row(2025, revenue=400.0)]))["revenue"]
    assert rev["change_pct"] is None
    assert "only one period" in rev["reading"].lower()


# --- Direction is not a verdict -----------------------------------------

def test_rising_revenue_reads_as_good_and_rising_debt_does_not():
    m = _by_key(build_snapshot(GROWING, SCORES))
    assert m["revenue"]["direction"] == "up"
    assert m["revenue"]["sentiment"] == "good"
    # Debt/equity falls across this fixture, which is favourable.
    assert m["leverage"]["direction"] == "down"
    assert m["leverage"]["sentiment"] == "good"


def test_rising_leverage_is_flagged_unfavourable():
    rising = [
        _row(2025, total_debt=150.0, equity=50.0),
        _row(2024, total_debt=100.0, equity=50.0),
        _row(2023, total_debt=80.0, equity=50.0),
    ]
    lev = _by_key(build_snapshot(rising))["leverage"]
    assert lev["direction"] == "up"
    assert lev["sentiment"] == "bad"


def test_a_banks_leverage_is_reported_without_a_verdict():
    """Banks fund themselves with deposits, so a generic debt-to-equity
    reading is a fact about the business model, not a warning."""
    rising = [
        _row(2025, total_debt=150.0, equity=50.0),
        _row(2024, total_debt=100.0, equity=50.0),
        _row(2023, total_debt=80.0, equity=50.0),
    ]
    lev = _by_key(build_snapshot(rising, peer_group="bank"))["leverage"]
    assert lev["available"] is True
    assert lev["direction"] == "up"          # the fact is still reported
    assert lev["sentiment"] == "neutral"     # the judgement is withheld
    assert lev["note"]


def test_negative_free_cash_flow_is_called_out_as_consuming_cash():
    burning = [
        _row(2025, free_cash_flow=-40.0),
        _row(2024, free_cash_flow=-25.0),
        _row(2023, free_cash_flow=-10.0),
    ]
    cash = _by_key(build_snapshot(burning))["cash_generation"]
    assert cash["sentiment"] == "bad"
    assert "consumed cash" in cash["reading"]


# --- Percentage changes that would mislead are withheld ------------------

def test_percentage_change_across_a_sign_flip_is_not_stated():
    """Going from -$2B to +$1B is not '150% growth'. The direction is still
    reported; the misleading percentage is not."""
    assert _pct_change(1.0, -2.0) is None
    assert _pct_change(-1.0, 2.0) is None
    assert _pct_change(110.0, 100.0) == 10.0
    assert _pct_change(100.0, 0) is None      # no percentage from a zero base


def test_consecutive_rise_counting_stops_at_the_first_decline():
    assert _consecutive_rises([5, 4, 3, 2]) == 3      # newest first
    assert _consecutive_rises([5, 4, 9, 2]) == 1
    assert _consecutive_rises([5, 6, 7]) == 0
    assert _consecutive_rises([5, None, 3]) == 0


# --- Fallbacks -----------------------------------------------------------

def test_operating_cash_flow_substitutes_when_free_cash_flow_is_absent():
    timeline = [
        _row(2025, operating_cash_flow=120.0),
        _row(2024, operating_cash_flow=100.0),
        _row(2023, operating_cash_flow=90.0),
    ]
    cash = _by_key(build_snapshot(timeline))["cash_generation"]
    assert cash["available"] is True
    assert cash["label"] == "Operating Cash Flow"     # relabelled honestly
    assert cash["value"] == 120.0


def test_negative_equity_does_not_produce_a_nonsense_ratio():
    """A negative book value makes debt/equity and ROE meaningless rather
    than merely large."""
    timeline = [_row(2025, total_debt=100.0, equity=-20.0, net_income=10.0)]
    m = _by_key(build_snapshot(timeline))
    assert m["leverage"]["available"] is False
    assert m["roe"]["available"] is False


# --- Financial health trend ---------------------------------------------

def test_health_trend_reads_from_the_real_score_history():
    ht = _by_key(build_snapshot(GROWING, SCORES))["health_trend"]
    assert ht["available"] is True
    assert ht["value"] == 87
    assert [p["value"] for p in ht["series"]] == [71, 76, 81, 87]
    assert ht["direction"] == "up"
    assert ht["change_display"] == "+6 pts"


def test_health_trend_is_absent_rather_than_invented_when_unscoreable():
    ht = _by_key(build_snapshot(GROWING, []))["health_trend"]
    assert ht["available"] is False
    assert ht["series"] == []
    assert "could not be calculated" in ht["reason"]


def test_currency_is_carried_through_and_never_assumed_to_be_dollars():
    snap = build_snapshot(GROWING, SCORES, currency="TWD")
    assert snap["currency"] == "TWD"
    assert "TWD" in _by_key(snap)["revenue"]["display"]
    assert "$" not in _by_key(snap)["revenue"]["display"]


# --- The REIT shape ------------------------------------------------------
#
# Realty Income is the live example: it distributes its income rather than
# retaining it, so it does not tag retained earnings at all, and it reports
# almost no period under the debt spellings the rules query. Its analysis is
# therefore full of legitimately unavailable measures. What must NOT happen is
# the page treating that as a company in trouble, or as an empty page.

REIT_LIKE = [
    _row(2025, revenue=5300.0, net_income=860.0, equity=39000.0,
         free_cash_flow=3100.0, operating_cash_flow=3300.0, net_margin_pct=16.2),
    _row(2024, revenue=5000.0, net_income=860.0, equity=38000.0,
         free_cash_flow=2900.0, operating_cash_flow=3100.0, net_margin_pct=17.2),
    _row(2023, revenue=4080.0, net_income=872.0, equity=36000.0,
         free_cash_flow=2400.0, operating_cash_flow=2600.0, net_margin_pct=21.4),
]


def test_a_filer_with_no_retained_earnings_still_gets_a_snapshot():
    """No retained earnings means no historical score series, which must cost
    that one metric and nothing else."""
    snap = build_snapshot(REIT_LIKE, [])
    m = _by_key(snap)
    assert snap["available"] is True
    assert m["revenue"]["available"] is True
    assert m["cash_generation"]["available"] is True
    assert m["health_trend"]["available"] is False


def test_debt_reported_in_only_one_period_yields_no_leverage_reading():
    """One data point is a figure, not a position. Reporting it as a trend -
    or as zero leverage - would both be wrong."""
    sparse_debt = [dict(r) for r in REIT_LIKE]
    sparse_debt[2]["total_debt"] = 3975.0        # a single historical period
    lev = _by_key(build_snapshot(sparse_debt))["leverage"]
    assert lev["available"] is True               # the one real figure is shown
    assert lev["has_trend"] is False              # but no trend is claimed
    assert len(lev["series"]) == 1


def test_no_debt_data_at_all_is_unavailable_rather_than_zero_leverage():
    lev = _by_key(build_snapshot(REIT_LIKE))["leverage"]
    assert lev["available"] is False
    assert lev["value"] is None
    assert lev["sentiment"] == "neutral"          # never coloured as a failure
