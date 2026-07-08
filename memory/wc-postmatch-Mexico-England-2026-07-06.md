---
name: wc-postmatch-mexico-england-2026-07-06
description: "Post-match: Mexico 2-3 England"
metadata:
  type: project
---

# Mexico vs England: 2-3

- **Brier**: 0.6261
- **Direction**: wrong
- **Prediction**: 39.0% / 25.1% / 35.9% (favored: home)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 0.90 × 1.00 = 0.4500
- **xG**: predicted 0.591-0.775, actual 1.551-1.944
- **Score metrics**: log loss 5.1438, exact hit False, top-3 hit False
- **Rich postmatch data**: basic_only / quality 0.0000 / comeback unavailable

## Component Review

- **DC**: D / wrong / Brier 0.6102
- **Enhancer**: A / correct / Brier 0.1525
- **DC+Enhancer**: A / correct / Brier 0.5507
- **NegBin**: A / correct / Brier 0.5365
- **Weibull**: H / wrong / Brier 1.3266
- **Elo**: A / correct / Brier 0.4646
- **Pi**: H / wrong / Brier 0.6021
- **Market**: A / correct / Brier 0.5592

## Information-State Signal Attribution

- **Signals evaluated**: 0
- **Policy**: proposal-only; no automatic weight change.
