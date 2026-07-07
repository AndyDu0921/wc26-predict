# Portugal vs Spain 赛前预测报告

生成时间：2026-07-06T06:45:27.364254+00:00（DB赛前快照时间）
版本：4.9.0-alpha / 模式：full
比赛：FIFA World Cup 2026，北京时间 2026-07-07T03:00:00，场地：AT&T Stadium, Arlington, TX

## 系统最终预测（90 分钟）

| 主胜 | 平局 | 客胜 | 置信度 |
|---:|---:|---:|---|
| 41.3% | 21.9% | 36.7% | medium |

预期进球：Portugal 1.06 - 1.27 Spain

## Top 比分

| 排名 | 比分 | 概率 |
|---:|---:|---:|
| 1 | 1:0 | 14.2% |
| 2 | 0:0 | 11.2% |
| 3 | 0:1 | 10.8% |

## 组件拆解

| 组件 | 主胜 | 平 | 客胜 |
|---|---:|---:|---:|
| Dixon-Coles | 31.2% | 27.2% | 41.7% |
| Tabular Enhancer | 11.0% | 19.0% | 69.9% |
| NegBin | 33.2% | 22.6% | 44.2% |
| Elo-Davidson | 30.0% | 23.4% | 46.5% |
| Pi rating | 47.8% | 20.4% | 31.8% |
| Weibull | 18.5% | 67.5% | 14.1% |

有效权重：DC 0.506844，Enhancer 0.056316，Weibull 0.02964，Elo 0.1872，Pi 0.22。

说明：NegBin 已输出组件概率，并参与比分矩阵/审计；本场 KO 生产 1X2 有效权重行不列 NegBin，表示它不作为独立 1X2 outcome 权重直接进入最终概率。

比分矩阵审计：本报告保留赛前生成时的 Top 比分，不回写概率。V4.9 修复后，Weibull score matrix 若出现稀疏或单格概率异常，会作为 shadow 证据保留，不参与 fused score matrix。

## 市场赔率基准

主胜 23.1% / 平 25.8% / 客胜 51.1%，样本 3，provider=web-search-consensus。

未进入最终融合：当前生产逻辑只有模型-市场分歧跨过动态阈值时才 blend；本场 market_blended=0。

## 实时新闻/情报入模记录

- Portugal：利好 / other / confidence=0.55。Sport Grill: Portugal report no fresh injury concerns after the Croatia match. 来源：Sport Grill，有效至 UTC 2026-07-06 23:30:00。
- Spain：利空 / injury / confidence=0.62，球员：Nico Williams。Sport Grill: Spain could be without Nico Williams and Yeremy Pino after both missed the Round of 32. 来源：Sport Grill，有效至 UTC 2026-07-06 23:30:00。

## 天气

小毛雨，26.4C，降水 0.4mm，风速 12.7km/h，湿度 73.0%。

## 风险与审计

- 主队有利情报
- 客队不利情报
- high_model_disagreement_0.37

- 快照未记录降级原因；CLI postflight 仍提示 market_applied=False 属于审计风险。

## 结论

- 主输出采用系统最终概率，不把 market-only 当作最终预测。
- 本场市场已作为审计基准记录；是否改成“市场永远参与融合”需要单独做 walk-forward 回测，不能临场手改生产权重。
- 本报告不是投注建议，只用于模型预测、复盘和自进化闭环。

## 来源

- [FIFA Match Centre](https://www.fifa.com/en/match-centre/match/17/285023/289288/400021529)
- [FIFA Preview](https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/articles/spain-portugal-preview-live-stream-team-news-tickets)
- [Sport Grill Team News](https://sportgrill.co.uk/2026/07/05/2026-fifa-world-cup-round-of-16-portugal-vs-spain/)
- [CBS Sports / FanDuel Odds](https://www.cbssports.com/soccer/news/portugal-vs-spain-odds-2026-world-cup-picks-predictions-best-bets-by-expert-whos-33-20-on-plays/)
- [DKNetwork / DraftKings Odds](https://dknetwork.draftkings.com/2026/07/06/spain-vs-portugal-prediction-odds-picks-for-world-cup-round-of-16/)
- [Winible Live Odds](https://www.winible.com/soccer/portugal-vs-spain-predictions-picks-odds-july-6-2026/)
