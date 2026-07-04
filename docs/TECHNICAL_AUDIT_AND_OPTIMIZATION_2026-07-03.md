# WC26 Predict 技术审计与优化记录

日期: 2026-07-03  
更新: 2026-07-04（V4.8 Accuracy Engine 实施后事实校准）
范围: 后端预测链路、复盘学习、权重自进化、评估样本登记、候选实验框架、公开输出审计。静态前端暂不纳入本次优化范围。

## 结论

1. 架构仍有优化空间。核心问题不是“模型不够多”，而是预测链路、复盘证据、权重提案和市场共识需要更强的可重放性与闸门约束。
2. 新模型不应直接上线。动态双变量 Poisson / 动态贝叶斯类模型值得进入候选池，但必须先通过成对 walk-forward 回测与 `BacktestGate`。
3. 复盘系统可以进化，但前提是只让 verified / full-tier 样本驱动参数候选，并且候选必须持久化、可审计、不过闸不生效。
4. 当前公开输出审计发现 `reports/` 存量文件存在赔率/博彩商/投注相关词，默认视为发布前需要净化的内容。本次不擅自改写历史报告。
5. V4.8-alpha 后续修复确认：本地 DB 已升级到 Alembic `e5f6a7b8c9d0`；`model_weight_proposals`、学习日志比分字段、Accuracy Engine 审计表和通用 `model_change_proposals` 已落库。

## 已落地改动

- 统一动态市场 boost 判断到纯函数，修复同步/异步路径对 DC-Enhancer 分歧使用相反条件的问题。
- 快速化并稳定化字符化测试与 Dashboard 集成测试：测试默认禁用市场、天气和快照写入，CLI 子进程使用 UTF-8 读取。
- 学习归因改为可重放口径：最终 Brier 使用最终概率，边际归因使用历史快照权重；Pi 与 NegBin 纳入顺序重构。
- 快照持久化增加历史 `weight_config`、`pre_market_probs`、`market_weight_used`、`negbin_weight`、`calibration_applied`。
- 新增 `model_weight_proposals` 审计表、ORM model、Alembic migration 和 `BacktestGate`。权重候选只记录为 proposal，不会自动写生产配置。
- The Odds API / MarketCalibrator 改为多 bookmaker 中位数共识，不再只取第一家或单一 bookmaker。
- 新增公开输出审计脚本 `backend/scripts/audit_public_outputs_no_odds.py`。
- 新增 evaluation registry：显式区分 `matches + match_results`、`wc26_schedule`、赛前快照、预测快照、过程评估，不把 schedule-only finished 样本混入严格回测。
- 新增 shadow candidate experiment runner：输出 `experiment_id`、`sample_registry_hash`、paired metrics、group metrics、leakage checks 与 gate decision；不写生产权重，不覆盖 artifacts。
- 修复过程评估假信号：没有赛前 expected shots 时，射门量 delta 留空，不再用实际射门减实际射门制造 0 误差。
- 新增 player availability shadow 组件：只生成可审计 xG modifier 证据，缺数据时零影响，过 gate 前不影响生产预测。
- 抽出共享 score-matrix fusion helper，sync/async 路径复用同一套 DC + NegBin + Weibull 融合逻辑。
- V4.8 新增 Accuracy Engine v2：`evaluation_registry.v2` 显式输出 strict / diagnostic / rejected 样本池、horizon、leakage status、数据可用性和 registry hash。
- V4.8 新增 `PredictionKernel`：API async、sync/CLI、批处理的核心概率融合走同一纯 kernel，外层仅负责 I/O 和报告。
- V4.8 新增 shadow candidate pool 与统一 experiment runner：动态 DC、动态双变量 Poisson、Bayesian weighted dynamic、covariate ML、Dirichlet calibration、stacking optimizer 均只离线评估。
- V4.8 新增 `model_change_proposals` 通用提案 ledger：自进化只能生成 proposal，不会自动改生产权重、校准器或模型 artifacts。

## 验证结果

- 后端全量测试: `494 passed, 4 skipped`
- Evaluation registry v2 dry-run: `78` total samples, `58` canonical match-result rows, `78` schedule-finished rows, `9` strict eligible samples, `67` diagnostic samples, `2` rejected samples, `13` process-eval rows, `1` source-result conflict.
- Candidate experiment smoke: `current_fusion` 无 supported improvement；`uniform_baseline` 与 `bayesian_weighted_dynamic` 因 strict 样本过少只能 `shadow_needs_more_evidence`；动态 Poisson 类候选在当前 9 样本下未通过 gate。
- Alembic 当前本地 head: `e5f6a7b8c9d0`
- 公开输出审计摘要: `reports/` 扫描 71 个文件，29 个文件存在 275 条 forbidden-term findings。
- 代码编译检查: 核心变更文件 `py_compile` 通过。

## 研究依据

- Dixon & Coles, 1997, JRSS C: Poisson-based football score model with dynamic team performance and betting-market comparison. DOI: `10.1111/1467-9876.00065`.
- Karlis & Ntzoufras, 2003, The Statistician: bivariate Poisson sports data modelling. DOI: `10.1111/1467-9884.00366`.
- Koopman & Lit, 2015, JRSS A: dynamic bivariate Poisson model for English Premier League forecasting. DOI: `10.1111/rssa.12042`.
- Hvattum & Arntzen, 2010, International Journal of Forecasting: Elo ratings for football match-result prediction. DOI: `10.1016/j.ijforecast.2009.10.002`.
- Constantinou & Fenton, 2013, Journal of Quantitative Analysis in Sports: Pi-rating dynamic score-discrepancy ratings. DOI: `10.1515/jqas-2012-0036`.
- Gneiting & Raftery, 2007, JASA: strictly proper scoring rules for probabilistic forecast evaluation. DOI: `10.1198/016214506000001437`.

## 下一步 Plan

1. 提升 strict eligible 样本数：补齐赛前快照和 canonical match_results 的映射，解决 `58` vs `78` 状态口径差异。
2. 用统一 experiment runner 继续扩大动态 DC / 动态双变量 Poisson / 动态贝叶斯 / stacking / calibration 候选的无泄漏 paired evidence。
3. 补齐 `feature_snapshots` 与球员可用性数据；没有赛前 `available_at` 的数据只能进诊断池，不能驱动生产概率。
4. 清理报告生成模板，使 public / creator 输出默认不含裸赔率、bookmaker 名、投注词；内部研究报告继续允许保留。
5. 继续收敛剩余预测入口：长期目标是 CLI、API、批处理、模拟器都只调用同一个纯 kernel / experiment interface。
