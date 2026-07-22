"""Signal extraction service — DeepSeek V4 Pro → structured news_signals.

Reuses existing SignalExtractorService from llm_service.py but adds:
- Schema validation via schemas.py
- Confidence-based routing (low confidence → manual review)
- Extraction audit trail
"""
from __future__ import annotations

import logging

from app.services.llm.deepseek_client import DeepSeekClient
from app.services.llm.schemas import ExtractedSignal, SignalType, ImpactDirection, Severity

logger = logging.getLogger(__name__)

# ── Prompt template for signal extraction ──
EXTRACT_SIGNAL_SYSTEM_PROMPT = """You are a football pre-match intelligence analyst.
Extract structured event signals from news articles that affect upcoming matches.

RULES:
1. Only extract signals with clear, verifiable evidence in the article text.
2. Each signal must have an evidence_quote — the exact sentence from the article.
3. signal_type must be one of: injury, suspension, lineup, rotation, motivation, weather, travel, coach, morale, other
4. confidence: 0.0-1.0. Use 0.7+ for official announcements, 0.5-0.7 for credible reports, <0.5 for rumors.
5. If no relevant football intelligence is found, return {"has_signals": false}
6. All output must be valid JSON.
"""

EXTRACT_SIGNAL_USER_TEMPLATE = """Analyze this news article for pre-match football intelligence.

Match context: {home_team} vs {away_team} ({competition})
Source: {source_name}
Published: {published_at}

Article text:
{article_text}

Extract any signals about injuries, suspensions, lineup changes, rotation hints,
motivation factors, weather concerns, travel fatigue, coaching changes, or team morale.
For each signal, provide the exact evidence_quote from the article text."""


class SignalExtractionService:
    """Extract structured signals from news articles using DeepSeek V4 Pro.

    Thin wrapper around the existing llm_service.py extraction pipeline,
    adding schema validation and confidence-based routing.
    """

    def __init__(self) -> None:
        self._client = DeepSeekClient()

    async def extract_from_article(
        self,
        article_text: str,
        source_name: str = "",
        source_url: str = "",
        published_at: str = "",
        home_team: str = "",
        away_team: str = "",
        competition: str = "",
    ) -> list[ExtractedSignal]:
        """Extract signals from a single article.

        Args:
            article_text: Full article content.
            source_name: Publication name (e.g., "BBC Sport").
            source_url: Article URL.
            published_at: ISO datetime of publication.
            home_team: Home team name (for context).
            away_team: Away team name (for context).
            competition: Competition name (for context).

        Returns:
            List of ExtractedSignal objects (empty if no signals found).
        """
        from datetime import datetime, timezone

        # Truncate article to avoid token limits (DeepSeek has 128K context,
        # but we keep prompts reasonable for speed and cost)
        max_chars = 4000
        truncated = article_text[:max_chars]
        if len(article_text) > max_chars:
            truncated += f"\n\n[... article truncated, {len(article_text) - max_chars} more characters]"

        user_prompt = EXTRACT_SIGNAL_USER_TEMPLATE.format(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            source_name=source_name,
            published_at=published_at,
            article_text=truncated,
        )

        try:
            result = await self._client.extract_json(
                system=EXTRACT_SIGNAL_SYSTEM_PROMPT,
                user=user_prompt,
            )
        except Exception as e:
            logger.warning(f"Signal extraction failed: {e}")
            return []

        if not result.get("has_signals"):
            return []

        signals_data = result.get("signals", [])
        extracted = []

        for item in signals_data:
            try:
                signal = ExtractedSignal(
                    source_url=source_url,
                    source_title=source_name,
                    evidence_quote=str(item.get("evidence_quote", "")),
                    team=str(item.get("team", "")),
                    player=item.get("player"),
                    signal_type=SignalType(item.get("signal_type", "other")),
                    impact_direction=ImpactDirection(item.get("impact_direction", "neutral")),
                    severity=Severity(item.get("severity", "medium")),
                    confidence=float(item.get("confidence", 0.5)),
                    effective_from=item.get("effective_from"),
                    effective_until=item.get("effective_until"),
                    summary_zh=str(item.get("summary_zh", "")),
                    claim=str(item.get("claim", "")),
                    extracted_at=datetime.now(timezone.utc).isoformat(),
                    extraction_model="deepseek-v4-pro",
                )
                extracted.append(signal)
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"Skipping malformed signal: {e} — {item}")

        logger.info(
            f"Extracted {len(extracted)} signals from '{source_name}' "
            f"({len(signals_data)} raw items, {len(extracted)} valid)"
        )
        return extracted

    @staticmethod
    def partition_signals(
        signals: list[ExtractedSignal],
    ) -> tuple[list[ExtractedSignal], list[ExtractedSignal]]:
        """Split signals into model-ready and manual-review queues.

        Returns:
            (model_signals, review_signals)
        """
        model_signals = [s for s in signals if s.enters_model]
        review_signals = [s for s in signals if s.needs_review]
        return model_signals, review_signals
