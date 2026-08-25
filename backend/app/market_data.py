"""
Optional market-data layer: live stock price, market cap, and valuation
multiples, combined with real SEC-reported share counts and fundamentals.
This is the ONLY part of FORGE that uses non-SEC data sources, and it is
clearly labeled as such everywhere it's surfaced, with an explicit source
and "as of" timestamp attached to every field.

Two independent, keyless market-data providers are used so a single
provider outage degrades gracefully instead of taking valuation out
entirely:
  1. Yahoo Finance (query1.finance.yahoo.com) - primary. Provides live/last
     price, and is also used for the historical price-history endpoint that
     powers the timeframe selector (3M/6M/YTD/1Y/3Y/5Y/10Y/MAX).
  2. Stooq (stooq.com) - fallback if Yahoo is unreachable.

If neither price feed is reachable, or a required SEC fundamental is
missing, every dependent field is marked unavailable - nothing is
estimated or invented.
"""
from __future__ import annotations

import csv
import io
import time
from typing import Any, Optional

import httpx

from .models import NOT_REPORTED
from .normalize import annual_series, fact_from_entry

STOOQ_URL = "https://stooq.com/q/l/?s={symbol}.us&f=sd2t2ohlcv&h&e=csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_QUOTE_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FORGE-financial-analyzer/1.0)"}

_TIMEOUT = httpx.Timeout(8.0, connect=5.0)

# Historical price ranges the frontend timeframe selector can request, and
# how many days of lookback + what sampling interval each maps to. Longer
# ranges use a coarser interval to keep payloads small - Yahoo's chart API
# supports both.
HISTORY_RANGES: dict[str, dict[str, Any]] = {
    "3M": {"range": "3mo", "interval": "1d"},
    "6M": {"range": "6mo", "interval": "1d"},
    "YTD": {"range": "ytd", "interval": "1d"},
    "1Y": {"range": "1y", "interval": "1d"},
    "3Y": {"range": "3y", "interval": "1wk"},
    "5Y": {"range": "5y", "interval": "1wk"},
    "10Y": {"range": "10y", "interval": "1mo"},
    "MAX": {"range": "max", "interval": "1mo"},
}


async def _fetch_yahoo_chart(ticker: str, range_: str, interval: str) -> dict[str, Any]:
    symbol = ticker.strip().upper()
    params = {"range": range_, "interval": interval, "includePrePost": "false"}
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=YAHOO_HEADERS) as client:
        resp = await client.get(YAHOO_CHART_URL.format(symbol=symbol), params=params)
    resp.raise_for_status()
    data = resp.json()
    result = (data.get("chart") or {}).get("result")
    if not result:
        raise ValueError("Yahoo Finance returned no chart result for this symbol")
    return result[0]


async def fetch_stock_price(ticker: str) -> dict[str, Any]:
    """Best-effort live/last quote. Tries Yahoo Finance first, falls back to
    Stooq. Never raises - returns available=False on total failure so the
    rest of the app degrades gracefully."""
    try:
        chart = await _fetch_yahoo_chart(ticker, "5d", "1d")
        meta = chart.get("meta", {})
        price = meta.get("regularMarketPrice")
        if price is None:
            raise ValueError("No regularMarketPrice in Yahoo response")
        ts = meta.get("regularMarketTime")
        as_of = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts)) if ts else None
        return {
            "available": True,
            "price": float(price),
            "date": as_of,
            "previous_close": meta.get("chartPreviousClose"),
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "source": "Yahoo Finance (query1.finance.yahoo.com) - delayed market quote, not SEC data",
            "source_short": "Yahoo Finance",
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
    except Exception as yahoo_exc:  # noqa: BLE001 - best-effort external feed
        pass

    symbol = ticker.strip().lower()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(STOOQ_URL.format(symbol=symbol))
        if resp.status_code != 200:
            return {"available": False, "reason": f"Yahoo Finance and Stooq (HTTP {resp.status_code}) were both unreachable"}
        reader = csv.DictReader(io.StringIO(resp.text))
        row = next(reader, None)
        if not row or row.get("Close") in (None, "N/D", ""):
            return {"available": False, "reason": "No quote returned for this symbol from either Yahoo Finance or Stooq"}
        price = float(row["Close"])
        return {
            "available": True,
            "price": price,
            "date": row.get("Date"),
            "open": float(row["Open"]) if row.get("Open") not in (None, "N/D", "") else None,
            "high": float(row["High"]) if row.get("High") not in (None, "N/D", "") else None,
            "low": float(row["Low"]) if row.get("Low") not in (None, "N/D", "") else None,
            "volume": int(float(row["Volume"])) if row.get("Volume") not in (None, "N/D", "") else None,
            "source": "Stooq (stooq.com) delayed market quote - not SEC data (Yahoo Finance fallback path)",
            "source_short": "Stooq",
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
    except Exception as exc:  # noqa: BLE001 - best-effort external feed
        return {"available": False, "reason": f"Could not reach any price feed (Yahoo Finance or Stooq): {exc}"}


async def fetch_forward_pe(ticker: str) -> Optional[dict[str, Any]]:
    """Best-effort forward P/E from Yahoo's defaultKeyStatistics module.
    Returns None (not an error dict) on any failure - this is a bonus field
    that quietly omits itself rather than blocking the rest of valuation."""
    symbol = ticker.strip().upper()
    try:
        params = {"modules": "defaultKeyStatistics"}
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=YAHOO_HEADERS) as client:
            resp = await client.get(YAHOO_QUOTE_SUMMARY_URL.format(symbol=symbol), params=params)
        if resp.status_code != 200:
            return None
        data = resp.json()
        result = (((data.get("quoteSummary") or {}).get("result")) or [None])[0]
        if not result:
            return None
        fpe = (result.get("defaultKeyStatistics") or {}).get("forwardPE", {})
        raw = fpe.get("raw")
        if raw is None:
            return None
        return {"available": True, "value": round(raw, 2), "display": f"{raw:.2f}x",
                "source": "Yahoo Finance (query1.finance.yahoo.com) - analyst-consensus forward estimate, not SEC data"}
    except Exception:  # noqa: BLE001 - bonus field, fails silently
        return None


async def fetch_price_history(ticker: str, range_key: str) -> dict[str, Any]:
    """Real historical daily/weekly/monthly closing prices for the
    timeframe selector (3M/6M/YTD/1Y/3Y/5Y/10Y/MAX). Never fabricates a
    point - returns available=False with a reason if the feed can't be
    reached, rather than a flat/demo line."""
    spec = HISTORY_RANGES.get(range_key.upper())
    if spec is None:
        return {"available": False, "reason": f"Unknown range '{range_key}'"}
    try:
        chart = await _fetch_yahoo_chart(ticker, spec["range"], spec["interval"])
        timestamps = chart.get("timestamp") or []
        quote = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        volumes = quote.get("volume") or []
        points = []
        for i, ts in enumerate(timestamps):
            c = closes[i] if i < len(closes) else None
            if c is None:
                continue
            points.append({
                "date": time.strftime("%Y-%m-%d", time.gmtime(ts)),
                "open": opens[i] if i < len(opens) else None,
                "high": highs[i] if i < len(highs) else None,
                "low": lows[i] if i < len(lows) else None,
                "close": c,
                "volume": volumes[i] if i < len(volumes) else None,
            })
        if not points:
            return {"available": False, "reason": "Yahoo Finance returned no price points for this range"}
        return {
            "available": True,
            "range": range_key.upper(),
            "interval": spec["interval"],
            "points": points,
            "source": "Yahoo Finance (query1.finance.yahoo.com) - historical daily close, not SEC data",
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
    except Exception as exc:  # noqa: BLE001 - best-effort external feed
        return {"available": False, "reason": f"Could not reach Yahoo Finance for price history: {exc}"}


def _latest_annual(company_facts: dict[str, Any], tags: list[str], duration: bool):
    series = annual_series(company_facts, tags, duration=duration)
    return fact_from_entry(series[0]) if series else None


def compute_valuation(company_facts: dict[str, Any], price_info: dict[str, Any],
                       total_debt: Optional[float], cash: Optional[float],
                       forward_pe: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Combines a live price (if available) with real SEC fundamentals to
    compute market cap and valuation multiples. Every field is independently
    marked available/unavailable, with its own source and as-of date."""
    out: dict[str, Any] = {
        "price_source": price_info.get("source") if price_info.get("available") else None,
        "price_source_short": price_info.get("source_short") if price_info.get("available") else None,
        "last_updated": price_info.get("fetched_at") if price_info.get("available") else None,
    }

    if forward_pe:
        out["forward_pe_ratio"] = forward_pe
    else:
        out["forward_pe_ratio"] = {"available": False, "display": NOT_REPORTED}

    if not price_info.get("available"):
        out["market_cap"] = {"available": False, "display": NOT_REPORTED}
        out["note"] = f"Live price unavailable ({price_info.get('reason', 'no reason given')}), so market cap and price-based multiples cannot be computed."
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
    out["market_cap"] = {"available": True, "value": market_cap, "display": fmt(market_cap),
                          "source": "price (see price_source above) x SEC-reported shares outstanding"}
    out["shares_outstanding"] = {"available": True, "value": shares.value, "as_of": shares.period_end,
                                  "source": f"SEC EDGAR XBRL: CommonStockSharesOutstanding ({shares.form}, period end {shares.period_end})"}
    out["price"] = {"value": price, "as_of": price_info.get("date"), "source": out["price_source"]}

    if net_income and net_income.available and net_income.value:
        pe = market_cap / net_income.value
        out["pe_ratio"] = {"available": True, "value": round(pe, 2), "display": f"{pe:.2f}x",
                            "source": f"Market cap / SEC net income (FY{net_income.fiscal_year}, filed {net_income.filed})"}
    else:
        out["pe_ratio"] = {"available": False, "display": NOT_REPORTED}

    if equity and equity.available and equity.value:
        pb = market_cap / equity.value
        out["pb_ratio"] = {"available": True, "value": round(pb, 2), "display": f"{pb:.2f}x",
                            "source": f"Market cap / SEC stockholders' equity (period end {equity.period_end})"}
    else:
        out["pb_ratio"] = {"available": False, "display": NOT_REPORTED}

    if revenue and revenue.available and revenue.value:
        ps = market_cap / revenue.value
        out["ps_ratio"] = {"available": True, "value": round(ps, 2), "display": f"{ps:.2f}x",
                            "source": f"Market cap / SEC revenue (FY{revenue.fiscal_year}, filed {revenue.filed})"}
    else:
        out["ps_ratio"] = {"available": False, "display": NOT_REPORTED}

    if total_debt is not None and cash is not None:
        ev = market_cap + total_debt - cash
        out["enterprise_value"] = {"available": True, "value": ev, "display": fmt(ev),
                                    "source": "Market cap + SEC total debt - SEC cash & equivalents"}
        if op_income and op_income.available:
            ebitda = op_income.value + (dep_amort.value if dep_amort and dep_amort.available else 0)
            ebitda_note = "EBIT + D&A" if dep_amort and dep_amort.available else "operating income only (D&A not separately reported - approximate EBITDA)"
            if ebitda:
                out["ev_ebitda"] = {"available": True, "value": round(ev / ebitda, 2), "display": f"{(ev/ebitda):.2f}x", "note": ebitda_note,
                                     "source": f"Enterprise value / EBITDA ({ebitda_note}, SEC FY{op_income.fiscal_year})"}
            else:
                out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        else:
            out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        if revenue and revenue.available and revenue.value:
            out["ev_sales"] = {"available": True, "value": round(ev / revenue.value, 2), "display": f"{(ev/revenue.value):.2f}x",
                                "source": f"Enterprise value / SEC revenue (FY{revenue.fiscal_year})"}
        else:
            out["ev_sales"] = {"available": False, "display": NOT_REPORTED}
    else:
        out["enterprise_value"] = {"available": False, "display": NOT_REPORTED}
        out["ev_ebitda"] = {"available": False, "display": NOT_REPORTED}
        out["ev_sales"] = {"available": False, "display": NOT_REPORTED}

    return out
