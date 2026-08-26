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
from .normalize import annual_series, fact_from_entry, latest_shares_outstanding, annual_shares_outstanding_series

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


async def fetch_key_statistics(ticker: str) -> dict[str, Any]:
    """Best-effort bundle from Yahoo's defaultKeyStatistics module: forward
    P/E, shares outstanding, dividend yield. Returns {} (not an error) on
    any failure - these are all bonus/fallback fields that quietly omit
    themselves rather than blocking the rest of valuation."""
    symbol = ticker.strip().upper()
    try:
        params = {"modules": "defaultKeyStatistics,summaryDetail"}
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers=YAHOO_HEADERS) as client:
            resp = await client.get(YAHOO_QUOTE_SUMMARY_URL.format(symbol=symbol), params=params)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        result = (((data.get("quoteSummary") or {}).get("result")) or [None])[0]
        if not result:
            return {}
        out: dict[str, Any] = {}
        dks = result.get("defaultKeyStatistics") or {}
        summary = result.get("summaryDetail") or {}
        fpe_raw = dks.get("forwardPE", {}).get("raw")
        if fpe_raw is not None:
            out["forward_pe"] = {"available": True, "value": round(fpe_raw, 2), "display": f"{fpe_raw:.2f}x",
                                  "source": "Yahoo Finance (query1.finance.yahoo.com) - analyst-consensus forward estimate, not SEC data"}
        shares_raw = dks.get("sharesOutstanding", {}).get("raw")
        if shares_raw:
            out["shares_outstanding"] = float(shares_raw)
        div_raw = summary.get("dividendYield", {}).get("raw")
        if div_raw is not None:
            out["dividend_yield"] = {"available": True, "value": round(div_raw * 100, 2), "display": f"{div_raw*100:.2f}%",
                                      "source": "Yahoo Finance (query1.finance.yahoo.com)"}
        return out
    except Exception:  # noqa: BLE001 - bonus fields, fail silently
        return {}


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
                       key_stats: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Combines a live price (if available) with real SEC fundamentals to
    compute market cap and valuation multiples. Every field is independently
    marked available/unavailable, with its own source and as-of date."""
    key_stats = key_stats or {}
    out: dict[str, Any] = {
        "price_source": price_info.get("source") if price_info.get("available") else None,
        "price_source_short": price_info.get("source_short") if price_info.get("available") else None,
        "last_updated": price_info.get("fetched_at") if price_info.get("available") else None,
    }

    out["forward_pe_ratio"] = key_stats.get("forward_pe") or {"available": False, "display": NOT_REPORTED}
    out["dividend_yield"] = key_stats.get("dividend_yield") or {"available": False, "display": NOT_REPORTED}

    if not price_info.get("available"):
        out["market_cap"] = {"available": False, "display": NOT_REPORTED}
        out["note"] = f"Live price unavailable ({price_info.get('reason', 'no reason given')}), so market cap and price-based multiples cannot be computed."
        return out

    price = price_info["price"]
    shares = latest_shares_outstanding(company_facts)
    shares_source_note = f"SEC EDGAR XBRL: {shares.concept} ({shares.form}, period end {shares.period_end})" if shares.available else None
    if not shares.available and key_stats.get("shares_outstanding"):
        # Fall back to Yahoo Finance's reported share count only when SEC
        # XBRL has no shares-outstanding tag at all - never used to override
        # a real SEC figure, only to fill a genuine gap.
        from .models import FactValue
        shares = FactValue(value=key_stats["shares_outstanding"], available=True)
        shares_source_note = "Yahoo Finance (query1.finance.yahoo.com) - SEC XBRL had no shares-outstanding tag for this filer"
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
                                  "source": shares_source_note or "Yahoo Finance"}
    out["price"] = {"value": price, "as_of": price_info.get("date"), "source": out["price_source"]}

    if net_income and net_income.available and net_income.value:
        pe = market_cap / net_income.value
        out["pe_ratio"] = {"available": True, "value": round(pe, 2), "display": f"{pe:.2f}x",
                            "source": f"Market cap / SEC net income (FY{net_income.fiscal_year}, filed {net_income.filed})"}
        eps = net_income.value / shares.value
        out["eps"] = {"available": True, "value": round(eps, 2), "display": f"${eps:,.2f}",
                      "source": f"SEC net income (FY{net_income.fiscal_year}) / shares outstanding"}
    else:
        out["pe_ratio"] = {"available": False, "display": NOT_REPORTED}
        out["eps"] = {"available": False, "display": NOT_REPORTED}

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


def _nearest_price(points: list[dict[str, Any]], target_date: str) -> Optional[float]:
    """Closest historical close to a given date (fiscal year end), tolerating
    up to ~45 days of drift since exchanges are closed on the exact date."""
    import datetime as _dt
    try:
        target = _dt.date.fromisoformat(target_date)
    except (ValueError, TypeError):
        return None
    best = None
    best_diff = None
    for p in points:
        try:
            d = _dt.date.fromisoformat(p["date"])
        except (ValueError, TypeError):
            continue
        diff = abs((d - target).days)
        if diff > 45:
            continue
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = p["close"]
    return best


async def compute_valuation_history(ticker: str, company_facts: dict[str, Any],
                                     total_debt: Optional[float], cash: Optional[float]) -> dict[str, Any]:
    """Real, non-fabricated historical P/E, P/B, P/S and EV/EBITDA - one
    data point per fiscal year end, built from actual historical closing
    prices (Yahoo Finance) combined with the shares/earnings/equity/revenue
    SEC reported for that same fiscal year (not today's share count applied
    to the past). Returns available=False with a reason if either the price
    history or the SEC series can't be resolved - never estimates a point."""
    price_hist = await fetch_price_history(ticker, "MAX")
    if not price_hist.get("available"):
        return {"available": False, "reason": f"No historical price data: {price_hist.get('reason')}"}
    points = price_hist["points"]

    shares_series = annual_shares_outstanding_series(company_facts)
    revenue_series = annual_series(company_facts, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                                     "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"], duration=True)
    net_income_series = annual_series(company_facts, ["NetIncomeLoss", "ProfitLoss"], duration=True)
    equity_series = annual_series(company_facts, ["StockholdersEquity"], duration=False)
    op_income_series = annual_series(company_facts, ["OperatingIncomeLoss"], duration=True)

    if not shares_series:
        return {"available": False, "reason": "SEC XBRL has no annual shares-outstanding series for this filer"}

    by_year_end = {sf.period_end: sf for sf in shares_series if sf.period_end}
    rev_by_end = {e.get("end"): fact_from_entry(e) for e in revenue_series}
    ni_by_end = {e.get("end"): fact_from_entry(e) for e in net_income_series}
    eq_by_end = {e.get("end"): fact_from_entry(e) for e in equity_series}
    oi_by_end = {e.get("end"): fact_from_entry(e) for e in op_income_series}

    rows = []
    for period_end, shares_fact in sorted(by_year_end.items(), reverse=True):
        if not shares_fact.available or not shares_fact.value:
            continue
        price = _nearest_price(points, period_end)
        if price is None:
            continue
        market_cap = price * shares_fact.value
        row: dict[str, Any] = {"period_end": period_end, "price": round(price, 2), "market_cap": market_cap}
        ni = ni_by_end.get(period_end)
        if ni and ni.available and ni.value:
            row["pe"] = round(market_cap / ni.value, 2)
        eq = eq_by_end.get(period_end)
        if eq and eq.available and eq.value:
            row["pb"] = round(market_cap / eq.value, 2)
        rev = rev_by_end.get(period_end)
        if rev and rev.available and rev.value:
            row["ps"] = round(market_cap / rev.value, 2)
        if total_debt is not None and cash is not None:
            ev = market_cap + total_debt - cash
            oi = oi_by_end.get(period_end)
            if oi and oi.available and oi.value:
                row["ev_ebitda"] = round(ev / oi.value, 2)
        rows.append(row)

    if not rows:
        return {"available": False, "reason": "Could not align any fiscal year end with a historical closing price"}

    def _stats(field: str) -> dict[str, Any]:
        vals_5y = [r[field] for r in rows[:5] if field in r]
        vals_10y = [r[field] for r in rows[:10] if field in r]
        current = rows[0].get(field)
        avg5 = sum(vals_5y) / len(vals_5y) if vals_5y else None
        avg10 = sum(vals_10y) / len(vals_10y) if vals_10y else None
        out: dict[str, Any] = {
            "available": current is not None,
            "current": current,
            "avg_5y": round(avg5, 2) if avg5 else None,
            "avg_10y": round(avg10, 2) if avg10 else None,
        }
        if current is not None and avg5:
            out["premium_discount_vs_5y_pct"] = round((current / avg5 - 1) * 100, 1)
        if current is not None and avg10:
            out["premium_discount_vs_10y_pct"] = round((current / avg10 - 1) * 100, 1)
        return out

    return {
        "available": True,
        "years_of_data": len(rows),
        "series": rows,
        "pe": _stats("pe"),
        "pb": _stats("pb"),
        "ps": _stats("ps"),
        "ev_ebitda": _stats("ev_ebitda"),
        "source": "Yahoo Finance historical closes x SEC EDGAR shares/earnings/equity/revenue for each matching fiscal year end",
        "note": "Each year's multiple uses that year's own SEC-reported fundamentals and shares count, not today's - not a fabricated trend line.",
    }


def compute_bull_base_bear(valuation: dict[str, Any], valuation_history: dict[str, Any]) -> dict[str, Any]:
    """Bull/Base/Bear price scenarios built only from real, already-computed
    numbers: today's EPS applied to the low/average/high of this company's
    own historical P/E range. No invented growth rate or multiple - if the
    inputs aren't available, the case is marked unavailable rather than
    guessed."""
    eps = valuation.get("eps", {})
    pe_hist = valuation_history.get("pe", {}) if valuation_history.get("available") else {}
    if not eps.get("available") or not pe_hist.get("available"):
        return {"available": False,
                "reason": "Requires both current EPS and a resolvable historical P/E series - one or both are unavailable for this company."}

    eps_val = eps["value"]
    series = valuation_history.get("series", [])
    # Negative P/E values (from a loss-making fiscal year) aren't a
    # meaningful valuation floor - excluding them keeps the Bear case from
    # producing a nonsensical negative price target off a year the company
    # simply lost money, rather than being cheaply valued.
    pe_values = [r["pe"] for r in series if "pe" in r and r["pe"] > 0]
    if not pe_values:
        return {"available": False, "reason": "No positive historical P/E data points available (company had losses in every matched fiscal year)"}

    low_pe = min(pe_values)
    high_pe = max(pe_values)
    positive_avg = sum(pe_values) / len(pe_values)
    avg_pe = pe_hist.get("avg_5y") if (pe_hist.get("avg_5y") and pe_hist["avg_5y"] > 0) else \
             pe_hist.get("avg_10y") if (pe_hist.get("avg_10y") and pe_hist["avg_10y"] > 0) else positive_avg
    current_price = valuation.get("price", {}).get("value")

    def target(pe_mult):
        return round(eps_val * pe_mult, 2) if pe_mult else None

    return {
        "available": True,
        "current_price": current_price,
        "bear": {"price_target": target(low_pe), "pe_used": round(low_pe, 2),
                 "assumption": f"EPS (${eps_val:,.2f}) at this company's own lowest historical P/E ({low_pe:.1f}x) over the last {len(pe_values)} profitable fiscal years."},
        "base": {"price_target": target(avg_pe), "pe_used": round(avg_pe, 2),
                  "assumption": f"EPS (${eps_val:,.2f}) at this company's own average historical P/E ({avg_pe:.1f}x)."},
        "bull": {"price_target": target(high_pe), "pe_used": round(high_pe, 2),
                 "assumption": f"EPS (${eps_val:,.2f}) at this company's own highest historical P/E ({high_pe:.1f}x) over the last {len(pe_values)} profitable fiscal years."},
        "methodology": "All three cases apply the current trailing EPS to this specific company's own historical P/E range (low/average/high) - no assumed growth rate or peer multiple is invented.",
    }
