---
name: wc-postmatch-unitedstates-belgium-2026-07-07
description: "Post-match: United States 1-4 Belgium"
metadata:
  type: project
---

# United States vs Belgium: 1-4

- **Brier**: 0.6195
- **Direction**: wrong
- **Prediction**: 44.2% / 18.3% / 37.5% (favored: home)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 0.70 × 1.00 = 0.3500
- **xG**: predicted 1.742-2.108, actual 0.670-2.140
- **Score metrics**: log loss 3.6526, exact hit False, top-3 hit False

## Component Review

- **DC**: A / correct / Brier 0.4205
- **Enhancer**: A / correct / Brier 0.4146
- **DC+Enhancer**: A / correct / Brier 0.4175
- **NegBin**: A / correct / Brier 0.4052
- **Weibull**: A / correct / Brier 0.1004
- **Elo**: A / correct / Brier 0.5726
- **Pi**: H / wrong / Brier 0.7455
- **Market**: H / wrong / Brier 0.6128

## Information-State Signal Attribution

- **Signals evaluated**: 3
- **Policy**: proposal-only; no automatic weight change.
