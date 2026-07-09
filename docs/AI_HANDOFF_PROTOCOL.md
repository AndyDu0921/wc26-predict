# AI Handoff Protocol

This project is too stateful to operate from chat memory. Every AI agent must
recalibrate from project facts before making claims or changing data.

## Mandatory First Steps

Before answering tournament-state questions or running predictions/reviews:

1. Read `docs/CURRENT_PROJECT_STATE.md`.
2. Refresh and read the machine state report:

   ```powershell
   backend/.venv/Scripts/python.exe backend/scripts/build_project_state_report.py --output reports/audits/current_project_state.json
   ```

3. Check the relevant match rows in `reports/audits/current_project_state.json`.
4. If making code or data changes, run the relevant audit script after the change.

## Fact Hierarchy

Use this order of authority:

1. SQLite DB tables and persisted report/memory artifacts.
2. `evidence_items`, `information_state_signals`, `signal_evaluations`, and `match_data_raw`.
3. Official provider adapters and stored raw payloads.
4. Project audit scripts.
5. External web pages only after the source is captured or cited with URL and time.

Never treat these as facts:

- Compacted conversation summaries.
- A previous AI's uncited memory.
- Single web-search result snippets.
- Browser/fetch summaries that were not persisted to evidence/raw ledgers.

## Completed Matches

For completed matches:

- Do not re-litigate already reviewed results from a single external snippet.
- Check DB result, prediction snapshot, learning log, process eval, postmatch eval, report, and memory first.
- If external sources disagree with DB, create a discrepancy note or audit task. Do not overwrite DB unless the user explicitly asks.
- Distinguish review presence from strict V4.10+ completion:
  - `postmatch_review_present`: review/learning evidence exists.
  - `v410_postmatch_complete`: all newer closed-loop fields are present.

## Future Matches

For future or unresolved matches:

- Real-time news, weather, injury/lineup information, and market odds are core signals.
- Do not downplay market odds; they are a major external benchmark and prediction input.
- Persist evidence with source URL, fetched time, available time, and hash where possible.
- Do not use a one-off web summary as the only fact source for DB updates.

## FIFA Official Data

FIFA Match Centre web pages may be front-end shells. If a page cannot be parsed:

- Do not conclude that FIFA has no data.
- Use `collect_official_match_data.py` and the FIFA provider adapter.
- Prefer stored `match_data_raw` payloads over browser summaries.
- Be explicit about coverage: FIFA live payloads may provide score, goals, bookings, substitutions, and lineups without full shot map, shot xG, or technical player statistics.

## Forbidden Without Explicit User Approval

- Do not change production model weights.
- Do not overwrite model artifacts.
- Do not rewrite historical prediction probabilities.
- Do not fabricate kickoff times, probabilities, evidence, or timestamps.
- Do not promote a model from small samples or direction accuracy alone.
- Do not remove market/odds evidence; only remove betting-advice language.

## Required Closeout

After material work:

1. Regenerate `reports/audits/current_project_state.json`.
2. Run targeted tests for changed code.
3. Run `git diff --check`.
4. Report what changed, what was verified, and what remains unresolved.

