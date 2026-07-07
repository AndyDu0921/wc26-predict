# United States vs Belgium 赛前预测报告

生成时间：2026-07-06T06:46:57.111160+00:00（DB赛前快照时间）
版本：4.9.0-alpha / 模式：full
比赛：FIFA World Cup 2026，北京时间 2026-07-07T08:00:00，场地：Lumen Field, Seattle, WA

## 系统最终预测（90 分钟）

| 主胜 | 平局 | 客胜 | 置信度 |
|---:|---:|---:|---|
| 44.2% | 18.3% | 37.5% | medium |

预期进球：United States 1.74 - 2.11 Belgium

## Top 比分

| 排名 | 比分 | 概率 |
|---:|---:|---:|
| 1 | 3:1 | 9.1% |
| 2 | 1:1 | 8.2% |
| 3 | 1:3 | 7.2% |

## 组件拆解

| 组件 | 主胜 | 平 | 客胜 |
|---|---:|---:|---:|
| Dixon-Coles | 32.0% | 20.6% | 47.5% |
| Tabular Enhancer | 20.4% | 31.8% | 47.8% |
| NegBin | 34.7% | 16.2% | 49.1% |
| Elo-Davidson | 37.3% | 24.0% | 38.7% |
| Pi rating | 48.2% | 20.4% | 31.4% |
| Weibull | 10.3% | 15.4% | 74.3% |

有效权重：DC 0.506844，Enhancer 0.056316，Weibull 0.02964，Elo 0.1872，Pi 0.22。

说明：NegBin 已输出组件概率，并参与比分矩阵/审计；本场 KO 生产 1X2 有效权重行不列 NegBin，表示它不作为独立 1X2 outcome 权重直接进入最终概率。

比分矩阵审计：本报告保留赛前生成时的 Top 比分，不回写概率。V4.9 修复后，Weibull score matrix 若出现稀疏或单格概率异常，会作为 shadow 证据保留，不参与 fused score matrix；本场原始 Weibull score matrix 已被识别为这类异常分布。

## 市场赔率基准

主胜 36.3% / 平 27.4% / 客胜 36.3%，样本 3，provider=web-search-consensus。

未进入最终融合：当前生产逻辑只有模型-市场分歧跨过动态阈值时才 blend；本场 market_blended=0。

## 实时新闻/情报入模记录

- United States：利好 / return / confidence=0.90，球员：Folarin Balogun。FOX: FIFA lifted/suspended Balogun red-card suspension; he is eligible vs Belgium. 来源：FOX Sports，有效至 UTC 2026-07-07 03:30:00。

## 天气

晴，17.1C，降水 0.0mm，风速 1.9km/h，湿度 64.0%。

## 风险与审计

- 主队有利情报
- host_country_home
- home_like_venue
- effective_home_advantage
- KO draw underestimation risk
- high_model_disagreement_0.38

- 快照未记录降级原因；CLI postflight 仍提示 market_applied=False 属于审计风险。

## 结论

- 主输出采用系统最终概率，不把 market-only 当作最终预测。
- 本场市场已作为审计基准记录；是否改成“市场永远参与融合”需要单独做 walk-forward 回测，不能临场手改生产权重。
- 本报告不是投注建议，只用于模型预测、复盘和自进化闭环。

## 来源

- [U.S. Soccer Match Hub](https://www.ussoccer.com/competitions/fifa-world-cup-26/matches/united-states-vs-belgium-tickets-live-score-match-hub-lineups-highlights)
- [U.S. Soccer Opponent Profile](https://www.ussoccer.com/stories/2026/07/usmnt/opponent-profile-belgium-seattle-washington-world-cup-match-preview)
- [FOX Sports Balogun / Odds](https://www.foxsports.com/stories/soccer/2026-world-cup-odds-how-far-will-team-usa-go)
- [Sports Illustrated / DraftKings Odds](https://www.si.com/soccer/usa-belgium-prediction-odds-and-best-bet-for-world-cup-round-of-16-01jzgds6zk8c)
- [New York Post Odds](https://nypost.com/2026/07/06/betting/usa-vs-belgium-prediction-odds-picks-world-cup-2026/)
- [Covers / Kalshi Market Snapshot](https://www.covers.com/sport/soccer/world-cup/matchup/348112)
