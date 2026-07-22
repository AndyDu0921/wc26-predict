"""Signal extraction schemas — structured output from DeepSeek V4 Pro.

All extracted signals must conform to this schema before entering the model.
Low-confidence signals are routed to manual review, not the prediction pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SignalType(str, Enum):
    INJURY = "injury"
    SUSPENSION = "suspension"
    LINEUP = "lineup"
    ROTATION = "rotation"
    MOTIVATION = "motivation"
    WEATHER = "weather"
    TRAVEL = "travel"
    COACH = "coach"
    MORALE = "morale"
    OTHER = "other"


class ImpactDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Threshold for entering the model
MIN_CONFIDENCE = 0.5


@dataclass
class ExtractedSignal:
    """A single structured signal extracted from a news article by DeepSeek V4 Pro.

    Every signal must be traceable to its source (source_url, source_title, evidence_quote).
    Signals with confidence < MIN_CONFIDENCE are routed to manual review.
    """

    # Source traceability (required)
    source_url: str = ""
    source_title: str = ""
    evidence_quote: str = ""  # Original text snippet supporting this signal

    # Core signal fields
    team: str = ""  # Team name as it appears in the article
    player: str | None = None  # Player name, if applicable
    signal_type: SignalType = SignalType.OTHER
    impact_direction: ImpactDirection = ImpactDirection.NEUTRAL
    severity: Severity = Severity.MEDIUM
    confidence: float = 0.0  # LLM-estimated confidence [0.0, 1.0]

    # Temporal scope
    effective_from: str | None = None  # ISO datetime
    effective_until: str | None = None  # ISO datetime

    # Summary
    summary_zh: str = ""  # Chinese summary for content generation
    claim: str = ""  # Specific factual claim

    # Extraction metadata
    extracted_at: str = ""  # ISO datetime
    extraction_model: str = "deepseek-v4-pro"

    @property
    def enters_model(self) -> bool:
        """Whether this signal is reliable enough to enter the prediction model."""
        return self.confidence >= MIN_CONFIDENCE

    @property
    def needs_review(self) -> bool:
        """Whether this signal should go to manual review."""
        return self.confidence < MIN_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "player": self.player,
            "signal_type": self.signal_type.value,
            "impact_direction": self.impact_direction.value,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "effective_from": self.effective_from,
            "effective_until": self.effective_until,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "evidence_quote": self.evidence_quote,
            "summary_zh": self.summary_zh,
            "claim": self.claim,
        }


# JSON Schema for DeepSeek structured output (OpenAI-compatible json_object format)
EXTRACTION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "has_signals": {
            "type": "boolean",
            "description": "Whether any football-relevant signals were found in the article"
        },
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "description": "Team name affected"},
                    "player": {"type": ["string", "null"], "description": "Player name if specific"},
                    "signal_type": {
                        "type": "string",
                        "enum": ["injury", "suspension", "lineup", "rotation",
                                  "motivation", "weather", "travel", "coach", "morale", "other"]
                    },
                    "impact_direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "effective_from": {"type": ["string", "null"]},
                    "effective_until": {"type": ["string", "null"]},
                    "evidence_quote": {"type": "string", "description": "Exact text from the article"},
                    "summary_zh": {"type": "string"},
                    "claim": {"type": "string"}
                },
                "required": ["team", "signal_type", "impact_direction", "confidence",
                             "evidence_quote", "summary_zh"]
            }
        }
    },
    "required": ["has_signals", "signals"]
}
