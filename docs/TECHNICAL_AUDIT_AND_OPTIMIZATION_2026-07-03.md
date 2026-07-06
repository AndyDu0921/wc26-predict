# WC26 Predict 技术审计与优化记录

日期: 2026-07-03  
更新: 2026-07-06（V4.9 Accuracy Data OS + 197-200 闭环修复后事实校准）
范围: 后端预测链路、复盘学习、权重自进化、评估样本登记、候选实验框架、市场数据链路、技术债清理。静态前端暂不纳入本次优化范围。

## 结论

1. 架构仍有优化空间。核心问题不是“模型不够多”，而是预测链路、复盘证据、权重提案和市场共识需要更强的可重放性与闸门约束。
2. 新模型不应直接上线。动态双变量 Poisson / 动态贝叶斯类模型值得进入候选池，但必须先通过成对 walk-forward 回测与 `BacktestGate`。
3. 复盘系统可以进化，但前提是只让 verified / full-tier 样本驱动参数候选，并且候选必须持久化、可审计、不过闸不生效。
4. 市场赔率/多博彩商共识是重要预测信号，不再视为污染；只禁止投注建议、保证收益、带单等诱导性语言。
5. V4.9-alpha 后续修复确认：本地 DB 已升级到 Alembic `f6a7b8c9d0e1`；`model_weight_proposals`、学习日志比分字段、Accuracy Engine 审计表、通用 `model_change_proposals`、结构化情报 FeatureSnapshot v2、`prediction_snapshots` score matrix 审计字段均已落库或可物化。

## 已落地改动

- 统一动态市场 boost 判断到纯函数，修复同步/异步路径对 DC-Enhancer 分歧使用相反条件的问题。
- 快速化并稳定化字符化测试与 Dashboard 集成测试：测试默认禁用市场、天气和快照写入，CLI 子进程使用 UTF-8 读取。
- 学习归因改为可重放口径：最终 Brier 使用最终概率，边际归因使用历史快照权重；Pi 与 NegBin 纳入顺序重构。
- 快照持久化增加历史 `weight_config`、`pre_market_probs`、`market_weight_used`、`negbin_weight`、`calibration_applied`。
- 新增 `model_weight_proposals` 审计表、ORM model、Alembic migration 和 `BacktestGate`。权重候选只记录为 proposal，不会自动写生产配置。
- The Odds API / MarketCalibrator 改为多 bookmaker 中位数共识，不再只取第一家或单一 bookmaker。
- 更新输出审计脚本：当前推荐入口为 `backend/scripts/audit_public_outputs.py`；旧 `audit_public_outputs_no_odds.py` 仅作为兼容入口保留。V4.9 起只检查投注建议/保证性语言，不禁止市场赔率或 bookmaker 证据。
- 新增 evaluation registry：显式区分 `matches + match_results`、`wc26_schedule`、赛前快照、预测快照、过程评估；只有无结果冲突、可验证 kickoff、且有赛前概率快照的样本才能进入严格回测。
- 新增 shadow candidate experiment runner：输出 `experiment_id`、`sample_registry_hash`、paired metrics、group metrics、leakage checks 与 gate decision；不写生产权重，不覆盖 artifacts。
- 修复过程评估假信号：没有赛前 expected shots 时，射门量 delta 留空，不再用实际射门减实际射门制造 0 误差。
- 新增 player availability shadow 组件：只生成可审计 xG modifier 证据，缺数据时零影响，过 gate 前不影响生产预测。
- 抽出共享 score-matrix fusion helper，sync/async 路径复用同一套 DC + NegBin + Weibull 融合逻辑。
- V4.8 新增 Accuracy Engine v2：`evaluation_registry.v2` 显式输出 strict / diagnostic / rejected 样本池、horizon、leakage status、数据可用性和 registry hash。
- V4.8 新增 `PredictionKernel`：API async、sync/CLI、批处理的核心概率融合走同一纯 kernel，外层仅负责 I/O 和报告。
- V4.8 新增 shadow candidate pool 与统一 experiment runner：动态 DC、动态双变量 Poisson、Bayesian weighted dynamic、covariate ML、Dirichlet calibration、stacking optimizer 均只离线评估。
- V4.8 新增 `model_change_proposals` 通用提案 ledger：自进化只能生成 proposal，不会自动改生产权重、校准器或模型 artifacts。
- V4.8 关键数据口径修复：`wc26_schedule` 可在无冲突时补齐 canonical result；schedule-only 样本使用 `match_date + kickoff_time` 做 kickoff source，并保留审计来源。
- V4.8/V4.9 新增并扩展 `feature_snapshots` 物化：当前 registry 可构建 `32` 条 strict 样本；payload 不包含真实比分字段。
- V4.9 新增 evaluation registry repair report v2：逐场给出 `missing_pre_match_snapshot`、`snapshot_or_kickoff_time_unknown`、`missing_current_probabilities`、赛后快照、结果冲突的修复动作，并输出 priority、blocking level、repair order 和 action groups；只允许真实赛前证据提升 strict。
- V4.9 新增 accuracy todo backlog：从 registry、repair report、DB integrity、市场赔率覆盖、阵容/伤停覆盖和 `prediction_pipeline.py` 规模生成只读 TODO，不创建快照、概率、权重或报告。
- V4.9 新增 strict sample repair queue：逐场检查本地 `pre_match_snapshots` / `prediction_snapshots` 是否存在真实赛前概率证据；当前本地没有更多 diagnostic 样本可直接提升 strict。
- V4.9 新增 candidate experiment preflight：默认候选实验在 DB 不干净或 strict/eligible 样本不足时直接阻塞，并返回 blockers；`--force` 仅用于只读诊断，不构成上线证据。
- V4.9 修复 registry 主证据选择：同场若存在赛后 `pre_match_snapshots` 和真实赛前 `prediction_snapshots`，严格评估优先使用可验证赛前概率记录，避免赛后快照误杀样本。
- V4.9 新增 structured information-state signals：伤停/停赛等本地记录必须带 `available_at`、`published_at`、`confidence`、`source`，未来信号不得进入 strict features。
- V4.9 扩展 FeatureSnapshot v2：加入结构化情报、player availability shadow、schedule context 和数据质量摘要；当前 `feature_snapshots` 表保留 93 条历史审计记录。
- V4.9 扩展 self-evolution proposal ledger：新增 `data-repair` proposal 类型，并区分 `calibrator`、`stacking` 与普通 `model` 候选。
- V4.9 清理旧实验入口：`backtest_full_pipeline.py`、`grid_search_score_params.py`、`collect_stacking_training_data.py` 变成统一 experiment runner wrapper，不再覆盖旧 artifact。
- V4.9 新增数据库完整性审计/修复：`audit_db_integrity.py` 默认只读；apply 模式会先备份 DB，精确修复 team aliases，空 nullable FK 归一为 NULL，其余孤儿行隔离到 `data_integrity_quarantine`，不伪造父记录。
- V4.9 删除确定过时的长文档：`docs/PRD_ARCHITECTURE_COMPLETE.md`、`docs/EXTERNAL_REVIEW_SUMMARY.md`、`backend/docs/POSTMATCH_SOP.md`，避免 V3/V4.5/V4.8 历史事实污染当前判断。

## 验证结果

- 后端全量测试: `541 passed, 4 skipped`。
- Evaluation registry v2 dry-run: `87` total samples, `87` canonical result rows, `62` match-result rows, `85` schedule-finished rows, `32` strict eligible samples, `46` diagnostic samples, `9` rejected samples, `22` registry process-eval matches, `1` source-result conflict.
- Evaluation registry repair smoke: 非 strict 样本只有在真实赛前证据补齐后才可提升 strict；禁止 placeholder probability、赛后补预测、无时间戳信号进入 strict。
- Feature snapshot materialization: `feature_snapshots` 表当前 `93` 条历史审计记录；payload 抽查无 actual-goal labels。
- Candidate experiment smoke: 默认 `min_sample_count=30` 时 preflight 当前 ready（strict=32），但仍低于 50+ 目标；shadow candidate 的 CI 仍跨 0 时不得上线。
- Proposal ledger smoke: `model_change_proposals` 当前 `30` 条，权重、数据修复、feature-rule、calibrator、stacking proposal 均保持 proposal-only。
- Alembic 当前本地 head: `f6a7b8c9d0e1`
- DB integrity: `PRAGMA integrity_check=ok`，`PRAGMA foreign_key_check=0`；历史孤儿行 `104` 条已隔离到 `data_integrity_quarantine` 并保留备份。
- 代码编译检查: 核心变更文件 `py_compile` 通过。

## 研究依据

- Dixon & Coles, 1997, JRSS C: Poisson-based football score model with dynamic team performance and betting-market comparison. DOI: `10.1111/1467-9876.00065`.
- Karlis & Ntzoufras, 2003, The Statistician: bivariate Poisson sports data modelling. DOI: `10.1111/1467-9884.00366`.
- Koopman & Lit, 2015, JRSS A: dynamic bivariate Poisson model for English Premier League forecasting. DOI: `10.1111/rssa.12042`.
- Hvattum & Arntzen, 2010, International Journal of Forecasting: Elo ratings for football match-result prediction. DOI: `10.1016/j.ijforecast.2009.10.002`.
- Constantinou & Fenton, 2013, Journal of Quantitative Analysis in Sports: Pi-rating dynamic score-discrepancy ratings. DOI: `10.1515/jqas-2012-0036`.
- Gneiting & Raftery, 2007, JASA: strictly proper scoring rules for probabilistic forecast evaluation. DOI: `10.1198/016214506000001437`.

## 下一步 Plan

1. 继续提升 strict eligible 样本数：优先补齐剩余 `46` 个 diagnostic 样本的赛前快照、current probabilities 和真实 kickoff 证据。
2. 用统一 experiment runner 继续扩大动态 DC / 动态双变量 Poisson / 动态贝叶斯 / stacking / calibration 候选的无泄漏 paired evidence。
3. 扩大 `feature_snapshots` 与球员可用性数据；没有赛前 `available_at` 的数据只能进诊断池，不能驱动生产概率。
4. 保留报告中的市场赔率与 bookmaker 证据；只清理投注建议、保证收益、带单等诱导性表达。
5. 继续收敛剩余预测入口：长期目标是 CLI、API、批处理、模拟器都只调用同一个纯 kernel / experiment interface。
