# Spain vs Belgium 赛前预测报告

生成时间：2026-07-09T04:04:16.938449+00:00
系统版本：4.11.0-alpha / 模式：full / 比赛编号：202 (QF98)
比赛：FIFA World Cup 2026 Quarterfinal，北京时间 2026-07-11 03:00，场地：Los Angeles Stadium, Inglewood, CA
快照时间：2026-07-09T03:59:45.374207+00:00；kickoff_at：2026-07-11T03:00:00+08:00

## 结论

- 预测倾向：**Spain**
- 胜平负概率：Spain 52.4% / 平局 20.6% / Belgium 27.0%
- 预期进球：Spain 1.363 / Belgium 1.061
- 置信度：medium；模型分歧：0.369

## Top 比分矩阵

| Rank | Score | Probability |
|---:|:---:|---:|
| 1 | 1:0 | 12.2% |
| 2 | 2:1 | 9.8% |
| 3 | 1:1 | 9.3% |

## 组件概率

| Component | Spain | Draw | Belgium | Component Pick |
|---|---:|---:|---:|---|
| Dixon-Coles | 44.3% | 26.3% | 29.4% | home |
| Tabular Enhancer | 13.7% | 21.2% | 65.0% | away |
| Negative Binomial | 46.8% | 21.9% | 31.3% | home |
| Weibull | 24.3% | 52.2% | 23.5% | draw |
| Elo-Davidson | 49.8% | 23.0% | 27.3% | home |
| Pi rating | 50.6% | 20.2% | 29.1% | home |

## 有效权重

```json
{
  "dc_effective": 0.506844,
  "enhancer_effective": 0.056316,
  "weibull_effective": 0.02964,
  "elo_effective": 0.1872,
  "pi_effective": 0.22
}
```

## 市场、天气、情报

- 市场赔率/共识：home=58.8%, draw=24.0%, away=17.2%, providers=24
- 天气快照：temperature_c=18.6, precipitation_mm=0.0, wind_speed_kmh=4.9, humidity_percent=85.0, weather_description=晴
- odds_available=1；weather_available=1；injury_data_available=1；news_signals_available=0

## 结构化信号（shadow-only）

- Belgium `injury` negative magnitude=0.082, confidence=0.68: Belgium: injury/negative - Team-news evidence: Spain and Belgium quarterfinal preview notes both sides' expected lineup and suspension/injury context
- Spain `injury` negative magnitude=0.078, confidence=0.65: Spain: injury/negative - Spain could be without Nico Williams and Yeremy Pino after both missed the Austria match
- Spain `injury` negative magnitude=0.082, confidence=0.68: Spain: injury/negative - Team-news evidence: Spain and Belgium quarterfinal preview notes both sides' expected lineup and suspension/injury context
- Belgium `lineup` neutral magnitude=0.041, confidence=0.68: Belgium: lineup/neutral - Spain vs Belgium lineups, team news and suspensions
- Spain `lineup` neutral magnitude=0.041, confidence=0.68: Spain: lineup/neutral - Spain vs Belgium lineups, team news and suspensions
- Spain `market_move` neutral magnitude=0.078, confidence=0.78: Spain: market_move/neutral - 2026 World Cup quarterfinal odds: Spain vs Belgium Market odds evidence as-of collection: Spain listed as favorite around -135 moneyline, Belgium aroun
- Spain `market_move` neutral magnitude=0.085, confidence=0.85: Spain: market_move/neutral - market_odds snapshot for Spain vs Belgium {"home_prob": 0.5879882402351954, "draw_prob": 0.2403401931961361, "away_prob": 0.17167156656866864, "vig": 0
- Spain `weather` negative magnitude=0.025, confidence=0.85: Spain: weather/negative - weather snapshot for Spain vs Belgium {"temperature_c": 18.6, "precipitation_mm": 0.0, "wind_speed_kmh": 4.9, "humidity_percent": 85.0, "weather_code": 0,

## 可追溯来源

- market_odds: [2026 World Cup quarterfinal odds: Spain vs Belgium](https://www.foxsports.com/stories/soccer/2026-world-cup-quarterfinal-odds-which-squads-will-make-final-8) — FOX Sports, available_at `2026-07-09T03:56:12.840336+00:00`
- market_odds: [market_odds snapshot for Spain vs Belgium](internal://pre_match_snapshots/659ed40f-62c4-48dd-9998-c1f1a1173ee0/odds_snapshot) — pre_match_snapshots, available_at `2026-07-09T03:59:45.374207+00:00`
- news: [Spain vs Belgium lineups, team news and suspensions](https://www.goal.com/en-us/lists/spain-vs-belgium-lineups-team-news-suspensions-world-cup-quarter-final/blt2e5d003a1b40c3c3) — GOAL, available_at `2026-07-09T03:56:12.840336+00:00`
- news: [2026 World Cup Odds: Balogun Back; USA Favored Over Belgium In Round Of 16](https://www.foxsports.com/stories/soccer/2026-world-cup-odds-how-far-will-team-usa-go) — FOX Sports, available_at `2026-07-05T14:33:00+00:00`
- news: [2026 FIFA World Cup: Round of 16 - Portugal vs Spain](https://sportgrill.co.uk/2026/07/05/2026-fifa-world-cup-round-of-16-portugal-vs-spain/) — Sport Grill, available_at `2026-07-05T00:00:00+00:00`
- news: [2026 FIFA World Cup: Round of 16 - USA vs Belgium](https://sportgrill.co.uk/2026/07/05/2026-fifa-world-cup-round-of-16-usa-vs-belgium/) — Sport Grill, available_at `2026-07-05T00:00:00+00:00`
- news: [Spain defender Batlle close to agreeing Arsenal move](https://www.bbc.com/sport/football/articles/c937e402z5xo?at_medium=RSS&at_campaign=rss) — BBC Sport, available_at `2026-04-22T09:24:13+00:00`
- schedule_context: [Quarterfinal match previews: Spain vs Belgium](https://www.houstonchronicle.com/world-cup/article/quarterfinal-matches-preview-round-of-8-22337109.php) — Houston Chronicle, available_at `2026-07-09T03:56:12.840336+00:00`
- weather: [weather snapshot for Spain vs Belgium](internal://pre_match_snapshots/659ed40f-62c4-48dd-9998-c1f1a1173ee0/weather_snapshot) — pre_match_snapshots, available_at `2026-07-09T03:59:45.374207+00:00`

## 重要说明

- 市场赔率是核心外部信号和校验基准；本报告不包含投注建议。
- 新闻/伤停/天气信号进入 information-state ledger 和 shadow scoring，不直接改生产权重。
- 赛后复盘必须使用本场 match_id=202 的 prediction_run / prediction_snapshot / feature_snapshot，不能回填赛后信息到赛前 strict snapshot。
- Risk tags: 模型与市场存在显著分歧 (14.0pp), high_model_disagreement_0.37
