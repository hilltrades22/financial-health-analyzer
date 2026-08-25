from __future__ import annotations

import asyncio
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .history import build_financial_timeline, build_historical_scores, explain_score_trend
from .market_data import compute_valuation, fetch_stock_price
from .normalize import build_company_financials
from .pillars import compute_forge_score
from .quality import compute_financial_quality, compute_piotroski_f_score
from .risk import compute_altman_z_score
from .scoring import score_company
from .sec_client import (
    SecClient,
    SecUnavailableError,
    TickerNotFoundError,
    SEC_USER_AGENT,
)
from .story import build_financial_story

app = FastAPI(title="FORGE Financial Intelligence", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_sec_client = SecClient()

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


async def _analyze_ticker(ticker_key: str) -> dict[str, Any]:
    now = time.time()
    cached = _cache.get(ticker_key)
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

    cf = build_company_financials(ticker_key, cik, name or company_facts.get("entityName", ticker_key), company_facts)
    score = score_company(cf)
    story = build_financial_story(cf.name, cf.ticker, score)

    quality_metrics = compute_financial_quality(company_facts, cf.annual)
    piotroski = compute_piotroski_f_score(company_facts)
    historical_scores = build_historical_scores(company_facts)
    trend_story = explain_score_trend(historical_scores)
    timeline = build_financial_timeline(company_facts)

    # Live market price is best-effort and clearly separated from SEC data.
    price_info = await fetch_stock_price(ticker_key)
    total_debt = None
    cash_val = None
    if cf.quarterly.short_term_debt.available or cf.quarterly.long_term_debt.available:
        total_debt = (cf.quarterly.short_term_debt.value or 0) + (cf.quarterly.long_term_debt.value or 0)
    if cf.quarterly.cash_and_equivalents.available:
        cash_val = cf.quarterly.cash_and_equivalents.value
    valuation = compute_valuation(company_facts, price_info, total_debt, cash_val)

    market_cap_val = valuation.get("market_cap", {}).get("value") if valuation.get("market_cap", {}).get("available") else None
    altman = compute_altman_z_score(company_facts, market_cap_val)

    forge = compute_forge_score(score["overall_score"], piotroski, altman, valuation)

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
        "sector": None,
        "industry": submissions.get("sicDescription"),
        "sic_code": submissions.get("sic"),
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
        "score": score,
        "financial_story": story,
        "quality_metrics": quality_metrics,
        "piotroski": piotroski,
        "altman": altman,
        "valuation": valuation,
        "market_price": price_info,
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
        "market_data_source": "Stooq.com delayed market quote, used only for live price / market cap / valuation multiples - clearly separate from SEC data.",
    }

    _cache[ticker_key] = (now, result)
    return result


@app.get("/api/analyze/{ticker}")
async def analyze(ticker: str) -> JSONResponse:
    result = await _analyze_ticker(ticker.strip().upper())
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

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(str(_FRONTEND_DIR / "index.html"))

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        candidate = _FRONTEND_DIR / path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIR / "index.html"))
