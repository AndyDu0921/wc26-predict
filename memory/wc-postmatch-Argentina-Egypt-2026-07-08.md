---
name: wc-postmatch-argentina-egypt-2026-07-08
description: "Post-match: Argentina 3-2 Egypt"
metadata:
  type: project
---

# Argentina vs Egypt: 3-2

- **Brier**: 0.3184
- **Direction**: correct
- **Prediction**: 54.0% / 21.4% / 24.7% (favored: home)
- **Stats completeness**: partial
- **Failure type**: MODEL_INPUT_ERROR
- **Learning weight**: 0.50 × 1.00 × 1.00 = 0.5000
- **xG**: predicted 0.935-0.603, actual 2.840-0.980
- **Score metrics**: log loss 4.5056, exact hit False, top-3 hit False
- **Rich postmatch data**: goal_timeline_complete / quality 0.9000 / comeback late_comeback
- **Rich data coverage**: events=yes, goals=yes, lineups=yes, minutes=yes, shot map=no, shot xG=no, player stats=no
- **Rich data warnings**: no_full_shot_map, no_shot_xg, no_technical_player_statistics, player_stats_event_derived_only, shot_events_from_event_timeline_only

## Component Review

- **DC**: H / correct / Brier 0.5062
- **Enhancer**: A / wrong / Brier 1.1877
- **DC+Enhancer**: H / correct / Brier 0.5500
- **NegBin**: H / correct / Brier 0.4368
- **Weibull**: H / correct / Brier 0.3503
- **Elo**: H / correct / Brier 0.3476
- **Pi**: H / correct / Brier 0.1949
- **Market**: H / correct / Brier 0.1617

## Information-State Signal Attribution

- **Signals evaluated**: 3
- **Policy**: proposal-only; no automatic weight change.
