"""LLM-assisted post-match review helpers.

The production post-match path is ``scripts/run_postmatch_complete.py``.
This module only provides reusable narrative generation for Dashboard views
and does not write learning logs, proposals, weights, or artifacts.
"""

from __future__ import annotations

import asyncio

from app.services.postmatch import MatchReview


def generate_ai_review(review: MatchReview) -> str | None:
    """Generate a concise LLM post-match narrative for a completed review."""
    return asyncio.run(generate_ai_review_async(review))


async def generate_ai_review_async(review: MatchReview) -> str | None:
    """Async implementation used by UI and future service callers."""
    try:
        from app.services.llm.deepseek_client import DeepSeekClient
    except ImportError:
        return None

    system_prompt = """你是一名专业的足球分析师和球评人。
你的任务是对比赛前预测和实际比赛结果，撰写赛后复盘分析。

规则:
- 客观分析预测偏差的原因
- 不使用投注建议语言
- 承认模型局限性和足球的不确定性
- 如果预测准确，分析为什么准确
- 如果预测偏差，诚实分析哪里出了问题
- 字数: 300-400字"""

    direction_label = {
        "home": f"{review.home_team} 胜",
        "draw": "平局",
        "away": f"{review.away_team} 胜",
    }

    top_scores = ", ".join(
        f"{s['score']}({s['prob'] * 100:.1f}%)"
        for s in review.pred_top_scores[:3]
    ) if review.pred_top_scores else "N/A"

    user_prompt = f"""请复盘以下比赛:

## 比赛信息
{review.home_team} vs {review.away_team} | {review.competition}
实际比分: {review.actual_score_str} ({direction_label.get(review.actual_outcome, review.actual_outcome)})

## 赛前预测
- {review.home_team} 胜: {review.pred_home_prob * 100:.1f}%
- 平局: {review.pred_draw_prob * 100:.1f}%
- {review.away_team} 胜: {review.pred_away_prob * 100:.1f}%
- 最可能比分: {top_scores}
- xG: {review.home_team} {review.pred_home_xg:.2f} vs {review.away_team} {review.pred_away_xg:.2f}

## 评估指标
- Brier Score: {review.brier_score:.4f} (0=完美)
- 方向正确: {'是' if review.directional_correct else '否'}
- 比分命中: {'是' if review.exact_score_hit else '否'}
- 综合评级: {review.grade}

请从以下角度分析:
1. 预测与实际结果的核心偏差是什么?
2. 哪些赛前因素模型没有捕捉到?
3. 这个偏差对未来类似比赛的预测有什么启示?
4. 模型的概率输出在什么意义上仍然有用(或无用)?

请撰写300-400字的赛后复盘分析。"""

    try:
        client = DeepSeekClient()
        result = await client.chat(
            system=system_prompt,
            user=user_prompt,
            response_format="text",
        )
        return result if result else None
    except Exception:
        return None
