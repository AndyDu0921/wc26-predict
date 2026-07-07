---
name: wc-prediction-argentina-egypt-2026-07-08
description: "Pre-match prediction: Argentina vs Egypt R16, 2026-07-08 Beijing time"
metadata:
  type: project
---

# Argentina vs Egypt — 赛前预测 (2026-07-08)

- **Prediction (90min)**: ARG 54.0% / Draw 21.4% / EGY 24.7%
- **xG**: ARG 0.935 / EGY 0.603
- **Favorite**: Argentina (home)
- **Elo gap**: +123 (ARG 1820, EGY 1697)
- **Stage**: Round of 16
- **Venue**: Mercedes-Benz Stadium, Atlanta, GA (indoor, climate-controlled)
- **Snapshot**: match_id=194, model_version=4.10.0-alpha
- **Weight config**: WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA

## Component Review

| Component | H/D/A | Fav | Note |
|:---|---:|:---:|:---|
| DC | 42.3/34.7/23.0 | ARG | DC sees ~35% draw, highest among components |
| Enhancer | 14.8/20.4/64.8 | **EGY** | Systematic underdog bias (KO: 69% wrong) |
| NegBin | 46.1/29.1/24.8 | ARG | r=8.0, xG calibration 1.20 |
| Weibull | 56.2/39.6/4.3 | ARG | 4.3% EGY extremely low, 39.6% draw very high |
| Elo | 51.9/22.6/25.5 | ARG | Elo gap 123pts → ~55-60% win prob |
| Pi | 64.0/18.5/17.5 | ARG | Strong ARG signal |
| Market (10Bet) | 67.8/21.5/10.7 | ARG | Only 1 bookmaker via API |

**Consensus**: 6/7 components → Argentina. Only Enhancer → Egypt.

## Key Risk Factors

- **Model-market divergence**: 19.9pp (model 47.9% vs market 67.8%)
- **Component disagreement**: 0.49 (Pi 64.0% vs Enhancer 14.8% = 49.2pp range)
- **Single bookmaker**: Only 10Bet via API. Web consensus (7 bookmakers) ~71% ARG not injected.
- **Market weight used**: 37.9% (divergence boost triggered at 19.9pp > 15pp)
- **KO Draw Guard**: NOT triggered (draw 21.4% >= 20% floor)
- **Weibull score matrix**: shadowed (max_cell=35.8%)
- **Calibration**: 55.4% → 54.0% (69 WC training samples, ECE=0.052)

## Match Context

- Both teams played 120 minutes in R32 (4 days rest)
- Egypt defensive crisis: Fatouh (out), Abdelmonem (major doubt), Hafez (doubt)
- Salah managing hamstring concern, not 100%
- Messi: 7 goals in 4 matches, Golden Boot leader
- Argentina 8-game winning streak vs African nations
- Mercedes-Benz Stadium: retractable roof, indoor conditions → neutral weather

## Information State

- Quality score: 0.17 (low — missing news, injury DB, structured signals)
- Market: partial (1 API bookmaker, web consensus not merged)
- Weather: indoor, neutral
- Injuries: manual from web search, not in DB
- Confidence modifier: 0.875
