---
name: wc-prediction-ARG-ENG-2026-07-16
description: "ARG 47.8/21.3/30.9 vs ENG, SF, 7/7 components, market integrated (8 bookmakers, 35.6% weight), model-market opposite direction, high divergence 0.78"
metadata:
  type: project
---

ARG 47.8/21.3/30.9 vs ENG, SF #206, Mercedes-Benz Stadium Atlanta

7/7 components running. Market integrated via web consensus (8 bookmakers: FanDuel, BetMGM, Bet365, Pinnacle, Betano, 1xBet, DraftKings, RotoWire). CV 2.6-3.1%.

Component breakdown (KO weights + effective):
- DC (50.7% eff): 35.9/30.9/33.2 — near-even
- NegBin (5%): 38.5/25.9/35.6 — slight ARG lean, applied
- Weibull (2.5% eff, SHADOW): 91.3/7.8/0.9 — extreme ARG, market_conflict scenario, 50% weight cut
- Elo (18.7% eff): 46.7/23.4/29.9 — ARG, kappa=0.48, gap=78 Elo
- Pi (22% eff): 61.6/18.9/19.5 — ARG strongly
- Enhancer (5.6% eff): 13.7/21.7/64.6 — ENG (cold bias, ~31% accuracy)
- Market (35.6% dyn): 32.2/31.5/36.3 — ENG slight favorite (OPPOSES model direction)

xG: ARG 0.98 vs ENG 0.93
Top scores: 1-0 (16.8%), 0-1 (11.2%), 1-1 (9.2%)
Score matrix: DC + NegBin dual-source (Weibull matrix rejected: max_cell=44.3%, sparse=40%)
Calibration: 69 WC samples, Isotonic, applied. Pre-cal: 43.3/27.9/32.8 → Post: 47.8/21.3/30.9

Market integration: divergence=11.1pp, market_weight=35.6%, progressive threshold (8 bookmakers + low CV = ~10pp threshold). Web consensus successfully crossed gate.

Risk tags: high_model_disagreement_0.78, market_model_opposite_direction
KO Draw Guard: not triggered (draw 21.3% >= 20% floor)
Quality: 0.67, 2 evidence, 1 signal

Key context:
- ARG: full squad, Messi 8 goals, same XI 3rd KO match, played AET vs SUI
- ENG: Henderson OUT (arm), Quansah SUSPENDED, Rice illness recovered, Bellingham+Kane=all 7 KO goals
- Market consensus (8 bookmakers): ENG 36.3% slight fav — opposite to model direction
- Weather: Mercedes-Benz retractable roof, climate-controlled

ROOT CAUSE DIAGNOSIS OF INITIAL FAILURE:
- Market data was missing because _web_odds_cache.json had no Argentina|England entry
- API returned 404 (apifootball.com doesn't have semifinal match)
- FIX: Used web_odds_aggregator.ingest() to write 8 bookmakers with correct format
- NegBin: actually works fine (earlier error was test print formatting, not function bug)
- Weibull: predict() returns None (no training data), pipeline handles with market_conflict shadow mode

Snapshot: predict_match_full.py --no-save, model 4.11.0-alpha, weight WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA
