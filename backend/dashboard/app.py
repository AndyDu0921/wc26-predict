"""app.py — WC26 Predict 本地工作台 — Streamlit Dashboard 入口"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st

from dashboard.dashboard_config import (
    APP_TITLE,
    SUB_TITLE,
    VERSION,
    PAGE_CONFIG,
)

st.set_page_config(**PAGE_CONFIG)

# ── 侧边栏 ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title(f":soccer: {APP_TITLE}")
    st.caption(f"{SUB_TITLE} v{VERSION}")

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
            timeout=5,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            st.caption(f"提交: `{commit}`")
    except Exception:
        st.caption("提交: 未知")

    st.divider()

    # 快速状态
    try:
        from dashboard.db import db
        stats = db.get_db_stats()
        team_count = stats.get("teams", "?")
        match_count = stats.get("matches", "?")
        schedule_count = stats.get("wc26_schedule", "?")
        st.caption(f"球队: {team_count} | 比赛: {match_count}")
        st.caption(f"WC26 赛程: {schedule_count}")
    except Exception:
        st.caption("数据库: 离线")

    st.divider()

    # 导航
    st.markdown("### 导航")
    st.page_link("app.py", label="首页")
    st.page_link("pages/01_Overview.py", label="1. 系统总览")
    st.page_link("pages/02_Match_Prediction.py", label="2. 单场预测")
    st.page_link("pages/03_Match_Context.py", label="3. 比赛上下文")
    st.page_link("pages/04_WC26_Schedule.py", label="4. WC26 赛程")
    st.page_link("pages/05_Teams_Facts.py", label="5. 球队事实库")
    st.page_link("pages/06_Database_Explorer.py", label="6. 数据库浏览器")
    st.page_link("pages/07_Tournament_Simulator.py", label="7. 赛事模拟器")
    st.page_link("pages/08_Creator_Mode.py", label="8. 创作者模式")
    st.page_link("pages/09_Postmatch_Review.py", label="9. 赛后复盘")

    st.divider()
    st.caption("本地研究工具")
    st.caption("不构成投注建议")

# ── 首页 ──────────────────────────────────────────────────────────────────────
st.title(f":soccer: {APP_TITLE} {SUB_TITLE}")
st.caption(f"版本 {VERSION} | 你的个人 AI 足球研究工作台")

st.markdown("""
### 欢迎使用 WC26 Predict 本地工作台

这是本地足球概率研究工作台。实时赔率、天气、新闻和 LLM 数据源仅在明确配置时调用。

#### 快速导航

| 页面 | 功能 |
|---|---|
| **单场预测** | 跑一次完整的 4 模型融合预测 |
| **WC26 赛程** | 浏览全部 104 场世界杯比赛 |
| **球队事实库** | 查验 48 支已晋级球队的硬事实 |
| **数据库浏览器** | 探索 SQLite 数据库（只读） |
| **赛事模拟器** | 全量 Monte Carlo 世界杯模拟 |
| **创作者模式** | 为录屏和内容制作优化的展示 |

#### 系统架构

```
历史数据 → immutable active bundle
                            ↓
                 Canonical Prediction Core
                            ↓
       DC + Enhancer + NegBin + Weibull + Elo + Pi + Market
                            ↓
               概率输出 (0 token，纯数学计算)
```
""")

# 快速状态卡片
try:
    from dashboard.db import db
    from app.services.artifact_bundle import load_active_bundle

    stats = db.get_db_stats()
    bundle = load_active_bundle()
    trained = bundle.get("registered_at", "未注册")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("历史比赛", stats.get("matches", "?"))
    with col2:
        st.metric("球队数量", stats.get("teams", "?"))
    with col3:
        st.metric("WC26 赛程", f"{stats.get('wc26_schedule', '?')}/104")
    with col4:
        st.metric("Active bundle", str(trained)[:16] if trained else "未注册")

except Exception as e:
    st.warning(f"无法加载系统状态: {e}")
