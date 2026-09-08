"""
Annual vs quarterly period selection.

Two failure modes matter here. The first is offering a quarterly view to a
company that has none and drawing an empty chart, which a reader sees as
reported zeros rather than as absent data. The second is blending the two -
comparing a quarter against a fiscal year - which is the kind of error a
financial product does not get to make.
"""
from backend.app.periods import (
    MIN_TREND_POINTS, build_period_options, usable_rows,
)


def _annual(fy, **kw):
    row = {"fiscal_year": fy, "fiscal_period": "FY", "period_end": f"{fy}-09-30"}
    row.update(kw)
    return row


def _quarter(fy, q, **kw):
    row = {"fiscal_year": fy, "fiscal_period": q, "period_end": f"{fy}-0{q[-1]}-30"}
    row.update(kw)
    return row


ANNUAL = [_annual(y, revenue=100.0 + y) for y in (2025, 2024, 2023, 2022)]
QUARTERLY = [_quarter(2025, q, revenue=25.0) for q in ("Q4", "Q3", "Q2", "Q1")]


def _by_key(res):
    return {o["key"]: o for o in res["options"]}


# --- Both views offered when both exist ---------------------------------

def test_both_views_are_offered_when_both_have_enough_history():
    res = build_period_options(ANNUAL, QUARTERLY)
    o = _by_key(res)
    assert o["annual"]["available"] is True
    assert o["quarterly"]["available"] is True
    assert res["any_available"] is True


def test_annual_is_the_default_so_the_page_stays_on_one_footing():
    """Every scored rule, the snapshot and the peer position are annual, so
    the trend view starts there too."""
    assert build_period_options(ANNUAL, QUARTERLY)["default"] == "annual"


def test_period_counts_and_range_labels_describe_what_is_there():
    o = _by_key(build_period_options(ANNUAL, QUARTERLY))
    assert o["annual"]["periods"] == 4
    assert o["annual"]["first_label"] == "FY2022"
    assert o["annual"]["last_label"] == "FY2025"
    assert o["quarterly"]["first_label"] == "Q1 2025"
    assert o["quarterly"]["last_label"] == "Q4 2025"


# --- A view with no history is refused, with a reason -------------------

def test_no_quarterly_history_is_unavailable_with_a_reason():
    o = _by_key(build_period_options(ANNUAL, []))
    assert o["quarterly"]["available"] is False
    assert o["quarterly"]["periods"] == 0
    assert "no usable" in o["quarterly"]["reason"]
    assert o["annual"]["available"] is True      # the other view is unaffected


def test_too_few_quarters_is_refused_rather_than_drawn_as_a_trend():
    two = QUARTERLY[:2]
    o = _by_key(build_period_options(ANNUAL, two))
    assert o["quarterly"]["available"] is False
    assert o["quarterly"]["periods"] == 2
    assert str(MIN_TREND_POINTS) in o["quarterly"]["reason"]


def test_exactly_the_minimum_is_enough():
    o = _by_key(build_period_options(ANNUAL, QUARTERLY[:MIN_TREND_POINTS]))
    assert o["quarterly"]["available"] is True


def test_a_company_with_neither_view_offers_no_default():
    res = build_period_options([], [])
    assert res["any_available"] is False
    assert res["default"] is None
    assert all(o["available"] is False for o in res["options"])


def test_quarterly_becomes_the_default_only_when_annual_is_absent():
    res = build_period_options([], QUARTERLY)
    assert res["default"] == "quarterly"


# --- The two views are never blended ------------------------------------

def test_fiscal_year_rows_are_rejected_from_the_quarterly_view():
    """A company whose only interim facts are cumulative year-to-date figures
    must not appear to have quarterly history."""
    disguised = [_annual(2025, revenue=400.0), _annual(2024, revenue=380.0),
                 _annual(2023, revenue=360.0)]
    o = _by_key(build_period_options(ANNUAL, disguised))
    assert o["quarterly"]["available"] is False
    assert o["quarterly"]["periods"] == 0


def test_usable_rows_filters_fiscal_years_out_of_quarterly_but_not_annual():
    mixed = [_quarter(2025, "Q3", revenue=25.0), _annual(2024, revenue=380.0)]
    assert len(usable_rows(mixed, quarterly=True)) == 1
    assert len(usable_rows(mixed)) == 2


def test_the_note_states_that_views_are_not_combined():
    assert "never combined" in build_period_options(ANNUAL, QUARTERLY)["note"]


# --- Rows that would plot nothing do not count --------------------------

def test_rows_carrying_only_a_date_are_not_counted_as_usable():
    empty_rows = [{"fiscal_year": y, "fiscal_period": "FY", "period_end": f"{y}-09-30"}
                  for y in (2025, 2024, 2023)]
    o = _by_key(build_period_options(empty_rows, []))
    assert o["annual"]["available"] is False
    assert o["annual"]["periods"] == 0


def test_a_row_with_any_substantive_figure_counts():
    for field in ("revenue", "net_income", "operating_cash_flow", "cash", "equity", "eps"):
        rows = [{"fiscal_year": 2025 - i, "fiscal_period": "FY",
                 "period_end": f"{2025 - i}-09-30", field: 1.0} for i in range(3)]
        assert _by_key(build_period_options(rows, []))["annual"]["available"] is True, field


def test_none_timelines_are_handled_without_error():
    res = build_period_options(None, None)
    assert res["any_available"] is False
    assert res["default"] is None
