---
name: wc-postmatch-colombia-switzerland-2026-07-08
description: "Post-match: Colombia 0-0 Switzerland"
metadata:
  type: project
---

# Colombia vs Switzerland: 0-0

- **Shootout**: Switzerland advanced 4-3 on penalties; 1X2 evaluation remains draw.
- **Brier**: 0.9229
- **Direction**: wrong
- **Prediction**: 34.2% / 21.8% / 44.1% (favored: away)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 1.00 × 1.00 = 0.5000
- **xG**: predicted 1.280-1.342, actual 1.090-0.390
- **Score metrics**: log loss 3.0904, exact hit False, top-3 hit False

## Component Review

- **DC**: A / wrong / Brier 0.8332
- **Enhancer**: A / wrong / Brier 1.1800
- **DC+Enhancer**: A / wrong / Brier 0.8865
- **NegBin**: A / wrong / Brier 0.9351
- **Weibull**: H / wrong / Brier 0.7060
- **Elo**: H / wrong / Brier 0.8763
- **Pi**: A / wrong / Brier 1.0865

## Information-State Signal Attribution

- **Signals evaluated**: 2
- **Policy**: proposal-only; no automatic weight change.
