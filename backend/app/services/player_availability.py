"""Player availability shadow component.

This is a research-only component: it translates player availability records
into auditable xG modifier candidates, but it does not mutate production
probabilities or weights.  The prediction pipeline can store this payload as
shadow evidence; BacktestGate must approve any future production use.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.services.injury_data import InjuryDataService, InjuryRecord


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

STATUS_MINUTES_DELTA = {
    "out": -90.0,
    "suspended": -90.0,
    "injured": -90.0,
    "doubtful": -45.0,
    "probable": -15.0,
    "available": 0.0,
    "fit": 0.0,
}
IMPORTANCE_MULTIPLIER = {
    "key": 1.0,
    "starter": 0.75,
    "rotation": 0.40,
    "backup": 0.15,
    "unknown": 0.35,
}
POSITION_ATTACK_XG = {
    "forward": 0.12,
    "midfielder": 0.07,
    "winger": 0.10,
    "unknown": 0.06,
}
POSITION_OPPONENT_XG = {
    "goalkeeper": 0.10,
    "defender": 0.08,
    "midfielder": 0.03,
}


@dataclass(frozen=True)
class PlayerImpactAdjustment:
    player_name: str
    team_name: str
    availability_status: str
    expected_minutes_delta: float
    importance_level: str
    position_group: str
    xg_modifier: float
    opponent_xg_modifier: float
    confidence: float
    source_status: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlayerAvailabilitySnapshot:
    home_team: str
    away_team: str
    home_xg_modifier: float
    away_xg_modifier: float
    adjustments: list[PlayerImpactAdjustment]
    source_status: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["adjustments"] = [item.to_dict() for item in self.adjustments]
        return payload


def build_player_availability_shadow(
    home_team: str,
    away_team: str,
    *,
    injury_records: list[InjuryRecord | dict[str, Any]] | None = None,
    player_catalog: list[dict[str, Any]] | None = None,
    db_path: str | Path | None = None,
) -> PlayerAvailabilitySnapshot:
    """Build a shadow xG adjustment payload from availability records."""
    records = injury_records
    if records is None:
        records = InjuryDataService().load()
    catalog = player_catalog
    if catalog is None:
        catalog = _load_player_catalog(db_path or DEFAULT_DB_PATH, home_team, away_team)

    if not records:
        return PlayerAvailabilitySnapshot(
            home_team=home_team,
            away_team=away_team,
            home_xg_modifier=0.0,
            away_xg_modifier=0.0,
            adjustments=[],
            source_status={
                "status": "unavailable",
                "reason": "empty_availability_dataset",
                "shadow_only": True,
            },
        )

    relevant = [
        _coerce_record(record)
        for record in records
        if _coerce_record(record).get("team_name", "").lower() in {home_team.lower(), away_team.lower()}
    ]
    if not relevant:
        return PlayerAvailabilitySnapshot(
            home_team=home_team,
            away_team=away_team,
            home_xg_modifier=0.0,
            away_xg_modifier=0.0,
            adjustments=[],
            source_status={
                "status": "unavailable",
                "reason": "no_relevant_availability_records",
                "loaded_records": len(records),
                "shadow_only": True,
            },
        )

    catalog_by_key = {
        (_norm(item.get("team_name")), _norm(item.get("player_name") or item.get("name"))): item
        for item in catalog or []
    }
    adjustments: list[PlayerImpactAdjustment] = []
    home_delta = 0.0
    away_delta = 0.0

    for record in relevant:
        team_name = str(record.get("team_name", ""))
        player_name = str(record.get("player_name", ""))
        status = str(record.get("status", "unknown")).lower()
        confidence = _clamp(float(record.get("confidence", 0.6) or 0.6), 0.0, 1.0)
        player_meta = catalog_by_key.get((_norm(team_name), _norm(player_name)), {})
        importance = _importance_level(record, player_meta)
        position = _position_group(record.get("position") or player_meta.get("position"))
        minutes_delta = float(record.get("expected_minutes_delta") or STATUS_MINUTES_DELTA.get(status, 0.0))
        availability_scale = min(1.0, abs(minutes_delta) / 90.0)
        importance_scale = IMPORTANCE_MULTIPLIER.get(importance, IMPORTANCE_MULTIPLIER["unknown"])

        attack_impact = 0.0 if position in {"goalkeeper", "defender"} else POSITION_ATTACK_XG.get(
            position, POSITION_ATTACK_XG["unknown"]
        )
        own_xg_modifier = -attack_impact
        own_xg_modifier *= availability_scale * importance_scale * confidence
        opponent_xg_modifier = POSITION_OPPONENT_XG.get(position, 0.0)
        opponent_xg_modifier *= availability_scale * importance_scale * confidence

        if minutes_delta >= 0:
            own_xg_modifier = 0.0
            opponent_xg_modifier = 0.0

        adjustment = PlayerImpactAdjustment(
            player_name=player_name,
            team_name=team_name,
            availability_status=status,
            expected_minutes_delta=round(minutes_delta, 2),
            importance_level=importance,
            position_group=position,
            xg_modifier=round(own_xg_modifier, 4),
            opponent_xg_modifier=round(opponent_xg_modifier, 4),
            confidence=round(confidence, 4),
            source_status="used",
            source=str(record.get("source", "unknown")),
        )
        adjustments.append(adjustment)

        if team_name.lower() == home_team.lower():
            home_delta += adjustment.xg_modifier
            away_delta += adjustment.opponent_xg_modifier
        elif team_name.lower() == away_team.lower():
            away_delta += adjustment.xg_modifier
            home_delta += adjustment.opponent_xg_modifier

    return PlayerAvailabilitySnapshot(
        home_team=home_team,
        away_team=away_team,
        home_xg_modifier=round(_clamp(home_delta, -0.25, 0.20), 4),
        away_xg_modifier=round(_clamp(away_delta, -0.25, 0.20), 4),
        adjustments=adjustments,
        source_status={
            "status": "used" if adjustments else "unavailable",
            "reason": "shadow_adjustments_computed" if adjustments else "no_adjustments",
            "loaded_records": len(records),
            "relevant_records": len(relevant),
            "shadow_only": True,
        },
    )


def _load_player_catalog(db_path: str | Path, home_team: str, away_team: str) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                p.name AS player_name,
                p.position,
                p.is_key_player,
                p.importance_level,
                p.status,
                t.name AS team_name
            FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE lower(t.name) IN (?, ?)
            """,
            (home_team.lower(), away_team.lower()),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _coerce_record(record: InjuryRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, InjuryRecord):
        return {
            "player_name": record.player_name,
            "team_name": record.team_name,
            "status": record.status,
            "confidence": record.confidence,
            "source": record.source,
        }
    return dict(record)


def _importance_level(record: dict[str, Any], player_meta: dict[str, Any]) -> str:
    raw = record.get("importance_level") or player_meta.get("importance_level")
    if raw:
        value = str(raw).lower()
    elif bool(player_meta.get("is_key_player")):
        value = "key"
    else:
        value = "unknown"
    return value if value in IMPORTANCE_MULTIPLIER else "unknown"


def _position_group(raw: Any) -> str:
    value = str(raw or "").lower()
    if any(token in value for token in ("gk", "goalkeeper", "keeper")):
        return "goalkeeper"
    if any(token in value for token in ("cb", "lb", "rb", "def", "back")):
        return "defender"
    if any(token in value for token in ("fw", "st", "striker", "forward")):
        return "forward"
    if any(token in value for token in ("wing", "lw", "rw")):
        return "winger"
    if any(token in value for token in ("mid", "dm", "cm", "am")):
        return "midfielder"
    return "unknown"


def _norm(raw: Any) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
