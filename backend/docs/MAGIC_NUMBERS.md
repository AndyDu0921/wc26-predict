# V4.12 常量与闸门事实表

> 最后核对: 2026-07-18<br>
> 版本: `4.12.0-alpha`<br>
> 目的: 记录会影响生产概率、比分分布、数据资格或候选晋级的当前代码事实。

本文不保存历史命中率结论，也不使用易失效的代码行号。准确度只能从同一模型 cohort 的 evaluation registry 和 paired walk-forward 实验读取。任何表中数值都只是当前配置，不等于已经证明最优。

## 1. 生产边界

| 事实 | 当前值 | 代码源 |
|---|---:|---|
| 版本源 | `4.12.0-alpha` | `app/version.py` |
| 生产权重来源 | code-versioned config only | `services/weights.py` |
| DB optimizer 自动加载 | disabled | `services/weights.py` |
| 自动修改生产权重/artifact | forbidden | `services/model_change_proposals.py` |
| Active bundle 状态 | `legacy_active_unvalidated` | `artifacts/active_bundle.json` |
| Stacking 生产开关 | `False` | `core/stacking_features.py` |
| Conformal 生产开关 | `False` | `core/conformal_core.py` |

赔率和多博彩公司市场共识是核心研究证据，可以入模和出现在报告中。公开输出只禁止投注建议、带单和保证性收益语言。

## 2. 胜平负融合

### 2.1 Engine 常量

| 常量 | 当前值 | 作用 |
|---|---:|---|
| `WC_XG_CALIBRATION_FACTOR` | 1.35 | NegBin 世界杯 xG 缩放 |
| `NEGBIN_R` | 8.0 | Negative Binomial dispersion |
| `NEGBIN_FUSION_WEIGHT` | 0.05 | NegBin 顺序融合权重 |
| `DRAW_FLOOR` | 0.12 | 世界杯小组赛平局下限 |
| `KO_DRAW_FLOOR` | 0.18 | 淘汰赛 90 分钟平局下限 |
| postflight `MIN_PROB` | 0.02 | 最终 H/D/A 单项概率下限 |

### 2.2 市场动态影响

| 常量 | 当前值 | 作用 |
|---|---:|---|
| `MARKET_BOOST_ATTENUATION` | 0.6 | DC/Enhancer 冲突时的 boost 衰减 |
| `MARKET_BOOST_DC_ENH_DIVERGENCE_PP` | 15.0 | DC/Enhancer 高分歧阈值（百分点） |
| `MARKET_BOOST_DIVERGENCE_THRESHOLD` | 0.15 | 低质量市场默认分歧阈值 |
| `MARKET_BOOST_MAX` | 0.2 | `market_max` 之外的附加 boost 上限 |
| `MARKET_BOOST_SLOPE` | 1.0 | 超阈值分歧到 boost 的斜率 |
| high-consensus threshold | 0.10 | 至少 6 家且低 CV 时使用 |
| medium-consensus threshold | 0.13 | 至少 3 家时使用 |
| low-consensus threshold | 0.15 | 1-2 家时使用 |

### 2.3 市场共识 Gate

| 常量 | 当前值 | 作用 |
|---|---:|---|
| `MARKET_CONSENSUS_GATE_ENABLED` | True | 启用多博彩公司一致性检查 |
| `MARKET_CONSENSUS_CV_THRESHOLD` | 0.03 | 三项最差 CV 必须低于 3% |
| `MARKET_CONSENSUS_BOOST` | 0.08 | 高一致性时提高 market cap |
| `MARKET_CONSENSUS_MAX_CAP` | 0.45 | 市场融合绝对上限 |
| `MARKET_CONSENSUS_MIN_BOOKMAKERS` | 6 | 高一致性 gate 最少公司数 |

## 3. 生产权重

这些权重按 `DC -> Enhancer -> NegBin -> Weibull -> Elo -> Pi -> Market` 顺序应用，不是平面加权平均。`enhancer` 字段是审计信息；DC/Enhancer 第一步的真实 Enhancer 权重为 `1 - dc`。

| 场景 | DC | Enhancer | Weibull | Elo | Pi | Market max | 标签 |
|---|---:|---:|---:|---:|---:|---:|---|
| World Cup group | 0.90 | 0.10 | 0.10 | 0.12 | 0.17 | 0.30 | `WORLD_CUP_V4.7.0_ALPHA` |
| World Cup knockout | 0.90 | 0.10 | 0.05 | 0.24 | 0.22 | 0.35 | `WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA` |
| UCL final | 0.42 | 0.58 | 0.08 | 0.08 | 0.12 | 0.08 | `UCL_FINAL` |
| UCL other | 0.45 | 0.55 | 0.10 | 0.07 | 0.10 | 0.10 | `UCL_KNOCKOUT` |
| League default | 0.50 | 0.50 | 0.10 | 0.05 | 0.05 | 0.10 | `LEAGUE` |
| Friendly legacy config | 0.28 | 0.72 | 0.12 | 0.02 | 0.16 | 0.10 | `FRIENDLY_ADJUSTED_V2` |

训练样本 competition weight:

| 常量 | 当前值 |
|---|---:|
| `DEFAULT_COMPETITION_WEIGHT` | 0.9 |
| `WORLD_CUP_COMPETITION_WEIGHT` | 1.5 |
| `FRIENDLY_COMPETITION_WEIGHT` | 0.5 |

## 4. 比分分布

生产路径构建每队进球 `0..10` 的 11x11 矩阵。最终矩阵必须再次校准到最终 H/D/A 边际概率，因此 stacking、校准器或 guard 之后，比分矩阵与胜平负不会分叉。

| 参数 | 当前值 | 状态 |
|---|---:|---|
| DC score matrix weight | 0.45 | legacy, 未被当前 cohort 重新验证 |
| NegBin score matrix weight | 0.38 | legacy, 与 DC xG 有特征重叠 |
| Weibull score matrix weight | 0.17 | 仅在质量 gate 通过时使用 |
| Weibull max cell | 0.16 | 超过即 shadow |
| Weibull minimum nonzero share | 0.50 | 低于即 shadow |
| xG direction check gap | 0.25 | top score 方向冲突时 shadow |
| Score LogLoss epsilon | `1e-12` | 越界或零概率仍受惩罚，不静默删除 |

比分权重是否优于纯 DC，必须单独查看 paired Score LogLoss；精确比分命中率不能替代 proper scoring rule。

## 5. Rating 与动态模型

### 5.1 Elo-Davidson

| 常量 | 当前值 |
|---|---:|
| `DEFAULT_RATING` | 1500.0 |
| `HOME_ADVANTAGE` | 100.0 |
| `K_LEAGUE` | 20 |
| `K_KNOCKOUT` | 32 |
| `KAPPA_DEFAULT` | 0.24 |
| `KAPPA_WORLD_CUP` | 0.48 |
| `KAPPA_EPL` | 0.28 |
| `KAPPA_UCL` | 0.18 |
| draw clamp | 0.02 to 0.35 |

Elo κ 是代码版本化参数，不从可变 DB 状态读取；实际 κ 会写入预测 provenance。

### 5.2 Dixon-Coles

| 参数 | 当前值 | 说明 |
|---|---:|---|
| `half_life_days` | 180 | 指数时间衰减半衰期 |
| optimizer `maxiter` | 5000 | L-BFGS-B 迭代上限 |
| optimizer `maxfun` | 50000 | 函数调用上限 |

推理阶段必须加载已注册且 hash 匹配的 DC/Enhancer artifact；缺失时 fail closed，不允许现场隐式重训。

## 6. 信息状态

| 参数 | 当前值 | 作用 |
|---|---:|---|
| `LOW_CONFIDENCE_THRESHOLD` | 0.45 | 低于该值的结构化信号拒绝进入 shadow 调整 |
| 单条 approved signal cap | 0.15 | 单条概率影响硬上限 |
| 单队 combined signal cap | 0.20 | 正负净影响限制在 +/-20% |
| 信息质量 confidence range | 0.85 to 1.00 | `0.85 + 0.15 * quality_score` |

伤停、阵容、新闻、天气和赔率必须关联可追溯 evidence。`available_at > as_of` 的记录只进入排除诊断；`available_at > kickoff` 不能进入 strict。人工审核不得生成虚构 `evidence_id`。

## 7. 实验与自进化 Gate

| 参数 | 当前值 | 作用 |
|---|---:|---|
| candidate minimum paired samples | 30 | 不足即 rejected |
| supported mean delta | `<= -0.001` | Brier/LogLoss/RPS，越低越好 |
| supported metrics | at least 2 | 不接受只靠方向准确率或 ECE |
| paired CI | upper `<= 0` | 95% paired bootstrap CI 不跨 0 |
| bootstrap repetitions | 2000 | paired percentile bootstrap |
| subgroup minimum | 5 | 小于 5 不做 degradation gate |
| subgroup degradation | `> 0.02` | 任一核心指标超出即拒绝 |
| clean-boundary minimum | `max(20, ceil(0.70*n))` | 防止 legacy 零概率样本主导结论 |
| learning-log proposal minimum | 30 | 仅可生成待回测 proposal |

候选通过 gate 后仍只是 `shadow_candidate_only`。Promotion 还要求 registry hash、同 cohort、至少 30 条样本、先进入 `approved_for_shadow`，并由人工批准。

## 8. Feature-flagged 组件

| 常量 | 当前值 |
|---|---:|
| `STACKING_META_LEARNER_ENABLED` | False |
| `STACKING_C` | 1.0 |
| `STACKING_MAX_ITER` | 1000 |
| `STACKING_MIN_TRAINING_SAMPLES` | 20 |
| `STACKING_FEATURE_FILL` | `1/3` |
| stacking features | 7 components x 3 outcomes = 21 |
| `WEIGHTED_CONFORMAL_PREDICTION_ENABLED` | False |
| `CONFORMAL_ALPHA` | 0.1 |
| `CONFORMAL_RECENCY_HALFLIFE_DAYS` | 30.0 |
| `CONFORMAL_MIN_CALIBRATION_SIZE` | 10 |

如果开关设为 `True` 而 active bundle 中对应 artifact 缺失、hash 不匹配或结构无效，预测必须 fail closed。

## 9. 维护规则

1. 修改本表中的代码常量时，同一提交必须更新本表和 `tests/test_magic_numbers_doc.py`。
2. 不在这里写“某组件更准”或“方向命中率 X%”等时变结论。
3. 当前样本数、指标与 cohort 分布只从 `reports/audits/current_project_state.json` 获取。
4. 生产数值变更必须先产生无泄漏 paired evidence 和 proposal；文档变更不能替代 gate。
