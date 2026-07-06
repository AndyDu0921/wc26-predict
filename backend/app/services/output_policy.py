"""OutputPolicy — enforces content safety rules based on output mode.

Three modes (from action plan Section 1.2):
  internal_research: Full output — probabilities, market debug, odds data.
  creator_safe:      Creator-friendly — keeps market/odds evidence but blocks
                     betting advice and guaranteed-outcome language.
  public_safe:        Public-compliant — additionally hides probabilities,
                     xG, score predictions. Only rankings, history, info.

Usage:
    from app.services.output_policy import OutputPolicy
    policy = OutputPolicy(mode="creator_safe")
    safe_dict = policy.filter_prediction(prediction_result)
"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

from enum import Enum
from typing import Any

from app.services.public_safety_filter import (
    CREATOR_SAFE_FORBIDDEN,
    PUBLIC_SAFE_EXTRA_FORBIDDEN,
    scan_text,
)


class OutputMode(str, Enum):
    INTERNAL = "internal_research"
    CREATOR = "creator_safe"
    PUBLIC = "public_safe"


class OutputPolicy:
    """Enforces output content safety based on mode.

    Rules:
    - creator_safe: market/odds evidence is allowed; betting advice is blocked
    - public_safe: also hides probabilities, xG, score predictions
    """

    def __init__(self, mode: str = "internal_research") -> None:
        self.mode = OutputMode(mode)

    # ── Public API ──────────────────────────────────────────

    def filter_text(self, text: str) -> tuple[str, list[str]]:
        """Filter text based on current mode. Returns (safe_text, blocked_terms)."""
        if self.mode == OutputMode.INTERNAL:
            return text, []

        forbidden = self._get_forbidden_terms()
        findings = scan_text(text, forbidden)
        blocked = [f["term"] for f in findings]

        if not blocked:
            return text, []

        # Replace blocked terms with safe alternatives
        safe = text
        replacements = self._get_replacements()
        for term in set(blocked):
            replacement = replacements.get(term.lower(), "[已过滤]")
            safe = safe.replace(term, replacement)

        return safe, blocked

    def filter_prediction(self, result: dict[str, Any]) -> dict[str, Any]:
        """Filter a prediction result dict for the current mode.

        Args:
            result: Prediction result dict (from snapshot.py / PredictionResult.to_dict())

        Returns:
            Filtered dict safe for the current mode.
        """
        if self.mode == OutputMode.INTERNAL:
            return result

        filtered = dict(result)

        # ── public_safe: strip market/probability fields for broad publishing ──
        if self.mode == OutputMode.PUBLIC:
            pred = filtered.get("prediction", {})
            if isinstance(pred, dict):
                pred.pop("market_applied", None)
                pred.pop("market_weight_used", None)
                pred.pop("divergence", None)

            # Remove market component probs
            comp = filtered.get("component_probs", {})
            if isinstance(comp, dict):
                comp.pop("market", None)

            # Remove odds_info
            filtered.pop("odds_info", None)
            filtered.pop("market_divergence", None)

        # ── public_safe: additionally strip probabilities/xG/score ──
        if self.mode == OutputMode.PUBLIC:
            pred = filtered.get("prediction", {})
            if isinstance(pred, dict):
                pred.pop("home_win_prob", None)
                pred.pop("draw_prob", None)
                pred.pop("away_win_prob", None)
                pred.pop("home_xg", None)
                pred.pop("away_xg", None)
                pred.pop("top_scores", None)
                pred.pop("score_matrix", None)
                pred.pop("over_under", None)

            # Remove all component probs
            comp = filtered.get("component_probs", {})
            if isinstance(comp, dict):
                for key in list(comp.keys()):
                    comp.pop(key, None)

            # Remove elo probabilities
            elo = filtered.get("elo", {})
            if isinstance(elo, dict):
                elo.pop("detail", None)

        return filtered

    def filter_markdown(self, markdown: str) -> tuple[str, list[str]]:
        """Filter a markdown report for the current mode.

        Returns (safe_markdown, blocked_terms_list).
        """
        return self.filter_text(markdown)

    def audit_artifact(
        self, text: str, artifact_type: str, artifact_path: str = ""
    ) -> dict[str, Any]:
        """Audit a single artifact for compliance.

        Returns audit result dict suitable for output_audit_log table.
        """
        blocked_terms: list[str] = []
        if self.mode != OutputMode.INTERNAL:
            safe_text, blocked = self.filter_text(text)
            blocked_terms = blocked

        return {
            "artifact_type": artifact_type,
            "artifact_path": artifact_path,
            "mode": self.mode.value,
            "passed": len(blocked_terms) == 0,
            "blocked_terms": blocked_terms,
        }

    def fact_check_report(
        self, text: str, team_statuses: dict[str, dict] | None = None
    ) -> tuple[bool, list[dict[str, str]]]:
        """Check report text for factual errors against known team statuses.

        Loads team_tournament_status.json if no team_statuses provided.

        Returns (passed: bool, violations: list[dict]).
        Each violation: {"team": str, "status": str, "matched": str, "context": str}
        """
        if team_statuses is None:
            team_statuses = _load_team_statuses()

        if not team_statuses:
            return True, []

        violations: list[dict[str, str]] = []
        teams = team_statuses.get("teams", {})
        forbidden_if_qualified = team_statuses.get(
            "FORBIDDEN_PHRASES_IF_QUALIFIED", []
        )
        forbidden_if_eliminated = team_statuses.get(
            "FORBIDDEN_PHRASES_IF_ELIMINATED", []
        )

        for team_name, info in teams.items():
            status = info.get("status", "unknown")

            if status == "qualified":
                for phrase in forbidden_if_qualified:
                    if phrase.lower() in text.lower():
                        # Extract context: +/- 50 chars around match
                        idx = text.lower().find(phrase.lower())
                        start = max(0, idx - 30)
                        end = min(len(text), idx + len(phrase) + 30)
                        context = text[start:end].replace("\n", " ")
                        violations.append({
                            "team": team_name,
                            "status": "qualified",
                            "matched": phrase,
                            "context": f"...{context}...",
                        })

            elif status == "eliminated":
                for phrase in forbidden_if_eliminated:
                    if phrase.lower() in text.lower():
                        idx = text.lower().find(phrase.lower())
                        start = max(0, idx - 30)
                        end = min(len(text), idx + len(phrase) + 30)
                        context = text[start:end].replace("\n", " ")
                        violations.append({
                            "team": team_name,
                            "status": "eliminated",
                            "matched": phrase,
                            "context": f"...{context}...",
                        })

        passed = len(violations) == 0
        if not passed:
            team_names = {v["team"] for v in violations}
            logger.warning(
                f"[FACT_CHECK_FAILED] Teams {team_names}: "
                f"{len(violations)} factual error(s) detected"
            )
            for v in violations:
                logger.warning(
                    f"  -> Team '{v['team']}' is '{v['status']}' "
                    f"but report says: '{v['matched']}'"
                )

        return passed, violations

    # ── Helpers ─────────────────────────────────────────────

    def _get_forbidden_terms(self) -> list[str]:
        if self.mode == OutputMode.CREATOR:
            return list(CREATOR_SAFE_FORBIDDEN)
        if self.mode == OutputMode.PUBLIC:
            return list(CREATOR_SAFE_FORBIDDEN) + list(PUBLIC_SAFE_EXTRA_FORBIDDEN)
        return []

    @staticmethod
    def _get_replacements() -> dict[str, str]:
        """Replace forbidden terms with compliant alternatives."""
        return {
            "建议投注": "[已过滤]",
            "推荐投注": "[已过滤]",
            "竞彩推荐": "[已过滤]",
            "下注": "[已过滤]",
            "带单": "[已过滤]",
            "推单": "[已过滤]",
            "稳赚": "[已过滤]",
            "必中": "[已过滤]",
            "包赢": "[已过滤]",
            "盈利保证": "[已过滤]",
            "best bet": "[filtered]",
            "betting tips": "[filtered]",
            "bet this": "[filtered]",
            "odds pick": "[filtered]",
            "guaranteed prediction": "[filtered]",
            "sure win": "[filtered]",
            "lock pick": "[filtered]",
            "wager now": "[filtered]",
            "命中率": "准确度",
            "胜率": "表现",
            "概率": "分析",
            "xG": "期望值",
            "expected goals": "expected value",
            "主胜": "主队",
            "客胜": "客队",
            "平局概率": "平局分析",
            "比分预测": "比赛分析",
            "预计比分": "比赛展望",
        }


def _load_team_statuses() -> dict[str, Any] | None:
    """Load team tournament status from JSON fact file."""
    import json
    from pathlib import Path

    # Search for the JSON file in likely locations
    candidates = [
        Path(__file__).resolve().parents[4] / "data" / "team_tournament_status.json",
        Path(__file__).resolve().parents[1] / ".." / ".." / "data" / "team_tournament_status.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

    return None

