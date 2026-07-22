"""09_Postmatch_Review.py — 赛后复盘页面 (V2.6)"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st
import pandas as pd

from dashboard.db import db
from dashboard.components.probability_charts import render_probability_gauge


st.title("赛后指标预览")
st.caption("只读比较已保存的赛前快照与输入比分")

# ── Input form ──────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    teams = db.get_teams()
    review_home = st.selectbox(
        "主队", teams,
        index=teams.index("Spain") if "Spain" in teams else 0,
        key="review_home",
    )
with col2:
    review_away = st.selectbox(
        "客队", teams,
        index=teams.index("Iraq") if "Iraq" in teams else 1,
        key="review_away",
    )
with col3:
    review_neutral = st.checkbox("中立场地", value=False, key="review_neutral")

col1b, col2b, col3b = st.columns(3)
with col1b:
    review_comp = st.text_input("赛事", value="International Friendly", key="review_comp")
with col2b:
    review_hg = st.number_input("主队进球", min_value=0, max_value=20, value=1, key="review_hg")
with col3b:
    review_ag = st.number_input("客队进球", min_value=0, max_value=20, value=1, key="review_ag")
can_review = review_home != review_away


def _load_persisted_prediction(home_team: str, away_team: str, competition: str):
    rows = db.query(
        """
        SELECT id, match_id, snapshot_at, competition,
               final_home_prob, final_draw_prob, final_away_prob,
               home_xg, away_xg, top_scores, component_probs
        FROM pre_match_snapshots
        WHERE home_team=? AND away_team=?
          AND (?='' OR competition=?)
        ORDER BY snapshot_at DESC, id DESC
        LIMIT 1
        """,
        (home_team, away_team, competition, competition),
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "snapshot_id": row["id"],
        "match_id": row["match_id"],
        "snapshot_at": row["snapshot_at"],
        "home_team": home_team,
        "away_team": away_team,
        "competition": row["competition"],
        "home_win_prob": float(row["final_home_prob"]),
        "draw_prob": float(row["final_draw_prob"]),
        "away_win_prob": float(row["final_away_prob"]),
        "home_xg": float(row["home_xg"]),
        "away_xg": float(row["away_xg"]),
        "top_scores": json.loads(row["top_scores"] or "[]"),
        "components_used": list(json.loads(row["component_probs"] or "{}").keys()),
    }

# ── Run review ──────────────────────────────────────────────────────────────
if st.button("预览指标", type="primary", disabled=not can_review):
    with st.status("正在计算...", expanded=True) as status:
        # Step 1: Load the immutable pre-match prediction. Recomputing here
        # would train/evaluate with knowledge that only existed after kickoff.
        st.write("📊 读取赛前预测快照...")
        result = _load_persisted_prediction(review_home, review_away, review_comp)
        if result is None:
            st.error("没有可追溯的赛前预测快照，无法执行可信复盘。")
            st.stop()
        result["is_neutral"] = review_neutral
        st.caption(f"Snapshot {result['snapshot_id']} · {result['snapshot_at']}")

        # Step 2: Evaluate
        st.write("📐 计算评估指标...")
        from app.services.postmatch import (
            evaluate_prediction,
            generate_comparison_text,
        )

        review = evaluate_prediction(result, review_hg, review_ag)

        status.update(label="计算完成", state="complete")

    # ── Results ──────────────────────────────────────────────────────────
    st.divider()

    # Grade badge
    grade_color = {
        "A+": "#00C853", "A": "#4CAF50", "B+": "#8BC34A",
        "B": "#FFC107", "C": "#FF9800", "D": "#F44336", "F": "#D50000",
    }
    bg = grade_color.get(review.grade, "#999")

    st.markdown(f"""
    <div style="text-align:center; padding:20px 0;">
        <span style="font-size:14px; color:#888;">综合评级</span><br>
        <span style="font-size:72px; font-weight:900; color:{bg};">{review.grade}</span>
        <br><span style="color:#888;">{review.grade_reason}</span>
    </div>
    """, unsafe_allow_html=True)

    # Score comparison
    sc1, sc2, sc3 = st.columns([1, 1, 1])
    with sc1:
        st.metric(f"{review_home} 预测胜率", f"{review.pred_home_prob*100:.1f}%")
    with sc2:
        st.metric("预测平局率", f"{review.pred_draw_prob*100:.1f}%")
    with sc3:
        st.metric(f"{review_away} 预测胜率", f"{review.pred_away_prob*100:.1f}%")

    # Actual result highlight
    outcome_label = {"home": f"{review_home} 胜", "draw": "平局", "away": f"{review_away} 胜"}
    st.info(f"**实际结果**: {review.actual_score_str} — {outcome_label.get(review.actual_outcome, '?')}")

    st.divider()

    # Probability gauge
    st.subheader("预测 vs 实际")
    render_probability_gauge(
        review.pred_home_prob, review.pred_draw_prob, review.pred_away_prob,
        review_home, review_away,
    )

    st.divider()

    # Metrics table
    st.subheader("评估指标")
    m_left, m_right = st.columns(2)

    with m_left:
        metrics_df = pd.DataFrame([
            {"指标": "Brier Score", "值": f"{review.brier_score:.4f}", "说明": "0=完美, 1=最差"},
            {"指标": "Log Loss", "值": f"{review.log_loss:.4f}", "说明": "越低越好"},
            {"指标": "RPS", "值": f"{review.rps:.4f}", "说明": "越低越好"},
        ])
        st.dataframe(metrics_df, hide_index=True, use_container_width=True)

    with m_right:
        checks_df = pd.DataFrame([
            {"指标": "方向正确", "结果": "✅" if review.directional_correct else "❌"},
            {"指标": "比分命中 (Top1)", "结果": "✅" if review.exact_score_hit else "❌"},
            {"指标": "比分命中 (Top3)", "结果": "✅" if review.top3_score_hit else "❌"},
            {"指标": "xG 误差", "结果": f"{review.xg_error:+.2f}"},
        ])
        st.dataframe(checks_df, hide_index=True, use_container_width=True)

    # Top scores vs actual
    st.subheader("比分预测对比")
    score_cols = st.columns(min(len(review.pred_top_scores[:5]) + 1, 6))
    with score_cols[0]:
        st.markdown(f"""
        <div style="border:2px solid {bg}; border-radius:8px; padding:10px; text-align:center;">
            <div style="font-size:11px; color:#888;">实际比分</div>
            <div style="font-size:24px; font-weight:bold;">{review.actual_score_str}</div>
        </div>
        """, unsafe_allow_html=True)
    for i, s in enumerate(review.pred_top_scores[:5]):
        is_hit = s.get("score") == review.actual_score_str
        border = "#00C853" if is_hit else "#444"
        with score_cols[i + 1]:
            st.markdown(f"""
            <div style="border:1px solid {border}; border-radius:8px; padding:10px; text-align:center;">
                <div style="font-size:11px; color:#888;">预测 #{i+1}</div>
                <div style="font-size:20px; font-weight:bold;">{s.get('score', '?')}</div>
                <div style="font-size:12px; color:#888;">{s.get('prob', 0)*100:.1f}%</div>
            </div>
            """, unsafe_allow_html=True)

    st.divider()

    # Raw comparison text
    with st.expander("完整比较文本"):
        st.markdown(generate_comparison_text(review))

st.divider()
st.caption("这里只读预览单场指标；正式赛后闭环由唯一赛后入口执行。")
