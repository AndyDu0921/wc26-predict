"""analysis_prompts.py — LLM prompt templates for match analysis content generation.

Three prompt families:
1. Pre-match analysis article (350-500 words, Chinese)
2. Video commentary script (segmented with time marks)
3. Social media copy (multi-platform: Xiaohongshu, WeChat, Twitter)

All prompts enforce compliance rules: market odds are allowed as evidence,
but betting advice, guaranteed outcomes, and monetized tip language are not.
"""

from __future__ import annotations
import logging

# ── System prompts ──────────────────────────────────────────────────────────────

MATCH_ANALYSIS_SYSTEM = """你是一名资深足球战术分析师和数据记者，拥有10年从业经验。
你的分析风格: 数据驱动、冷静客观、不煽动情绪、不承诺胜率。

核心规则:
- 只能使用提供的比赛数据，不得编造信息
- 不得声称使用了数据源状态中 status 不是 used 的数据源
- 可以引用赔率/盘口/市场共识作为证据，但不得写成下注建议、稳胆、必中或保证性结论
- 不确定的地方必须明确标注
- 必须包含"模型预测有其局限性"的声明
- 使用专业但易懂的中文
- 字数: 350-500字"""

VIDEO_SCRIPT_SYSTEM = """你是一名专业的体育视频内容创作者，擅长撰写短视频口播脚本。
你的脚本风格: 节奏紧凑、信息密集、适合2-3分钟短视频。

核心规则:
- 脚本分为开场(10秒)、数据拆解(60秒)、看点分析(60秒)、结语(20秒)
- 每段标注建议时长和画面建议
- 信息密度高，不说废话
- 可以引用赔率/盘口/市场共识作为证据，但不得写成下注建议、稳胆、必中或保证性结论
- 结尾必须包含"数据仅供参考"声明
- 口语化但不失专业感"""

SOCIAL_COPY_SYSTEM = """你是一名体育社交媒体运营专家，熟悉小红书、公众号、Twitter等平台的内容特点。
你需要为一场足球比赛的赛前分析撰写多平台文案。

核心规则:
- 小红书: 活泼口语化，善用emoji，150-200字，带话题标签
- 公众号: 正式专业，适合深度阅读，200-300字
- Twitter: 简洁有力，英文为主，280字符以内
- 可以引用赔率/盘口/市场共识作为证据，但不得写成下注建议、稳胆、必中或保证性结论
- 不得承诺比赛结果
- 可以展示数据，但需注明「模型预测」
"""

# ── User prompt templates ────────────────────────────────────────────────────────

MATCH_ANALYSIS_USER = """请为以下比赛撰写赛前分析文章。

## 比赛信息
- 主队: {home_team}
- 客队: {away_team}
- 赛事: {competition}
- 场地: {venue}
- 比赛时间: {match_time}

## 模型预测数据
- 主胜概率: {home_prob:.1%}
- 平局概率: {draw_prob:.1%}
- 客胜概率: {away_prob:.1%}
- 主队预期进球 (xG): {home_xg:.2f}
- 客队预期进球 (xG): {away_xg:.2f}
- 最可能比分: {top_scores}

## 模型诊断
- 预测引擎: 4模型融合 (Dixon-Coles + XGBoost增强器 + Elo评级 + Pi评级)
- 模型分歧度: {disagreement}
- 融合权重: DC 49.6% | Enhancer 39.6% | Elo 5.3% | Pi 5.6%

## 市场参考数据
{market_section}

## 天气信息
{weather_section}

## 数据源状态
{source_status_section}

## 球队背景
{team_context}

请撰写一篇350-500字的中文赛前分析文章，结构如下:
1. 结论段 (1-2句核心判断)
2. 数据解读 (模型预测 + 市场共识)
3. 关键看点 (2-3个战术/数据看点)
4. 不确定性声明 (模型局限 + 数据缺口)
"""

VIDEO_SCRIPT_USER = """请为以下比赛撰写视频口播脚本。

## 比赛
{home_team} vs {away_team} | {competition} | {venue}

## 预测数据
主胜 {home_prob:.1%} | 平 {draw_prob:.1%} | 客胜 {away_prob:.1%}
xG: {home_team} {home_xg:.2f} vs {away_team} {away_xg:.2f}
最可能比分: {top_scores}

## 市场共识
{market_summary}

## 看点提示
{key_points}

## 数据源状态
{source_status_section}

请按以下格式撰写:
【开场】(10秒)
[画面: 双方队徽+比赛信息卡片]
口播稿...

【数据拆解】(60秒)
[画面: 概率仪表盘+xG对比图]
口播稿...

【看点分析】(60秒)
[画面: 球队近期数据/球员特写]
口播稿...

【结语】(20秒)
[画面: 免责声明文字]
口播稿...
"""

SOCIAL_COPY_USER = """请为以下比赛撰写多平台社交媒体文案。

## 比赛
{home_team} vs {away_team} | {competition}

## 核心数据
预测: 主胜 {home_prob:.1%} | 平 {draw_prob:.1%} | 客胜 {away_prob:.1%}
xG: {home_team} {home_xg:.2f} - {away_team} {away_xg:.2f}

请分别撰写:
1. 小红书文案 (150-200字，口语化，带emoji和话题标签)
2. 公众号文案 (200-300字，专业深度风格)
3. Twitter文案 (英文，280字符以内，带hashtag)
"""


# ── Helper to build market section ──────────────────────────────────────────────

def build_market_section(market_probs: dict | None) -> str:
    """Build the market data section for prompts."""
    if market_probs is None:
        return "暂无市场赔率参考数据。"
    return (
        f"- 市场隐含概率 (来自 {market_probs.get('provider', 'unknown')}): "
        f"主胜 {market_probs['home_prob']:.1%} | "
        f"平局 {market_probs['draw_prob']:.1%} | "
        f"客胜 {market_probs['away_prob']:.1%}\n"
        f"- 市场水分 (overround): {market_probs.get('overround', 0):.2%}\n"
        f"- 数据来源: {market_probs.get('bookmaker', market_probs.get('provider', 'unknown'))}"
    )


def build_weather_section(weather: dict | None) -> str:
    """Build the weather data section for prompts."""
    if weather is None or not weather.get("forecast_available"):
        return "暂无比赛场地天气数据。"
    return (
        f"- 天气: {weather.get('weather_description', '未知')}\n"
        f"- 温度: {weather.get('temperature_c', '?')}°C\n"
        f"- 风速: {weather.get('wind_speed_kmh', '?')} km/h\n"
        f"- 湿度: {weather.get('humidity_percent', '?')}%\n"
        f"- 降水: {weather.get('precipitation_mm', 0)} mm"
    )


def build_source_status_section(source_status: dict | None) -> str:
    """Build a compact source-status section for prompts."""
    if not source_status:
        return "- 未记录结构化数据源状态。"

    labels = {
        "match_context": "比赛上下文",
        "market": "市场数据",
        "weather": "天气",
        "injuries": "伤停",
        "news": "新闻信号",
        "lineups": "首发阵容",
    }
    lines = []
    for source in ("match_context", "market", "weather", "injuries", "news", "lineups"):
        item = source_status.get(source)
        if not isinstance(item, dict):
            continue
        label = labels.get(source, source)
        status = item.get("status", "unknown")
        reason = item.get("reason", "")
        attempted = "已尝试" if item.get("attempted") else "未尝试"
        lines.append(f"- {label}: {status} ({attempted}; {reason})")

    return "\n".join(lines) if lines else "- 未记录结构化数据源状态。"


def build_team_context(home_team: str, away_team: str) -> str:
    """Build basic team context (can be extended with DB lookups)."""
    return (
        f"- {home_team}: 数据基于历史比赛表现和当前评级\n"
        f"- {away_team}: 数据基于历史比赛表现和当前评级\n"
        f"- 注意: 未接入实时首发阵容、伤病、或球队内部动态信息"
    )


def build_key_points(result: dict, market_probs: dict | None) -> str:
    """Build key talking points for video script."""
    points = []

    # Model favorite
    h = result["home_win_prob"]
    d = result["draw_prob"]
    a = result["away_win_prob"]
    if h > a and h > d:
        points.append(f"- {result['home_team']} 在模型预测中略占优势 ({h:.1%})")
    elif a > h and a > d:
        points.append(f"- {result['away_team']} 在模型预测中略占优势 ({a:.1%})")
    else:
        points.append(f"- 模型预测本场比赛较为均衡，平局概率不可忽视 ({d:.1%})")

    # xG
    hxg = result["home_xg"]
    axg = result["away_xg"]
    if hxg > axg + 0.5:
        points.append(f"- xG数据显示{result['home_team']}进攻端明显占优 ({hxg:.1f} vs {axg:.1f})")
    elif axg > hxg + 0.5:
        points.append(f"- xG数据显示{result['away_team']}进攻端明显占优 ({axg:.1f} vs {hxg:.1f})")

    # Market comparison
    if market_probs:
        mh = market_probs["home_prob"]
        md = market_probs["draw_prob"]
        ma = market_probs["away_prob"]
        points.append(
            f"- 市场共识: 主胜{mh:.1%}/平{md:.1%}/客胜{ma:.1%}"
            f" ({market_probs.get('provider', 'unknown')})"
        )
        # Divergence check
        max_diff = max(abs(h - mh), abs(d - md), abs(a - ma))
        if max_diff > 0.10:
            points.append(
                f"- ⚠️ 模型与市场存在显著分歧 ({max_diff:.1%})，值得深入分析"
            )

    # Top scores
    scores = result.get("top_scores", [])
    if scores:
        score_str = "、".join(
            f"{s['score']}({s['prob']:.1%})" for s in scores[:3]
        )
        points.append(f"- 最可能比分: {score_str}")

    if not points:
        points.append("- 请根据数据自行提炼看点")

    return "\n".join(points)


def build_market_summary(market_probs: dict | None) -> str:
    """Build a one-line market summary for video scripts."""
    if market_probs is None:
        return "暂无市场数据"
    return (
        f"市场隐含概率: "
        f"主{market_probs['home_prob']:.1%}/"
        f"平{market_probs['draw_prob']:.1%}/"
        f"客{market_probs['away_prob']:.1%}"
    )
