from __future__ import annotations

import sqlite3

import pytest

from app.services.information_state_engine import (
    EvidenceInput,
    audit_match_information_state,
    ensure_information_state_tables,
    evaluate_match_signals,
    extract_information_signals,
    score_information_signals,
    upsert_evidence_item,
)


def _db(tmp_path):
    path = tmp_path / "info_state.db"
    conn = sqlite3.connect(path)
    try:
        ensure_information_state_tables(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def test_evidence_requires_source_url(tmp_path):
    db_path = _db(tmp_path)

    with pytest.raises(ValueError, match="source_url"):
        upsert_evidence_item(
            db_path,
            EvidenceInput(
                evidence_type="news",
                source_url="",
                title="Team news",
                content="Alpha striker is doubtful.",
                match_id="m1",
                home_team="Alpha",
                away_team="Beta",
            ),
        )


def test_evidence_upsert_is_idempotent(tmp_path):
    db_path = _db(tmp_path)
    evidence = EvidenceInput(
        evidence_type="news",
        source_url="https://example.test/alpha-news",
        source_name="Example",
        title="Alpha injury update",
        content="Alpha striker is out with an injury.",
        published_at="2026-07-07T08:00:00+00:00",
        available_at="2026-07-07T08:00:00+00:00",
        reliability_score=0.8,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
    )

    first = upsert_evidence_item(db_path, evidence)
    second = upsert_evidence_item(db_path, evidence)

    assert first["inserted"] is True
    assert second["inserted"] is False
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM evidence_items").fetchone()[0] == 1
    finally:
        conn.close()


def test_after_kickoff_evidence_is_not_strict_ready(tmp_path):
    db_path = _db(tmp_path)
    upsert_evidence_item(
        db_path,
        EvidenceInput(
            evidence_type="news",
            source_url="https://example.test/late",
            title="Late injury update",
            content="Alpha midfielder is injured.",
            published_at="2026-07-07T22:00:00+00:00",
            available_at="2026-07-07T22:00:00+00:00",
            reliability_score=0.8,
            match_id="m1",
            home_team="Alpha",
            away_team="Beta",
        ),
    )

    extracted = extract_information_signals(
        db_path,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
        kickoff_at="2026-07-07T20:00:00+00:00",
    )
    audit = audit_match_information_state(
        db_path,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
        kickoff_at="2026-07-07T20:00:00+00:00",
    )

    assert extracted["signals_extracted"] == 1
    assert extracted["signals"][0]["source_status"] == "after_kickoff_excluded_from_strict"
    assert audit["checks"]["all_evidence_before_kickoff"] is False
    assert audit["strict_ready"] is False


def test_low_confidence_signal_stays_shadow_rejected(tmp_path):
    db_path = _db(tmp_path)
    upsert_evidence_item(
        db_path,
        EvidenceInput(
            evidence_type="news",
            source_url="https://example.test/rumor",
            title="Alpha rumor",
            content="Alpha striker is doubtful according to a rumor.",
            published_at="2026-07-07T08:00:00+00:00",
            available_at="2026-07-07T08:00:00+00:00",
            reliability_score=0.3,
            match_id="m1",
            home_team="Alpha",
            away_team="Beta",
        ),
    )
    extract_information_signals(db_path, match_id="m1", home_team="Alpha", away_team="Beta")

    scored = score_information_signals(db_path, match_id="m1", home_team="Alpha", away_team="Beta")

    assert scored["signals_scored"] == 1
    assert scored["signals"][0]["status"] == "rejected_low_confidence"
    assert scored["signals"][0]["shadow_adjustment"]["shadow_only"] is True


def test_news_extraction_respects_team_clause_and_injury_negation(tmp_path):
    db_path = _db(tmp_path)
    upsert_evidence_item(
        db_path,
        EvidenceInput(
            evidence_type="news",
            source_url="https://example.test/team-news",
            title="Alpha vs Beta team news",
            content="Alpha have no fresh injury concerns; Beta could be without two wingers after both missed the prior match.",
            published_at="2026-07-07T08:00:00+00:00",
            available_at="2026-07-07T08:00:00+00:00",
            reliability_score=0.8,
            match_id="m1",
            home_team="Alpha",
            away_team="Beta",
        ),
    )

    extracted = extract_information_signals(
        db_path,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
    )

    assert extracted["signals_extracted"] == 1
    signal = extracted["signals"][0]
    assert signal["team"] == "Beta"
    assert signal["signal_type"] == "injury"
    assert signal["direction"] == "negative"


def test_signal_evaluation_is_idempotent_and_proposal_only(tmp_path):
    db_path = _db(tmp_path)
    upsert_evidence_item(
        db_path,
        EvidenceInput(
            evidence_type="news",
            source_url="https://example.test/return",
            title="Alpha forward returns",
            content="Alpha forward returns and is fit.",
            published_at="2026-07-07T08:00:00+00:00",
            available_at="2026-07-07T08:00:00+00:00",
            reliability_score=0.9,
            match_id="m1",
            home_team="Alpha",
            away_team="Beta",
        ),
    )
    extract_information_signals(db_path, match_id="m1", home_team="Alpha", away_team="Beta")
    score_information_signals(db_path, match_id="m1", home_team="Alpha", away_team="Beta")

    first = evaluate_match_signals(
        db_path,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
        home_score=2,
        away_score=0,
    )
    second = evaluate_match_signals(
        db_path,
        match_id="m1",
        home_team="Alpha",
        away_team="Beta",
        home_score=2,
        away_score=0,
    )

    assert first["signals_evaluated"] == 1
    assert first["evaluations"][0]["verdict"] == "accurate"
    assert "do not change production weights" in first["notes"]
    assert second["signals_evaluated"] == 1
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM signal_evaluations").fetchone()[0] == 1
    finally:
        conn.close()
