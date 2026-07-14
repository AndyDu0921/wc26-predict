# Norway vs England 赛前预测报告

生成时间：2026-07-09T04:04:16.938449+00:00
系统版本：4.11.0-alpha / 模式：full / 比赛编号：203 (QF99)
比赛：FIFA World Cup 2026 Quarterfinal，北京时间 2026-07-12 05:00，场地：Hard Rock Stadium, Miami Gardens, FL
快照时间：2026-07-09T04:00:05.708195+00:00；kickoff_at：2026-07-12T05:00:00+08:00

## 结论

- 预测倾向：**Norway**
- 胜平负概率：Norway 39.6% / 平局 21.1% / England 39.4%
- 预期进球：Norway 1.154 / England 1.057
- 置信度：medium；模型分歧：0.413

## Top 比分矩阵

| Rank | Score | Probability |
|---:|:---:|---:|
| 1 | 0:1 | 11.7% |
| 2 | 1:0 | 11.2% |
| 3 | 1:1 | 9.5% |

## 组件概率

| Component | Norway | Draw | England | Component Pick |
|---|---:|---:|---:|---|
| Dixon-Coles | 38.4% | 28.2% | 33.4% | home |
| Tabular Enhancer | 10.5% | 24.7% | 64.8% | away |
| Negative Binomial | 40.9% | 23.5% | 35.6% | home |
| Weibull | 37.2% | 34.7% | 28.1% | home |
| Elo-Davidson | 27.8% | 23.1% | 49.2% | away |
| Pi rating | 51.8% | 20.1% | 28.0% | home |

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

- 市场赔率/共识：home=22.8%, draw=25.8%, away=51.3%, providers=24
- 天气快照：temperature_c=None, precipitation_mm=0.0, wind_speed_kmh=None, humidity_percent=None, weather_description=未知
- odds_available=1；weather_available=0；injury_data_available=1；news_signals_available=0

## 结构化信号（shadow-only）

- England `injury` negative magnitude=0.079, confidence=0.66: England: injury/negative - Team-news evidence: England training availability and illness/injury update before Norway
- Norway `injury` negative magnitude=0.079, confidence=0.66: Norway: injury/negative - Team-news evidence: England training availability and illness/injury update before Norway
- Norway `market_move` neutral magnitude=0.078, confidence=0.78: Norway: market_move/neutral - 2026 World Cup quarterfinal odds: Norway vs England Market odds evidence as-of collection: England listed around +125, Norway around +195, draw around
- Norway `market_move` neutral magnitude=0.085, confidence=0.85: Norway: market_move/neutral - market_odds snapshot for Norway vs England {"home_prob": 0.2280182452215191, "draw_prob": 0.2584822213037463, "away_prob": 0.5134995334747346, "vig": 
- Norway `weather` negative magnitude=0.025, confidence=0.85: Norway: weather/negative - weather snapshot for Norway vs England {"temperature_c": null, "precipitation_mm": 0.0, "wind_speed_kmh": null, "humidity_percent": null, "weather_code":
- Norway `weather` negative magnitude=0.022, confidence=0.74: Norway: weather/negative - Miami Gardens match-day weather for Norway vs England Forecast evidence for Miami Gardens around the Norway vs England match window: humid, high near 93F

## 可追溯来源

- market_odds: [2026 World Cup quarterfinal odds: Norway vs England](https://www.foxsports.com/stories/soccer/2026-world-cup-quarterfinal-odds-which-squads-will-make-final-8) — FOX Sports, available_at `2026-07-09T03:56:12.840336+00:00`
- market_odds: [market_odds snapshot for Norway vs England](internal://pre_match_snapshots/fca9d6d0-f277-4fd6-a822-6199fad98016/odds_snapshot) — pre_match_snapshots, available_at `2026-07-09T04:00:05.708195+00:00`
- news: [England injuries and illness update before Norway quarterfinal](https://www.talksport.com/football/3458144/england-injuries-illness-recovered-world-cup-norway/) — talkSPORT, available_at `2026-07-09T03:56:12.840336+00:00`
- news: [Turner has 8 saves as Revs add to Atlanta's woe](https://www.espn.com/soccer/report/_/gameId/761560) — www.espn.com - SOCCER, available_at `2026-04-22T22:49:05+00:00`
- schedule_context: [FIFA World Cup 2026 quarterfinal fixtures and previews](https://www.aljazeera.com/sports/2026/7/8/fifa-world-cup-2026-quarterfinal-fixtures-match-previews-schedule) — Al Jazeera, available_at `2026-07-09T03:56:12.840336+00:00`
- weather: [Miami Gardens match-day weather for Norway vs England](weather://codex-web-weather/Miami-Gardens-FL/2026-07-11) — Codex weather forecast tool, available_at `2026-07-09T04:01:14.848242+00:00`
- weather: [weather snapshot for Norway vs England](internal://pre_match_snapshots/fca9d6d0-f277-4fd6-a822-6199fad98016/weather_snapshot) — pre_match_snapshots, available_at `2026-07-09T04:00:05.708195+00:00`

## 重要说明

- 市场赔率是核心外部信号和校验基准；本报告不包含投注建议。
- 新闻/伤停/天气信号进入 information-state ledger 和 shadow scoring，不直接改生产权重。
- 赛后复盘必须使用本场 match_id=203 的 prediction_run / prediction_snapshot / feature_snapshot，不能回填赛后信息到赛前 strict snapshot。
- Internal model weather snapshot was unavailable for this match; an external weather evidence row was stored separately and kept shadow-only.
- Risk tags: 模型与市场存在显著分歧 (15.1pp), high_model_disagreement_0.41
