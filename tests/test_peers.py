"""
Peer context.

The single most important thing this module must do is refuse to invent a
benchmark. There is no peer-data provider behind this analysis, so a "sector
average" would be fabricated - and a fabricated benchmark is worse than no
benchmark, because the reader cannot tell the difference. What is real is the
company's own reported history, and that is what is offered, clearly labelled
as such.
"""
from backend.app.peers import MIN_HISTORY, build_peer_context, _position


def _row(fy, **kw):
    row = {"fiscal_year": fy, "period_end": f"{fy}-09-30"}
    row.update(kw)
    return row


TIMELINE = [
    _row(2025, net_margin_pct=26.9, gross_margin_pct=46.2, net_income=100.0,
         equity=60.0, total_debt=90.0, free_cash_flow=110.0, revenue=400.0),
    _row(2024, net_margin_pct=24.0, gross_margin_pct=45.9, net_income=94.0,
         equity=58.0, total_debt=95.0, free_cash_flow=100.0, revenue=380.0),
    _row(2023, net_margin_pct=25.3, gross_margin_pct=44.1, net_income=88.0,
         equity=56.0, total_debt=99.0, free_cash_flow=92.0, revenue=360.0),
    _row(2022, net_margin_pct=25.3, gross_margin_pct=43.3, net_income=80.0,
         equity=54.0, total_debt=105.0, free_cash_flow=85.0, revenue=340.0),
    _row(2021, net_margin_pct=25.9, gross_margin_pct=41.8, net_income=70.0,
         equity=50.0, total_debt=110.0, free_cash_flow=75.0, revenue=300.0),
]


def _by_key(res):
    return {c["key"]: c for c in res["comparisons"]}


# --- The benchmark that does not exist ----------------------------------

def test_a_cross_company_benchmark_is_reported_unavailable_never_invented():
    res = build_peer_context(TIMELINE, "Hardware", "note")
    assert res["peer_benchmark"]["available"] is False
    assert "no peer-data provider is configured" in res["peer_benchmark"]["reason"]


def test_the_self_comparison_is_labelled_as_such_and_not_as_a_peer_average():
    res = build_peer_context(TIMELINE)
    assert res["basis"] == "This company's own reported history"
    assert "not against other companies" in res["source"]


def test_peer_group_label_is_carried_through_without_becoming_a_benchmark():
    res = build_peer_context(TIMELINE, peer_group_label="Bank", peer_group_note="Banks fund...")
    assert res["peer_group_label"] == "Bank"
    assert res["peer_group_note"] == "Banks fund..."
    # Naming the peer group must not make a numeric benchmark appear.
    assert res["peer_benchmark"]["available"] is False


# --- Positioning against the company's own range -------------------------

def test_current_figure_is_placed_within_the_companys_own_range():
    nm = _by_key(build_peer_context(TIMELINE))["net_margin"]
    assert nm["available"] is True
    assert nm["current"] == 26.9
    assert nm["max"] == 26.9 and nm["min"] == 24.0
    assert nm["position_pct"] == 100.0      # the highest it has been
    assert nm["periods"] == 5


def test_position_is_the_midpoint_when_the_history_is_flat():
    """Every period identical means 'highest ever' and 'lowest ever' are
    equally meaningless, so neither is claimed."""
    assert _position([5.0, 5.0, 5.0], 5.0) == 50.0


def test_position_scales_between_the_min_and_max():
    assert _position([0.0, 10.0, 5.0], 5.0) == 50.0
    assert _position([0.0, 10.0], 0.0) == 0.0
    assert _position([0.0, 10.0], 10.0) == 100.0


def test_reading_names_the_period_and_the_range_it_is_measured_against():
    nm = _by_key(build_peer_context(TIMELINE))["net_margin"]
    assert "FY2025" in nm["reading"]
    assert "own" in nm["reading"]
    assert "range" in nm["reading"]


# --- Direction of "better" differs by metric -----------------------------

def test_lower_leverage_counts_as_better_than_its_own_average():
    lev = _by_key(build_peer_context(TIMELINE))["leverage"]
    assert lev["available"] is True
    # Debt/equity falls across the fixture, so the latest is below average.
    assert lev["current"] < lev["average"]
    assert lev["better_than_own_average"] is True


def test_higher_margin_counts_as_better_than_its_own_average():
    nm = _by_key(build_peer_context(TIMELINE))["net_margin"]
    assert nm["current"] > nm["average"]
    assert nm["better_than_own_average"] is True


# --- Not enough history --------------------------------------------------

def test_too_little_history_is_unavailable_with_a_reason_not_a_position():
    short = TIMELINE[:2]
    nm = _by_key(build_peer_context(short))["net_margin"]
    assert nm["available"] is False
    assert str(MIN_HISTORY) in nm["reason"]
    assert "position_pct" not in nm


def test_an_empty_timeline_offers_no_comparisons_at_all():
    res = build_peer_context([])
    assert res["available"] is False
    assert res["available_count"] == 0
    assert all(c["available"] is False for c in res["comparisons"])


def test_periods_missing_the_figure_are_skipped_not_zero_filled():
    timeline = [_row(2025, net_margin_pct=26.9), _row(2024), _row(2023, net_margin_pct=25.3)]
    nm = _by_key(build_peer_context(timeline))["net_margin"]
    assert nm["available"] is False          # only two usable periods
    assert "2 for this company" in nm["reason"]


# --- Derived ratios refuse nonsense inputs -------------------------------

def test_negative_equity_periods_are_excluded_from_derived_ratios():
    timeline = [
        _row(2025, net_income=10.0, equity=-20.0, total_debt=100.0),
        _row(2024, net_income=10.0, equity=50.0, total_debt=100.0),
        _row(2023, net_income=10.0, equity=50.0, total_debt=100.0),
    ]
    res = _by_key(build_peer_context(timeline))
    # Two usable periods remain, which is below the minimum - so unavailable
    # rather than a ratio computed off a negative book value.
    assert res["roe"]["available"] is False
    assert res["leverage"]["available"] is False


def test_zero_revenue_periods_do_not_produce_an_infinite_margin():
    timeline = [
        _row(2025, free_cash_flow=10.0, revenue=0.0),
        _row(2024, free_cash_flow=10.0, revenue=100.0),
        _row(2023, free_cash_flow=10.0, revenue=100.0),
        _row(2022, free_cash_flow=10.0, revenue=100.0),
    ]
    fcf = _by_key(build_peer_context(timeline))["fcf_margin"]
    assert fcf["available"] is True
    assert all(abs(p["value"]) < 1000 for p in fcf["series"])
    assert fcf["periods"] == 3               # the zero-revenue period dropped
