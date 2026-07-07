# 🏆 Post-Match Review: United States vs Belgium

**Date**: 2026-07-07
**Score**: 1 - 4
**Verified**: ✅ (2 sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 44.2% | 18.3% | 37.5% |
| Actual | — | — | 100% |

- **Favorite**: home
- **Direction**: ❌ WRONG
- **Brier Score**: 0.6195

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  32.0% /  20.6% /  47.5% | A | ✅ | 0.4205 |
| Enhancer     |  20.4% /  31.8% /  47.8% | A | ✅ | 0.4146 |
| DC+Enhancer  |  30.8% /  21.7% /  47.5% | A | ✅ | 0.4175 |
| NegBin       |  34.7% /  16.2% /  49.1% | A | ✅ | 0.4052 |
| Weibull      |  10.3% /  15.4% /  74.3% | A | ✅ | 0.1004 |
| Elo          |  37.3% /  24.0% /  38.7% | A | ✅ | 0.5726 |
| Pi           |  48.2% /  20.4% /  31.4% | H | ❌ | 0.7455 |
| Market       |  36.3% /  27.4% /  36.3% | H | ❌ | 0.6128 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 1.742 | 0.670 |
| Away xG | 2.108 | 2.140 |

**Stats Completeness**: Full
**Learning Data Quality**: 0.7

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.6195 |
| Direction | overestimate_home |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.3500 |
| Learning Formula | 0.50 × 0.70 × 1.00 |
| Score Log Loss | 3.6526 |
| Exact Score Hit | False |
| Top-3 Score Hit | False |
| DC Score Log Loss | 3.3796 |
| NegBin Score Log Loss | 3.2936 |
| Weibull Score Log Loss | 27.6310 |
| DC Marginal | -0.1373 |
| Enhancer Marginal | -0.1229 |
| Elo Marginal | -0.1582 |


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| United States | market_move | neutral | neutral | 0.0000 |
| United States | market_move | neutral | neutral | 0.0000 |
| United States | weather | negative | accurate | 0.0255 |

**Signal Evaluations**: 3
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-07T06:45:20.484405+00:00 | Pipeline: run_postmatch_complete.py*
