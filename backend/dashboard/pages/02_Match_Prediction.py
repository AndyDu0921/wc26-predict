"""02_Match_Prediction.py — 单场预测页面 (V2.6 Enhanced)"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st

from dashboard.dashboard_config import PREDICTION_MODES, DEFAULT_MODE
from dashboard.db import db
from dashboard.components.probability_charts import (
    render_probability_gauge,
    render_scoreline_bars,
    render_xg_comparison,
    render_model_breakdown,
)
from dashboard.components.fusion_graph_view import render_fusion_graph
from dashboard.components.run_quality_panel import render_run_quality, render_timings


st.title("单场概率预览")
st.caption("Canonical core 只读预览 + 市场赔率 + 天气 + AI 解释")

# ── 输入表单 ──────────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns([2, 2, 1])

with col1:
    teams = db.get_teams()
    home_team = st.selectbox(
        "主队",
        teams,
        index=teams.index("Spain") if "Spain" in teams else 0,
        key="pred_home",
    )

with col2:
    away_team = st.selectbox(
        "客队",
        teams,
        index=teams.index("Iraq") if "Iraq" in teams else 1,
        key="pred_away",
    )

with col3:
    is_neutral = st.checkbox("中立场地", value=True, key="pred_neutral")

col1b, col2b, col3b = st.columns([2, 1, 1])
with col1b:
    competition = st.text_input("赛事名称", value="International Friendly", key="pred_competition")
with col2b:
    mode = st.selectbox(
        "预测模式",
        list(PREDICTION_MODES.keys()),
        format_func=lambda m: PREDICTION_MODES[m],
        index=list(PREDICTION_MODES.keys()).index(DEFAULT_MODE),
        key="pred_mode",
    )
with col3b:
    enhanced_mode = st.checkbox("增强模式", value=True, key="pred_enhanced",
                                help="启用市场赔率融合 + 天气 + AI 分析")

col1c, col2c, col3c, col4c = st.columns([2, 2, 2, 1])
with col1c:
    match_id = st.text_input("match_id", value="", key="pred_match_id")
with col2c:
    venue = st.text_input("场地", value="", key="pred_venue")
with col3c:
    match_date = st.text_input("开球时间 ISO", value="", key="pred_match_date")
with col4c:
    strict_full = st.checkbox("严格完整", value=False, key="pred_strict_full")

# ── 校验 ──────────────────────────────────────────────────────────────────────
can_predict = home_team != away_team
if not can_predict:
    st.warning("请选择不同的主队和客队")

# ── Enhanced mode ─────────────────────────────────────────────────────────────

def _run_enhanced(home_team, away_team, competition, is_neutral, mode):
    """Run enhanced prediction with market, weather, and LLM."""

    from app.services.prediction_enhanced import (
        run_enhanced_prediction,
        enhanced_result_to_dict,
    )

    # ── Phase 1: Base artifact prediction ──
    with st.status("正在运行增强预测...", expanded=True) as status:
        st.write("🔬 运行基础模型 (Dixon-Coles + Enhancer + Elo + Pi)...")
        try:
            enhanced = run_enhanced_prediction(
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                is_neutral=is_neutral,
                mode=mode,
                enable_market=True,
                enable_weather=True,
                enable_llm=True,
                match_id=match_id or None,
                match_date=match_date or None,
                venue=venue or None,
                require_full_context=strict_full,
            )
        except Exception as e:
            st.error(f"预测失败: {e}")
            st.stop()

        # Store a flat result for Creator Mode, with enhanced fields at top level.
        session_dict = enhanced_result_to_dict(enhanced)
        st.session_state["last_prediction"] = {
            **session_dict,
            "result": session_dict,
            "quality": enhanced.base_quality,
            "timings": enhanced.timings.to_dict() if enhanced.timings else {},
            "total_seconds": enhanced.total_seconds,
        }
        # Also store the enhanced object for direct access
        st.session_state["last_enhanced"] = enhanced

        status.update(label=f"预测完成 — {enhanced.total_seconds:.1f}s", state="complete")

    # ── Results layout ──────────────────────────────────────────────────────
    result = enhanced.base_result

    st.divider()
    st.markdown("### ⚽ 预测结果")

    # Status tags
    tag_cols = st.columns(4)
    with tag_cols[0]:
        if enhanced.market_divergence_triggered:
            st.warning(f"⚠️ 市场分歧 {enhanced.market_divergence*100:.1f}pp")
        elif enhanced.market_probs:
            st.success("✅ 市场数据已接入")
    with tag_cols[1]:
        if enhanced.weather and enhanced.weather.get("forecast_available"):
            st.info(f"🌤 {enhanced.weather.get('weather_description', '?')} {enhanced.weather.get('temperature_c', '?')}°C")
    with tag_cols[2]:
        if enhanced.is_ai_analyzed:
            st.success("🤖 AI 分析已生成")
    with tag_cols[3]:
        st.caption(f"⏱ {enhanced.total_seconds:.1f}s")

    left, right = st.columns([3, 2])

    with left:
        # ── Final probabilities (after market blend) ──
        st.subheader("最终概率" + (" (含市场融合)" if enhanced.market_blended else ""))
        render_probability_gauge(
            enhanced.final_home_prob,
            enhanced.final_draw_prob,
            enhanced.final_away_prob,
            home_team,
            away_team,
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric(f"{home_team} 胜", f"{enhanced.final_home_prob * 100:.1f}%")
        with c2:
            st.metric("平局", f"{enhanced.final_draw_prob * 100:.1f}%")
        with c3:
            st.metric(f"{away_team} 胜", f"{enhanced.final_away_prob * 100:.1f}%")

        # ── Market comparison ──
        if enhanced.market_probs:
            st.subheader("市场赔率对比")
            mc1, mc2, mc3 = st.columns(3)
            mp = enhanced.market_probs
            with mc1:
                delta_h = (enhanced.final_home_prob - mp["home_prob"]) * 100
                st.metric(
                    f"{home_team} (市场)",
                    f"{mp['home_prob']*100:.1f}%",
                    delta=f"{delta_h:+.1f}pp vs 模型",
                )
            with mc2:
                delta_d = (enhanced.final_draw_prob - mp["draw_prob"]) * 100
                st.metric(
                    "平局 (市场)",
                    f"{mp['draw_prob']*100:.1f}%",
                    delta=f"{delta_d:+.1f}pp vs 模型",
                )
            with mc3:
                delta_a = (enhanced.final_away_prob - mp["away_prob"]) * 100
                st.metric(
                    f"{away_team} (市场)",
                    f"{mp['away_prob']*100:.1f}%",
                    delta=f"{delta_a:+.1f}pp vs 模型",
                )
            st.caption(f"数据来源: {mp.get('provider', 'unknown')}")

        # ── xG ──
        st.subheader("预期进球 (xG)")
        render_xg_comparison(result["home_xg"], result["away_xg"], home_team, away_team)

        # ── Top scores ──
        st.subheader("最可能比分")
        render_scoreline_bars(result.get("top_scores", []))

        # ── Model breakdown ──
        st.subheader("各模型独立预测")
        fg_data = result.get("fusion_graph", {})
        steps = fg_data.get("steps", [])
        if steps:
            component_probs = {}
            name_map = {
                "dixon_coles": "Dixon-Coles",
                "enhancer": "XGBoost 增强器",
                "elo": "Elo 评级",
                "pi_rating": "Pi 评级",
                "weibull": "Weibull",
            }
            for step in steps:
                before = step.get("before", {})
                for comp_name, probs in before.items():
                    if comp_name not in component_probs and len(probs) == 3:
                        display = name_map.get(comp_name, comp_name)
                        component_probs[display] = {"home": probs[0], "draw": probs[1], "away": probs[2]}
            if component_probs:
                render_model_breakdown(component_probs)

        # ── Weather detail ──
        if enhanced.weather and enhanced.weather.get("forecast_available"):
            st.subheader("天气信息")
            w = enhanced.weather
            wc1, wc2, wc3, wc4 = st.columns(4)
            with wc1:
                st.metric("天气", w.get("weather_description", "?"))
            with wc2:
                st.metric("温度", f"{w.get('temperature_c', '?')}°C")
            with wc3:
                st.metric("风速", f"{w.get('wind_speed_kmh', '?')} km/h")
            with wc4:
                st.metric("湿度", f"{w.get('humidity_percent', '?')}%")
            if enhanced.weather_impact_tags:
                st.info(f"天气影响: {' | '.join(enhanced.weather_impact_tags)}")

    with right:
        st.subheader("管线状态")
        if enhanced.base_quality:
            render_run_quality(enhanced.base_quality)

        st.subheader("性能耗时")
        if enhanced.timings:
            render_timings(enhanced.timings.to_dict(), enhanced.timings.total())

        st.subheader("融合诊断")
        render_fusion_graph(fg_data)

        st.subheader("风险标签")
        if enhanced.risk_tags:
            for tag in enhanced.risk_tags:
                st.warning(tag)
        else:
            st.success("无特殊风险标签")

        st.subheader("已启用组件")
        all_components = list(enhanced.components_used)
        if enhanced.market_probs:
            all_components.append("market_odds")
        if enhanced.weather and enhanced.weather.get("forecast_available"):
            all_components.append("weather")
        if enhanced.is_ai_analyzed:
            all_components.append("llm_analysis")
        st.json(all_components)

        st.subheader("数据源状态")
        _render_source_status(enhanced.source_status)

    # ── AI Analysis ──────────────────────────────────────────────────────────
    if enhanced.is_ai_analyzed:
        st.divider()
        st.markdown("### 🤖 AI 赛前分析 (DeepSeek V4 Pro)")

        tab1, tab2, tab3 = st.tabs(["赛前分析", "视频脚本", "社交媒体文案"])

        with tab1:
            if enhanced.llm_analysis:
                st.markdown(enhanced.llm_analysis)
            else:
                st.info("AI 分析未生成")

        with tab2:
            if enhanced.llm_video_script:
                st.markdown(enhanced.llm_video_script)
            else:
                st.info("视频脚本未生成")

        with tab3:
            if enhanced.llm_social_copy:
                st.markdown(enhanced.llm_social_copy)
            else:
                st.info("社交媒体文案未生成")

    elif enhanced.llm_error:
        st.warning(f"AI 分析生成失败: {enhanced.llm_error}")


# ── Artifact-only mode ────────────────────────────────────────────────────────

def _run_artifact_only(home_team, away_team, competition, is_neutral, mode):
    """Run a read-only preview through the canonical model adapter."""
    from app.services.canonical_prediction_core import (
        PredictionInvocation,
        execute_prediction_core,
    )
    from app.services.run_quality import RunQuality
    from app.services.prediction_timer import PredictionTimer

    with st.spinner("正在运行预测管线..."):
        try:
            result = execute_prediction_core(PredictionInvocation(
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                is_neutral=is_neutral,
                mode=mode,
                match_id=match_id or "",
                kickoff_at=match_date or None,
                enable_market=False,
                enable_weather=False,
                venue=venue or None,
                save_snapshot=False,
            ))
            # Compatibility: build flat dict + quality + timer for existing render functions
            result_dict = result.to_dict()
            pred = result_dict["prediction"]
            flat_result = {
                "home_team": result.home_team,
                "away_team": result.away_team,
                "competition": result.competition,
                "is_neutral": result.is_neutral,
                "match_id": result.match_id,
                "match_date": result.match_date,
                "mode": result.mode,
                "home_win_prob": pred["home_win_prob"],
                "draw_prob": pred["draw_prob"],
                "away_win_prob": pred["away_win_prob"],
                "home_xg": pred["home_xg"],
                "away_xg": pred["away_xg"],
                "top_scores": pred.get("top_scores", []),
                "components_used": list(result.components_used),
                "component_probs": result_dict.get("component_probs", {}),
                "fusion_graph": result_dict.get("fusion_graph", {}),
                "risk_tags": list(result.risk_tags),
                "degraded_reasons": result_dict.get("degraded_reasons", []),
                "missing_inputs": result_dict.get("missing_inputs", []),
                "source_status": result_dict.get("source_status", {}),
            }
            quality = RunQuality()
            quality.pipeline_status = "full"
            for c in result.components_used:
                quality.model_components[c] = "loaded_from_artifact"
            timer = PredictionTimer()
            timer._steps = {}
            timer._start = 0.0
            st.session_state["last_prediction"] = {
                "result": flat_result,
                "quality": quality,
                "timings": timer.to_dict(),
                "total_seconds": 0.0,
                "is_ai_analyzed": False,
                "market_probs": result.market_probs,
                "weather": None,
            }
        except Exception as e:
            st.error(f"预测失败: {e}")
            st.stop()

    st.divider()
    st.markdown("### :large_blue_circle: 基础模型预测")

    # Show data completeness status
    degraded = result_dict.get("degraded_reasons", [])
    missing = result_dict.get("missing_inputs", [])
    if degraded or missing:
        missing_str = ", ".join([d.get("source", "unknown") for d in degraded] + list(missing))
        st.caption(f"⚠️ 数据不完整: {missing_str}")
    else:
        st.caption("首发阵容: 未获取 | 天气: 未查询 | 市场数据: 未查询")

    left, right = st.columns([3, 2])

    with left:
        pred = result_dict["prediction"]
        st.subheader("胜平负概率")
        render_probability_gauge(
            pred["home_win_prob"], pred["draw_prob"], pred["away_win_prob"],
            home_team, away_team,
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric(f"{home_team} 胜", f"{pred['home_win_prob'] * 100:.1f}%")
        with c2:
            st.metric("平局", f"{pred['draw_prob'] * 100:.1f}%")
        with c3:
            st.metric(f"{away_team} 胜", f"{pred['away_win_prob'] * 100:.1f}%")

        st.subheader("预期进球 (xG)")
        render_xg_comparison(pred["home_xg"], pred["away_xg"], home_team, away_team)

        st.subheader("最可能比分")
        render_scoreline_bars(pred.get("top_scores", []))

        st.subheader("各模型独立预测")
        comp = result_dict.get("component_probs", {})
        component_probs = {}
        name_map = {
            "dc": "Dixon-Coles",
            "enhancer": "XGBoost 增强器",
            "elo": "Elo 评级",
            "pi_rating": "Pi 评级",
            "weibull": "Weibull",
        }
        for comp_name, probs in comp.items():
            if probs and isinstance(probs, dict) and "home" in probs:
                display = name_map.get(comp_name, comp_name)
                component_probs[display] = probs
        if component_probs:
            render_model_breakdown(component_probs)

    with right:
        st.subheader("管线状态")
        render_run_quality(quality)

        st.subheader("组件详情")
        st.markdown(f"**活跃组件:** {', '.join(result.components_used)}")
        if result.risk_tags:
            st.markdown("**风险标签:**")
            for tag in result.risk_tags:
                st.markdown(f"- {tag}")

        st.subheader("融合诊断")
        fg_data = result_dict.get("fusion_graph", {})
        render_fusion_graph(fg_data)

        st.subheader("已启用组件")
        st.json(result.components_used)

        st.subheader("数据源状态")
        _render_source_status(result_dict.get("source_status", {}))


def _render_source_status(source_status: dict) -> None:
    """Render structured data-source status for completeness checks."""
    if not source_status:
        st.caption("暂无数据源状态")
        return

    rows = []
    for source, item in source_status.items():
        if not isinstance(item, dict):
            continue
        rows.append({
            "source": source,
            "status": item.get("status", "unknown"),
            "attempted": item.get("attempted", False),
            "required": item.get("required", False),
            "reason": item.get("reason", ""),
        })
    st.dataframe(rows, hide_index=True, use_container_width=True)


# ── 运行预测 (placed after function definitions to avoid NameError) ──────────
if st.button("生成预览", type="primary", disabled=not can_predict):
    if enhanced_mode:
        _run_enhanced(home_team, away_team, competition, is_neutral, mode)
    else:
        _run_artifact_only(home_team, away_team, competition, is_neutral, mode)

    st.divider()
    st.caption("免责声明: 本内容仅用于足球研究和内容创作，不构成投注建议，也不保证比赛结果。")
