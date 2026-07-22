"""Read-only World Cup Monte Carlo simulation dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pandas as pd
import streamlit as st

from dashboard.dashboard_config import (
    DEFAULT_SIMULATION_RUNS,
    GROUPS,
    PREDICTION_MODES,
    SIMULATION_RUNS_OPTIONS,
)
from dashboard.db import db


st.title("赛事模拟器")
st.caption("FIFA World Cup 2026 Monte Carlo 研究视图")

col1, col2 = st.columns(2)
with col1:
    runs = st.selectbox(
        "模拟次数",
        SIMULATION_RUNS_OPTIONS,
        index=SIMULATION_RUNS_OPTIONS.index(DEFAULT_SIMULATION_RUNS),
        key="sim_runs",
    )
with col2:
    sim_mode = st.selectbox(
        "模型模式",
        ["standard", "full"],
        index=1,
        key="sim_mode",
        format_func=lambda item: PREDICTION_MODES.get(item, item),
    )


def _result_frame(results, groups_frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for team in sorted(groups_frame["team_name"].unique()):
        group_values = groups_frame[groups_frame["team_name"] == team]["group_name"].values
        group_name = group_values[0] if len(group_values) else "?"
        probability = results.get(team)
        if probability is None:
            raise RuntimeError(f"Simulation produced no result for {team}")
        rows.append(
            {
                "球队": team,
                "小组": group_name,
                "小组第一": f"{probability.group_win_prob * 100:.1f}%",
                "小组出线": f"{probability.advance_prob * 100:.1f}%",
                "32 强": f"{probability.round_of_32_prob * 100:.1f}%",
                "16 强": f"{probability.round_of_16_prob * 100:.1f}%",
                "8 强": f"{probability.quarter_final_prob * 100:.1f}%",
                "4 强": f"{probability.semi_final_prob * 100:.1f}%",
                "决赛": f"{probability.final_prob * 100:.1f}%",
                "冠军": f"{probability.champion_prob * 100:.1f}%",
            }
        )
    frame = pd.DataFrame(rows)
    frame["_sort"] = frame["冠军"].str.rstrip("%").astype(float)
    return frame.sort_values("_sort", ascending=False).drop(columns=["_sort"])


if st.button("开始模拟", type="primary", key="sim_run"):
    try:
        from app.services.artifact_bundle import load_active_bundle
        from app.services.tournament_simulator import TournamentSimulator
        from app.services.weights import get_weight_config
        from scripts.simulate_wc26 import (
            MODE_REQUIRED_COMPONENTS,
            load_dc,
            load_elo,
            load_enhancer,
            load_pi,
            load_training_df,
            load_weibull,
            predict_group_match,
        )

        bundle = load_active_bundle()
        available = set((bundle.get("components") or {}).keys())
        missing = [
            component
            for component in MODE_REQUIRED_COMPONENTS[sim_mode]
            if component not in available
        ]
        if missing:
            raise RuntimeError(f"Active artifact bundle缺少组件: {missing}")

        groups_data = db.get_wc26_groups()
        schedule = db.get_wc26_schedule()
        if not groups_data or not schedule:
            raise RuntimeError("本地数据库缺少完整小组或赛程数据")
        groups_frame = pd.DataFrame(groups_data)
        group_matches = [
            match
            for match in schedule
            if match.get("stage") == "Group Stage"
            or str(match.get("stage", "")).startswith("Group")
        ]
        if not group_matches:
            raise RuntimeError("本地数据库没有明确标记的小组赛")
        incomplete = [
            match.get("match_number")
            for match in group_matches
            if not match.get("home_team") or not match.get("away_team")
        ]
        if incomplete:
            raise RuntimeError(f"小组赛球队槽位不完整: {incomplete}")

        progress = st.progress(0)
        status = st.empty()
        training_df = load_training_df()
        dc = load_dc()
        enhancer = load_enhancer() if sim_mode in ("standard", "full") else None
        elo = load_elo() if sim_mode in ("standard", "full") else None
        pi_model = load_pi() if sim_mode == "full" else None
        weibull = load_weibull(training_df) if sim_mode in ("standard", "full") else None
        group_weights = get_weight_config("FIFA World Cup 2026", "Group Stage")
        knockout_weights = get_weight_config("FIFA World Cup 2026", "Knockout")

        def resolve_matchup(home: str, away: str, is_group: bool) -> dict[str, float]:
            weights = group_weights if is_group else knockout_weights
            probabilities = predict_group_match(
                dc,
                enhancer,
                elo,
                pi_model,
                weibull,
                training_df,
                home,
                away,
                sim_mode,
                weights,
                enable_market=is_group,
            )
            return {
                "home_win": probabilities["home_win_prob"],
                "draw": probabilities["draw_prob"],
                "away_win": probabilities["away_win_prob"],
            }

        simulator = TournamentSimulator(runs=runs, seed=42)
        group_teams = {
            group_name: groups_frame[groups_frame["group_name"] == group_name][
                "team_name"
            ].tolist()
            for group_name in GROUPS
        }
        simulator.load_teams(group_teams)
        simulator.schedule = {0: {"stage": "Group Stage"}}
        simulator.set_probability_resolver(resolve_matchup)

        for index, match in enumerate(group_matches):
            home = str(match["home_team"])
            away = str(match["away_team"])
            probabilities = resolve_matchup(home, away, True)
            simulator.set_match_probability(home, away, probabilities, is_group=True)
            progress.progress((index + 1) / len(group_matches))
            status.text(f"预测 {index + 1}/{len(group_matches)}: {home} vs {away}")

        status.text(f"运行 {runs:,} 次模拟")
        results_frame = _result_frame(simulator.run(), groups_frame)
        status.text(f"模拟完成: {runs:,} 次")
        st.subheader("夺冠概率 Top 10")
        st.dataframe(results_frame.head(10), use_container_width=True, hide_index=True)
        st.subheader("全部球队")
        st.dataframe(results_frame, use_container_width=True, hide_index=True, height=600)
        st.session_state["cached_sim_results"] = results_frame
        st.session_state["cached_sim_runs_count"] = runs
    except Exception as exc:
        st.error(f"模拟失败: {exc}")
elif "cached_sim_results" in st.session_state:
    st.dataframe(
        st.session_state["cached_sim_results"],
        use_container_width=True,
        hide_index=True,
        height=600,
    )
