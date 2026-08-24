"""Builds the plain-English 'Financial Story' summary from scored rules."""
from __future__ import annotations

from typing import Any


def build_financial_story(company_name: str, ticker: str, score: dict[str, Any]) -> str:
    label = score["label"]
    pct = score["overall_score"]
    by_id = {r["id"]: r for r in score["rules"]}

    opener = {
        "Strong": f"{company_name} ({ticker}) shows a strong financial position based on its latest SEC filings.",
        "Healthy": f"{company_name} ({ticker}) shows a generally healthy financial position based on its latest SEC filings, with a few areas worth watching.",
        "Needs Review": f"{company_name} ({ticker}) shows some financial areas that warrant a closer look based on its latest SEC filings.",
        "Insufficient Data": f"There is not enough standardized SEC data available to form a confident view of {company_name} ({ticker})'s overall financial health.",
    }.get(label, f"{company_name} ({ticker}) financial summary.")

    sentences = [opener]

    liq = by_id.get("liquidity")
    if liq and liq["status"] != "UNAVAILABLE":
        if liq["status"] == "PASS":
            sentences.append(f"The company holds more cash and marketable securities than it owes in debt ({liq['value']} net), a comfortable liquidity cushion.")
        else:
            sentences.append(f"Reported debt currently exceeds the company's cash and marketable securities by {liq['value'].lstrip('-')}.")

    de = by_id.get("debt_to_equity")
    if de and de["status"] != "UNAVAILABLE":
        sentences.append(f"Total liabilities run {de['value']} of total equity" + (", within the conservative target." if de["status"] == "PASS" else ", above the conservative 0.80x target."))

    re_rule = by_id.get("retained_earnings")
    reg = by_id.get("retained_earnings_growth")
    if re_rule and re_rule["status"] != "UNAVAILABLE":
        if re_rule["status"] == "PASS":
            trend = ""
            if reg and reg["status"] == "PASS":
                trend = " and continues to grow year over year"
            elif reg and reg["status"] == "FAIL":
                trend = ", though it declined from the prior fiscal year"
            sentences.append(f"The company has positive retained earnings of {re_rule['value']}{trend}, reflecting a history of cumulative profitability.")
        else:
            sentences.append(f"The company carries an accumulated deficit of {re_rule['value']}, meaning cumulative losses outweigh cumulative profits.")

    fcf = by_id.get("free_cash_flow")
    if fcf and fcf["status"] != "UNAVAILABLE":
        if fcf["status"] == "PASS":
            sentences.append(f"It generated {fcf['value']} of free cash flow in its latest fiscal year, after capital spending.")
        else:
            sentences.append(f"Its latest fiscal year shows negative free cash flow of {fcf['value'].lstrip('-')}, meaning capital spending outpaced cash generated from operations.")

    ic = by_id.get("interest_coverage")
    if ic and ic["status"] != "UNAVAILABLE" and ic["value"] != "N/A (no interest expense)":
        if ic["status"] == "PASS":
            sentences.append(f"Operating income covers interest expense comfortably at {ic['value']}.")
        elif ic["status"] == "WATCH":
            sentences.append(f"Interest coverage is thinner than ideal at {ic['value']}, worth monitoring.")
        else:
            sentences.append(f"Interest coverage is weak at {ic['value']}, a debt-service risk.")

    treasury = by_id.get("treasury_stock")
    if treasury and treasury["status"] == "WATCH":
        sentences.append("The company has repurchased shares and/or carries treasury stock, which reduces reported equity - a factor worth understanding rather than assuming is automatically positive.")

    unavailable_count = len(score["unavailable_rules"])
    if unavailable_count:
        sentences.append(
            f"{unavailable_count} of 9 rule{'s' if unavailable_count != 1 else ''} could not be scored because "
            f"the underlying data was not available in SEC's standardized XBRL facts for this company; "
            f"those rules did not count for or against the score."
        )

    return " ".join(sentences)
