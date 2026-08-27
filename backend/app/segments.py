"""
Business Mix / Revenue Map - real SEC segment and geographic revenue
disaggregation, parsed directly from a company's own XBRL instance
document (not the flattened companyfacts API, which drops dimensional
"segment" contexts entirely).

Nothing here is estimated or fabricated: if a filer did not tag its
segment/geographic revenue disaggregation in a way we can parse, the
result says so explicitly rather than guessing.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from xml.etree import ElementTree as ET

from .sec_client import SecClient, SecUnavailableError, TickerNotFoundError

REVENUE_TAGS = {
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
}

BUSINESS_AXES = {
    "StatementBusinessSegmentsAxis",
    "ProductOrServiceAxis",
}
GEOGRAPHIC_AXES = {"StatementGeographicalAxis"}

_LINKBASE_SUFFIXES = ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", ".xsd")


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _local_ns(tag: str) -> str:
    return tag.split("}", 1)[0][1:] if "}" in tag else ""


_KNOWN_PRODUCT_NAMES = {"IPhone": "iPhone", "IPad": "iPad", "IMac": "iMac", "IPod": "iPod", "IOS": "iOS", "MacOS": "macOS"}


def _clean_member(member: str) -> str:
    """'us-gaap:AmericasSegmentMember' or 'aapl:AmericasSegmentMember' -> 'Americas'"""
    name = member.split(":", 1)[-1]
    name = re.sub(r"(Segment)?Member$", "", name)
    if name in _KNOWN_PRODUCT_NAMES:
        return _KNOWN_PRODUCT_NAMES[name]
    # CamelCase -> spaced words, but keep a leading lowercase-style brand
    # prefix (iPhone, iPad, ...) glued to its following capital.
    spaced = re.sub(r"(?<!^)(?<![A-Z])(?=[A-Z])", " ", name).strip()
    return spaced or name or member


def find_latest_10k(submissions: dict[str, Any]) -> Optional[dict[str, Any]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []
    for i, form in enumerate(forms):
        if form == "10-K":
            return {
                "accessionNumber": accessions[i],
                "primaryDocument": docs[i] if i < len(docs) else None,
                "filingDate": dates[i] if i < len(dates) else None,
            }
    return None


def _pick_instance_filename(index_json: dict[str, Any]) -> Optional[str]:
    items = (index_json.get("directory") or {}).get("item") or []
    candidates = []
    for item in items:
        name = item.get("name", "")
        if not name.lower().endswith(".xml"):
            continue
        if any(name.lower().endswith(suf) for suf in _LINKBASE_SUFFIXES):
            continue
        if name.lower() == "filingsummary.xml":
            continue
        candidates.append(name)
    # Prefer the shortest plausible instance filename (linkbases tend to be longer).
    candidates.sort(key=len)
    return candidates[0] if candidates else None


def _parse_contexts(root: ET.Element) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for ctx in root.iter():
        if _strip_ns(ctx.tag) != "context":
            continue
        ctx_id = ctx.get("id")
        if not ctx_id:
            continue
        period_start = period_end = period_instant = None
        dims: list[tuple[str, str]] = []
        for child in ctx.iter():
            local = _strip_ns(child.tag)
            if local == "startDate":
                period_start = (child.text or "").strip()
            elif local == "endDate":
                period_end = (child.text or "").strip()
            elif local == "instant":
                period_instant = (child.text or "").strip()
            elif local == "explicitMember":
                axis = child.get("dimension", "")
                axis_local = axis.split(":", 1)[-1]
                member = (child.text or "").strip()
                dims.append((axis_local, member))
        contexts[ctx_id] = {
            "start": period_start,
            "end": period_end or period_instant,
            "dims": dims,
        }
    return contexts


def _parse_facts(root: ET.Element, contexts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for el in root:
        local = _strip_ns(el.tag)
        if local not in REVENUE_TAGS:
            continue
        ctx_ref = el.get("contextRef")
        ctx = contexts.get(ctx_ref)
        if not ctx or not ctx["dims"]:
            continue  # whole-company (default) fact, not a segment breakdown
        val = (el.text or "").strip()
        try:
            value = float(val)
        except (TypeError, ValueError):
            continue
        facts.append({"tag": local, "context": ctx, "value": value})
    return facts


def _build_breakdown(facts: list[dict[str, Any]], axes: set[str], target_start: Optional[str], target_end: Optional[str]) -> list[dict[str, Any]]:
    by_member: dict[str, float] = {}
    for f in facts:
        ctx = f["context"]
        if ctx["end"] != target_end:
            continue
        if target_start and ctx["start"] and ctx["start"] != target_start:
            continue
        matching = [m for (axis, m) in ctx["dims"] if axis in axes]
        if len(matching) != 1 or len(ctx["dims"]) != 1:
            continue  # skip facts cross-tagged on multiple axes to avoid double-counting
        member = matching[0]
        label = _clean_member(member)
        # Prefer the most "whole" revenue tag if multiple report the same member.
        by_member[label] = max(by_member.get(label, 0.0), f["value"])
    total = sum(by_member.values())
    if not by_member or total <= 0:
        return []
    return sorted(
        [{"label": label, "value": v, "pct_of_total": round(v / total * 100, 1)} for label, v in by_member.items()],
        key=lambda r: -r["value"],
    )


async def build_business_mix(sec_client: SecClient, cik: int, submissions: dict[str, Any], annual_period_end: Optional[str], annual_period_start: Optional[str]) -> dict[str, Any]:
    """Real segment/geographic revenue mix for the most recent 10-K, parsed
    directly from that filing's XBRL instance document. Returns an
    'available: False' result (never fabricated placeholder data) if the
    filer's tagging can't be parsed."""
    result = {
        "available": False,
        "reason": None,
        "period_end": annual_period_end,
        "filing": None,
        "business_segments": [],
        "geographic": [],
        "source": "SEC EDGAR XBRL instance document (filing-level, dimensional facts) - REPORTED data.",
    }
    if not annual_period_end:
        result["reason"] = "Not reported / unavailable - no annual period to match segment data against."
        return result

    filing = find_latest_10k(submissions)
    if not filing or not filing.get("accessionNumber"):
        result["reason"] = "Not reported / unavailable - no 10-K filing found."
        return result
    accession_nodash = filing["accessionNumber"].replace("-", "")
    result["filing"] = {"accession_number": filing["accessionNumber"], "filing_date": filing.get("filingDate"), "form": "10-K"}

    try:
        index_json = await sec_client.get_filing_index(cik, accession_nodash)
        instance_name = _pick_instance_filename(index_json)
        if not instance_name:
            result["reason"] = "Not reported / unavailable - could not locate an XBRL instance document in this filing."
            return result
        raw = await sec_client.get_filing_file(cik, accession_nodash, instance_name)
        root = ET.fromstring(raw)
    except (SecUnavailableError, TickerNotFoundError, ET.ParseError) as exc:
        result["reason"] = f"Not reported / unavailable - could not parse this filing's XBRL instance document ({exc})."
        return result

    contexts = _parse_contexts(root)
    facts = _parse_facts(root, contexts)
    if not facts:
        result["reason"] = "Not reported / unavailable - this filer did not tag segment or geographic revenue disaggregation in its XBRL."
        return result

    business = _build_breakdown(facts, BUSINESS_AXES, annual_period_start, annual_period_end)
    geo = _build_breakdown(facts, GEOGRAPHIC_AXES, annual_period_start, annual_period_end)

    result["business_segments"] = business
    result["geographic"] = geo
    result["available"] = bool(business or geo)
    if not result["available"]:
        result["reason"] = "Not reported / unavailable - no segment/geographic revenue breakdown found for the most recent fiscal year in this filer's XBRL."
    return result
