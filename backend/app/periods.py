"""
Annual and quarterly, decided once and honestly.

The page offers a period toggle, and there are two ways to get that wrong.

The first is architectural: asking the server for a whole second analysis just
to redraw one chart. Everything else in that response - the score, the rules,
the story, the market data - is identical, and re-running it is wasted work on
both sides. Both period views are instead built from the same company facts
already in memory, in the same pass, which costs one extra walk over data we
hold and no SEC traffic at all.

The second is presentational: offering the toggle to a company that has no
usable quarterly history, and drawing an empty chart when the reader presses
it. An empty chart is not a neutral outcome - it reads as a company that
reported nothing, or worse, as zeros. So availability is decided here, up
front, and the reader is told which periods exist before they click.

The two views are never blended. A quarterly row is one fiscal quarter and an
annual row is one fiscal year; mixing them would silently compare a quarter
against a year, which is the sort of error a financial product does not get to
make twice.
"""
from __future__ import annotations

from typing import Any, Optional

# Same threshold the snapshot uses: under three points there is no trend to
# show, only a couple of dots that imply one.
MIN_TREND_POINTS = 3

# Fields a period row must carry at least one of to count as usable. A row
# with a date and nothing else plots nothing.
_SUBSTANTIVE_FIELDS = (
    "revenue", "net_income", "operating_cash_flow", "free_cash_flow",
    "cash", "total_debt", "equity", "eps",
)


def _is_quarterly_row(row: dict[str, Any]) -> bool:
    """A genuine single quarter, not a fiscal year wearing a quarter's label."""
    fp = str(row.get("fiscal_period") or "").strip().upper()
    return bool(fp) and fp != "FY"


def _has_substance(row: dict[str, Any]) -> bool:
    return any(row.get(f) is not None for f in _SUBSTANTIVE_FIELDS)


def usable_rows(timeline: Optional[list[dict[str, Any]]],
                quarterly: bool = False) -> list[dict[str, Any]]:
    """Rows that would actually plot something.

    For the quarterly view this additionally rejects rows that are not single
    quarters, so a company whose only interim facts are cumulative year-to-date
    figures cannot masquerade as having quarterly history.
    """
    rows = [r for r in (timeline or []) if _has_substance(r)]
    if quarterly:
        rows = [r for r in rows if _is_quarterly_row(r)]
    return rows


def build_period_options(annual: Optional[list[dict[str, Any]]],
                         quarterly: Optional[list[dict[str, Any]]]) -> dict[str, Any]:
    """Which period views this company can honestly be shown in.

    Returns an entry per view with whether it is offerable, how many usable
    periods back it, and - when it is not offerable - the reason, so the UI can
    say why rather than presenting a control that does nothing.
    """
    annual_rows = usable_rows(annual)
    quarterly_rows = usable_rows(quarterly, quarterly=True)

    def entry(key: str, label: str, rows: list[dict[str, Any]], noun: str) -> dict[str, Any]:
        count = len(rows)
        if count >= MIN_TREND_POINTS:
            return {"key": key, "label": label, "available": True, "periods": count,
                    "reason": None,
                    "first_label": _label(rows[-1]), "last_label": _label(rows[0])}
        if count == 0:
            reason = (f"SEC data for this company contains no usable {noun} periods.")
        else:
            reason = (f"Only {count} usable {noun} "
                      f"{'period' if count == 1 else 'periods'} in SEC data - at least "
                      f"{MIN_TREND_POINTS} are needed before a trend can be shown.")
        return {"key": key, "label": label, "available": False, "periods": count,
                "reason": reason, "first_label": None, "last_label": None}

    ann = entry("annual", "Annual", annual_rows, "annual")
    qtr = entry("quarterly", "Quarterly", quarterly_rows, "single-quarter")

    return {
        "options": [ann, qtr],
        # Annual is the default wherever it exists: it is the basis every
        # scored rule, the snapshot and the peer position already use, so
        # starting there keeps the whole page on one footing.
        "default": "annual" if ann["available"] else ("quarterly" if qtr["available"] else None),
        "any_available": ann["available"] or qtr["available"],
        "note": "Annual periods are fiscal years as filed; quarterly periods are single fiscal "
                "quarters. The two are never combined in one view.",
    }


def _label(row: dict[str, Any]) -> str:
    fy = row.get("fiscal_year")
    fp = str(row.get("fiscal_period") or "").strip().upper()
    if fp and fp != "FY":
        return f"{fp} {fy}" if fy else fp
    return f"FY{fy}" if fy else str(row.get("period_end") or "")
