"""Structured pre-match information-state signals.

The signal layer turns locally available pre-match evidence into a replayable
shadow payload.  It never changes production probabilities, and it excludes
records that were not available at the feature snapshot time.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.injury_data import InjuryDataService, InjuryRecord


@dataclass(frozen=True)
class InformationStateSignal:
    signal_id: str
    signal_type: str
    source_status: str
    source: str
    source_url: str | None
    published_at: str | None
    available_at: str | None
    expires_at: str | None
    confidence: float
    affected_team: str
    affected_player: str | None
    availability_status: str | None
    expected_minutes_delta: float | None
    summary: str
    shadow_only: bool
    included_in_strict_features: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_information_state_signals(
    home_team: str,
    away_team: str,
    *,
    as_of_time: str | None,
    kickoff_at: str | None,
    injury_records: list[InjuryRecord | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build leak-aware structured signals for a match.

    Only evidence available at ``as_of_time`` is returned in ``signals``.
    Evidence after kickoff is never considered strict, even if it was loaded
    from a local file.
    """
    records = injury_records
    if records is None:
        records = InjuryDataService().load()

    as_of_dt = _parse_dt(as_of_time)
    kickoff_dt = _parse_dt(kickoff_at)
    teams = {_norm(home_team), _norm(away_team)}
    signals: list[InformationStateSignal] = []
    excluded_future = 0
    excluded_other_team = 0
    malformed = 0

    for raw in records:
        record = _coerce_record(raw)
        team = str(record.get("team_name") or "")
        if _norm(team) not in teams:
            excluded_other_team += 1
            continue

        available_at = _first_present(record.get("available_at"), record.get("last_updated"))
        available_dt = _parse_dt(available_at)
        if available_at and available_dt is None:
            malformed += 1
            continue

        if as_of_dt is not None and available_dt is not None and available_dt > as_of_dt:
            excluded_future += 1
            continue

        strict_ready = (
            available_dt is not None
            and kickoff_dt is not None
            and available_dt <= kickoff_dt
            and (as_of_dt is None or available_dt <= as_of_dt)
        )
        status = str(record.get("status") or "unknown").lower()
        injury_type = str(record.get("injury_type") or "").lower()
        signal_type = "suspension" if "suspension" in injury_type or status == "suspended" else "player_availability"
        player = str(record.get("player_name") or "")
        confidence = _clamp(float(record.get("confidence") or 0.6), 0.0, 1.0)
        signal = InformationStateSignal(
            signal_id=_stable_signal_id(team, player, available_at, status),
            signal_type=signal_type,
            source_status="used_pre_match" if strict_ready else "used_but_not_strict",
            source=str(record.get("source") or "unknown"),
            source_url=record.get("source_url"),
            published_at=_first_present(record.get("published_at"), available_at),
            available_at=available_at,
            expires_at=_expiry_value(record),
            confidence=round(confidence, 4),
            affected_team=team,
            affected_player=player or None,
            availability_status=status,
            expected_minutes_delta=_expected_minutes_delta(status, record),
            summary=_signal_summary(player, team, status, record.get("injury_type")),
            shadow_only=True,
            included_in_strict_features=strict_ready,
        )
        signals.append(signal)

    strict_count = sum(1 for item in signals if item.included_in_strict_features)
    payload = {
        "schema_version": "information_state_signals.v1",
        "home_team": home_team,
        "away_team": away_team,
        "as_of_time": as_of_time,
        "kickoff_at": kickoff_at,
        "signals": [item.to_dict() for item in sorted(signals, key=lambda s: s.signal_id)],
        "summary": {
            "total_loaded_records": len(records),
            "used_signals": len(signals),
            "strict_eligible_signals": strict_count,
            "excluded_future_signals": excluded_future,
            "excluded_other_team_records": excluded_other_team,
            "malformed_records": malformed,
            "shadow_only": True,
            "source_status": "used" if signals else "unavailable",
            "reason": "signals_materialized" if signals else "no_relevant_pre_match_signals",
        },
    }
    return payload


def _coerce_record(record: InjuryRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, InjuryRecord):
        return {
            "player_name": record.player_name,
            "team_name": record.team_name,
            "status": record.status,
            "injury_type": record.injury_type,
            "expected_return": record.expected_return,
            "confidence": record.confidence,
            "source": record.source,
            "last_updated": record.last_updated,
        }
    return dict(record)


def _expected_minutes_delta(status: str, record: dict[str, Any]) -> float | None:
    if record.get("expected_minutes_delta") is not None:
        return float(record["expected_minutes_delta"])
    mapping = {
        "out": -90.0,
        "suspended": -90.0,
        "injured": -90.0,
        "doubtful": -45.0,
        "probable": -15.0,
        "available": 0.0,
        "fit": 0.0,
    }
    return mapping.get(status)


def _expiry_value(record: dict[str, Any]) -> str | None:
    return _first_present(record.get("expires_at"), record.get("effective_until"), record.get("expected_return"))


def _signal_summary(player: str, team: str, status: str, injury_type: Any) -> str:
    detail = f": {injury_type}" if injury_type else ""
    who = player or "unknown player"
    return f"{team} {who} status={status}{detail}"


def _first_present(*values: Any) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _stable_signal_id(team: str, player: str, available_at: str | None, status: str) -> str:
    raw = f"{_norm(team)}::{_norm(player)}::{available_at or ''}::{status}"
    import hashlib

    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    if len(text) == 10:
        text = f"{text}T00:00:00+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm(raw: Any) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
