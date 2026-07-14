# Argentina vs Switzerland 赛前预测报告

生成时间：2026-07-09T04:04:16.938449+00:00
系统版本：4.11.0-alpha / 模式：full / 比赛编号：204 (QF100)
比赛：FIFA World Cup 2026 Quarterfinal，北京时间 2026-07-12 09:00，场地：Arrowhead Stadium, Kansas City, MO
快照时间：2026-07-09T04:00:26.863966+00:00；kickoff_at：2026-07-12T09:00:00+08:00

## 结论

- 预测倾向：**Argentina**
- 胜平负概率：Argentina 52.0% / 平局 20.7% / Switzerland 27.3%
- 预期进球：Argentina 1.330 / Switzerland 0.831
- 置信度：medium；模型分歧：0.734

## Top 比分矩阵

| Rank | Score | Probability |
|---:|:---:|---:|
| 1 | 1:0 | 13.9% |
| 2 | 2:0 | 9.8% |
| 3 | 0:1 | 9.7% |

## 组件概率

| Component | Argentina | Draw | Switzerland | Component Pick |
|---|---:|---:|---:|---|
| Dixon-Coles | 49.0% | 27.4% | 23.6% | home |
| Tabular Enhancer | 23.9% | 23.5% | 52.5% | away |
| Negative Binomial | 52.1% | 22.8% | 25.1% | home |
| Weibull | 97.3% | 2.5% | 0.2% | home |
| Elo-Davidson | 53.1% | 22.3% | 24.6% | home |
| Pi rating | 25.0% | 19.8% | 55.1% | away |

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

- 市场赔率/共识：home=55.9%, draw=26.7%, away=17.4%, providers=25
- 天气快照：temperature_c=25.5, precipitation_mm=0.0, wind_speed_kmh=7.2, humidity_percent=79.0, weather_description=晴
- odds_available=1；weather_available=1；injury_data_available=1；news_signals_available=0

## 结构化信号（shadow-only）

- Switzerland `injury` negative magnitude=0.091, confidence=0.76: Switzerland: injury/negative - Switzerland continued its knockout run by beating Colombia
- Argentina `market_move` neutral magnitude=0.078, confidence=0.78: Argentina: market_move/neutral - 2026 World Cup quarterfinal odds: Argentina vs Switzerland Market odds evidence as-of collection: Argentina listed as favorite around -165 moneylin
- Argentina `market_move` neutral magnitude=0.085, confidence=0.85: Argentina: market_move/neutral - market_odds snapshot for Argentina vs Switzerland {"home_prob": 0.559108152927012, "draw_prob": 0.2670600395265896, "away_prob": 0.1738318075463983
- Argentina `weather` negative magnitude=0.025, confidence=0.85: Argentina: weather/negative - weather snapshot for Argentina vs Switzerland {"temperature_c": 25.5, "precipitation_mm": 0.0, "wind_speed_kmh": 7.2, "humidity_percent": 79.0, "weath

## 可追溯来源

- market_odds: [2026 World Cup quarterfinal odds: Argentina vs Switzerland](https://www.foxsports.com/stories/soccer/2026-world-cup-quarterfinal-odds-which-squads-will-make-final-8) — FOX Sports, available_at `2026-07-09T03:56:12.840336+00:00`
- market_odds: [market_odds snapshot for Argentina vs Switzerland](internal://pre_match_snapshots/29855761-752b-46bd-abb1-6baff11a7cf2/odds_snapshot) — pre_match_snapshots, available_at `2026-07-09T04:00:26.863966+00:00`
- news: [Argentina and Switzerland advance to quarterfinal](https://www.foxsports.com/stories/soccer/world-cup-roundup-lionel-messi-moves-on-switzerland-continues-surprise) — FOX Sports, available_at `2026-07-09T03:56:12.840336+00:00`
- schedule_context: [FIFA World Cup 2026 quarterfinal fixtures and previews](https://www.aljazeera.com/sports/2026/7/8/fifa-world-cup-2026-quarterfinal-fixtures-match-previews-schedule) — Al Jazeera, available_at `2026-07-09T03:56:12.840336+00:00`
- weather: [weather snapshot for Argentina vs Switzerland](internal://pre_match_snapshots/29855761-752b-46bd-abb1-6baff11a7cf2/weather_snapshot) — pre_match_snapshots, available_at `2026-07-09T04:00:26.863966+00:00`

## 重要说明

- 市场赔率是核心外部信号和校验基准；本报告不包含投注建议。
- 新闻/伤停/天气信号进入 information-state ledger 和 shadow scoring，不直接改生产权重。
- 赛后复盘必须使用本场 match_id=204 的 prediction_run / prediction_snapshot / feature_snapshot，不能回填赛后信息到赛前 strict snapshot。
- Risk tags: high_model_disagreement_0.73
