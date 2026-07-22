"""01_Overview.py — 系统总览页面"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import streamlit as st

from dashboard.dashboard_config import VERSION, EXPECTED_MATCHES
from dashboard.db import db
from dashboard.components.metric_cards import render_metric_row


def _count_tests() -> int:
    tests_dir = BACKEND_DIR / "tests"
    if not tests_dir.exists():
        return 0
    return len(list(tests_dir.glob("test_*.py")))


st.title("系统总览")
st.caption("WC26 Predict 系统当前状态")

# ── 顶部指标行 ────────────────────────────────────────────────────────────────
try:
    stats = db.get_db_stats()
    test_count = _count_tests()

    render_metric_row(
        [
            ("历史比赛", str(stats.get("matches", "?")), None),
            ("球队数量", str(stats.get("teams", "?")), None),
            ("WC26 赛程", f"{stats.get('wc26_schedule', '?')}/{EXPECTED_MATCHES}", None),
            ("测试文件", str(test_count), None),
        ],
        columns=4,
    )
except Exception as e:
    st.error(f"无法加载数据库统计: {e}")

# ── 模型文件状态 ──────────────────────────────────────────────────────────────
st.subheader("模型文件")
try:
    from app.services.artifact_bundle import load_active_bundle, verified_artifact_path

    bundle = load_active_bundle()
    components = bundle.get("components", {})
    registered_at = bundle.get("registered_at", "未注册")
    training_data = bundle.get("training_data", {})
    fingerprint = training_data.get("fingerprint", "?")

    if components:
        cols = st.columns(4)
        name_map = {
            "dixon_coles": "Dixon-Coles",
            "tabular_enhancer": "XGBoost 增强器",
            "elo": "Elo 评级",
            "pi_rating": "Pi 评级",
            "weibull": "Weibull（可选）",
        }
        for i, (comp_name, comp_info) in enumerate(components.items()):
            try:
                verified_artifact_path(comp_name)
                status = "已校验"
                icon = ":white_check_mark:"
            except Exception:
                status = "不可用"
                icon = ":x:"
            display_name = name_map.get(comp_name, comp_name)
            with cols[i % 4]:
                st.metric(
                    label=f"{icon} {display_name}",
                    value=status,
                    delta=str(comp_info.get("sha256", ""))[:12],
                )
    else:
        st.warning("未找到已训练的模型。请先运行 `python scripts/train_models.py`")

    st.caption(
        f"Bundle: {bundle.get('bundle_id', '?')} | 注册: {registered_at} | "
        f"数据指纹: {fingerprint}"
    )
except Exception as e:
    st.error(f"无法加载模型注册表: {e}")

# ── 数据库表 ──────────────────────────────────────────────────────────────────
st.subheader("数据库表")
try:
    tables = db.get_tables()
    table_data = []
    for t in sorted(tables):
        try:
            count = db.get_row_count(t)
            table_data.append({"表名": t, "行数": count})
        except Exception:
            table_data.append({"表名": t, "行数": "?"})

    midpoint = len(table_data) // 2 + 1
    col1, col2 = st.columns(2)
    with col1:
        st.dataframe(table_data[:midpoint], use_container_width=True, hide_index=True)
    with col2:
        st.dataframe(table_data[midpoint:], use_container_width=True, hide_index=True)
    st.caption(f"共 {len(tables)} 张表")
except Exception as e:
    st.error(f"无法列出数据库表: {e}")

# ── 版本信息 ──────────────────────────────────────────────────────────────────
st.subheader("版本信息")
st.json({
    "版本": VERSION,
    "推理模式": "artifact-inference（离线训练 → 本地加载）",
    "预测模式": ["baseline（基础）", "standard（标准）", "full（完整）", "research-full（研究）"],
    "模型": ["Dixon-Coles", "XGBoost 增强器", "Elo 评级", "Pi 评级", "Weibull（可选）"],
    "数据库": str(db.db_path),
    "编码": "UTF-8（强制）",
})
