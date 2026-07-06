"""SignalAdjusterSync — lightweight synchronous signal adjustment for artifact pipeline.

Unlike the async SignalAdjuster (which requires DB for dynamic multipliers and
rebuilds xG matrices), this version applies simple probability-level adjustments
based on approved signals. Designed for the sync prediction_core.py pipeline.

Usage:
    from app.services.signal_adjuster_sync import apply_signal_adjustments

    home_prob, draw_prob, away_prob, risk_tags = apply_signal_adjustments(
        home_prob=0.45, draw_prob=0.25, away_prob=0.30,
        home_team="China PR", away_team="Thailand",
    )
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

# Signal type → max probability shift
ADJUSTMENT_MAX: dict[str, float] = {
    "injury": 0.15,
    "suspension": 0.15,
    "lineup_hint": 0.10,
    "lineup_change": 0.10,
    "travel_fatigue": 0.06,
    "morale_event": 0.04,
    "form_change": 0.08,
    "tactical_shift": 0.06,
    "schedule_pressure": 0.04,
    "manager_change": 0.08,
    "weather_impact": 0.04,
    "return": 0.08,
    "other": 0.03,
}


def load_approved_signals(
    home_team: str,
    away_team: str,
    match_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load APPROVED + enters_model=1 signals relevant to either team.

    Returns list of dicts suitable for apply_signal_adjustments().
    """
    if not DB_PATH.exists():
        return []

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Find team IDs by name
        home_row = conn.execute(
            "SELECT id FROM teams WHERE name = ?", (home_team,)
        ).fetchone()
        away_row = conn.execute(
            "SELECT id FROM teams WHERE name = ?", (away_team,)
        ).fetchone()

        if not home_row and not away_row:
            conn.close()
            return []

        # Find signals matching either team via team_id, or via team name lookup
        placeholders = []
        team_ids = []
        if home_row:
            team_ids.append(home_row["id"])
        if away_row:
            team_ids.append(away_row["id"])

        if not team_ids:
            conn.close()
            return []

        placeholders = ",".join("?" for _ in team_ids)

        scope_clause = (
            "AND (ns.conflict_group_id IS NULL OR ns.conflict_group_id = '' "
            "OR ns.conflict_group_id = ?)"
            if match_id
            else "AND (ns.conflict_group_id IS NULL OR ns.conflict_group_id = '')"
        )
        params = []
        if match_id:
            params.append(f"wc26:{match_id}")
        params.extend(team_ids)

        rows = conn.execute(
            f"""SELECT ns.id, ns.signal_type, ns.impact_direction, ns.confidence,
                       ns.summary_zh, ns.player_name, ns.claim, ns.source_reliability,
                       ns.review_status, ns.enters_model, ns.evidence_id, ns.team_id,
                       t.name as team_name
                FROM news_signals ns
                LEFT JOIN teams t ON ns.team_id = t.id
                WHERE (ns.review_status = 'approved' OR ns.review_status = 'APPROVED')
                  AND ns.enters_model = 1
                  AND ns.evidence_id IS NOT NULL
                  AND (ns.effective_until IS NULL OR ns.effective_until > CURRENT_TIMESTAMP)
                  {scope_clause}
                  AND (ns.team_id IN ({placeholders})
                       OR ns.team_id IS NULL)
                ORDER BY ns.confidence DESC""",
            params,
        ).fetchall()

        conn.close()

        signals = []
        for r in rows:
            sig = {
                "id": r["id"],
                "signal_type": r["signal_type"],
                "impact_direction": r["impact_direction"],
                "confidence": r["confidence"],
                "summary_zh": r["summary_zh"],
                "player_name": r["player_name"],
                "claim": r["claim"],
                "source_reliability": r["source_reliability"],
                "team_name": r["team_name"],
                "evidence_id": r["evidence_id"],
            }
            signals.append(sig)

        return signals

    except Exception:
        logger.debug("Failed to load approved signals", exc_info=True)
        return []


def apply_signal_adjustments(
    *,
    home_prob: float,
    draw_prob: float,
    away_prob: float,
    home_team: str,
    away_team: str,
    match_id: str | None = None,
    signals: list[dict[str, Any]] | None = None,
) -> tuple[float, float, float, list[str]]:
    """Apply approved news signals as probability adjustments.

    Args:
        home_prob, draw_prob, away_prob: Base probabilities (sum ≈ 1.0).
        home_team, away_team: Team names for matching.
        signals: Optional pre-loaded signal list. If None, loads from DB.

    Returns:
        (adjusted_home, adjusted_draw, adjusted_away, risk_tags).
    """
    risk_tags: list[str] = []

    if signals is None:
        signals = load_approved_signals(home_team, away_team, match_id=match_id)

    if not signals:
        return home_prob, draw_prob, away_prob, risk_tags

    # Separate signals by which team they affect
    home_negative = 0.0
    home_positive = 0.0
    away_negative = 0.0
    away_positive = 0.0

    for sig in signals:
        team_name = (sig.get("team_name") or "").lower()
        impact = sig.get("impact_direction", "neutral")
        signal_type = sig.get("signal_type", "other")
        confidence = float(sig.get("confidence", 0.5))
        reliability = float(sig.get("source_reliability", 0.5))

        # How much to shift
        max_shift = ADJUSTMENT_MAX.get(signal_type, 0.03)
        magnitude = max_shift * confidence * min(reliability, 1.0)
        magnitude = min(magnitude, 0.15)  # hard cap at 15%

        if team_name == home_team.lower():
            if impact == "negative":
                home_negative += magnitude
            elif impact == "positive":
                home_positive += magnitude
        elif team_name == away_team.lower():
            if impact == "negative":
                away_negative += magnitude
            elif impact == "positive":
                away_positive += magnitude
        # If team_name doesn't match either, try matching via key_players or claim
        elif team_name:
            logger.debug(f"Signal team '{team_name}' does not match '{home_team}' or '{away_team}' — skipping")
            continue

    # Cap combined adjustments
    home_net = min(home_positive - home_negative, 0.20)
    away_net = min(away_positive - away_negative, 0.20)

    # Apply: shift probability from draw and the other team
    if home_net > 0:
        risk_tags.append("主队有利情报")
    elif home_net < 0:
        risk_tags.append("主队不利情报")
    if away_net > 0:
        risk_tags.append("客队有利情报")
    elif away_net < 0:
        risk_tags.append("客队不利情报")

    new_home = home_prob + home_net * (1.0 - home_prob)
    new_away = away_prob + away_net * (1.0 - away_prob)
    new_draw = home_prob + away_prob + draw_prob - new_home - new_away

    # Ensure non-negative
    new_home = max(0.01, new_home)
    new_draw = max(0.01, new_draw)
    new_away = max(0.01, new_away)

    # Renormalize
    total = new_home + new_draw + new_away
    new_home /= total
    new_draw /= total
    new_away /= total

    if signals:
        logger.info(
            f"  [Signal] {len(signals)} approved signals applied — "
            f"H={new_home:.3f} D={new_draw:.3f} A={new_away:.3f}"
        )

    return new_home, new_draw, new_away, risk_tags
