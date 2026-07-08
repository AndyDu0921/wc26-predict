# 🏆 Post-Match Review: Colombia vs Switzerland

**Date**: 2026-07-08
**Score**: 0 - 0
**Verified**: ✅ (2 sources)
**Shootout**: Switzerland advanced 4-3 on penalties. Penalties are not counted in the 1X2 model evaluation.

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | 34.2% | 21.8% | 44.1% |
| Actual | — | 100% | — |

- **Favorite**: away
- **Direction**: ❌ WRONG
- **Brier Score**: 0.9229

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
| DC           |  35.8% /  25.5% /  38.7% | A | ❌ | 0.8332 |
| Enhancer     |   9.9% /  18.7% /  71.4% | A | ❌ | 1.1800 |
| DC+Enhancer  |  29.3% /  23.8% /  46.9% | A | ❌ | 0.8865 |
| NegBin       |  37.9% /  21.1% /  41.0% | A | ❌ | 0.9351 |
| Weibull      |  44.1% /  32.4% /  23.4% | H | ❌ | 0.7060 |
| Elo          |  43.1% /  23.8% /  33.1% | H | ❌ | 0.8763 |
| Pi           |  18.6% /  18.8% /  62.6% | A | ❌ | 1.0865 |

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | 1.280 | 1.090 |
| Away xG | 1.342 | 0.390 |

**Stats Completeness**: Full
**Learning Data Quality**: 1.0

## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | 0.9229 |
| Direction | overestimate_away |
| Status | active |
| Failure Type | MODEL_INPUT_ERROR |
| Learning Weight | 0.5000 |
| Learning Formula | 0.50 × 1.00 × 1.00 |
| Score Log Loss | 3.0904 |
| Exact Score Hit | False |
| Top-3 Score Hit | False |
| DC Score Log Loss | 2.6946 |
| NegBin Score Log Loss | 3.2572 |
| Weibull Score Log Loss | 27.6310 |
| DC Marginal | 0.1141 |
| Enhancer Marginal | -0.0475 |
| Elo Marginal | -0.0368 |


## 🧠 Information-State Signal Attribution

| Team | Signal | Direction | Verdict | Contribution |
|:---|:---|:---:|:---:|---:|
| Colombia | weather | negative | accurate | 0.0255 |
| Colombia | market_move | neutral | neutral | 0.0000 |

**Signal Evaluations**: 2
**Policy**: proposal-only; no automatic production weight change.

---

*Generated: 2026-07-08T03:02:14.252430+00:00 | Pipeline: run_postmatch_complete.py*
