"""
Market-data provider layer.

Everything in this file is NON-SEC data: live quotes, analyst consensus,
price targets, forward estimates, institutional ownership. It is kept behind
a provider interface for two reasons:

  1. The financial-health engine must never depend on it. If every provider
     here is unavailable, the SEC-based analysis is unaffected.
  2. Providers change. Yahoo's quoteSummary endpoint, which used to serve
     key statistics, now returns "Unauthorized / Invalid Crumb", so the app
     must be able to report a capability as unavailable rather than quietly
     returning empty values that look like zeros.

A provider that is not configured returns available=False with a reason.
Nothing here ever invents a value.
"""
from __future__ import annotations

import os
from typing import Any

# Capability -> what it would supply, and which env var configures it.
CAPABILITIES: dict[str, dict[str, str]] = {
    "price": {
        "description": "Live and historical share price",
        "env": "",  # keyless
    },
    "analyst": {
        "description": "Analyst consensus rating, number of analysts, average price target",
        "env": "ANALYST_PROVIDER_API_KEY",
    },
    "estimates": {
        "description": "Forward EPS and revenue consensus estimates",
        "env": "ANALYST_PROVIDER_API_KEY",
    },
    "ownership": {
        "description": "Institutional holders and position changes",
        "env": "OWNERSHIP_PROVIDER_API_KEY",
    },
}

_NO_PROVIDER = "Unavailable - no provider configured."


def _configured(env_var: str) -> bool:
    return bool(env_var) and bool(os.environ.get(env_var, "").strip())


def provider_status() -> dict[str, dict[str, Any]]:
    """What each market-data capability can currently supply.

    The price capability is keyless and is reported as available because the
    chart endpoint is verified to work. Everything else requires a provider
    that has not been configured, and says so explicitly - the UI shows the
    section with 'Unavailable' rather than hiding it, so a reader can tell
    the difference between 'no data' and 'no coverage'.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, spec in CAPABILITIES.items():
        if name == "price":
            out[name] = {
                "available": True,
                "provider": "Yahoo Finance chart API",
                "description": spec["description"],
                "note": "Keyless endpoint, verified working. Delayed quote - not SEC data.",
            }
            continue
        if _configured(spec["env"]):
            out[name] = {
                "available": True,
                "provider": os.environ.get(f"{spec['env']}_NAME", "configured provider"),
                "description": spec["description"],
            }
        else:
            out[name] = {
                "available": False,
                "provider": None,
                "description": spec["description"],
                "reason": f"{_NO_PROVIDER} Set {spec['env']} to enable {spec['description'].lower()}.",
            }
    return out


def analyst_data(ticker: str) -> dict[str, Any]:
    """Analyst consensus / price target / estimates.

    No provider is configured, so this returns an explicit unavailable
    result. It deliberately does NOT fall back to Yahoo's quoteSummary
    endpoint, which currently rejects requests with 'Invalid Crumb' -
    building on a known-broken endpoint would produce silent empty values.
    """
    status = provider_status()["analyst"]
    return {
        "available": False,
        "reason": status.get("reason", _NO_PROVIDER),
        "consensus": None,
        "analyst_count": None,
        "price_target": None,
        "price_target_analyst_count": None,
        "source": "Market-data provider (not SEC data)",
    }


def estimates_data(ticker: str) -> dict[str, Any]:
    status = provider_status()["estimates"]
    return {
        "available": False,
        "reason": status.get("reason", _NO_PROVIDER),
        "eps_estimates": None,
        "revenue_estimates": None,
        "source": "Market-data provider (not SEC data)",
    }


def ownership_data(ticker: str) -> dict[str, Any]:
    """Institutional ownership.

    SEC's own 13F filings do report institutional positions, but they are
    filed per institution with no reverse index by security, so producing a
    complete holder list for one company would mean scanning every 13F
    filed. That is not a lookup SEC provides, and pretending otherwise would
    misrepresent what the data can do.
    """
    status = provider_status()["ownership"]
    return {
        "available": False,
        "reason": status.get("reason", _NO_PROVIDER),
        "sec_note": (
            "SEC Form 13F reports institutional holdings, but it is indexed by filing institution rather "
            "than by security, so SEC data alone cannot produce a complete list of institutions holding a "
            "given company without scanning every 13F filed."
        ),
        "holders": None,
        "source": "Market-data provider (not SEC data)",
    }
