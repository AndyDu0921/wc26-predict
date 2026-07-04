# Magic Number 中央注册表

> **维护规则**: 每次修改常量 → 必须更新此表。每次新增常量 → 必须在此表注册。
> **最后更新**: 2026-07-04
> **对应版本**: V4.8.0-alpha

本文档是 WC26 Predict 系统中所有硬编码常量的单一真相来源。每个值附设定依据、历史来源和修改记录。

---

## 一、融合引擎 (`core/engine.py`)

### NegBin（过度离散修正）

| 常量 | 值 | 行号 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `WC_XG_CALIBRATION_FACTOR` | 1.20 | `core/engine.py` | V4.7-score grid search 当前最优；需继续用严格 walk-forward 复核 | V4.7.0-alpha | V4.7 从过时文档值 1.35 统一为代码事实 1.20 |
| `NEGBIN_R` | 8.0 | `core/engine.py` | V4.7-score grid search 当前最优；仅作为候选证据，不单独证明上线优势 | V4.7.0-alpha | V4.7 从过时文档值 3.5 统一为代码事实 8.0 |
| `NEGBIN_FUSION_WEIGHT` | 0.05 | L19 | 保守 5%，仅做微调 | V3.9.5 | — |

### Market Boost（动态市场影响增强）

| 常量 | 值 | 行号 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `MARKET_BOOST_ATTENUATION` | 0.60 | L20 | DC-Enhancer 分歧时 boost 打 6 折 | V4.1 | — |
| `MARKET_BOOST_DC_ENH_DIVERGENCE_PP` | 15.0 | L21 | DC-Enhancer >15pp 分歧触发衰减 | V4.3.1 | — |
| `MARKET_BOOST_DIVERGENCE_THRESHOLD` | 0.15 | L22 | 模型-市场 >15pp 分歧触发 boost | V4.1 | — |
| `MARKET_BOOST_MAX` | 0.20 | L23 | 最多额外 +20% 市场影响 | V4.1 | — |
| `MARKET_BOOST_SLOPE` | 1.0 | L24 | 每 pp 分歧 boost 1pp | V4.1 | — |

### Draw Floor（平局地板）

| 常量 | 值 | 行号 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `DRAW_FLOOR` | 0.12 | L25 | WC 最低平局概率（小组赛基线） | V4.3.1 | — |

### Score Matrix Fusion（V4.7-alpha）

| 常量 | 值 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|
| DC score matrix weight | 0.40 | 三源比分矩阵融合中的 DC 基础矩阵权重 | V4.7.0-alpha |
| NegBin score matrix weight | 0.35 | 过度离散修正矩阵权重；从 DC xG 派生，需注意特征重叠 | V4.7.0-alpha |
| Weibull score matrix weight | 0.25 | Copula 候选矩阵权重；可用时进入比分矩阵融合 | V4.7.0-alpha |
| Score matrix max goals | 5 | 与现有 top-score/score-logloss 评估口径保持一致 | V4.7.0-alpha |

### 自适应 DC 分歧（内联常量）

| 常量 | 值 | 位置 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `DC-Enhancer 最大分歧门限` | 20.0 pp | L190 | >20pp 触发自适应降低 DC | V4.3.1 | — |
| `DC 权重最大下降幅度` | 0.15 | L191 | 最多降 0.15（如 0.90→0.75） | V4.3.1 | — |
| `DC 自适应下降速率` | 0.015/pp | L191 | 每超 1pp 降 0.015 | V4.3.1 | — |
| `DC 最小权重地板` | 0.30 | L192 | DC 不可低于 30% | V4.3.1 | — |
| `Market boost 上限` | 0.50 | L405 | 加权后总 market 影响 ≤50% | V4.1 | — |

### Draw Floor 内部算法（`enforce_draw_floor()`）

| 常量 | 值 | 位置 | 设定依据 |
|:---|:---|:---|:---|
| Favorite 承担比例 | 70% | L219 | draw deficit 的 70% 从热门方扣除 |
| Underdog 承担比例 | 30% | L220 | 30% 从冷门方扣除 |
| 安全最低概率 | 0.02 | L219-224 | 地板抽取后单方不低于 2% |

---

## 二、权重配置 (`services/weights.py`)

### World Cup 小组赛权重

| 常量 | 值 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|
| `dc` | 0.90 | Enhancer 23% 方向正确率，dc 高权重有效压制 | V4.3.1 | V4.3.1: 0.68→0.90 |
| `enhancer`（信息性） | 0.10 | =1-dc=0.10，仅用于学习引擎追踪 | V4.3.1 | — |
| `elo` | 0.12 | 9/13=69% 方向正确，稳定锚点 | V4.3.1 | V4.3.1: 0.05→0.12 |
| `pi` | 0.17 | 9/13=69% 方向正确，最佳非市场组件 | V4.3.1 | V4.3.1: 0.14→0.17 |
| `weibull` | 0.10 | 保守 10%，30% 失败率 | V4.3.1 | — |
| `market_max` | 0.30 | 11/13=85% 方向正确，最强组件 | V4.3.1 | — |

### World Cup 淘汰赛权重

| 常量 | 值 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|
| `dc` | 0.90 | 淘汰赛 Enhancer 偏差更危险 | V4.3.1 | V4.3.1: 0.78→0.90 |
| `elo` | 0.22 | 淘汰赛竞争性强，Elo 更可靠 | V4.3.1 | V4.3.1: 0.20→0.22 |
| `pi` | 0.18 | Brier 0.29 在竞争性比赛中最佳 | V4.3.1 | V4.3.1: 0.15→0.18 |
| `market_max` | 0.30 | 淘汰赛市场数据质量高 | V4.3.1 | — |
| `weibull` | 0.10 | （与小组赛相同） | V4.3.1 | — |

### 友谊赛特殊权重

| 常量 | 值 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|
| `dc` | 0.28 | DC 在友谊赛 0/3 方向正确 | V2.7 |
| `elo` | 0.02 | Elo 在友谊赛 0/3 方向正确 | V2.7 |
| `pi` | 0.16 | Pi 在友谊赛 1/3，捕捉 DC/Enhancer 遗漏 | V2.7 |
| `weibull` | 0.12 | 微调 | V2.7 |
| `market_max` | 0.10 | 友谊赛市场数据稀疏 | V2.7 |

### 序贯融合有效权重（V4.4.2 新增）

> **关键洞察**: 序贯融合中每层 `(1 - w_n)` 乘数会稀释早期组件。DC 名义权重 0.90，但经 4 层衰减后有效权重仅 0.52–0.59。以下为所有预测输出中 `pipeline_params.effective_weights` 的固化快照。

**公式**: `dc_effective = w_dc × (1−w_wb) × (1−w_elo) × (1−w_pi)`, etc.

| 权重 | WC 小组赛 | WC 淘汰赛 | 计算链 |
|:---|:---|:---|:---|
| `dc_effective` | **0.5916** (65.7%) | **0.5181** (57.6%) | `0.90 × 0.90 × 0.88 × 0.83` / `0.90 × 0.90 × 0.78 × 0.82` |
| `enhancer_effective` | **0.0657** (7.3%) | **0.0576** (6.4%) | `0.10 × 0.90 × 0.88 × 0.83` / `0.10 × 0.90 × 0.78 × 0.82` |
| `weibull_effective` | **0.0730** (8.1%) | **0.0640** (7.1%) | `0.10 × 0.88 × 0.83` / `0.10 × 0.78 × 0.82` |
| `elo_effective` | **0.0996** (11.1%) | **0.1804** (20.0%) | `0.12 × 0.83` / `0.22 × 0.82` |
| `pi_effective` | **0.1700** (18.9%) | **0.1800** (20.0%) | `0.17` / `0.18` |
| **∑** | **1.0000** | **1.0000** | 5 个有效权重恒和为 1.0 |

> **解读**: 淘汰赛中 DC 被稀释得更严重（57.6% vs 65.7%），因为 Elo/Pi 权重更高。这解释了为什么淘汰赛预测比小组赛更依赖 Elo+Pi 共识，而非 DC 主导。该解释是结构性权重分析，不等同于准确率结论；准确率结论必须来自 evaluation registry + paired walk-forward。

### DB 自动优化权重门槛

| 常量 | 值 | 位置 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `dc` 最低合理值 | 0.20 | L307 | 低于此值拒绝自动优化，回退默认 | R4-H8 |
| `elo/pi/weibull` 最低合理值 | 0.005 | L313 | 任一 ≤0.005 即拒绝 | R4-H8 |
| `market_max` 最高合理值 | 0.95 | L319 | ≥0.95 拒绝，市场不能垄断 | R4-H8 |

---

## 三、竞争权重 (`services/prediction_pipeline.py`)

| 常量 | 值 | 行号 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `DEFAULT_COMPETITION_WEIGHT` | 0.9 | L42 | 非 WC/非友谊赛默认 | V4.1-fix |
| `WORLD_CUP_COMPETITION_WEIGHT` | 1.5 | L43 | WC 比赛权重 1.5x | V4.1-fix (Bug 16/22/24) |
| `FRIENDLY_COMPETITION_WEIGHT` | 0.5 | L44 | 友谊赛权重仅 0.5x | V4.1-fix |

---

## 四、校准器 (`services/calibration.py`)

| 常量 | 值 | 行号 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `MIN_PROB` | 0.02 | L184 | 安全最低概率，防 0% 极端输出 | V4.3.1 |
| `CALIBRATOR_MIN_SAMPLES` (WC) | 20 | — | WC 校准器至少 20 场训练样本 | V4.3.1 (Fix C3: 50→20) |
| Isotonic 裁剪 y_min | 0.0 | L91 | 保序回归最小输出 | — |
| Isotonic 裁剪 y_max | 1.0 | L91 | 保序回归最大输出 | — |
| ECE 容差 | — | L233 | Expected Calibration Error | V4.3.1 |

---

## 五、Elo 评分 (`services/elo_ratings.py`)

| 常量 | 值 | 行号 | 设定依据 | 修改记录 |
|:---|:---|:---|:---|:---|
| `DEFAULT_RATING` | 1500.0 | L27 | 新球队默认评分（⚠️ 系统不认识该球队的信号） | — |
| `HOME_ADVANTAGE` | 100.0 | L28 | 主场加分 100 Elo 分 | — |
| `K_LEAGUE` | 20 | L29 | 常规联赛 K 因子 | — |
| `K_KNOCKOUT` | 32 | L30 | 淘汰赛/世界杯 K 因子 | — |
| `kappa_elo`（默认） | 0.24 | L33 | Davidson 平局模型 κ 参数 | Fix C2: WC 场景提升 kappa |
| WC competition_weight≥ | 1.5 → kappa=0.30 | L273 | WC 高权重匹配高 kappa | Fix C2 |
| UCL competition_weight≥ | 1.2 → kappa=0.27 | L275 | UCL 中高 kappa | — |

---

## 六、Pi-Rating (`services/pi_ratings.py`)

| 常量 | 值 | 行号 | 设定依据 |
|:---|:---|:---|:---|
| `PROB_SCALE` | 0.35 | L20 | Pi 概率缩放因子 |
| `k`（Elo 权重） | 0.1 | L26 | Pi 更新中 Elo 贡献 |
| `home_adj` | 0.3（非中立场地） | L71 | 主场进攻加成 |
| xG→概率 Sigmoid 斜率 | 2.5 | L74-75 | `1/(1+exp(-xg_diff*2.5))` |
| 平局指数衰减 | 0.26 × exp(-xg_diff²/2) | L76 | xG 差 ↑ → 平局概率 ↓ |
| `pi_weight`（默认） | 0.10 | L92 | 融合默认权重（会被 WC config 覆盖） |

---

## 七、Weibull (`services/weibull_model.py`)

| 常量 | 值 | 行号 | 设定依据 | 修改记录 |
|:---|:---|:---|:---|:---|
| `FIT_TIMEOUT` | 120.0s | L47 | 训练超时阈值 | — |
| `wb_weight`（默认） | 0.15 | L204 | 融合默认权重（会被 config 覆盖） | — |
| 失败率（追踪） | ~30% | — | 训练超时+极端输出 | V4.3.1 记录 |

---

## 八、Dixon-Coles (`services/dixon_coles.py`)

| 常量 | 值 | 位置 | 设定依据 |
|:---|:---|:---|:---|
| **`half_life_days`** | **180** | L172 `__init__` → L239 `_time_weight` → L366 `fit()` 向量化 + L619 `save()`/L633 `load()` 持久化 | **Walk-forward CV 确认** — 58场WC26逐日前向回测：180d Brier=0.5411 > 60d Brier=0.5541 > 30d Brier=0.5885。粗搜索（coarse）有数据泄露导致30d虚高（0.3896），walk-forward才是真正的out-of-sample评估 |
| 粗搜索最优（有数据泄露） | 30 | — | Coarse single-fit Brier=0.3896但不可信（WC26比赛同时用于训练和评估） |
| 默认主队进攻力 | 1.0 | L225 | 未知球队默认值 |
| 默认客队防守力 | 1.0 | L227 | 未知球队默认值 |
| 未知球队攻防 modifier | 1.0 | L225-227 | 全局回退值 |
| 低样本置信度惩罚 | 0.15 | L519 | 球队历史比赛 < 5 场时施加 |
| rho（低分相关性） | 0.0（初始） | L176 | 初始化为无相关性 |

---

## 九、学习引擎 (`services/learning_engine.py`)

| 常量 | 值 | 行号 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `LEARNING_SIGNAL_MIN_SAMPLES` | 3 | — | 最少评分样本才能更新权重 | V4.0.3 |
| 信号准确率乘数公式 | 0.4 + 0.6 × accuracy | L680 | 100%=1.0, 0%=0.4（60% 降权） | V4.0.3 |
| Brier 默认值（缺失时） | 1/3=0.333 | — | 非信息性先验 | — |

---

## 十、赛后评估 (`services/postmatch.py`)

| 常量 | 值 | 行号 | 设定依据 |
|:---|:---|:---|:---|
| LogLoss epsilon | 1e-12 | L153 | 防 log(0) |
| 预测概率默认值 | 0.333/0.334/0.333 | L39-41 | 完全无信息先验 |

---

## 十一、市场共识 (`services/market/consensus.py`)

| 常量 | 值 | 行号 | 设定依据 |
|:---|:---|:---|:---|
| 完全置信所需 Provider 数 | 4 | L58 | 4+ 家 = 100% provider 置信 |
| 完全置信所需 Bookmaker 数 | 8 | L59 | 8+ 家 = 100% bookmaker 置信 |

### 十一-B、去水分域驱动修正 (`services/market/probability.py`)

**来源**: Karimov et al. (2025) Mathematics MDPI — 359,035 场比赛分析，发现博彩商对平局和客胜系统性高估 3-8%。

| 常量 | 值 | 适用场景 | 设定依据 |
|:---|:---|:---|:---|
| Draw 修正因子 | 0.94 (strong fav) / 0.95 (balanced/away fav) | 平局概率降权 5-6% | 博彩商系统性高估平局 |
| Away 修正因子 | 0.96 (strong home fav) / 0.98 (balanced) | 客胜概率降权 2-4% | 博彩商高估冷门客胜 |
| Home 修正因子 | 0.97-0.98 (balanced/away fav) | 主胜概率微调 | 非热门的偏主队被低估 |
| 域检测门限 (strong home) | home > 0.50 | 强主队域 | 修正目标：平局+客胜 |
| 域检测门限 (balanced) | \|home − away\| < 0.10 | 平衡域 | 修正目标：平局为主 |
| 域检测门限 (away fav) | away > home + 0.05 | 强客队域 | 修正目标：平局+主胜 |

### 十一-C、MC λ 多项式系数 (`services/tournament_simulator.py`)

**来源**: ~~Csató & Gyimesi (2025) EJOR~~ **⚠️ UNVERIFIED_SOURCE** — 标注为"40,000+ 场比赛拟合"，但已知 Csató & Gyimesi (2025) EJOR 论文主题是 48 队世界杯赛制与竞争不平衡，并非 win-prob→xG 多项式。真实出处待确认。已通过 feature flag `USE_CG_LAMBDA_POLYNOMIAL=False` 默认关闭，仅影响 tournament simulator 不影口向单场预测。替换启发式 λ = 1.0 + 0.8×(hw−aw)。

| 系数 | 值 | 设定依据 |
|:---|:---|:---|
| W⁴ 系数 | 3.904 | 4 次多项式拟合 40,000+ 场 |
| W³ 系数 | −0.585 | 同上 |
| W² 系数 | −2.983 | 同上 |
| W¹ 系数 | 3.132 | 同上 |
| 常数项 | 0.332 | 零胜率球队最低期望进球 0.332 |
| λ 裁剪下限 | 0.30 | tournament_simulator L371 |
| λ 裁剪上限 | 2.50 | tournament_simulator L371 |

---

## 十二、复盘自动化 (`scripts/auto_postmatch.py`)

| 常量 | 值 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|
| `VERIFICATION_MIN_SOURCES` | 2 | 最少独立源才能达成共识 | R4-C10-fix |

---

## 十三、V4.8 Accuracy Engine 审计闸门

> 以下常量只控制 shadow 实验、自进化提案和评估审计；不改变生产预测权重，不代表任何候选模型已被证明更准。

### Candidate Experiment (`services/candidate_experiments.py`)

| 常量 | 值 | 位置 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `CandidateExperimentConfig.min_sample_count` | 30 | config 默认值 | 少于 30 个 paired strict 样本只允许 smoke / diagnostic，不允许生成上线证据 | V4.8.0-alpha |
| Supported improvement threshold | `mean_delta <= -0.001` | `_shadow_gate_decision()` | 避免把浮点噪声或近似无变化当成改进 | V4.8.0-alpha |
| Supported improvement count | `>= 2` 个核心 proper scoring 指标 | `_shadow_gate_decision()` | 候选至少同时改善 Brier / LogLoss / RPS 中两个指标，不能只靠单指标或 direction accuracy | V4.8.0-alpha |
| CI gate | `ci95 upper <= 0` | `_shadow_gate_decision()` | 配对差值的 95% 置信区间不能跨 0 才算 supported improvement | V4.8.0-alpha |
| CI multiplier | 1.96 | `_ci95()` | 正态近似 95% 区间；小样本下只作保守 shadow gate，不作显著性宣称 | V4.8.0-alpha |
| ECE bins | 10 | `_ece()` | 标准十分箱校准粗检；ECE 只作辅助指标，不能单独放行候选 | V4.8.0-alpha |
| LogLoss epsilon | `1e-12` | `_logloss()` / `_score_logloss_mean()` | 防止 `log(0)`，保持与赛后评估数值稳定策略一致 | V4.8.0-alpha |

### Model Change Proposals (`services/model_change_proposals.py`)

| 常量 | 值 | 位置 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| `min_active_logs` | 30 | `build_learning_log_weight_proposal()` 默认值 | 少于 30 条 active / diagnostic 学习日志不生成有效权重候选 | V4.8.0-alpha |
| Marginal action threshold | `±0.002` | `_marginal_recommendations()` | 只把平均边际贡献绝对值超过 0.002 的组件标记为考虑增减，其余保持 hold | V4.8.0-alpha |

## 十三、淘汰赛特殊参数（手动融合脚本）

| 常量 | 值 | 适用范围 | 设定依据 | 引入版本 |
|:---|:---|:---|:---|:---|
| KO draw multiplier | 1.15-1.18 | 淘汰赛 | 结构性平局上调 | V4.3.5 (GER-PAR 复盘) |
| KO draw floor | 0.18-0.22 | 淘汰赛 | 淘汰赛平局最低概率 | V4.3.5 (GER-PAR 复盘) |

> ⚠️ **注意**：上述两个值是散装的（不同比赛用不同值）。Phase 3（16 场 R32 全部完赛）后将统一为单一参数。

---

## 十四、组件性能追踪（`project-status.md`，非代码常量）

这些是运行时统计数据，但同样影响决策。每次复盘后更新。

| 指标 | 当前值 (17 场) | 趋势 | 下次验证 |
|:---|:---|---|:---|
| Market 方向正确率 | 76% (13/17) | ↓ | R32 完赛后 |
| DC 方向正确率 | 71% (12/17) | ↓ | R32 完赛后 |
| Pi 方向正确率 | 65% (11/17) | ↓ | R32 完赛后 |
| Elo 方向正确率 | 59% (10/17) | ↓ | R32 完赛后 |
| Enhancer 方向正确率 | 24% (4/17) | ↓ | R32 完赛后 |
| Weibull 失败率 | ~30% | — | Phase 3 决定命运 |
| 淘汰赛平局实际率 | 50% (2/4) | ⚠️ 严重低估 | 每场淘汰赛更新 |

---

## 附录：修改日志

| 日期 | 常量 | 旧值 | 新值 | 原因 |
|:---|:---|:---|:---|:---|
| 2026-06-23 | WC dc | 0.68 | 0.90 | P1-4: Enhancer 23% 诊断揭示系统性偏差 |
| 2026-06-23 | WC elo | 0.05 | 0.12 | WC 权重全面调优 |
| 2026-06-23 | WC pi | 0.14 | 0.17 | Pi 累计 69% 方向正确，最佳非市场组件 |
| 2026-06-23 | KO dc | 0.78 | 0.90 | 淘汰赛与小组赛统一高 dc |
| 2026-06-23 | KO elo | 0.20 | 0.22 | 淘汰赛 Elo 更可靠 |
| 2026-06-23 | KO pi | 0.15 | 0.18 | 淘汰赛 Pi 微调 |
| 2026-06-29 | KO draw multiplier | — | 1.15-1.18 | GER-PAR 复盘：淘汰赛平局结构性低估 |
| 2026-06-29 | KO draw floor | — | 0.18-0.22 | GER-PAR 复盘：淘汰赛平局地板 |
| 2026-06-26 | CALIBRATOR_MIN_SAMPLES | 50 | 20 | Fix C3: WC 样本不足，降低阈值 |
| 2026-06-22 | WC_XG_CALIBRATION_FACTOR | 1.20 | 1.35 | V3.9.6 调优回退 |
| 2026-06-22 | kappa_elo (WC) | 0.24 | 0.30 | Fix C2: WC 提升 kappa 修正平局低估 |
| 2026-06-20 | DB sanity dc floor | — | 0.20 | R4-H8: 自动优化权重门槛 |
| 2026-06-20 | DB sanity market_max cap | — | 0.95 | R4-H8: 市场垄断防护 |
| 2026-06-18 | MIN_PROB | — | 0.02 | V4.3.1: 安全裁剪防 0% 极端输出 |
| 2026-06-30 | MC λ 公式 | 1.0+0.8×(hw−aw) | Csató-Gyimesi 4次多项式 | B2: 40,000场拟合替换启发式 |
| 2026-06-30 | De-vig 方法 | 纯 proportional | 域驱动修正 (Karimov et al. 2025) | B3: 359,035场分析，修正平局/客胜系统性高估 |
| **2026-07-01** | **DC `half_life_days`** | **180** | **180（不变）** | **P1-2: Walk-forward CV确认180d最优（Brier=0.541），短半衰期在粗搜索中因数据泄露虚高** |
| 2026-07-01 | CG λ polynomial | 4次多项式 | **feature-flag 关闭** | **P0-3: 文献归属验证失败，标注 UNVERIFIED_SOURCE** |
| **2026-07-01** | **序贯融合有效权重** | **名称权重** | **pipeline_params 自动输出** | **P1-2: 固化WC小组赛/淘汰赛有效权重快照; DC名义0.90→小组有效0.59/淘汰有效0.52** |

---

## 15. A3 Stacking Meta-Learner (`core/stacking_features.py` + `services/stacking_meta_learner.py`)

| 常量 | 值 | 行号/位置 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `STACKING_META_LEARNER_ENABLED` | `False` | `stacking_features.py:28` | 回测验证后才开启 | V4.5 | — |
| `STACKING_C` | `1.0` | `stacking_features.py:31` | LogisticRegression 默认正则化强度 | V4.5 | — |
| `STACKING_MAX_ITER` | `1000` | `stacking_features.py:32` | LBFGS 收敛上限 | V4.5 | — |
| `STACKING_MIN_TRAINING_SAMPLES` | `20` | `stacking_features.py:33` | 最少拟合样本数，不足则 fallback uniform | V4.5 | — |
| `STACKING_FEATURE_FILL` | `1/3` | `stacking_features.py:34` | 缺失组件默认填充概率 | V4.5 | — |
| `STACKING_FEATURE_KEYS` | 7 组件 | `stacking_features.py:37` | 特征向量canonical顺序 (DC,Enh,NB,WB,Elo,Pi,Market) | V4.5 | — |

**设计依据**: IPP Porto (2025) 多篇论文独立验证 stacking 优于单一融合。7 组件 → 21 特征 → 3 类 multinomial logistic regression。系数序列化为 JSON（非 pickle）以保证可审计性。

**已知风险**: NegBin 概率由 DC xG 推导而来（`compute_negbin_probs()` 输入为 DC 输出的 home_xg/away_xg），与 DC 本身携带高度重叠信号。在 58 样本 + 只有 DC/Elo/Pi 有效的条件下，LR 可能不可靠地同时给 DC 和 NegBin 分配大系数（当前 |coef| ≈ 0.64），因为两者信息几乎相同。训练数据中仅 3/7 组件有真实概率，剩余 4 组件均匀填充 → 系数矩阵对均匀特征几乎是零敏感（|coef| < 1e-4）。当 Enhancer/Weibull/Market 有真实概率后应重新训练并确认 NegBin 系数是否合理。

---

## 16. B1 Weighted Conformal Prediction (`core/conformal_core.py` + `services/conformal_predictor.py`)

| 常量 | 值 | 行号/位置 | 设定依据 | 引入版本 | 修改记录 |
|:---|:---|:---|:---|:---|:---|
| `WEIGHTED_CONFORMAL_PREDICTION_ENABLED` | `False` | `conformal_core.py:24` | 校准集构建后才开启 | V4.5 | — |
| `CONFORMAL_ALPHA` | `0.1` | `conformal_core.py:27` | 90% nominal coverage | V4.5 | — |
| `CONFORMAL_RECENCY_HALFLIFE_DAYS` | `30.0` | `conformal_core.py:28` | 30天后权重衰减 50% | V4.5 | — |
| `CONFORMAL_MIN_CALIBRATION_SIZE` | `10` | `conformal_core.py:29` | 最少校准记录数 | V4.5 | — |

**设计依据**: Stocker et al. (2025) + Barber & Pananjady (2025)。Split-conformal with exponential recency weighting。Nonconformity score = 1 − P(true_class)。不支持 Bootstrap — 仅需校准集存储。
