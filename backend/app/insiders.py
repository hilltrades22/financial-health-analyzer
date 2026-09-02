"""
Insider activity from SEC Form 4 filings.

Form 4 is the primary source: every officer, director and 10% owner must
report their transactions in the issuer's securities. The filings are public
XML at SEC EDGAR, so this is SEC-native data with no third-party provider.

Two things this module deliberately does NOT do:

  * It does not treat insider selling as bearish or insider buying as
    bullish. Executives receive most of their pay in stock and sell on
    pre-arranged schedules for reasons that have nothing to do with their
    view of the company. The transactions are presented as facts.
  * It does not lump transaction types together. An open-market purchase, a
    restricted-stock grant, an option exercise and shares withheld to cover
    tax on a vesting award are entirely different events, and Form 4's
    transaction code distinguishes them. They are reported separately.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from xml.etree import ElementTree as ET

from .sec_client import SecClient, SecUnavailableError, TickerNotFoundError

# Form 4 transaction codes (SEC Form 345 code table). Only the codes that
# actually change an insider's position are interpreted; anything else is
# reported under its own code without being forced into a category.
TRANSACTION_CODES: dict[str, dict[str, str]] = {
    "P": {"label": "Open-market purchase", "category": "open_market_buy",
          "note": "The insider bought shares on the open market with their own money."},
    "S": {"label": "Open-market sale", "category": "open_market_sell",
          "note": "The insider sold shares on the open market. This is frequently a scheduled or "
                  "diversification sale rather than a view on the company."},
    "A": {"label": "Grant or award", "category": "grant",
          "note": "Shares or units granted as compensation, not bought by the insider."},
    "M": {"label": "Option/derivative exercise", "category": "option_exercise",
          "note": "The insider converted options or units into shares; it is not an open-market purchase."},
    "F": {"label": "Shares withheld for tax", "category": "tax_withholding",
          "note": "Shares automatically withheld by the company to cover tax on a vesting award. "
                  "The insider did not choose to sell these."},
    "G": {"label": "Gift", "category": "gift", "note": "Shares given away or received as a gift."},
    "C": {"label": "Conversion", "category": "conversion", "note": "Conversion of a derivative security."},
    "D": {"label": "Disposition to the issuer", "category": "other",
          "note": "Shares returned to or cancelled by the company."},
}

_MAX_FILINGS = 12          # keep the request budget bounded
_FETCH_TIMEOUT_S = 20.0


def _text(node: Optional[ET.Element]) -> Optional[str]:
    """Form 4 wraps most fields as <field><value>x</value></field>."""
    if node is None:
        return None
    value = node.find("value")
    raw = (value.text if value is not None else node.text) or ""
    return raw.strip() or None


def _num(node: Optional[ET.Element]) -> Optional[float]:
    raw = _text(node)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def find_form4_filings(submissions: dict[str, Any], limit: int = _MAX_FILINGS) -> list[dict[str, Any]]:
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    dates = recent.get("filingDate") or []
    out = []
    for i, form in enumerate(forms):
        if form == "4":
            out.append({"accession": accessions[i],
                        "filing_date": dates[i] if i < len(dates) else None})
            if len(out) >= limit:
                break
    return out


def parse_form4(xml_bytes: bytes, filing_date: Optional[str] = None) -> list[dict[str, Any]]:
    """Every reported transaction in one Form 4, with the insider's identity,
    role and the transaction code's real meaning."""
    root = ET.fromstring(xml_bytes)

    owner = root.find("reportingOwner")
    name = role_title = None
    is_director = is_officer = is_ten_pct = False
    if owner is not None:
        ident = owner.find("reportingOwnerId")
        if ident is not None:
            name_el = ident.find("rptOwnerName")
            name = (name_el.text or "").strip() if name_el is not None else None
        rel = owner.find("reportingOwnerRelationship")
        if rel is not None:
            def flag(tag):
                el = rel.find(tag)
                return bool(el is not None and (el.text or "").strip() in ("1", "true"))
            is_director, is_officer, is_ten_pct = flag("isDirector"), flag("isOfficer"), flag("isTenPercentOwner")
            title_el = rel.find("officerTitle")
            role_title = (title_el.text or "").strip() if title_el is not None and title_el.text else None

    roles = [r for r, on in (("Director", is_director), ("Officer", is_officer),
                             ("10% owner", is_ten_pct)) if on]
    role = role_title or (", ".join(roles) if roles else None)

    transactions = []
    for table, derivative in (("nonDerivativeTable", False), ("derivativeTable", True)):
        tbl = root.find(table)
        if tbl is None:
            continue
        tag = "nonDerivativeTransaction" if not derivative else "derivativeTransaction"
        for txn in tbl.findall(tag):
            coding = txn.find("transactionCoding")
            code = None
            if coding is not None:
                code_el = coding.find("transactionCode")
                code = (code_el.text or "").strip() if code_el is not None and code_el.text else None
            amounts = txn.find("transactionAmounts")
            shares = price = None
            acquired_disposed = None
            if amounts is not None:
                shares = _num(amounts.find("transactionShares"))
                price = _num(amounts.find("transactionPricePerShare"))
                acquired_disposed = _text(amounts.find("transactionAcquiredDisposedCode"))
            security = _text(txn.find("securityTitle"))
            date = _text(txn.find("transactionDate"))
            meta = TRANSACTION_CODES.get(code or "", {
                "label": f"Other (code {code})" if code else "Other",
                "category": "other",
                "note": "Reported under a Form 4 transaction code outside the common set.",
            })
            # Value is only real when the filing reports an actual price. A
            # grant at price 0 has no market value and must not be shown as $0
            # of buying or selling.
            value = shares * price if (shares is not None and price) else None
            transactions.append({
                "insider": name,
                "role": role,
                "transaction_date": date,
                "filing_date": filing_date,
                "code": code,
                "type": meta["label"],
                "category": meta["category"],
                "type_note": meta["note"],
                "security": security,
                "is_derivative": derivative,
                "shares": shares,
                "price_per_share": price,
                "value": value,
                "direction": "acquired" if acquired_disposed == "A" else
                             "disposed" if acquired_disposed == "D" else None,
            })
    return transactions


def summarize(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals by transaction category. Open-market activity is kept separate
    from compensation events, which is the distinction that actually matters."""
    def totals(category: str) -> dict[str, Any]:
        rows = [t for t in transactions if t["category"] == category]
        shares = sum(t["shares"] or 0 for t in rows)
        value = sum(t["value"] or 0 for t in rows)
        return {"count": len(rows), "shares": shares or None, "value": value or None}

    buys, sells = totals("open_market_buy"), totals("open_market_sell")
    net_shares = None
    if buys["shares"] is not None or sells["shares"] is not None:
        net_shares = (buys["shares"] or 0) - (sells["shares"] or 0)

    largest = None
    priced = [t for t in transactions if t["value"]]
    if priced:
        largest = max(priced, key=lambda t: t["value"])

    return {
        "open_market_purchases": buys,
        "open_market_sales": sells,
        "net_open_market_shares": net_shares,
        "grants": totals("grant"),
        "option_exercises": totals("option_exercise"),
        "tax_withholding": totals("tax_withholding"),
        "largest_transaction": largest,
        "interpretation_note": (
            "These are reported facts, not signals. Executives are paid largely in stock and commonly sell on "
            "pre-arranged schedules, so sales are not read here as a negative view - and grants, option "
            "exercises and shares withheld for tax are not open-market trades at all, which is why they are "
            "counted separately."
        ),
    }


async def build_insider_activity(sec_client: SecClient, cik: int,
                                 submissions: dict[str, Any]) -> dict[str, Any]:
    """Recent Form 4 activity for one company, parsed from SEC filings."""
    result: dict[str, Any] = {
        "available": False,
        "reason": None,
        "transactions": [],
        "summary": None,
        "filings_examined": 0,
        "source": "SEC EDGAR Form 4 filings (ownership reports filed by officers, directors and 10% owners).",
    }
    filings = find_form4_filings(submissions)
    if not filings:
        result["reason"] = "Unavailable - no Form 4 insider filings found for this company on SEC EDGAR."
        return result

    transactions: list[dict[str, Any]] = []
    examined = 0
    for filing in filings:
        accession = (filing.get("accession") or "").replace("-", "")
        if not accession:
            continue
        try:
            index_json = await sec_client.get_filing_index(cik, accession)
            items = (index_json.get("directory") or {}).get("item") or []
            # The raw ownership XML, not the XSL-rendered viewer document.
            name = next((i.get("name") for i in items
                         if str(i.get("name", "")).lower().endswith(".xml")
                         and "xsl" not in str(i.get("name", "")).lower()), None)
            if not name:
                continue
            raw = await sec_client.get_filing_file(cik, accession, name)
            parsed = await asyncio.to_thread(parse_form4, raw, filing.get("filing_date"))
            transactions.extend(parsed)
            examined += 1
        except (SecUnavailableError, TickerNotFoundError, ET.ParseError):
            continue
        except Exception:  # noqa: BLE001 - one malformed filing must not break the rest
            continue

    result["filings_examined"] = examined
    if not transactions:
        result["reason"] = (
            f"Unavailable - {len(filings)} Form 4 filing(s) were found but no transactions could be parsed "
            "from them." if examined else
            "Unavailable - Form 4 filings were found but could not be retrieved from SEC EDGAR."
        )
        return result

    transactions.sort(key=lambda t: (t.get("transaction_date") or ""), reverse=True)
    result["available"] = True
    result["transactions"] = transactions[:50]
    result["summary"] = summarize(transactions)
    return result
