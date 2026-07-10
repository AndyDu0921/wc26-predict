---
name: wc-postmatch-france-morocco-2026-07-10
description: "Post-match: France 2-0 Morocco"
metadata:
  type: project
---

# France vs Morocco: 2-0

- **Brier**: 0.3301
- **Direction**: correct
- **Prediction**: 53.2% / 21.1% / 25.7% (favored: home)
- **Stats completeness**: full
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 0.90 × 1.00 = 0.4500
- **xG**: predicted 0.758-0.941, actual 3.690-0.140
- **Score metrics**: log loss 2.3387, exact hit False, top-3 hit False
- **Rich postmatch data**: event_timeline_only / quality 0.6000 / comeback no_comeback
- **Rich data coverage**: events=yes, goals=yes, lineups=no, minutes=no, shot map=no, shot xG=no, player stats=no
- **Rich data warnings**: no_full_shot_map, no_player_statistics_found

## Component Review

- **DC**: A / wrong / Brier 0.7715
- **Enhancer**: A / wrong / Brier 1.0061
- **DC+Enhancer**: A / wrong / Brier 0.7936
- **NegBin**: A / wrong / Brier 0.7348
- **Weibull**: H / correct / Brier 0.0191
- **Elo**: H / correct / Brier 0.4664
- **Pi**: A / wrong / Brier 0.6255
- **Market**: H / correct / Brier 0.2343

## Information-State Signal Attribution

- **Signals evaluated**: 7
- **Policy**: proposal-only; no automatic weight change.
