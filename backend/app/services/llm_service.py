from __future__ import annotations

import asyncio
import hashlib
import json
import re
from abc import ABC, abstractmethod
from datetime import timedelta
from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.logging import get_logger
from app.models import Match, NewsArticle, NewsSignal
from app.models.enums import ImpactDirection, ReviewStatus, SignalType
from app.services.team_resolver import TeamResolver
from app.utils.datetime import utc_now

settings = get_settings()
logger = get_logger(__name__)


class LLMAdapter(ABC):
    @abstractmethod
    async def chat(self, system: str, user: str, response_format: str = "text") -> str:
        raise NotImplementedError


class OpenAICompatibleAdapter(LLMAdapter):
    def __init__(self, base_url: str, api_key: str | None, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def chat(self, system: str, user: str, response_format: str = "text") -> str:
        if not self.api_key:
            raise RuntimeError("LLM_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class QwenAdapter(OpenAICompatibleAdapter):
    def __init__(self) -> None:
        super().__init__(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )


class DeepSeekAdapter(OpenAICompatibleAdapter):
    def __init__(self) -> None:
        # DeepSeek OpenAI-compatible API: base URL should NOT include /v1.
        # The adapter appends /v1 internally so the config value stays clean.
        _base = (settings.llm_base_url or "https://api.deepseek.com").rstrip("/")
        super().__init__(
            base_url=f"{_base}/v1",
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )


class ZhipuAdapter(OpenAICompatibleAdapter):
    def __init__(self) -> None:
        super().__init__(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )


def get_llm_adapter() -> LLMAdapter:
    if settings.llm_provider == "deepseek":
        return DeepSeekAdapter()
    if settings.llm_provider == "qwen":
        return QwenAdapter()
    if settings.llm_provider == "zhipu":
        return ZhipuAdapter()
    # Fallback to DeepSeek (default provider)
    return DeepSeekAdapter()


def _extract_json_payload(raw: str) -> Any:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        fence_match = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        array_match = re.search(r"\[[\s\S]*\]", cleaned)
        if array_match:
            return json.loads(array_match.group(0))
        object_match = re.search(r"\{[\s\S]*\}", cleaned)
        if object_match:
            return json.loads(object_match.group(0))
        raise


class SignalExtractorService:
    SYSTEM_PROMPT = """系统提示词：
你是足球赛前情报抽取器。你的任务不是写分析文章，而是把新闻、采访、
公告中的事实抽取为结构化JSON。
输出必须是合法JSON，不要输出任何解释文字。
若信息不确定，保留uncertainty和evidence。
若文章没有相关信息，返回 {"has_signal": false}。"""

    USER_PROMPT_TEMPLATE = """比赛：{home_team} vs {away_team}
原文来源：{source_name}
发布时间：{published_at}
正文：
{article_text}

请抽取以下字段：
- has_signal: boolean
- signal_type: injury/suspension/starting_xi_tendency/rotation/
               coach_quote/training_status/travel/weather/other
- team（球队名）
- player（球员名，没有则null）
- claim（简短描述这条信息的核心内容）
- normalized_availability: available/doubtful/out/suspended/
                            likely_start/likely_bench/unknown
- expected_minutes_delta: 整数（预计上场时间变化，缺阵约-90，轮换约-45，null=不确定）
- confidence: 0~1
- evidence_snippet: 原文中支持该结论的关键句子（不超过100字）
- effective_until: ISO时间字符串（这条信息在什么时间前有效，null=不确定）
- contradiction_risk: low/medium/high
- impact_direction: positive/negative/neutral/uncertain
- summary_zh: 30字内中文摘要"""

    def __init__(self, adapter: LLMAdapter | None = None) -> None:
        self.adapter = adapter or get_llm_adapter()
        self.team_resolver = TeamResolver()

    async def extract_signals(self, article: NewsArticle, db: AsyncSession) -> list[NewsSignal]:
        match_context = await self._resolve_match_context(article, db)
        user_prompt = self.USER_PROMPT_TEMPLATE.format(
            home_team=match_context["home_team"],
            away_team=match_context["away_team"],
            source_name=article.source_name or "unknown",
            published_at=article.published_at.isoformat() if article.published_at else "unknown",
            article_text=article.content[:4000],
        )
        try:
            raw = await self.adapter.chat(self.SYSTEM_PROMPT, user_prompt, response_format="json")
            payload = _extract_json_payload(raw)
        except Exception as exc:
            logger.warning("LLM extraction failed for article %s: %s", article.id, exc)
            payload = []

        created: list[NewsSignal] = []
        if isinstance(payload, dict):
            if payload.get("has_signal") is False:
                payload = []
            else:
                payload = [payload]
        elif not isinstance(payload, list):
            payload = []

        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("has_signal") is False:
                continue

            signal_type = self._canonical_signal_type(str(item.get("signal_type", "other")))
            impact = self._normalize_impact_direction(str(item.get("impact_direction", "uncertain")))
            confidence = self._normalize_confidence(item.get("confidence"))
            team = await self.team_resolver.resolve_team(item.get("team"), db)
            match = await self._resolve_match(team.id if team else None, article.published_at, db)
            player_name = self._trim_optional(item.get("player"), 100)
            claim = self._trim_optional(item.get("claim"), 300)
            evidence_snippet = self._trim_optional(item.get("evidence_snippet"), 300)
            summary_zh = self._trim_optional(item.get("summary_zh"), 200) or claim or article.title[:120]
            normalized_availability = self._normalize_availability(item.get("normalized_availability"))
            contradiction_risk = self._normalize_contradiction_risk(item.get("contradiction_risk"))
            signal = NewsSignal(
                article_id=article.id,
                match_id=match.id if match else None,
                team_id=team.id if team else None,
                signal_type=SignalType(signal_type),
                impact_direction=ImpactDirection(impact),
                confidence=confidence,
                key_players=[player_name] if player_name else [],
                summary_zh=summary_zh,
                player_name=player_name,
                claim=claim,
                evidence_snippet=evidence_snippet,
                normalized_availability=normalized_availability,
                expected_minutes_delta=self._normalize_minutes(item.get("expected_minutes_delta")),
                effective_until=self._parse_datetime(item.get("effective_until")),
                contradiction_risk=contradiction_risk,
                source_reliability=await self.team_resolver.lookup_source_reliability(article.source_url, db),
                review_status=ReviewStatus.PENDING,
                enters_model=False,
            )
            db.add(signal)
            created.append(signal)
        article.is_processed = True
        await db.commit()
        for signal in created:
            await db.refresh(signal)
        return created

    async def process_unprocessed_articles(self, db: AsyncSession, batch_size: int = 10) -> None:
        batch_limit = max(1, min(batch_size, 10))
        total_processed = 0
        touched_matches: set[UUID] = set()
        while True:
            result = await db.execute(
                select(NewsArticle)
                .where(NewsArticle.is_processed.is_(False))
                .order_by(NewsArticle.created_at.asc())
                .limit(batch_limit)
            )
            batch = result.scalars().all()
            if not batch:
                break
            for article in batch:
                created = await self.extract_signals(article, db)
                touched_matches.update(signal.match_id for signal in created if signal.match_id is not None)
                total_processed += 1
            logger.info("Processed %s article(s) in current LLM run", total_processed)
            if len(batch) == batch_limit:
                await asyncio.sleep(2)
        for match_id in touched_matches:
            await self.detect_and_group_conflicts(match_id, db)

    async def resolve_conflicts(self, match_id: UUID, signal_type: str, db: AsyncSession) -> str:
        result = await db.execute(
            select(NewsSignal)
            .where(NewsSignal.match_id == match_id, NewsSignal.signal_type == signal_type)
            .order_by(NewsSignal.created_at.desc())
        )
        signals = result.scalars().all()
        if len(signals) <= 1:
            return "No conflict detected."

        system = "你是足球情报仲裁员。请判断哪条消息更可信，并说明理由。"
        user = "\n".join(
            [
                f"- {signal.summary_zh} | confidence={signal.confidence} | source={signal.source_reliability}"
                for signal in signals
            ]
        )
        try:
            return await self.adapter.chat(system, user, response_format="text")
        except Exception as exc:
            logger.warning("Conflict resolution failed for match %s: %s", match_id, exc)
            return "Conflict detected but LLM arbitration is unavailable."

    async def detect_and_group_conflicts(
        self,
        match_id: UUID,
        db: AsyncSession,
    ) -> int:
        result = await db.execute(
            select(NewsSignal)
            .where(
                NewsSignal.match_id == match_id,
                NewsSignal.review_status == ReviewStatus.PENDING,
            )
            .order_by(NewsSignal.created_at.asc())
        )
        signals = result.scalars().all()
        grouped: dict[tuple[str, str], list[NewsSignal]] = {}
        for signal in signals:
            if signal.impact_direction not in {ImpactDirection.POSITIVE, ImpactDirection.NEGATIVE}:
                continue
            anchor = signal.player_name or (str(signal.team_id) if signal.team_id else None)
            if not anchor:
                continue
            grouped.setdefault((anchor.strip().lower(), str(signal.signal_type)), []).append(signal)

        conflict_count = 0
        for (anchor, signal_type), entries in grouped.items():
            directions = {str(item.impact_direction) for item in entries}
            if not {"positive", "negative"}.issubset(directions):
                continue
            suffix = hashlib.sha256(
                f"{anchor}:{signal_type}".encode("utf-8")
            ).hexdigest()[:8]
            conflict_group_id = f"{str(match_id)[:8]}_{suffix}"
            for entry in entries:
                entry.conflict_group_id = conflict_group_id
                entry.contradiction_risk = "high"
            conflict_count += 1
        if conflict_count:
            await db.commit()
        return conflict_count

    async def _resolve_match(
        self,
        team_id: UUID | None,
        published_at,
        db: AsyncSession,
    ) -> Match | None:
        if team_id is None or published_at is None:
            return None
        result = await db.execute(
            select(Match)
            .where(
                (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
                Match.match_date >= published_at - timedelta(days=3),
                Match.match_date <= published_at + timedelta(days=14),
            )
            .order_by(Match.match_date.asc())
        )
        return result.scalars().first()

    async def _resolve_match_context(self, article: NewsArticle, db: AsyncSession) -> dict[str, str]:
        result = await db.execute(
            select(Match)
            .options(selectinload(Match.home_team), selectinload(Match.away_team))
            .where(
                Match.match_date >= (article.published_at or utc_now()) - timedelta(days=3),
                Match.match_date <= (article.published_at or utc_now()) + timedelta(days=14),
            )
            .order_by(Match.match_date.asc())
            .limit(1)
        )
        match = result.scalar_one_or_none()
        if match is None:
            return {"home_team": "未知主队", "away_team": "未知客队"}
        return {
            "home_team": match.home_team.name if match.home_team else "未知主队",
            "away_team": match.away_team.name if match.away_team else "未知客队",
        }

    def _canonical_signal_type(self, raw_value: str) -> str:
        normalized = (raw_value or "").strip().lower()
        mapping = {
            "injury": SignalType.INJURY.value,
            "suspension": SignalType.INJURY.value,
            "starting_xi_tendency": SignalType.LINEUP_HINT.value,
            "rotation": SignalType.LINEUP_HINT.value,
            "coach_quote": SignalType.COACH_STATEMENT.value,
            "training_status": SignalType.TRAINING.value,
            "travel": SignalType.TRAVEL.value,
            "weather": SignalType.WEATHER.value,
            "other": SignalType.OTHER.value,
            "return": SignalType.RETURN.value,
            "lineup_hint": SignalType.LINEUP_HINT.value,
            "coach_statement": SignalType.COACH_STATEMENT.value,
            "training": SignalType.TRAINING.value,
        }
        return mapping.get(normalized, SignalType.OTHER.value)

    def _normalize_impact_direction(self, raw_value: str) -> str:
        normalized = (raw_value or "").strip().lower()
        if normalized in {member.value for member in ImpactDirection}:
            return normalized
        return ImpactDirection.UNCERTAIN.value

    def _normalize_availability(self, value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        allowed = {"available", "doubtful", "out", "suspended", "likely_start", "likely_bench", "unknown"}
        return normalized if normalized in allowed else None

    def _normalize_contradiction_risk(self, value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in {"low", "medium", "high"} else None

    def _normalize_confidence(self, value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            numeric = 0.5
        return max(0.0, min(1.0, numeric))

    def _normalize_minutes(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _trim_optional(self, value: Any, max_length: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:max_length] if text else None

    def _parse_datetime(self, value: Any) -> datetime | None:
        if value in (None, "", "null"):
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
