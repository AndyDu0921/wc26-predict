# 🏆 Post-Match Review: France vs Morocco

**Date**: 2026-07-10
**Score**: 2 - 0
**Verified**: ✅ (2 sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 53.2% | 21.1% | 25.7% |
| Actual | 100% | — | — |

- **Favorite**: home
- **Direction**: ✅ CORRECT
- **Brier Score**: 0.3301

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  28.4% /  33.0% /  38.6% | A | ❌ | 0.7715 |
| Enhancer     |  18.2% /  36.7% /  45.0% | A | ❌ | 1.0061 |
| DC+Enhancer  |  27.3% /  33.4% /  39.2% | A | ❌ | 0.7936 |
| NegBin       |  30.5% /  27.8% /  41.7% | A | ❌ | 0.7348 |
| Weibull      |  89.7% /   9.1% /   1.2% | H | ✅ | 0.0191 |
| Elo          |  44.4% /  23.7% /  31.9% | H | ✅ | 0.4664 |
| Pi           |  36.7% /  20.6% /  42.7% | A | ❌ | 0.6255 |
| Market       |  60.9% /  24.3% /  14.8% | H | ✅ | 0.2343 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 0.758 | 3.690 |
| Away xG | 0.941 | 0.140 |

**Stats Completeness**: Full
**Learning Data Quality**: 0.9

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.3301 |
| Direction | correct |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.4500 |
| Learning Formula | 0.50 × 0.90 × 1.00 |
| Score Log Loss | 2.3387 |
| Exact Score Hit | False |
| Top-3 Score Hit | False |
| DC Score Log Loss | 2.9461 |
| NegBin Score Log Loss | 2.9048 |
| Weibull Score Log Loss | 27.6310 |
| DC Marginal | 0.1831 |
| Enhancer Marginal | 0.1258 |
| Elo Marginal | 0.1584 |


## 🧭 Rich Match Data / Game State

| Metric | Value |
|:---|:---|
| Rich Data Tier | event_timeline_only |
| Event Quality Score | 0.6000 |
| Data Coverage | events=yes, goals=yes, lineups=no, minutes=no, shot map=no, shot xG=no, player stats=no |
| Data Warnings | no_full_shot_map, no_player_statistics_found |
| Raw / Events / Shots / Lineups / Player Stats | 1 / 2 / 0 / 0 / 0 |
| Missing Rich Data | lineups, player_minutes, shot_events, full_shot_map, shot_xg, technical_player_statistics |
| Comeback Profile | no_comeback |
| Max Deficit For Winner | 0 |
| Late Comeback | False |

### Goal Timeline

| Minute | Team | Player | Score |
|:---:|:---|:---|:---:|
| 60 | France | Kylian Mbappe | 1-0 |
| 66 | France | Ousmane Dembele | 2-0 |

### Game-State Segments

| Window | Score | Leader | Shots | xG |
|:---:|:---:|:---:|:---:|:---:|
| 0-15 | 0-0 → 0-0 | draw → draw | 0-0 | N/A-N/A |
| 16-30 | 0-0 → 0-0 | draw → draw | 0-0 | N/A-N/A |
| 31-45 | 0-0 → 0-0 | draw → draw | 0-0 | N/A-N/A |
| 46-60 | 0-0 → 1-0 | draw → home | 0-0 | N/A-N/A |
| 61-75 | 1-0 → 2-0 | home → home | 0-0 | N/A-N/A |
| 76-90 | 2-0 → 2-0 | home → home | 0-0 | N/A-N/A |
| 91-105 | 2-0 → 2-0 | home → home | 0-0 | N/A-N/A |
| 106-120 | 2-0 → 2-0 | home → home | 0-0 | N/A-N/A |

**Leakage Policy**: post-match-only; never joined into same-match pre-match strict features.


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| France | injury | negative | misleading | -0.0984 |
| France | market_move | neutral | neutral | 0.0000 |
| France | lineup | neutral | neutral | 0.0000 |
| Morocco | lineup | neutral | neutral | 0.0000 |
| Morocco | return | positive | misleading | -0.0560 |
| France | market_move | neutral | neutral | 0.0000 |
| France | weather | negative | misleading | -0.0255 |

**Signal Evaluations**: 7
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-10T07:27:23.163675+00:00 | Pipeline: run_postmatch_complete.py*
