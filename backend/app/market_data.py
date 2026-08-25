"""
Optional market-data layer: live stock price from Stooq (free, no API key)
combined with real SEC-reported share counts and fundamentals to compute
market cap and valuation multiples. This is the ONLY module that uses a
non-SEC data source, and it is clearly labeled as such everywhere it's
surfaced. If the price feed fails or a required fundamental is missing,
every dependent field is marked unavailable - nothing is estimated or
invented.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

import httpx

from .models import NOT_REPORTED
from .normalize import annual_series, fact_from_entry

STOOQ_URL = "https://stooq.com/q/l/?s={symbol}.us&f=sd2t2ohlcv&h&e=csv"

_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


async def fetch_stock_price(ticker: str) -> dict[str, Any]:
    """Best-effort live quote from Stooq. Never raises - returns available=False
    on any failure so the rest of the app degrades gracefully."""
    symbol = ticker.strip().lower()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(STOOQ_URL.format(symbol=symbol))
        if resp.status_code != 200:
            return {"available": False, "reason": f"Stooq returned HTTP {resp.status_code}"}
        reader = csv.DictReader(io.StringIO(resp.text))
        row = next(reader, None)
        if not row or row.get("Close") in (None, "N/D", ""):
            return {"available": False, "reason": "No quote returned for this symbol"}
        price = float(row["Close"])
        return {
            "available": True,
            "price": price,
            "date": row.get("Date"),
            "open": float(row["Open"]) if row.get("Open") not in (None, "N/D", "") else None,
            "high": float(row["High"]) if row.get("High") not in (None, "N/D", "") else None,
            "low": float(row["Low"]) if row.get("Low") not in (None, "N/D", "") else None,
            "volume": int(float(row["Volume"])) if row.get("Volume") not in (None, "N/D", "") else None,
            "source": "Stooq (stooq.com) delayed market quote - not SEC data",
        }
    except Exception as exc:  # noqa: BLE001 - best-effort external feed
        return {"available": False, "reason": f"Could not reach price feed: {exc}"}


def _latest_annual(company_facts: dict[str, Any], tags: list[str], duration: bool):
    series = annual_series(company_facts, tags, duration=duration)
    return fact_from_entry(series[0]) if series else None


def compute_valuation(company_facts: dict[str, Any], price_info: dict[str, Any],
                       total_debt: Optional[float], cash: Optional[float]) -> dict[str, Any]:
    """Combines a live price (if available) with real SEC fundamentals to
    compute market cap and valuation multiples. Every field is independently
    marked available/unavailable."""
    out: dict[str, Any] = {"price_source": price_info.get("source") if price_info.get("available") else None}

    if not price_info.get("available"):
        out["market_cap"] = {"available": False, "display": NOT_REPORTED}
        out["note"] = "Live price unavailable, so market cap and price-based multiples cannot be computed."
        return out

    price = price_info["price"]
    shares = _latest_annual(company_facts, ["CommonStockSharesOutstanding"], duration=False)
    equity = _latest_annual(company_facts, ["StockholdersEquity"], duration=False)
    revenue = _latest_annual(company_facts, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                               "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], duration=True)
    net_income = _latest_annual(company_facts, ["NetIncomeLoss", "ProfitLoss"], duration=True)
    op_income = _latest_annual(company_facts, ["OperatingIncomeLoss"], duration=True)
    dep_amort = _latest_annual(company_facts, ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet"], duration=True)

    def fmt(v):
        if v is None:
            return NOT_REPORTED
        sign = "-" if v < 0 else ""
        a = abs(v)
        if a >= 1_000_000_000:
            return f"{sign}${a/1_000_000_000:,.2f}B"
        if a >= 1_000_000:
            return f"{sign}${a/1_000_000:,.2f}M"
        return f"{sign}${a:,.0f}"

    if shares is None or not shares.available or not shares.value:
        out["market_cap"] = {"available": False, "display": NOT_REPORTED}
        out["note"] = "Shares outstanding not found in SEC XBRL data, so market cap cannot be computed."
        return out

    market_cap = price * shares.value
    out["market_cap"] = {"available": True, "value": market_cap, "display": fmt(market_cap)}
    out["shares_outstanding"] = {"available": True, "value": shares.value, "as_of": shares.period_end}
    out["price"] = {"value": price, "as_of": price_info.get("date")}

    if net_income and net_income.available and net_income.value:
        pe = market_cap / net_income.value
        out["pe_ratio"] = {"available": True, "value": round(pe, 2), "display": f"{pe:.2f}x"}
    else:
        out["pe_ratio"] = {"available": False, "display": NOT_REPORTED}

    if equity and equity.available and equity.value:
        pb = market_cap / equity.value
        out["pb_ratio"] = {"available": True, "value": round(pb, 2), "display": f"{pb:.2f}x"}
    else:
        out["pb_ratio"] = {"available": False, "display": NOT_REPORTED}

    if revenue and revenue.available and revenue.value:
        ps = market_cap / revenue.value
        out["ps_ratio"] = {"available": True, "value": round(ps, 2), "display": f"{ps:.2f}x"}
    else:
        out["ps_ratio"] = {"available": False, "display": NOT_REPORTED}

    if total_debt is not None and cash is not None:
        ev = market_cap + total_debt - cash
        out["enterprise_value"] = {"available": True, "value": ev, "display": fmt(ev)}
        if op_income and op_income.available:
            ebitda = op_income.value + (dep_amort.value if dep_amort and dep_amort.available else 0)
            ebitda_note = "EBIT + D&A" if dep_amort and dep_amort.available else "operating income only (D&A not separately reported - approximate EBITDA)"
            if ebitda:
                out["ev_ebitda"] = {"available": True, "value": round(ev / ebitda, 2), "display": f"{(ev/ebitda):.2f}x", "note": ebitda_note}
            else:
                out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        else:
            out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        if revenue and revenue.available and revenue.value:
            out["ev_sales"] = {"available": True, "value": round(ev / revenue.value, 2), "display": f"{(ev/revenue.value):.2f}x"}
        else:
            out["ev_sales"] = {"available": False, "display": NOT_REPORTED}
    else:
        out["enterprise_value"] = {"available": False, "display": NOT_REPORTED}
        out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        out["ev_sales"] = {"available": False, "display": NOT_REPORTED}

    return out
