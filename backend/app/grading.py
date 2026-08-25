"""
FORGE Grading System.

Turns the existing pillar scores (Financial Health, Financial Quality,
Valuation, Risk) into a single, fully-documented letter grade and health
classification. This module does not compute any new financial metric and
does not change how the FORGE Score itself is calculated (see pillars.py) -
it only translates already-computed scores into a clearer, standardized
grade so the thresholds can be disclosed once and applied consistently
everywhere in the product.

Rule: a pillar (or the overall score) with no underlying data is never
graded as if it were a zero, and never averaged in as a phantom value. It
is marked "N/A - Insufficient Data" and excluded from every calculation
that would otherwise be pulled up or down by its absence.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Letter grade thresholds (0-100 scale). Documented once, used everywhere.
# ---------------------------------------------------------------------------
LETTER_GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (97, "A+"), (93, "A"), (90, "A-"),
    (87, "B+"), (83, "B"), (80, "B-"),
    (77, "C+"), (73, "C"), (70, "C-"),
    (67, "D+"), (63, "D"), (60, "D-"),
    (0, "F"),
]

# ---------------------------------------------------------------------------
# Health classification thresholds (0-100 scale). Distinct from the letter
# grade so a user gets both a familiar academic-style grade and a plain
# financial-health label.
# ---------------------------------------------------------------------------
HEALTH_CLASSIFICATION_THRESHOLDS: list[tuple[float, str]] = [
    (90, "Exceptional"),
    (80, "Strong"),
    (65, "Healthy"),
    (50, "Watch"),
    (35, "Weak"),
    (0, "Critical"),
]

GRADING_METHODOLOGY = (
    "Letter grades map the 0-100 FORGE Score (and each individual pillar score) onto a "
    "standard A+ through F scale: A+ 97-100, A 93-96, A- 90-92, B+ 87-89, B 83-86, "
    "B- 80-82, C+ 77-79, C 73-76, C- 70-72, D+ 67-69, D 63-66, D- 60-62, F below 60. "
    "Health classification uses its own thresholds on the same 0-100 score: "
    "Exceptional 90-100, Strong 80-89, Healthy 65-79, Watch 50-64, Weak 35-49, "
    "Critical below 35. A score of None (insufficient underlying SEC or market data) "
    "is never graded, defaulted to zero, or averaged in - it is shown as "
    "'N/A - Insufficient Data' and excluded from every roll-up calculation."
)


def letter_grade(score: Optional[float]) -> str:
    if score is None:
        return "N/A"
    for threshold, grade in LETTER_GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def health_classification(score: Optional[float]) -> str:
    if score is None:
        return "Insufficient Data"
    for threshold, label in HEALTH_CLASSIFICATION_THRESHOLDS:
        if score >= threshold:
            return label
    return "Critical"


def _key_reasons_financial_health(score: dict[str, Any]) -> list[str]:
    reasons = []
    for r in score.get("rules", []):
        if r["status"] in ("FAIL", "PASS") and len(reasons) < 4:
            tag = "Strength" if r["status"] == "PASS" else "Concern"
            reasons.append(f"{tag}: {r['name']} ({r['value']})")
    if not reasons:
        reasons.append("Insufficient SEC data to identify specific drivers.")
    return reasons


def _key_reasons_quality(piotroski: dict[str, Any]) -> list[str]:
    reasons = []
    for c in piotroski.get("criteria", []):
        if c["status"] in ("PASS", "FAIL") and len(reasons) < 4:
            tag = "Pass" if c["status"] == "PASS" else "Fail"
            reasons.append(f"{tag}: {c['label']}")
    if not reasons:
        reasons.append("Insufficient consecutive-year SEC data for Piotroski criteria.")
    return reasons


def _key_reasons_valuation(valuation: dict[str, Any]) -> list[str]:
    reasons = []
    pe = valuation.get("pe_ratio", {})
    if pe.get("available"):
        reasons.append(f"P/E ratio of {pe['display']} relative to market price.")
    if not valuation.get("price_source"):
        reasons.append("Live market price unavailable - valuation pillar excluded from the FORGE Score.")
    if not reasons:
        reasons.append("Market data required for valuation was not available.")
    return reasons


def _key_reasons_risk(altman: dict[str, Any]) -> list[str]:
    reasons = []
    if altman.get("available"):
        reasons.append(f"Altman Z″-Score {altman['score']} places the company in the {altman['zone_label']}.")
        if altman.get("missing_components"):
            reasons.append("Missing components: " + ", ".join(altman["missing_components"]) + " (defaulted to 0 in the formula, disclosed here).")
    else:
        reasons.append(altman.get("reason", "Insufficient SEC data to compute Altman Z-Score."))
    return reasons


def build_grading(forge: dict[str, Any], score: dict[str, Any], piotroski: dict[str, Any],
                   valuation: dict[str, Any], altman: dict[str, Any]) -> dict[str, Any]:
    """Augments the existing forge-score payload with letter grades, health
    classification, per-pillar contribution weight, and key reasons. Pure
    presentation layer on top of pillars.compute_forge_score - no scores are
    recomputed here."""
    pillars = forge["pillars"]
    n_available = forge["pillars_available"]

    key_reason_builders = {
        "financial_health": lambda: _key_reasons_financial_health(score),
        "financial_quality": lambda: _key_reasons_quality(piotroski),
        "valuation": lambda: _key_reasons_valuation(valuation),
        "risk": lambda: _key_reasons_risk(altman),
    }

    graded_pillars = {}
    for key, p in pillars.items():
        contribution_pct = round(100.0 / n_available, 1) if (p["available"] and n_available > 0) else 0.0
        graded_pillars[key] = {
            **p,
            "letter_grade": letter_grade(p["score"]),
            "contribution_pct": contribution_pct,
            "contribution_note": (
                f"Equal-weighted: 1 of {n_available} available pillars contributes to the overall score."
                if p["available"] else
                "Not included in the overall score - no underlying data available."
            ),
            "key_reasons": key_reason_builders[key](),
        }

    overall = forge["forge_score"]
    return {
        "forge_score": overall,
        "letter_grade": letter_grade(overall),
        "health_classification": health_classification(overall),
        "pillars": graded_pillars,
        "pillars_available": n_available,
        "methodology_note": forge["methodology_note"],
        "grading_methodology": GRADING_METHODOLOGY,
    }
