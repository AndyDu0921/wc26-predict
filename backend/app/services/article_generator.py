from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging import get_logger
from app.models import ArticleEvidence, ContentArticle, NewsSignal, PredictionRun
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMAdapter, get_llm_adapter
from app.utils.datetime import utc_now

logger = get_logger(__name__)


class ArticleGeneratorService:
    ARTICLE_SYSTEM_PROMPT = """你是一名专业的足球分析记者。
根据提供的预测数据、情报信号和原始证据片段，撰写简洁、准确的赛前分析文章。
重要规则：
- 只能使用 provided data 中的信息，不能编造
- 每个关键判断必须对应一条证据
- 使用“据[来源]报道”格式引用证据
- 可以引用市场赔率/多平台共识作为证据，但不得写成投注建议、带单或保证性结论
文章结构：结论段 → 数据支撑 → 情报证据 → 风险提示 → 更新说明
字数：350-500字"""

    def __init__(self, adapter: LLMAdapter | None = None) -> None:
        self.adapter = adapter or get_llm_adapter()
        self.embedding_service = EmbeddingService()

    async def generate_article(
        self,
        prediction_run: PredictionRun,
        approved_signals: list[NewsSignal],
        db: AsyncSession,
    ) -> ContentArticle:
        existing_count = await db.scalar(
            select(func.count()).select_from(ContentArticle).where(ContentArticle.prediction_run_id == prediction_run.id)
        ) or 0

        title = f"赛前预测：{prediction_run.match.home_team.name} vs {prediction_run.match.away_team.name}"
        evidence_items = await self.build_evidence_chain(prediction_run, approved_signals, db)
        signal_lines = "\n".join(
            [
                f"- {signal.summary_zh} | type={signal.signal_type} | confidence={signal.confidence:.2f}"
                for signal in approved_signals
            ]
        ) or "- 暂无已审核情报信号"
        evidence_lines = "\n".join(
            [
                f"- 据{item.article.source_name or '相关媒体'}报道：{item.evidence_snippet}"
                for item in evidence_items
                if item.article is not None
            ]
        ) or "- 暂无可用证据链条目"

        prompt = f"""比赛：{prediction_run.match.home_team.name} vs {prediction_run.match.away_team.name}
时间：{prediction_run.match.match_date.isoformat()}
胜平负概率：主胜 {prediction_run.home_win_prob:.2%} / 平 {prediction_run.draw_prob:.2%} / 客胜 {prediction_run.away_win_prob:.2%}
期望进球：{prediction_run.match.home_team.name} {prediction_run.home_xg:.2f}，{prediction_run.match.away_team.name} {prediction_run.away_xg:.2f}
Top3 比分：{prediction_run.top3_scores}
风险标签：{prediction_run.risk_tags}
已审核信号：
{signal_lines}

以下是从原始报道中检索到的相关证据，请在文章中引用：
{evidence_lines}

请只根据这些 provided data 写一篇 350-500 字中文分析文章。"""

        try:
            body = await self.adapter.chat(self.ARTICLE_SYSTEM_PROMPT, prompt, response_format="text")
        except Exception as exc:
            logger.warning("LLM article generation failed for run %s: %s", prediction_run.id, exc)
            body = self._build_fallback_article(prediction_run, approved_signals, evidence_items)

        article = ContentArticle(
            match_id=prediction_run.match_id,
            prediction_run_id=prediction_run.id,
            title=title,
            body=body.strip(),
            article_version=existing_count + 1,
            is_published=False,
            correction_log=[
                {
                    "action": "generated",
                    "at": utc_now().isoformat(),
                    "signal_count": len(approved_signals),
                }
            ],
        )
        db.add(article)
        for evidence in evidence_items:
            evidence.used_in_article = True
        await db.commit()
        await db.refresh(article)
        return article

    async def build_evidence_chain(
        self,
        prediction_run: PredictionRun,
        approved_signals: list[NewsSignal],
        db: AsyncSession,
    ) -> list[ArticleEvidence]:
        await self.embedding_service.batch_embed_articles(db, batch_size=20)
        query_specs = [
            {
                "query": (
                    f"{prediction_run.match.home_team.name} vs {prediction_run.match.away_team.name} "
                    f"home win {prediction_run.home_win_prob:.2f} draw {prediction_run.draw_prob:.2f} "
                    f"away win {prediction_run.away_win_prob:.2f}"
                ),
                "signal_id": None,
            }
        ]
        query_specs.extend(
            {
                "query": f"{prediction_run.match.home_team.name} {prediction_run.match.away_team.name} {signal.claim or signal.summary_zh}",
                "signal_id": signal.id,
            }
            for signal in approved_signals[:4]
        )

        created_items: list[ArticleEvidence] = []
        seen: set[tuple[str, str]] = set()
        for spec in query_specs:
            matches = await self.embedding_service.search_similar(
                spec["query"],
                prediction_run.match_id,
                db,
                top_k=2,
                min_relevance=0.45,
            )
            for match in matches:
                dedupe_key = (str(match["article_id"]), match["snippet"])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                evidence = ArticleEvidence(
                    match_id=prediction_run.match_id,
                    prediction_run_id=prediction_run.id,
                    article_id=match["article_id"],
                    signal_id=spec["signal_id"],
                    evidence_snippet=match["snippet"],
                    relevance_score=match["relevance_score"],
                    used_in_article=False,
                )
                db.add(evidence)
                created_items.append(evidence)
        if created_items:
            await db.flush()
            refreshed_result = await db.execute(
                select(ArticleEvidence)
                .options(selectinload(ArticleEvidence.article), selectinload(ArticleEvidence.signal))
                .where(ArticleEvidence.id.in_([item.id for item in created_items]))
                .order_by(ArticleEvidence.relevance_score.desc())
            )
            return refreshed_result.scalars().all()
        return created_items

    def _build_fallback_article(
        self,
        prediction_run: PredictionRun,
        approved_signals: list[NewsSignal],
        evidence_items: list[ArticleEvidence],
    ) -> str:
        home = prediction_run.match.home_team.name
        away = prediction_run.match.away_team.name
        top_score = prediction_run.top3_scores[0]["score"] if prediction_run.top3_scores else "1:0"
        signal_summary = "；".join(signal.summary_zh for signal in approved_signals[:3]) or "目前暂无已审核信号进入模型。"
        evidence_summary = "；".join(
            f"据{item.article.source_name or '相关媒体'}报道：{item.evidence_snippet}"
            for item in evidence_items[:3]
            if item.article is not None
        ) or "目前没有足够的证据链条目。"
        return (
            f"结论段：本场模型更看好{home}，主胜概率为{prediction_run.home_win_prob:.1%}，"
            f"平局概率为{prediction_run.draw_prob:.1%}，客胜概率为{prediction_run.away_win_prob:.1%}。"
            f"当前最可能比分为{top_score}。\n\n"
            f"数据支撑：模型给出的期望进球为{home} {prediction_run.home_xg:.2f}，{away} {prediction_run.away_xg:.2f}。"
            f"这说明双方仍存在一定拉锯空间，但{home}在基础强度上略占优。\n\n"
            f"情报证据：{evidence_summary}\n\n"
            f"风险提示：{signal_summary}\n\n"
            f"更新说明：本文基于 {prediction_run.run_type} 时点生成，后续若有新的首发或伤停审核信号，结论会同步更新。"
        )
