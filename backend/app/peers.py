"""
Where this company stands - against the only benchmark we actually have.

The honest position first, because it shapes everything below: this product
has no peer-data provider. It reads one company's SEC filings at a time.
Producing a sector or industry average would mean fetching and normalizing
the filings of every company in that peer group, which is not something the
application does, so any "sector average" shown here would be invented.

What IS real is the company's own record. A margin of 26% means little on its
own and a great deal against the same company's last five years, so that is
the comparison offered: today's figure positioned within the range this
company has actually reported, from its own filings, with the fiscal periods
named.

The cross-company benchmark is reported as unavailable, with the reason -
never quietly replaced by the self-comparison, which answers a different
question.
"""
from __future__ import annotations

from typing import Any, Optional

# A position needs a spread to sit in. Three periods is the minimum at which
# "high for this company" means anything at all.
MIN_HISTORY = 3

PEER_BENCHMARK_REASON = (
    "Sector and industry averages are unavailable: this analysis reads one company's SEC filings "
    "at a time and no peer-data provider is configured, so a cross-company benchmark would have to "
    "be invented rather than measured."
)

# (key, label, timeline field, higher_is_better, formatter)
_SELF_COMPARISONS = [
    ("net_margin", "Net Margin", "net_margin_pct", True, "pct"),
    ("gross_margin", "Gross Margin", "gross_margin_pct", True, "pct"),
]


def _fmt(kind: str, v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if kind == "pct":
        return f"{v:.1f}%"
    if kind == "ratio":
        return f"{v:.2f}x"
    return f"{v:,.0f}"


def _position(values: list[float], current: float) -> float:
    """Where the current figure sits within this company's own range, 0-100.

    A flat history returns the midpoint rather than 0 or 100: when every
    period is the same, "highest ever" and "lowest ever" are equally
    meaningless.
    """
    lo, hi = min(values), max(values)
    if hi == lo:
        return 50.0
    return round((current - lo) / (hi - lo) * 100, 1)


def _series(timeline: list[dict[str, Any]], field: str, limit: int = 5) -> list[dict[str, Any]]:
    points = []
    for row in timeline:
        v = row.get(field)
        if v is None:
            continue
        fy = row.get("fiscal_year")
        points.append({"label": f"FY{fy}" if fy else str(row.get("period_end") or ""),
                       "period_end": row.get("period_end"), "value": v})
        if len(points) >= limit:
            break
    points.reverse()
    return points


def _derived_series(timeline: list[dict[str, Any]], numer: str, denom: str,
                    scale: float = 1.0, limit: int = 5,
                    positive_denominator_only: bool = True) -> list[dict[str, Any]]:
    points = []
    for row in timeline:
        n, d = row.get(numer), row.get(denom)
        if n is None or d is None:
            continue
        if positive_denominator_only and d <= 0:
            continue
        if d == 0:
            continue
        fy = row.get("fiscal_year")
        points.append({"label": f"FY{fy}" if fy else str(row.get("period_end") or ""),
                       "period_end": row.get("period_end"), "value": round(n / d * scale, 2)})
        if len(points) >= limit:
            break
    points.reverse()
    return points


def _entry(key: str, label: str, points: list[dict[str, Any]], fmt: str,
           higher_is_better: bool) -> dict[str, Any]:
    if len(points) < MIN_HISTORY:
        return {
            "key": key, "label": label, "available": False,
            "reason": (f"{label} needs at least {MIN_HISTORY} comparable fiscal periods to place the "
                       f"current figure in context; SEC data provides "
                       f"{len(points)} for this company."),
        }
    values = [p["value"] for p in points]
    current = values[-1]
    average = round(sum(values) / len(values), 2)
    position = _position(values, current)
    vs_own = round(current - average, 2)

    # Above/below its own average, said plainly, with the direction's meaning
    # attached rather than left to the reader to work out.
    if abs(vs_own) < 1e-9:
        verdict = "in line with"
    else:
        verdict = "above" if vs_own > 0 else "below"
    better = (vs_own > 0) == higher_is_better if vs_own else None

    return {
        "key": key, "label": label, "available": True, "reason": None,
        "current": current, "current_display": _fmt(fmt, current),
        "average": average, "average_display": _fmt(fmt, average),
        "min": min(values), "min_display": _fmt(fmt, min(values)),
        "max": max(values), "max_display": _fmt(fmt, max(values)),
        "position_pct": position,
        "periods": len(points),
        "first_label": points[0]["label"],
        "last_label": points[-1]["label"],
        "series": points,
        "better_than_own_average": better,
        "reading": (f"{_fmt(fmt, current)} in {points[-1]['label']}, {verdict} this company's own "
                    f"{len(points)}-period average of {_fmt(fmt, average)} "
                    f"(range {_fmt(fmt, min(values))} to {_fmt(fmt, max(values))})."),
    }


def build_peer_context(timeline: list[dict[str, Any]],
                       peer_group_label: Optional[str] = None,
                       peer_group_note: Optional[str] = None) -> dict[str, Any]:
    """The company against its own history, plus an explicit statement that a
    cross-company benchmark is not available and why."""
    tl = list(timeline or [])

    comparisons = [
        _entry("net_margin", "Net Margin", _series(tl, "net_margin_pct"), "pct", True),
        _entry("gross_margin", "Gross Margin", _series(tl, "gross_margin_pct"), "pct", True),
        _entry("roe", "Return on Equity",
               _derived_series(tl, "net_income", "equity", scale=100.0), "pct", True),
        _entry("leverage", "Debt / Equity",
               _derived_series(tl, "total_debt", "equity"), "ratio", False),
        _entry("fcf_margin", "Free Cash Flow Margin",
               _derived_series(tl, "free_cash_flow", "revenue", scale=100.0,
                               positive_denominator_only=True), "pct", True),
    ]
    available = [c for c in comparisons if c["available"]]

    return {
        # The self-comparison is available whenever the company has enough of
        # its own history; the peer benchmark separately is not.
        "available": bool(available),
        "comparisons": comparisons,
        "available_count": len(available),
        "basis": "This company's own reported history",
        "peer_group_label": peer_group_label,
        "peer_group_note": peer_group_note,
        "peer_benchmark": {
            "available": False,
            "reason": PEER_BENCHMARK_REASON,
        },
        "source": "SEC EDGAR XBRL annual figures already normalized for this company. Positions are "
                  "against its own reported range, not against other companies.",
    }
