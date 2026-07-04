"""Tests for multi-bookmaker 1X2 consensus extraction."""

from __future__ import annotations

import pytest

from app.services.market.bookmaker_consensus import consensus_from_bookmakers


def _event():
    return {
        "home_team": "Brazil",
        "away_team": "Germany",
        "bookmakers": [
            {
                "key": "a",
                "title": "Book A",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Brazil", "price": 2.00},
                            {"name": "Draw", "price": 3.20},
                            {"name": "Germany", "price": 3.80},
                        ],
                    }
                ],
            },
            {
                "key": "b",
                "title": "Book B",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Brazil", "price": 1.90},
                            {"name": "Draw", "price": 3.30},
                            {"name": "Germany", "price": 4.10},
                        ],
                    }
                ],
            },
            {
                "key": "c",
                "title": "Book C",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Brazil", "price": 2.10},
                            {"name": "Draw", "price": 3.10},
                            {"name": "Germany", "price": 3.60},
                        ],
                    }
                ],
            },
        ],
    }


def test_consensus_uses_median_odds_across_bookmakers():
    result = consensus_from_bookmakers(
        _event(),
        "Brazil",
        "Germany",
        provider="the-odds-api",
    )

    assert result is not None
    assert result["sample_bookmakers"] == 3
    assert result["bookmaker"] == "3-bookmaker-consensus"
    assert result["home_odds"] == pytest.approx(2.00)
    assert result["draw_odds"] == pytest.approx(3.20)
    assert result["away_odds"] == pytest.approx(3.80)
    assert sum(result[key] for key in ("home_prob", "draw_prob", "away_prob")) == pytest.approx(1.0)


def test_consensus_rejects_insufficient_bookmaker_count():
    result = consensus_from_bookmakers(
        _event(),
        "Brazil",
        "Germany",
        provider="the-odds-api",
        min_bookmakers=4,
    )

    assert result is None
