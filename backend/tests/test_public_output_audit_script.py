"""Tests for public-output unsafe betting-advice audit script."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_public_outputs import audit_paths as audit_paths_alias
from scripts.audit_public_outputs_no_odds import audit_paths


def test_audit_paths_passes_clean_file(tmp_path: Path):
    clean = tmp_path / "clean.md"
    clean.write_text("Team form and tactical notes only.", encoding="utf-8")

    result = audit_paths([clean])

    assert result["passed"] is True
    assert result["files_scanned"] == 1
    assert result["findings"] == []


def test_audit_paths_allows_market_odds_data(tmp_path: Path):
    report = tmp_path / "market.md"
    report.write_text("Bookmaker odds consensus: 2.10 / 3.50 / 3.80", encoding="utf-8")

    result = audit_paths([report])

    assert result["passed"] is True
    assert result["files_scanned"] == 1
    assert result["findings"] == []


def test_new_audit_entrypoint_is_backward_compatible(tmp_path: Path):
    report = tmp_path / "market.md"
    report.write_text("Market odds and bookmaker consensus are research evidence.", encoding="utf-8")

    result = audit_paths_alias([report])

    assert result["passed"] is True
    assert result["files_scanned"] == 1


def test_audit_paths_flags_betting_advice_terms(tmp_path: Path):
    risky = tmp_path / "risky.md"
    risky.write_text("This is a guaranteed prediction. Bet this side now.", encoding="utf-8")

    result = audit_paths([risky])

    assert result["passed"] is False
    assert result["files_scanned"] == 1
    terms = {finding["term"].lower() for finding in result["findings"]}
    assert "guaranteed prediction" in terms
    assert "bet this" in terms


def test_audit_paths_skips_archive_by_default(tmp_path: Path):
    archive = tmp_path / "archive" / "legacy"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text("This is a guaranteed prediction.", encoding="utf-8")

    default = audit_paths([tmp_path])
    explicit = audit_paths([tmp_path], include_archive=True)

    assert default["passed"] is True
    assert default["files_scanned"] == 0
    assert explicit["passed"] is False
    assert explicit["files_scanned"] == 1
