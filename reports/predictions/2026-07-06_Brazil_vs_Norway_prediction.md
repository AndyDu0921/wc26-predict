# Brazil vs Norway — 赛前预测分析

**系统版本**：4.9.0-alpha  
**本地 match_id**：197  
**官方比赛**：FIFA MatchNumber 91 / IdMatch 400021532  
**开球时间**：2026-07-05 20:00 UTC / 北京时间 2026-07-06 04:00  
**场地**：New York/New Jersey Stadium, New Jersey  
**官方来源**：https://api.fifa.com/api/v3/live/football/17/285023/289288/400021532  
**生成时间**：2026-07-05T06:34:34Z

## 核心预测

| 结果 | 概率 |
|:--|--:|
| Brazil 胜 | 48.8% |
| 平局 | 20.7% |
| Norway 胜 | 30.5% |

**模型首选方向**：Brazil（48.8%）  
**预测 xG**：Brazil 1.294 - 1.400 Norway  
**置信状态**：fitted  
**校准**：已应用；ECE=0.052319

## 最可能比分

| Rank | Score | Probability |
|--:|:--|--:|
| 1 | 1:0 | 11.72% |
| 2 | 2:1 | 10.82% |
| 3 | 1:1 | 9.76% |

## 组件分歧

| 组件 | H/D/A |
|:--|:--|
| Dixon-Coles | 35.0% / 25.1% / 40.0% |
| Tabular enhancer | 25.1% / 18.3% / 56.6% |
| Negative binomial | 37.1% / 20.7% / 42.2% |
| Weibull | 31.3% / 39.1% / 29.7% |
| Elo | 50.4% / 22.8% / 26.7% |
| Pi rating | 52.0% / 20.1% / 27.9% |
| External benchmark shadow | 53.4% / 25.6% / 21.0% |

## 风险与降级

- weather: forecast_unavailable
- postflight_gate:external_benchmark_provider_count: Only 1 external benchmark sample — insufficient for robust consensus.  benchmark_max capped at 15%.

- Risk tags: ["模型与外部基准存在显著分歧 (18.2pp)"]
- external benchmark shadow 仅有单一外部样本；项目 gate 标记为单 provider，不作为强共识。
- Lineups: 项目当前 sync pipeline 标记为 `skipped`，未把首发作为数值输入。

## Source Status

| Source | Status | Reason |
|:--|:--|:--|
| match_context | used | explicit_context_supplied |
| injuries | used | relevant_records_applied |
| news | unavailable | no_approved_signals |
| lineups | skipped | not_implemented_in_sync_pipeline |
| weather | unavailable | forecast_unavailable |
| external_benchmark | used | shadow_mode_loaded |

## 声明

此报告为内部研究预测，不构成任何决策建议。赛前实时信息只采用可追溯来源；缺失的首发、天气或多源外部共识不会被编造。
