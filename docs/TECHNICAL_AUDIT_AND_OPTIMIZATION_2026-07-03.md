# WC26 Predict 技术审计与优化记录

日期: 2026-07-03  
范围: 后端预测链路、复盘学习、权重自进化、市场共识、公开输出审计。静态前端暂不纳入本次优化范围。

## 结论

1. 架构仍有优化空间。核心问题不是“模型不够多”，而是预测链路、复盘证据、权重提案和市场共识需要更强的可重放性与闸门约束。
2. 新模型不应直接上线。动态双变量 Poisson / 动态贝叶斯类模型值得进入候选池，但必须先通过成对 walk-forward 回测与 `BacktestGate`。
3. 复盘系统可以进化，但前提是只让 verified / full-tier 样本驱动参数候选，并且候选必须持久化、可审计、不过闸不生效。
4. 当前公开输出审计发现 `reports/` 存量文件存在赔率/博彩商/投注相关词，默认视为发布前需要净化的内容。本次不擅自改写历史报告。

## 已落地改动

- 统一动态市场 boost 判断到纯函数，修复同步/异步路径对 DC-Enhancer 分歧使用相反条件的问题。
- 快速化并稳定化字符化测试与 Dashboard 集成测试：测试默认禁用市场、天气和快照写入，CLI 子进程使用 UTF-8 读取。
- 学习归因改为可重放口径：最终 Brier 使用最终概率，边际归因使用历史快照权重；Pi 与 NegBin 纳入顺序重构。
- 快照持久化增加历史 `weight_config`、`pre_market_probs`、`market_weight_used`、`negbin_weight`、`calibration_applied`。
- 新增 `model_weight_proposals` 审计表、ORM model、Alembic migration 和 `BacktestGate`。权重候选只记录为 proposal，不会自动写生产配置。
- The Odds API / MarketCalibrator 改为多 bookmaker 中位数共识，不再只取第一家或单一 bookmaker。
- 新增公开输出审计脚本 `backend/scripts/audit_public_outputs_no_odds.py`。

## 验证结果

- 后端全量测试: `466 passed, 4 skipped`
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

1. 建立 walk-forward candidate runner，把动态双变量 Poisson / 动态贝叶斯候选统一输出到 `WeightProposalCandidate`。
2. 将 stacking meta-learner 的训练和评估统一纳入 proper scoring 指标，不允许只用方向准确率决定上线。
3. 清理报告生成模板，使 public / creator 输出默认不含裸赔率、bookmaker 名、投注词；内部研究报告继续允许保留。
4. 收敛剩余预测入口：长期目标是 CLI、API、批处理、模拟器都只调用同一个纯 fusion engine。
