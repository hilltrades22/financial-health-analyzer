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

import datetime
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

# A filer may tag revenue on BOTH of these axes for the same period (e.g.
# Amazon reports North America / International / AWS operating segments on
# StatementBusinessSegmentsAxis *and, separately*, Online stores / AWS /
# Advertising / etc. product-and-service categories on ProductOrServiceAxis).
# These are two different, non-comparable partitions of the same total
# revenue - merging them into one breakdown silently mixes incompatible
# categories and produces a total that doesn't reconcile to actual revenue.
# We therefore try each axis SEPARATELY and keep only the one whose members
# actually reconcile to the company's real total revenue.
BUSINESS_SEGMENT_AXIS = {"StatementBusinessSegmentsAxis"}
PRODUCT_SERVICE_AXIS = {"ProductOrServiceAxis"}
GEOGRAPHIC_AXES = {"StatementGeographicalAxis"}
_RECONCILE_TOLERANCE = 0.03  # breakdown total must be within 3% of real revenue

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
    spaced = re.sub(r"\bHomeand\b", "Home and", spaced)  # Apple's own tag name lacks a space here
    return spaced or name or member


def find_latest_10k(submissions: dict[str, Any]) -> Optional[dict[str, Any]]:
    filings = find_10k_filings(submissions)
    return filings[0] if filings else None


def find_10k_filings(submissions: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    """Most recent 10-K filings, newest first. Used so that if the very
    latest 10-K's XBRL instance document isn't yet fully available from SEC
    EDGAR's static file server (this happens for filings filed very
    recently - the index.json can briefly list only a handful of index/
    header files before the full document set is published), we can fall
    back to the prior year's 10-K rather than showing nothing. Any fallback
    used is clearly labeled with its own real filing date/period - never
    silently presented as the latest year's data."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    dates = recent.get("filingDate") or []
    out = []
    for i, form in enumerate(forms):
        if form == "10-K":
            out.append({
                "accessionNumber": accessions[i],
                "primaryDocument": docs[i] if i < len(docs) else None,
                "filingDate": dates[i] if i < len(dates) else None,
            })
            if len(out) >= limit:
                break
    return out


def _pick_instance_filename(index_json: dict[str, Any]) -> Optional[str]:
    items = (index_json.get("directory") or {}).get("item") or []
    candidates = []
    for item in items:
        name = item.get("name", "")
        if not name.lower().endswith(".xml"):
            continue
        if any(name.lower().endswith(suf) for suf in _LINKBASE_SUFFIXES):
            continue
        if name.lower() in ("filingsummary.xml", "metalinks.json"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        candidates.append((name, size))
    if not candidates:
        return None
    # The real XBRL instance document is, by a wide margin, the largest
    # remaining .xml file (it carries every tagged fact in the filing) and
    # modern filers name it "<ticker>-<date>_htm.xml" (inline XBRL); older
    # filings sometimes omit the "_htm" suffix. Prefer that naming, then
    # fall back to simply the largest file so a naming-convention change
    # from any given filer doesn't silently break this.
    candidates.sort(key=lambda c: (0 if c[0].lower().endswith("_htm.xml") else 1, -c[1]))
    return candidates[0][0]


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


def _drop_aggregate_members(by_member: dict[str, float]) -> dict[str, float]:
    """Some filers (e.g. Apple) tag both an umbrella member ('Product',
    'Service') AND its individual children (iPhone, Mac, iPad, ...) on the
    very same axis. Left alone, that double-counts revenue and produces
    percentages that sum to well over 100%. Detect any member whose value
    is (within a small tolerance) exactly the sum of two or more OTHER
    members, and drop it as a roll-up rather than a real line item."""
    import itertools

    labels = list(by_member.keys())
    if len(labels) < 3:
        return by_member
    to_drop = set()
    for i, li in enumerate(labels):
        vi = by_member[li]
        others = [lj for j, lj in enumerate(labels) if j != i]
        found = False
        for r in range(2, len(others) + 1):
            for combo in itertools.combinations(others, r):
                s = sum(by_member[c] for c in combo)
                if abs(s - vi) <= max(1.0, vi * 0.005):
                    found = True
                    break
            if found:
                break
        if found:
            to_drop.add(li)
    return {l: v for l, v in by_member.items() if l not in to_drop}


def _infer_annual_period(facts: list[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    """When we fall back to an older 10-K (see find_10k_filings), we don't
    have that filing's own fiscal-year start/end handed to us - infer it
    from the parsed facts themselves: the most common ~1-year (350-380 day)
    duration among the dimensional contexts, preferring the latest end
    date. Real data only - derived from the filing's own XBRL contexts."""
    counts: dict[tuple[str, str], int] = {}
    for f in facts:
        ctx = f["context"]
        start, end = ctx.get("start"), ctx.get("end")
        if not start or not end:
            continue
        try:
            days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
        except ValueError:
            continue
        if 350 <= days <= 380:
            counts[(start, end)] = counts.get((start, end), 0) + 1
    if not counts:
        return None, None
    # Prefer the most frequently tagged annual period; break ties by latest end date.
    best = sorted(counts.items(), key=lambda kv: (kv[1], kv[0][1]), reverse=True)[0][0]
    return best[0], best[1]


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
    by_member = _drop_aggregate_members(by_member)
    total = sum(by_member.values())
    if not by_member or total <= 0:
        return []
    return sorted(
        [{"label": label, "value": v, "pct_of_total": round(v / total * 100, 1)} for label, v in by_member.items()],
        key=lambda r: -r["value"],
    )


async def build_business_mix(sec_client: SecClient, cik: int, submissions: dict[str, Any], annual_period_end: Optional[str], annual_period_start: Optional[str], total_revenue: Optional[float] = None) -> dict[str, Any]:
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

    candidates = find_10k_filings(submissions, limit=3)
    if not candidates:
        result["reason"] = "Not reported / unavailable - no 10-K filing found."
        return result

    def reconciles(rows: list[dict[str, Any]], revenue: Optional[float]) -> bool:
        if not rows or not revenue:
            return bool(rows)  # no ground truth to check against - accept if non-empty
        total = sum(r["value"] for r in rows)
        return abs(total - revenue) <= revenue * _RECONCILE_TOLERANCE

    last_reason = None
    for idx, filing in enumerate(candidates):
        is_latest = idx == 0
        accession_nodash = filing["accessionNumber"].replace("-", "")

        try:
            index_json = await sec_client.get_filing_index(cik, accession_nodash)
            instance_name = _pick_instance_filename(index_json)
            if not instance_name:
                items_dbg = (index_json.get("directory") or {}).get("item") or []
                names_dbg = [i.get("name") for i in items_dbg][:10]
                last_reason = f"Not reported / unavailable - could not locate an XBRL instance document in this filing (names: {names_dbg})."
                continue  # this filing's static files aren't fully published yet - try the prior year's 10-K
            raw = await sec_client.get_filing_file(cik, accession_nodash, instance_name)
            root = ET.fromstring(raw)
        except (SecUnavailableError, TickerNotFoundError, ET.ParseError) as exc:
            last_reason = f"Not reported / unavailable - could not parse this filing's XBRL instance document ({exc})."
            continue
        except Exception as exc:  # noqa: BLE001 - never let one filer's quirk break the whole analysis
            last_reason = f"Not reported / unavailable - unexpected error reading this filing's XBRL instance document ({type(exc).__name__}: {exc})."
            continue

        contexts = _parse_contexts(root)
        facts = _parse_facts(root, contexts)
        if not facts:
            last_reason = "Not reported / unavailable - this filer did not tag segment or geographic revenue disaggregation in its XBRL."
            continue

        if is_latest:
            period_start, period_end = annual_period_start, annual_period_end
            revenue_for_check = total_revenue
        else:
            # Falling back to an older 10-K than the one used for the rest of
            # the analysis: infer that filing's own fiscal year from its XBRL
            # rather than reusing the latest year's period/revenue, and don't
            # require reconciliation against a revenue figure that belongs to
            # a different fiscal year.
            period_start, period_end = _infer_annual_period(facts)
            revenue_for_check = None
            if not period_end:
                last_reason = "Not reported / unavailable - could not determine this filing's fiscal year from its XBRL."
                continue

        # Prefer the true operating-segment breakdown (StatementBusinessSegmentsAxis)
        # over the product/service category breakdown when both exist, since
        # "segments" is what a 10-K's actual segment-reporting footnote means -
        # but only keep whichever one actually adds up to real total revenue.
        segment_breakdown = _build_breakdown(facts, BUSINESS_SEGMENT_AXIS, period_start, period_end)
        product_breakdown = _build_breakdown(facts, PRODUCT_SERVICE_AXIS, period_start, period_end)
        geo = _build_breakdown(facts, GEOGRAPHIC_AXES, period_start, period_end)

        if reconciles(segment_breakdown, revenue_for_check):
            business = segment_breakdown
        elif reconciles(product_breakdown, revenue_for_check):
            business = product_breakdown
        else:
            business = []

        if not reconciles(geo, revenue_for_check):
            geo = []

        if not (business or geo):
            last_reason = "Not reported / unavailable - this filer's segment/geographic XBRL tagging either wasn't found or didn't reconcile to total reported revenue for that fiscal year."
            continue

        result["filing"] = {"accession_number": filing["accessionNumber"], "filing_date": filing.get("filingDate"), "form": "10-K"}
        result["period_end"] = period_end
        result["business_segments"] = business
        result["geographic"] = geo
        result["available"] = True
        if not is_latest:
            result["reason"] = (
                f"Note: the most recent 10-K's XBRL instance document was not yet fully published by SEC EDGAR "
                f"at analysis time, so this shows real reported segment data from the prior 10-K "
                f"(filed {filing.get('filingDate')}, fiscal year ended {period_end}) instead."
            )
        return result

    result["reason"] = last_reason or "Not reported / unavailable - segment data could not be parsed for this company."
    result["filing"] = {"accession_number": candidates[0]["accessionNumber"], "filing_date": candidates[0].get("filingDate"), "form": "10-K"}
    return result
