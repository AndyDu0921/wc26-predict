"""Player injury and availability data service.

Bridges external injury data into the NewsSignal system that the
the canonical synchronous signal path consumes. Supports multiple data sources:

  - Local JSON seed file (for manual updates, no API key needed)
  - Placeholder for Transfermarkt / FlashScore scraping
  - Placeholder for paid API (Sportmonks, API-Football, etc.)

Design principle:  Degrade gracefully.  If no injury data is available,
the pipeline continues with Dixon-Coles + Enhancer + Elo only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.logging import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------
#  Data structures
# ------------------------------------------------------------------
@dataclass(slots=True)
class InjuryRecord:
    """A single player injury / availability record."""
    player_name: str
    team_name: str
    status: str                 # "out", "doubtful", "probable", "available"
    injury_type: str | None     # e.g. "hamstring", "knee", "suspension"
    expected_return: str | None  # ISO date string
    confidence: float           # 0-1, how certain the report is
    source: str
    last_updated: str           # ISO date


# ------------------------------------------------------------------
#  Default injury data (2026 World Cup teams — illustrative seed)
# ------------------------------------------------------------------
_SEED_INJURIES: list[dict[str, Any]] = [
    # This is a placeholder.  Real data should come from a live API or
    # community-maintained file.  Values below are illustrative only.
    #
    # Format per record:
    #   {
    #     "player_name": "Kylian Mbappé",
    #     "team_name": "France",
    #     "status": "out",             # out / doubtful / probable
    #     "injury_type": "hamstring",
    #     "expected_return": "2026-07-01",
    #     "confidence": 0.85,
    #     "source": "Transfermarkt",
    #   }
]


# ------------------------------------------------------------------
#  Service
# ------------------------------------------------------------------
class InjuryDataService:
    """Fetch and manage player injury data.

    Usage::

        svc = InjuryDataService()
        records = svc.load()
        for r in records:
            logger.info(f"{r.player_name} ({r.team_name}): {r.status}")
    """

    def __init__(self, seed_path: str | None = None) -> None:
        self._seed_path = Path(seed_path) if seed_path else Path(__file__).parent.parent.parent / "data" / "injuries.json"

    def load(self) -> list[InjuryRecord]:
        """Load injury records from the local JSON seed file.

        Returns empty list if file doesn't exist or is invalid.
        """
        records: list[InjuryRecord] = []
        try:
            if self._seed_path.exists():
                data = json.loads(self._seed_path.read_text(encoding="utf-8"))
                for item in data:
                    if "player_name" not in item:
                        continue  # skip comment/placeholder entries
                    records.append(InjuryRecord(
                        player_name=item["player_name"],
                        team_name=item["team_name"],
                        status=item.get("status", "unknown"),
                        injury_type=item.get("injury_type"),
                        expected_return=item.get("expected_return"),
                        confidence=float(item.get("confidence", 0.7)),
                        source=item.get("source", "manual"),
                        last_updated=item.get("last_updated", datetime.now(timezone.utc).isoformat()),
                    ))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to load injury seed file %s: %s", self._seed_path, exc)

        return records

    def query_team(self, team_name: str, *, match_date: datetime | None = None) -> list[InjuryRecord]:
        """Get injury records for a specific team, filtered by date relevance."""
        all_records = self.load()
        team_records = [r for r in all_records if r.team_name.lower() == team_name.lower()]

        if match_date:
            match_date_utc = match_date.replace(tzinfo=timezone.utc) if match_date.tzinfo is None else match_date
            team_records = [
                r for r in team_records
                if not r.expected_return
                or datetime.fromisoformat(r.expected_return).replace(tzinfo=timezone.utc) > match_date_utc - timedelta(days=7)
            ]

        return sorted(team_records, key=lambda r: r.confidence, reverse=True)

    # ------------------------------------------------------------------
    #  Signal generation for the orchestrator
    # ------------------------------------------------------------------
    def to_signal_payload(
        self,
        record: InjuryRecord,
        *,
        match_id: UUID | None = None,
        team_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Convert an InjuryRecord into the signal payload format the
        canonical pipeline passes to the signal adjustment layer.
        """
        impact = "negative" if record.status in ("out", "doubtful") else "positive"
        minutes_delta = {
            "out": -90.0,
            "doubtful": -45.0,
            "probable": -15.0,
            "available": 0.0,
        }.get(record.status, 0.0)

        return {
            "id": str(uuid4()),
            "team_id": str(team_id) if team_id else None,
            "signal_type": "injury" if record.injury_type != "suspension" else "suspension",
            "impact_direction": impact,
            "confidence": record.confidence,
            "summary_zh": f"{record.player_name}（{record.status}）",
            "key_players": [record.player_name],
            "player_name": record.player_name,
            "claim": f"{record.player_name}: {record.injury_type or 'injury'} — {record.status}",
            "evidence_snippet": f"Source: {record.source}",
            "normalized_availability": record.status,
            "expected_minutes_delta": minutes_delta,
            "effective_until": record.expected_return,
            "contradiction_risk": "low",
            "conflict_group_id": None,
            "source_reliability": record.confidence,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }

    def generate_signals_for_match(
        self,
        home_team: str,
        away_team: str,
        *,
        match_id: UUID | None = None,
        home_team_id: UUID | None = None,
        away_team_id: UUID | None = None,
        match_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Generate injury signal payloads for both teams in a match."""
        signals: list[dict[str, Any]] = []

        for team, team_id in [(home_team, home_team_id), (away_team, away_team_id)]:
            records = self.query_team(team, match_date=match_date)
            for record in records:
                signals.append(self.to_signal_payload(
                    record,
                    match_id=match_id,
                    team_id=team_id,
                ))

        return signals


# ------------------------------------------------------------------
#  Integration helper
# ------------------------------------------------------------------
def fuse_injury_signals(
    base_probabilities: dict[str, float],
    injury_signals: list[dict[str, Any]],
    *,
    home_team: str = "",
    away_team: str = "",
) -> dict[str, float]:
    """Apply injury signals as a simple multiplier on win probability.

    This lightweight path works without database NewsSignal entries. For the
    full evidence-ledger path, injuries should be persisted with traceable
    source and availability timestamps.

    Adjustments:
      - Out (key player):  -15% win prob for affected team
      - Doubtful:          -8%
      - Probable:          -3%
    """
    probs = dict(base_probabilities)
    for sig in injury_signals:
        player_team = sig.get("team_name", "")
        status = sig.get("status", "")
        confidence = float(sig.get("confidence", 0.6))

        if status == "out":
            factor = 1.0 - 0.15 * confidence
        elif status == "doubtful":
            factor = 1.0 - 0.08 * confidence
        elif status == "probable":
            factor = 1.0 - 0.03 * confidence
        else:
            continue

        if player_team.lower() == home_team.lower():
            probs["home_win_prob"] = max(0.02, probs.get("home_win_prob", 0.33) * factor)
            probs["away_win_prob"] = min(0.98, probs.get("away_win_prob", 0.33) / factor)
        elif player_team.lower() == away_team.lower():
            probs["away_win_prob"] = max(0.02, probs.get("away_win_prob", 0.33) * factor)
            probs["home_win_prob"] = min(0.98, probs.get("home_win_prob", 0.33) / factor)

    # Re-normalize
    total = probs.get("home_win_prob", 0) + probs.get("draw_prob", 0) + probs.get("away_win_prob", 0)
    if total > 0:
        for k in ("home_win_prob", "draw_prob", "away_win_prob"):
            probs[k] = probs[k] / total

    return probs
