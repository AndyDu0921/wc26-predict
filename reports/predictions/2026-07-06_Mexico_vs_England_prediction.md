# Mexico vs England — 赛前预测分析

**系统版本**：4.9.0-alpha  
**本地 match_id**：198  
**官方比赛**：FIFA MatchNumber 92 / IdMatch 400021531  
**开球时间**：2026-07-06 00:00 UTC / 北京时间 2026-07-06 08:00  
**场地**：Mexico City Stadium, Mexico City  
**官方来源**：https://api.fifa.com/api/v3/live/football/17/285023/289288/400021531  
**生成时间**：2026-07-05T06:35:01Z

## 核心预测

| 结果 | 概率 |
|:--|--:|
| Mexico 胜 | 39.0% |
| 平局 | 25.1% |
| England 胜 | 35.9% |

**模型首选方向**：Mexico（39.0%）  
**预测 xG**：Mexico 0.591 - 0.775 England  
**置信状态**：fitted  
**校准**：已应用；ECE=0.052319

## 最可能比分

| Rank | Score | Probability |
|--:|:--|--:|
| 1 | 1:0 | 21.09% |
| 2 | 0:1 | 17.53% |
| 3 | 0:0 | 15.37% |

## 组件分歧

| 组件 | H/D/A |
|:--|:--|
| Dixon-Coles | 25.5% / 37.9% / 36.6% |
| Tabular enhancer | 12.4% / 19.3% / 68.4% |
| Negative binomial | 27.7% / 32.0% / 40.2% |
| Weibull | 57.9% / 35.3% / 6.9% |
| Elo | 31.8% / 23.7% / 44.5% |
| Pi rating | 41.6% / 20.6% / 37.8% |
| External benchmark shadow | 31.4% / 29.7% / 39.0% |

## 风险与降级

- postflight_gate:all_components_run: Only 6/7 components ran.  Missing components must be declared in the report with explicit reason.
- postflight_gate:external_benchmark_applied: external_benchmark_applied=False. If external benchmark data was available but not applied, this must be treated as a reportable diagnostic.
- postflight_gate:external_benchmark_provider_count: Only 1 external benchmark sample — insufficient for robust consensus. benchmark_max capped at 15%.

- Risk tags: ["high_model_disagreement_0.46"]
- external benchmark shadow 仅有单一外部样本；项目 gate 标记为单 provider，不作为强共识。
- Lineups: 项目当前 sync pipeline 标记为 `skipped`，未把首发作为数值输入。

## Source Status

| Source | Status | Reason |
|:--|:--|:--|
| match_context | used | explicit_context_supplied |
| injuries | unavailable | no_relevant_records |
| news | unavailable | no_approved_signals |
| lineups | skipped | not_implemented_in_sync_pipeline |
| weather | used | forecast_loaded |
| external_benchmark | used | shadow_mode_loaded |

## 声明

此报告为内部研究预测，不构成任何决策建议。赛前实时信息只采用可追溯来源；缺失的首发、天气或多源外部共识不会被编造。
