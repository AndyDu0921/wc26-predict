"""Tests for public-output odds leakage audit script."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_public_outputs_no_odds import audit_paths


def test_audit_paths_passes_clean_file(tmp_path: Path):
    clean = tmp_path / "clean.md"
    clean.write_text("Team form and tactical notes only.", encoding="utf-8")

    result = audit_paths([clean])

    assert result["passed"] is True
    assert result["files_scanned"] == 1
    assert result["findings"] == []


def test_audit_paths_flags_odds_terms(tmp_path: Path):
    risky = tmp_path / "risky.md"
    risky.write_text("Bookmaker odds: 2.10 / 3.50 / 3.80", encoding="utf-8")

    result = audit_paths([risky])

    assert result["passed"] is False
    assert result["files_scanned"] == 1
    terms = {finding["term"].lower() for finding in result["findings"]}
    assert "odds" in terms
    assert "bookmaker" in terms
