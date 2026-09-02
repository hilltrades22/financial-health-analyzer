"""
Company classification: sector, industry, sub-industry, exchange, country.

Everything here is derived from data the company itself filed with the SEC -
its SIC code and its submissions metadata - not from a third-party
classification vendor and not invented. The SIC code is assigned in EDGAR
and returned by SEC's submissions API; SEC publishes SIC codes in numbered
ranges (its "Office" groupings and the standard SIC divisions), so mapping a
code to a sector is a deterministic lookup, not an estimate.

Where SEC reports nothing (some foreign private issuers have no SIC code on
file), the field is returned as unavailable rather than guessed.
"""
from __future__ import annotations

from typing import Any, Optional

# Standard SIC divisions (ranges are the published SIC structure). The
# "sector" is the division; the "industry" is SEC's own sicDescription for
# the specific 4-digit code, so it stays exactly as the filer is classified.
_SIC_DIVISIONS: list[tuple[int, int, str]] = [
    (100, 999, "Agriculture, Forestry & Fishing"),
    (1000, 1499, "Mining & Energy Extraction"),
    (1500, 1799, "Construction"),
    (2000, 3999, "Manufacturing"),
    (4000, 4999, "Transportation, Communications & Utilities"),
    (5000, 5199, "Wholesale Trade"),
    (5200, 5999, "Retail Trade"),
    (6000, 6799, "Finance, Insurance & Real Estate"),
    (7000, 8999, "Services"),
    (9100, 9999, "Public Administration"),
]

# Finer-grained sub-industry groupings for the SIC ranges that matter most to
# how a balance sheet should be read. These drive sector-aware interpretation
# of the financial-health rules (a bank's leverage is not a software
# company's leverage), so each one names the peer group, not a judgement.
_SUB_INDUSTRY_RANGES: list[tuple[int, int, str, str]] = [
    # (low, high, sub-industry, analysis peer group)
    (1311, 1399, "Oil & Gas Extraction / Services", "energy"),
    (2833, 2836, "Pharmaceuticals & Biotechnology", "biotech"),
    (3570, 3579, "Computer & Office Equipment", "hardware"),
    (3600, 3629, "Electronic & Electrical Equipment", "hardware"),
    (3661, 3669, "Communications Equipment", "hardware"),
    (3670, 3679, "Semiconductors & Related Devices", "semiconductor"),
    (3711, 3799, "Motor Vehicles & Transportation Equipment", "industrial"),
    (4813, 4899, "Telecommunications & Media", "telecom"),
    (4900, 4991, "Utilities", "utility"),
    (5200, 5999, "Retail", "retail"),
    (6020, 6099, "Banking", "bank"),
    (6199, 6299, "Investment Banking, Brokerage & Securities", "financial"),
    (6300, 6411, "Insurance", "insurance"),
    (6500, 6599, "Real Estate", "reit"),
    (6798, 6798, "Real Estate Investment Trusts (REITs)", "reit"),
    (7370, 7379, "Software & Information Technology Services", "software"),
    (8000, 8099, "Health Care Services", "healthcare"),
]

# Plain-English note on what makes each peer group's financial statements
# read differently. Surfaced to the user so a sector-aware verdict explains
# itself instead of just being more lenient.
PEER_GROUP_NOTES: dict[str, str] = {
    "bank": "Banks fund themselves with customer deposits, which appear as liabilities. High 'leverage' and low "
            "current ratios are the normal structure of a healthy bank, not a warning sign.",
    "insurance": "Insurers hold large reserves against future claims as liabilities and carry big investment "
                 "portfolios, so conventional debt and liquidity ratios do not read the same way.",
    "financial": "Financial firms carry large balance sheets of financial instruments on both sides, so gross "
                 "leverage ratios are not comparable to an operating company's.",
    "reit": "REITs are structured to hold property with substantial mortgage debt and to distribute most of their "
            "income, so leverage and payout ratios are naturally high and cash flow matters more than earnings.",
    "utility": "Utilities have regulated, highly predictable revenue and fund long-lived infrastructure with debt, "
               "so higher leverage is normal and sustainable for them.",
    "semiconductor": "Semiconductor companies run heavy, cyclical capital expenditure programmes, so free cash flow "
                     "swings with the build cycle and a capex-heavy year is not automatically a weakness.",
    "biotech": "Development-stage biotech companies often have little or no revenue and burn cash by design, so "
               "profitability tests say more about stage than about health; cash runway is the key measure.",
    "software": "Software companies are asset-light with low capital expenditure and often carry deferred revenue "
                "as a liability, which depresses working capital without indicating stress.",
    "retail": "Retailers carry large inventories and lease obligations and turn over cash quickly, so inventory and "
              "lease-adjusted leverage matter more than headline debt.",
    "energy": "Energy producers' earnings and cash flow swing with commodity prices, so a single year's figures can "
              "misrepresent the underlying position.",
    "telecom": "Telecom operators fund long-lived network infrastructure with debt against steady subscriber cash "
               "flow, so higher leverage is structurally normal.",
    "hardware": "Hardware manufacturers carry inventory and manufacturing assets, so working capital and inventory "
                "turns matter more than for an asset-light business.",
    "industrial": "Industrial manufacturers are capital- and inventory-intensive and often cyclical.",
    "healthcare": "Health care service providers carry significant receivables with long collection cycles.",
    "general": "Interpreted with general-purpose thresholds for an operating company.",
}


def _sic_int(sic: Any) -> Optional[int]:
    try:
        return int(str(sic).strip())
    except (TypeError, ValueError):
        return None


def sector_for_sic(sic: Any) -> Optional[str]:
    code = _sic_int(sic)
    if code is None:
        return None
    for low, high, name in _SIC_DIVISIONS:
        if low <= code <= high:
            return name
    return None


def sub_industry_for_sic(sic: Any) -> tuple[Optional[str], str]:
    """Returns (sub-industry label, peer group key). Peer group defaults to
    'general' so callers always have a usable interpretation basis."""
    code = _sic_int(sic)
    if code is None:
        return None, "general"
    for low, high, label, group in _SUB_INDUSTRY_RANGES:
        if low <= code <= high:
            return label, group
    return None, "general"


def build_classification(submissions: dict[str, Any], market_cap: Optional[float] = None) -> dict[str, Any]:
    """Sector / industry / sub-industry / exchange / country / market cap,
    built from SEC's own submissions metadata. Anything SEC does not report
    comes back as None with available=False rather than a guess."""
    sic = submissions.get("sic")
    industry = submissions.get("sicDescription")
    sector = sector_for_sic(sic)
    sub_industry, peer_group = sub_industry_for_sic(sic)

    # A company with several listed share classes repeats the same exchange
    # once per class (Alphabet returns Nasdaq four times) - list each once.
    exchanges = list(dict.fromkeys(e for e in (submissions.get("exchanges") or []) if e))
    addresses = submissions.get("addresses") or {}
    business = addresses.get("business") or {}
    mailing = addresses.get("mailing") or {}
    country = (
        business.get("stateOrCountryDescription")
        or business.get("country")
        or mailing.get("stateOrCountryDescription")
        or mailing.get("country")
    )
    # SEC uses a two-letter code for US states and a country code otherwise;
    # a US state means the business address is in the United States.
    state_or_country = business.get("stateOrCountry") or mailing.get("stateOrCountry")
    if not country and state_or_country and len(str(state_or_country)) == 2 and str(state_or_country).isalpha():
        country = "United States" if str(state_or_country).isupper() else None

    def field(value: Any, source: str) -> dict[str, Any]:
        return {
            "available": value not in (None, "", []),
            "value": value if value not in (None, "", []) else None,
            "source": source if value not in (None, "", []) else None,
        }

    sec_src = "SEC EDGAR submissions API (company metadata as filed)"
    return {
        "sector": field(sector, f"Derived from SEC-assigned SIC code {sic} (standard SIC division)"),
        "industry": field(industry, f"SEC-assigned SIC code {sic} description"),
        "sub_industry": field(sub_industry, f"Derived from SEC-assigned SIC code {sic}"),
        "sic_code": field(str(sic) if sic else None, sec_src),
        "exchange": field(", ".join(exchanges) if exchanges else None, sec_src),
        "exchanges": exchanges,
        "country": field(country, sec_src),
        "state_of_incorporation": field(submissions.get("stateOfIncorporation") or None, sec_src),
        "fiscal_year_end": field(submissions.get("fiscalYearEnd") or None, sec_src),
        "entity_type": field(submissions.get("entityType") or None, sec_src),
        "market_cap": {
            "available": market_cap is not None,
            "value": market_cap,
            "source": "Live market price x SEC-reported shares outstanding" if market_cap is not None else None,
        },
        "peer_group": peer_group,
        "peer_group_note": PEER_GROUP_NOTES.get(peer_group, PEER_GROUP_NOTES["general"]),
        "source": sec_src,
    }
