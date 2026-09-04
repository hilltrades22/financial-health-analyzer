"""
Concept-level SEC retrieval.

The company-facts endpoint returns every fact a filer has ever tagged - well
over 100 MB for a long-established filer like JPMorgan. These tests cover the
targeted alternative: fetch only the concepts the analysis reads, assemble
them into the same shape company-facts returns, and cache and de-duplicate
the requests.
"""
import asyncio

import httpx
import pytest
import respx

from backend.app import concepts as C
from backend.app import sec_client as SC
from backend.app.sec_client import SecClient, SecUnavailableError


@pytest.fixture(autouse=True)
def clear_concept_cache():
    SC._concept_cache._data.clear()
    SC._inflight.clear()
    yield
    SC._concept_cache._data.clear()
    SC._inflight.clear()


def _concept(taxonomy, tag, unit="USD", entries=None):
    return {"cik": 320193, "taxonomy": taxonomy, "tag": tag, "entityName": "Test",
            "units": {unit: entries if entries is not None else [
                {"end": "2025-12-31", "val": 1000.0, "form": "10-K", "filed": "2026-02-01",
                 "fy": 2025, "fp": "FY", "accn": "a"}]}}


def _url(cik, taxonomy, tag):
    return f"https://data.sec.gov/api/xbrl/companyconcept/CIK{str(cik).zfill(10)}/{taxonomy}/{tag}.json"


# --- Registry ------------------------------------------------------------

def test_registry_is_derived_from_the_normalization_tag_lists():
    """Retrieval and normalization must never drift apart, so the concept
    list is generated from normalize.py rather than duplicated."""
    from backend.app import normalize as N
    required = C.required_concepts()
    for tag in N.REVENUE_TAGS + N.TOTAL_EQUITY_TAGS + N.OPERATING_CASH_FLOW_TAGS:
        assert tag in required
    assert len(required) == len(set(required))          # de-duplicated
    assert "Deposits" in required                        # sector concepts included
    assert "CommonStockSharesOutstanding" in required    # share counts included


def test_taxonomy_filtering_never_asks_a_filer_for_the_wrong_spelling():
    us = C.concepts_for_taxonomy("us-gaap")
    ifrs = C.concepts_for_taxonomy("ifrs-full")
    assert "Revenues" in us and "Revenue" not in us
    assert "Revenue" in ifrs and "Revenues" not in ifrs
    assert "CashFlowsFromUsedInOperatingActivities" in ifrs
    assert "NetCashProvidedByUsedInOperatingActivities" in us
    # Concepts spelled identically in both taxonomies stay available to both.
    assert "Assets" in us and "Assets" in ifrs


def test_metric_groups_are_ordered_candidates_not_a_flat_list():
    groups = C.metric_groups()
    assert all(isinstance(g, list) and g for g in groups)
    revenue = next(g for g in groups if "Revenues" in g)
    assert revenue.index("Revenues") < revenue.index("SalesRevenueNet")


# --- Retrieval -----------------------------------------------------------

@respx.mock
def test_us_gaap_bundle_has_the_same_shape_as_company_facts():
    respx.get(_url(320193, "us-gaap", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    respx.get(_url(320193, "us-gaap", "Revenues")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Revenues")))
    # Registered last: respx matches in order, so this catches everything else.
    respx.get(url__regex=r".*/companyconcept/.*").mock(return_value=httpx.Response(404))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 320193, "Apple Inc."))
    asyncio.run(client.aclose())

    assert bundle["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] == 1000.0
    assert "Revenues" in bundle["facts"]["us-gaap"]
    assert bundle["_forge_retrieval"]["taxonomy"] == "us-gaap"
    # The shape must be interchangeable with a real company-facts payload.
    from backend.app import normalize as N
    fv = N.latest_instant(bundle, N.ASSETS_TAGS, N.QUARTERLY_ELIGIBLE_FORMS)
    assert fv.available and fv.value == 1000.0


@respx.mock
def test_ifrs_filer_is_detected_and_read_in_its_own_taxonomy():
    respx.get(_url(1046179, "ifrs-full", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("ifrs-full", "Assets", unit="TWD")))
    respx.get(_url(1046179, "ifrs-full", "Revenue")).mock(
        return_value=httpx.Response(200, json=_concept("ifrs-full", "Revenue", unit="TWD")))
    respx.get(url__regex=r".*/companyconcept/.*").mock(return_value=httpx.Response(404))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 1046179, "TSMC"))
    asyncio.run(client.aclose())

    assert bundle["_forge_retrieval"]["taxonomy"] == "ifrs-full"
    assert "Revenue" in bundle["facts"]["ifrs-full"]
    assert "us-gaap" not in bundle["facts"]
    from backend.app import normalize as N
    assert N.reporting_currency(bundle) == "TWD"


@respx.mock
def test_ordered_fallback_stops_at_the_first_concept_the_filer_reports():
    """The later spellings are fallbacks: if the first candidate hits, the
    rest must not cost a request."""
    respx.get(_url(1, "us-gaap", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    first = respx.get(_url(1, "us-gaap", "Revenues")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Revenues")))
    later = respx.get(_url(1, "us-gaap", "SalesRevenueNet")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "SalesRevenueNet")))
    respx.get(url__regex=r".*/companyconcept/.*").mock(return_value=httpx.Response(404))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 1, "Test"))
    asyncio.run(client.aclose())

    assert first.called
    assert not later.called
    assert "SalesRevenueNet" not in bundle["facts"]["us-gaap"]


@respx.mock
def test_a_concept_the_filer_does_not_report_is_absent_not_zero():
    respx.get(_url(1, "us-gaap", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    respx.get(url__regex=r".*/companyconcept/.*").mock(return_value=httpx.Response(404))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 1, "Test"))
    asyncio.run(client.aclose())

    facts = bundle["facts"]["us-gaap"]
    assert "Deposits" not in facts       # missing, never fabricated as 0
    assert facts["Assets"]["units"]["USD"][0]["val"] == 1000.0


@respx.mock
def test_bundle_is_empty_when_no_taxonomy_can_be_established():
    """A filer we cannot read must produce an empty bundle so the caller can
    fall back, rather than a half-built one that looks like real coverage."""
    respx.get(url__regex=r".*/companyconcept/.*").mock(return_value=httpx.Response(404))
    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 999, "Unknown"))
    asyncio.run(client.aclose())
    assert bundle["facts"] == {}
    assert bundle["_forge_retrieval"]["taxonomy"] is None


# --- Cache and de-duplication -------------------------------------------

@respx.mock
def test_repeat_requests_are_served_from_cache():
    route = respx.get(_url(1, "us-gaap", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    client = SecClient()

    async def go():
        a = await client.get_company_concept(1, "us-gaap", "Assets")
        b = await client.get_company_concept(1, "us-gaap", "Assets")
        return a, b

    a, b = asyncio.run(go())
    asyncio.run(client.aclose())
    assert a == b
    assert route.call_count == 1      # second call never reached SEC


@respx.mock
def test_a_missing_concept_is_cached_too():
    """Re-asking SEC for a known 404 on every analysis is exactly the traffic
    the rate limiter exists to avoid."""
    route = respx.get(_url(1, "us-gaap", "Deposits")).mock(return_value=httpx.Response(404))
    client = SecClient()

    async def go():
        return (await client.get_company_concept(1, "us-gaap", "Deposits"),
                await client.get_company_concept(1, "us-gaap", "Deposits"))

    a, b = asyncio.run(go())
    asyncio.run(client.aclose())
    assert a is None and b is None
    assert route.call_count == 1


@respx.mock
def test_concurrent_requests_for_one_concept_make_a_single_sec_request():
    route = respx.get(_url(1, "us-gaap", "Assets")).mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    client = SecClient()

    async def go():
        return await asyncio.gather(*(client.get_company_concept(1, "us-gaap", "Assets")
                                      for _ in range(6)))

    results = asyncio.run(go())
    asyncio.run(client.aclose())
    assert all(r == results[0] for r in results)
    assert route.call_count == 1      # six callers, one request


def test_cache_is_bounded_and_evicts_oldest_entries():
    cache = SC._ConceptCache(ttl=3600, max_entries=3)
    for i in range(5):
        cache.put((i,), {"v": i})
    assert cache.stats()["entries"] == 3
    assert cache.get((0,))[0] is False     # evicted
    assert cache.get((4,))[0] is True      # most recent retained


def test_cache_entries_expire():
    cache = SC._ConceptCache(ttl=0, max_entries=10)
    cache.put(("k",), {"v": 1})
    assert cache.get(("k",))[0] is False


@respx.mock
def test_sec_throttling_is_reported_as_throttling_not_a_missing_concept():
    respx.get(_url(1, "us-gaap", "Assets")).mock(return_value=httpx.Response(429))
    client = SecClient()
    with pytest.raises(SecUnavailableError) as exc:
        asyncio.run(client.get_company_concept(1, "us-gaap", "Assets"))
    asyncio.run(client.aclose())
    assert "rate-limiting" in str(exc.value)


# --- Registry completeness -------------------------------------------------
#
# Concept retrieval only fetches what the registry names. If a module grows a
# new candidate spelling and the registry does not know about it, the concept
# is silently never requested and the affected measure degrades to
# UNAVAILABLE in production while every mocked test keeps passing. These
# tests fail loudly on that drift instead.

def _tag_list_names(module) -> list[str]:
    return [n for n in dir(module) if n.endswith("_TAGS") and not n.startswith("_")
            and isinstance(getattr(module, n), list)]


def test_registry_covers_every_normalize_tag_list():
    from backend.app import normalize as N
    from backend.app import concepts as C

    registered = set(C._METRIC_GROUPS)
    declared = set(_tag_list_names(N))
    assert declared - registered == set(), (
        "normalize tag lists missing from the concept registry: "
        f"{sorted(declared - registered)}"
    )
    assert registered - declared == set(), (
        "concept registry names tag lists normalize no longer defines: "
        f"{sorted(registered - declared)}"
    )


def test_registry_covers_every_sector_rule_tag_list():
    from backend.app import sector_rules as SR
    from backend.app import concepts as C

    registered = set(C._SECTOR_GROUPS)
    declared = set(_tag_list_names(SR))
    assert declared - registered == set(), (
        "sector rule tag lists missing from the concept registry: "
        f"{sorted(declared - registered)}"
    )
    assert registered - declared == set(), (
        "concept registry names tag lists sector_rules no longer defines: "
        f"{sorted(registered - declared)}"
    )


def test_every_candidate_spelling_is_requestable():
    """Each candidate must be reachable under at least one taxonomy, or it is
    dead weight that can never be fetched."""
    from backend.app import concepts as C

    reachable = set(C.concepts_for_taxonomy("us-gaap")) | set(
        C.concepts_for_taxonomy("ifrs-full"))
    unreachable = [t for t in C.required_concepts() if t not in reachable]
    assert unreachable == [], f"candidates no taxonomy can request: {unreachable}"


def test_reit_rental_revenue_candidates_are_all_registered():
    """Regression: RENTAL_REVENUE_TAGS gained a fourth spelling that the
    hand-copied registry did not have, so REIT rental revenue could go
    UNAVAILABLE for filers using only that spelling."""
    from backend.app import sector_rules as SR
    from backend.app import concepts as C

    req = set(C.required_concepts())
    assert set(SR.RENTAL_REVENUE_TAGS) <= req


# --- Throttling must never be reported as "the company reports nothing" ----
#
# Every concept request is independent, and a failed one used to be swallowed
# the same way a genuine 404 was. Under SEC throttling that produced an empty
# bundle, and an empty bundle renders an analysis in which every measure reads
# "not reported by this company" - a confident, wrong statement about the
# filer. These tests pin the distinction.

def test_an_empty_bundle_is_never_returned_when_sec_refused():
    """The guard itself: no facts plus at least one refusal must raise, never
    return a bundle that would render as "this company reports nothing".

    Every taxonomy probe tag is also a metric tag and probe results are
    cached, so in practice a successful probe always leaves one fact behind -
    this state is reachable only defensively, which is exactly why it is
    asserted directly rather than through a mocked HTTP scenario.
    """
    import inspect

    src = inspect.getsource(C.build_facts_bundle)
    assert 'if not facts and stats["unavailable"]:' in src
    assert "raise SecUnavailableError(_THROTTLED_MESSAGE)" in src
    assert "rate-limiting" in C._THROTTLED_MESSAGE


@respx.mock
def test_throttled_taxonomy_probe_also_raises():
    """If SEC will not even answer the probes, the filer's taxonomy is unknown
    because we could not ask - not because the filer reports nothing."""
    respx.get(url__regex=r".*companyconcept.*").mock(
        return_value=httpx.Response(429, text="rate limited"))

    client = SecClient()
    with pytest.raises(SecUnavailableError):
        asyncio.run(C.build_facts_bundle(client, 320193, "Throttled Co"))


@respx.mock
def test_a_filer_that_simply_reports_nothing_is_not_an_error():
    """404s are the normal "this filer does not tag that" answer and must keep
    returning an empty bundle so the Company Facts fallback can run."""
    respx.get(url__regex=r".*companyconcept.*").mock(
        return_value=httpx.Response(404, text="not found"))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 320193, "Quiet Co"))
    assert bundle["facts"] == {}
    assert bundle["_forge_retrieval"]["unavailable"] == 0


@respx.mock
def test_concepts_that_resolve_are_kept_even_when_some_are_throttled():
    """A partial answer is still a real answer - it must not be discarded."""
    respx.get(url__regex=r".*companyconcept.*us-gaap/Assets\.json").mock(
        return_value=httpx.Response(200, json=_concept("us-gaap", "Assets")))
    respx.get(url__regex=r".*companyconcept.*").mock(
        return_value=httpx.Response(429, text="rate limited"))

    client = SecClient()
    bundle = asyncio.run(C.build_facts_bundle(client, 320193, "Partly Co"))
    assert "Assets" in bundle["facts"]["us-gaap"]
    assert bundle["_forge_retrieval"]["unavailable"] > 0


# --- The Company Facts fallback must actually be reachable ----------------

def test_detected_taxonomy_with_no_concepts_still_counts_as_no_data():
    """{"us-gaap": {}} is a truthy dict, so a naive truthiness check on
    "facts" would skip the fallback and analyse a company with no financials
    in hand."""
    from backend.app.main import _has_financial_concepts

    assert _has_financial_concepts({"facts": {"us-gaap": {}}}) is False
    assert _has_financial_concepts({"facts": {}}) is False
    assert _has_financial_concepts({}) is False


def test_a_cover_page_share_count_alone_is_not_enough_to_analyse():
    from backend.app.main import _has_financial_concepts

    only_dei = {"facts": {"us-gaap": {},
                          "dei": {"EntityCommonStockSharesOutstanding": {"units": {}}}}}
    assert _has_financial_concepts(only_dei) is False


def test_one_resolved_accounting_concept_is_enough():
    from backend.app.main import _has_financial_concepts

    assert _has_financial_concepts({"facts": {"us-gaap": {"Assets": {"units": {}}}}}) is True
    assert _has_financial_concepts({"facts": {"ifrs-full": {"Assets": {"units": {}}}}}) is True
