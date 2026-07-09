# Current Project State

Last refreshed: 2026-07-09

This file is the human-readable entry point. The machine-readable fact source is:

`reports/audits/current_project_state.json`

Refresh it before making tournament-state claims:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/build_project_state_report.py --output reports/audits/current_project_state.json
```

## Source Of Truth

Use these as project facts:

- Local SQLite DB: `backend/data/local_stage2.db`
- Stored reports and memory files
- `evidence_items` and `match_data_raw` raw ledgers
- Project audit scripts
- `reports/audits/current_project_state.json`

Do not use these as authority:

- Compacted chat summaries
- Uncited AI memory
- Single web-search snippets
- Unpersisted browser summaries

## Current Version

- Version: `4.11.0-alpha`
- Build: `V4.11.0-alpha - Match Data OS + game-state postmatch engine`
- DB integrity: `ok`
- Foreign-key violations: `0`

## Accuracy OS

Current evaluation registry facts from the generated state report:

- Total samples: `91`
- Strict eligible samples: `36`
- Diagnostic samples: `47`
- Rejected samples: `8`
- Source-result conflicts: `0`

Important: strict samples are still below the `50+` target. Candidate models, calibrators, and stacking changes remain shadow/proposal-only unless walk-forward paired evidence clears the gate.

## Tournament State In DB

The DB currently has `104` schedule rows.

| Stage | Total | Finished | Teams Known | Empty Team Slots | Postmatch Review Present | V4.10+ Postmatch Complete |
|:---|---:|---:|---:|---:|---:|---:|
| Group Stage | 72 | 65 | 72 | 0 | 36 | 0 |
| Round of 32 | 16 | 15 | 15 | 1 | 15 | 0 |
| Round of 16 | 8 | 8 | 8 | 0 | 8 | 3 |
| Quarterfinal | 4 | 1 | 1 | 3 | 1 | 1 |
| Semifinal | 2 | 0 | 0 | 2 | 0 | 0 |
| Third Place Playoff | 1 | 0 | 0 | 1 | 0 | 0 |
| Final | 1 | 0 | 0 | 1 | 0 | 0 |

Key distinction:

- `postmatch_review_present` means there is DB/report evidence that a review or learning pass exists.
- `v410_postmatch_complete` means all newer V4.10+ closed-loop fields are present, including signal evaluations and postmatch eval linkage.

Do not tell the user "R16 was not reviewed" merely because `v410_postmatch_complete < 8`. The DB state is: R16 has `8/8` finished and `8/8` postmatch review presence; only `3/8` are complete under the stricter V4.10+ field checklist.

## Current Operational Counts

- `evidence_items`: `15`
- `information_state_signals`: `12`
- `signal_evaluations`: `12`
- `match_data_raw`: `1`
- `match_events`: `21`
- `match_game_state_segments`: `8`
- `model_change_proposals`: `33`
- `model_weight_proposals`: `0`

## Known Risks

- `P0`: strict eligible sample count is `36`, below the `50+` target.
- `P0`: Quarterfinal stage has `3` scheduled rows with empty team slots in DB.
- `P1`: One Round of 32 row is still scheduled with empty teams.
- `P1`: Some completed knockout matches lack one or more V4.10+ closed-loop fields. This does not mean they were never reviewed.
- `P2`: Semifinal and Final team slots are naturally unresolved until upstream matches are resolved.

## Required Operating Rules

- Completed-match facts come from DB plus stored verification/review artifacts, not from one-off web snippets.
- For new or future matches, real-time news, weather, lineup/injury information, and market odds remain core inputs.
- External information must be persisted through evidence/raw ledgers with source URL, fetched time, available time, and hash where possible.
- FIFA Match Centre pages may be front-end shells; use official provider adapters and stored raw payloads when available.
- Post-match official data can support reviews and learning logs, but must not enter same-match pre-match strict snapshots.
- Do not alter historical predictions, production weights, model artifacts, or historical probabilities without an explicit user request.

