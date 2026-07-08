---
name: wc-postmatch-brazil-norway-2026-07-05
description: "Post-match: Brazil 1-2 Norway"
metadata:
  type: project
---

# Brazil vs Norway: 1-2

- **Brier**: 0.7643
- **Direction**: wrong
- **Prediction**: 48.8% / 20.7% / 30.5% (favored: home)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 0.90 × 1.00 = 0.4500
- **xG**: predicted 1.294-1.400, actual 1.053-2.614
- **Score metrics**: log loss 2.7424, exact hit False, top-3 hit False
- **Rich postmatch data**: basic_only / quality 0.0000 / comeback unavailable

## Component Review

- **DC**: A / correct / Brier 0.5454
- **Enhancer**: A / correct / Brier 0.2850
- **DC+Enhancer**: A / correct / Brier 0.5155
- **NegBin**: A / correct / Brier 0.5140
- **Weibull**: D / wrong / Brier 0.7453
- **Elo**: H / wrong / Brier 0.8436
- **Pi**: H / wrong / Brier 0.8316
- **Market**: H / wrong / Brier 0.9748

## Information-State Signal Attribution

- **Signals evaluated**: 0
- **Policy**: proposal-only; no automatic weight change.
