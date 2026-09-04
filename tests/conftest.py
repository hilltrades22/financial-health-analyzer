"""
Shared test configuration.

Every SEC request goes through a real rate limiter (5 requests/second by
default) so the application cannot breach SEC's fair-access policy. That
pacing is correct in production but would add minutes to a suite whose HTTP
calls are all mocked, so it is neutralised here by default. The limiter's own
behaviour is still tested directly in test_derived_and_timeline.py, which
constructs its own _RateLimiter rather than relying on the shared one.
"""
import pytest

from backend.app import sec_client as SC


@pytest.fixture(autouse=True)
def _no_rate_limit_pacing(monkeypatch):
    monkeypatch.setattr(SC._rate_limiter, "_min_interval", 0.0)
    yield
