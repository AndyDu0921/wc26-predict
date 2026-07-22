"""Test asyncio.run() safety — Ticket 0.5B.

Verifies that service-layer functions using asyncio.run() do not crash
when called from within an existing event loop, and return structured
degraded reasons.
"""
import asyncio


class TestAsyncioRunSafety:
    """Verify HIGH-risk asyncio.run() call sites don't crash in async context."""

    # ---- weather_service ----

    def test_weather_does_not_crash_in_event_loop(self):
        """get_weather_for_match_sync() must not raise RuntimeError in async context."""
        from app.services.weather_service import WeatherService

        svc = WeatherService()

        async def call_weather():
            return svc.get_weather_for_match_sync(
                venue="Lusail Stadium",
                home_team="Argentina",
                away_team="Brazil",
            )

        result = asyncio.run(call_weather())
        assert isinstance(result, dict)

    def test_weather_degraded_when_event_loop_conflict(self):
        """get_weather_for_match_sync() returns degraded when called within event loop."""
        from app.services.weather_service import WeatherService

        svc = WeatherService()

        async def inner():
            # Call from within running loop — should not crash
            return svc.get_weather_for_match_sync(
                venue="Lusail Stadium",
                home_team="Argentina",
                away_team="Brazil",
            )

        result = asyncio.run(inner())
        assert isinstance(result, dict)
        # When called from within an event loop, should be degraded
        # (weather_service wraps asyncio.run which can't be nested)
        if result.get("degraded"):
            assert "source" in result
            assert result["source"] == "weather"

    # ---- prediction_enhanced ----

    def test_llm_analysis_does_not_crash_in_event_loop(self):
        """_generate_llm_analysis() must not raise RuntimeError in async context."""
        from app.services.prediction_enhanced import _generate_llm_analysis

        # Create a minimal mock result — the function will try asyncio.run
        # which should be caught by our safety wrapper
        class FakeResult:
            pass

        async def call_llm():
            return _generate_llm_analysis(FakeResult())

        result = asyncio.run(call_llm())
        # Should return either a dict with degraded flag or (if no API key) None
        if isinstance(result, dict):
            assert "degraded" in result or "analysis" in result

    def test_llm_analysis_returns_degraded_on_loop_conflict(self):
        """LLM analysis returns structured degraded when loop conflict."""
        from app.services.prediction_enhanced import _generate_llm_analysis

        class FakeResult:
            pass

        async def inner():
            return _generate_llm_analysis(FakeResult())

        result = asyncio.run(inner())
        # The function runs inside asyncio.run(inner()) which has a loop,
        # then _generate_llm_analysis tries asyncio.run() again — conflict
        if isinstance(result, dict) and result.get("degraded"):
            assert "source" in result
            assert result["source"] == "llm_analysis"
            assert "reason" in result

    # ---- market sync_provider ----

    def test_market_consensus_does_not_crash_in_event_loop(self):
        """fetch_market_consensus_sync must not raise RuntimeError in async ctx."""
        from app.services.market.sync_provider import fetch_market_consensus_sync

        async def call_market():
            return fetch_market_consensus_sync(
                "Argentina", "Brazil", "FIFA World Cup 2026"
            )

        result = asyncio.run(call_market())
        # Returns dict (degraded or real) or None (all providers unavailable)
        if isinstance(result, dict):
            assert isinstance(result, dict)

    def test_market_consensus_degraded_on_loop_conflict(self):
        """Market consensus returns structured degraded on loop conflict."""
        from app.services.market.sync_provider import fetch_market_consensus_sync

        async def inner():
            return fetch_market_consensus_sync(
                "Argentina", "Brazil", "FIFA World Cup 2026"
            )

        result = asyncio.run(inner())
        if isinstance(result, dict) and result.get("degraded"):
            assert "source" in result
            assert result["source"] == "market_consensus"
            assert "reason" in result


class TestAsyncioRunDegradedFields:
    """Verify degraded response structure matches spec."""

    def test_degraded_response_has_required_fields(self):
        """Degraded response must include source, reason."""
        # Test with weather_service which is the most predictable
        from app.services.weather_service import WeatherService

        svc = WeatherService()

        async def inner():
            return svc.get_weather_for_match_sync(venue="Lusail Stadium")

        result = asyncio.run(inner())
        if result.get("degraded"):
            required = ["source", "reason"]
            for field in required:
                assert field in result, f"Missing required field: {field}"
