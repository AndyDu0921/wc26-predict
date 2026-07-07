---
name: wc-postmatch-portugal-spain-2026-07-07
description: "Post-match: Portugal 0-1 Spain"
metadata:
  type: project
---

# Portugal vs Spain: 0-1

- **Brier**: 0.6193
- **Direction**: wrong
- **Prediction**: 41.3% / 21.9% / 36.7% (favored: home)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 0.70 × 1.00 = 0.3500
- **xG**: predicted 1.059-1.269, actual 0.630-1.690
- **Score metrics**: log loss 2.2306, exact hit False, top-3 hit True

## Component Review

- **DC**: A / correct / Brier 0.5115
- **Enhancer**: A / correct / Brier 0.1389
- **DC+Enhancer**: A / correct / Brier 0.4059
- **NegBin**: A / correct / Brier 0.4728
- **Weibull**: D / wrong / Brier 1.2279
- **Elo**: A / correct / Brier 0.4312
- **Pi**: H / wrong / Brier 0.7350
- **Market**: A / correct / Brier 0.3586

## Information-State Signal Attribution

- **Signals evaluated**: 4
- **Policy**: proposal-only; no automatic weight change.
