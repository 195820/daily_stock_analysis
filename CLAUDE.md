# Daily Stock Analysis (DSA)

> A股智能分析系统，支持多数据源故障切换、LLM 驱动分析报告、多 Agent 协作、选股流水线、持仓管理。

## 开发规则

**→ 参见 [`AGENTS.md`](AGENTS.md)（CLAUDE.md 为其镜像）**

### GOT 运行规范

- **影子模式输出总结**：当 GOT 运行包含影子模式内容（如 A6 glue 影子模式）时，运行完成后必须在终端输出总结，包括：
  - Phase 1 各节点核心结论（大盘/板块/资金/消息/情绪/聚合）
  - 影子模式输出内容（如 `agent_glue.json` 的关系/强度/家族统计）
  - Phase 2 交易建议（持仓分析/候选股/操作建议）
  - 关键风险提示和主线续航评估

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
│   │   │   ├── aggregator.py         # Node 6: 聚合（A5匹配/A6泛化词/T3仲裁/埋伏通道/跨节点共振）
│   │   │   └── portfolio_decision.py # Node 7: 统一决策（Phase 2，5步流水线+埋伏退出）
│   │   ├── catalyst_calendar.py   # 催化剂日历（D1回声/D2周末/D3过期/D4置信守卫/T5对齐/T2校验）
│   │   ├── catalyst_config.py     # 催化剂配置（13组全球→A股映射/影子模式开关/可调参数集中管理）
│   │   ├── catalyst_realization.py # P6: 催化兑现追踪（A1三级解析/PEAD多窗口/故事寿命/resolution三态）
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
│   ├── data_provider/           # 数据获取层（策略模式）
│   │   ├── manager.py                # DataFetcherManager（故障切换+熔断器）
│   │   ├── mootdx_fetcher.py         # 通达信 TCP P0（不封 IP，验活主站池+故障转移）
│   │   ├── em_rate_limiter.py        # 东财 HTTP 统一限流（1s间隔+抖动+重试+连接池）
│   │   ├── efinance_fetcher.py       # 东财 HTTP P0
│   │   ├── akshare_fetcher.py        # akshare P1（多源降级）
│   │   ├── tushare_fetcher.py        # Tushare Pro（P0 with Token）
│   │   └── ...                         # 共 11 个数据源
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
├── data/                        # 数据文件
│   ├── stock_analysis.db             # SQLite 数据库（30+ 表）
│   ├── aggregator_contexts/          # GOT 上下文持久化（EMA基线/跨日ctx）
│   ├── got_daily/YYYYMMDD/           # 每日历史档（6节点JSON+reasoning+meta）
│   └── got_timeseries/               # 时序数据（monitoring.json）
│
├── strategies/custom/           # 自定义选股策略（YAML）
├── tests/                       # 191 个测试文件
├── docs/                        # 文档
│   ├── CLAUDE.md                     # 开发环境 + 个性化改造记录
│   ├── AGENTS.md                     # AI 协作规则（真源）
│   └── custom/
│       └── screener_design.md        # 选股系统设计文档
│
└── agent_cot_design.md          # GOT 架构设计文档（v1.0，2026-08-11）
```

---

## GOT 架构概览

> **详细设计文档**：[`agent_cot_design.md`](agent_cot_design.md)（v1.0，2026-08-11）  
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
                    │    transition_pending)     │                          │
                    │                          │                          │
                    │  L1: 板块 mini-GOT        │                          │
                    │   (4维交叉验证+A5匹配     │                          │
                    │    +A6泛化词护栏)          │                          │
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

### 节点 Schema 数据流

| 节点 | 输入 | 输出 Schema | 关键输出字段 | 持久化 |
|------|------|-------------|-------------|--------|
| **Node 1** 大盘 | 无（直取 5 指数 250d K线 + breadth + T-0 行情） | `MarketContext` | `tech_growth.score/trend`, `market_state.attack_power/regime/crash_override`, `realized_volatility`, `vol_adjusted_position`, `regime_scale`, `position_limit`, `confidence` | — |
| **Node 2** 板块 | `MarketContext`（`attack_power` → 主线数量限制, `today_change` → 弱市检测） | `SectorContext` | `active_mainline[]`, `ambush[]`, `near_miss_ambush[]`, `cooling_down[]`, `industry_top[]`, `theme_families[]`, `strength_lookup{}` | `node2_sector_trends`（RRG 状态机+6态Markov） |
| **Node 3** 资金面 | `MarketContext`（trend）, `SectorContext`（主线名称 → 匹配 bonus） | `CapitalContext` | `institutional_score`, `evidence.sector_flow/dde/northbound(永久豁免)`, `status`, `confidence_multiplier` | — |
| **Node 4** 消息面 | 无（7 源推送采集） | `NewsContext` | `major_events[]`（含 `lifecycle_state`）, `catalyst_calendar`, `impact_score` | `node4_events`（事件生命周期）, `catalyst_events`（日历 UPSERT）, `node4_raw_news`（原始新闻跨日检测） |
| **Node 5** 情绪面 | 无（6 源并行采集） | `SentimentContext` | `sentiment_score`, `sentiment_stage`（5 阶段）, `stage_state`, `persistence_regime`, `fear_greed_index`, `sentiment_dispersion`, `sentiment_momentum` | — |
| **Node 6** 聚合 | 全部 5 个 context + 催化剂日历 + 跨日记忆 | `UnifiedContext` | `market_regime`(dwell_days/transition_count/transition_pending), `cross_node_signals[]`, `confirmed_sectors[]`（`SectorIntel`）, `ambush_pool[]`, `ambush_reservoir[]`, `contradictions[]`（含T3仲裁字段） | `persistence.py` → `ema_baseline.json` + `previous_context_*.json` + `timeseries/` + `monitoring.json` |
| **Node 7** Phase 2 | `UnifiedContext` + 全部 5 个 context | `PortfolioDecision` | `risk_assessment`, `unified_scoring[]`, `allocation[]`, `ambush_exit_signals[]` | `ambush_position_snapshots`（买入/退出快照） |

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
| `data/got_daily/YYYYMMDD/` | `archive.py` | 6 个节点 JSON + `reasoning.md`（全节点 LLM 推理汇总）+ `meta.json` + `portfolio_decision.json` + `trading_recommendations.md` |
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
| 影子先行 | Track B 全部 `SHADOW=true` 默认，积累数据后转正 | 避免"一上来就 agent 化"掩盖管道 bug；用归档数据验证准确率 |
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
| Round 1.5 | 停滞过滤（量价停滞+动量停滞+宽度停滞+趋势停滞） | ~18-28 |
| Round 2 | 资金面评分 Top 20（DDE 百分位 + 板块资金流） | 20 |
| Round 3 | 波段技术评分 Top 15（8 维连续评分：MA20/布林/RSI/MACD 等） | 15 |
| Round 4 | 风险评分：Top 6 主线(risk>50) + ≤2只保底(risk>35) | 6-8 |
| **P5** | **催化剂关联度重排**（仅 ambush 板块, confidence≥0.7） | 不变 |
| Round 5 | 板块分散（每板块最多 1 只）→ Top N | 2-3 |
| **T1** | **Agent 终审**（默认关，只有否决权，reject→降级移末位） | 不变 |

**5 维评分权重**：资金面 15% + 消息面 15% + 板块强度 25% + 技术面(波段) 25% + 风险因子 20%

---

> **详细实施记录**（Phase 日志、Node 重设计、修复记录、文件改动清单）已归档至 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)

## 环境

- **Conda**: `daily_stock_analysis`（Python 3.12.13）
- **数据库**: SQLite `data/stock_analysis.db`（自动建表）
- **配置**: `.env`（LLM API Key、NGA Cookie、东财反爬等）

```bash
# ⚠️ Claude Code 使用 Git Bash，必须 source conda profile 后再 activate
# 禁止使用 conda run（Windows GBK 编码崩溃）
source D:/ProgramData/miniconda3/etc/profile.d/conda.sh && conda activate daily_stock_analysis
python main.py --got               # 全流程 GOT（Phase 1 + Phase 2）
python main.py                     # 常规分析
python main.py --screener          # 选股
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
