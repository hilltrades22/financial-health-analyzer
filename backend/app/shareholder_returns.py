"""
Dividends and buybacks, from SEC XBRL only.

The question this answers is the one that actually matters to a shareholder:
is the company returning cash, and is the share count genuinely falling? A
company can spend heavily on buybacks and still dilute its owners if it
issues at least as much stock to employees, so gross repurchase spending and
the net change in shares outstanding are reported separately rather than
collapsed into one flattering number.

Everything here is SEC-reported. Dividend YIELD is not computed here because
it requires a live market price - that belongs to the market-data provider
layer and is labelled as such.
"""
from __future__ import annotations

from typing import Any, Optional

from . import normalize as N


def _fmt(v: Optional[float], currency: str = "USD") -> Optional[str]:
    if v is None:
        return None
    sign = "-" if v < 0 else ""
    a = abs(v)
    sym = "$" if currency == "USD" else f"{currency} "
    if a >= 1_000_000_000:
        return f"{sign}{sym}{a/1_000_000_000:,.2f}B"
    if a >= 1_000_000:
        return f"{sign}{sym}{a/1_000_000:,.2f}M"
    return f"{sign}{sym}{a:,.0f}"


def _series(company_facts: dict[str, Any], tags: list[str], duration: bool, limit: int = 6):
    return [N.fact_from_entry(e) for e in N.annual_series(company_facts, tags, duration=duration)[:limit]]


def build_shareholder_returns(company_facts: dict[str, Any]) -> dict[str, Any]:
    currency = N.reporting_currency(company_facts)

    dividends = _series(company_facts, N.DIVIDENDS_PAID_TAGS, duration=True)
    dps = _series(company_facts, N.DIVIDENDS_PER_SHARE_TAGS, duration=True)
    buybacks = _series(company_facts, N.REPURCHASE_TAGS, duration=True)
    net_income = _series(company_facts, N.NET_INCOME_TAGS, duration=True)
    ocf = _series(company_facts, N.OPERATING_CASH_FLOW_TAGS, duration=True)
    shares = N.annual_shares_outstanding_series(company_facts)

    def rows(facts):
        return [{"period_end": f.period_end, "fiscal_year": f.fiscal_year, "value": f.value,
                 "display": _fmt(f.value, currency), "concept": f.concept, "form": f.form,
                 "filed": f.filed, "unit": f.unit}
                for f in facts if f.available]

    div_rows, dps_rows, buyback_rows = rows(dividends), rows(dps), rows(buybacks)
    latest_div = dividends[0] if dividends and dividends[0].available else None
    latest_buyback = buybacks[0] if buybacks and buybacks[0].available else None
    latest_ni = net_income[0] if net_income and net_income[0].available else None
    latest_ocf = ocf[0] if ocf and ocf[0].available else None

    pays_dividend = bool(latest_div and latest_div.value)
    dividend: dict[str, Any] = {
        "pays_dividend": pays_dividend,
        "history": div_rows,
        "per_share_history": [{**r, "display": f"{r['value']:.2f}" if r["value"] is not None else None}
                              for r in dps_rows],
        "source": "SEC EDGAR XBRL Company Facts (annual cash dividends paid / declared per share).",
        "yield_note": (
            "Dividend yield needs a live share price, which comes from a market-data provider rather than "
            "SEC filings, and is reported separately from these SEC-reported amounts."
        ),
    }
    if not pays_dividend:
        dividend["reason"] = (
            "This company reports no cash dividends paid in its SEC filings. Not paying a dividend is a "
            "capital-allocation choice, not a weakness, and it is not scored against the company."
        )
    else:
        dividend["latest"] = {"value": latest_div.value, "display": _fmt(latest_div.value, currency),
                              "period_end": latest_div.period_end, "concept": latest_div.concept,
                              "form": latest_div.form, "filed": latest_div.filed}
        # Payout ratios: only where the denominator is genuinely reported.
        if latest_ni and latest_ni.value and latest_ni.value > 0:
            dividend["payout_ratio_earnings"] = {
                "value": round(abs(latest_div.value) / latest_ni.value, 3),
                "display": f"{abs(latest_div.value)/latest_ni.value*100:.0f}%",
                "basis": f"Dividends paid / net income (FY{latest_ni.fiscal_year})",
            }
        if latest_ocf and latest_ocf.value and latest_ocf.value > 0:
            dividend["payout_ratio_cash_flow"] = {
                "value": round(abs(latest_div.value) / latest_ocf.value, 3),
                "display": f"{abs(latest_div.value)/latest_ocf.value*100:.0f}%",
                "basis": f"Dividends paid / operating cash flow (FY{latest_ocf.fiscal_year})",
            }
        if len(div_rows) >= 2:
            newest, oldest = div_rows[0]["value"], div_rows[-1]["value"]
            years = len(div_rows) - 1
            if oldest and newest and oldest > 0 and years > 0:
                cagr = (abs(newest) / abs(oldest)) ** (1 / years) - 1
                dividend["growth"] = {
                    "value": round(cagr, 4), "display": f"{cagr*100:+.1f}% per year",
                    "basis": f"Compound annual change in total dividends paid across {years + 1} reported years "
                             f"({div_rows[-1]['period_end']} to {div_rows[0]['period_end']}).",
                }

    # Buybacks vs actual dilution.
    buyback: dict[str, Any] = {
        "repurchases_reported": bool(latest_buyback and latest_buyback.value),
        "history": buyback_rows,
        "source": "SEC EDGAR XBRL Company Facts (payments for repurchase of common stock).",
    }
    if latest_buyback and latest_buyback.value:
        buyback["latest"] = {"value": latest_buyback.value, "display": _fmt(abs(latest_buyback.value), currency),
                             "period_end": latest_buyback.period_end, "concept": latest_buyback.concept,
                             "form": latest_buyback.form, "filed": latest_buyback.filed}
    else:
        buyback["reason"] = "This company reports no share repurchases in its SEC filings."

    share_rows = [{"period_end": f.period_end, "shares": f.value, "concept": f.concept}
                  for f in shares if f.available and f.value]
    share_rows.sort(key=lambda r: r["period_end"] or "")
    dilution: dict[str, Any] = {"history": share_rows}
    if len(share_rows) >= 2:
        first, last = share_rows[0], share_rows[-1]
        change = (last["shares"] - first["shares"]) / first["shares"]
        years = len(share_rows) - 1
        reducing = change < -0.005
        diluting = change > 0.005
        dilution.update({
            "available": True,
            "change_pct": round(change * 100, 2),
            "display": f"{change*100:+.2f}%",
            "period": f"{first['period_end']} to {last['period_end']} ({years} year{'s' if years != 1 else ''})",
            "direction": "reducing" if reducing else "diluting" if diluting else "flat",
            "explanation": (
                (f"Shares outstanding fell {abs(change)*100:.1f}% over this period, so buybacks more than offset "
                 "any stock issued to employees - each remaining share represents a larger claim on the business.")
                if reducing else
                (f"Shares outstanding rose {change*100:.1f}% over this period. Even where the company also "
                 "repurchased stock, it issued more than it bought back, so existing shareholders were diluted.")
                if diluting else
                "Shares outstanding were essentially unchanged over this period, so repurchases roughly offset "
                "shares issued."
            ),
        })
    else:
        dilution.update({
            "available": False,
            "reason": "Unavailable - fewer than two annual share-count datapoints are reported by this filer.",
        })

    total_returned = None
    if (latest_div and latest_div.value) or (latest_buyback and latest_buyback.value):
        total_returned = abs(latest_div.value if latest_div and latest_div.value else 0) + \
                         abs(latest_buyback.value if latest_buyback and latest_buyback.value else 0)

    return {
        "currency": currency,
        "dividend": dividend,
        "buyback": buyback,
        "share_count_trend": dilution,
        "total_returned_latest_year": {
            "value": total_returned,
            "display": _fmt(total_returned, currency) if total_returned else None,
            "basis": "Dividends paid + share repurchases in the latest reported fiscal year.",
        },
        "note": (
            "All figures are SEC-reported amounts. Gross repurchase spending and the net change in shares "
            "outstanding are shown separately because a company can buy back stock and still end up with more "
            "shares outstanding if it issues more to employees than it retires."
        ),
    }
