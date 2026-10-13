# Daily Stock Analysis (DSA)

> A股智能分析系统，支持多数据源故障切换、LLM 驱动分析报告、多 Agent 协作、选股流水线、持仓管理。

## 开发规则

**→ 参见 [`docs/AGENTS.md`](docs/AGENTS.md)（CLAUDE.md 为其镜像）**

### GOT 运行规范

- **影子模式输出总结**：当 GOT 运行包含影子模式内容（如 4.1 超限减仓 `sell_source_shadow`、4b 市场结构、3b 分歧摘要、T1/T1-H 审查层）时，运行完成后必须在终端输出总结，包括：
  - Phase 1 各节点核心结论（大盘/板块/资金/消息/情绪/聚合）
  - 影子模式输出内容（`reasoning.md` 末尾「🧪 影子模式观测」段承载五条线：4b/3b/4.1/T1/T1-H；A6 glue 已转正，其关系/强度/家族统计改在 Node 6 正文「🧬 A6 语义粘合（已转正）」子块展示）
  - Phase 2 交易建议（持仓分析/候选股/操作建议）
  - 关键风险提示和主线续航评估
- **长时间运行命令必须加 `timeout: 0`**：执行 `python main.py --got`、`python main.py --screener` 等命令时，必须在 bash 工具调用中设置 `timeout: 0` 参数，禁止使用默认超时。

---

## 项目架构

```
┌─────────────────────────────────────────────────────────────────┐
│  入口层                                                          │
│  main.py / CLI / FastAPI / Bot / Web UI / Desktop               │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  Agent 层（个股分析）                                            │
│  TechnicalAgent → IntelAgent → [RiskAgent] → DecisionAgent      │
│  src/agent/                                                      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  GOT 层（市场分析）                                              │
│  Phase 1: GOT 七节点（大盘→板块→资金‖消息‖情绪 → 聚合）         │
│  Phase 2: 统一决策节点（风险评估→统一打分→组合优化→埋伏退出）    │
│  催化剂系统: 日历构建(D1-D4守卫/T5对齐/T2校验) + P5重排 + P6兑现 │
│  跨日记忆 v2.1: persistence.py 归档读取 + 跨节点时序共振         │
│  生产监控: monitoring.json 运行时指标                            │
│  src/got/                                                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  服务层                                                          │
│  选股流水线 / 板块轮动 / 技术指标 / 舆情 / 持仓 / 通知           │
│  src/services/                                                   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  数据层                                                          │
│  DataFetcherManager（11 个数据源策略模式 + 自动故障切换）         │
│  MootdxFetcher(P0 TCP) + EfinanceFetcher(P0) + AkshareFetcher(P1)│
│  data_provider/ + src/storage.py（SQLite 30+ 表）               │
│  em_rate_limiter.py（东财 HTTP 统一限流）                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
daily_stock_analysis/
├── main.py                      # 主入口
├── src/
│   ├── agent/                   # Agent 框架（个股分析）
│   │   ├── agents/              # Technical/Intel/Risk/Decision/Portfolio Agent
│   │   ├── tools/               # 22 个现有工具（纯规则）
│   │   ├── skills/              # 技能框架
│   │   ├── orchestrator.py      # Agent 编排器
│   │   ├── llm_adapter.py       # LLM 调用封装（litellm 多 Provider）
│   │   └── memory.py            # Agent 记忆 + 置信度校准
│   │
│   ├── got/                     # GOT 架构（市场分析）
│   │   ├── nodes/               # 7 个 GOT 节点（Phase 1: 6 个 + Phase 2: 1 个）
│   │   │   ├── market_analysis.py    # Node 1: 大盘（4维技术评分/多时间框架/跨指数一致性/波动率仓位）
│   │   │   ├── sector_analysis.py    # Node 2: 板块（4维百分位/RRG状态机/主线家族/6维埋伏评分）
│   │   │   ├── capital_analysis.py   # Node 3: 资金面（3维：板块流/DDE/北向永久豁免/降级机制）
│   │   │   ├── news_analysis.py      # Node 4: 消息面（7源推送/4次LLM/事件持久化/原始新闻跨日检测）
│   │   │   ├── sentiment_analysis.py # Node 5: 情绪面（6维置信度加权/反身性/恐贪指数/时序动量）
│   │   │   ├── aggregator.py         # Node 6: 聚合（A6 glue 直读·规则评分模式已退役/regime 状态机迟滞豁免/T3仲裁/埋伏通道/跨节点共振/分歧摘要影子）
│   │   │   └── portfolio_decision.py # Node 7: 统一决策（Phase 2，5步流水线+Step 1.5超限强制减仓+埋伏退出+T1-H持仓审查）
│   │   ├── catalyst_calendar.py   # 催化剂日历（D1回声/D2周末/D3过期/D4置信守卫/T5对齐/T2校验）
│   │   ├── catalyst_config.py     # 催化剂配置（13组全球→A股映射/影子模式开关/A6与§15审查层开关/可调参数集中管理）
│   │   ├── catalyst_realization.py # P6: 催化兑现追踪（A1三级解析/PEAD多窗口/故事寿命/resolution三态）
│   │   ├── index_registry.py      # 指数注册表 + 身份解析（裸码→股票/带后缀→指数，消除 000001 双身份歧义）
│   │   ├── intelligence_service.py # 情报服务
│   │   ├── market_theme.py      # 市场结构主题上下文（Step 2：mootdx 板块排名→MarketThemeContext，影子观测）
│   │   ├── disagreement.py      # 分歧摘要（Phase 3b：11 类冲突分类学 + RiskOverridePlan，影子观测）
│   │   ├── glue_pipeline.py     # C-P13 阶段1 事件→板块接地式召回-验证管线（L1 embedding 检索→L2 受约束生成→L3 CoVe 验证；仅 --got-replay + GLUE_PIPELINE_ENABLED，生产休眠）
│   │   ├── family_keywords.py     # 主线家族关键词（受控词表 12+1，glue 家族归属）
│   │   ├── board_aliases.py       # 板块别名表
│   │   ├── schemas.py           # 节点输入输出 Pydantic Schema（~45 模型）
│   │   ├── orchestrator.py      # GOT 编排器（并行/串行调度/use_mock主控）
│   │   ├── indicators.py        # 纯规则指标函数（ADX/MACD/A-D线/截面百分位/RRG）
│   │   ├── persistence.py       # 4存储区（EMA基线/跨日ctx/时序数据/生产监控）
│   │   ├── archive.py           # 每日历史档 + 跨日记忆读取（data/got_daily/YYYYMMDD/）
│   │   └── news_feeds.py        # Node 4 新闻采集（7源推送型：新浪/CLS/华尔街见闻/东财/NewsNow/同花顺/东方财富）
│   │
│   ├── services/                # 核心服务
│   │   ├── technical_indicator.py    # 短线技术指标（ADX/MACD/RSI/布林带/均线系统）
│   │   ├── sector_rotation.py        # 板块轮动分析
│   │   ├── forum_sentiment.py        # 舆情分析（雪球/东财千股千评）
│   │   ├── stock_screener.py         # 选股流水线（GOT 模式：DDE 初筛 + 5轮漏斗 + P5催化重排 + T1 Agent终审）
│   │   ├── ths_screener.py           # 同花顺问财
│   │   ├── portfolio_service.py      # 持仓管理
│   │   ├── risk_assessment.py        # 风险评估
│   │   ├── strategy_filter.py        # YAML 策略筛选
│   │   ├── dde_fetcher.py            # DDE 大单净量
│   │   ├── board_name_resolver.py    # 板块名解析（TDX→THS 四层：清洗/别名表/模糊匹配/kind区分）
│   │   ├── ths_hot_list.py           # 同花顺热榜（4端点：热门股/板块/话题/期货）
│   │   ├── portfolio_ocr_import.py   # 持仓OCR导入（券商截图→PaddleOCR→交易事件）
│   │   └── ...
│   │
│   ├── core/                    # 核心引擎
│   │   ├── trading_calendar.py       # 交易日历（cn/hk/us）
│   │   ├── pipeline.py               # 分析流水线编排
│   │   └── config_manager.py         # 配置管理
│   │
│   ├── schemas/                 # Pydantic Schema
│   │   ├── report_schema.py          # LLM 输出验证
│   │   ├── analysis_context_pack.py  # 输入上下文（DataQuality 字段追踪）
│   │   └── decision_action.py        # 决策动作分类
│   │
│   ├── repositories/            # 数据访问层
│   ├── storage.py               # SQLAlchemy ORM（30+ 表）
│   ├── notification_sender/     # 多渠道通知
│   ├── llm/                     # LLM 配置
│   └── patches/                 # 反爬补丁
│
├── data_provider/               # 数据获取层（策略模式，仓库根目录）
│   ├── base.py                       # DataFetcherManager（故障切换+熔断器）
│   ├── mootdx_fetcher.py             # 通达信 TCP P0（不封 IP，验活主站池+故障转移）
│   ├── em_rate_limiter.py            # 东财 HTTP 统一限流（1s间隔+抖动+重试+连接池）
│   ├── efinance_fetcher.py           # 东财 HTTP P0
│   ├── tencent_fetcher.py            # 腾讯免费指数 K 线 P1（仅指数可用，个股 fallback 排除）
│   ├── akshare_fetcher.py            # akshare P1（多源降级）
│   ├── tushare_fetcher.py            # Tushare Pro（P0 with Token）
│   └── ...                           # 另含 baostock/longbridge/yfinance/finnhub 等
│
├── data/                        # 数据文件
│   ├── stock_analysis.db             # SQLite 数据库（30+ 表）
│   ├── aggregator_contexts/          # GOT 上下文持久化（EMA基线/跨日ctx）
│   ├── got_daily/YYYYMMDD/           # 每日历史档（6节点JSON+reasoning+meta+agent_glue/shadow_t1/shadow_t1h）
│   └── got_timeseries/               # 时序数据（monitoring.json）
│
├── strategies/custom/           # 自定义选股策略（YAML）
├── tests/                       # 224 个测试文件
└── docs/                        # 文档（2026-09-17 三区重排，导航见 docs/INDEX.md）
    ├── AGENTS.md                     # AI 协作规则（真源）
    ├── CLAUDE.md                     # 重定向页（历史正文已归档）
    ├── CHANGELOG.md                  # 变更日志（[Unreleased] 分「本地工作/上游同步」两段）
    ├── guide/                        # 旧项目（非 GOT）基底用户文档 28 篇
    ├── got/                          # GOT 现行：README（导航）/ backlog（待实施）/ verification（待验证）
    │   ├── architecture.md           # GOT 架构设计文档（v1.0，2026-08-11，原根目录 agent_cot_design.md）
    │   ├── plans/                    # 待实施计划（埋伏分支拆分 / 选股胜率优化）
    │   └── archive/                  # GOT 历史文档（0902 审核 / 0903 主方案 / 0911 四层等）
    └── archive/plans/                # 非 GOT 已完结计划（选股设计 / 持仓整合 / 云部署 / 选股开发日志）
```

---

## GOT 架构概览

> **详细设计文档**：[`docs/got/architecture.md`](docs/got/architecture.md)（v1.0，2026-08-11）  
> 本文档保留高层架构速览 + 数据流转全图 + 编排流程 + 持久化层。节点级详细设计（评分维度、阈值、LLM 调用、Schema 字段）见设计文档。

### 核心理念

前半段 GOT（并行+聚合），后半段 CoT（线性流水线），外围催化剂系统（前向+回溯）

```
                          ┌─────────────────────────────────────────────────┐
                          │              数据源层                             │
                          │  指数K线 · 板块K线 · 资金流 · 7源新闻 · 论坛 · 热榜│
                          └────────────────────┬────────────────────────────┘
                                               │
                  ┌────────────────────────────┼────────────────────────────┐
                  │                            │                            │
     ┌────────────┴────────────┐  ┌────────────┴────────────┐              │
     │  技术分析路（串行）      │  │  独立数据路（并行）      │              │
     │                         │  │                         │              │
     │  ┌─────────┐            │  │  ┌─────────┐ ┌────────┐│              │
     │  │ Node 1  │←─ 5指数   │  │  │ Node 4  │ │ Node 5 ││              │
     │  │ 大盘    │   250d K线 │  │  │ 消息面  │ │ 情绪面 ││              │
     │  │         │   + breadth│  │  │ 7源推送 │ │ 6维评分││              │
     │  └────┬────┘            │  │  │ 3次LLM  │ │ 恐贪指 ││              │
     │       │ MarketContext   │  │  └────┬────┘ └───┬────┘│              │
     │       ▼                 │  │       │NewsCtx   │SentimentCtx        │
     │  ┌─────────┐            │  │       │          │      │              │
     │  │ Node 2  │←─ attack_ │  │       ▼          │      │              │
     │  │ 板块    │   power   │  │  ┌──────────────┐│      │              │
     │  │         │           │  │  │CatalystCal   ││      │              │
     │  │ 4维百分位│           │  │  │Builder       ││      │              │
     │  │ RRG状态机│           │  │  │(D1-D4守卫+  ││      │              │
     │  │ 6维埋伏 │           │  │  │ T5对齐+T2校验││      │              │
     │  │ 埋伏检测 │           │  │  └──────┬───────┘│      │              │
     │  └────┬────┘            │  │         │        │      │              │
     │       │ SectorContext   │  │         ▼        │      │              │
     │       ▼                 │  │         ▼        │      │              │
     │  ┌─────────┐            │  │  catalyst_calendar│  │              │
     │  │ Node 3  │←─ mainline│  │  嵌入 NewsContext │  │              │
     │  │ 资金面  │   名称    │  │                         │              │
     │  │         │           │  └─────────────────────────┘              │
     │  │ 3维资金  │           │                                           │
     │  │ 降级机制 │           │                                           │
     │  └────┬────┘            │                                           │
     │       │ CapitalContext  │                                           │
     └───────┴─────────────────┘                                           │
                  │              │            │            │                │
                  ▼              ▼            ▼            ▼                │
            MarketCtx     SectorCtx    CapitalCtx   NewsCtx   SentimentCtx │
                  └──────────────┬──────────────────────┘                │
                                 ▼                                        │
                    ┌──────────────────────────┐                          │
                    │     Node 6: 聚合          │                          │
                    │                          │                          │
                    │  L0: MarketRegime状态机    │                          │
                    │   (regime+dwell_days+     │                          │
                    │    迟滞豁免pinned_days+   │                          │
                    │    确认事件transition_events)│                        │
                    │                          │                          │
                    │  L1: 板块 mini-GOT        │                          │
                    │   (4维交叉验证+A5匹配     │                          │
                    │    +A6 glue 直读·规则模式已退役)│                    │
                    │                          │                          │
                    │  L1': 埋伏通道 (P3)       │◄── catalyst_calendar    │
                    │   (6维评分+B公式排序      │                          │
                    │    +蓄水池+跨节点共振)     │                          │
                    │                          │                          │
                    │  L2: 组合级决策           │                          │
                    │   (T3矛盾仲裁+仓位日限幅) │                          │
                    └────────────┬─────────────┘                          │
                                 │ UnifiedContext                         │
                                 │ (market_regime+cross_node_signals      │
                                 │  +confirmed_sectors+ambush_pool        │
                                 │  +ambush_reservoir)                    │
                                 ▼                                        │
                    ┌──────────────────────────┐                          │
                    │   Node 7: 统一决策 (Phase 2)                        │
                    │                          │                          │
                    │  Step 1: 风险评估         │                          │
                    │   (仓位上限/止损/板块限)   │                          │
                    │  Step 1.5: 组合层超限强制  │                          │
                    │   减仓（三态开关·默认影子） │                          │
                    │                          │                          │
                    │  Step 2: 统一打分         │                          │
                    │   (持仓+候选 5维评分)      │                          │
                    │   + P5: 催化剂关联度重排   │◄── catalyst_calendar    │
                    │   + P4: 埋伏退出信号       │                          │
                    │                          │                          │
                    │  Step 3: 组合优化         │                          │
                    │   (卖→预算→买 Top 3)      │                          │
                    │                          │                          │
                    │  Step 4: 轮动+拥挤检测    │                          │
                    │                          │                          │
                    │  Step 5: LLM 精炼        │                          │
                    └────────────┬─────────────┘                          │
                                 │                                        │
                                 ▼                                        │
                        PortfolioDecision                                 │
                    (trades + ambush_exit_signals                         │
                     + reasoning + warnings)                              │
                                                                          │
                    ┌──────────────────────────┐                          │
                    │   P6: 催化兑现追踪        │                          │
                    │   (PEAD多窗口验证:        │                          │
                    │    预期 vs 实际收益)       │                          │
                    │   (故事寿命+方向正确性)    │                          │
                    │   → CatalystRealization   │                          │
                    │     Record (SQLite)       │                          │
                    └──────────────────────────┘                          │
```

### 编排流程

`GOTOrchestrator.run()` 入口，`use_mock` 主控开关，`asyncio.gather` 分组并行：

```
run(use_mock=False)
 ├─ asyncio.gather(
 │    tech_path:  Node 1 → Node 2 → Node 3     （串行，后者消费前者输出）
 │    data_path:  Node 4 ‖ Node 5               （并行，独立采集）
 │  )
 ├─ Node 6: 聚合（消费全部 5 个 context + 跨日记忆 + 催化剂日历）
 ├─ [仅生产模式] archive_daily_run()（Phase 1 归档 → data/got_daily/YYYYMMDD/）
 ├─ [仅生产模式] Phase 2: PortfolioDecision（消费 6 个 context）
 ├─ [仅生产模式] archive_phase2()（Phase 2 归档）
 └─ [仅生产模式] CatalystRealizationTracker.check_and_record_realization()（P6）
```

**催化剂反馈循环**：Node 4 事件 → `CatalystCalendarBuilder`（D1回声守卫+D2周末守卫+T5语义对齐+T2叙事校验）→ `CatalystCalendar` → 嵌入 `NewsContext.catalyst_calendar` → Node 6 埋伏通道 + Phase 2 选股重排（P5）→ P6 兑现追踪（PEAD多窗口验证）

**影子观测旁挂**（2026-09-28 排期轮批 5，默认开·零决策改动，转正归负责人）：`GOT_MARKET_THEME_SHADOW` 市场结构落归档「🧭」段；`GOT_DISAGREEMENT_SHADOW` 分歧摘要落 `conflict_summary_shadow`；`GOT_SELL_SOURCE_SHADOW` 超限减仓检测落 `sell_source_shadow`；`funnel_stats` 漏斗归零率留痕；`GOT_AMBUSH_SPLIT` 埋伏道拆分（ENABLED=1 抽道+四闸门+trace 全开；action gate lift 仍 ACTIVE 门控默认影子，bear 闸埋伏道豁免已于 2026-10-13 转正无条件生效）

### 节点 Schema 数据流

| 节点 | 输入 | 输出 Schema | 关键输出字段 | 持久化 |
|------|------|-------------|-------------|--------|
| **Node 1** 大盘 | 无（直取 5 指数 250d K线 + breadth + T-0 行情） | `MarketContext` | `tech_growth.score/trend`, `market_state.attack_power/regime/crash_override`, `realized_volatility`, `vol_adjusted_position`, `regime_scale`, `position_limit`, `confidence` | — |
| **Node 2** 板块 | `MarketContext`（`attack_power` → 主线数量限制, `today_change` → 弱市检测） | `SectorContext` | `active_mainline[]`, `ambush[]`, `near_miss_ambush[]`, `cooling_down[]`, `industry_top[]`, `theme_families[]`, `strength_lookup{}` | `node2_sector_trends`（RRG 状态机+6态Markov） |
| **Node 3** 资金面 | `MarketContext`（trend）, `SectorContext`（主线名称 → 匹配 bonus） | `CapitalContext` | `institutional_score`, `evidence.sector_flow/dde/northbound(永久豁免)`, `status`, `confidence_multiplier` | — |
| **Node 4** 消息面 | 无（7 源推送采集） | `NewsContext` | `major_events[]`（含 `lifecycle_state`）, `catalyst_calendar`, `impact_score` | `node4_events`（事件生命周期）, `catalyst_events`（日历 UPSERT）, `node4_raw_news`（原始新闻跨日检测） |
| **Node 5** 情绪面 | 无（6 源并行采集） | `SentimentContext` | `sentiment_score`, `sentiment_stage`（5 阶段）, `stage_state`, `persistence_regime`, `fear_greed_index`, `sentiment_dispersion`, `sentiment_momentum` | — |
| **Node 6** 聚合 | 全部 5 个 context + 催化剂日历 + 跨日记忆 | `UnifiedContext` | `market_regime`(dwell_days/pinned_days/transition_events/transition_count), `cross_node_signals[]`, `confirmed_sectors[]`（`SectorIntel`）, `ambush_pool[]`, `ambush_reservoir[]`, `contradictions[]`（含T3仲裁字段）, `glue_consumed`/`glue_status`（A6 接线归档）, `conflict_summary_shadow`/`decision_path_hint`（Phase 3b 分歧摘要） | `persistence.py` → `ema_baseline.json` + `previous_context_*.json` + `timeseries/` + `monitoring.json`；`agent_glue.json`（glue 产物） |
| **Node 7** Phase 2 | `UnifiedContext` + 全部 5 个 context | `PortfolioDecision` | `risk_assessment`（含 `raw_position_ratio` pre-clip 观测）, `unified_scoring[]`, `allocation[]`, `ambush_exit_signals[]`, `sell_source_shadow`/`risk_override_shadow`（批 4.1/3b 影子观测）, `ambush_lane_shadow`/`ambush_lane_trace`（埋伏道拆分产物）；**埋伏道 Node 7 接线点**：action 判定抬 priority≥55（`GOT_AMBUSH_SPLIT_ACTIVE` 门控，默认影子）+ bear gate 豁免扩展（2026-10-13 转正·无条件，不随 ACTIVE） | `ambush_position_snapshots`（买入/退出快照）；`shadow_t1.json` / `shadow_t1h.json`（审查影子产物） |

### 持久化层总览

**SQLite 表（`data/stock_analysis.db`）**：

| 表名 | 写入方 | 用途 |
|------|--------|------|
| `node2_sector_trends` | Node 2 | 板块轨迹追踪（6 态状态机 + RRG EMA + 生命周期 + 强度轨迹 JSON） |
| ~~`sector_frames`~~ | ~~Node 2~~ | **已废除(2026-08-31)**，职责收编到状态机 `exit_pressure_days` |
| `node4_events` | Node 4 | 事件生命周期（emerging→fermenting→peak→fading→archived, 7 日自动归档） |
| `node4_raw_news` | Node 4 | 原始新闻持久化（title 归一化 + Jaccard 去重 + 跨日发酵检测） |
| `catalyst_events` | CatalystCalendar | 催化剂日历事件（event_id UPSERT, 14 日窗口） |
| `catalyst_refresh` | CatalystCalendar | 日历刷新节奏（单行表, 全量 5 日 / 增量其余） |
| `ambush_position_snapshots` | Phase 2 (P4) | 埋伏仓位快照（买入价 + 退出信号 + 退出价 + is_open） |
| `catalyst_realizations` | P6 | 催化兑现归档（预期 vs 实际收益 + 方向正确性 + 故事寿命 + resolution三态） |

**文件持久化**：

| 路径 | 写入方 | 内容 |
|------|--------|------|
| `data/got_daily/YYYYMMDD/` | `archive.py` | 6 个节点 JSON + `reasoning.md`（全节点 LLM 推理汇总；含 Node 6 正文「🧬 A6 语义粘合（已转正）」子块；末尾「🧪 影子模式观测」段承载五条线 4b/3b/4.1/T1/T1-H；🧭 市场结构段仅 4b 转正后升格主正文，影子态收编入 🧪）+ `meta.json`（含 `funnel_stats`） + `portfolio_decision.json` + `trading_recommendations.md` + `agent_glue.json` / `shadow_t1.json` / `shadow_t1h.json`（影子/审查产物） |
| `data/aggregator_contexts/previous_context_YYYYMMDD.json` | `persistence.py` | Node 6 跨日 UnifiedContext（时间一致性校验基准 + 跨日记忆读取） |
| `data/aggregator_contexts/ema_baseline.json` | `persistence.py` | EMA 基线（α=0.3, regime_score + 滚动标准差, 冷启动 5 日预热） |
| `data/got_timeseries/monitoring.json` | `persistence.py` | 生产监控指标（运行耗时/数据质量/LLM调用次数/降级事件） |

### 关键设计决策速览

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据源优先级 | mootdx TCP P0 → efinance HTTP P0 → akshare P1 | TCP 不受 HTTP 反爬影响 |
| 评分归一化 | 截面百分位排名（Node 2） | 自适应牛熊市，避免 clip 硬阈值 |
| 矛盾检测 | 规则决策树（Node 6） | 比 LLM 推断更稳定、可解释 |
| 仓位计算 | 连续函数（波动率×状态缩放） | 比离散映射更平滑 |
| 选股消息面 | 1次LLM评100板块 + 字典查找 | 比40次LLM评40只股快10倍 |
| 状态机 | RRG四象限 + 6态Markov（exit_pressure_days替代主线框） | ~~主线框~~已废除(2026-08-31)，职责收编到状态机 |
| 埋伏检测 | 6维加权评分（非AND闸门）+ 冷启动惩罚 + 方向惩罚 | 硬闸门候选池归零；Track1(状态机)与Track2(评分)真并集合并 |
| 埋伏仓位 | 类型化系数（drift×1.0 / fade×0.7 / binary×0.4）| 二元事件限仓压缩（Yee 2025）；漂移型给最大仓位 |
| 催化剂排序 | B 公式（影响权重×临近度×置信度 + 冗余加成封顶 40%）| 事件驱动前瞻维度注入选股，仅 ambush 板块参与重排 |
| 兑现追踪 | PEAD多窗口验证 + 故事寿命统计（P6, 零 LLM）| 所有可配参数的 A 股本地化校准源（0.7 阈值/10 日衰竭/4 周除名） |
| 板块名解析 | 四层：清洗→别名表（自动学习）→模糊匹配→统一入口 `_match_sector` | TDX 与 THS 板块名不一致是选股流水线的常见故障源；`_match_sector` 统一归一化精确→别名变体→受控子串≥3字 |
| 板块名反向映射 | `_build_name_to_code_map` 从 TDX_BOARD_REGISTRY 构建 name→code 反查 | TDX_REGISTRY 键是 6 位代码而非板块名，直接 `get(name)` 恒空 |
| 事件→板块扇出 | T5 LLM 语义对齐 + `affected_sectors` 多板块字段 | 规则别名表无法 scale（89 个名字还在增长），LLM 擅长语义映射 |
| 催化剂守卫 | D1回声检测 + D2周末守卫 + D3过期守卫 + D4置信度守卫 | 防止已发生事件回声、周末无效事件、低置信事件污染日历 |
| 矛盾仲裁 | 规则检测 + LLM 语义裁决（T3, 仅 medium/high）| 规则层可能过度悲观/遗漏风险，LLM 补充语义判断但权限受限（不可 exclude） |
| Agent 终审 | 只有否决权（reject→降级展示），默认关闭 | 防止 Agent 幻觉误删合格候选；影子模式验证准确率后再开写权限 |
| 审查层（T1/T1-H） | 独立 profile + 独立词表（confirm/warn/reject 与 hold/add/reduce/exit），默认关闭 + 影子 | T1 候选终审在选股漏斗末尾；T1-H 持仓审查在 Node 7 Step 2.5（埋伏持仓 > P4 退出 > 低分浮亏，reduce/exit 施加降档惩罚） |
| A6 glue 直读 | 阶段 1 转正（2026-09-17）：`GOT_A6_ENABLED` 默认 true，glue status=ok 时直接替换板块评分四件套；阶段 2 退役（2026-09-26）：规则评分模式与僵尸三件套 `_cross_check_*` 删除，漏命中/降级板块改 neutral 中性垫底（放弃逐板块规则兜底） | 8 交易日影子实测 M1 +25.5pp / M6 +31.4pp；`GOT_A6_ENABLED=false` 为 fail-fast（glue 不可用直接 raise），不再回退规则评分 |
| 影子先行 | Track B 全部 `SHADOW=true` 默认，积累数据后转正（A6 已于 2026-09-17 转正，SHADOW 默认翻 false；排期轮批 5 的 4b/3b/4.1 同样影子先行，2026-09-28） | 避免"一上来就 agent 化"掩盖管道 bug；用归档数据验证准确率 |
| regime 振荡修复 | 迟滞豁免（连续钉住 3 日直接确认当日 raw）+ `transition_count` 改统确认切换事件（`transition_events` 落盘、20 日窗口、同日幂等） | 0910 起 bull↔bear 日日交替致确认死锁，输出被永久钉死 `transition`，×0.8 震荡惩罚建立在失效输入上；回滚 `DISABLE_REGIME_OSCILLATION_FIX=true` |
| 组合层超限减仓 | Step 1.5：个股无卖出但组合仍超 `position_limit` 时按 composite_score 升序削最弱持仓（三态开关 `GOT_SELL_SOURCE_*`，默认影子） | 补 W1「超限无减仓路径/不减反买」；复用板块漂移宽限防 688598 式误卖；影子态落 `sell_source_shadow` 观测、零决策改动 |
| C-P13 接地管线 | L1 embedding 检索接地（K=8）→ L2 受约束生成（board_code 白名单+证据 span 硬护栏）→ L3 CoVe 验证剪枝；仅 `--got-replay` + `GLUE_PIPELINE_ENABLED` 双与门 | 阶段 1 六条验收全过（2026-09-27 结项，M3=0%）；阶段 2 realized 收益率标定证伪关闭（2026-09-28）→ 管线休眠不解禁不删除；conf≥0.70 全真/牵强全≤0.50，重标定须用 M3 口径 |
| 北向资金 | 永久豁免维度（`EXEMPT_DIMENSIONS`） | 2024-08 起官方停披，不计入 missing_dimensions 和 confidence 分母 |

### GOT 驱动选股流水线

> 入口：`StockScreenerPipeline.run_got_mode(got_date, top_n)`  
> 消费：`data/got_daily/YYYYMMDD/` 下 6 个节点 JSON

**问财初筛策略**：
- 主线板块：`dde大单净量>=0.5%`（确认主力当日介入）；≤2只时自动放松至≥0.3%补充
- 埋伏板块：`dde连续3天为正`（持续吸筹信号，不要求当天爆发）
- 横盘过滤：停滞校准标记 `_STAGNATION_CALIBRATION_DATE`，建议每 3 个月重新校准

**5 轮漏斗 + P5 催化重排 + T1 Agent 终审**：

| 轮次 | 过滤逻辑 | 保留数 |
|------|---------|--------|
| Round 0 | 去除涨停股（无法买入） | ~30-40 |
| Round 0b | ≥8%涨幅软过滤（追高风险） | ~25-35 |
| Round 1 | 排除 exhausted 板块 + 多窗口停滞过滤（4死区） | ~20-30 |
| Round 1.5 | 停滞过滤（量价停滞+动量停滞+宽度停滞+趋势停滞）；**埋伏道拆分后 ambush 票走独立道（G-A1~A4），不进主线 R1–R4** | ~18-28 |
| Round 2 | 资金面评分 Top 20（DDE 百分位 + 板块资金流） | 20 |
| Round 3 | 波段技术评分 Top 15（8 维连续评分：MA20/布林/RSI/MACD 等）；**仅主线道票参与，埋伏道票已绕过** | 15 |
| Round 4 | 风险评分：Top 6 主线(risk>50) + ≤2只保底(risk>35) | 6-8 |
| **P5** | **催化剂关联度重排**（仅 ambush 板块, confidence≥0.7） | 不变 |
| Round 5 | 板块分散（每板块最多 1 只）→ Top N | 2-3 |
| **T1** | **Agent 终审**（默认关，只有否决权，reject→降级移末位） | 不变 |

**5 维评分权重**：资金面 15% + 消息面 15% + 板块强度 25% + 技术面(波段) 25% + 风险因子 20%

---

> **详细实施记录**（Phase 日志、Node 重设计、修复记录、文件改动清单）已归档至 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## 环境

- **Conda**: `daily_stock_analysis`（Python 3.12.13，日常运行主环境）；仓内另有 `.venv`（Python 3.14，pytest 回归/脚本校验用）
- **数据库**: SQLite `data/stock_analysis.db`（自动建表）
- **配置**: `.env`（LLM API Key、NGA Cookie、东财反爬等）

```bash
# ⚠️ Claude Code 使用 Git Bash，必须 source conda profile 后再 activate
# 禁止使用 conda run（Windows GBK 编码崩溃）
source D:/ProgramData/miniconda3/etc/profile.d/conda.sh && conda activate daily_stock_analysis
python main.py --got               # 全流程 GOT（Phase 1 + Phase 2）
python main.py                     # 常规分析
python main.py --screener          # 选股
python main.py --got-replay        # GOT 回放（glue/C-P13 管线变体实验入口，生产链路零影响）
python main.py --schedule          # 定时模式
```

---

## 关键设计决策

- **GOT 独立模块**：`src/got/` 与现有 Agent 层并行，零侵入
- **直接调用服务**：不包装 Tool，仅 LLM 推理节点调用 `LLMToolAdapter`
- **数据降级**：维度缺失时权重归一化 + 置信度下调，不做硬降级
- **路径级失败**：技术分析路/独立数据路任一完全失败 → 聚合节点报错（use_mock 模式下跳过）
- **非交易日跳过**：`trading_calendar.is_market_open()` 判断
- **上下文持久化**：`data/aggregator_contexts/` 按交易日存储 JSON，支持时间一致性校验 + 跨日记忆读取
- **生产监控**：`data/got_timeseries/monitoring.json` 记录运行指标
