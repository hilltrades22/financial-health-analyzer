"""
Combines the four FORGE pillars (Financial Health, Financial Quality,
Valuation, Risk) into the headline FORGE Score. This blending formula is
FORGE's own composite methodology - clearly disclosed as such - built on
top of the individually-sourced, individually-explainable pillar scores
(the 9-rule SEC health score, Piotroski F-Score, Altman Z-Score, and
market-value multiples). A pillar with no underlying data contributes
nothing and is excluded from the blend, never defaulted to a value.
"""
from __future__ import annotations

from typing import Any, Optional


def _label(score: Optional[float]) -> str:
    if score is None:
        return "Insufficient Data"
    if score >= 80:
        return "Strong"
    if score >= 60:
        return "Healthy"
    if score >= 40:
        return "Needs Review"
    return "Weak"


def _valuation_pillar_score(valuation: dict[str, Any]) -> Optional[float]:
    pe = valuation.get("pe_ratio", {})
    if not pe.get("available"):
        return None
    v = pe["value"]
    if v <= 0:
        return None  # negative/meaningless P/E - don't score it
    if v <= 15:
        return 90.0
    if v <= 25:
        return 75.0
    if v <= 40:
        return 55.0
    if v <= 60:
        return 35.0
    return 15.0


def _risk_pillar_score(altman: dict[str, Any]) -> Optional[float]:
    if not altman.get("available"):
        return None
    zone = altman.get("zone")
    return {"SAFE": 90.0, "GREY": 55.0, "DISTRESS": 20.0}.get(zone)


def _quality_pillar_score(piotroski: dict[str, Any]) -> Optional[float]:
    if piotroski.get("scored_out_of", 0) <= 0:
        return None
    return round(piotroski["score"] / piotroski["scored_out_of"] * 100, 1)


def compute_forge_score(health_score: Optional[float], piotroski: dict[str, Any],
                         altman: dict[str, Any], valuation: dict[str, Any]) -> dict[str, Any]:
    pillars = {
        "financial_health": {
            "score": health_score,
            "label": _label(health_score),
            "available": health_score is not None,
            "methodology": "SEC-EDGAR 9-rule explainable score (liquidity, leverage, retained earnings, cash flow, debt service, etc.)",
        },
        "financial_quality": {
            "score": _quality_pillar_score(piotroski),
            "available": piotroski.get("scored_out_of", 0) > 0,
            "methodology": f"Piotroski F-Score ({piotroski.get('score', 0)}/{piotroski.get('scored_out_of', 0)} criteria available), scaled to 100",
        },
        "valuation": {
            "score": _valuation_pillar_score(valuation),
            "available": valuation.get("pe_ratio", {}).get("available", False),
            "methodology": "Banded against P/E ratio using live market price x SEC-reported fundamentals",
        },
        "risk": {
            "score": _risk_pillar_score(altman),
            "available": altman.get("available", False),
            "methodology": f"Altman Z″-Score zone ({altman.get('zone_label', 'unavailable')})",
        },
    }
    for p in pillars.values():
        p["label"] = _label(p["score"])

    available_scores = [p["score"] for p in pillars.values() if p["score"] is not None]
    if len(available_scores) >= 2:
        forge_score = round(sum(available_scores) / len(available_scores), 1)
    elif len(available_scores) == 1:
        forge_score = round(available_scores[0], 1)
    else:
        forge_score = None

    return {
        "forge_score": forge_score,
        "label": _label(forge_score),
        "pillars": pillars,
        "pillars_available": len(available_scores),
        "methodology_note": (
            "The FORGE Score is the simple average of whichever pillars have sufficient data "
            "(Financial Health, Financial Quality, Valuation, Risk). A pillar with no underlying "
            "SEC or market data is excluded rather than assumed - it never drags the score up or down."
        ),
    }
