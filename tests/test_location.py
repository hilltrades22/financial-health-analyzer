"""
Company location presentation.

SEC's submissions metadata is raw: the city arrives upper-cased and the state
as a two-letter code whose "description" field is the same code again. These
tests lock in that the product shows a readable location, never invents one,
and never confuses where a company operates with where it is registered.

The fixtures below are the exact shapes SEC returns for these filers
(verified against data.sec.gov).
"""
from backend.app.classification import build_classification
from backend.app.location import (
    build_location, expand_region, titlecase_place, CA_PROVINCES, US_STATES,
)

AAPL = {"addresses": {"business": {"street1": "ONE APPLE PARK WAY", "city": "CUPERTINO",
                                    "stateOrCountry": "CA", "stateOrCountryDescription": "CA",
                                    "isForeignLocation": None, "country": None}},
        "stateOfIncorporation": "CA", "stateOfIncorporationDescription": "CA"}

MSFT = {"addresses": {"business": {"city": "REDMOND", "stateOrCountry": "WA",
                                    "isForeignLocation": None}},
        "stateOfIncorporation": "WA"}

SHOP = {"addresses": {"business": {"city": "OTTAWA", "stateOrCountry": None,
                                    "isForeignLocation": 1, "foreignStateTerritory": "ONTARIO",
                                    "country": "Ontario, Canada", "countryCode": "A6"}},
        "stateOfIncorporation": "Z4", "stateOfIncorporationDescription": "Canada (Federal Level)"}

TSM = {"addresses": {"business": {"city": "HSINCHU", "stateOrCountry": None,
                                   "isForeignLocation": 1, "foreignStateTerritory": None,
                                   "country": "Taiwan", "countryCode": "F5"}},
       "stateOfIncorporation": ""}


# --- Display -------------------------------------------------------------

def test_us_company_shows_city_state_country_not_a_two_letter_code():
    loc = build_location(AAPL)
    assert loc["display"] == "Cupertino, California, United States"
    assert loc["city"] == "Cupertino"        # not "CUPERTINO"
    assert loc["region"] == "California"     # not "CA"
    assert loc["country"] == "United States"


def test_second_us_company_confirms_the_mapping_is_general():
    assert build_location(MSFT)["display"] == "Redmond, Washington, United States"


def test_canadian_issuer_uses_province_and_country():
    loc = build_location(SHOP)
    assert loc["display"] == "Ottawa, Ontario, Canada"
    assert loc["region"] == "Ontario"        # not "ONTARIO", not "ON"
    assert loc["is_foreign"] is True


def test_country_only_issuer_is_shown_as_country_not_an_invented_city():
    loc = build_location(TSM)
    assert loc["display"] == "Hsinchu, Taiwan"
    assert loc["country"] == "Taiwan"
    assert loc["region"] is None             # SEC reports no territory; none is invented


def test_missing_address_is_unavailable_with_a_reason_not_a_guess():
    loc = build_location({})
    assert loc["available"] is False
    assert loc["display"] is None
    assert "does not report" in loc["reason"]


def test_mailing_address_used_only_when_business_address_is_absent():
    subs = {"addresses": {"business": {}, "mailing": {"city": "AUSTIN", "stateOrCountry": "TX"}}}
    assert build_location(subs)["display"] == "Austin, Texas, United States"


# --- Incorporation is a separate fact ------------------------------------

def test_incorporation_is_kept_separate_from_where_the_company_operates():
    """Shopify operates in Ottawa but is incorporated federally in Canada;
    Apple operates in Cupertino and is incorporated in California. Neither
    fact may stand in for the other."""
    shop = build_location(SHOP)
    assert shop["display"] == "Ottawa, Ontario, Canada"
    assert shop["state_of_incorporation"] == "Canada (Federal Level)"

    aapl = build_location(AAPL)
    assert aapl["state_of_incorporation"] == "California"
    assert aapl["state_of_incorporation"] != aapl["display"]


# --- Normalization primitives -------------------------------------------

def test_region_codes_expand_for_states_and_provinces():
    assert expand_region("CA") == "California"
    assert expand_region("WA") == "Washington"
    assert expand_region("TX") == "Texas"
    assert expand_region("NY") == "New York"
    assert expand_region("ON") == "Ontario"
    assert len(US_STATES) >= 50 and len(CA_PROVINCES) == 13


def test_unknown_region_code_is_passed_through_not_dropped():
    assert expand_region("ZZ") == "ZZ"
    assert expand_region(None) is None


def test_place_names_are_cased_like_a_person_would_write_them():
    assert titlecase_place("CUPERTINO") == "Cupertino"
    assert titlecase_place("NEW YORK") == "New York"
    assert titlecase_place("SAINT-JEAN") == "Saint-Jean"
    assert titlecase_place("ISLE OF MAN") == "Isle of Man"
    # Text the filer already cased deliberately is left alone.
    assert titlecase_place("iPhone City") == "iPhone City"
    assert titlecase_place("") is None


# --- Wiring --------------------------------------------------------------

def test_classification_exposes_location_and_keeps_incorporation_apart():
    c = build_classification({**AAPL, "sic": "3571", "sicDescription": "Electronic Computers",
                              "exchanges": ["Nasdaq"]})
    assert c["location"]["available"] is True
    assert c["location"]["value"] == "Cupertino, California, United States"
    assert c["state_of_incorporation"]["value"] == "California"
    assert c["country"]["value"] == "United States"
    assert c["peer_group_label"]


def test_classification_location_unavailable_is_explained():
    c = build_classification({"sic": "3571"})
    assert c["location"]["available"] is False
    assert c["location"]["reason"]
