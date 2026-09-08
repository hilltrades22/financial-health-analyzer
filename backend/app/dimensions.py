"""
Financial health, one dimension at a time.

The 3D Financial Core shows the shape of a company's health; this is its flat
companion - the same structure as a list you can read, compare and scan. Both
are built from the same scored rules, so they cannot tell different stories.

A dimension here is simply a rule category: Liquidity, Capital Structure,
Cash Generation, Profitability, Debt Service, Financial Risk and whatever
sector-specific categories the peer group added. Nothing is re-derived from
raw SEC facts - the rules have already done that work, including the
sector-aware decisions - so this module is pure aggregation over
`score["rules"]`.

The distinctions the rest of the product makes are preserved exactly:

  * NOT_APPLICABLE is not a low score. A bank whose generic liquidity rule
    was set aside has that dimension marked not-applicable, and the
    business-model-appropriate rule that replaced it is what carries the
    score.
  * UNAVAILABLE is not a failure either. A dimension nobody could measure is
    reported as unmeasured, never as zero out of ten.
  * A dimension's score is points earned over points ACTUALLY AVAILABLE
    within it, so unscored rules never quietly drag it down.
"""
from __future__ import annotations

from typing import Any, Optional

# Worst-first. A dimension takes the most serious status among the rules that
# were actually scored, because that is the thing a reader needs to see.
_SEVERITY = {"FAIL": 0, "WATCH": 1, "PASS": 2}

# Dimensions in the order a reader works through a balance sheet: can it pay
# its bills, how is it funded, does it generate cash, does it earn, what is
# the risk. Categories the rules produce that are not listed here keep their
# own place at the end rather than being dropped.
_PREFERRED_ORDER = [
    "Liquidity",
    "Debt & Leverage",
    "Capital Structure",
    "Debt Service",
    "Cash Generation",
    "Profitability",
    "Retained Earnings",
    "Financial Risk",
    "Treasury Stock",
    "Lease Obligations",
]

# Where a dimension has a matching snapshot metric, its direction of travel is
# borrowed rather than recomputed - one derivation, two presentations.
_TREND_KEYS = {
    "Debt & Leverage": "leverage",
    "Capital Structure": "leverage",
    "Cash Generation": "cash_generation",
    "Profitability": "roe",
}


def _dimension_status(rules: list[dict[str, Any]]) -> str:
    """PASS / WATCH / FAIL from the scored rules; NOT_APPLICABLE or
    UNAVAILABLE when there is nothing to score."""
    scored = [r for r in rules if (r.get("points_available") or 0) > 0]
    if scored:
        statuses = [r.get("status") for r in scored if r.get("status") in _SEVERITY]
        if statuses:
            return min(statuses, key=lambda s: _SEVERITY[s])
    # Nothing scored. Distinguish "does not apply to this business" from
    # "could not be measured" - they mean opposite things to a reader.
    if any(r.get("status") == "NOT_APPLICABLE" for r in rules):
        return "NOT_APPLICABLE"
    return "UNAVAILABLE"


def _primary_rule(rules: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The rule that best represents the dimension: the most heavily weighted
    scored one, falling back to the first rule when none are scored."""
    scored = [r for r in rules if (r.get("points_available") or 0) > 0]
    if scored:
        return max(scored, key=lambda r: r.get("points_available") or 0)
    return rules[0] if rules else None


def _interpretation(name: str, status: str, rules: list[dict[str, Any]],
                    primary: Optional[dict[str, Any]]) -> str:
    """One sentence describing what the rules actually said.

    Deliberately assembled from rule counts and statuses rather than written
    per dimension: the wording can then never contradict the data, because it
    has nothing in it that the data did not supply.
    """
    scored = [r for r in rules if (r.get("points_available") or 0) > 0]
    na = [r for r in rules if r.get("status") == "NOT_APPLICABLE"]
    unavailable = [r for r in rules if r.get("status") == "UNAVAILABLE"]

    if status == "NOT_APPLICABLE":
        reason = next((r.get("not_applicable_reason") for r in na if r.get("not_applicable_reason")), None)
        return reason or f"{name} is not assessed for this type of company."
    if status == "UNAVAILABLE":
        return (f"{name} could not be measured: SEC data for this company does not report the "
                f"figures the {'measure' if len(unavailable) == 1 else 'measures'} needs.")

    passed = sum(1 for r in scored if r.get("status") == "PASS")
    watch = sum(1 for r in scored if r.get("status") == "WATCH")
    failed = sum(1 for r in scored if r.get("status") == "FAIL")
    parts = []
    if passed:
        parts.append(f"{passed} passing")
    if watch:
        parts.append(f"{watch} on watch")
    if failed:
        parts.append(f"{failed} failing")
    body = ", ".join(parts) if parts else "no scored measures"

    tail = ""
    if na:
        tail = (f" {len(na)} generic {'measure was' if len(na) == 1 else 'measures were'} set aside as "
                f"not applicable to this business model.")
    elif unavailable:
        tail = (f" {len(unavailable)} further {'measure' if len(unavailable) == 1 else 'measures'} "
                f"could not be measured from SEC data.")

    lead = {"PASS": "holds up", "WATCH": "needs watching", "FAIL": "is a weakness"}[status]
    return f"{name} {lead} on this company's SEC figures - {body}.{tail}"


def build_dimensions(score: dict[str, Any],
                     snapshot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Group the scored rules into readable dimensions.

    Returns one entry per rule category with its score, status, the rule that
    represents it, its direction of travel where the snapshot has one, and a
    sentence assembled from what the rules actually reported.
    """
    rules = list((score or {}).get("rules") or [])
    if not rules:
        return {"available": False, "dimensions": [],
                "reason": "No scored measures are available for this company."}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rules:
        grouped.setdefault(r.get("category") or "Other", []).append(r)

    trends = {m.get("key"): m for m in ((snapshot or {}).get("metrics") or []) if m.get("available")}

    out: list[dict[str, Any]] = []
    for name, group in grouped.items():
        status = _dimension_status(group)
        earned = sum(r.get("points_earned") or 0 for r in group)
        available = sum(r.get("points_available") or 0 for r in group)
        scored = available > 0
        primary = _primary_rule(group)

        trend_metric = trends.get(_TREND_KEYS.get(name, ""))
        # A sector rule replacing a set-aside generic one is worth naming:
        # it is the whole reason the dimension still has a score.
        sector_rules = [r for r in group if r.get("rule_type") == "sector"]

        out.append({
            "name": name,
            "status": status,
            "scored": scored,
            "score": round(earned / available * 100) if scored else None,
            "points_earned": earned if scored else None,
            "points_available": available if scored else None,
            "rule_count": len(group),
            "primary_metric": {
                "name": (primary or {}).get("name"),
                "value": (primary or {}).get("value"),
                "status": (primary or {}).get("status"),
            } if primary else None,
            "direction": (trend_metric or {}).get("direction"),
            "direction_sentiment": (trend_metric or {}).get("sentiment"),
            "direction_label": (trend_metric or {}).get("change_display"),
            "sector_specific": bool(sector_rules),
            "interpretation": _interpretation(name, status, group, primary),
        })

    order = {n: i for i, n in enumerate(_PREFERRED_ORDER)}
    out.sort(key=lambda d: (order.get(d["name"], len(_PREFERRED_ORDER)), d["name"]))

    scored_dims = [d for d in out if d["scored"]]
    return {
        "available": True,
        "dimensions": out,
        "scored_count": len(scored_dims),
        "total_count": len(out),
        "source": "Aggregated from this company's scored financial-health rules - the same rules, "
                  "including sector-specific ones, shown individually further down the page.",
    }
