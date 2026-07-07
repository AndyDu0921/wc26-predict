---
name: wc-prediction-colombia-switzerland-2026-07-08
description: "Pre-match prediction: Colombia vs Switzerland R16, 2026-07-08 Beijing time"
metadata:
  type: project
---

# Colombia vs Switzerland — 赛前预测 (2026-07-08)

- **Prediction (90min)**: COL 34.2% / Draw 21.8% / SUI 44.1%
- **xG**: COL 1.28 / SUI 1.34
- **Favorite**: Switzerland (away) — but deeply contested
- **Elo gap**: +46 (COL 1733, SUI 1686)
- **Pi gap**: SUI 2.31 > COL 1.62 (SUI objectively better in Pi)
- **Stage**: Round of 16
- **Venue**: BC Place, Vancouver, BC (indoor, mild climate)
- **Snapshot**: match_id=201, model_version=4.10.0-alpha
- **Weight config**: WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA
- **Market**: NOT available (API no data, web consensus ~COL 43%/D 30%/SUI 27% not injected)

## Component Review

| Component | COL/D/SUI | Fav | Note |
|:---|---:|:---:|:---|
| DC | 35.8/25.5/38.7 | SUI | Nearly neutral, slight SUI edge |
| Enhancer | 9.9/18.7/71.4 | **SUI** | Systematic underdog bias (KO: 69% wrong) |
| Elo | 43.1/23.8/33.1 | COL | Elo gap 46 → ~53% win prob |
| Pi | 18.6/18.8/62.6 | **SUI** | Pi SUI 2.31 > COL 1.62, objective SUI edge |
| Weibull | 44.1/32.4/23.4 | COL | 32.4% draw is very high |
| Market | N/A | — | Web consensus ~43/30/27, not in fusion |

**Consensus**: 3/5 → SUI, 2/5 → COL. Deeply split.

## Key Risk Factors

- **No market data**: API unavailable. Web consensus (6 bookmakers) not injected into divergence calculation.
- **Component split**: Pi 62.6% SUI vs Elo 43.1% COL = direction conflict
- **KO Draw Underestimation**: Elo gap 46 (<50) but draw only 21.8%. Historical pattern matches GER-PAR + NED-MAR false negatives.
- **KO Post-Cal Draw Guard triggered**: 19.7% → 21.8% (blend 65%, risks: close Elo gap)
- **Córdoba OUT**: Colombia's primary striker out for tournament
- **Pi rating**: SUI 2.31 objectively superior to COL 1.62 — this Pi signal is more credible than typical
- **Enhancer 71.4% SUI**: Matches systematic underdog bias pattern but SUI may genuinely be the better side here
- **Manual estimate**: COL 36-40% / Draw 26-30% / SUI 30-34% — draw significantly undervalued by model

## Match Context

- Colombia: 3 consecutive clean sheets, only 1 goal conceded all tournament
- Switzerland: scored in every match (9 goals in 4 games)
- Johan Manzambi: breakout star, 3G+2A
- Luis Díaz: Colombia's primary attacking threat
- Both teams unbeaten in last 3 matches
- Winner faces Argentina or Egypt in QF
