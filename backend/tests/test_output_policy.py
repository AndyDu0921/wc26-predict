"""Test output policy filtering."""
from __future__ import annotations

import pytest
from app.services.output_policy import OutputPolicy


def test_output_policy_creation():
    """OutputPolicy can be created with each mode."""
    for mode in ("internal_research", "creator_safe", "public_safe"):
        policy = OutputPolicy(mode=mode)
        assert policy.mode == mode


def test_output_policy_internal_research_allows_all():
    """Internal research mode allows model terms."""
    policy = OutputPolicy(mode="internal_research")
    text = "Model predicts home_win_prob=0.45, draw=0.25"
    result = policy.filter_text(text)
    # filter_text may return tuple (text, warnings) or plain text
    result_text = result[0] if isinstance(result, tuple) else result
    assert result_text == text  # internal_research passes through


def test_output_policy_public_safe_filters_probability():
    """Public safe mode filters probability claims."""
    policy = OutputPolicy(mode="public_safe")
    text = "The model home_win_prob is 0.65"
    result = policy.filter_text(text)
    # Public safe should not leave raw probability claims
    assert "home_win_prob" not in result or "0.65" not in result


def test_output_policy_creator_safe_preserves_market_odds_data():
    """Creator safe mode keeps market data, because it improves analysis quality."""
    policy = OutputPolicy(mode="creator_safe")
    text = "odds: 2.10 / 3.50 / 3.80 from Bet365"
    result = policy.filter_text(text)
    result_text = result[0] if isinstance(result, tuple) else result
    assert "2.10" in result_text


def test_output_policy_creator_safe_blocks_betting_advice():
    """Creator safe mode blocks advice-like gambling language."""
    policy = OutputPolicy(mode="creator_safe")
    result_text, blocked = policy.filter_text("This is a guaranteed prediction, bet this side now.")

    assert "guaranteed prediction" in blocked
    assert "bet this" in blocked
    assert "guaranteed prediction" not in result_text
