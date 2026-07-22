from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest


def test_tournament_simulation_fails_closed_without_placeholder_probabilities(
    monkeypatch,
):
    from scripts import simulate_wc26

    monkeypatch.setattr(
        simulate_wc26,
        "load_active_bundle",
        lambda: {
            "bundle_id": "test-bundle",
            "components": {"dixon_coles": {}},
        },
    )
    monkeypatch.setattr(
        simulate_wc26,
        "load_dc",
        lambda: SimpleNamespace(attack_params={"A": 0.0}),
    )
    monkeypatch.setattr(
        simulate_wc26,
        "load_training_df",
        lambda: pd.DataFrame(
            [{"match_date": pd.Timestamp("2026-06-01T00:00:00Z")}]
        ),
    )
    monkeypatch.setattr(
        simulate_wc26,
        "get_weight_config",
        lambda *_: SimpleNamespace(
            dc=1.0,
            enhancer=0.0,
            weibull=0.0,
            elo=0.0,
            pi=0.0,
            market_max=0.0,
        ),
    )
    monkeypatch.setattr(
        simulate_wc26,
        "load_group_teams",
        lambda: {"A": ["A", "B", "C", "D"]},
    )
    monkeypatch.setattr(
        simulate_wc26,
        "predict_group_match",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model failed")),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["simulate_wc26.py", "--mode", "baseline", "--runs", "1"],
    )

    with pytest.raises(RuntimeError, match="no placeholder probabilities"):
        simulate_wc26.main()
