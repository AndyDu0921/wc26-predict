"""V4.10 information-state evidence ledger and signal scoring.

This module is deliberately conservative:
- evidence requires a traceable source_url, even when the source is an
  internal snapshot URI;
- signals are shadow-only by default and never change production weights;
- strict eligibility is based on available_at <= kickoff_at.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.evaluation_registry import DEFAULT_DB_PATH


VALID_EVIDENCE_TYPES = {
    "news",
    "injury",
    "lineup",
    "weather",
    "market_odds",
    "schedule_context",
    "manual_event",
}

SIGNAL_BASE_MAGNITUDE = {
    "injury": 0.12,
    "suspension": 0.12,
    "return": 0.08,
    "lineup": 0.06,
    "rotation": 0.05,
    "tactical": 0.04,
    "coach": 0.03,
    "morale": 0.03,
    "fatigue": 0.05,
    "travel": 0.04,
    "weather": 0.03,
    "market_move": 0.10,
    "schedule_context": 0.03,
    "other": 0.02,
}

LOW_CONFIDENCE_THRESHOLD = 0.45


@dataclass(frozen=True)
class EvidenceInput:
    evidence_type: str
    source_url: str
    source_name: str | None = None
    title: str | None = None
    content: str | None = None
    language: str | None = None
    published_at: str | datetime | None = None
    fetched_at: str | datetime | None = None
    available_at: str | datetime | None = None
    reliability_score: float = 0.5
    match_id: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    metadata: dict[str, Any] | None = None


def ensure_information_state_tables(conn: sqlite3.Connection) -> None:
    """Create V4.10 tables for local tests or emergency sqlite use.

    Alembic remains the production path. This helper mirrors the migration so
    service-level tests can run on tiny throwaway databases.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS evidence_items (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            evidence_key TEXT NOT NULL UNIQUE,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            evidence_type TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_name TEXT,
            title TEXT,
            content_excerpt TEXT,
            raw_text_hash TEXT NOT NULL,
            language TEXT,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            reliability_score REAL NOT NULL DEFAULT 0.5,
            metadata JSON
        );
        CREATE INDEX IF NOT EXISTS ix_evidence_items_match_id ON evidence_items(match_id);
        CREATE INDEX IF NOT EXISTS ix_evidence_items_evidence_type ON evidence_items(evidence_type);
        CREATE INDEX IF NOT EXISTS ix_evidence_items_available_at ON evidence_items(available_at);
        CREATE INDEX IF NOT EXISTS ix_evidence_items_raw_text_hash ON evidence_items(raw_text_hash);

        CREATE TABLE IF NOT EXISTS information_state_signals (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            signal_key TEXT NOT NULL UNIQUE,
            match_id TEXT,
            team TEXT NOT NULL,
            player TEXT,
            signal_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            magnitude REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.5,
            available_at TEXT NOT NULL,
            expires_at TEXT,
            evidence_ids JSON NOT NULL,
            status TEXT NOT NULL DEFAULT 'shadow',
            source_status TEXT NOT NULL DEFAULT 'used_pre_match',
            shadow_adjustment JSON,
            summary TEXT,
            metadata JSON
        );
        CREATE INDEX IF NOT EXISTS ix_information_state_signals_match_id ON information_state_signals(match_id);
        CREATE INDEX IF NOT EXISTS ix_information_state_signals_team ON information_state_signals(team);
        CREATE INDEX IF NOT EXISTS ix_information_state_signals_type ON information_state_signals(signal_type);
        CREATE INDEX IF NOT EXISTS ix_information_state_signals_status ON information_state_signals(status);
        CREATE INDEX IF NOT EXISTS ix_information_state_signals_available_at ON information_state_signals(available_at);

        CREATE TABLE IF NOT EXISTS signal_evaluations (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            evaluation_key TEXT NOT NULL UNIQUE,
            match_id TEXT NOT NULL,
            prediction_run_id TEXT,
            signal_id TEXT NOT NULL,
            actual_outcome TEXT NOT NULL,
            verdict TEXT NOT NULL,
            contribution_score REAL NOT NULL DEFAULT 0,
            notes TEXT,
            metrics JSON
        );
        CREATE INDEX IF NOT EXISTS ix_signal_evaluations_match_id ON signal_evaluations(match_id);
        CREATE INDEX IF NOT EXISTS ix_signal_evaluations_signal_id ON signal_evaluations(signal_id);
        CREATE INDEX IF NOT EXISTS ix_signal_evaluations_verdict ON signal_evaluations(verdict);
        """
    )


def upsert_evidence_item(db_path: str | Path, evidence: EvidenceInput | dict[str, Any]) -> dict[str, Any]:
    """Validate and persist one evidence item idempotently."""
    item = evidence if isinstance(evidence, EvidenceInput) else EvidenceInput(**dict(evidence))
    normalized = _normalize_evidence(item)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_information_state_tables(conn)
        existing = conn.execute(
            "SELECT * FROM evidence_items WHERE evidence_key = ?",
            (normalized["evidence_key"],),
        ).fetchone()
        if existing is not None:
            return {"inserted": False, "evidence": _row_to_dict(existing)}
        conn.execute(
            """
            INSERT INTO evidence_items (
                id, evidence_key, match_id, home_team, away_team, evidence_type,
                source_url, source_name, title, content_excerpt, raw_text_hash,
                language, published_at, fetched_at, available_at,
                reliability_score, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized["id"],
                normalized["evidence_key"],
                normalized["match_id"],
                normalized["home_team"],
                normalized["away_team"],
                normalized["evidence_type"],
                normalized["source_url"],
                normalized["source_name"],
                normalized["title"],
                normalized["content_excerpt"],
                normalized["raw_text_hash"],
                normalized["language"],
                normalized["published_at"],
                normalized["fetched_at"],
                normalized["available_at"],
                normalized["reliability_score"],
                _json(normalized["metadata"]),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?",
            (normalized["id"],),
        ).fetchone()
        return {"inserted": True, "evidence": _row_to_dict(row)}
    finally:
        conn.close()


def collect_match_evidence(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    limit_articles: int = 20,
) -> dict[str, Any]:
    """Import traceable local evidence into the V4.10 ledger."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    inserted = 0
    skipped = 0
    candidates = []
    try:
        ensure_information_state_tables(conn)
        resolved = _resolve_match_context(conn, match_id=match_id, home_team=home_team, away_team=away_team)
        candidates.extend(_evidence_from_latest_pre_match_snapshot(conn, resolved))
        candidates.extend(_evidence_from_manual_events(conn, resolved))
        candidates.extend(_evidence_from_news_articles(conn, resolved, limit_articles=limit_articles))
    finally:
        conn.close()

    details = []
    for item in candidates:
        try:
            result = upsert_evidence_item(db_path, item)
            details.append(result)
            inserted += 1 if result["inserted"] else 0
            skipped += 0 if result["inserted"] else 1
        except ValueError as exc:
            details.append({"inserted": False, "error": str(exc), "source_url": item.source_url})
            skipped += 1
    return {
        "schema_version": "evidence_collection.v1",
        "match_id": match_id,
        "home_team": home_team,
        "away_team": away_team,
        "candidate_count": len(candidates),
        "inserted": inserted,
        "skipped": skipped,
        "details": details,
    }


def extract_information_signals(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    kickoff_at: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Extract deterministic shadow signals from ledger evidence."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_information_state_tables(conn)
        evidence_rows = _load_evidence_rows(
            conn,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
        )
        signals = []
        for row in evidence_rows:
            signals.extend(_signals_from_evidence(row, kickoff_at=kickoff_at))
        if persist:
            persisted = [_upsert_signal(conn, signal) for signal in signals]
            conn.commit()
        else:
            persisted = signals
        return {
            "schema_version": "information_state_signal_extraction.v1",
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "evidence_count": len(evidence_rows),
            "signals_extracted": len(signals),
            "persisted": persist,
            "signals": persisted,
            "notes": "Deterministic extractor; LLM extraction can feed the same tables but does not set probabilities.",
        }
    finally:
        conn.close()


def score_information_signals(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
) -> dict[str, Any]:
    """Apply transparent initial scoring rules to shadow signals."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_information_state_tables(conn)
        rows = _load_signal_rows(conn, match_id=match_id, home_team=home_team, away_team=away_team)
        scored = []
        for row in rows:
            payload = _score_signal_row(row)
            conn.execute(
                """
                UPDATE information_state_signals
                SET magnitude = ?, status = ?, shadow_adjustment = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload["magnitude"],
                    payload["status"],
                    _json(payload["shadow_adjustment"]),
                    _now_iso(),
                    row["id"],
                ),
            )
            scored.append(payload)
        conn.commit()
        return {
            "schema_version": "information_state_signal_scoring.v1",
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "signals_scored": len(scored),
            "signals": scored,
            "notes": "All adjustments are shadow-only and do not alter production probabilities.",
        }
    finally:
        conn.close()


def audit_match_information_state(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    kickoff_at: str | None = None,
) -> dict[str, Any]:
    """Return a non-blocking pre-match information quality gate."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_information_state_tables(conn)
        resolved = _resolve_match_context(conn, match_id=match_id, home_team=home_team, away_team=away_team)
        effective_kickoff = kickoff_at or resolved.get("kickoff_at")
        evidence_rows = _load_evidence_rows(
            conn,
            match_id=resolved.get("match_id") or match_id,
            home_team=resolved.get("home_team") or home_team,
            away_team=resolved.get("away_team") or away_team,
        )
        signal_rows = _load_signal_rows(
            conn,
            match_id=resolved.get("match_id") or match_id,
            home_team=resolved.get("home_team") or home_team,
            away_team=resolved.get("away_team") or away_team,
        )
    finally:
        conn.close()

    evidence_types = {str(row["evidence_type"]) for row in evidence_rows}
    active_signals = [row for row in signal_rows if str(row["status"]) in {"shadow", "approved_for_shadow"}]
    signal_types = {str(row["signal_type"]) for row in active_signals}
    future_items = [
        row["id"]
        for row in evidence_rows
        if _is_after(row["available_at"], effective_kickoff)
    ]
    checks = {
        "market_odds": "market_odds" in evidence_types,
        "weather": "weather" in evidence_types,
        "news": "news" in evidence_types or "manual_event" in evidence_types,
        "injury_or_lineup": bool(
            {"injury", "lineup", "manual_event"} & evidence_types
            or {"injury", "suspension", "return", "lineup", "rotation"} & signal_types
        ),
        "all_evidence_before_kickoff": len(future_items) == 0,
        "has_structured_signals": bool(active_signals),
    }
    missing = [key for key, value in checks.items() if not value]
    score = sum(1 for value in checks.values() if value) / max(len(checks), 1)
    return {
        "schema_version": "match_information_state_audit.v1",
        "match_id": resolved.get("match_id") or match_id,
        "home_team": resolved.get("home_team") or home_team,
        "away_team": resolved.get("away_team") or away_team,
        "kickoff_at": effective_kickoff,
        "checks": checks,
        "missing": missing,
        "evidence_count": len(evidence_rows),
        "signal_count": len(signal_rows),
        "active_shadow_signal_count": len(active_signals),
        "future_evidence_ids": future_items,
        "quality_score": round(score, 4),
        "confidence_modifier": round(0.85 + 0.15 * score, 4),
        "strict_ready": checks["all_evidence_before_kickoff"] and checks["market_odds"] and checks["weather"],
        "notes": "Low quality does not block prediction; it must be shown and snapshotted.",
    }


def build_match_information_state_snapshot(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str | None = None,
    home_team: str | None = None,
    away_team: str | None = None,
    kickoff_at: str | None = None,
) -> dict[str, Any]:
    """Assemble the V4.10 information-state payload for prediction snapshots."""
    try:
        audit = audit_match_information_state(
            db_path,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=kickoff_at,
        )
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            signals = [_row_to_dict(row) for row in _load_signal_rows(
                conn,
                match_id=audit.get("match_id") or match_id,
                home_team=audit.get("home_team") or home_team,
                away_team=audit.get("away_team") or away_team,
            )]
        finally:
            conn.close()
        return {
            "schema_version": "information_state_snapshot.v1",
            "audit": audit,
            "signals": signals,
            "shadow_only": True,
        }
    except Exception as exc:
        return {
            "schema_version": "information_state_snapshot.v1",
            "audit": {
                "quality_score": 0.0,
                "missing": ["information_state_unavailable"],
                "error": str(exc),
            },
            "signals": [],
            "shadow_only": True,
        }


def evaluate_match_signals(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str,
    home_team: str,
    away_team: str,
    home_score: int,
    away_score: int,
    prediction_run_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Evaluate pre-match information-state signals after the result."""
    actual_outcome = "home" if home_score > away_score else ("draw" if home_score == away_score else "away")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        ensure_information_state_tables(conn)
        rows = _load_signal_rows(conn, match_id=match_id, home_team=home_team, away_team=away_team)
        evaluations = []
        for row in rows:
            evaluation = _evaluate_signal_row(
                row,
                home_team=home_team,
                away_team=away_team,
                actual_outcome=actual_outcome,
                match_id=match_id,
                prediction_run_id=prediction_run_id,
            )
            evaluations.append(evaluation)
            if not dry_run:
                _upsert_signal_evaluation(conn, evaluation)
        if not dry_run:
            conn.commit()
        return {
            "schema_version": "signal_evaluation_summary.v1",
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "actual_outcome": actual_outcome,
            "signals_evaluated": len(evaluations),
            "persisted": not dry_run,
            "evaluations": evaluations,
            "notes": "Signal evaluations are attribution evidence only and do not change production weights.",
        }
    finally:
        conn.close()


def _normalize_evidence(item: EvidenceInput) -> dict[str, Any]:
    evidence_type = str(item.evidence_type or "").strip().lower()
    if evidence_type not in VALID_EVIDENCE_TYPES:
        raise ValueError(f"Unsupported evidence_type={item.evidence_type!r}")
    source_url = str(item.source_url or "").strip()
    if not source_url:
        raise ValueError("Evidence requires source_url")
    now = _now_iso()
    content = str(item.content or item.title or source_url)
    fetched_at = _dt_iso(item.fetched_at) or now
    published_at = _dt_iso(item.published_at)
    available_at = _dt_iso(item.available_at) or published_at or fetched_at
    raw_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    evidence_key = hashlib.sha256(
        f"{item.match_id or ''}|{source_url}|{raw_hash}|{evidence_type}".encode("utf-8")
    ).hexdigest()
    return {
        "id": str(uuid4()),
        "evidence_key": evidence_key,
        "match_id": _empty_to_none(item.match_id),
        "home_team": _empty_to_none(item.home_team),
        "away_team": _empty_to_none(item.away_team),
        "evidence_type": evidence_type,
        "source_url": source_url,
        "source_name": _empty_to_none(item.source_name),
        "title": _empty_to_none(item.title),
        "content_excerpt": content[:2000],
        "raw_text_hash": raw_hash,
        "language": _empty_to_none(item.language),
        "published_at": published_at,
        "fetched_at": fetched_at,
        "available_at": available_at,
        "reliability_score": _clamp(float(item.reliability_score or 0.5), 0.0, 1.0),
        "metadata": item.metadata or {},
    }


def _evidence_from_latest_pre_match_snapshot(conn: sqlite3.Connection, context: dict[str, str | None]) -> list[EvidenceInput]:
    if not _has_table(conn, "pre_match_snapshots"):
        return []
    filters, params = _match_filters(context)
    if not filters:
        return []
    row = conn.execute(
        f"""
        SELECT *
        FROM pre_match_snapshots
        WHERE {' OR '.join(filters)}
        ORDER BY snapshot_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if row is None:
        return []
    items: list[EvidenceInput] = []
    for column, evidence_type in (
        ("odds_snapshot", "market_odds"),
        ("weather_snapshot", "weather"),
        ("injury_records", "injury"),
        ("lineup_snapshot", "lineup"),
    ):
        raw = row[column] if column in row.keys() else None
        if raw in (None, "", "null"):
            continue
        items.append(
            EvidenceInput(
                evidence_type=evidence_type,
                source_url=f"internal://pre_match_snapshots/{row['id']}/{column}",
                source_name="pre_match_snapshots",
                title=f"{evidence_type} snapshot for {context.get('home_team')} vs {context.get('away_team')}",
                content=raw if isinstance(raw, str) else _json(raw),
                published_at=row["snapshot_at"],
                fetched_at=row["snapshot_at"],
                available_at=row["snapshot_at"],
                reliability_score=0.85,
                match_id=context.get("match_id") or str(row["match_id"]),
                home_team=context.get("home_team") or row["home_team"],
                away_team=context.get("away_team") or row["away_team"],
                metadata={"source_column": column, "snapshot_id": row["id"]},
            )
        )
    return items


def _evidence_from_manual_events(conn: sqlite3.Connection, context: dict[str, str | None]) -> list[EvidenceInput]:
    if not _has_table(conn, "manual_events"):
        return []
    filters = []
    params: list[Any] = []
    if context.get("match_id"):
        filters.append("CAST(match_id AS TEXT) = ?")
        params.append(context["match_id"])
    teams = [item for item in (context.get("home_team"), context.get("away_team")) if item]
    if teams:
        filters.append(f"team_name IN ({','.join('?' for _ in teams)})")
        params.extend(teams)
    if not filters:
        return []
    rows = conn.execute(
        f"""
        SELECT *
        FROM manual_events
        WHERE ({' OR '.join(filters)})
          AND status = 'active'
        ORDER BY created_at DESC
        """,
        params,
    ).fetchall()
    items: list[EvidenceInput] = []
    for row in rows:
        source_url = row["source_url"]
        if not source_url:
            continue
        content = f"{row['event_type']} {row['severity']} {row['team_name']} {row['player_name'] or ''} {row['note']}"
        items.append(
            EvidenceInput(
                evidence_type="manual_event",
                source_url=source_url,
                source_name=row["source_name"],
                title=f"Manual {row['event_type']} event",
                content=content,
                published_at=row["created_at"],
                fetched_at=row["created_at"],
                available_at=row["created_at"],
                reliability_score=float(row["confidence"] or 0.5),
                match_id=context.get("match_id") or row["match_id"],
                home_team=context.get("home_team"),
                away_team=context.get("away_team"),
                metadata={
                    "manual_event_id": row["id"],
                    "team": row["team_name"],
                    "player": row["player_name"],
                    "event_type": row["event_type"],
                    "severity": row["severity"],
                    "expires_at": row["expires_at"],
                },
            )
        )
    return items


def _evidence_from_news_articles(
    conn: sqlite3.Connection,
    context: dict[str, str | None],
    *,
    limit_articles: int,
) -> list[EvidenceInput]:
    if not _has_table(conn, "news_articles"):
        return []
    teams = [item for item in (context.get("home_team"), context.get("away_team")) if item]
    if not teams:
        return []
    clauses = []
    params: list[Any] = []
    for team in teams:
        clauses.append("(title LIKE ? OR content LIKE ?)")
        params.extend([f"%{team}%", f"%{team}%"])
    rows = conn.execute(
        f"""
        SELECT *
        FROM news_articles
        WHERE {' OR '.join(clauses)}
        ORDER BY COALESCE(published_at, fetched_at) DESC
        LIMIT ?
        """,
        [*params, limit_articles],
    ).fetchall()
    return [
        EvidenceInput(
            evidence_type="news",
            source_url=row["source_url"],
            source_name=row["source_name"],
            title=row["title"],
            content=f"{row['title']}\n\n{row['content']}",
            language=row["language"],
            published_at=row["published_at"],
            fetched_at=row["fetched_at"],
            available_at=row["published_at"] or row["fetched_at"],
            reliability_score=0.65,
            match_id=context.get("match_id"),
            home_team=context.get("home_team"),
            away_team=context.get("away_team"),
            metadata={"news_article_id": row["id"]},
        )
        for row in rows
    ]


def _load_evidence_rows(
    conn: sqlite3.Connection,
    *,
    match_id: str | None,
    home_team: str | None,
    away_team: str | None,
) -> list[sqlite3.Row]:
    ensure_information_state_tables(conn)
    filters, params = _match_filters({"match_id": match_id, "home_team": home_team, "away_team": away_team})
    if not filters:
        return []
    return conn.execute(
        f"""
        SELECT *
        FROM evidence_items
        WHERE {' OR '.join(filters)}
        ORDER BY available_at ASC, created_at ASC
        """,
        params,
    ).fetchall()


def _load_signal_rows(
    conn: sqlite3.Connection,
    *,
    match_id: str | None,
    home_team: str | None,
    away_team: str | None,
) -> list[sqlite3.Row]:
    ensure_information_state_tables(conn)
    teams = [team for team in (home_team, away_team) if team]
    if not match_id and not teams:
        return []
    params: list[Any] = []
    if match_id and teams:
        team_placeholders = ",".join("?" for _ in teams)
        where_sql = f"(CAST(match_id AS TEXT) = ? OR (match_id IS NULL AND team IN ({team_placeholders})))"
        params = [match_id, *teams]
    elif match_id:
        where_sql = "CAST(match_id AS TEXT) = ?"
        params = [match_id]
    else:
        where_sql = f"team IN ({','.join('?' for _ in teams)})"
        params = teams
    return conn.execute(
        f"""
        SELECT *
        FROM information_state_signals
        WHERE {where_sql}
        ORDER BY available_at ASC, created_at ASC
        """,
        params,
    ).fetchall()


def _signals_from_evidence(row: sqlite3.Row, *, kickoff_at: str | None) -> list[dict[str, Any]]:
    metadata = _loads(row["metadata"], {})
    evidence_type = str(row["evidence_type"])
    content = f"{row['title'] or ''}\n{row['content_excerpt'] or ''}"
    teams = [team for team in (row["home_team"], row["away_team"]) if team]
    if not teams and metadata.get("team"):
        teams = [str(metadata["team"])]
    if not teams:
        return []

    source_status = "used_pre_match"
    if _is_after(row["available_at"], kickoff_at):
        source_status = "after_kickoff_excluded_from_strict"

    candidate_texts: list[tuple[str, str]] = []
    if evidence_type in {"news", "injury", "lineup", "manual_event"}:
        for clause in _split_signal_clauses(content):
            if _is_availability_clear_clause(clause):
                continue
            for team_name in teams:
                if team_name.lower() in clause.lower() or _norm(metadata.get("team")) == _norm(team_name):
                    candidate_texts.append((team_name, clause))
        if not candidate_texts and metadata.get("team"):
            candidate_texts.append((str(metadata["team"]), content))
    else:
        candidate_texts.append((str(metadata.get("team") or _infer_team(content, teams) or teams[0]), content))

    signals: list[dict[str, Any]] = []
    confidence = _clamp(float(row["reliability_score"] or 0.5), 0.0, 1.0)
    player = metadata.get("player")
    seen: set[tuple[str, str, str]] = set()
    for team, text in candidate_texts:
        signal_type, direction = _classify_signal(evidence_type, text, metadata)
        if signal_type is None:
            continue
        key = (team, signal_type, direction)
        if key in seen:
            continue
        seen.add(key)
        base = SIGNAL_BASE_MAGNITUDE.get(signal_type, SIGNAL_BASE_MAGNITUDE["other"])
        magnitude = round(base * confidence, 4)
        signal_key = _stable_key(
            row["id"],
            row["match_id"] or "",
            team,
            player or "",
            signal_type,
            direction,
        )
        signals.append({
            "id": str(uuid4()),
            "signal_key": signal_key,
            "match_id": row["match_id"],
            "team": team,
            "player": player,
            "signal_type": signal_type,
            "direction": direction,
            "magnitude": magnitude,
            "confidence": confidence,
            "available_at": row["available_at"],
            "expires_at": metadata.get("expires_at"),
            "evidence_ids": [row["id"]],
            "status": "shadow" if confidence >= LOW_CONFIDENCE_THRESHOLD else "rejected_low_confidence",
            "source_status": source_status,
            "shadow_adjustment": {
                "probability_shift": magnitude,
                "applies_to": team,
                "direction": direction,
                "shadow_only": True,
            },
            "summary": _summary_for_signal(signal_type, direction, team, player, text),
            "metadata": {"evidence_type": evidence_type, **metadata},
        })
    return signals


def _upsert_signal(conn: sqlite3.Connection, signal: dict[str, Any]) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT * FROM information_state_signals WHERE signal_key = ?",
        (signal["signal_key"],),
    ).fetchone()
    if existing is not None:
        return _row_to_dict(existing)
    conn.execute(
        """
        INSERT INTO information_state_signals (
            id, signal_key, match_id, team, player, signal_type, direction,
            magnitude, confidence, available_at, expires_at, evidence_ids,
            status, source_status, shadow_adjustment, summary, metadata
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal["id"],
            signal["signal_key"],
            signal["match_id"],
            signal["team"],
            signal["player"],
            signal["signal_type"],
            signal["direction"],
            signal["magnitude"],
            signal["confidence"],
            signal["available_at"],
            signal["expires_at"],
            _json(signal["evidence_ids"]),
            signal["status"],
            signal["source_status"],
            _json(signal["shadow_adjustment"]),
            signal["summary"],
            _json(signal["metadata"]),
        ),
    )
    row = conn.execute(
        "SELECT * FROM information_state_signals WHERE id = ?",
        (signal["id"],),
    ).fetchone()
    return _row_to_dict(row)


def _score_signal_row(row: sqlite3.Row) -> dict[str, Any]:
    signal_type = str(row["signal_type"])
    confidence = _clamp(float(row["confidence"] or 0.0), 0.0, 1.0)
    base = SIGNAL_BASE_MAGNITUDE.get(signal_type, SIGNAL_BASE_MAGNITUDE["other"])
    magnitude = round(base * confidence, 4)
    status = "shadow" if confidence >= LOW_CONFIDENCE_THRESHOLD else "rejected_low_confidence"
    if str(row["source_status"]) == "after_kickoff_excluded_from_strict":
        status = "shadow_after_kickoff"
    return {
        "id": row["id"],
        "signal_key": row["signal_key"],
        "team": row["team"],
        "signal_type": signal_type,
        "direction": row["direction"],
        "confidence": confidence,
        "magnitude": magnitude,
        "status": status,
        "shadow_adjustment": {
            "probability_shift": magnitude,
            "applies_to": row["team"],
            "direction": row["direction"],
            "shadow_only": True,
            "reason": "initial_v4_10_rule",
        },
    }


def _evaluate_signal_row(
    row: sqlite3.Row,
    *,
    home_team: str,
    away_team: str,
    actual_outcome: str,
    match_id: str,
    prediction_run_id: str | None,
) -> dict[str, Any]:
    team = str(row["team"])
    direction = str(row["direction"])
    team_outcome = "home" if _norm(team) == _norm(home_team) else ("away" if _norm(team) == _norm(away_team) else "unknown")
    team_won = team_outcome == actual_outcome
    if actual_outcome == "draw":
        team_won = False
    if direction == "positive":
        verdict = "accurate" if team_won else "misleading"
    elif direction == "negative":
        verdict = "accurate" if not team_won else "misleading"
    else:
        verdict = "neutral"
    magnitude = float(row["magnitude"] or 0.0)
    contribution = magnitude if verdict == "accurate" else (-magnitude if verdict == "misleading" else 0.0)
    evaluation_key = _stable_key(match_id, row["id"], actual_outcome)
    return {
        "id": str(uuid4()),
        "evaluation_key": evaluation_key,
        "match_id": match_id,
        "prediction_run_id": prediction_run_id,
        "signal_id": row["id"],
        "actual_outcome": actual_outcome,
        "verdict": verdict,
        "contribution_score": round(contribution, 4),
        "notes": "Initial signal attribution; proposal-only, no automatic weight change.",
        "metrics": {
            "team": team,
            "signal_type": row["signal_type"],
            "direction": direction,
            "magnitude": magnitude,
            "confidence": row["confidence"],
            "team_outcome": team_outcome,
        },
    }


def _upsert_signal_evaluation(conn: sqlite3.Connection, evaluation: dict[str, Any]) -> None:
    existing = conn.execute(
        "SELECT id FROM signal_evaluations WHERE evaluation_key = ?",
        (evaluation["evaluation_key"],),
    ).fetchone()
    if existing is not None:
        conn.execute(
            """
            UPDATE signal_evaluations
            SET verdict = ?, contribution_score = ?, notes = ?, metrics = ?, updated_at = ?
            WHERE evaluation_key = ?
            """,
            (
                evaluation["verdict"],
                evaluation["contribution_score"],
                evaluation["notes"],
                _json(evaluation["metrics"]),
                _now_iso(),
                evaluation["evaluation_key"],
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO signal_evaluations (
            id, evaluation_key, match_id, prediction_run_id, signal_id,
            actual_outcome, verdict, contribution_score, notes, metrics
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evaluation["id"],
            evaluation["evaluation_key"],
            evaluation["match_id"],
            evaluation["prediction_run_id"],
            evaluation["signal_id"],
            evaluation["actual_outcome"],
            evaluation["verdict"],
            evaluation["contribution_score"],
            evaluation["notes"],
            _json(evaluation["metrics"]),
        ),
    )


def _resolve_match_context(
    conn: sqlite3.Connection,
    *,
    match_id: str | None,
    home_team: str | None,
    away_team: str | None,
) -> dict[str, str | None]:
    context = {"match_id": match_id, "home_team": home_team, "away_team": away_team, "kickoff_at": None}
    if match_id and _has_table(conn, "matches") and _has_table(conn, "teams"):
        row = conn.execute(
            """
            SELECT CAST(m.id AS TEXT) AS match_id, ht.name AS home_team,
                   at.name AS away_team, m.match_date AS kickoff_at
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE CAST(m.id AS TEXT) = ?
               OR REPLACE(CAST(m.id AS TEXT), '-', '') = REPLACE(?, '-', '')
            LIMIT 1
            """,
            (match_id, match_id),
        ).fetchone()
        if row:
            return {key: row[key] for key in context}
    if match_id and _has_table(conn, "wc26_schedule"):
        row = conn.execute(
            """
            SELECT CAST(id AS TEXT) AS match_id, home_team, away_team,
                   CASE
                     WHEN match_date IS NOT NULL AND kickoff_time IS NOT NULL
                     THEN match_date || 'T' || kickoff_time || ':00'
                     ELSE match_date
                   END AS kickoff_at
            FROM wc26_schedule
            WHERE CAST(id AS TEXT) = ?
            LIMIT 1
            """,
            (match_id,),
        ).fetchone()
        if row:
            return {key: row[key] for key in context}
    return context


def _match_filters(context: dict[str, str | None]) -> tuple[list[str], list[Any]]:
    filters = []
    params: list[Any] = []
    if context.get("match_id"):
        filters.append("CAST(match_id AS TEXT) = ?")
        params.append(context["match_id"])
    if context.get("home_team") and context.get("away_team"):
        filters.append("(home_team = ? AND away_team = ?)")
        params.extend([context["home_team"], context["away_team"]])
    return filters, params


def _classify_signal(
    evidence_type: str,
    content: str,
    metadata: dict[str, Any],
) -> tuple[str | None, str]:
    text = content.lower()
    event_type = str(metadata.get("event_type") or "").lower()
    if evidence_type == "market_odds":
        return "market_move", "neutral"
    if evidence_type == "weather":
        if any(term in text for term in ("storm", "rain", "wind", "heat", "snow", "extreme")):
            return "weather", "negative"
        return "weather", "neutral"
    if evidence_type in {"injury", "manual_event"} or event_type:
        if "suspension" in text or event_type == "suspension":
            return "suspension", "negative"
        if any(term in text for term in ("return", "fit", "available", "复出", "恢复")):
            return "return", "positive"
        if any(term in text for term in ("injury", "injured", "out", "doubtful", "without", "missed", "伤", "缺阵")) or event_type == "injury":
            return "injury", "negative"
        if "rotation" in text or event_type == "rotation_hint":
            return "rotation", "negative"
        if "lineup" in text or "starting" in text or event_type.startswith("lineup"):
            return "lineup", "neutral"
        if event_type == "motivation":
            return "morale", "positive"
    if any(term in text for term in ("injury", "injured", "out", "doubtful", "without", "missed", "伤", "缺阵")):
        return "injury", "negative"
    if any(term in text for term in ("returns", "return", "fit", "available", "复出", "恢复")):
        return "return", "positive"
    if any(term in text for term in ("lineup", "starting xi", "首发")):
        return "lineup", "neutral"
    if any(term in text for term in ("rotate", "rotation", "轮换")):
        return "rotation", "negative"
    if any(term in text for term in ("coach", "manager", "主帅", "教练")):
        return "coach", "neutral"
    if any(term in text for term in ("travel", "fatigue", "rest", "疲劳", "旅行")):
        return "fatigue", "negative"
    return None, "neutral"


def _split_signal_clauses(content: str) -> list[str]:
    clauses = [
        " ".join(part.split())
        for part in re.split(r"[\n.;。；]+", content or "")
    ]
    return [clause for clause in clauses if clause]


def _is_availability_clear_clause(clause: str) -> bool:
    text = clause.lower()
    clear_terms = (
        "no fresh injury concerns",
        "no injury concerns",
        "no fresh injuries",
        "no new injuries",
        "clean bill of health",
        "fully fit squad",
    )
    return any(term in text for term in clear_terms)


def _infer_team(content: str, teams: list[str]) -> str | None:
    text = content.lower()
    for team in teams:
        if team.lower() in text:
            return team
    return None


def _summary_for_signal(
    signal_type: str,
    direction: str,
    team: str,
    player: Any,
    content: str,
) -> str:
    snippet = " ".join(content.split())[:160]
    who = f" {player}" if player else ""
    return f"{team}{who}: {signal_type}/{direction} - {snippet}"


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    payload = {key: row[key] for key in row.keys()}
    for key in ("metadata", "evidence_ids", "shadow_adjustment", "metrics"):
        if key in payload:
            payload[key] = _loads(payload[key], {} if key != "evidence_ids" else [])
    return payload


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _is_after(left: Any, right: Any) -> bool:
    left_dt = _parse_dt(left)
    right_dt = _parse_dt(right)
    return bool(left_dt and right_dt and left_dt > right_dt)


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
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


def _dt_iso(raw: Any) -> str | None:
    dt = _parse_dt(raw)
    return dt.isoformat() if dt else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _loads(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _stable_key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()


def _empty_to_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
