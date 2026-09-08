"""
The visual financial snapshot.

A reader should be able to see a company's financial condition before having
to read about it. This module turns figures the pipeline has ALREADY
normalized - the fiscal timeline, the quality metrics and the historical
score series - into a small, bounded set of things worth looking at:

    latest value + direction of travel + a short series + one plain sentence

It deliberately makes no SEC requests and reads no XBRL concepts of its own.
Everything here is arithmetic over data that normalize.py, quality.py and
history.py have already produced, so the snapshot can never disagree with the
rest of the page, and adding a concept in one place still keeps every
consumer in step.

Three rules run through all of it:

  * Nothing is invented. A metric whose inputs are missing comes back
    available=False with a reason, never as 0 and never as a flat line.
  * A short series is not a trend. Fewer than three usable periods yields
    "insufficient history" rather than a two-point chart that implies one.
  * Direction is not a verdict. Rising debt is reported as rising; whether
    that is bad is the scored rules' job, and for a bank it is not even the
    right question - so the judgement is withheld where the peer group makes
    it meaningless.
"""
from __future__ import annotations

from typing import Any, Optional

# How many periods a snapshot series carries. Bounded on purpose: this is a
# glanceable shape, not the full history, and the Timeline tab already holds
# the complete series for anyone who wants it.
SERIES_POINTS = 5

# Below this many points, a series is a couple of dots rather than a trend,
# and drawing it would imply a direction the data does not support.
MIN_TREND_POINTS = 3

# Peer groups whose balance sheets make a generic debt-to-equity reading
# meaningless. Banks fund themselves with deposits; the number is still shown
# as a fact, but no favourable/unfavourable judgement is attached to it.
_LEVERAGE_JUDGEMENT_WITHHELD = {"bank", "insurance", "financial"}


def _fmt_money(v: Optional[float], currency: str = "USD") -> Optional[str]:
    if v is None:
        return None
    sign = "-" if v < 0 else ""
    a = abs(float(v))
    unit = "$" if currency == "USD" else ""
    suffix = "" if currency == "USD" else f" {currency}"
    if a >= 1_000_000_000_000:
        return f"{sign}{unit}{a / 1_000_000_000_000:,.2f}T{suffix}"
    if a >= 1_000_000_000:
        return f"{sign}{unit}{a / 1_000_000_000:,.2f}B{suffix}"
    if a >= 1_000_000:
        return f"{sign}{unit}{a / 1_000_000:,.2f}M{suffix}"
    return f"{sign}{unit}{a:,.0f}{suffix}"


def _fmt_pct(v: Optional[float], places: int = 1) -> Optional[str]:
    return None if v is None else f"{v:.{places}f}%"


def _fmt_ratio(v: Optional[float]) -> Optional[str]:
    return None if v is None else f"{v:.2f}x"


def _pct_change(cur: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Percentage change, or None when it cannot be stated honestly.

    A prior period of zero or of the opposite sign makes a percentage
    meaningless (an improvement from -$2B to +$1B is not "150% growth"), so
    those cases return None and the caller falls back to describing the
    direction in words.
    """
    if cur is None or prior is None or prior == 0:
        return None
    if (cur < 0) != (prior < 0):
        return None
    return round((cur - prior) / abs(prior) * 100, 1)


def _direction(cur: Optional[float], prior: Optional[float], tolerance: float = 0.005) -> str:
    """up / down / flat, where flat means a move too small to be worth a word."""
    if cur is None or prior is None:
        return "flat"
    if prior == 0:
        return "up" if cur > 0 else "down" if cur < 0 else "flat"
    move = (cur - prior) / abs(prior)
    if abs(move) < tolerance:
        return "flat"
    return "up" if move > 0 else "down"


def _consecutive_rises(values: list[Optional[float]]) -> int:
    """How many periods in a row the series has risen, counting back from the
    most recent. Used to say "the third consecutive annual increase" only
    when that is actually true."""
    run = 0
    for i in range(len(values) - 1):
        cur, prior = values[i], values[i + 1]
        if cur is None or prior is None or cur <= prior:
            break
        run += 1
    return run


def _series_from_timeline(timeline: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """The most recent SERIES_POINTS periods that actually report *field*,
    oldest first so it reads left to right. Periods missing the field are
    dropped rather than zero-filled."""
    points = []
    for row in timeline:
        v = row.get(field)
        if v is None:
            continue
        points.append({
            "label": _period_label(row),
            "period_end": row.get("period_end"),
            "value": v,
        })
        if len(points) >= SERIES_POINTS:
            break
    points.reverse()
    return points


def _period_label(row: dict[str, Any]) -> str:
    fy = row.get("fiscal_year")
    fp = row.get("fiscal_period")
    if fy and fp and str(fp).upper() not in ("FY", ""):
        return f"{fp} {fy}"
    return f"FY{fy}" if fy else str(row.get("period_end") or "")


def _unavailable(key: str, label: str, question: str, reason: str) -> dict[str, Any]:
    return {
        "key": key, "label": label, "question": question,
        "available": False, "reason": reason,
        "value": None, "display": None, "change_pct": None, "change_display": None,
        "direction": "flat", "sentiment": "neutral", "series": [], "has_trend": False,
        "reading": None,
    }


def _metric(key: str, label: str, question: str, *, value, display,
            series: list[dict[str, Any]], change_pct: Optional[float],
            direction: str, sentiment: str, reading: str,
            change_display: Optional[str] = None,
            note: Optional[str] = None) -> dict[str, Any]:
    return {
        "key": key, "label": label, "question": question,
        "available": True, "reason": None,
        "value": value, "display": display,
        "change_pct": change_pct,
        "change_display": change_display if change_display is not None else (
            None if change_pct is None else f"{change_pct:+.1f}%"),
        "direction": direction,
        # sentiment is "good" / "bad" / "neutral" - deliberately separate from
        # direction, because a rising number is not automatically good news.
        "sentiment": sentiment,
        "series": series,
        "has_trend": len(series) >= MIN_TREND_POINTS,
        "reading": reading,
        "note": note,
    }


def _sentiment(direction: str, rising_is_good: Optional[bool]) -> str:
    if rising_is_good is None or direction == "flat":
        return "neutral"
    if direction == "up":
        return "good" if rising_is_good else "bad"
    return "bad" if rising_is_good else "good"


def _word(direction: str, up: str, down: str, flat: str) -> str:
    return up if direction == "up" else down if direction == "down" else flat


# --- Individual metrics ---------------------------------------------------

def _revenue(timeline, currency) -> dict[str, Any]:
    q, l = "Is revenue growing?", "Revenue"
    series = _series_from_timeline(timeline, "revenue")
    if not series:
        return _unavailable("revenue", l, q,
                            "SEC data for this company does not report revenue in a form that can be read.")
    values = [p["value"] for p in reversed(series)]
    cur = values[0]
    prior = values[1] if len(values) > 1 else None
    change = _pct_change(cur, prior)
    direction = _direction(cur, prior)
    latest_label = series[-1]["label"]

    if prior is None:
        reading = f"Revenue of {_fmt_money(cur, currency)} in {latest_label}. Only one period is available, so no trend can be shown."
    else:
        run = _consecutive_rises(values)
        streak = (f", the {_ordinal(run)} consecutive increase" if run >= 2 else "")
        moved = f"{abs(change):.1f}%" if change is not None else "materially"
        reading = (f"Revenue {_word(direction, 'grew', 'fell', 'was broadly flat')} "
                   f"{moved if direction != 'flat' else ''} in {latest_label}{streak}.".replace("  ", " "))
    return _metric("revenue", l, q, value=cur, display=_fmt_money(cur, currency), series=series,
                   change_pct=change, direction=direction,
                   sentiment=_sentiment(direction, True), reading=reading)


def _ordinal(n: int) -> str:
    return {2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


def _margin(timeline) -> dict[str, Any]:
    q, l = "Is profitability improving?", "Net Margin"
    series = _series_from_timeline(timeline, "net_margin_pct")
    if not series:
        return _unavailable("net_margin", l, q,
                            "Net margin needs both revenue and net income for the same period; SEC data does not report both.")
    values = [p["value"] for p in reversed(series)]
    cur, prior = values[0], (values[1] if len(values) > 1 else None)
    direction = _direction(cur, prior, tolerance=0.01)
    pts = None if prior is None else round(cur - prior, 1)
    latest_label = series[-1]["label"]
    if prior is None:
        reading = f"Net margin of {_fmt_pct(cur)} in {latest_label}."
    else:
        reading = (f"Net margin {_word(direction, 'widened', 'narrowed', 'held steady')} "
                   f"{'' if direction == 'flat' else f'to {_fmt_pct(cur)} '}in {latest_label} "
                   f"from {_fmt_pct(prior)}.").replace("  ", " ")
    return _metric("net_margin", l, q, value=cur, display=_fmt_pct(cur), series=series,
                   change_pct=pts, direction=direction, sentiment=_sentiment(direction, True),
                   reading=reading,
                   change_display=None if pts is None else f"{pts:+.1f} pts")


def _cash_generation(timeline, currency) -> dict[str, Any]:
    q, l = "Is the company generating cash?", "Free Cash Flow"
    series = _series_from_timeline(timeline, "free_cash_flow")
    if not series:
        series = _series_from_timeline(timeline, "operating_cash_flow")
        l = "Operating Cash Flow"
        if not series:
            return _unavailable("cash_generation", "Free Cash Flow", q,
                                "SEC data does not report operating cash flow for this company.")
    values = [p["value"] for p in reversed(series)]
    cur, prior = values[0], (values[1] if len(values) > 1 else None)
    change = _pct_change(cur, prior)
    direction = _direction(cur, prior)
    latest_label = series[-1]["label"]
    negative = cur is not None and cur < 0
    if prior is None:
        reading = f"{l} of {_fmt_money(cur, currency)} in {latest_label}."
    elif negative:
        reading = (f"{l} was negative at {_fmt_money(cur, currency)} in {latest_label} - "
                   f"the business consumed cash rather than producing it.")
    else:
        reading = (f"{l} {_word(direction, 'rose', 'fell', 'was broadly flat')} to "
                   f"{_fmt_money(cur, currency)} in {latest_label}.")
    return _metric("cash_generation", l, q, value=cur, display=_fmt_money(cur, currency),
                   series=series, change_pct=change, direction=direction,
                   sentiment="bad" if negative else _sentiment(direction, True), reading=reading)


def _leverage(timeline, peer_group: Optional[str]) -> dict[str, Any]:
    q, l = "Is leverage rising?", "Debt / Equity"
    points = []
    for row in timeline:
        debt, eq = row.get("total_debt"), row.get("equity")
        if debt is None or eq is None or eq <= 0:
            continue
        points.append({"label": _period_label(row), "period_end": row.get("period_end"),
                       "value": round(debt / eq, 3)})
        if len(points) >= SERIES_POINTS:
            break
    if not points:
        return _unavailable("leverage", l, q,
                            "Debt-to-equity needs total debt and positive shareholders' equity for the same period; "
                            "SEC data does not report both.")
    points.reverse()
    values = [p["value"] for p in reversed(points)]
    cur, prior = values[0], (values[1] if len(values) > 1 else None)
    direction = _direction(cur, prior, tolerance=0.02)
    change = _pct_change(cur, prior)
    latest_label = points[-1]["label"]

    withheld = (peer_group or "").lower() in _LEVERAGE_JUDGEMENT_WITHHELD
    note = ("Shown as a fact rather than a verdict: this company funds itself in a way that makes a "
            "generic debt-to-equity reading uninformative." if withheld else None)
    if prior is None:
        reading = f"Debt stands at {_fmt_ratio(cur)} shareholders' equity in {latest_label}."
    else:
        reading = (f"Debt-to-equity {_word(direction, 'rose', 'fell', 'was broadly unchanged')} to "
                   f"{_fmt_ratio(cur)} in {latest_label} from {_fmt_ratio(prior)}.")
    return _metric("leverage", l, q, value=cur, display=_fmt_ratio(cur), series=points,
                   change_pct=change, direction=direction,
                   sentiment="neutral" if withheld else _sentiment(direction, False),
                   reading=reading, note=note)


def _returns(timeline) -> dict[str, Any]:
    q, l = "Is shareholder return improving?", "Return on Equity"
    points = []
    for row in timeline:
        ni, eq = row.get("net_income"), row.get("equity")
        if ni is None or eq is None or eq <= 0:
            continue
        points.append({"label": _period_label(row), "period_end": row.get("period_end"),
                       "value": round(ni / eq * 100, 1)})
        if len(points) >= SERIES_POINTS:
            break
    if not points:
        return _unavailable("roe", l, q,
                            "Return on equity needs net income and positive shareholders' equity for the same "
                            "period; SEC data does not report both.")
    points.reverse()
    values = [p["value"] for p in reversed(points)]
    cur, prior = values[0], (values[1] if len(values) > 1 else None)
    direction = _direction(cur, prior, tolerance=0.01)
    pts = None if prior is None else round(cur - prior, 1)
    latest_label = points[-1]["label"]
    reading = (f"Return on equity of {_fmt_pct(cur)} in {latest_label}."
               if prior is None else
               f"Return on equity {_word(direction, 'improved', 'declined', 'held steady')} to "
               f"{_fmt_pct(cur)} in {latest_label} from {_fmt_pct(prior)}.")
    return _metric("roe", l, q, value=cur, display=_fmt_pct(cur), series=points,
                   change_pct=pts, direction=direction, sentiment=_sentiment(direction, True),
                   reading=reading, change_display=None if pts is None else f"{pts:+.1f} pts")


def _health_trend(historical_scores: list[dict[str, Any]]) -> dict[str, Any]:
    q, l = "Is financial condition improving?", "Financial Health"
    usable = [h for h in (historical_scores or []) if h.get("overall_score") is not None]
    if not usable:
        return _unavailable("health_trend", l, q,
                            "Historical financial-health scores could not be calculated from this company's SEC data.")
    ordered = sorted(usable, key=lambda h: h.get("period_end") or "")
    points = [{"label": f"FY{h.get('fiscal_year')}", "period_end": h.get("period_end"),
               "value": h.get("overall_score")} for h in ordered][-SERIES_POINTS:]
    values = [p["value"] for p in reversed(points)]
    cur, prior = values[0], (values[1] if len(values) > 1 else None)
    direction = _direction(cur, prior, tolerance=0.001)
    pts = None if prior is None else cur - prior
    first = points[0]
    reading = (f"Financial-health score of {cur} in {points[-1]['label']}."
               if prior is None else
               f"The score {_word(direction, 'improved', 'declined', 'was unchanged')} to {cur} in "
               f"{points[-1]['label']}, against {first['value']} in {first['label']}.")
    return _metric("health_trend", l, q, value=cur, display=str(cur), series=points,
                   change_pct=None, direction=direction, sentiment=_sentiment(direction, True),
                   reading=reading, change_display=None if pts is None else f"{pts:+d} pts")


def build_snapshot(timeline: list[dict[str, Any]],
                   historical_scores: Optional[list[dict[str, Any]]] = None,
                   currency: str = "USD",
                   peer_group: Optional[str] = None) -> dict[str, Any]:
    """The scannable financial overview: six questions, answered from data the
    pipeline has already normalized.

    Metrics whose inputs are missing are returned as unavailable with a
    reason, so the reader can tell "this company does not report it" from
    "the number is zero" - which are very different statements.
    """
    tl = list(timeline or [])
    metrics = [
        _revenue(tl, currency),
        _margin(tl),
        _cash_generation(tl, currency),
        _leverage(tl, peer_group),
        _returns(tl),
        _health_trend(historical_scores or []),
    ]
    available = [m for m in metrics if m["available"]]
    return {
        "available": bool(available),
        "metrics": metrics,
        "available_count": len(available),
        "currency": currency,
        "series_points": SERIES_POINTS,
        "source": "Derived from SEC EDGAR XBRL figures already normalized for this company - "
                  "no separate data source and nothing estimated.",
    }
