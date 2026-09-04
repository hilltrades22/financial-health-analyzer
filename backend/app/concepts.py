"""
Concept-level SEC retrieval.

SEC exposes two XBRL endpoints:

  * /api/xbrl/companyfacts/CIK##########.json   - every fact the filer has
    ever tagged, in one payload. For a long-established filer such as
    JPMorgan this is well over 100 MB, which on a small instance means slow
    cold starts, memory pressure and requests that never return.
  * /api/xbrl/companyconcept/CIK##########/<taxonomy>/<concept>.json - the
    full history of ONE concept, typically 5-20 KB.

This module fetches only the concepts the analysis actually reads, and
assembles them into exactly the same shape companyfacts returns:

    {"facts": {"us-gaap": {"Assets": {"units": {"USD": [ ... ]}}}}}

Because the shape is identical, normalization, quality, risk, sector rules,
scoring and story generation are all completely unchanged - there is one
financial pipeline, not two. The concept list is derived from the tag lists
that already live in normalize.py rather than duplicated here, so adding a
concept in one place keeps retrieval and normalization in step.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable, Optional

from . import normalize as N
from . import sector_rules as SR
from .sec_client import SecClient, SecUnavailableError, TickerNotFoundError

# Tags that only exist in the IFRS taxonomy. Everything else is tried under
# us-gaap first. A handful (Assets, Liabilities, GrossProfit) are spelled the
# same in both taxonomies, which is harmless: we only ever query the
# taxonomy a given filer actually uses.
IFRS_ONLY_TAGS = {
    "CashAndCashEquivalents", "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss",
    "OtherCurrentFinancialAssets", "OtherNoncurrentFinancialAssets",
    "NoncurrentFinancialAssetsAtFairValueThroughOtherComprehensiveIncome",
    "ShorttermBorrowings", "CurrentPortionOfLongtermBorrowings",
    "NoncurrentPortionOfLongtermBorrowings", "LongtermBorrowings", "Borrowings",
    "EquityAttributableToOwnersOfParent", "Equity", "RetainedEarnings",
    "CurrentLeaseLiabilities", "NoncurrentLeaseLiabilities",
    "CashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInInvestingActivities",
    "CashFlowsFromUsedInFinancingActivities",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "ProfitLossFromOperatingActivities", "FinanceCosts", "Revenue",
    "RevenueFromContractsWithCustomers", "ProfitLossAttributableToOwnersOfParent",
    "CurrentAssets", "CurrentLiabilities", "NoncurrentLiabilities", "CostOfSales",
    "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare",
    "DepreciationAndAmortisationExpense", "DividendsPaidClassifiedAsFinancingActivities",
    "DividendsPerShareDeclared", "PaymentsToAcquireOrRedeemEntitysShares",
    "NumberOfSharesOutstanding", "IssuedCapitalNumberOfShares",
}

# Each entry is one normalized metric and its candidate concepts in priority
# order. Retrieval walks the candidates and stops at the first one the filer
# actually reports, so a typical company costs roughly one request per metric
# rather than one per possible spelling. The lists come from normalize.py so
# retrieval and normalization can never drift apart.
_METRIC_GROUPS = [
    "CASH_TAGS", "SHORT_TERM_INVESTMENT_TAGS", "LONG_TERM_INVESTMENT_TAGS",
    "DEBT_CURRENT_AGGREGATE_TAGS", "DEBT_CURRENT_COMPONENT_TAGS", "LONG_TERM_DEBT_TAGS",
    "TOTAL_LIABILITIES_TAGS", "TOTAL_EQUITY_TAGS", "TREASURY_STOCK_TAGS",
    "PREFERRED_STOCK_TAGS", "RETAINED_EARNINGS_TAGS",
    "OPERATING_LEASE_CURRENT_TAGS", "OPERATING_LEASE_NONCURRENT_TAGS",
    "FINANCE_LEASE_CURRENT_TAGS", "FINANCE_LEASE_NONCURRENT_TAGS",
    "OPERATING_CASH_FLOW_TAGS", "CAPEX_TAGS", "OPERATING_INCOME_TAGS",
    "INTEREST_EXPENSE_TAGS", "REPURCHASE_TAGS", "REVENUE_TAGS", "NET_INCOME_TAGS",
    "ASSETS_TAGS", "ASSETS_CURRENT_TAGS", "LIABILITIES_CURRENT_TAGS",
    "LIABILITIES_NONCURRENT_TAGS", "GROSS_PROFIT_TAGS", "COST_OF_REVENUE_TAGS",
    "DILUTED_EPS_TAGS", "BASIC_EPS_TAGS", "DEPRECIATION_TAGS",
    "DIVIDENDS_PAID_TAGS", "DIVIDENDS_PER_SHARE_TAGS",
    "INVESTING_CASH_FLOW_TAGS", "FINANCING_CASH_FLOW_TAGS",
]

# Sector-specific metrics, same ordered-candidate treatment. Like the metric
# groups above these are referenced by NAME from the module that consumes
# them, so a candidate spelling added to a sector rule is fetched without
# anyone having to remember to copy it here.
_SECTOR_GROUPS = [
    "DEPOSITS_TAGS", "NET_INTEREST_INCOME_TAGS", "CREDIT_PROVISION_TAGS",
    "RENTAL_REVENUE_TAGS", "RD_TAGS",
]

_SHARE_GROUP = ["CommonStockSharesOutstanding", "CommonStockSharesIssued",
                "NumberOfSharesOutstanding", "IssuedCapitalNumberOfShares"]


def metric_groups() -> list[list[str]]:
    """Every metric as an ordered list of candidate concepts."""
    groups: list[list[str]] = []
    for name in _METRIC_GROUPS:
        tags = list(getattr(N, name, []) or [])
        if tags:
            groups.append(tags)
    for name in _SECTOR_GROUPS:
        tags = list(getattr(SR, name, []) or [])
        if tags:
            groups.append(tags)
    groups.append(list(_SHARE_GROUP))
    return groups


def required_concepts() -> list[str]:
    """De-duplicated, order-preserving list of every concept we may request."""
    seen: set[str] = set()
    out: list[str] = []
    for group in metric_groups():
        for tag in group:
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out


def candidates_for_taxonomy(group: list[str], taxonomy: str) -> list[str]:
    """The candidates in this group that can exist in the filer's taxonomy,
    so a request is never spent asking us-gaap for an IFRS-only concept."""
    shared = {"Assets", "Liabilities", "GrossProfit"}
    if taxonomy == "ifrs-full":
        return [t for t in group if t in IFRS_ONLY_TAGS or t in shared]
    return [t for t in group if t not in IFRS_ONLY_TAGS]


def concepts_for_taxonomy(taxonomy: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for group in metric_groups():
        for tag in candidates_for_taxonomy(group, taxonomy):
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out


# Concepts probed to work out which taxonomy a filer reports under. Assets is
# reported by essentially every operating company in both taxonomies; the
# income-statement fallbacks cover filers that somehow omit it.
_THROTTLED_MESSAGE = (
    "SEC EDGAR did not answer the requests for this company's financial "
    "concepts, most likely because it is rate-limiting this service. No "
    "financial data was retrieved, so no analysis is shown rather than one "
    "in which every measure would wrongly read as unreported."
)

_TAXONOMY_PROBES = [
    ("us-gaap", "Assets"), ("ifrs-full", "Assets"),
    ("us-gaap", "Liabilities"), ("ifrs-full", "Revenue"),
]


async def detect_taxonomy(client: SecClient, cik: int,
                          stats: Optional[dict[str, int]] = None) -> Optional[str]:
    """Which accounting taxonomy this filer reports under, established from
    its own data rather than assumed from where it is listed.

    A probe SEC refuses to answer is counted in *stats* when one is given, so
    the caller can tell "this filer reports nothing" apart from "SEC would not
    talk to us" - two situations that look identical here but mean very
    different things to a reader.
    """
    for taxonomy, tag in _TAXONOMY_PROBES:
        try:
            payload = await client.get_company_concept(cik, taxonomy, tag)
        except SecUnavailableError:
            if stats is not None:
                stats["unavailable"] = stats.get("unavailable", 0) + 1
            continue
        except TickerNotFoundError:
            continue
        if payload and (payload.get("units") or {}):
            return taxonomy
    return None


async def build_facts_bundle(client: SecClient, cik: int,
                             entity_name: str = "",
                             concurrency: int = 4) -> dict[str, Any]:
    """Assemble a companyfacts-shaped payload from concept-level requests.

    Returns the same structure the companyfacts endpoint would, restricted to
    the concepts this application actually reads, so every downstream module
    works unchanged. Concepts the filer does not report are simply absent,
    exactly as they would be in a real companyfacts payload.
    """
    stats = {"requests": 0, "hits": 0, "unavailable": 0}
    taxonomy = await detect_taxonomy(client, cik, stats)
    bundle: dict[str, Any] = {
        "cik": cik,
        "entityName": entity_name,
        "facts": {},
        # Marks how this payload was built, so a caller can tell a targeted
        # bundle from a full companyfacts download.
        "_forge_retrieval": {"mode": "companyconcept", "taxonomy": taxonomy},
    }
    if taxonomy is None:
        if stats["unavailable"]:
            raise SecUnavailableError(_THROTTLED_MESSAGE)
        bundle["_forge_retrieval"].update(stats)
        return bundle

    gate = asyncio.Semaphore(concurrency)
    facts: dict[str, Any] = {}

    async def fetch_group(group: list[str]) -> None:
        # Walk this metric's candidates in priority order and stop at the
        # first the filer actually reports - the later spellings exist only
        # as fallbacks and cost nothing when the first one hits.
        for tag in candidates_for_taxonomy(group, taxonomy):
            if tag in facts:
                return
            async with gate:
                stats["requests"] += 1
                try:
                    payload = await client.get_company_concept(cik, taxonomy, tag)
                except SecUnavailableError:
                    # SEC declined to answer. That is emphatically not the
                    # same as the filer not reporting the concept, so it is
                    # counted rather than quietly treated as a miss.
                    stats["unavailable"] += 1
                    continue
                except TickerNotFoundError:
                    continue
                except Exception:  # noqa: BLE001 - one bad concept must not fail the analysis
                    continue
            units = (payload or {}).get("units") or {}
            if units:
                facts[tag] = {"units": units}
                stats["hits"] += 1
                return

    await asyncio.gather(*(fetch_group(g) for g in metric_groups()))

    # Nothing came back AND SEC was refusing requests. Returning an empty
    # bundle here would render an analysis in which every single measure reads
    # "not reported by this company" - a confident, wrong statement about the
    # filer when the real story is that we could not ask. Say what actually
    # happened instead.
    if not facts and stats["unavailable"]:
        raise SecUnavailableError(_THROTTLED_MESSAGE)

    bundle["facts"][taxonomy] = facts
    bundle["_forge_retrieval"].update(stats)

    # The cover-page share count every filer reports, used when the
    # accounting taxonomy carries no share-count concept (common for foreign
    # private issuers).
    try:
        dei = await client.get_company_concept(cik, "dei", "EntityCommonStockSharesOutstanding")
        if dei and (dei.get("units") or {}):
            bundle["facts"]["dei"] = {"EntityCommonStockSharesOutstanding": {"units": dei["units"]}}
    except (SecUnavailableError, TickerNotFoundError):
        pass
    except Exception:  # noqa: BLE001
        pass

    return bundle
