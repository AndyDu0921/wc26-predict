# Current Project State

Last reviewed: 2026-07-18

This document is the human-readable state entry point. Refresh the machine-readable fact source before making operational claims:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/build_project_state_report.py --output reports/audits/current_project_state.json
```

## Authority

Use, in order:

1. `backend/data/local_stage2.db` and immutable pre-match snapshots.
2. Stored prediction/postmatch reports and memory files.
3. `evidence_items`, `information_state_signals`, and `match_data_raw` ledgers.
4. Project audit scripts and `reports/audits/current_project_state.json`.
5. External sources only after URL, timestamp, and raw/hash evidence are persisted.

Do not treat chat summaries, previous AI memory, search snippets, or unpersisted browser summaries as facts.

## Version And Branch

- Code version: `4.12.0-alpha`.
- Build: first-principles accuracy and closed-loop hardening.
- Working branch: `codex/v412-first-principles-accuracy-audit`.
- Production WC numeric weights were not changed.
- Active runtime bundle status: `legacy_active_unvalidated`.
- Active bundle files are hash-verified, but exact training cutoff is still `unknown`; this is provenance debt, not proof of leakage or proof of validity.

## Engineering State

- One inference adapter: `app.services.canonical_prediction_core.execute_prediction_core`.
- Manual pre-match CLI: `backend/scripts/predict_match_full.py`.
- API/admin/worker trigger: `app.services.canonical_prediction_runner.run_canonical_prediction`.
- Manual and worker postmatch path: `backend/scripts/run_postmatch_complete.py`.
- Required model artifacts fail closed when missing, tampered, or incompatible.
- Offline training writes immutable `candidate_unvalidated` bundles and cannot replace `active_bundle.json`.
- Learning writes proper scores, approximate attribution, signal evaluation, and proposals; it does not mutate production weights, multipliers, or artifacts.
- Score matrices cover goals `0..10` and are reconciled to final H/D/A probabilities after guards/calibration.
- Local SQLite integrity: `ok`; foreign-key violations: `0`.
- Final local suite: `617 passed`; Ruff: `0` findings; compileall and `git diff --check` pass.
- Alembic current and head are both `i9d0e1f2g3h4`; SQLite integrity is `ok` with `0` foreign-key violations.
- Async API/worker triggers run synchronous canonical inference in a worker thread, so weather collection no longer loses data to an active-event-loop conflict.

## Honest Accuracy State

Evaluation registry:

| Population | Count |
|:---|---:|
| Independent matches | 93 |
| Strict eligible | 35 |
| Diagnostic | 47 |
| Rejected | 11 |
| Strict rows with legacy exact-zero probabilities | 5 |

Pooled strict descriptive metrics across incompatible historical cohorts:

| Metric | Value | Important limitation |
|:---|---:|:---|
| Direction accuracy | 60.00% | Mixed model versions; not current-production evidence |
| Brier | 0.514731 | Proper score; lower is better |
| LogLoss | 1.590134 | Distorted by five legacy zero-boundary forecasts |
| RPS | 0.193357 | Mixed cohorts |
| ECE | 0.241351 | Small, heterogeneous sample |
| Score LogLoss | 3.005877 (`n=12`) | Score matrices exist for only 12 strict rows |
| Exact score | 1/12 | Descriptive only |
| Top-3 score | 3/12 | Descriptive only |

After excluding the five legacy boundary-probability rows, pooled LogLoss is `0.886515` and direction accuracy is `56.67%` (`n=30`). This robustness slice is still mixed-cohort evidence.

Latest completed predecessor cohort, `4.11.0-alpha`, has only `n=4` strict rows:

- Brier `0.397069`
- LogLoss `0.716061`
- RPS `0.176740`
- Direction `75%`
- ECE `0.454929`
- Score LogLoss `2.563519`
- Exact score `0/4`; Top-3 `1/4`

These four rows are descriptive, not statistically persuasive. V4.12 has `0` completed same-cohort rows, so **no V4.12 accuracy improvement is proven**. Promotion preflight must remain blocked until same-cohort evidence meets the configured sample and paired-CI gates.

The final pooled nine-candidate shadow tournament completed against registry hash `7793416a258b2ed05d5bff2686dc7164010e46a299563d5490320ff6eb2070cd`. No candidate passed:

- Dynamic Dixon-Coles and empirical-Bayes weighted goal candidates worsened core proper scores.
- Dynamic bivariate Poisson improved point estimates (`Brier 0.498654`, `LogLoss 0.856690`, `RPS 0.180847`, direction `74.29%`) but all H/D/A paired CIs crossed zero; on the 12 score-matrix pairs it worsened Score LogLoss by `+0.214121` with CI `[+0.025018, +0.414218]`.
- Player availability produced only about `-0.001` Brier improvement and its CIs crossed zero.
- Covariate, Dirichlet, and stacking candidates were unavailable because same-cohort prior samples were insufficient.

This tournament is pooled diagnostic evidence only. It cannot activate or recommend a production model.

## Score Forecast Meaning

The score output is a joint discrete distribution:

```text
P(home_goals=i, away_goals=j | information available at prediction freeze)
```

It is not a claim that the Top-1 score will occur. Football scorelines are noisy because finishing, red cards, penalties, goalkeeper performance, tactical state, and late-game substitutions create large conditional variance. Model selection should prioritize Score LogLoss, total-goal and goal-difference calibration, Top-k coverage, and uncertainty. Exact-score hit rate is secondary.

## Local Competition State

The following is what the local DB records, not a claim about current real-world FIFA state:

| Stage | Rows | Finished in DB | Pre-match snapshot present | Postmatch review present |
|:---|---:|---:|---:|---:|
| Group Stage | 72 | 65 | 0 | 36 |
| Round of 32 | 16 | 15 | 10 | 15 |
| Round of 16 | 8 | 8 | 8 | 8 |
| Quarterfinal | 4 | 4 | 4 | 1 |
| Semifinal | 2 | 0 | 1 | 0 |
| Third Place | 1 | 0 | 0 | 0 |
| Final | 1 | 0 | 0 | 0 |

The schedule/result ledger is stale beyond the quarterfinals and must be refreshed only from verified sources. Do not infer current tournament state from this table without a new collection pass.

Operational counts:

- `evidence_items`: 43
- `information_state_signals`: 38
- `signal_evaluations`: 19
- `match_data_raw`: 2
- `match_events`: 23
- `match_game_state_segments`: 16
- `model_change_proposals`: 45
- `model_weight_proposals`: 0

## Primary Risks

- `P0`: V4.12 has no completed same-cohort evaluation sample; the latest predecessor has only four.
- `P0`: only 35 strict rows exist, and five contain legacy exact-zero probabilities.
- `P0`: active artifact training cutoff/fingerprint provenance is incomplete even though file integrity is now verified.
- `P1`: local tournament schedule/results are stale after the quarterfinals.
- `P1`: 23 finished knockout rows lack one or more V4.10+ postmatch fields. Review presence and full modern closed-loop completion are not the same thing.
- `P1`: only 12 strict rows have usable score matrices; exact-score claims are severely underpowered.
- `P2`: runtime model files are local ignored assets. A clean machine must provision the exact bundle files before prediction can run.
- `P2`: Bandit has `0` high-severity findings. Its remaining medium findings are B608 dynamic-SQL heuristics reviewed as fixed internal identifiers or generated `?` placeholders with parameterized values; keep this boundary under test when adding new SQL.

## Next Actions

1. Freeze V4.12 and collect at least 30, preferably 50+, genuinely same-cohort predictions before promotion research.
2. Persist exact training cutoff, row fingerprint, competitions, and code revision for the next candidate bundle.
3. Repair diagnostic samples only from real pre-kickoff evidence; never manufacture probabilities or times.
4. Expand score evaluation with dynamic latent attack/defence, overdispersion, low-score dependence, posterior rate uncertainty, and regime mixtures.
5. Add market totals/handicap/BTTS evidence alongside 1X2 for score-distribution identification.
6. Backfill modern postmatch fields only where real snapshots, verified results, and source evidence already exist.
7. Refresh tournament state from verified providers before any new match operation.
8. Treat dynamic bivariate Poisson as the leading research hypothesis, but first improve score-distribution construction and collect same-cohort evidence; do not promote its current implementation.
