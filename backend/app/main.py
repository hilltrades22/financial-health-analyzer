from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .classification import build_classification
from .grading import build_grading
from .history import build_financial_timeline, build_historical_scores, explain_score_trend
from .insiders import build_insider_activity
from .market_data import (
    compute_bull_base_bear,
    compute_valuation,
    compute_valuation_history,
    fetch_key_statistics,
    fetch_price_history,
    fetch_stock_price,
)
from .normalize import build_company_financials, reporting_currency
from .pillars import compute_forge_score
from .providers import analyst_data, estimates_data, ownership_data, provider_status
from .quality import compute_financial_quality, compute_piotroski_f_score
from .risk import compute_altman_z_score
from .scoring import score_company
from .sector_rules import evaluate_with_sector
from .sec_client import (
    SecClient,
    SecUnavailableError,
    TickerNotFoundError,
    SEC_USER_AGENT,
)
from .segments import build_business_mix
from .shareholder_returns import build_shareholder_returns
from .story import build_financial_story, build_story_sections

app = FastAPI(title="FORGE Financial Intelligence", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_sec_client = SecClient()

# A handful of very large filers (JPMorgan's company-facts payload is well
# over 100 MB) take far longer to download and parse than a normal company.
# On a single-worker instance an unbounded request for one of them starves
# every other request, so analysis is bounded two ways: only a small number
# run concurrently, and any single one gives up rather than hanging forever.
# The caller gets a clear, honest error instead of a request that never
# returns and a service that appears dead.
_ANALYSIS_CONCURRENCY = 2
_ANALYSIS_TIMEOUT_S = float(os.environ.get("ANALYSIS_TIMEOUT_SECONDS", "110"))
_analysis_semaphore = asyncio.Semaphore(_ANALYSIS_CONCURRENCY)

# Simple in-memory cache so re-analyzing the same ticker within a short
# window doesn't hammer SEC EDGAR (SEC asks for reasonable request rates).
_CACHE_TTL = 15 * 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _fact_to_dict(fv) -> dict[str, Any]:
    return asdict(fv)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sec_user_agent_configured": bool(SEC_USER_AGENT),
    }


async def _analyze_ticker(ticker_key: str, frequency: str = "annual") -> dict[str, Any]:
    now = time.time()
    cache_key = f"{ticker_key}:{frequency}"
    cached = _cache.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    try:
        resolved = await _sec_client.resolve_ticker(ticker_key)
    except TickerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SecUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"SEC EDGAR is currently unavailable: {exc}") from exc

    cik = resolved["cik"]
    name = resolved["title"]

    try:
        company_facts = await _sec_client.get_company_facts(cik)
    except TickerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SecUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"SEC EDGAR is currently unavailable: {exc}") from exc

    submissions: dict[str, Any] = {}
    try:
        submissions = await _sec_client.get_submissions(cik)
    except (TickerNotFoundError, SecUnavailableError):
        submissions = {}

    # Normalisation, scoring, quality, history and the timeline are all
    # pure CPU work over a potentially very large facts payload. Run them in
    # a worker thread so one big filer cannot stall the event loop and make
    # the whole service unresponsive to other requests.
    def _cpu_analysis():
        _cf = build_company_financials(
            ticker_key, cik, name or company_facts.get("entityName", ticker_key), company_facts)
        return (
            _cf,
            score_company(_cf),
            compute_financial_quality(company_facts, _cf.annual),
            compute_piotroski_f_score(company_facts),
            build_historical_scores(company_facts),
            build_financial_timeline(company_facts, frequency=frequency),
        )

    cf, score, quality_metrics, piotroski, historical_scores, timeline = await asyncio.to_thread(_cpu_analysis)
    trend_story = explain_score_trend(historical_scores)

    # Live market price is best-effort and clearly separated from SEC data.
    price_info, key_stats = await asyncio.gather(
        fetch_stock_price(ticker_key), fetch_key_statistics(ticker_key)
    )
    total_debt = None
    cash_val = None
    if cf.quarterly.short_term_debt.available or cf.quarterly.long_term_debt.available:
        total_debt = (cf.quarterly.short_term_debt.value or 0) + (cf.quarterly.long_term_debt.value or 0)
    if cf.quarterly.cash_and_equivalents.available:
        cash_val = cf.quarterly.cash_and_equivalents.value
    valuation = compute_valuation(company_facts, price_info, total_debt, cash_val, key_stats)
    valuation_history = await compute_valuation_history(ticker_key, company_facts, total_debt, cash_val)
    bull_base_bear = compute_bull_base_bear(valuation, valuation_history)

    market_cap_val = valuation.get("market_cap", {}).get("value") if valuation.get("market_cap", {}).get("available") else None
    altman = compute_altman_z_score(company_facts, market_cap_val)

    # Sector / industry / exchange / country, from SEC's own company metadata.
    # Also carries the peer group that sector-aware interpretation keys off.
    classification = build_classification(submissions, market_cap_val)

    # Re-run the health assessment with business-model awareness: rules that
    # do not describe this kind of company become NOT_APPLICABLE (excluded
    # from the score rather than failed) and peer-appropriate rules are added.
    try:
        sector_score = await asyncio.to_thread(
            evaluate_with_sector, score, cf, company_facts, classification)
    except Exception:  # noqa: BLE001 - never let sector logic break the base analysis
        sector_score = None
    if sector_score:
        score = {**score, **sector_score}
    story = build_financial_story(cf.name, cf.ticker, score, classification, valuation)
    story_sections = build_story_sections(cf.name, cf.ticker, score, classification, valuation)

    annual_revenue_val = next(
        (t["revenue"] for t in timeline if t.get("period_end") == cf.annual.period_end and t.get("revenue") is not None),
        None,
    )
    try:
        business_mix = await asyncio.wait_for(
            build_business_mix(
                _sec_client, cik, submissions, cf.annual.period_end, cf.annual.period_start, annual_revenue_val
            ),
            timeout=25.0,
        )
    except asyncio.TimeoutError:
        # Business Mix is best-effort - never let a slow filing (a large XBRL
        # instance document, or a fallback that had to try multiple 10-Ks)
        # hold up the rest of the analysis.
        business_mix = {"available": False, "reason": "Not reported / unavailable - segment data took too long to retrieve from SEC EDGAR for this company.", "business_segments": [], "geographic": []}
    except Exception:
        # Business Mix is best-effort - never let a parsing edge case in one
        # filer's XBRL take down the whole analysis.
        business_mix = {"available": False, "reason": "Not reported / unavailable - segment data could not be parsed for this company.", "business_segments": [], "geographic": []}

    # Insider activity from real Form 4 filings. Best-effort and bounded: it
    # must never delay or break the SEC financial analysis.
    try:
        insider_activity = await asyncio.wait_for(
            build_insider_activity(_sec_client, cik, submissions), timeout=20.0)
    except asyncio.TimeoutError:
        insider_activity = {"available": False, "transactions": [], "summary": None,
                            "reason": "Unavailable - Form 4 filings took too long to retrieve from SEC EDGAR."}
    except Exception:  # noqa: BLE001
        insider_activity = {"available": False, "transactions": [], "summary": None,
                            "reason": "Unavailable - Form 4 insider filings could not be parsed for this company."}

    try:
        shareholder_returns = await asyncio.to_thread(build_shareholder_returns, company_facts)
    except Exception:  # noqa: BLE001
        shareholder_returns = {"dividend": {"pays_dividend": False,
                                             "reason": "Unavailable - dividend data could not be read."},
                               "buyback": {"repurchases_reported": False},
                               "share_count_trend": {"available": False}}

    forge = compute_forge_score(score["overall_score"], piotroski, altman, valuation)
    grading = build_grading(forge, score, piotroski, valuation, altman)

    lease_current = (cf.quarterly.operating_lease_current.value or 0) if cf.quarterly.operating_lease_current.available else 0
    lease_current += (cf.quarterly.finance_lease_current.value or 0) if cf.quarterly.finance_lease_current.available else 0
    lease_noncurrent = (cf.quarterly.operating_lease_noncurrent.value or 0) if cf.quarterly.operating_lease_noncurrent.available else 0
    lease_noncurrent += (cf.quarterly.finance_lease_noncurrent.value or 0) if cf.quarterly.finance_lease_noncurrent.available else 0
    lease_available = any(f.available for f in (
        cf.quarterly.operating_lease_current, cf.quarterly.operating_lease_noncurrent,
        cf.quarterly.finance_lease_current, cf.quarterly.finance_lease_noncurrent,
    ))

    result = {
        "ticker": cf.ticker,
        "cik": cf.cik,
        "company_name": cf.name,
        "sector": classification["sector"]["value"],
        "industry": classification["industry"]["value"],
        "sub_industry": classification["sub_industry"]["value"],
        "exchange": classification["exchange"]["value"],
        "country": classification["country"]["value"],
        "sic_code": submissions.get("sic"),
        "classification": classification,
        "reporting_currency": reporting_currency(company_facts),
        "sec_edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
        "last_updated": None,
        "latest_quarter": {
            "period_end": cf.quarterly.period_end,
            "form": cf.quarterly.form,
            "filed": cf.quarterly.filed,
        },
        "latest_annual": {
            "fiscal_year": cf.annual.fiscal_year,
            "period_end": cf.annual.period_end,
            "period_start": cf.annual.period_start,
            "filed": cf.annual.filed,
        },
        "forge": forge,
        "grading": grading,
        "score": score,
        "financial_story": story,
        "financial_story_sections": story_sections,
        "quality_metrics": quality_metrics,
        "piotroski": piotroski,
        "altman": altman,
        "valuation": valuation,
        "valuation_history": valuation_history,
        "bull_base_bear": bull_base_bear,
        "market_price": price_info,
        "business_mix": business_mix,
        # Non-SEC market data, kept strictly separate from the SEC-based
        # analysis. Unconfigured capabilities report themselves unavailable
        # with a reason rather than returning empty values.
        "market_providers": provider_status(),
        "analyst": analyst_data(ticker_key),
        "estimates": estimates_data(ticker_key),
        "institutional_ownership": ownership_data(ticker_key),
        "insider_activity": insider_activity,
        "shareholder_returns": shareholder_returns,
        "historical_scores": historical_scores,
        "trend_story": trend_story,
        "timeline": timeline,
        "lease_summary": {
            "available": lease_available,
            "current_total": lease_current if lease_available else None,
            "noncurrent_total": lease_noncurrent if lease_available else None,
            "grand_total": (lease_current + lease_noncurrent) if lease_available else None,
            "note": "Already included within Total Liabilities above - shown separately, not added twice.",
        },
        "quarterly_facts": {
            "cash_and_equivalents": _fact_to_dict(cf.quarterly.cash_and_equivalents),
            "short_term_investments": _fact_to_dict(cf.quarterly.short_term_investments),
            "long_term_investments": _fact_to_dict(cf.quarterly.long_term_investments),
            "short_term_debt": _fact_to_dict(cf.quarterly.short_term_debt),
            "long_term_debt": _fact_to_dict(cf.quarterly.long_term_debt),
            "total_liabilities": _fact_to_dict(cf.quarterly.total_liabilities),
            "total_equity": _fact_to_dict(cf.quarterly.total_equity),
            "treasury_stock": _fact_to_dict(cf.quarterly.treasury_stock),
            "preferred_stock": _fact_to_dict(cf.quarterly.preferred_stock),
            "retained_earnings": _fact_to_dict(cf.quarterly.retained_earnings),
            "operating_lease_current": _fact_to_dict(cf.quarterly.operating_lease_current),
            "operating_lease_noncurrent": _fact_to_dict(cf.quarterly.operating_lease_noncurrent),
            "finance_lease_current": _fact_to_dict(cf.quarterly.finance_lease_current),
            "finance_lease_noncurrent": _fact_to_dict(cf.quarterly.finance_lease_noncurrent),
        },
        "annual_facts": {
            "retained_earnings": _fact_to_dict(cf.annual.retained_earnings),
            "prior_retained_earnings": _fact_to_dict(cf.annual.prior_retained_earnings),
            "operating_cash_flow": _fact_to_dict(cf.annual.operating_cash_flow),
            "capital_expenditures": _fact_to_dict(cf.annual.capital_expenditures),
            "operating_income": _fact_to_dict(cf.annual.operating_income),
            "interest_expense": _fact_to_dict(cf.annual.interest_expense),
            "repurchases_of_stock": _fact_to_dict(cf.annual.repurchases_of_stock),
            "treasury_stock": _fact_to_dict(cf.annual.treasury_stock),
        },
        "data_source": "SEC EDGAR (data.sec.gov) - XBRL Company Facts, Submissions, and ticker/CIK mapping. No demo or fabricated data.",
        "market_data_source": "Yahoo Finance (query1.finance.yahoo.com), with Stooq.com as a fallback - used only for live price / market cap / valuation multiples / price history, clearly separate from SEC data.",
    }

    _cache[cache_key] = (now, result)
    return result


@app.get("/api/analyze/{ticker}")
async def analyze(ticker: str, frequency: str = "annual") -> JSONResponse:
    freq = frequency.strip().lower() if frequency.strip().lower() == "quarterly" else "annual"
    symbol = ticker.strip().upper()
    try:
        async with _analysis_semaphore:
            result = await asyncio.wait_for(_analyze_ticker(symbol, freq), timeout=_ANALYSIS_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(
                f"Analysis of {symbol} did not complete within {int(_ANALYSIS_TIMEOUT_S)} seconds. This "
                "company's SEC XBRL dataset is unusually large (some long-established filers publish well "
                "over 100 MB of company facts), which exceeds what this deployment can download and process "
                "in one request. No partial or estimated result is returned."
            ),
        ) from None
    return JSONResponse(result)


@app.get("/api/price-history/{ticker}")
async def price_history(ticker: str, range: str = "1Y") -> JSONResponse:
    result = await fetch_price_history(ticker.strip().upper(), range)
    return JSONResponse(result)


@app.get("/api/compare")
async def compare(tickers: str) -> JSONResponse:
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()][:5]
    if not symbols:
        raise HTTPException(status_code=400, detail="Provide at least one ticker, e.g. ?tickers=AAPL,MSFT")

    async def safe_analyze(sym: str):
        try:
            return await _analyze_ticker(sym)
        except HTTPException as exc:
            return {"ticker": sym, "error": exc.detail}

    results = await asyncio.gather(*(safe_analyze(s) for s in symbols))
    return JSONResponse({"companies": results})


@app.on_event("shutdown")
async def _shutdown() -> None:
    await _sec_client.aclose()


# --- Serve the frontend (single-service deployment) -----------------------
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if _FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIR)), name="assets")

    def _asset_version() -> str:
        """Cache-busting stamp derived from the frontend files' own mtimes.

        index.html references /assets/app.js and /assets/styles.css with no
        version, so browsers happily serve a cached copy after a deploy and
        users see the previous build's UI against the new API. Stamping the
        current file mtime onto those URLs makes each deploy a new URL.
        """
        stamp = 0.0
        for name in ("app.js", "styles.css"):
            f = _FRONTEND_DIR / name
            if f.exists():
                stamp = max(stamp, f.stat().st_mtime)
        return str(int(stamp))

    @app.get("/")
    async def index() -> HTMLResponse:
        html = (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
        ver = _asset_version()
        html = html.replace("/assets/app.js", f"/assets/app.js?v={ver}")
        html = html.replace("/assets/styles.css", f"/assets/styles.css?v={ver}")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        candidate = _FRONTEND_DIR / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return await index()
