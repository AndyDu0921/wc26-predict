# Result Conflict Reconciliation - 2026-07-06

## Scope

This audit records one data correction in `backend/data/local_stage2.db`.

- Match: Belgium vs Iran
- Date: 2026-06-21
- Local sample id: `36e346ef4050d6a2`
- Table corrected: `match_results`
- Row id: `1d3cc38bbff64161933ee94d7453c74e`

## Evidence

Two independent public result sources list the match as a 0-0 draw:

- ESPN final score page: https://www.espn.com/soccer/match/_/gameId/760451/iran-belgium
- LA Times match report: https://www.latimes.com/sports/soccer/story/2026-06-21/irans-beleaguered-world-cup-team-finds-hope-belgium-draw

Local `wc26_schedule` already stored `Belgium 0-0 Iran`, while
`match_results` incorrectly stored `Belgium 2-1 Iran`.

## Action

Updated only `match_results.home_goals` and `match_results.away_goals` for
the Belgium vs Iran row:

- Before: `2-1`
- After: `0-0`

## Safety Boundary

- Did not modify prediction probabilities.
- Did not modify production weights.
- Did not overwrite model artifacts.
- Did not create or edit historical reports for this match.
- Created a local ignored DB backup before the correction:
  `_archive/db_backups/20260706/local_stage2.before-belgium-iran-result-reconcile-20260706T151125Z.db`

## Residual Risk

Some historical experiment artifacts still contain the old `2-1` result for
Belgium vs Iran. They are preserved as historical artifacts and should not be
used as current truth without regeneration from the corrected database.
