"""
Plain-English 'Financial Story', generated entirely from scored rule results.

The story never hardcodes anything about a specific company: it reads the
rules that actually ran, groups them into what is strong, what needs
attention, what the business model changes about the analysis, and what
could not be assessed. That last part matters - a reader should be told when
a measure was skipped because it does not apply to this kind of company, as
opposed to skipped because the filer does not report the data.
"""
from __future__ import annotations

from typing import Any, Optional

PASS, WATCH, FAIL = "PASS", "WATCH", "FAIL"
UNAVAILABLE, NOT_APPLICABLE = "UNAVAILABLE", "NOT_APPLICABLE"

# Rules whose plain-English meaning is worth spelling out in the narrative
# rather than just listing. Anything not here still appears in the strengths
# / concerns lists using its own explanation.
_HEADLINE_ORDER = [
    "liquidity", "bank_capital_strength", "bank_deposit_funding", "sector_leverage",
    "debt_to_equity", "free_cash_flow", "sector_cash_generation", "reit_distribution_coverage",
    "bank_roe", "sector_gross_margin", "interest_coverage", "sector_interest_coverage",
    "bank_credit_cost", "retained_earnings", "earnings_stability",
]


def _ordered(rules: list[dict[str, Any]], statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    picked = [r for r in rules if r["status"] in statuses]
    rank = {rid: i for i, rid in enumerate(_HEADLINE_ORDER)}
    return sorted(picked, key=lambda r: (rank.get(r["id"], 999), -r.get("points_available", 0)))


def _first_sentence(text: str) -> str:
    """The rule explanations are written as 'fact sentence + verdict
    sentence'. For the story we want the substance, not the whole paragraph."""
    if not text:
        return ""
    parts = [p.strip() for p in text.split(". ") if p.strip()]
    if not parts:
        return text.strip()
    out = parts[0]
    if not out.endswith("."):
        out += "."
    return out


def build_story_sections(company_name: str, ticker: str, score: dict[str, Any],
                         classification: Optional[dict[str, Any]] = None,
                         valuation: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Structured story: overview, strengths, concerns, sector context and
    an explicit account of what could not be assessed."""
    rules = score.get("rules", [])
    label = score.get("label", "Insufficient Data")
    pct = score.get("overall_score")
    classification = classification or {}
    valuation = valuation or {}

    sector = (classification.get("sector") or {}).get("value") if isinstance(classification.get("sector"), dict) else None
    industry = (classification.get("industry") or {}).get("value") if isinstance(classification.get("industry"), dict) else None
    peer_group = score.get("peer_group") or classification.get("peer_group") or "general"

    descriptor = industry or sector
    what_it_is = f"{company_name} ({ticker})"
    if descriptor:
        what_it_is += f" is classified by the SEC as {descriptor.lower()}"

    overview = {
        "Strong": f"{what_it_is}. Its latest SEC filings show a strong financial position.",
        "Healthy": f"{what_it_is}. Its latest SEC filings show a generally healthy position, with a few areas worth watching.",
        "Needs Review": f"{what_it_is}. Its latest SEC filings show several areas that warrant a closer look.",
        "Insufficient Data": f"{what_it_is}. There is not enough standardised SEC data to form a confident view of its financial health.",
    }.get(label, f"{what_it_is}.")
    if pct is not None:
        overview += f" Overall score: {pct} out of 100, based on {score.get('points_available_scored', 0)} points of applicable, available measures."

    strengths = [{"rule": r["name"], "detail": _first_sentence(r["explanation"]), "value": r.get("value")}
                 for r in _ordered(rules, (PASS,)) if r.get("points_available", 0) > 0][:5]
    concerns = [{"rule": r["name"], "status": r["status"], "detail": _first_sentence(r["explanation"]),
                 "value": r.get("value")}
                for r in _ordered(rules, (FAIL, WATCH))][:5]

    # Sector context: why this company is not judged by generic thresholds.
    not_applicable = [r for r in rules if r["status"] == NOT_APPLICABLE]
    sector_context = ""
    if peer_group != "general":
        note = score.get("peer_group_note") or classification.get("peer_group_note") or ""
        sector_context = note
        if not_applicable:
            names = ", ".join(sorted({r["name"] for r in not_applicable}))
            sector_context += (
                f" Because of that, {len(not_applicable)} standard measure"
                f"{'s' if len(not_applicable) != 1 else ''} ({names}) "
                f"{'were' if len(not_applicable) != 1 else 'was'} set aside for this company rather than counted "
                "as failures, and business-model-appropriate measures were used instead."
            )

    # What could not be assessed, separating the two very different reasons.
    unavailable = [r for r in rules if r["status"] == UNAVAILABLE]
    gaps = []
    if unavailable:
        names = ", ".join(sorted({r["name"] for r in unavailable}))
        gaps.append(
            f"{len(unavailable)} measure{'s' if len(unavailable) != 1 else ''} ({names}) could not be assessed "
            "because this filer does not report the required facts in SEC's standardised XBRL data. "
            "They did not count for or against the score."
        )
    val_reason = None
    for key in ("market_cap", "pe_ratio"):
        entry = valuation.get(key) or {}
        if not entry.get("available") and entry.get("reason"):
            val_reason = entry["reason"]
            break
    if val_reason:
        gaps.append("Valuation figures are unavailable: " + val_reason)
    elif valuation.get("currency_note"):
        gaps.append(valuation["currency_note"])

    confidence = score.get("data_confidence_pct")
    if confidence is not None and confidence < 100:
        gaps.append(f"Overall, {confidence}% of the measures considered could be scored from available SEC data.")

    return {
        "overview": overview,
        "strengths": strengths,
        "concerns": concerns,
        "sector_context": sector_context,
        "data_gaps": gaps,
        "peer_group": peer_group,
    }


def build_financial_story(company_name: str, ticker: str, score: dict[str, Any],
                          classification: Optional[dict[str, Any]] = None,
                          valuation: Optional[dict[str, Any]] = None) -> str:
    """Flowing plain-English narrative. Kept as a string for backward
    compatibility with the existing API field and frontend."""
    s = build_story_sections(company_name, ticker, score, classification, valuation)
    parts = [s["overview"]]

    if s["strengths"]:
        parts.append("What is working: " + " ".join(x["detail"] for x in s["strengths"][:3]))
    if s["concerns"]:
        lead = "What needs attention: "
        parts.append(lead + " ".join(x["detail"] for x in s["concerns"][:3]))
    if s["sector_context"]:
        parts.append("Sector context: " + s["sector_context"])
    if s["data_gaps"]:
        parts.append("What could not be assessed: " + " ".join(s["data_gaps"]))

    return " ".join(p.strip() for p in parts if p and p.strip())
