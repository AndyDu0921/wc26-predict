# Changelog

All notable changes to WC26 Predict will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [4.7.0-alpha] — 2026-07-03

### Added
- **BacktestGate proposal-only workflow**: model-weight candidates are persisted as auditable proposals and never mutate production weights automatically.
- **ModelWeightProposal audit table**: Alembic migration + ORM model for weight-candidate evidence.
- **Stacking safety gates**: the meta-learner now requires all three outcomes (home/draw/away) before fitting or loading an artifact.
- **Replayable snapshot metadata**: snapshots preserve historical weight config, pre-market probabilities, market weight used, NegBin weight, and calibration state.

### Changed
- World Cup weight metadata now reports `WORLD_CUP_V4.7.0_ALPHA` / `WORLD_CUP_KNOCKOUT_V4.7.0_ALPHA` without changing the numeric weights.
- `PredictionSnapshot` and app settings now default model version from `app.version.VERSION`.
- Core fusion returns NegBin component probabilities directly so async/sync downstream consumers use the same component evidence.

### Fixed
- Async `PredictionPipeline.predict_match()` now runs Weibull scenario rules after Elo is available; the previous order caused the guard to fall back to full Weibull weight.
- Async stacking integration now passes canonical component probabilities instead of a component-name list.
- Invalid or partial stacking artifacts now degrade to uniform fallback instead of emitting incomplete probability dictionaries.

### Verified
- Backend test suite: `466 passed, 4 skipped`.

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
