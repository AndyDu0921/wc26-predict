"""Test market provider selection logic."""
from __future__ import annotations



def test_apifootball_com_provider_imports():
    """Provider module can be imported."""
    from app.services.market.apifootball_com_provider import \
        ApifootballComProvider
    assert ApifootballComProvider is not None


def test_apifootball_com_provider_name():
    """Provider name is correct."""
    from app.services.market.apifootball_com_provider import \
        ApifootballComProvider
    provider = ApifootballComProvider()
    assert provider.name == "apifootball.com"


def test_apifootball_com_graceful_no_key():
    """Provider gracefully handles missing key."""
    from app.services.market.apifootball_com_provider import \
        ApifootballComProvider
    provider = ApifootballComProvider()
    # Without key configured, is_available should return False (no crash)
    if not provider.api_key:
        import asyncio
        result = asyncio.run(provider.is_available())
        assert result is False


def test_market_calibrator_provider_selection():
    """MarketCalibrator can resolve providers."""
    from app.services.market_calibrator import MarketCalibrator
    calibrator = MarketCalibrator(shadow_mode=True)
    assert calibrator.shadow_mode is True
    assert calibrator._odds_provider_resolved is False


def test_provider_base_interface():
    """MarketOddsResult and MarketProvider are importable."""
    from app.services.market.provider_base import MarketOddsResult, MarketProvider
    assert MarketOddsResult is not None
    assert MarketProvider is not None
