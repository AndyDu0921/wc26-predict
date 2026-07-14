# 🏆 Post-Match Review: Argentina vs Egypt

**Date**: 2026-07-08
**Score**: 3 - 2
**Verified**: ✅ (2 sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 54.0% | 21.4% | 24.7% |
| Actual | 100% | — | — |

- **Favorite**: home
- **Direction**: ✅ CORRECT
- **Brier Score**: 0.3184

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  42.3% /  34.7% /  23.0% | H | ✅ | 0.5062 |
| Enhancer     |  14.8% /  20.4% /  64.8% | A | ❌ | 1.1877 |
| DC+Enhancer  |  39.5% /  33.2% /  27.2% | H | ✅ | 0.5500 |
| NegBin       |  46.1% /  29.1% /  24.8% | H | ✅ | 0.4368 |
| Weibull      |  56.2% /  39.6% /   4.3% | H | ✅ | 0.3503 |
| Elo          |  51.9% /  22.6% /  25.5% | H | ✅ | 0.3476 |
| Pi           |  64.0% /  18.5% /  17.5% | H | ✅ | 0.1949 |
| Market       |  67.8% /  21.5% /  10.7% | H | ✅ | 0.1617 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 0.935 | 2.840 |
| Away xG | 0.603 | 0.980 |

**Stats Completeness**: Partial — missing: possession_home, possession_away, shots_home, shots_away, sot_home, sot_away
**Learning Data Quality**: 1.0

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.3184 |
| Direction | correct |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.5000 |
| Learning Formula | 0.50 × 1.00 × 1.00 |
| Score Log Loss | 4.5056 |
| Exact Score Hit | False |
| Top-3 Score Hit | False |
| DC Score Log Loss | 5.2299 |
| NegBin Score Log Loss | 4.3140 |
| Weibull Score Log Loss | 6.9592 |
| DC Marginal | 0.1048 |
| Enhancer Marginal | -0.0290 |
| Elo Marginal | 0.0002 |


## 🧭 Rich Match Data / Game State

| Metric | Value |
|:---|:---|
| Rich Data Tier | goal_timeline_complete |
| Event Quality Score | 0.9000 |
| Data Coverage | events=yes, goals=yes, lineups=yes, minutes=yes, shot map=no, shot xG=no, player stats=no |
| Data Warnings | no_full_shot_map, no_shot_xg, no_technical_player_statistics, player_stats_event_derived_only, shot_events_from_event_timeline_only |
| Raw / Events / Shots / Lineups / Player Stats | 1 / 21 / 5 / 50 / 5 |
| Missing Rich Data | full_shot_map, shot_xg, technical_player_statistics |
| Comeback Profile | late_comeback |
| Max Deficit For Winner | 2 |
| Late Comeback | True |

### Goal Timeline

| Minute | Team | Player | Score |
|:---:|:---|:---|:---:|
| 15 | Egypt | YASSER IBRAHIM | 0-1 |
| 67 | Egypt | MOSTAFA ZICO | 0-2 |
| 79 | Argentina | Cristian ROMERO | 1-2 |
| 83 | Argentina | Lionel MESSI | 2-2 |
| 90+2 | Argentina | Enzo FERNANDEZ | 3-2 |

### Game-State Segments

| Window | Score | Leader | Shots | xG |
|:---:|:---:|:---:|:---:|:---:|
| 0-15 | 0-0 → 0-1 | draw → away | 0-1 | N/A-N/A |
| 16-30 | 0-1 → 0-1 | away → away | 0-0 | N/A-N/A |
| 31-45 | 0-1 → 0-1 | away → away | 0-0 | N/A-N/A |
| 46-60 | 0-1 → 0-1 | away → away | 0-0 | N/A-N/A |
| 61-75 | 0-1 → 0-2 | away → away | 0-1 | N/A-N/A |
| 76-90 | 0-2 → 3-2 | away → home | 3-0 | N/A-N/A |
| 91-105 | 3-2 → 3-2 | home → home | 0-0 | N/A-N/A |
| 106-120 | 3-2 → 3-2 | home → home | 0-0 | N/A-N/A |

**Leakage Policy**: post-match-only; never joined into same-match pre-match strict features.


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| Argentina | market_move | neutral | neutral | 0.0000 |
| Argentina | weather | negative | misleading | -0.0255 |
| Argentina | market_move | neutral | neutral | 0.0000 |

**Signal Evaluations**: 3
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-09T01:28:43.888391+00:00 | Pipeline: run_postmatch_complete.py*
