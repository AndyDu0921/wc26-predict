# WC26 Predict — 完整 PRD、架构文档与解决方案

> **当前代码版本**: V4.8.0-alpha
> **日期**: 2026-07-01  
> **作者**: AndyDu  
> **仓库**: github.com/AndyDu0921/wc26-predict  
> **用途**: 供第三方AI / 新开发者完整理解项目全貌

---

> ⚠️ **准确性警告 (Accuracy Notice)**
>
> 本文档主体包含 V4.3-V4.5 阶段的历史架构说明。当前运行代码以 `backend/app/version.py` 的 `V4.8.0-alpha` 为准；旧版本字符串保留为历史上下文，请以实际代码和 `CHANGELOG.md` 最新章节为准：
>
> | 文档声明 | 实际状态 | 说明 |
> |:---|:---|:---|
> | **B1**: NegBin 5% 融合只在 CLI，API/Dashboard 路径缺少 | ✅ 已修复 | V4.3.0 S4 (commit `9356159`) 已加入 `predict_match()` 和 `predict_sync()` |
> | **C5**: README 版本号仍是 V4.2.2 | ✅ 已修复 | README 当前版本 V4.8.0-alpha |
> | **Fix 1 设计**: `engine.py` 为 ~400 行 `PredictionEngine` 类 + `run()` 方法 | ⚠️ 简化实现 | 实际为 147 行纯函数模块（`negbin_pmf`, `overdispersed_scoreline`, `fuse_dc_enhancer_adaptive`, `enforce_draw_floor`）— 无类架构 |
> | **C3**: `prediction_enhanced.py` 是多余的包装层 | ⚠️ 不准确 | 实际提供 ~250 行实质功能（天气、LLM、市场融合），是 Dashboard 的合法适配器 |
> | **2.2**: Final 累计准确率 50% | ⚠️ 无依据 | 该数字不存在于赛后 memory 文件中，来源不明 |
> | **Section 6 表**: `prediction_pipeline.py` 2189 行、`predict_match_full.py` 757 行 | ⚠️ 已变化 | 当前分别约为 2889 行和 189 行；主体表格保留历史上下文 |
> | **Phase 2-5 迁移路线图**: 标记为"待执行" | ⚠️ 未执行 | 完整迁移路线图未实施 |

> 本文档对**项目目标、融合链设计、组件描述、数据库设计、复盘流程**的描述仍基本准确。架构缺陷诊断（三条路径、文件臃肿、复盘碎片化）方向正确且仍适用。

---

## 目录

1. [项目概述与目标](#1-项目概述与目标)
2. [当前版本状态](#2-当前版本状态)
3. [完整文件结构](#3-完整文件结构)
4. [数据库设计](#4-数据库设计)
5. [核心预测流水线详解](#5-核心预测流水线详解)
6. [三条预测路径对比](#6-三条预测路径对比)
7. [调用链详解](#7-调用链详解)
8. [赛后复盘与自进化系统](#8-赛后复盘与自进化系统)
9. [Dashboard 与 Web 层](#9-dashboard-与-web-层)
10. [已知问题与架构缺陷](#10-已知问题与架构缺陷)
11. [解决方案](#11-解决方案)
12. [迁移路线图](#12-迁移路线图)

---

## 1. 项目概述与目标

### 1.1 项目定义

**WC26 Predict** 是 2026 年 FIFA 世界杯的足球比赛概率预测研究系统。项目核心目标：

1. **赛前**: 在只使用赛前已知信息的前提下，输出主胜/平局/客胜三路概率
2. **赛后**: 将预测结果与真实赛果对比，按 Brier / LogLoss / RPS 评估误差
3. **自进化**: 从误差中学习，调整模型组件权重，通过 walk-forward 回测后人工批准上线

### 1.2 非目标（明确不做的事）

- **不做博彩/投注建议** — 项目是研究工具
- **不承诺准确率** — 输出概率分布，不输出"必赢"结论
- **不展示裸赔率** — 市场赔率仅作为内部特征使用
- **不支持用户注册/登录** — 仅供研究使用

### 1.3 技术栈

| 层级 | 技术 |
|:---|:---|
| 后端框架 | FastAPI (Python 3.11+) |
| 数据库 | SQLite (本地) + PostgreSQL (可选远程) |
| 异步 | asyncio + Celery (Redis broker) |
| 前端/Dashboard | Streamlit (Python 原生) |
| 模型 | Dixon-Coles, Elo, Pi-Rating, Weibull Copula, Tabular Enhancer |
| AI/LLM | DeepSeek V4 Pro (OpenAI兼容API) |
| 部署 | nginx 反向代理 + Uvicorn |

---

## 2. 当前版本状态

### 2.1 版本信息

```
版本号: 4.5.0-beta
Git tag: v4.5.0-beta
Build:   V4.5.0 测试版 — A3 Stacking元学习器 + B1加权共形预测 + 7组件21维特征 + DC半衰期学习(180d最优)
```

### 2.2 预测准确率 (累计19场 WC 小组赛)

| 组件 | 方向准确率 | Avg Brier | 评价 |
|:---|---:|---:|:---|
| Market | 11/13 (85%) | 0.49 | 最可靠信号 |
| DC | 10/13 (77%) | 0.64 | 稳定基础 |
| Pi | 9/13 (69%) | 0.57 | 最佳非市场组件 ↑ |
| Elo | 9/13 (69%) | 0.67 | 与Pi持平 |
| Enhancer | 3/13 (23%) | 0.68 | 系统性偏向下盘 |
| **FINAL** | **50%** | **0.50** | 受Enhancer拖累 |

### 2.3 当前权重配置 (WORLD_CUP_V4.5.0)

```python
dc = 0.68          # Dixon-Coles base weight (enhancer blend = 1-dc = 0.32)
enhancer = 0.32    # 仅用于学习引擎归因，不控制融合比例
elo = 0.12         # 2/6 June26方向正确，偏低但暂不动
pi = 0.17          # ↑ 0.14→0.17 (June26赛后自进化)
weibull = 0.10     # 保持
market_max = 0.30  # 市场最大融合权重
```

### 2.4 比赛进展

- 54/104 场小组赛已完成（finished）
- 50 场待进行
- 已进入淘汰赛阶段

---

## 3. 完整文件结构

```
D:\hermes agent\2026世界杯分析\
│
├── .env / .env.example / .env.local         # 环境变量（不提交）
├── .gitignore
├── README.md                                 # 项目首页
├── CHANGELOG.md                              # 版本历史
├── LICENSE (MIT)
│
├── docs/
│   └── COMPLIANCE_AND_OUTPUT_POLICY.md       # 合规与输出策略
│
├── data/
│   └── team_tournament_status.json           # 48支球队分组/状态 (31KB)
│
├── memory/                                   # 赛后复盘记忆文件
│   ├── wc-postmatch-*-20260626.md            # 6场June26逐场
│   └── wc-postmatch-summary-20260625.md      # 累计19场总结
│
├── reports/
│   ├── June25_Article_Draft.md               # 个人草稿
│   ├── June25_Predictions_Report.md          # 6·25预测报告
│   ├── June26_Predictions_Report.md          # 6·26预测报告
│   └── postmatch/
│       ├── 2026-06-20_Brazil_Haiti_postmatch.md
│       ├── 2026-06-21_Spain_SaudiArabia_postmatch.md
│       ├── 2026-06-22_Argentina_Austria_postmatch.md
│       ├── 2026-06-22_France_Iraq_postmatch.md
│       ├── 2026-06-23_England_Ghana_postmatch.md
│       ├── 2026-06-23_Norway_Senegal_postmatch.md
│       ├── 2026-06-23_Portugal_Uzbekistan_postmatch.md
│       ├── 2026-06-25_Matchday3_6Match_Postmatch.md
│       └── 20260626_June26_Batch_Postmatch.md
│
├── scripts/
│   ├── register_windows_tasks.ps1            # Windows计划任务注册
│   ├── run_checks.ps1                        # 系统检查脚本
│   └── start_dashboard.ps1                   # Dashboard启动
│
├── backend/
│   ├── requirements.txt
│   ├── alembic/                              # 数据库迁移
│   │   ├── env.py
│   │   └── versions/                         # 9个迁移脚本
│   │
│   ├── app/
│   │   ├── __init__.py
│   │   ├── config.py                         # 环境配置读取
│   │   ├── database.py                       # SQLAlchemy AsyncSession
│   │   ├── dependencies.py                   # FastAPI 依赖注入
│   │   ├── exceptions.py                     # 统一异常类
│   │   ├── logging.py                        # 日志配置
│   │   ├── main.py                           # FastAPI 应用入口
│   │   ├── rate_limit.py                     # 速率限制
│   │   ├── version.py                        # 单例版本号 V4.3.0-beta
│   │   │
│   │   ├── models/                           # SQLAlchemy ORM模型
│   │   │   ├── base.py                       # Declarative Base
│   │   │   ├── enums.py                      # 枚举类型
│   │   │   ├── match.py                      # 比赛 + 赛果
│   │   │   ├── team.py / team_alias.py       # 球队
│   │   │   ├── player.py                     # 球员
│   │   │   ├── prediction_run.py             # 预测运行记录
│   │   │   ├── prediction_snapshot.py        # 预测快照
│   │   │   ├── prediction_learning_log.py    # 学习日志
│   │   │   ├── postmatch_eval.py             # 赛后评估
│   │   │   ├── postmatch_signal_eval.py      # 信号评估
│   │   │   ├── motivation_event.py           # 战意事件
│   │   │   ├── news_article.py               # 新闻文章
│   │   │   ├── news_signal.py                # 新闻信号
│   │   │   ├── market_divergence_log.py      # 市场分歧日志
│   │   │   ├── signal_track_record.py        # 信号追踪
│   │   │   ├── context_performance_matrix.py # 上下文表现矩阵
│   │   │   ├── feedback.py                   # 用户反馈
│   │   │   ├── match_result_verification.py  # 赛果验证
│   │   │   ├── manual_event.py               # 手动事件
│   │   │   ├── ingest_run.py                 # 数据摄取运行
│   │   │   ├── source_registry.py            # 数据源注册
│   │   │   ├── content_article.py            # 内容文章
│   │   │   └── article_evidence.py           # 文章证据
│   │   │
│   │   ├── schemas/                          # Pydantic 请求/响应模型
│   │   ├── routers/                          # FastAPI 路由 (9个)
│   │   │   ├── health.py, matches.py, predictions.py, dashboard.py,
│   │   │   ├── signals.py, feedback.py, stats.py, admin.py, analysis.py
│   │   │
│   │   ├── services/                         # 核心业务逻辑 (40+ 文件)
│   │   │   ├── prediction_pipeline.py        # ★ 主预测引擎 (2189行)
│   │   │   ├── prediction_core.py            # 模型加载辅助 (417行)
│   │   │   ├── prediction_enhanced.py        # Dashboard兼容包装 (583行)
│   │   │   ├── prediction_orchestrator.py    # 异步编排层 (862行)
│   │   │   ├── dixon_coles.py                # Dixon-Coles模型 (734行)
│   │   │   ├── tabular_match_model.py        # Tabular Enhancer (597行)
│   │   │   ├── elo_ratings.py                # Elo评分 (314行)
│   │   │   ├── pi_ratings.py                 # Pi-Rating
│   │   │   ├── weibull_model.py              # Weibull Copula
│   │   │   ├── weights.py                    # 权重配置 (372行)
│   │   │   ├── calibration.py                # 同位素校准器
│   │   │   ├── market_calibrator.py          # 市场赔率校准 (537行)
│   │   │   ├── group_standings.py            # 积分榜服务 (347行)
│   │   │   ├── match_importance.py           # 战意分析 (582行)
│   │   │   ├── match_resolver.py             # 比赛匹配
│   │   │   ├── tournament_simulator.py       # 赛事模拟 (674行)
│   │   │   ├── learning_engine.py            # 学习引擎 (703行)
│   │   │   ├── postmatch.py                  # 赛后复盘
│   │   │   ├── evaluation_metrics.py         # 评估指标 (Brier/LogLoss/RPS)
│   │   │   ├── snapshot_service.py           # 快照服务 (402行)
│   │   │   ├── snapshot_store.py             # 快照存储 (335行)
│   │   │   ├── signal_adjuster.py            # 异步信号调整
│   │   │   ├── signal_adjuster_sync.py       # 同步信号调整
│   │   │   ├── context_adjuster.py           # 上下文调整
│   │   │   ├── fusion_graph.py               # 融合图
│   │   │   ├── prediction_result.py          # 预测结果数据类 (327行)
│   │   │   ├── prediction_timer.py           # 性能计时
│   │   │   ├── weather_service.py            # Open-Meteo天气
│   │   │   ├── team_resolver.py              # 球队名解析
│   │   │   ├── football_data_service.py      # 足球数据API (492行)
│   │   │   ├── openfootball_service.py       # OpenFootball数据
│   │   │   ├── result_verification.py        # 赛果验证 (351行)
│   │   │   ├── news_ingest_service.py        # 新闻摄取 (321行)
│   │   │   ├── article_generator.py          # 文章生成
│   │   │   ├── llm_service.py                # LLM调用 (413行)
│   │   │   ├── embedding_service.py          # 向量嵌入
│   │   │   ├── closed_loop_resolution.py     # 闭环解决
│   │   │   ├── output_policy.py              # 输出策略
│   │   │   ├── public_safety_filter.py       # 安全过滤
│   │   │   ├── run_quality.py                # 运行质量
│   │   │   ├── artifact_registry.py          # 模型工件注册
│   │   │   ├── model_cache.py                # 内存缓存
│   │   │   ├── model_cache_disk.py           # 磁盘缓存
│   │   │   ├── evaluation_sample.py          # 评估样本
│   │   │   │
│   │   │   ├── llm/                          # LLM子系统
│   │   │   │   ├── deepseek_client.py
│   │   │   │   ├── analysis_prompts.py
│   │   │   │   ├── signal_extraction.py
│   │   │   │   ├── schemas.py
│   │   │   │   └── prompts/extract_signal_v1.md
│   │   │   │
│   │   │   └── market/                       # 市场赔率子系统
│   │   │       ├── provider_base.py
│   │   │       ├── apifootball_com_provider.py (433行)
│   │   │       ├── sync_provider.py (326行)
│   │   │       ├── consensus.py
│   │   │       ├── consensus_save.py
│   │   │       ├── probability.py
│   │   │       └── schemas.py
│   │   │
│   │   ├── utils/                            # 工具函数
│   │   │   ├── hash.py, datetime.py, http.py,
│   │   │   ├── task_runs.py, text.py
│   │   │
│   │   └── workers/                          # Celery 异步任务
│   │       ├── celery_app.py
│   │       └── tasks.py (381行)
│   │
│   ├── artifacts/                            # 模型工件
│   │   ├── calibrator.json                   # 通用校准器
│   │   ├── calibrator_wc.json               # WC专用校准器 (69样本)
│   │   ├── model_registry.json               # 模型注册表
│   │   └── ratings/
│   │       ├── elo.json                       # Elo评分 (10KB)
│   │       └── pi.json                        # Pi评分 (10KB)
│   │
│   ├── data/
│   │   ├── local_stage2.db                   # ★ 主SQLite数据库 (~18MB)
│   │   ├── _pred_*.json                      # 11个预测输出JSON
│   │   ├── _manual_odds.json                 # 手动录入赔率
│   │   └── injuries.json                     # 伤停数据
│   │
│   ├── model_artifacts/dc_cache/             # DC/Enhancer 磁盘缓存
│   │   ├── dc_*.pkl (7个WC + 2个Friendly)
│   │   └── enhancer_*.pkl (7+2个)
│   │
│   ├── scripts/                              # CLI 脚本 (10个)
│   │   ├── predict_match_full.py             # ★ 全流水线CLI预测 (757行)
│   │   ├── run_postmatch_complete.py         # 标准7步复盘 (714行)
│   │   ├── auto_postmatch.py                 # 每日自动复盘
│   │   ├── postmatch_review.py               # 单场复盘审查
│   │   ├── backfill_postmatch_evals.py       # 历史数据回填 (756行)
│   │   ├── train_models.py                   # 模型训练 (518行)
│   │   ├── seed_wc26_schedule.py             # WC赛程种子 (482行)
│   │   ├── simulate_wc26.py                  # 赛事蒙特卡洛模拟 (390行)
│   │   ├── add_manual_event.py               # 手动事件注入 (323行)
│   │   ├── verify_env.py                     # 环境验证
│   │   └── _bootstrap_ci.py                  # Bootstrap CI (449行，几乎不调用)
│   │
│   ├── dashboard/                            # Streamlit Dashboard
│   │   ├── app.py                            # 主入口
│   │   ├── db.py                             # DB连接
│   │   ├── dashboard_config.py               # 配置
│   │   ├── home.py                           # 首页
│   │   ├── pages/                            # 9个页面
│   │   │   ├── 01_Overview.py
│   │   │   ├── 02_Match_Prediction.py        # 比赛预测 (477行)
│   │   │   ├── 03_Match_Context.py
│   │   │   ├── 04_WC26_Schedule.py
│   │   │   ├── 05_Teams_Facts.py
│   │   │   ├── 06_Database_Explorer.py
│   │   │   ├── 07_Tournament_Simulator.py
│   │   │   ├── 08_Creator_Mode.py
│   │   │   └── 09_Postmatch_Review.py
│   │   └── components/                       # UI组件
│   │       ├── creator_cards.py
│   │       ├── database_table.py
│   │       ├── fusion_graph_view.py
│   │       ├── metric_cards.py
│   │       ├── probability_charts.py
│   │       └── run_quality_panel.py
│   │
│   ├── static/
│   │   └── dashboard.html                    # 备选HTML Dashboard
│   │
│   ├── tests/                                # 196个测试
│   │   ├── conftest.py
│   │   ├── test_prediction_pipeline.py
│   │   ├── test_dashboard_prediction.py
│   │   ├── test_dashboard_db.py
│   │   ├── test_fusion_graph.py
│   │   ├── test_dixon_coles.py
│   │   ├── test_evaluation_metrics.py
│   │   ├── test_weights_config.py
│   │   ├── test_match_resolver.py
│   │   ├── test_market_provider_selection.py
│   │   ├── test_shin_formula.py
│   │   ├── test_asyncio_safety.py
│   │   ├── test_result_verification.py
│   │   ├── test_snapshot_store_contract.py
│   │   ├── test_closed_loop_resolution.py
│   │   ├── test_news_signal_validation.py
│   │   ├── test_wc26_closure.py
│   │   └── test_output_policy.py
│   │
│   └── docs/
│       └── POSTMATCH_SOP.md                  # 赛后复盘SOP
```

---

## 4. 数据库设计

### 4.1 数据库

**主库**: `backend/data/local_stage2.db` (SQLite, ~18MB)  
**备选**: PostgreSQL (通过环境变量激活，用于生产部署)

### 4.2 核心表

| 表名 | 行数 | 用途 |
|:---|:---:|:---|
| `matches` | ~17,000 | 历史比赛（含国家队+俱乐部） |
| `match_results` | ~16,749 | 比赛结果（比分、xG、射门等） |
| `teams` | ~300 | 球队 |
| `wc26_schedule` | 104 | WC26 赛程 |
| `wc26_groups` | 48 | 小组赛分组 |
| `wc26_group_standings` | 48 | 实时积分榜 |
| `prediction_runs` | 255 | 预测运行记录 |
| `prediction_snapshots` | ~100+ | 预测快照（含V3.8+格式） |
| `postmatch_eval` | 48 | 赛后评估 |
| `motivation_events` | 6+ | 战意事件 |
| `model_weight_config` | ~10 | 权重配置历史 |
| `signal_track_record` | ~20 | 信号准确率追踪 |
| `market_odds` | ~100 | 市场赔率 |
| `injuries` | ~10 | 伤停数据 |

### 4.3 match_id (关键设计)

比赛唯一标识用 MD5 hash：
```python
match_id = hashlib.md5(f"{HOME}|{AWAY}|{COMP}".encode()).hexdigest()[:32]
```

---

## 5. 核心预测流水线详解

### 5.1 流水线总览

```
预测输入: HOME, AWAY, COMPETITION, STAGE, IS_NEUTRAL
          │
  ┌───────┴───────────────────────────────────────────────────────┐
  │                       权重配置获取                              │
  │  get_weight_config(COMP, STAGE) → WeightConfig                │
  │  WC: dc=0.68 enh=0.32 elo=0.12 pi=0.17 wb=0.10 mkt=0.30     │
  └───────┬───────────────────────────────────────────────────────┘
          │
  ════════╪═══════════════════════════════════════════════════════
    Step  │  Component        │ Weight │ Description
  ════════╪═══════════════════╪════════╪══════════════════════════
     1    │  Dixon-Coles      │   —    │ Poisson GLM with team attack/defense params
     2    │  + Enhancer       │  dc=0.68│ Tabular ML model (XGBoost on match features)
    2.5   │  + Divergence     │   —    │ DC vs Enhancer divergence detection & guard
    2.7   │  + NegBin 5%      │  0.05  │ Negative Binomial overdispersion correction
    2.8   │  + Weibull        │  0.10  │ Weibull Copula (time-decay + head-to-head)
     3    │  + Elo            │  0.12  │ Elo rating system (kappa-Davidson)
     4    │  + Pi-Rating      │  0.17  │ Pi ball possession-based rating
    4.5   │  + Motivation     │   —    │ 战意因子 (WC MD3 only, match importance)
     5    │  + Market         │ ≤0.30  │ Vig-removed implied probabilities
     6    │  + Draw Floor     │  0.12  │ Enforce minimum 12% draw probability
     7    │  + Calibration    │   —    │ Isotonic regression (if calibrator fitted)
  ════════╪═══════════════════╪════════╪══════════════════════════
          │
          ▼
    最终输出: home_win_prob, draw_prob, away_win_prob
```

### 5.2 融合方式：顺序融合 (Sequential Fusion)

每次融合使用 `fuse_outcome_probabilities(base, new, base_weight)`：

```python
# base_weight=0.68 时:
# fused = base * 0.68 + new * 0.32
# 即: base 占 68% 的剩余概率空间

fused = DC
fused = fuse(fused, Enhancer, base_weight=dc)       # 68% DC, 32% Enhancer
fused = fuse(fused, NegBin,  base_weight=0.95)      # 95% prev, 5% NegBin
fused = fuse(fused, Weibull, base_weight=0.90)      # 90% prev, 10% Weibull
fused = fuse(fused, Elo,     base_weight=0.88)      # 88% prev, 12% Elo
fused = fuse(fused, Pi,      base_weight=0.83)      # 83% prev, 17% Pi
# Market: blended on top with divergence-adaptive weight (max 0.30, dynamic boost to 0.50)
# Draw Floor: enforced minimum 12%, deficit taken 70/30 from home/away
```

### 5.3 各组件详解

#### Dixon-Coles (DC)
- **模型**: Bivariate Poisson GLM
- **输入**: 历史比赛结果 + 球队攻防参数
- **训练**: 在 ~17,000 场历史比赛上训练
- **缓存**: `model_artifacts/dc_cache/dc_*.pkl`
- **特点**: 最稳定的基础模型，WC方向正确率 77%

#### TabularMatchEnhancer (Enhancer)
- **模型**: XGBoost on tabular match features
- **问题**: 系统性偏向下盘（weak side/away team），WC 23% 方向正确
- **抑制策略**: dc=0.68 → enhancer blend = 0.32 (effective weight ~23%)

#### Negative Binomial 5%
- **作用**: 修正 Poisson 独立性假设（WC 进球 Var/Mean=1.42）
- **权重**: 5% (边际增益 ~2%)
- **模式**: 仅用于三分类概率融合，不改变 xG

#### Weibull Copula
- **作用**: 时间衰减 + 对战历史建模
- **缓存**: 内存缓存避免重复拟合

#### Elo Rating
- **模型**: kappa-Davidson Elo 系统
- **评分文件**: `artifacts/ratings/elo.json`
- **WC kappa**: 0.02 → 0.07 (V4.1.3 提升平局预测)

#### Pi-Rating
- **模型**: 基于控球的球队评分
- **评分文件**: `artifacts/ratings/pi.json`
- **表现**: 19场 69% 方向正确，最佳非市场组件（权重从 0.12 → 0.17）

#### Motivation (战意因子)
- **触发条件**: WC 小组赛第3轮
- **分析维度**: 双方晋级形势、平局对双方的影响、轮换风险
- **输出**: 6种比赛类型标签 + 概率调整量
  - OFFENSIVE, OFFENSIVE_ASYMMETRIC, DEFENSIVE, DEFENSIVE_ASYMMETRIC, ANTAGONISTIC, UNIMPORTANT

#### Market Consensus (市场赔率)
- **来源**: apifootball.com / The Odds API / Web搜索回退
- **处理**: Shin (1993) 公式去除水分 (vig)
- **融合**: 最大 30%，模型-市场分歧 >15pp 时动态提升至 50%
- **表现**: 85% 方向正确，最可靠信号

#### Draw Floor 12%
- **逻辑**: WC 比赛 final draw 概率 < 12% 时强制提升
- **赤字分配**: 70% 从高概率方、30% 从低概率方

#### Calibration (校准)
- **模型**: Isotonic Regression (PAVA)
- **文件**: `calibrator_wc.json` (69样本, WC专用)
- **触发条件**: 市场数据存在时跳过（"市场 IS 校准"）

### 5.4 分歧保护系统

```
DC-Enhancer 分歧 > 20pp + 方向冲突
  → Enhancer 被覆盖，DC weight 保持不变

DC-Enhancer 分歧 > 20pp + 无方向冲突  
  → DC weight 自适应降低 (floor 0.30, shift = (max_div-20)*0.015)

Model-Market 分歧 > 15pp
  → Market weight 动态提升至 0.50 (divergence_boost)
```

---

## 6. 三条预测路径对比

这是项目当前 **最大的架构问题**。

### 6.1 路径对比

| 维度 | predict_match_full.py | PredictionPipeline.predict_match() | PredictionPipeline.predict_sync() |
|:---|:---|:---|:---|
| **文件** | `backend/scripts/` | `prediction_pipeline.py` | `prediction_pipeline.py` |
| **调用方** | CLI 终端手动执行 | Celery workers, admin API, snapshot | Dashboard (Streamlit), 脚本 |
| **代码量** | ~750行 | ~600行 | ~450行 |
| **同步/异步** | 同步 | 异步 (async/await) | 同步 |
| **DB依赖** | 无 (纯磁盘加载) | 需要 AsyncSession | 需要 SQLite 直连 |
| **Motivation** | ✅ | ✅ | ✅ |
| **Draw Floor** | ✅ | ✅ | ✅ |
| **Divergence Guard** | ✅ | ✅ | ✅ |
| **NegBin 5%** | ✅ | ❌ (缺失!) | ❌ (缺失!) |
| **Market Bootstrap** | ✅ | ✅ | ✅ |
| **Calibration** | ✅ (跳过) | ✅ | ✅ |
| **Bootstrap CI** | 可选 (--bootstrap) | ❌ | ❌ |
| **输出格式** | JSON (文件+stdout) | PredictionResult 对象 | dict |
| **DB写入** | prediction_runs + motivation_events | 无 (由调用方处理) | 无 |

### 6.2 问题

1. **同一逻辑维护3次** — 每次新增特性需要改 2-3 个地方
2. **predict_match_full.py CLI 和 prediction_pipeline.py 是两套独立代码** — 不是一套调用另一套
3. **NegBin 5% 只在 CLI 路径** — API/Dashboard 路径没有
4. **prediction_pipeline.py 膨胀到 2189 行** — 单文件过大

---

## 7. 调用链详解

### 7.1 CLI 路径

```
$ python scripts/predict_match_full.py "Home" "Away" "Competition"
          │
          ▼
  predict_match_full.py:main()
    ├─ _load_dc(), _load_enhancer(), _load_elo(), _load_pi()
    │  (prediction_core.py — 磁盘加载)
    ├─ dc.predict_match()
    ├─ enh.predict_match()
    ├─ fuse_outcome_probabilities(dc, enh, dc_weight)
    ├─ divergence_diagnostic()
    ├─ overdispersed_poisson_scoreline() → NegBin 5%
    ├─ weibull.predict() → fuse_weibull_probs()
    ├─ fuse_elo_probabilities()
    ├─ fuse_pi_probabilities()
    ├─ motivation analysis (match_importance.py)
    ├─ market consensus (if available)
    ├─ draw floor 12%
    ├─ calibration (skipped: market IS calibration)
    ├─ write prediction_runs DB → local_stage2.db
    ├─ write motivation_events DB → local_stage2.db
    └─ write _pred_{Home}_{Away}.json → backend/data/
```

### 7.2 Dashboard 路径

```
Streamlit Dashboard
  → prediction_enhanced.py → run_enhanced_prediction()
    → PredictionPipeline.from_artifacts(mode="full")
      → PredictionPipeline.predict_sync()
          (same pipeline steps, sync version, ~450 lines)
```

### 7.3 API / Worker 路径

```
FastAPI route (predictions.py or admin.py)
  → PredictionOrchestrator.predict()
    → PredictionPipeline.predict_match()  (async, ~600 lines)
```

---

## 8. 赛后复盘与自进化系统

### 8.1 复盘流程

```
比赛结束
  ↓
Step 1: 从可靠来源获取赛果（至少2个独立来源交叉验证）
  ↓
Step 2: 计算预测误差 (Brier, LogLoss, RPS, 方向)
  ↓
Step 3: 逐组件评估 (DC, Enhancer, Weibull, Elo, Pi, Market 各自的表现)
  ↓
Step 4: 自进化 — 计算边际 Brier 贡献，推荐权重调整
  ↓
Step 5: 人工批准权重调整
  ↓
Step 6: 更新 model_weight_config 表
  ↓
Step 7: 生成复盘报告 + memory 文件
```

### 8.2 复盘脚本对比

| 脚本 | 状态 | 适用场景 |
|:---|:---|:---|
| `run_postmatch_complete.py` | **半活跃** | 需要 PredictionSnapshot DB 记录（很多比赛没有） |
| `auto_postmatch.py` | 活跃 | 每日自动复盘（昨天完赛的比赛） |
| `postmatch_review.py` | 活跃 | 手动单场复盘审查 |
| `backfill_postmatch_evals.py` | 一次性 | 回填历史数据（已完成） |

### 8.3 评估指标

- **Brier Score**: `(p_home - actual_home)^2 + (p_draw - actual_draw)^2 + (p_away - actual_away)^2`
- **Log Loss**: `-sum(actual_i * log(p_i))`
- **RPS**: Ranked Probability Score
- **Direction**: 最高概率的方向是否与实际结果一致

---

## 9. Dashboard 与 Web 层

### 9.1 Dashboard (Streamlit)

启动: `powershell -File scripts/start_dashboard.ps1`

9个页面：
1. **Overview** — 系统总览
2. **Match_Prediction** — 比赛预测 (主页面)
3. **Match_Context** — 比赛上下文
4. **WC26_Schedule** — 赛程表
5. **Teams_Facts** — 球队资料
6. **Database_Explorer** — 数据库浏览器
7. **Tournament_Simulator** — 赛事模拟
8. **Creator_Mode** — AI内容生成
9. **Postmatch_Review** — 赛后复盘

### 9.2 FastAPI (后端API)

路由表：
- `GET /health` — 健康检查
- `GET /api/matches` — 比赛列表
- `POST /api/predictions` — 生成预测
- `GET /api/signals` — 信号列表
- `POST /api/feedback` — 用户反馈
- `GET /api/stats` — 统计
- `GET /api/admin/*` — 管理功能

---

## 10. 已知问题与架构缺陷

### 🔴 P0 — 严重架构问题

| # | 问题 | 影响 |
|:---:|:---|:---|
| **A1** | **三条独立预测流水线** — CLI (`predict_match_full.py` 757行)、API async (`prediction_pipeline.py predict_match()` ~600行)、Dashboard sync (`prediction_pipeline.py predict_sync()` ~450行) 各自实现了相同的 DC→Enhancer→Elo→Pi→Weibull→Market 逻辑 | 每次加功能要改3处，导致特性不一致（如NegBin5%只在CLI） |
| **A2** | **prediction_pipeline.py 过于臃肿** — 2189行单文件，包含预测引擎、数据获取、业务逻辑、输出格式化 | 可维护性差，测试困难 |
| **A3** | **predict_match_full.py 不走 PredictionPipeline** — CLI 脚本绕过了 pipeline 类，直接调用底层函数 | 架构不一致 |

### 🟡 P1 — 功能缺失 / 不一致

| # | 问题 | 状态 |
|:---:|:---|:---:|
| **B1** | **NegBin 5% 融合只在CLI** — `prediction_pipeline.py` 的 async 和 sync 路径都没有 | 未修复 |
| **B3** | **xG 校准因子 1.35 只作用于比分展示** — 不作用于 DC 融合中的 xG 输入 | 边际增益 <2% |
| **B8** | **run_postmatch_complete.py 不写复盘报告文件** — 只写DB，不生成 markdown | 已有替代脚本 |

### 🟢 P2 — 数据 / 配置问题

| # | 问题 |
|:---:|:---|
| **C1** | **data/ 目录分散** — `./data/` (根级) + `./backend/data/` 两个数据目录 |
| **C2** | **`run_postmatch_complete.py` 依赖 PredictionSnapshot** — 很多比赛只有 JSON 文件，没有 DB 快照，导致脚本无法工作 |
| **C3** | **`prediction_enhanced.py` (583行) 是多余的包装层** — 只把 PredictionPipeline 输出转成另一种格式，增加一层间接调用 |
| **C4** | **Elo kappa 每次预测都查 SQLite** — 已有缓存修复但仍增加了不必要的 DB 查询 |
| **C5** | **README 版本号仍是 V4.2.2** — 实际已是 V4.3.0-beta |

### 性能问题

| # | 问题 |
|:---:|:---|
| **D1** | 预测流水线 ~1-3 秒（全量模式），主要耗时在模型加载 |
| **D2** | Weibull 每次预测都需要 `.fit()`（已有缓存修复） |
| **D3** | `prediction_pipeline.py` 中大量的 try/except 嵌套影响可读性 |

---

## 11. 解决方案

### 方案总览：提取共享引擎 + 收敛入口

```
                    ┌──────────────────────────┐
                    │   PredictionEngine        │
                    │   (core/engine.py)        │
                    │                           │
                    │   唯一的流水线实现          │
                    │   ~400行                   │
                    │                           │
                    │   run(home, away, comp,   │
                    │       stage, is_neutral)  │
                    │   → PipelineOutput        │
                    └─────┬────────┬───────────┘
                          │        │
              ┌───────────┘        └─────────┐
              ▼                              ▼
    ┌─────────────────┐          ┌──────────────────┐
    │  CLI Adapter    │          │  API Adapter     │
    │  (scripts/      │          │  (adapters/      │
    │   predict.py)   │          │   api_adapter.py)│
    │                 │          │                  │
    │  参数解析       │          │  AsyncSession     │
    │  JSON输出       │          │  PredictionResult │
    │  DB写入         │          │  HTTP响应         │
    │  ~50行          │          │  ~150行           │
    └─────────────────┘          └──────────────────┘
                                          │
                              ┌───────────┘
                              ▼
                    ┌──────────────────┐
                    │ Dashboard Adapter│
                    │ (adapters/       │
                    │  dashboard_      │
                    │  adapter.py)     │
                    │                  │
                    │  Streamlit sync  │
                    │  ~100行           │
                    └──────────────────┘
```

### 具体修复计划

#### Fix 1: 创建 PredictionEngine (P0)

**新文件**: `backend/app/core/engine.py` (~400行)

```python
@dataclass
class PipelineStep:
    name: str
    prob: dict[str, float]  # {home_win_prob, draw_prob, away_win_prob}
    brier: float | None
    direction: str  # "H"/"D"/"A"

@dataclass  
class PipelineOutput:
    final: PipelineStep
    steps: list[PipelineStep]
    xg: dict[str, float]
    divergence: dict | None
    motivation: dict | None
    market: dict | None
    provenance: dict

class PredictionEngine:
    """Single source of truth for the prediction pipeline."""
    
    def __init__(self, config: WeightConfig, *, load_models: bool = True):
        self.config = config
        if load_models:
            self._load_models()
    
    def run(
        self,
        home: str, away: str, competition: str,
        *,
        stage: str = "",
        is_neutral: bool = False,
        market_data: dict | None = None,
        standings: dict | None = None,
        weather: dict | None = None,
    ) -> PipelineOutput:
        """Execute the full DC→Enhancer→NegBin→Weibull→Elo→Pi→Motivation→Market→DrawFloor pipeline."""
        ...
```

#### Fix 2: 让 predict_match_full.py CLI 通过 Engine 调用

**修改文件**: `backend/scripts/predict_match_full.py`

改造为 thin wrapper (~80行):
```python
def main():
    args = parse_args()
    engine = PredictionEngine(get_weight_config(args.comp, args.stage))
    output = engine.run(args.home, args.away, args.comp, 
                        stage=args.stage, is_neutral=True)
    write_json(output)
    write_db(output)
```

#### Fix 3: 收敛 prediction_pipeline.py

**修改文件**: `backend/app/services/prediction_pipeline.py`

`predict_match()` 和 `predict_sync()` 方法都改为委托给 `PredictionEngine.run()`:

```python
async def predict_match(self, home, away, comp, **kwargs):
    engine = PredictionEngine(self._get_config(comp, kwargs.get("stage", "")))
    # 只处理 async IO: DB查询、市场数据获取等
    market_data = await self._fetch_market(home, away)
    standings = await self._fetch_standings(home, away, comp)
    output = engine.run(home, away, comp, market_data=market_data, standings=standings)
    return PredictionResult.from_output(output)
```

这将从 2189 行缩减到 ~900 行。

#### Fix 4: 删除 prediction_enhanced.py 的流水线逻辑

**修改文件**: `backend/app/services/prediction_enhanced.py`

移除重复的流水线代码，改为调用 Engine：
- 583 行 → ~200 行（只保留 LLM 和 Weather 增强的逻辑）

#### Fix 5: 数据目录统一

```
移动: ./data/team_tournament_status.json → ./backend/data/team_tournament_status.json
更新: output_policy.py, dashboard_config.py, tournament_simulator.py 中的路径引用
```

#### Fix 6: README 版本号更新

```
README.md: V4.2.2-beta → V4.3.0-beta
```

---

## 12. 迁移路线图

### Phase 1: 最小破坏性清理（已完成 ✅）

- [x] 删除死文件 (nginx/, .auth/, feature_flags.yaml, batch_postmatch_june26.py)
- [x] 提交: `e19bd0b`

### Phase 2: 提取核心引擎 (1-2天)

1. 创建 `backend/app/core/engine.py` — 提取所有流水线步骤
2. 为 `PredictionEngine.run()` 写单元测试
3. 让 `prediction_pipeline.py` 的 `predict_sync()` 通过 engine 调用
4. 验证: 196 个测试全部通过

### Phase 3: CLI 收敛 (1天)

1. 改造 `predict_match_full.py` 通过 engine 调用
2. 验证: 新预测与旧预测输出完全一致

### Phase 4: 清理冗余 (1天)

1. 从 `prediction_pipeline.py` 移除重复代码
2. 简化 `prediction_enhanced.py`
3. 数据目录统一

### Phase 5: 标记淘汰 (后续)

1. 标记 `prediction_orchestrator.py` 中与 engine 重复的代码为 deprecated
2. 标记 `_bootstrap_ci.py` 为 deprecated（保持可用但不维护）
3. 评估是否需要保留 `prediction_enhanced.py` 或完全合并到 pipeline

---

## 附录 A: 关键常量

```python
# predict_match_full.py
WC_XG_CALIBRATION_FACTOR = 1.35    # WC xG 校准因子
NEGBIN_R = 3.5                      # Negative Binomial overdispersion
NEGBIN_FUSION_WEIGHT = 0.05         # NegBin 融合权重

# prediction_pipeline.py  
DEFAULT_COMPETITION_WEIGHT = 0.9
WORLD_CUP_COMPETITION_WEIGHT = 1.5
FRIENDLY_COMPETITION_WEIGHT = 0.5
DRAW_FLOOR = 0.12                   # 平局最低概率

# weights.py (WORLD_CUP_V4.3.0)
dc = 0.68
enhancer = 0.32   # = 1-dc
elo = 0.12
pi = 0.17
weibull = 0.10
market_max = 0.30
```

## 附录 B: 环境变量

```env
# 必需
ADMIN_TOKEN=<随机生成的安全令牌>

# 市场数据
APIFOOTBALL_COM_KEY=<apifootball.com API key>
ODDS_API_KEY=<The Odds API key>

# LLM (内容生成，非预测必需)
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<DeepSeek API key>
LLM_MODEL=deepseek-v4-pro

# 数据库 (可选，默认 SQLite)
POSTGRES_URL=postgresql+asyncpg://user:pass@host/db
REDIS_URL=redis://host:6379/0

# 运行时
MODEL_ARTIFACT_DIR=backend/model_artifacts
```

## 附录 C: 自进化规则

1. 只有测试集（不是训练集）的比赛才能触发权重更新
2. 权重调整基于边际 Brier 贡献，不是方向正确率
3. 单场比赛不会触发超过 5% 的权重变化（防过拟合）
4. 所有调整都是人工批准后合入
5. 组件权重总和始终为 1.0（顺序融合）

---

*文档版本: 1.0 | 生成日期: 2026-06-26 | 关联 commit: e19bd0b*
