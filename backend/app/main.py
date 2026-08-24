from __future__ import annotations

import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .normalize import build_company_financials
from .scoring import score_company
from .sec_client import (
    SecClient,
    SecUnavailableError,
    TickerNotFoundError,
    SEC_USER_AGENT,
)
from .story import build_financial_story

app = FastAPI(title="Financial Health Analyzer", version="1.0.0")

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
    d = asdict(fv)
    return d


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "sec_user_agent_configured": bool(SEC_USER_AGENT),
    }


@app.get("/api/analyze/{ticker}")
async def analyze(ticker: str) -> JSONResponse:
    ticker_key = ticker.strip().upper()
    now = time.time()
    cached = _cache.get(ticker_key)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return JSONResponse(cached[1])

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

    result = {
        "ticker": cf.ticker,
        "cik": cf.cik,
        "company_name": cf.name,
        "sec_edgar_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
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
        "score": score,
        "financial_story": story,
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
    }

    _cache[ticker_key] = (now, result)
    return JSONResponse(result)


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
