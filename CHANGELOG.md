# Changelog

All notable changes to WC26 Predict will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [4.9.0-alpha] — 2026-07-05

### Added
- **Accuracy Data OS repair report**: `evaluation_registry_repair_report.v1` produces deterministic repair actions for diagnostic/rejected samples without creating snapshots, probabilities, artifacts, or reports.
- **Structured pre-match intelligence layer**: `information_state_signals.v1` converts local player availability evidence into source/time/confidence-scored shadow signals and excludes future evidence from strict features.
- **FeatureSnapshot v2**: strict feature payloads now include information-state signals, player availability shadow payloads, schedule context, and data quality fields while still excluding actual goals.
- **V4.9 shadow candidate names**: dynamic Bayesian weighted goal model, international covariate hybrid, Dirichlet calibration candidate, and proper-scoring stacking candidate are available as shadow/proposal-only candidates.
- **Data-repair proposal type**: `model_change_proposals` can now record `data-repair`, `calibrator`, and `stacking` proposal categories separately from ordinary model proposals.
- **Legacy experiment wrappers**: old full-pipeline backtest, score grid, and stacking collection scripts now delegate to the unified accuracy experiment runner and no longer overwrite old artifacts.

### Changed
- Version source of truth is now `4.9.0-alpha`.
- Candidate experiment output includes `candidate_family` and `sample_quality_summary` so proposal review can distinguish model, calibration, stacking, and data-quality failures.
- `run_accuracy_experiments.py` defaults to the V4.9 candidate names while retaining backward-compatible candidate aliases.
- Feature snapshot materialization persisted 25 new V4.9 strict audit rows; `feature_snapshots` now contains 66 audit rows.
- Proposal generation is idempotent and inserted one new data-repair proposal; `model_change_proposals` now contains 12 rows.

### Verified
- Backend test suite: `516 passed, 4 skipped`.
- Evaluation registry repair smoke: `81` total samples, `25` strict, `46` diagnostic, `10` rejected; `46` diagnostic samples are potentially promotable only with real pre-kickoff evidence.
- Default shadow experiment smoke still rejects all candidates because strict eligible sample count is `25 < 30`.
- Low-threshold engineering smoke confirms dynamic candidates run end-to-end, but this is not production evidence.
- Legacy wrappers for `backtest_full_pipeline.py`, `grid_search_score_params.py`, and `collect_stacking_training_data.py` execute through the unified runner.

## [4.8.0-alpha] — 2026-07-04

### Added
- **Accuracy Engine v2**: `evaluation_registry.v2` is now the only strict backtest sample gateway, with explicit strict / diagnostic / rejected sample statuses, leakage status, horizon, model version, data availability, and registry hash.
- **PredictionKernel**: async API, sync CLI, and batch paths now share a pure deterministic fusion kernel for core probability fusion and provenance generation.
- **Accuracy audit tables**: Alembic migration `e5f6a7b8c9d0` adds `feature_snapshots`, `experiment_runs`, `candidate_predictions`, and `model_change_proposals` without touching production weight tables.
- **Shadow candidate pool**: dynamic Dixon-Coles, dynamic bivariate Poisson, Bayesian weighted dynamic, covariate ML, Dirichlet calibration, stacking optimizer, and uniform/current baselines can be evaluated offline.
- **Unified experiment runner**: `run_accuracy_experiments.py` compares champion vs candidate with paired Brier, LogLoss, RPS, score LogLoss, ECE, group metrics, leakage checks, and gate decision.
- **Proposal-only self-evolution ledger**: `model_change_proposals` records model, calibrator, feature-rule, and weight proposals without applying them to production configuration.
- **Feature snapshot materializer**: `materialize_feature_snapshots.py` persists pre-result feature payloads for strict registry samples without storing actual goals or changing production state.

### Changed
- Version source of truth is now `4.8.0-alpha`; current WC weight labels are group `WORLD_CUP_V4.7.0_ALPHA` and knockout `WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA`. Generic self-evolution proposals do not auto-apply production weights.
- Legacy backtest/grid/stacking scripts now point to the new accuracy experiment runner for current evidence generation while remaining available for historical reproducibility.
- Documentation now separates verified local facts from historical V4.3-V4.7 architecture text and avoids unverified accuracy claims.
- Schedule-only finished rows with no source conflict can now supply canonical results; kickoff time resolves from `wc26_schedule.match_date + kickoff_time` when `matches.match_date` is unavailable.

### Fixed
- Process-evaluation shot-volume deltas remain unavailable when pre-match expected shots are missing, preventing fake zero-error learning signals.
- Candidate gates reject metric regressions, identity/no-op candidates, insufficient samples, and candidates whose apparent gains are not supported by paired proper-scoring evidence.
- Legacy memory backfill no longer creates placeholder `prediction_runs` by default; synthetic placeholder runs require the explicit `--allow-placeholder-runs` escape hatch.

### Verified
- Backend test suite: `508 passed, 4 skipped`.
- Evaluation registry v2 dry-run: `81` total local samples, `81` canonical result rows, `58` match-result rows, `81` schedule-finished rows, `25` strict eligible samples, `46` diagnostic samples, `10` rejected samples.
- Feature snapshot materialization: `25` strict pre-result payloads built; persistence is idempotent (`inserted=0, skipped=25` after the latest rerun). The table currently contains `41` rows because earlier V4.8 materializations are retained as audit history.
- Local Alembic head: `e5f6a7b8c9d0`.
- Shadow candidate smoke: no candidate is production-ready on the current `25` strict samples. Dynamic DC, dynamic bivariate Poisson, and Bayesian weighted dynamic remain `shadow_needs_more_evidence`; player availability worsened Brier/LogLoss/RPS in shadow and is rejected.

## [4.7.0-alpha] — 2026-07-03

### Added
- **BacktestGate proposal-only workflow**: model-weight candidates are persisted as auditable proposals and never mutate production weights automatically.
- **ModelWeightProposal audit table**: Alembic migration + ORM model for weight-candidate evidence.
- **Evaluation registry**: reconciles `matches + match_results`, `wc26_schedule`, pre-match snapshots, prediction snapshots, and process eval rows before any strict backtest.
- **Shadow candidate experiment runner**: emits paired Brier/LogLoss/RPS/ECE/group metrics and leakage checks without changing production weights or artifacts.
- **Player availability shadow component**: converts availability records into auditable xG modifier candidates while keeping production probabilities unchanged.
- **Shared score-matrix fusion helper**: sync and async prediction paths now share the DC + NegBin + Weibull score-matrix fusion logic.
- **Stacking safety gates**: the meta-learner now requires all three outcomes (home/draw/away) before fitting or loading an artifact.
- **Replayable snapshot metadata**: snapshots preserve historical weight config, pre-market probabilities, market weight used, NegBin weight, and calibration state.

### Changed
- World Cup weight metadata now reports `WORLD_CUP_V4.7.0_ALPHA` / `WORLD_CUP_KNOCKOUT_V4.7.0_ALPHA` without changing the numeric weights.
- `PredictionSnapshot` and app settings now default model version from `app.version.VERSION`.
- Core fusion returns NegBin component probabilities directly so async/sync downstream consumers use the same component evidence.
- Alembic migrations are idempotent for the observed local SQLite drift state, allowing upgrade from `f7a8b9c0d1e2` to `d4e5f6a7b8c9`.

### Fixed
- Async `PredictionPipeline.predict_match()` now runs Weibull scenario rules after Elo is available; the previous order caused the guard to fall back to full Weibull weight.
- Async stacking integration now passes canonical component probabilities instead of a component-name list.
- Invalid or partial stacking artifacts now degrade to uniform fallback instead of emitting incomplete probability dictionaries.
- `process_evaluator` no longer computes shot-volume deltas as actual shots minus actual shots. Missing pre-match expected shots are now recorded as unavailable instead of fake zero error.

### Verified
- Backend test suite: `483 passed, 4 skipped`.
- Evaluation registry dry-run: `78` total local samples, `58` canonical match-result rows, `9` strict eligible backtest samples.

## [4.5.0-beta] — 2026-07-01

### Added
- **A3 Stacking Meta-Learner**: Multinomial Logistic Regression on 21 features (7 components × 3 outcomes). Feature-flagged (`STACKING_META_LEARNER_ENABLED = False`). Trained on 58 WC26 matches via walk-forward backtest.
- **B1 Weighted Conformal Prediction**: Split-conformal with exponential recency weighting (halflife=30d, α=0.1). Feature-flagged (`WEIGHTED_CONFORMAL_PREDICTION_ENABLED = False`).
- `backend/app/core/stacking_features.py` — Pure feature engineering (21-element vector).
- `backend/app/core/conformal_core.py` — Pure math: nonconformity score + recency weight + prediction set.
- `backend/app/services/stacking_meta_learner.py` — Service layer: fit/predict/save/load with sklearn compatibility.
- `backend/app/services/conformal_predictor.py` — Calibration records store + lazy threshold computation.
- `backend/scripts/collect_stacking_training_data.py` — Walk-forward backtest collecting all 7 component probabilities.
- 35 new tests (`test_stacking_features.py` + `test_conformal_core.py`), total now 287.

### Changed
- Pipeline integration: A3/B1 at steps 10.8/10.9 in both `predict_match()` and `predict_sync()`.
- Artifact cleanup: removed redundant `conformal_calibration_records.json`.

### Fixed
- README badges stale (V4.3.0 → V4.5.0, 213 tests → 287, Phase 2B → A3+B1).
- Training data description: 54 → 58 matches.
- NegBin-DC feature overlap risk documented in `MAGIC_NUMBERS.md§15`.
- sklearn 1.9.0 `multi_class` parameter removed — handled via try/except TypeError.

---

## [4.4.2-beta] — 2026-06-30

### Added
- P1-1: Full-pipeline walk-forward backtest script (`backtest_full_pipeline.py`).
- P1-2: Effective weights report automation — nominal vs effective weight comparison.
- `backend/app/configs/full_pipeline_backtest_results.json`.

### Changed
- DC half-life confirmed optimal at 180d via full-pipeline (not just DC-component) backtest.
- Sequential fusion effective weights documented: DC 0.90 → effective 0.52-0.59.

---

## [4.4.1-beta] — 2026-06-29

### Added
- **P0-1**: Outcome-Constrained Score Matrix Calibrator (`score_matrix_calibrator.py`). Aligns score matrix with final H/D/A probabilities.
- **P0-2**: Post-Calibration KO Draw Guard (`ko_draw_guard.py`). Warnings for systematic KO draw underestimation.
- **P0-3**: λ formula source audit — flagged `_win_prob_to_xg()` as `UNVERIFIED_SOURCE`, added feature flag.
- **P0-4**: Gates (`verification_gates.py`) integrated into `prediction_pipeline.py` library path.
- 4 new test files for P0-1 through P0-4.

### Changed
- `tournament_simulator.py`: λ polynomial gated behind `use_cg_lambda_polynomial` flag (default off).
- `MAGIC_NUMBERS.md`: λ formula marked as UNVERIFIED.

---

## [4.3.11-beta] — 2026-06-29

### Changed
- **B2 MC λ Upgrade**: Replaced heuristic with Csató & Gyimesi (2025) EJOR polynomial — 40,000-match fitted `λ = 3.904W⁴ − 0.585W³ − 2.983W² + 3.132W + 0.332`.
- **B3 Domain-Driven De-Vig**: Improved proportional de-vig considering bookmaker behavioral biases (draw/away systematic overestimation), validated on 359,035 matches (Karimov et al. 2025).

---

## [4.3.10-beta] — 2026-06-29

### Added
- **AGENTS.md**: Agent behavior constitution — every line corresponds to a historical error pattern.
- **MAGIC_NUMBERS.md**: Central registry — 47 magic numbers across 15 files, each with provenance, rationale, and modification history.
- **Verification Gates**: `verification_gates.py` — preflight/postflight checks for prediction pipeline.
- **Memory System Bridge**: `memory_bridge_check.py` — cross-references memory files with DB records for consistency.
- **KO Draw Tracker**: Formalized tracking of knockout-stage draw probability patterns.

---

## [4.3.9-beta] — 2026-06-29

### Added
- WC26 knockout schedule import — 16 R32 fixtures from FIFA official bracket.

### Changed
- MEX vs ECU: First-ever "direction = DRAW" knockout prediction (37.8%).

---

## [4.3.8-beta] — 2026-06-30

### Added
- FRA vs SWE full prediction + multi-bookmaker market data.

---

## [4.3.7-beta] — 2026-06-30

### Fixed
- Côte d'Ivoire Elo rating: 1500 default value → real World Cup Elo. Fixed 29.7pp market-model divergence.
- Multi-bookmaker cross-validation methodology established.

---

## [4.3.6-beta] — 2026-06-29

### Added
- NED vs MAR (1-1) knockout post-match review. 2/4 KO matches ended in draws (50% actual vs ~20% predicted).

### Changed
- Learning engine: KO draw rate tracking updated.
- KO draw guard: warning threshold documented.

---

## [4.3.3-beta — 4.3.5-beta] — 2026-06-29

### Added
- SA vs CAN (0-1), BRA vs JPN (2-1), GER vs PAR (1-1) knockout post-match reviews.
- 4-match KO cumulative panel: 2 correct direction, 2 draw underestimates.
- KO draw calibration: manual multipliers (×1.15 – ×1.18) and floors (18%–22%).

---

## [4.3.2-beta] — 2026-06-27

### Added
- Multi-bookmaker web-search consensus as Tier 3 market data fallback.
- 12-bookmaker cross-validation pipeline.
- Full WC26 knockout schedule (Beijing time).

---

## [4.3.1-beta] — 2026-06-27

### Added
- **P1-1 Post-Fusion Isotonic Calibration**: Calibrator applied after fusion chain. 69-sample training set. ECE = 0.052.
- **P1-4 Enhancer Diagnostics**: Systematic bias diagnosis — Enhancer 23% directional accuracy (3/13), confirmed underdog bias.

### Changed
- **P1-2 DC Time Decay**: Grid search over half-life [30, 60, 90, 180, 365] — 180d confirmed optimal via walk-forward CV.
- **P1-3 Dead Code**: Removed 7 dead imports, deprecated Gate decisions, unused snapshot enum.

---

## [4.3.0-beta] — 2026-06-26

### Added
- **NegBin 5% Fusion**: Negative Binomial component with `R=3.5`, 5% fusion weight. Corrects Poisson over-dispersion.
- **Group Standings Table**: Real-time WC26 group standings in DB (`wc26_group_standings`).
- **Calibrator Rebuild**: `calibrator_wc.json` rebuilt with 69 WC26 matches, threshold lowered to 20.
- **Shared Fusion Engine**: `core/engine.py` — 7-component sequential fusion, single implementation serving CLI + API + Dashboard.
- **Weight Proposal Gate**: `weight_proposal.py` + `backtest_gate.py` — institutionalized learning with mandatory backtest verification.
- **Golden Prediction Fixtures**: 4 characterization tests locking down prediction outputs.
- Complete PRD/Architecture document (918 lines).

### Changed
- Fusion chain unified: 5 duplicated code paths → 1 `run_core_fusion()` function.
- Learning engine: Bug 29 fixed — true sequential fusion marginal contribution (not weighted average).
- Repository: Bilingual README (EN+ZH), community docs (LICENSE, SECURITY, CONTRIBUTING, PR template, Issue templates).

### Fixed
- DC-Enhancer divergence guard: 20pp threshold → `dc_adaptive=0.30` floor.
- Market boost: 15pp divergence trigger, 20% max boost, ×0.6 attenuation.
- Draw floor: 12% minimum for WC matches.
- xG calibration factor: 1.35 for WC systematic underestimation.

---

## [4.2.2-beta] — 2026-06-25

### Changed
- Self-evolution: Pi weight 0.12 → 0.14 (5/6 directional accuracy, best non-market component).
- 6-match June 25 post-match review batch complete.

---

## [4.2.1-beta] — 2026-06-25

### Fixed
- 8 fixes from audit: pipeline sync, motivation factor integration, draw floor enforcement, divergence paradox resolution, predictor weight column names, Weibull WC KO weight, daily_ops dead script references, signal accuracy tracking.

---

## [4.2.0-beta] — 2026-06-24

### Added
- **Motivation Factor**: Team motivation modeling for group stage match-day 3.
- **Draw Floor**: 12% minimum draw probability for WC matches.
- **Divergence Paradox Fix**: Resolved counter-intuitive market-model interaction.
- Post-match review standardization (SOP in `POSTMATCH_SOP.md`).

---

## [4.1.6-beta] — 2026-06-24

### Changed
- Global version synchronization across all files.
- Codebase cleanup: dead files removed, imports consolidated.

---

## [4.1.5-beta] — 2026-06-24

### Fixed
- Calibrator skip logic for WC matches.
- Dead code sweep: unused scripts and market providers removed.

---

## [4.1.4-beta] — 2026-06-24

### Fixed
- API key rotation — live market data restored after provider key expiry.

---

## [4.1.3-beta] — 2026-06-24

### Fixed
- `predict_sync()` market data flow: manual-odds fallback chain, calibrator skip for low-sample scenarios.

---

## [4.1.2-beta] — 2026-06-24

### Changed
- Removed 17 dead files from the repository.
- Market data pipeline: manual-odds priority restored.

---

## [4.1.1-beta] — 2026-06-23

### Fixed
- 5 consistency fixes across pipeline paths.
- Direction-conflict guard: prevents contradictory predictions.
- Prediction regeneration for 5 matches with calibration overflow.

---

## [4.0.9-beta] — 2026-06-23

### Fixed
- 8 critical issues across 4 files (global audit).
- 2 additional consistency fixes from follow-up review.

---

## [4.0.8-beta] — 2026-06-22

### Added
- Dynamic market boost in both `predict_match()` and `predict_sync()` paths.
- Calibration enabled in pipeline paths (R4-C7).
- Weibull + Pi-Rating integration in orchestrator (R4-C5).

### Fixed
- Dead DB weight keys: `dc_weight`, `enhancer_weight`, `elo_blend`, `pi_weight` (R4-C8).
- Orchestrator 22% blind spot: Weibull/Pi missing from API path.

---

## [4.0.7-beta] — 2026-06-21

### Fixed
- 14 issues from R4 deep audit across data integrity, pipeline consistency, and model accuracy.
- R4-C6: NameError in orchestrator (`comp_weight` scope).
- R4-H8: Auto-optimized weights always rejected due to missing `weibull`/`market_max` keys.

---

## [4.0.6-beta] — 2026-06-21

### Fixed
- 18 issues from R3 deep audit across pipeline synchronization, data integrity, and model accuracy.

---

## [4.0.5-beta] — 2026-06-21

### Fixed
- 8 issues from R2 deep audit. Unified pipelines, data integrity restored.

---

## [4.0.4-beta] — 2026-06-20

### Added
- WC knockout weight configuration (`WORLD_CUP_KNOCKOUT_V4.3.1`).

---

## [4.0.3-beta] — 2026-06-20

### Changed
- V3.9.8: Pi weight 5% → 8% based on Norway-Senegal post-match evaluation.
- All version identifiers unified to `V4.0.3-beta`.

---

## [4.0.2-beta] — 2026-06-20

### Changed
- V3.9.7: WC weight architecture based on Argentina-Austria post-match.

---

## [4.0.1-beta] — 2026-06-19

### Fixed
- The Odds API WC sport key + region configuration. Market data now 4/4 matches.

### Changed
- Weather data saved in prediction JSON — single source of truth.

---

## [4.0.0-beta] — 2026-06-18

### Added
- 4-match WC prediction batch: Argentina-Austria, France-Iraq, Norway-Senegal, Jordan-Algeria.
- Spain 4-0 Saudi post-match review + self-evolution.

### Fixed
- Replaced fabricated weather/injury data with real Open-Meteo API + web search data.

---

## [3.9.6] — 2026-06-18

### Changed
- WC weight rebalance: Elo 0.03 → 0.08, Enhancer 0.45 → 0.30.
- xG calibration factor: 1.35 → 1.20.
- Massive codebase cleanup.

---

## [3.9.5] — 2026-06-17

### Added
- **Negative Binomial Component**: Over-dispersion correction (R=3.5).
- **Divergence-Adaptive DC Weight**: Auto-reduces DC when DC-Enhancer > 20pp.
- CI confidence intervals in prediction reports.

---

## [3.9.0 – 3.9.4] — 2026-06-13 to 2026-06-16

### Added
- 7-component prediction pipeline: DC, Enhancer, Weibull, Elo, Pi, Market, NegBin.
- Post-match evaluation with Brier/LogLoss/RPS.
- Bootstrap CI for uncertainty quantification.
- Self-evolution learning engine with margin-attribution.

---

## [3.8.0] — 2026-06-15

### Changed
- Model loading: static artifacts → disk cache only (single-source-of-truth).
- Weight gating + parameter provenance tracking.

---

## [Initial] — 2026-05-26

- Project inception: Dixon-Coles Poisson model for WC 2026 predictions.
