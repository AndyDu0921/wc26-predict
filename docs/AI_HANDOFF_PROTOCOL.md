# AI Handoff Protocol

This project must be operated from persisted facts, not conversational memory.

## Mandatory Startup

Before answering tournament-state questions or changing code/data:

1. Read `docs/CURRENT_PROJECT_STATE.md`.
2. Run:

   ```powershell
   backend/.venv/Scripts/python.exe backend/scripts/build_project_state_report.py --output reports/audits/current_project_state.json
   ```

3. Read the relevant match rows in `reports/audits/current_project_state.json`.
4. Check `git status --short --branch`; never revert unrelated dirty-worktree changes.
5. Run the relevant audit after every material operation.

## Fact Hierarchy

1. SQLite rows plus immutable pre-match snapshots.
2. Stored reports/memory and evidence/raw ledgers.
3. Official provider adapters and persisted payloads.
4. Project audit output.
5. External pages only after source URL, retrieval time, and raw/hash evidence are captured.

Chat summaries, AI recollection, snippets, and unpersisted web text are hints only.

## Canonical Entrypoints

- Manual prediction: `backend/scripts/predict_match_full.py`.
- Manual postmatch review: `backend/scripts/run_postmatch_complete.py`.
- API/admin/worker prediction: `app.services.canonical_prediction_runner.run_canonical_prediction`.
- All model inference enters `app.services.canonical_prediction_core.execute_prediction_core`.
- Worker postmatch delegates to `run_postmatch_complete.py`; do not recreate a second evaluator.

Before handing off entrypoint work, run:

```powershell
backend/.venv/Scripts/python.exe backend/scripts/smoke_canonical_trigger.py
backend/.venv/Scripts/python.exe backend/scripts/audit_entrypoints.py --json
```

The smoke uses a temporary DB copy. Real DB row counts must remain unchanged.

## Prediction Contract

- A complete prediction persists `prediction_runs`, `pre_match_snapshots`, `prediction_snapshots`, and `feature_snapshots` in one SQLite DB.
- `as_of` is actual generation/freeze time. T-minus horizons are request metadata and must not be recorded as fabricated generation times.
- News, weather, injury/lineup evidence, and market odds are core inputs.
- Every strict evidence item requires traceable source and `available_at <= as_of <= kickoff_at`.
- Missing critical context must be reported as missing; never silently invent it.
- Required model artifacts must match `active_bundle.json` hashes. Do not bypass the bundle or implicitly retrain during inference.
- `active_bundle.json` is the only runtime artifact registry. Do not restore the deleted `artifact_registry.py` or `model_registry.json` paths.
- Training may emit only immutable `candidate_unvalidated` bundles. Tournament simulation must stop on missing predictions and must never insert placeholder probabilities.
- Market odds are valid evidence. Only betting advice, stake instructions, and guaranteed-outcome language are forbidden.

## Postmatch Contract

- Verify results with the project's consensus rules before activating learning.
- Inspect prediction run, snapshots, process eval, learning log, postmatch eval, signal eval, report, and memory.
- Rich official event/player data is postmatch-only for the same match.
- Reruns must be idempotent.
- Learning may write diagnostics and proposals; it must not mutate production weights, multipliers, or artifacts.
- Attribution from component probabilities is approximate unless the full historical nonlinear chain is replayed. Label it honestly.

## Accuracy Claims

Never quote one number without its sample definition.

Required context:

- strict/diagnostic/rejected counts;
- model cohort/version;
- temporal split method;
- Brier, LogLoss, RPS, calibration, and score metrics;
- paired confidence interval;
- boundary-probability and subgroup robustness;
- whether the result is descriptive, shadow evidence, or promotion evidence.

Current invariant: V4.12 has no completed same-cohort sample. The predecessor V4.11 strict cohort has only four. Pooled historical metrics are not proof that V4.12 is better.

## Score Interpretation

The score forecast is `P(HG=i, AG=j | pre-match information)`, not a deterministic score. Optimize Score LogLoss, marginal calibration, Top-k coverage, and uncertainty. Do not tune solely for exact-score hits.

Keep 90-minute, extra-time, penalty-shootout, and advancement probabilities separate.

## Forbidden Without Explicit Approval And Evidence

- Changing production numeric weights.
- Overwriting active artifacts.
- Rewriting historical prediction probabilities.
- Backdating evidence or snapshots.
- Promoting pooled, underpowered, or CI-inconclusive experiments.
- Using postmatch data in the same match's pre-match features.
- Restoring removed orchestrators, wrappers, or duplicate persistence paths.
- Printing or committing credentials.

## Required Closeout

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
backend/.venv/Scripts/python.exe -m ruff check backend/app backend/scripts backend/tests
backend/.venv/Scripts/python.exe -m compileall -q backend/app backend/scripts
backend/.venv/Scripts/python.exe backend/scripts/audit_db_integrity.py
backend/.venv/Scripts/python.exe backend/scripts/audit_report_paths.py --json
backend/.venv/Scripts/python.exe backend/scripts/audit_entrypoints.py --json
backend/.venv/Scripts/python.exe backend/scripts/audit_public_outputs.py
backend/.venv/Scripts/python.exe backend/scripts/preflight_accuracy_experiments.py
git diff --check
```

Regenerate `reports/audits/current_project_state.json`, then report what changed, what was verified, and what remains unproven.
