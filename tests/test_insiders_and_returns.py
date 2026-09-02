"""
SEC-native insider activity (Form 4) and shareholder returns.

The Form 4 XML below matches the real structure served by SEC EDGAR
(verified against Apple's filing 0001140361-26-035362): fields are wrapped
as <field><value>x</value></field>, and the transaction code carries the
meaning - P is an open-market purchase, S an open-market sale, A a grant,
M an option exercise, F shares withheld for tax.
"""
from backend.app.insiders import TRANSACTION_CODES, parse_form4, summarize
from backend.app.shareholder_returns import build_shareholder_returns

FORM4 = b"""<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <periodOfReport>2026-09-01</periodOfReport>
  <issuer><issuerName>Example Corp</issuerName><issuerTradingSymbol>EX</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector><isOfficer>1</isOfficer>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-08-28</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>150.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-08-29</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>400</value></transactionShares>
        <transactionPricePerShare><value>160.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-09-01</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>250</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <securityTitle><value>Restricted Stock Unit</value></securityTitle>
      <transactionDate><value>2026-09-01</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>7690</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>"""


def test_form4_parses_insider_identity_and_role():
    txns = parse_form4(FORM4, filing_date="2026-09-02")
    assert len(txns) == 4
    assert txns[0]["insider"] == "Doe Jane"
    assert txns[0]["role"] == "Chief Financial Officer"
    assert txns[0]["filing_date"] == "2026-09-02"


def test_transaction_types_are_distinguished_not_lumped_together():
    txns = {t["code"]: t for t in parse_form4(FORM4)}
    assert txns["P"]["category"] == "open_market_buy"
    assert txns["S"]["category"] == "open_market_sell"
    assert txns["F"]["category"] == "tax_withholding"
    assert txns["A"]["category"] == "grant"
    assert txns["A"]["is_derivative"] is True
    # A grant and a tax withholding are explicitly NOT open-market trades.
    assert "not bought by the insider" in txns["A"]["type_note"]
    assert "did not choose to sell" in txns["F"]["type_note"]


def test_value_is_only_computed_when_a_real_price_is_reported():
    txns = {t["code"]: t for t in parse_form4(FORM4)}
    assert txns["P"]["value"] == 1000 * 150.0
    assert txns["S"]["value"] == 400 * 160.0
    # Price 0 on a grant means no market value - not $0 of trading.
    assert txns["A"]["value"] is None
    assert txns["F"]["value"] is None


def test_summary_separates_open_market_activity_from_compensation():
    s = summarize(parse_form4(FORM4))
    assert s["open_market_purchases"]["count"] == 1
    assert s["open_market_purchases"]["shares"] == 1000
    assert s["open_market_sales"]["count"] == 1
    assert s["open_market_sales"]["shares"] == 400
    assert s["net_open_market_shares"] == 600
    # Grants and tax withholding are counted, but kept out of buy/sell totals.
    assert s["grants"]["count"] == 1
    assert s["tax_withholding"]["count"] == 1
    # Largest by value is the 1,000-share purchase, not the 400-share sale.
    assert s["largest_transaction"]["value"] == 1000 * 150.0
    assert s["largest_transaction"]["code"] == "P"


def test_insider_summary_refuses_to_call_selling_bearish():
    note = summarize(parse_form4(FORM4))["interpretation_note"]
    assert "not signals" in note
    assert "pre-arranged" in note
    for word in ("bearish", "bullish"):
        assert word not in note.lower()


def test_unknown_transaction_code_is_reported_not_forced_into_a_category():
    xml = FORM4.replace(b"<transactionCode>P</transactionCode>",
                        b"<transactionCode>Z</transactionCode>")
    txns = {t["code"]: t for t in parse_form4(xml)}
    assert txns["Z"]["category"] == "other"
    assert "Z" in txns["Z"]["type"]
    assert "Z" not in TRANSACTION_CODES


# --- Shareholder returns -------------------------------------------------

def _inst(vals):
    return {"units": {"shares": [{"end": e, "val": v, "form": "10-K", "filed": e,
                                   "fy": int(e[:4]), "fp": "FY", "accn": "a"} for e, v in vals]}}


def _dur(vals):
    return {"units": {"USD": [{"start": s, "end": e, "val": v, "form": "10-K", "filed": e,
                                "fy": int(e[:4]), "fp": "FY", "accn": "a"} for s, e, v in vals]}}


PAYER = {"facts": {"us-gaap": {
    "PaymentsOfDividends": _dur([("2023-01-01", "2023-12-31", 1.4e10),
                                  ("2024-01-01", "2024-12-31", 1.5e10),
                                  ("2025-01-01", "2025-12-31", 1.6e10)]),
    "PaymentsForRepurchaseOfCommonStock": _dur([("2025-01-01", "2025-12-31", 7.0e10)]),
    "NetIncomeLoss": _dur([("2025-01-01", "2025-12-31", 9.0e10)]),
    "NetCashProvidedByUsedInOperatingActivities": _dur([("2025-01-01", "2025-12-31", 1.1e11)]),
    "CommonStockSharesOutstanding": _inst([("2023-12-31", 1.60e10), ("2024-12-31", 1.55e10),
                                            ("2025-12-31", 1.48e10)]),
}}}


def test_dividend_payout_uses_only_reported_denominators():
    r = build_shareholder_returns(PAYER)
    d = r["dividend"]
    assert d["pays_dividend"] is True
    assert d["payout_ratio_earnings"]["display"] == "18%"     # 16B / 90B
    assert d["payout_ratio_cash_flow"]["display"] == "15%"    # 16B / 110B
    assert "growth" in d
    assert d["growth"]["value"] > 0
    # Yield needs a market price and must be flagged as a different source.
    assert "market-data provider" in d["yield_note"]


def test_buyback_and_real_dilution_are_reported_separately():
    r = build_shareholder_returns(PAYER)
    assert r["buyback"]["repurchases_reported"] is True
    trend = r["share_count_trend"]
    assert trend["available"] is True
    assert trend["direction"] == "reducing"
    assert trend["change_pct"] < 0
    assert "larger claim" in trend["explanation"]


def test_company_issuing_more_stock_than_it_buys_back_is_shown_as_diluting():
    facts = {"facts": {"us-gaap": {
        "PaymentsForRepurchaseOfCommonStock": _dur([("2025-01-01", "2025-12-31", 2.0e9)]),
        "CommonStockSharesOutstanding": _inst([("2023-12-31", 1.0e9), ("2025-12-31", 1.08e9)]),
    }}}
    trend = build_shareholder_returns(facts)["share_count_trend"]
    assert trend["direction"] == "diluting"
    assert "diluted" in trend["explanation"]


def test_non_payer_is_not_penalised():
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": _dur([("2025-01-01", "2025-12-31", 5.0e9)]),
    }}}
    d = build_shareholder_returns(facts)["dividend"]
    assert d["pays_dividend"] is False
    assert "not a weakness" in d["reason"]
    assert "not scored against" in d["reason"]
