from __future__ import annotations

import sqlite3

from scripts.build_project_state_report import build_project_state_report


def test_project_state_report_separates_review_presence_from_v410_completion(tmp_path):
    db_path = tmp_path / "state.db"
    repo_root = tmp_path / "repo"
    (repo_root / "reports" / "postmatch").mkdir(parents=True)
    (repo_root / "memory").mkdir(parents=True)
    (repo_root / "reports" / "postmatch" / "2026-07-07_Portugal_Spain_postmatch.md").write_text(
        "# Portugal Spain\n",
        encoding="utf-8",
    )
    (repo_root / "memory" / "wc-postmatch-Portugal-Spain-2026-07-07.md").write_text(
        "# memory\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE wc26_schedule (
            id TEXT PRIMARY KEY,
            match_number INTEGER,
            home_slot TEXT,
            away_slot TEXT,
            stage TEXT,
            group_name TEXT,
            match_date TEXT,
            kickoff_time TEXT,
            venue TEXT,
            city TEXT,
            home_team TEXT,
            away_team TEXT,
            home_goals INTEGER,
            away_goals INTEGER,
            match_status TEXT
        );
        CREATE TABLE prediction_learning_log (id TEXT PRIMARY KEY, match_id TEXT);
        CREATE TABLE postmatch_process_eval (id TEXT PRIMARY KEY, match_id TEXT);
        """
    )
    conn.execute(
        """
        INSERT INTO wc26_schedule VALUES (
            '199', 95, 'W87', 'W88', 'Round of 16', NULL,
            '2026-07-07', '03:00', 'Test Stadium', 'Test City',
            'Portugal', 'Spain', 0, 1, 'FINISHED'
        )
        """
    )
    conn.execute("INSERT INTO prediction_learning_log VALUES ('log-1', '199')")
    conn.execute("INSERT INTO postmatch_process_eval VALUES ('eval-1', '199')")
    conn.commit()
    conn.close()

    report = build_project_state_report(
        db_path,
        repo_root=repo_root,
        include_accuracy=False,
        include_db_integrity=False,
    )

    stage = report["competition_state"]["stage_summary"][0]
    match = report["competition_state"]["tracked_matches"][0]

    assert stage["finished"] == 1
    assert stage["postmatch_review_present"] == 1
    assert stage["v410_postmatch_complete"] == 0
    assert match["postmatch_review_present"] is True
    assert match["v410_postmatch_complete"] is False
    assert "postmatch_eval" in match["postmatch_missing"]
