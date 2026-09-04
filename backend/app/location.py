"""
Company location, presented the way a person would write it.

SEC's submissions metadata gives a principal business address but in a raw
form: the city is upper-cased ("CUPERTINO") and the state arrives as a
two-letter code whose "description" field is, unhelpfully, the same code
("CA"). Showing that verbatim reads like a database dump rather than a
product, so codes are expanded and names are cased properly here.

Two distinctions this module is careful about:

  * Principal business location is NOT state of incorporation. Apple's
    business address is Cupertino, California; Shopify is incorporated
    federally in Canada while its offices are in Ottawa, Ontario. They are
    different facts and are kept in different fields.
  * Nothing is invented. Where SEC reports only a country, the location is
    that country. Where it reports nothing usable, the location is
    unavailable and says so - a plausible-looking city is never guessed.
"""
from __future__ import annotations

from typing import Any, Optional

# US states, DC and territories, keyed by the postal code SEC uses.
US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "AS": "American Samoa", "GU": "Guam", "MP": "Northern Mariana Islands",
    "PR": "Puerto Rico", "VI": "U.S. Virgin Islands",
}

# Canadian provinces and territories, which SEC reports either as a code in
# stateOrCountry or spelled out in foreignStateTerritory.
CA_PROVINCES: dict[str, str] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

# Words that stay lower-case inside a place name, and forms that should keep
# a fixed casing rather than being naively title-cased.
_LOWER_WORDS = {"of", "the", "and", "upon", "de", "du", "la", "le", "el", "da", "van", "der"}
_FIXED_CASE = {
    "us": "US", "usa": "USA", "uk": "UK", "uae": "UAE", "dc": "DC",
    "ny": "NY", "la": "LA", "st": "St", "st.": "St.", "ste": "Ste", "mt": "Mt",
}


def titlecase_place(value: Optional[str]) -> Optional[str]:
    """'CUPERTINO' -> 'Cupertino', 'NEW YORK' -> 'New York',
    "KING'S LYNN" -> "King's Lynn". Text that is already mixed-case is left
    alone, since the filer has evidently written it deliberately."""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw != raw.upper():
        return raw  # already cased by the filer

    words = raw.lower().split()
    out: list[str] = []
    for i, word in enumerate(words):
        if word in _FIXED_CASE:
            out.append(_FIXED_CASE[word])
            continue
        if i > 0 and word in _LOWER_WORDS:
            out.append(word)
            continue
        # Capitalise across hyphens and apostrophes: "st-jean" -> "St-Jean".
        piece = word
        for sep in ("-", "'", "."):
            piece = sep.join(
                (p[:1].upper() + p[1:]) if p and not (sep == "'" and len(p) == 1) else p
                for p in piece.split(sep)
            )
        out.append(piece)
    return " ".join(out)


def expand_region(code: Optional[str]) -> Optional[str]:
    """Expand a US state or Canadian province code to its full name. Anything
    unrecognised is returned unchanged rather than dropped."""
    if not code:
        return None
    key = str(code).strip().upper()
    return US_STATES.get(key) or CA_PROVINCES.get(key) or (code.strip() or None)


def _country_from_field(value: Optional[str]) -> Optional[str]:
    """SEC sometimes packs region and country together ('Ontario, Canada').
    The country is the last comma-separated part."""
    if not value:
        return None
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    return parts[-1] if parts else None


def _region_from_country_field(value: Optional[str]) -> Optional[str]:
    parts = [p.strip() for p in str(value or "").split(",") if p.strip()]
    return parts[0] if len(parts) > 1 else None


def build_location(submissions: dict[str, Any]) -> dict[str, Any]:
    """The company's principal business location, formatted for a reader.

    Returns city / region / country separately as well as a formatted
    display string, plus the state of incorporation kept as its own field so
    the two are never conflated.
    """
    addresses = (submissions or {}).get("addresses") or {}
    business = addresses.get("business") or {}
    mailing = addresses.get("mailing") or {}
    # Prefer the business address; fall back to mailing only if it has a city.
    source = business if (business.get("city") or business.get("stateOrCountry")) else mailing

    city = titlecase_place(source.get("city"))
    is_foreign = bool(source.get("isForeignLocation"))
    country_field = source.get("country")

    region: Optional[str] = None
    country: Optional[str] = None

    if is_foreign or country_field:
        # Foreign filers: SEC gives a spelled-out territory and/or country.
        region = titlecase_place(source.get("foreignStateTerritory")) \
            or titlecase_place(_region_from_country_field(country_field))
        country = titlecase_place(_country_from_field(country_field))
    else:
        code = (source.get("stateOrCountry") or "").strip().upper()
        if code in US_STATES:
            region, country = US_STATES[code], "United States"
        elif code in CA_PROVINCES:
            region, country = CA_PROVINCES[code], "Canada"
        elif code:
            # An unrecognised code is more likely a country than a state, and
            # is shown as SEC gave it rather than guessed at.
            region, country = None, expand_region(code)

    parts = [p for p in (city, region, country) if p]
    display = ", ".join(parts) if parts else None

    incorporation = (submissions or {}).get("stateOfIncorporationDescription") \
        or (submissions or {}).get("stateOfIncorporation") or None
    if incorporation:
        incorporation = expand_region(incorporation) if len(str(incorporation).strip()) == 2 \
            else str(incorporation).strip()

    return {
        "available": bool(display),
        "display": display,
        "city": city,
        "region": region,
        "country": country,
        "is_foreign": is_foreign,
        "source": "SEC EDGAR submissions - principal business address as filed"
                  if display else None,
        "reason": None if display
                  else "Unavailable - SEC does not report a business address for this filer.",
        # Deliberately separate: where a company is registered is a different
        # fact from where it operates, and must never stand in for it.
        "state_of_incorporation": incorporation,
    }
