# 🏆 Post-Match Review: Portugal vs Spain

**Date**: 2026-07-07
**Score**: 0 - 1
**Verified**: ✅ (2 sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 41.3% | 21.9% | 36.7% |
| Actual | — | — | 100% |

- **Favorite**: home
- **Direction**: ❌ WRONG
- **Brier Score**: 0.6193

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  31.2% /  27.2% /  41.7% | A | ✅ | 0.5115 |
| Enhancer     |  11.0% /  19.0% /  69.9% | A | ✅ | 0.1389 |
| DC+Enhancer  |  26.7% /  25.3% /  48.0% | A | ✅ | 0.4059 |
| NegBin       |  33.2% /  22.6% /  44.2% | A | ✅ | 0.4728 |
| Weibull      |  18.5% /  67.5% /  14.1% | D | ❌ | 1.2279 |
| Elo          |  30.0% /  23.4% /  46.5% | A | ✅ | 0.4312 |
| Pi           |  47.8% /  20.4% /  31.8% | H | ❌ | 0.7350 |
| Market       |  23.1% /  25.8% /  51.1% | A | ✅ | 0.3586 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 1.059 | 0.630 |
| Away xG | 1.269 | 1.690 |

**Stats Completeness**: Full
**Learning Data Quality**: 0.7

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.6193 |
| Direction | overestimate_home |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.3500 |
| Learning Formula | 0.50 × 0.70 × 1.00 |
| Score Log Loss | 2.2306 |
| Exact Score Hit | False |
| Top-3 Score Hit | True |
| DC Score Log Loss | 2.0620 |
| NegBin Score Log Loss | 2.4566 |
| Weibull Score Log Loss | 2.0962 |
| DC Marginal | -0.3124 |
| Enhancer Marginal | -0.0696 |
| Elo Marginal | -0.0860 |


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| Spain | injury | negative | misleading | -0.0780 |
| Portugal | market_move | neutral | neutral | 0.0000 |
| Portugal | market_move | neutral | neutral | 0.0000 |
| Portugal | weather | negative | accurate | 0.0255 |

**Signal Evaluations**: 4
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-07T06:45:10.644874+00:00 | Pipeline: run_postmatch_complete.py*
