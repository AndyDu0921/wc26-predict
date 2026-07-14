# France vs Morocco 赛前预测报告

生成时间：2026-07-09T04:04:16.938449+00:00
系统版本：4.11.0-alpha / 模式：full / 比赛编号：201 (QF97)
比赛：FIFA World Cup 2026 Quarterfinal，北京时间 2026-07-10 04:00，场地：Boston Stadium, Foxborough, MA
快照时间：2026-07-09T03:58:15.799999+00:00；kickoff_at：2026-07-10T04:00:00+08:00

## 结论

- 预测倾向：**France**
- 胜平负概率：France 53.2% / 平局 21.1% / Morocco 25.7%
- 预期进球：France 0.758 / Morocco 0.941
- 置信度：medium；模型分歧：0.714

## Top 比分矩阵

| Rank | Score | Probability |
|---:|:---:|---:|
| 1 | 1:0 | 23.1% |
| 2 | 0:0 | 10.3% |
| 3 | 0:1 | 10.0% |

## 组件概率

| Component | France | Draw | Morocco | Component Pick |
|---|---:|---:|---:|---|
| Dixon-Coles | 28.4% | 33.0% | 38.6% | away |
| Tabular Enhancer | 18.2% | 36.7% | 45.0% | away |
| Negative Binomial | 30.5% | 27.8% | 41.7% | away |
| Weibull | 89.7% | 9.1% | 1.2% | home |
| Elo-Davidson | 44.4% | 23.7% | 31.9% | home |
| Pi rating | 36.7% | 20.6% | 42.7% | away |

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

- 市场赔率/共识：home=60.9%, draw=24.3%, away=14.8%, providers=25
- 天气快照：temperature_c=23.2, precipitation_mm=0.0, wind_speed_kmh=12.5, humidity_percent=89.0, weather_description=多云
- odds_available=1；weather_available=1；injury_data_available=1；news_signals_available=0

## 结构化信号（shadow-only）

- France `injury` negative magnitude=0.098, confidence=0.82: France: injury/negative - FIFA World Cup 2026 quarterfinal fixtures and previews Quarterfinal preview source listing France vs Morocco as the first quarterfinal at Boston Stadium/F
- France `lineup` neutral magnitude=0.042, confidence=0.70: France: lineup/neutral - France vs Morocco preview: team news and predicted lineups
- Morocco `lineup` neutral magnitude=0.042, confidence=0.70: Morocco: lineup/neutral - France vs Morocco preview: team news and predicted lineups
- France `market_move` neutral magnitude=0.085, confidence=0.85: France: market_move/neutral - market_odds snapshot for France vs Morocco {"home_prob": 0.6086142322097379, "draw_prob": 0.24344569288389514, "away_prob": 0.1479400749063671
- France `market_move` neutral magnitude=0.078, confidence=0.78: France: market_move/neutral - 2026 World Cup quarterfinal odds: France vs Morocco Market odds evidence as-of collection: France listed as favorite around -160 moneyline, Morocco ar
- Morocco `return` positive magnitude=0.056, confidence=0.70: Morocco: return/positive - Morocco have fitness questions including Amine Harit
- France `weather` negative magnitude=0.025, confidence=0.85: France: weather/negative - weather snapshot for France vs Morocco {"temperature_c": 23.2, "precipitation_mm": 0.0, "wind_speed_kmh": 12.5, "humidity_percent": 89.0, "weathe

## 可追溯来源

- injury: [France vs Morocco preview: team news and predicted lineups](https://www.si.com/soccer/france-vs-morocco-preview-predictions-lineups-7-9-26) — Sports Illustrated, available_at `2026-07-09T03:56:12.840336+00:00`
- market_odds: [2026 World Cup quarterfinal odds: France vs Morocco](https://www.foxsports.com/stories/soccer/2026-world-cup-quarterfinal-odds-which-squads-will-make-final-8) — FOX Sports, available_at `2026-07-09T03:56:12.840336+00:00`
- market_odds: [market_odds snapshot for France vs Morocco](internal://pre_match_snapshots/b009cfce-ef6b-43f2-874a-e70938341fdc/odds_snapshot) — pre_match_snapshots, available_at `2026-07-09T03:58:15.799999+00:00`
- schedule_context: [FIFA World Cup 2026 quarterfinal fixtures and previews](https://www.aljazeera.com/sports/2026/7/8/fifa-world-cup-2026-quarterfinal-fixtures-match-previews-schedule) — Al Jazeera, available_at `2026-07-09T03:56:12.840336+00:00`
- weather: [weather snapshot for France vs Morocco](internal://pre_match_snapshots/b009cfce-ef6b-43f2-874a-e70938341fdc/weather_snapshot) — pre_match_snapshots, available_at `2026-07-09T03:58:15.799999+00:00`

## 重要说明

- 市场赔率是核心外部信号和校验基准；本报告不包含投注建议。
- 新闻/伤停/天气信号进入 information-state ledger 和 shadow scoring，不直接改生产权重。
- 赛后复盘必须使用本场 match_id=201 的 prediction_run / prediction_snapshot / feature_snapshot，不能回填赛后信息到赛前 strict snapshot。
- Risk tags: 模型与市场存在显著分歧 (16.0pp), high_model_disagreement_0.71
