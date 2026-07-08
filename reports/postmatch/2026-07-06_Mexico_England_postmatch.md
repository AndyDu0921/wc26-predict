# 🏆 Post-Match Review: Mexico vs England

**Date**: 2026-07-06
**Score**: 2 - 3
**Verified**: ✅ (2 sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 39.0% | 25.1% | 35.9% |
| Actual | — | — | 100% |

- **Favorite**: home
- **Direction**: ❌ WRONG
- **Brier Score**: 0.6261

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  25.5% /  37.9% /  36.6% | D | ❌ | 0.6102 |
| Enhancer     |  12.4% /  19.3% /  68.4% | A | ✅ | 0.1525 |
| DC+Enhancer  |  24.2% /  36.0% /  39.8% | A | ✅ | 0.5507 |
| NegBin       |  27.7% /  32.0% /  40.2% | A | ✅ | 0.5365 |
| Weibull      |  57.9% /  35.3% /   6.9% | H | ❌ | 1.3266 |
| Elo          |  31.8% /  23.7% /  44.5% | A | ✅ | 0.4646 |
| Pi           |  41.6% /  20.6% /  37.8% | H | ❌ | 0.6021 |
| Market       |  31.4% /  29.7% /  39.0% | A | ✅ | 0.5592 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 0.591 | 1.551 |
| Away xG | 0.775 | 1.944 |

**Stats Completeness**: Full
**Learning Data Quality**: 0.9

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.6261 |
| Direction | overestimate_home |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.4500 |
| Learning Formula | 0.50 × 0.90 × 1.00 |
| Score Log Loss | 5.1438 |
| Exact Score Hit | False |
| Top-3 Score Hit | False |
| DC Score Log Loss | 5.6649 |
| NegBin Score Log Loss | 4.6427 |
| Weibull Score Log Loss | 7.0249 |
| DC Marginal | -0.3161 |
| Enhancer Marginal | -0.0418 |
| Elo Marginal | -0.0507 |


## 🧭 Rich Match Data / Game State

| Metric | Value |
|:---|:---|
| Rich Data Tier | basic_only |
| Event Quality Score | 0.0000 |
| Raw / Events / Shots / Lineups / Player Stats | 0 / 0 / 0 / 0 / 0 |
| Missing Rich Data | official_raw_payload, event_timeline, lineups, player_minutes, shot_events, player_statistics |
| Comeback Profile | unavailable |
| Max Deficit For Winner | N/A |
| Late Comeback | False |

### Goal Timeline

| Minute | Team | Player | Score |
|:---:|:---|:---|:---:|
| N/A | N/A | N/A | N/A |

### Game-State Segments

| Window | Score | Leader | Shots | xG |
|:---:|:---:|:---:|:---:|:---:|
| N/A | N/A | N/A | N/A | N/A |

**Leakage Policy**: post-match-only; never joined into same-match pre-match strict features.


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| N/A | N/A | N/A | no_signals | 0.0000 |

**Signal Evaluations**: 0
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-08T12:54:35.916060+00:00 | Pipeline: run_postmatch_complete.py*
