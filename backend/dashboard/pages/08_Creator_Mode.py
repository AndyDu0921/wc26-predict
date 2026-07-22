"""08_Creator_Mode.py — 创作者模式 (V2.6 — DeepSeek AI 实时生成)"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st

from dashboard.components.creator_cards import (
    render_creator_card,
    render_creator_conclusion,
    render_social_copy,
    render_video_script,
    render_wechat_copy,
)

st.title("创作者模式")
st.caption("AI 驱动的视频口播脚本和社交媒体内容生成")

# ── 检查是否有最近一次预测 ────────────────────────────────────────────────────
last_pred = st.session_state.get("last_prediction")
last_enhanced = st.session_state.get("last_enhanced")

if last_pred is None:
    st.info("暂无预测数据。请先前往 **单场预测** 页面运行一次预测，然后回到此处。")

if last_pred is None:
    st.stop()

# ═══════════════════════════════════════════════════════════════════════════════
# Guard: st.stop() above already stops execution when last_pred is None.
# All remaining code below depends on last_pred being a valid dict.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Extract result data ─────────────────────────────────────────────────────

def _flatten_prediction_payload(payload: dict) -> dict:
    """Normalize legacy/enhanced/canonical prediction payloads for this page."""
    raw = payload.get("result", payload)
    if isinstance(raw, dict) and "meta" in raw and "prediction" in raw:
        meta = raw.get("meta", {})
        pred = raw.get("prediction", {})
        required = ("home_win_prob", "draw_prob", "away_win_prob")
        missing = [key for key in required if key not in pred]
        if missing:
            raise ValueError(f"Prediction payload is incomplete: {missing}")
        return {
            "home_team": meta.get("home_team", ""),
            "away_team": meta.get("away_team", ""),
            "competition": meta.get("competition", ""),
            "is_neutral": meta.get("is_neutral", True),
            "match_id": meta.get("match_id", ""),
            "match_date": meta.get("match_date", ""),
            "mode": meta.get("mode", "internal_research"),
            "home_win_prob": pred["home_win_prob"],
            "draw_prob": pred["draw_prob"],
            "away_win_prob": pred["away_win_prob"],
            "home_xg": pred.get("home_xg", 0.0),
            "away_xg": pred.get("away_xg", 0.0),
            "top_scores": pred.get("top_scores", []),
            "components_used": meta.get("components_used", []),
            "source_status": raw.get("source_status", {}),
        }
    return raw if isinstance(raw, dict) else {}


result = _flatten_prediction_payload(last_pred)

home = result["home_team"]
away = result["away_team"]
comp = result["competition"]
h_prob = result["home_win_prob"] * 100
d_prob = result["draw_prob"] * 100
a_prob = result["away_win_prob"] * 100
home_xg = result["home_xg"]
away_xg = result["away_xg"]

# Check if we have AI-generated content
has_ai_content = last_pred.get("is_ai_analyzed", False)
llm_analysis = last_pred.get("llm_analysis")
llm_video_script = last_pred.get("llm_video_script")
llm_social_copy = last_pred.get("llm_social_copy")

# ── Header ──────────────────────────────────────────────────────────────────
st.markdown("### 今日分析场次")
st.markdown(f"## :soccer: {home} vs {away}")
st.caption(f"{comp} | 中立场地 | "
           f"{'AI 增强预测' if has_ai_content else '基础模型预测'}")
st.divider()

# ── Probability cards ───────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
with col1:
    render_creator_card(f"{home} 胜", f"{h_prob:.1f}%", accent_color="#2E86AB")
with col2:
    render_creator_card("平局", f"{d_prob:.1f}%", accent_color="#A23B72")
with col3:
    render_creator_card(f"{away} 胜", f"{a_prob:.1f}%", accent_color="#F18F01")

st.divider()

# ── xG comparison ───────────────────────────────────────────────────────────
st.subheader("预期进球")
cx1, cx2 = st.columns(2)
with cx1:
    render_creator_card(f"{home} xG", f"{home_xg:.2f}", accent_color="#2E86AB")
with cx2:
    render_creator_card(f"{away} xG", f"{away_xg:.2f}", accent_color="#F18F01")

# ── Top scores ──────────────────────────────────────────────────────────────
st.subheader("最可能比分")
scores = result.get("top_scores", [])
if scores:
    cols = st.columns(min(len(scores[:3]), 4))
    for i, s in enumerate(scores[:3]):
        with cols[i]:
            render_creator_card(s["score"], f"{s['prob'] * 100:.1f}%",
                              accent_color="#00BFFF" if i == 0 else "#888888")

st.divider()

# ── Market data (if available) ──────────────────────────────────────────────
market_probs = last_pred.get("market_probs") or result.get("market_probs")
if market_probs:
    st.subheader("市场共识")
    cm1, cm2, cm3 = st.columns(3)
    with cm1:
        render_creator_card(f"{home} (市场)", f"{market_probs['home_prob']*100:.1f}%",
                          accent_color="#F18F01")
    with cm2:
        render_creator_card("平局 (市场)", f"{market_probs['draw_prob']*100:.1f}%",
                          accent_color="#888888")
    with cm3:
        render_creator_card(f"{away} (市场)", f"{market_probs['away_prob']*100:.1f}%",
                          accent_color="#F18F01")
    st.caption(f"数据来源: {market_probs.get('provider', 'unknown')} | 仅用于研究参考")

st.divider()

# ── Conclusion ──────────────────────────────────────────────────────────────
if has_ai_content and llm_analysis:
    # Use first 2 sentences of AI analysis as conclusion
    sentences = llm_analysis.replace("\n", " ").split("。")
    conclusion = "。".join(sentences[:2]) + "。"
else:
    # Template fallback
    if h_prob > a_prob:
        conclusion = (
            f"WC26 Predict 四模型融合预测显示，{home} 略占优势，胜率 {h_prob:.1f}%。"
            f"但这是基础模型预测——首发阵容、伤病、天气等动态数据尚未接入。"
            f"赛后我将用实际比分进行复盘。"
        )
    else:
        conclusion = (
            f"WC26 Predict 四模型融合预测显示，在这场中立场地比赛中，{away} 略占优势，胜率 {a_prob:.1f}%。"
            f"但这是基础模型预测——首发阵容、伤病、天气等动态数据尚未接入。"
            f"赛后我将用实际比分进行复盘。"
        )

render_creator_conclusion(conclusion)

st.divider()

# ── Content Tools ───────────────────────────────────────────────────────────
st.subheader("内容工具")

tab1, tab2, tab3 = st.tabs(["AI 赛前分析", "视频口播脚本", "社交媒体文案"])

with tab1:
    st.markdown("#### AI 赛前分析文章")

    if has_ai_content and llm_analysis:
        st.markdown(llm_analysis)
        st.caption("由 DeepSeek V4 Pro 基于预测数据、市场赔率和天气信息实时生成")
    else:
        st.info("AI 分析未生成。在单场预测页面启用增强模式以获取 AI 生成的分析文章。")
        st.markdown("#### 模板分析（参考用）")
        template_analysis = (
            f"## {home} vs {away} 赛前分析\n\n"
            f"**比赛背景**\n\n"
            f"本场{comp}将于中立场地进行。"
            f"WC26 Predict 四模型融合预测给出了以下数据:\n\n"
            f"- {home} 胜率: {h_prob:.1f}%\n"
            f"- 平局概率: {d_prob:.1f}%\n"
            f"- {away} 胜率: {a_prob:.1f}%\n\n"
            f"**预期进球**\n\n"
            f"{home} xG {home_xg:.2f} vs {away} xG {away_xg:.2f}\n\n"
            f"**风险提示**\n\n"
            f"此为未接入首发阵容、伤病和天气信息的基础模型预测。"
            f"足球是复杂的——模型可能出错，所有数据仅供参考。\n\n"
            f"赛后本人将结合实际比分进行复盘分析。"
        )
        render_social_copy(template_analysis)

with tab2:
    st.markdown("#### 视频口播脚本")

    if has_ai_content and llm_video_script:
        st.markdown(llm_video_script)
        st.caption("由 DeepSeek V4 Pro 生成 — 包含时间标记和画面建议")
    else:
        st.info("AI 脚本未生成。在单场预测页面启用增强模式以获取 AI 生成的口播脚本。")
        st.markdown("#### 模板脚本（参考用）")
        script = render_video_script(home, away, result)
        render_social_copy(script)

with tab3:
    st.markdown("#### 社交媒体文案")

    if has_ai_content and llm_social_copy:
        st.markdown(llm_social_copy)
        st.caption("由 DeepSeek V4 Pro 生成 — 多平台版本")
    else:
        st.info("AI 文案未生成。在单场预测页面启用增强模式以获取 AI 生成的多平台文案。")
        st.markdown("#### 模板文案（参考用）")
        wechat = render_wechat_copy(home, away, result)
        render_social_copy(wechat)

# ── Regenerate button ───────────────────────────────────────────────────────
st.divider()
col_btn1, col_btn2 = st.columns([1, 3])
with col_btn1:
    if st.button("重新生成 AI 内容", key="regenerate_llm", type="secondary"):
        if last_enhanced is not None:
            with st.spinner("正在调用 DeepSeek V4 Pro 重新生成..."):
                try:
                    from app.services.prediction_enhanced import (
                        run_enhanced_prediction,
                        enhanced_result_to_dict,
                    )
                    enhanced = run_enhanced_prediction(
                        home_team=home,
                        away_team=away,
                        competition=comp,
                        is_neutral=result.get("is_neutral", True),
                        mode=result.get("mode", "full"),
                        enable_market=False,
                        enable_weather=False,
                        enable_llm=True,
                    )
                    session_dict = enhanced_result_to_dict(enhanced)
                    st.session_state["last_prediction"] = {
                        **session_dict,
                        "result": session_dict,
                        "quality": enhanced.base_quality,
                        "timings": enhanced.timings.to_dict() if enhanced.timings else {},
                        "total_seconds": enhanced.total_seconds,
                    }
                    st.session_state["last_enhanced"] = enhanced
                    st.rerun()
                except Exception as e:
                    st.error(f"生成失败: {e}")
        else:
            st.warning("请先运行增强模式预测")
with col_btn2:
    st.caption("重新生成会调用 DeepSeek API，消耗少量 token (~2K tokens/次)")

# ── Technical details (collapsed) ───────────────────────────────────────────
with st.expander("技术详情（参考用）"):
    details = {
        "模型": "4 模型融合 (DC + Enhancer + Elo + Pi)",
        "耗时": f"{last_pred.get('total_seconds', '?')}秒",
        "组件": result.get("components_used", []),
        "预测阶段": "AI 增强预测" if has_ai_content else "基础模型预测",
        "市场数据": "已接入" if market_probs else "未查询",
        "天气": "已查询" if last_pred.get("weather", {}).get("forecast_available") else "未查询",
        "AI 分析": "DeepSeek V4 Pro" if has_ai_content else "模板生成",
        "首发阵容": "未获取",
    }
    st.json(details)

st.divider()
st.caption("免责声明: 本内容仅用于足球研究和内容创作，不构成投注建议，也不保证比赛结果。AI 生成内容基于模型预测数据，可能存在偏差，请理性参考。")
