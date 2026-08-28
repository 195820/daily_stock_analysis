# Daily Stock Analysis (DSA)

> A股智能分析系统，支持多数据源故障切换、LLM 驱动分析报告、多 Agent 协作、选股流水线、持仓管理。

## 开发规则

**→ 参见 [`AGENTS.md`](AGENTS.md)（CLAUDE.md 为其镜像）**

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
│  催化剂系统: 日历构建(P1) + 关联度重排(P5) + 兑现追踪(P6)       │
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
│  data_provider/ + src/storage.py（SQLite 25+ 表）               │
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
│   │   │   ├── market_analysis.py    # Node 1: 大盘（4维技术评分/多时间框架/连续仓位）
│   │   │   ├── sector_analysis.py    # Node 2: 板块（4维百分位/启动检测/主线家族/主线框）
│   │   │   ├── capital_analysis.py   # Node 3: 资金面（3维：板块流/DDE/北向/降级机制）
│   │   │   ├── news_analysis.py      # Node 4: 消息面（7源推送/4次LLM/事件持久化）
│   │   │   ├── sentiment_analysis.py # Node 5: 情绪面（6维置信度加权/反身性/恐贪指数）
│   │   │   ├── aggregator.py         # Node 6: 聚合（3层分级GOT/L0全局/L1板块/L2组合）
│   │   │   └── portfolio_decision.py # Node 7: 统一决策（Phase 2，5步流水线）
│   │   ├── catalyst_calendar.py   # 催化剂日历构建（5源事件/全量5日刷新/增量更新）
│   │   ├── catalyst_config.py     # 催化剂配置（13组全球→A股映射/常量/宏观日历规则）
│   │   ├── catalyst_realization.py # P6: 催化兑现追踪（事后验证/故事寿命/参数校准源）
│   │   ├── schemas.py           # 节点输入输出 Pydantic Schema（~40 模型）
│   │   ├── orchestrator.py      # GOT 编排器（并行/串行调度）
│   │   ├── indicators.py        # 纯规则指标函数（ADX/MACD/A-D线/截面百分位）
│   │   ├── persistence.py       # 上下文持久化（data/aggregator_contexts/，EMA 基线）
│   │   ├── archive.py           # 每日历史档（data/got_daily/YYYYMMDD/，全量 ctx + reasoning）
│   │   └── news_feeds.py        # Node 4 新闻采集（7源推送型：新浪/CLS/华尔街见闻/东财/NewsNow/NGA/同花顺）
│   │
│   ├── services/                # 核心服务
│   │   ├── technical_indicator.py    # 短线技术指标（需扩展 ADX/MACD）
│   │   ├── sector_rotation.py        # 板块轮动分析
│   │   ├── forum_sentiment.py        # NGA 舆情（后续切雪球）
│   │   ├── stock_screener.py         # 选股流水线（GOT 模式：DDE 初筛 + 5 轮漏斗）
│   │   ├── ths_screener.py           # 同花顺问财
│   │   ├── portfolio_service.py      # 持仓管理
│   │   ├── risk_assessment.py        # 风险评估
│   │   ├── strategy_filter.py        # YAML 策略筛选
│   │   ├── dde_fetcher.py            # DDE 大单净量
│   │   ├── board_name_resolver.py    # 板块名解析（TDX→THS 三层：清洗/别名表/模糊匹配）
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
│   │   ├── mootdx_fetcher.py         # 通达信 TCP P0（不封 IP，mootdx BESTIP 自动探测）
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
│   ├── storage.py               # SQLAlchemy ORM（25+ 表）
│   ├── notification_sender/     # 多渠道通知
│   ├── llm/                     # LLM 配置
│   └── patches/                 # 反爬补丁
│
├── data/                        # 数据文件
│   ├── stock_analysis.db             # SQLite 数据库
│   └── aggregator_contexts/          # GOT 上下文持久化（新建）
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
     │  │         │           │  │  │催化剂日历    ││      │              │
     │  │ 4维百分位│           │  │  │CatalystCal   ││      │              │
     │  │ RRG状态机│           │  │  │Builder       ││      │              │
     │  │ 主线框   │           │  │  │(5源事件+    ││      │              │
     │  │ 埋伏检测 │           │  │  │ LLM叙事)    ││      │              │
     │  └────┬────┘            │  │  └──────┬───────┘│      │              │
     │       │ SectorContext   │  │         │        │      │              │
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
                    │  L0: 全局环境研判         │                          │
                    │   (regime_score → bull/bear/transition)             │
                    │                          │                          │
                    │  L1: 板块 mini-GOT        │                          │
                    │   (4维交叉验证: tech+capital+news+sentiment)        │
                    │                          │                          │
                    │  L1': 埋伏通道 (P3)       │◄── catalyst_calendar    │
                    │   (3层闸门 + B公式排序    │                          │
                    │    + 蓄水池)              │                          │
                    │                          │                          │
                    │  L2: 组合级决策           │                          │
                    │   (矛盾检测 + 仓位日限幅) │                          │
                    └────────────┬─────────────┘                          │
                                 │ UnifiedContext                         │
                                 │ (regime + confirmed_sectors            │
                                 │  + ambush_pool + ambush_reservoir)     │
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
                    │   (事后验证: 预期 vs 实际) │                          │
                    │   (故事寿命统计)           │                          │
                    │   → CatalystRealization   │                          │
                    │     Record (SQLite)       │                          │
                    └──────────────────────────┘                          │
```

### 编排流程

`GOTOrchestrator.run()` 入口，`asyncio.gather` 分组并行：

```
run()
 ├─ asyncio.gather(
 │    tech_path:  Node 1 → Node 2 → Node 3     （串行，后者消费前者输出）
 │    data_path:  Node 4 ‖ Node 5               （并行，独立采集）
 │  )
 ├─ Node 6: 聚合（消费全部 5 个 context）
 ├─ archive_daily_run()（Phase 1 归档 → data/got_daily/YYYYMMDD/）
 ├─ Phase 2: PortfolioDecision（消费 6 个 context）
 ├─ archive_phase2()（Phase 2 归档）
 └─ CatalystRealizationTracker.check_and_record_realization()（P6，失败不阻塞）
```

**催化剂反馈循环**：Node 4 事件 → `CatalystCalendarBuilder`（消费 `Node4EventRecord`）→ `CatalystCalendar` → 嵌入 `NewsContext.catalyst_calendar` → Node 6 埋伏通道 + Phase 2 选股重排

### 节点 Schema 数据流

| 节点 | 输入 | 输出 Schema | 关键输出字段 | 持久化 |
|------|------|-------------|-------------|--------|
| **Node 1** 大盘 | 无（直取 5 指数 250d K线 + breadth + T-0 行情） | `MarketContext` | `tech_growth.score/trend`, `market_state.attack_power/regime`, `position_limit`, `confidence` | — |
| **Node 2** 板块 | `MarketContext`（`attack_power` → 主线数量限制, `today_change` → 弱市检测） | `SectorContext` | `active_mainline[]`, `ambush[]`, `cooling_down[]`, `industry_top[]`, `theme_families[]` | `node2_sector_trends`（RRG 状态机）, `sector_frames`（主线框） |
| **Node 3** 资金面 | `MarketContext`（trend）, `SectorContext`（主线名称 → 匹配 bonus） | `CapitalContext` | `institutional_score`, `evidence.sector_flow/dde/northbound`, `status` | — |
| **Node 4** 消息面 | 无（7 源推送采集） | `NewsContext` | `major_events[]`（含 `lifecycle_state`）, `catalyst_calendar`, `impact_score` | `node4_events`（事件生命周期）, `catalyst_events`（日历 UPSERT） |
| **Node 5** 情绪面 | 无（6 源并行采集） | `SentimentContext` | `sentiment_score`, `sentiment_stage`（5 阶段）, `fear_greed_index`, `sentiment_dispersion`, `sentiment_momentum` | — |
| **Node 6** 聚合 | 全部 5 个 context | `UnifiedContext` | `market_regime`, `confirmed_sectors[]`（`SectorIntel`）, `ambush_pool[]`, `ambush_reservoir[]`, `contradictions[]` | `persistence.py` → `ema_baseline.json` + `previous_context_*.json` |
| **Node 7** Phase 2 | `UnifiedContext` + 全部 5 个 context | `PortfolioDecision` | `risk_assessment`, `unified_scoring[]`, `allocation[]`, `ambush_exit_signals[]` | `ambush_position_snapshots`（买入/退出快照） |

### 持久化层总览

**SQLite 表（`data/stock_analysis.db`）**：

| 表名 | 写入方 | 用途 |
|------|--------|------|
| `node2_sector_trends` | Node 2 | 板块轨迹追踪（6 态状态机 + RRG EMA + 生命周期 + 强度轨迹 JSON） |
| `sector_frames` | Node 2 | 主线框管理（框名/成员/进出时间/驻留天数/is_active） |
| `node4_events` | Node 4 | 事件生命周期（emerging→fermenting→peak→fading→archived, 7 日自动归档） |
| `catalyst_events` | CatalystCalendar | 催化剂日历事件（event_id UPSERT, 14 日窗口） |
| `catalyst_refresh` | CatalystCalendar | 日历刷新节奏（单行表, 全量 5 日 / 增量其余） |
| `ambush_position_snapshots` | Phase 2 (P4) | 埋伏仓位快照（买入价 + 退出信号 + 退出价 + is_open） |
| `catalyst_realizations` | P6 | 催化兑现归档（预期 vs 实际收益 + 方向正确性 + 故事寿命天数） |

**文件持久化**：

| 路径 | 写入方 | 内容 |
|------|--------|------|
| `data/got_daily/YYYYMMDD/` | `archive.py` | 6 个节点 JSON + `reasoning.md`（全节点 LLM 推理汇总）+ `meta.json` + `portfolio_decision.json` + `trading_recommendations.md` |
| `data/aggregator_contexts/previous_context_YYYYMMDD.json` | `persistence.py` | Node 6 跨日 UnifiedContext（时间一致性校验基准） |
| `data/aggregator_contexts/ema_baseline.json` | `persistence.py` | EMA 基线（α=0.3, regime_score + 滚动标准差, 冷启动 5 日预热） |

### 关键设计决策速览

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据源优先级 | mootdx TCP P0 → efinance HTTP P0 → akshare P1 | TCP 不受 HTTP 反爬影响 |
| 评分归一化 | 截面百分位排名（Node 2） | 自适应牛熊市，避免 clip 硬阈值 |
| 矛盾检测 | 规则决策树（Node 6） | 比 LLM 推断更稳定、可解释 |
| 仓位计算 | 连续函数（波动率×状态缩放） | 比离散映射更平滑 |
| 选股消息面 | 1次LLM评100板块 + 字典查找 | 比40次LLM评40只股快10倍 |
| 框系统 | SQLite 持久化（主线框个体级进出）| 比硬编码更灵活、可回溯；埋伏板块不持久化，每日实时判定 |
| 埋伏仓位 | 类型化系数（drift×1.0 / fade×0.7 / binary×0.4）| 二元事件限仓压缩（Yee 2025）；漂移型给最大仓位 |
| 催化剂排序 | B 公式（影响权重×临近度×置信度 + 冗余加成封顶 40%）| 事件驱动前瞻维度注入选股，仅 ambush 板块参与重排 |
| 兑现追踪 | 事后验证 + 故事寿命统计（P6, 零 LLM）| 所有可配参数的 A 股本地化校准源（0.7 阈值/10 日衰竭/4 周除名） |
| 板块名解析 | 三层：清洗→别名表（自动学习）→模糊匹配 | TDX 与 THS 板块名不一致是选股流水线的常见故障源 |

### GOT 驱动选股流水线

> 入口：`StockScreenerPipeline.run_got_mode(got_date, top_n)`  
> 消费：`data/got_daily/YYYYMMDD/` 下 6 个节点 JSON

**问财初筛策略**：
- 主线板块：`dde大单净量>=0.8%`（确认主力当日介入）
- 埋伏板块：`dde连续3天为正`（持续吸筹信号，不要求当天爆发）

**5 轮漏斗 + P5 催化重排**：

| 轮次 | 过滤逻辑 | 保留数 |
|------|---------|--------|
| Round 0 | 去除涨停股（无法买入） | ~30-40 |
| Round 1 | 排除 exhausted 板块 + 多窗口停滞过滤 | ~20-30 |
| Round 2 | 资金面评分 Top 20（DDE 百分位 + 板块资金流） | 20 |
| Round 3 | 波段技术评分 Top 15（8 维连续评分：MA20/布林/RSI/MACD 等） | 15 |
| Round 4 | 风险评分 Top 8（RSI 超买/5 日涨幅/波动率） | 8 |
| **P5** | **催化剂关联度重排**（仅 ambush 板块, confidence≥0.7） | 不变 |
| Round 5 | 板块分散（每板块最多 1 只）→ Top N | 2-3 |

**5 维评分权重**：资金面 15% + 消息面 15% + 板块强度 25% + 技术面(波段) 25% + 风险因子 20%

---

## 复用策略

### 直接复用（不改动）

| 服务 | 用途 |
|------|------|
| `DataFetcherManager` | 底层数据获取（11 源故障切换，MootdxFetcher TCP P0） |
| `SectorRotationService` | 板块轮动基础分析 |
| `LLMToolAdapter` | LLM 调用（litellm 多 Provider） |
| `trading_calendar` | 交易日判断 |
| `PortfolioService` | 持仓管理（Phase 2） |
| `THSScreenerService` | 同花顺问财（Phase 2） |

### 需要扩展

| 服务 | 扩展内容 |
|------|---------|
| `TechnicalIndicatorService` | 新增 `_calc_adx()` + `_calc_macd()` |

### 需要新建

| 模块 | 内容 |
|------|------|
| `src/got/` | 6 个节点 + 编排器 + Schema + 指标函数 |
| 北向资金 Service | 恢复 `market_analyzer.py` 中废弃代码 |
| 大宗交易 Service | akshare `stock_dzjy_*` |
| 市场宽度函数 | 涨跌家数/涨停跌停统计 |

---

## 缺失能力（按优先级）

| 优先级 | 能力 | 对应节点 |
|--------|------|---------|
| P0 | ADX 计算 | Node 1 |
| P0 | MACD 集成 | Node 1 |
| P0 | 北向资金 | Node 3 |
| P1 | 市场宽度 | Node 1 |
| P1 | 龙虎榜机构席位过滤 | Node 3 |
| P1 | ETF 申赎/融资融券 | Node 3 |
| P1 | Volume Profile | Node 1 (P3-2) |
| P1 | 大宗交易 | Node 3 (P3-5) |

---

## 实施计划

> 详见 [`agent_cot_design.md`](agent_cot_design.md) §2-§3（各节点设计）+ §6（编排流程）+ §7（历史档系统）

| 阶段 | 目标 | 工作量 | 状态 |
|------|------|--------|------|
| Phase 1a | MVP 骨架（mock 数据跑通 6 节点） | 1 天 | ✅ 完成 |
| Phase 1b | Node 1 大盘分析（真实数据） | 2 天 | ✅ 完成 |
| Phase 1c-1 | Node 2 板块分析（真实数据） | 1 天 | ✅ 完成 |
| Phase 1c-2 | Node 3 资金面分析（真实数据） | 1 天 | ✅ 完成 |
| Phase 1d-1 | Node 4 消息面分析（真实数据） | 1 天 | ✅ 完成 |
| Phase 1d-2 | Node 5 情绪面分析（真实数据） | 0.5 天 | ✅ 完成 |
| Phase 1e | Node 6 聚合 | 1.5 天 | ✅ 完成 |
| **总计** | | **~8 天** | |

**Phase 1b 修复记录：**
- ✅ A 股指数数据获取 KeyError 修复（`data_provider/base.py` + `akshare_fetcher.py`）
- ✅ 多时间框架信号 5 日评分为 0 修复（`src/got/nodes/market_analysis.py`）
- ✅ 设计文档更新至 v0.4（`agent_cot_design.md`）

**Phase 1c-1 Node 2 实现记录：**
- ✅ 5 维强度评分（量价/动量/宽度/相对强度/资金流，加权 0.30/0.25/0.20/0.15/0.10）
- ✅ 启动信号检测（3 硬门槛 + 5 维加权打分，strong_launch ≥ 0.75 / weak_launch ≥ 0.55）
- ✅ 过热信号检测（强背离/量能背离/宽度极端/换手率极端，任一触发）
- ✅ 6 级状态判定（启动 > 过热 > 持续强势 > 持续弱势 > 埋伏 > 防守 > 中性）
- ✅ 主线分级 S/A/B（政策+规模+产业链+龙头+连续性评分）
- ✅ 生命周期分类（tier 依赖阈值 → launch/acceleration/mature/late_stage/mature_exhausted）
- ✅ 精选概念列表 ~25 概念（AI 产业链 + 科技防守，关键词匹配东财板块）
- ✅ LLM reasoning 生成（纯文本总结，不影响信号逻辑）
- ✅ DataQuality 字段级质量追踪 + 降级记录

**Phase 1c-1 关键技术决策：**
- **数据源**：akshare `stock_board_concept_name_em()` 为主（单次拉全量 ~500 板块，避免 push2 分页限流），push2 直连降级
- **反爬**：集成 `src/patches/eastmoney_patch.py`（随机 UA + NID 令牌 + 请求休眠），启动时调用 `eastmoney_patch()`
- **关键词匹配**：精确 > 前缀 > 长关键词子串（覆盖 ≥60% 板名），防止短关键词误匹配（如"电机"→"发电机概念"）
- **端到端验证**：495 板块获取 → 8 概念匹配 → K 线 → 5 维评分 → LLM reasoning，全链路正常

**Phase 1c-1 已知限制：**
- push2 分页限流：2025-04 起东财加强反爬（TLS 指纹+频率），第 3 页后易失败，已改为 akshare 单页全量规避
- 非交易时段数据：周末/节假日 `涨跌幅/上涨家数` 全 0，板块分析意义有限（建议交易时段运行）
- LLM 配置依赖：需在 `.env` 中配置 `LITELLM_MODEL` 及 API Key，否则 reasoning 降级为简单文本

**Phase 1c-1 文件改动清单：**
- `src/got/nodes/sector_analysis.py`（新建）：Node 2 完整实现
- `src/services/sector_rotation.py`（修改）：`fetch_board_list` 改为 akshare 优先 + 字段提取补全
- `scripts/run_node2.py`（新建）：本地运行脚本 + 反爬补丁预加载
- `scripts/diagnose_network.py`（新建）：网络连通性诊断工具

**Phase 1c-2 Node 3 实现记录：**
- ✅ 5 维度评分体系（北向资金/龙虎榜/ETF申赎/融资融券/大宗交易，权重 0.25/0.25/0.25/0.15/0.10）
- ✅ 降级机制（P2-4）：缺失维度权重归一化 + 置信度按可用比例下调
- ✅ 龙虎榜机构评分（`stock_lhb_jgmmtj_em` 区间机构净买入 / 5e8 归一化）
- ✅ 融资融券评分（`macro_china_market_margin_sh` 融资余额日变化 / 2e9）
- ✅ 大宗交易评分（`stock_dzjy_mrtj` 成交额加权折溢率 → 分段评分：甩货/抢筹/平价）
- ✅ 北向资金自动降级（2024-08 后 `当日成交净买额` 全 NaN，自动标记缺失）
- ✅ ETF 申赎自动降级（`fund_etf_scale_sse/szse` 仅快照无历史，无法计算日度变化）
- ✅ LLM reasoning 生成（DeepSeek 驱动，含主线板块机构态度分析）
- ✅ DataQuality 字段级追踪 + 降级状态（normal/degraded/failed）

**Phase 1c-2 关键技术决策：**
- **数据源**：4 个 akshare API 并发获取（北向资金沪/深股通 × 2 + 龙虎榜 + 融资融券 + 大宗交易）
- **大宗交易阈值**：折价 > 5% → 甩货信号（score -1~-0.3），溢价 > 2% → 抢筹信号（score 0.3~1.0），中间 → 平价成交
- **北向资金停披处理**：2024-06 起沪深港通日度数据不再公开，akshare `stock_hsgt_hist_em` 的 `当日成交净买额` 列自 2024-08 后全 NaN。Node 3 自动检测并降级，不阻塞其他维度
- **ETF 申赎限制**：`fund_etf_scale_sse/szse` 仅返回当前快照（单一日期），无法计算日度份额变化。待后续接入历史数据源（如数据库逐日存储）后启用
- **实际可用维度**：3/5（龙虎榜 + 融资融券 + 大宗交易），置信度 0.6

**Phase 1c-2 已知限制：**
- 北向资金维度失效：2024-08 后数据不可用，需寻找替代数据源（如 Tushare `hsgt_top10` 或 Wind API）
- ETF 申赎维度失效：无历史份额序列，需逐日积累数据或使用第三方数据源
- 融资融券仅沪市：`macro_china_market_margin_sh` 只覆盖上交所，深市数据需额外接入 `macro_china_market_margin_sz`
- 龙虎榜去重：同一股票可能因多日上榜出现重复，当前仅取 top 5 不严格去重
- 非交易时段：大宗交易/龙虎榜在周末无新数据，返回空集时自动降级

**Phase 1c-2 文件改动清单：**
- `src/got/nodes/capital_analysis.py`（重写）：Node 3 真实数据实现，含 5 维度评分 + 降级 + LLM reasoning
- `scripts/run_node3.py`（新建）：本地运行脚本（使用 Node 1/2 mock 上下文 + Node 3 真实数据）

**Phase 1d-1 Node 4 实现记录：**
- ✅ 新闻采集层：`SearchService.search_market_news()` 新增通用市场级搜索方法（跳过个股 relevance 评分）
- ✅ 3 类 query 并发采集（policy/industry/global，6 个搜索词）
- ✅ 去重（title 相似度 ≥0.45 合并）+ 多源确认（`source_count` 统计独立来源域名数）
- ✅ 3 次 LLM 串行调用：事件提取（temp=0.2）→ 概念发现（temp=0.5）→ 风险总结（temp=0.3）
- ✅ 空响应重试（max_retries=3）+ 规则兜底（风险从负面事件提取）
- ✅ 阈值过滤 + 显著度加权评分：low 不参与，medium 权重 1.0，high 权重 2.0，source_count≥3 额外 1.5x
- ✅ `NewsContext` Schema 新增 `status` 字段（对齐 CapitalContext 模式）
- ✅ DataQuality 字段级追踪（available/missing/failed）
- ✅ 端到端验证：Bocha 搜索 → 5 事件 + 1 概念 + 1 风险 + reasoning，评分 0.75 positive

**Phase 1d-1 关键技术决策：**
- **数据源**：完全依赖 `SearchService`（Web 搜索），项目无 akshare 新闻 API、无财联社/雪球电报接入
- **搜索方法**：新增 `search_market_news()` 而非复用 `search_stock_news()`，因后者有个股 relevance 评分管道不适合市场级 query
- **串行 LLM**：原设计 3 并发，实测 DeepSeek API 并发时空响应率高，改串行执行保障稳定性
- **风险兜底**：DeepSeek 对风险总结 prompt 间歇返回空，增加规则降级——从 `sentiment="negative"` 事件中自动提取

**Phase 1d-1 已知限制：**
- 搜索依赖 API Key 配置（Bocha/Tavily/Brave 等），无 Key 则全降级
- Bocha 免费版有请求频率限制（6 并发 query 中约 1 个可能被限频）
- LLM 模型需保持可用状态（`deepseek-chat` 即将下线需切换至 `deepseek-v4-flash`）
- 概念发现偶尔 JSON 解析失败（中文编码 + max_tokens 边界），已通过增大 token 限制缓解

**Phase 1d-1 文件改动清单：**
- `src/search_service.py`（新增方法）：`SearchService.search_market_news()` 通用市场级搜索
- `src/got/schemas.py`（小改）：`NewsContext` 新增 `status` 字段
- `src/got/nodes/news_analysis.py`（重写）：Node 4 真实数据实现（~500 行）
- `scripts/run_node4.py`（新建）：本地运行脚本

**Node 4 消息面修正 v1→v2（2026-07-27~28）：**

**v1 问题（已废弃）：**
- CLS 旧端点 `nodeapi/telegraphList` 返回 404，v1 签名逆向后只覆盖 38 分钟滚动窗口——对每日分析无意义
- 东财 `stock_news_em("000001")` 搜索关键词而非热榜，漏掉长鑫等热门股
- 搜索型数据源（SearchService）query 不覆盖 IPO/宏观场景，核心事件遗漏

**v2 方案（参考 pre-market-brief 项目）：**
- ✅ **S1 新浪文章流**（`feed.sina.com.cn/api/roll/get`）：4 栏目 × 30-50 条，**覆盖 21+ 小时**，长鑫 10 条命中
- ✅ **S2 CLS 签名版**（`v1/roll/get_roll_list`）：`sign = MD5(SHA1(sorted_query))`，零 key，A 股核心快讯
- ✅ **S3 华尔街见闻**（`api-one.wallstcn.com/apiv1/content/lives`）：全球宏观，免签，含 `is_important` 标记
- ✅ **S4 东财快讯**（`np-listapi.eastmoney.com`）：国内媒体聚合，免签
- ✅ **S5 NewsNow 热榜**（`newsnow.busiyi.world/api/s`）：8 平台（微博/百度/知乎/澎湃/头条/财联社/抖音/B站），~200 条，覆盖国内时政/气候/社会/舆情
- ✅ 权重排序系统：来源权重(30%) + 时效性(25%) + 热度关键词(20%) + 跨源确认(25%)
- ✅ 砍掉 SearchService（搜索型），全部改为推送型
- ✅ 实测 5 源全活，长鑫在 7 个平台命中并作为独立事件提取（瑞穗证券目标价 70 元）
- ⚠️ 中热度消息（厄尔尼诺）在滚动窗口内短暂出现后滚出，5 源均无法稳定捕获，需持久化层解决

**v2 已知限制：**
- **中热度消息丢失**：5 源全是滚动窗口（新浪 21h / CLS 1h / 华尔街见闻 1h / 东财 2h / NewsNow 实时），中热度消息（如厄尔尼诺形成、政策预研）在窗口内只出现几小时，一旦滚出永久丢失。**需持久化层解决（见下方待办）**
- CLS 窗口仅 ~1 小时，分页再深也是同一小时数据重复
- 权重系统让高频低价值快讯（CLS 税务/统计）占前排，中热度消息被截断
- LLM 提取事件时会把相关新闻归入当日主线叙事（如长鑫被归入"韩日暴跌→存储芯片重挫"），不一定作为独立事件输出
- 概念发现（concept_suggestions）LLM 间歇返回空
- NewsNow 的 cls-hot 与 S2 CLS 有内容重叠，但去重会处理（不同数据通道，cls-hot 可捕获 CLS 签名版窗口外的热榜条目）

**待办：事件生命周期追踪（持久化层）**
- [ ] 每次 `_fetch_news()` 后将全量原始数据写入 SQLite（`node4_raw_news` 表），按 title + source + date 去重
- [ ] 下次运行时：当前窗口数据 + 历史库 = 完整视图
- [ ] 中热度事件检测：出现在历史库但不在当前窗口 → 标记"已消退"；连续多天出现 → 标记"正在发酵"
- [ ] 可复用 `src/got/persistence.py`（GOT 上下文持久化）或 `src/storage.py`（SQLite 25+ 表）

**关键发现：**
- 新浪有两个完全不同的 API：`zhibo.sina.com.cn`（直播流，38 分钟）vs `feed.sina.com.cn`（文章归档流，21+ 小时）
- CLS 签名算法 `MD5(SHA1(sorted_query))` 来自 simonlin1212/a-stock-data 项目（7.7k stars）
- NewsNow（`newsnow.busiyi.world/api/s`）是免费公开 API，聚合微博/百度/知乎/澎湃/头条/财联社/华尔街见闻/抖音/B站 9 平台热榜，单接口覆盖国内全域
- pre-market-brief 项目的 9 维 LLM 评估（催化指数/发酵潜力/博弈建议）是后续优化方向
- TrendRadar（60k+ stars）的热榜采集思路（NewsNow API）已集成到 S5

**v2 文件改动清单：**
- `src/got/news_feeds.py`（重写 v2）：5 源推送型采集（新浪/CLS/华尔街见闻/东财/NewsNow）+ 权重排序 + 跨源标记
- `src/got/nodes/news_analysis.py`（修改）：`_fetch_news()` 调用 `fetch_all_news()`，删除搜索型代码
- `src/got/schemas.py`（小改）：`NewsEvent` 新增 `source_type` 字段

**Phase 1d-2 Node 5 实现记录：**
- ✅ 数据源切换：NGA 论坛已关闭，改用 akshare 雪球热榜（关注/交易/讨论 3 榜并发）
- ✅ 市场宽度：akshare `stock_zh_a_spot_em` 获取涨跌家数/涨停跌停（代理受限时自动降级）
- ✅ 市场宽度数据源修复（2026-07-30）：东财 push2 `stock_zh_a_spot_em()` 被反爬封锁 → 改用新浪 `stock_zh_a_spot()` 优先，push2 降级为备用。修复后 breadth_data 从 missing 恢复为 available，unified_signal 从 neutral 修正为 bearish
- ✅ 搜索舆情：SearchService 搜索散户情绪关键词 → 正负面词频统计
- ✅ 情绪评分：3 维度加权（市场宽度 0.50 + 雪球热度 0.25 + 搜索舆情 0.25），缺失维度权重归一化
- ✅ 反身性 5 阶段分类（P2-2）：euphoria/optimism/neutral/pessimism/capitulation
- ✅ 极端情绪反向信号：euphoria → contrarian_sell，capitulation → contrarian_buy
- ✅ 市场温度（0=冰点, 1=沸点）+ 散户关注度 + 消息面偏离
- ✅ LLM reasoning + hot_topics 生成（JSON 输出，失败时规则降级）
- ✅ SentimentContext Schema 补全：retail_attention_count / market_temperature / news_drift
- ✅ 端到端验证：雪球 30 只热门股 + 搜索舆情 → 评分 -0.31（悲观）→ pessimism 阶段

**Phase 1d-2 关键技术决策：**
- **数据源**：NGA 已关闭（板块不存在），改用 akshare 雪球热榜。3 个 API（`stock_hot_follow_xq`/`stock_hot_deal_xq`/`stock_hot_tweet_xq`）各返回 5610 只股票，取 Top 30
- **不单独建 Service**：数据获取逻辑直接嵌入 Node 5（与 Node 1/2/3/4 保持一致），避免过度抽象
- **串行 LLM**：同 Node 4 教训，DeepSeek 并发空响应率高，改为串行调用
- **降级策略**：市场宽度失败时（东财 push2 代理限流），自动归一化到雪球+搜索两维度，不阻塞节点

**Phase 1d-2 已知限制：**
- 雪球 API 较慢（每个 ~20 秒，3 并发总计 ~20 秒），超时设 90 秒
- 市场宽度依赖东财 push2 接口，代理环境下可能失败（自动降级）
- 情绪评分依赖关键词匹配（搜索舆情维度），精度有限
- 反身性阈值（0.5/0.2）为初始值，运行 3 个月后需回测调整

**Phase 1d-2 文件改动清单：**
- `src/got/schemas.py`（小改）：`SentimentContext` 新增 3 字段（retail_attention_count/market_temperature/news_drift）
- `src/got/nodes/sentiment_analysis.py`（重写）：Node 5 真实数据实现（~480 行）
- `scripts/run_node5.py`（新建）：本地运行脚本

**Node 5 v2 重设计（2026-08-04）：**

> 基于 8 天运行数据（07-24~08-04）暴露的 6 大致命缺陷 + 3 篇学术论文（国金证券舆情选股/清华投资者情绪/fffinstill Fear & Greed Index）

**问题诊断**：
- ❌ hot_stocks sentiment 全部硬编码 "positive"（50 只股票无真实情绪判断）
- ❌ 雪球数据陈旧（07-24~07-31 热门股列表完全相同，API 返回缓存数据）
- ❌ 搜索舆情仅 28 个关键词匹配（无否定检测、无强度区分）
- ❌ NGA 间歇性缺失导致权重偏移（07-24~07-31 正常，08-03~04 缺失）
- ❌ 情绪评分与市场严重脱节（08-04 涨停 243 日 score 仅 0.085）
- ❌ 缺少关键维度（无恐贪指数、无时序对比、不利用 Node 4 新闻）

**核心变更**：
- ✅ 新增东财千股千评数据源（`stock_comment_em()` → `datacenter-web.eastmoney.com`，非 push2，5190 股 ~3s）
- ✅ 新增评论详情采样（`stock_comment_detail_*` 系列，15 只热门股并发获取参与意愿/关注度/机构参与度历史）
- ✅ 6 维置信度加权评分（东财评论 25% + 市场宽度 15% + NGA 20% + 搜索舆情 15% + 散户行为 10% + 时序动量 15%）
- ✅ 置信度加权聚合（参考 fffinstill F&G Index）：缺失维度权重自动重分配
- ✅ 情绪分歧度（参考国金证券研究）：`_compute_sentiment_dispersion()` 计算综合得分变异系数，低分歧+极端均值 → 反向信号
- ✅ 时序动量：`_compute_sentiment_momentum()` 加载近期 sentiment.json 计算 EMA(5) 偏差
- ✅ 恐贪指数：`fear_greed_index = (score + 1) / 2`，映射到 [0, 1]
- ✅ HotStocks 改造：使用东财综合得分判断真实情绪（≥70 positive, ≤30 negative），不再硬编码
- ✅ 关键词匹配改进：词库从 28 扩展到 44，新增否定词翻转检测（"不看好"→负面而非正面）
- ✅ 反身性分类改用东财关注指数（替代雪球的缓存数据）
- ✅ LLM reasoning prompt 增加分歧度/反向信号信息
- ✅ SentimentContext 新增 4 字段：fear_greed_index / sentiment_dispersion / contrarian_signal / sentiment_momentum
- ✅ Archive 覆盖修复：`archive_daily_run()` 开头清理旧档，确保当天重跑全量覆盖

**效果对比（08-04 涨停 243 日）**：

| 指标 | 旧版 | 新版 |
|------|------|------|
| sentiment_score | 0.085（中性） | 0.316（乐观） |
| hot_stocks sentiment | 全 "positive" | positive/neutral 真实区分 |
| fear_greed_index | 不存在 | 0.658（贪婪侧） |
| sentiment_dispersion | 不存在 | 0.092（低分歧=一致看多） |
| sentiment_momentum | 不存在 | +0.219（情绪升温） |

**数据源端点确认**：
- 东财千股千评 → `datacenter-web.eastmoney.com` ✅ 不走 push2
- 东财评论详情 → `datacenter-web.eastmoney.com` ✅ 不走 push2
- 数据时效性：实测返回当日数据

**Phase 2 性能优化（2026-08-04）**：
- ✅ `_get_sector_constituents_filtered()` 新增携带 `turnover_rate`/`volume` 字段（问财已返回，无需重复获取）
- ✅ `_score_technical_wave()` 换手率直接用候选数据，消除逐只实时行情获取（~5s/只）
- ✅ `_score_risk()` 移除 `get_realtime_quote` 依赖，全部使用 K 线数据
- ✅ 预估提速：Phase 2 从 ~11 分钟降到 ~3 分钟（43 次行情获取 × 5s → 0s）

**Node 5 v2 文件改动清单：**
- `src/got/nodes/sentiment_analysis.py`（核心重写 ~650 行）：6 源采集 + 置信度加权评分 + 分歧度 + 动量
- `src/got/schemas.py`（+4 字段）：fear_greed_index / sentiment_dispersion / contrarian_signal / sentiment_momentum
- `src/services/stock_screener.py`（性能优化）：携带问财字段 + 消除逐只行情获取
- `src/got/archive.py`（覆盖修复）：当天重跑清理旧档
- `scripts/run_node5.py`（更新）：打印新字段

**Phase 1e Node 6 聚合实现记录：**
- ✅ 12 步完整流水线（1018 行）：路径级校验 → 加载前日 JSON → 10 对矛盾规则检测 → LLM 批处理 → 消息面覆盖三层防护 → 时间一致性校验 4 类异常 → critical 强制人工审核 → 最终研判 LLM + 规则降级 → 仓位日限幅 ±0.2 → key_risks/opportunities 汇总 → UnifiedContext 构建 → 持久化
- ✅ 10 对矛盾检测（含 ④b/④c 子矛盾）
- ✅ 消息面覆盖三层防护（系统性关键词 → 历史验证 → LLM）
- ✅ 时间一致性校验 4 类异常（sudden_trend_flip / confidence_collapse / rapid_mainline_shift / position_limit_swing）
- ✅ 降级感知：`capital.status == "failed"` 的维度不参与矛盾检测
- ✅ LLM 调用：`LLMToolAdapter` 同步 + `asyncio.to_thread` 包装，3 次重试 + 空响应检测
- ✅ JSON 解析兼容 markdown code block

**Phase 1e 文件改动清单：**
- `src/got/nodes/aggregator.py`（新建）：Node 6 完整实现（~1018 行）

**Node 6 v2 重设计（2026-07-30）：**

> 基于 15+ 篇论文（GOT/AAAI 2024、Debate-or-Vote/NeurIPS 2025、ConsensAgent/ACL 2025、AoR/LREC 2024 等）

**核心变更**：
- ✅ 从「硬编码 10 对矛盾 + 3 次 LLM」→「三阶段流水线（最多 2 次 LLM）」
- ✅ Phase A：信号标准化 + 置信度加权聚合（纯规则，零 LLM）
- ✅ Phase B：自动矛盾检测（替代硬编码）+ 矛盾分类（factual/temporal/opinion）+ EMA 时间一致性
- ✅ Phase C：条件 LLM 精炼（agreement < 0.6 或 high 异常时触发；否则规则输出）
- ✅ 收敛剖面（ConvergenceProfile）：5 种模式 + Surprising Popularity 异见调查标记
- ✅ 新增 Schema 类型：DimensionSignal、ConvergenceProfile、DimensionContribution、AggregationResult
- ✅ UnifiedContext 新增 5 字段：convergence_profile、weighted_score、dimension_contributions、llm_refinement_used、ema_baseline
- ✅ EMA 基线持久化：`data/aggregator_contexts/ema_baseline.json`（α=0.3 + 滚动标准差）
- ✅ 砍掉死代码：`_classify_event_accuracy()`（静态历史准确率表）、`confidence_adjustment` 从未被使用
- ✅ 砍掉硬崩溃：旧版 Step 1 `validate_path_inputs` 任一路径失败直接报错 → 新版 failed 维度自动跳过

**关键设计决策**：
- **投票优于辩论**（Debate-or-Vote, NeurIPS 2025）：置信度加权投票覆盖多轮辩论大部分收益
- **概率聚合在 LLM 外部**（ThinkSum, ACL 2023）：规则推断比 LLM 推断更稳定
- **反 Sycophancy**（ConsensAgent, ACL 2025）：C2 阶段明确「批判者角色」
- **Surprising Popularity**（VOTING/ISP, 2024）：lone_outlier.confidence > avg × 1.5 → 标记调查

**Node 6 v2 文件改动清单：**
- `src/got/schemas.py`（+50 行）：新增 4 个类型 + UnifiedContext 5 字段
- `src/got/persistence.py`（+50 行）：EMA 基线 load/save
- `src/got/nodes/aggregator.py`（重写 ~530 行）：三阶段流水线替代 12 步
- `src/got/__init__.py`（小改）：更新文档注释
- `agent_cot_design.md`（更新）：Node 6 设计文档同步

**Node 6 埋伏通道 P3（2026-08-19）：**

> 基于 plan.md 的 GOT 埋伏分支拆分设计。P1（催化剂日历）+ P2（Node 2 闸门重构）已完成。

**核心变更**：
- ✅ Level 1 平行分支：`_build_ambush_channel()` 与主线 mini-GOT 独立
- ✅ 3 层流水线：闸门层（★★★利空否决 + 吸筹确认 + 防买顶双闸 + 拥挤黑名单）→ 排序层（B 公式）
- ✅ B 公式催化剂排序：主催化分 = 影响权重 × 临近系数 × 置信度 + 冗余加成（封顶 +40%）
- ✅ 拆除 ±5 主线尺子偏见：埋伏板块 `sentiment_check=="low"` 不再扣 5 分
- ✅ 蓄水池机制：闸门通过但无合格催化剂 → `AmbushReservoirEntry`（4 周自动除名）
- ✅ 矛盾否决日志：`ambush_veto_log` 记录所有否决理由
- ✅ Schema 扩展：`AmbushCandidate` + `AmbushReservoirEntry` + `SectorIntel` 4 个埋伏字段 + `UnifiedContext` 3 个输出字段
- ✅ Archive 集成：`reasoning.md` 新增埋伏通道摘要（正式池 + 蓄水池 + 否决记录）

**验证结果（20260814 归档数据，10 个埋伏板块）**：
- 9 个入蓄水池（无催化剂事件 → no_catalyst）
- 1 个被吸筹闸拦截（OLED概念，当日净流出 -1.71 亿）
- ±5 拆除验证通过：主线 composite=55 vs 埋伏 composite=60，差值 +5.0

**P3 文件改动清单**：
- `src/got/schemas.py`（+55 行）：`AmbushCandidate` + `AmbushReservoirEntry` + `SectorIntel` 4 字段 + `UnifiedContext` 3 字段
- `src/got/nodes/aggregator.py`（+230 行）：7 个新方法 + ±5 拆除 + run() 集成
- `src/got/archive.py`（+25 行）：reasoning.md 埋伏通道摘要
- `agent_cot_design.md`（+30 行）：§2.6.2b 埋伏通道设计文档

**Phase 2 埋伏仓位与退出 P4（2026-08-19）：**

> 基于 P3 正式池输出，消费 `category_tag`（drift/fade/binary）实现类型化仓位和催化剂驱动退出分支。

**核心变更**：
- ✅ 类型化仓位系数：`_assess_risk()` 中 `sector_limits` 公式追加 `catalyst_coeff`（drift×1.0 / fade×0.7 / binary×0.4）
- ✅ 埋伏退出分支：`_ambush_exit_signal()` 实现 6 种退出触发（fade_sell / drift_exhausted / drift_break_ma20 / drift_timeout / event_cancelled / stop_loss）
- ✅ 买入催化剂快照：`_write_ambush_buy_snapshots()` 在 Phase 2 建仓时写入 `ambush_position_snapshots` 表
- ✅ 退出关闭快照：`_close_ambush_snapshots()` 触发退出时更新 is_open=False + exit 字段
- ✅ `_score_holdings()` 集成：加载快照 → 退出检测 → 覆盖 action → 填充 `ambush_exit_signal` 字段
- ✅ `PortfolioDecision.ambush_exit_signals` 新增字段，归档集成到 `trading_recommendations.md` + `reasoning.md`
- ✅ Schema 扩展：`AmbushPositionSnapshot`（Pydantic）+ `UnifiedScoringResult` 3 埋伏字段 + `TradeAllocation` 4 催化剂字段 + `PortfolioDecision.ambush_exit_signals`
- ✅ Storage 新增：`AmbushPositionSnapshotRecord` 表（买入快照 + 退出追踪 + 复盘记录）

**退出触发清单**：
| 类型 | 触发条件 | exit_signal |
|------|---------|-------------|
| fade | 事件日次日 | fade_sell |
| drift | 事件后 ≥10 日 + 涨幅 <2% | drift_exhausted |
| drift | 破 MA20 | drift_break_ma20 |
| drift | 持有 >20 交易日 | drift_timeout |
| binary | 事件日 >2 日未发生 | event_cancelled |
| 通用 | P&L < -7% | stop_loss |

**验证结果（单元测试）**：
- ✅ `_ambush_exit_signal` 8 个场景全部通过（fade 退出/等待、binary 落空/等待、drift 衰竭/超时/持有、硬止损）
- ✅ `_assess_risk` 类型化系数：drift/binary 仓位比 = 2.85（预期 ~2.5）
- ✅ `AmbushPositionSnapshotRecord` 表读写 + 关闭流程正常

**P4 文件改动清单**：
- `src/got/schemas.py`（+65 行）：`AmbushPositionSnapshot` + `UnifiedScoringResult` 3 字段 + `TradeAllocation` 4 字段 + `PortfolioDecision.ambush_exit_signals`
- `src/storage.py`（+40 行）：`AmbushPositionSnapshotRecord` 表
- `src/got/nodes/portfolio_decision.py`（+200 行）：`_ambush_exit_signal()` + `_load_ambush_snapshots()` + `_write_ambush_buy_snapshots()` + `_close_ambush_snapshots()` + `_assess_risk` 类型化系数 + `_score_holdings()` 集成
- `src/got/archive.py`（+30 行）：`trading_recommendations.md` + `reasoning.md` 埋伏退出摘要
- `agent_cot_design.md`（+45 行）：§2.6.2c Phase 2 埋伏仓位与退出设计文档
- `scripts/test_p4_ambush.py`（新建 ~300 行）：P4 单元测试脚本

**Phase 2 催化剂关联度重排 P5（2026-08-19）：**

> 消费催化剂日历，在选股漏斗轮次 4（风险排雷）之后、轮次 5（板块分散）之前，按催化剂关联度对 ambush 候选重排。

**核心变更**：
- ✅ 新增 `_score_catalyst_relevance()` 方法：从 `news_context.catalyst_calendar` 加载事件，为每只候选股计算关联度分数
- ✅ 个股级事件（earnings）→ title 含股票代码 → 当事股直接置顶（score=100，direction=="negative" 跳过）
- ✅ 板块级事件（narrative/global/macro）→ sector 匹配 → score = confidence × impact × proximity × direction_coeff × 50
- ✅ 置信度 < 0.7 事件不参与评分（保持技术排序）
- ✅ 仅对 ambush 板块候选生效，主线保持原序
- ✅ ScreenerStock 新增 5 个催化剂字段（relevance_score/event_title/event_date/event_type/confidence）
- ✅ `_generate_got_reasoning()` 新增催化剂关联度展示（事件标题/类型/日期/置信度）

**验证结果**：
- ✅ 个股级事件置顶：600519 score=100（earnings 匹配）
- ✅ 板块级事件评分：000858 score=85.5（narrative，confidence=0.75）
- ✅ 低置信过滤：601318 score=0（confidence=0.5 < 0.7）

**P5 文件改动清单**：
- `src/services/stock_screener.py`（+120 行）：ScreenerStock 5 字段 + `_score_catalyst_relevance()` + 漏斗集成 + reasoning 展示

**催化归档表 P6（2026-08-19）：**

> 每个催化剂事件兑现后记录预期 vs 实际，用途：参数校准源、反向查询存废、否决准确率、故事寿命本地重估。

**核心变更**：
- ✅ `CatalystRealizationRecord` 表：event_id(唯一) + event_type/source/source_type + 预期字段 + 兑现结果(is_realized/actual_return/drift_direction_correct) + 故事寿命(story_start/peak/end/lifetime_days)
- ✅ `CatalystRealizationTracker` 类：`register_event()`（日历构建时注册）+ `check_and_record_realization()`（每日运行，检测过期事件并计算板块收益）+ `get_realization_stats()`（兑现率统计）+ `get_story_lifetimes()`（故事寿命统计）
- ✅ 催化剂日历集成：`_save_events()` 末尾自动注册到归档表
- ✅ 编排器集成：`run()` 末尾调用 `check_and_record_realization()`
- ✅ 与 AmbushPositionSnapshotRecord 通过 event_id 关联

**四大统计用途**：
1. 按 event_type/source_type/category_tag 分组兑现率
2. 漂移方向预判准确率
3. 各类事件的故事寿命分布（avg/median/min/max）
4. 反向查询/否决/蓄水池通道的独立准确率

**验证结果**：
- ✅ CatalystRealizationRecord 表读写 + 兑现更新正常
- ✅ 统计方法：total=3, realized=2, rate=0.67, direction_accuracy=0.50
- ✅ 故事寿命：earnings avg=10.0天

**P6 文件改动清单**：
- `src/storage.py`（+50 行）：`CatalystRealizationRecord` 表
- `src/got/catalyst_realization.py`（新建 ~280 行）：`CatalystRealizationTracker` 类
- `src/got/catalyst_calendar.py`（+10 行）：`_save_events()` 注册到归档表
- `src/got/orchestrator.py`（+10 行）：`run()` 末尾兑现检测
- `scripts/test_p5_p6.py`（新建 ~220 行）：P5/P6 验证脚本

**催化剂日历瘦身：限售解禁移除（2026-08-25）：**

> 限售解禁是利空风险事件，不应作为催化剂正面信号参与埋伏评分。0824 数据显示 121/242 条事件（50%）为 lockup，30 只股票扇出到 52 个板块，严重稀释 narrative/macro 事件信息密度。且 P5 存在 direction bug（negative 个股事件 score=100 置顶），利空被当成催化剂推荐。

**核心变更**：
- ✅ `_full_build()` 跳过 lockup：`all_stocks = earnings_raw`（消除无效板块映射 IO）+ 删除 lockup 事件生成循环
- ✅ `_build_structured_events()` 防御性早返回：`event_type == "lockup"` → 空列表
- ✅ P5 `_score_catalyst_relevance()` 两个 `if` 改 `elif` 互斥（防重复计分）
- ✅ P5 个股级事件增加 `direction == "negative"` 跳过（利空不置顶）
- ✅ `_fetch_lockup_calendar()` 保留（未来风险通道复用），但不再进入日历
- ✅ 行为变化：个股事件（如财报）不再外溢到同板块其他候选股的关联度分（收紧合理）
- 预期效果：日历事件从 242 降至 121，信息密度提升一倍
- 现有 lockup 记录由 `expires_at` 自然过期（最长 14 天）

**催化剂日历 LLM 叙事回声过滤（2026-08-25）：**

> 0825 数据显示 LLM 叙事事件出现"已发生事件回声"问题：新闻"美股存储光通信板块重挫"（8-25 已发生）被 LLM 映射到 8-29（周六），出现在"未来事件"列表。本质是时态问题（事后描述冒充前瞻性事件），非极性问题。

**核心变更**：
- ✅ A 增强版 prompt：注入今日日期 + 时态判断表格（区分"已发生事件"vs"已发生事件的后续影响"）+ 正反 few-shot 示例
- ✅ D1 回声检测：`_is_echo_event()` — title 与 source_news 互相包含或字符级 Jaccard ≥ 0.6 + direction="negative" → 丢弃
- ✅ D2 周末守卫：`event_date.weekday() >= 5` 直接丢弃（A 股周末不开盘）
- ✅ Schema 扩展：`CatalystEvent` 新增 `source_news` 字段（保留 LLM 输出的来源新闻标题）
- ✅ Storage 扩展：`CatalystEventRecord` 新增 `source_news` 列（DB 迁移需手动 ALTER TABLE）

**验证结果**：
- 单元测试 3/3 通过（周末守卫 + 回声检测 + 相似度阈值）
- 全量重建后 8-29 存储芯片事件不再出现，无周末事件

**文件改动清单**：
- `src/got/schemas.py`：`CatalystEvent` 加 `source_news` 字段
- `src/storage.py`：`CatalystEventRecord` 加 `source_news` 列
- `src/got/catalyst_calendar.py`：Prompt 增强 + `_parse_llm_narrative` 加 D1/D2 过滤 + `_save_events`/`_load_active_events` 同步 source_news
- `scripts/test_catalyst_fix.py`（新建）：3 项单元测试 + 全量重建验证

**Node 1 重设计（2026-07-30）：**

> 基于 Node 6 v2 重跑 Phase 1 暴露的问题

**核心变更**：
- ✅ 砍掉 market_breadth 维度：东财 `stock_zh_a_spot_em` 被反爬封锁 → 4 维权重归一化
- ✅ 新增跨指数一致性（`_compute_cross_index_consistency`）：5 指数方向分类替代 breadth 的"参与度"
- ✅ confidence 三因素加权：跨指数一致性 35% + 时间框架一致性 35% + ADX 强度 30%（替代旧版 `|score|`）
- ✅ 时间框架 mini GOT 改 score 级：矛盾检测从 categorical 比较改为 `|short-long| > 0.3`，加模式分类
- ✅ T-0 实时修正：技术面方向与当日实际反向 → confidence ×0.7
- ✅ LLM reasoning 生成：1 次调用，失败降级到规则拼接
- ✅ score→trend 阈值收窄：±0.5 → ±0.3
- ✅ 数据置信度 vs 信号置信度分离（延续 Node 6 v2 思路）
- ✅ **市场宽度恢复（2026-07-30 下午）**：数据源修复（新浪优先）后，Node 1 加回 `_fetch_market_breadth()` + `_apply_breadth_adjustment()`。技术面与市场宽度方向不一致时 confidence ×0.7。`MarketContext` schema 新增 `market_breadth` 字段

**Node 1 v2 效果**：
- confidence: 0.30 → 1.00（5 指数全向一致 → cross_index_consistency=1.0）
- score: -0.371 → -0.490（砍掉 breadth=0 死重后空头信号增强）
- trend: neutral → bearish（阈值收窄）
- contradiction_type: null → broad_decline（模式分类正确）
- Node 6 sig_conf_avg: 0.50 → 0.64（insufficient_data 门控不再误触发）

**Node 1 v2 已知限制**：
- short/medium 分数完全相同（-0.540 = -0.540）：时间框架差异化不够，实际只有 2 个独立信号
- cross_index_consistency 衡量 MA20 方向一致 ≠ 当日涨跌一致：MA20 全下方但当日风格分化时误报高一致性
- confidence=1.0 可能偏高（三因素全满 + T-0 同向×1.1 触顶）

**Node 1 v2 文件改动清单：**
- `src/got/indicators.py`（修改）：`calculate_all_indicators` 移除 market_breadth，4 维权重归一化
- `src/got/nodes/market_analysis.py`（重写核心方法）：confidence/cross_index/时间框架/T-0/LLM
- `agent_cot_design.md`（更新）：§3.1 设计文档同步

**Node 2 v2 重设计（2026-08-04）：**

> 基于 7 天运行数据（07-27~08-04）暴露的三个核心问题 + 3 个参考项目（sector-rotation-v2, STMS 模型, Athene）

**问题诊断**：
- ❌ `volume_price` 对所有概念板块恒定 0.3（30% 权重报废）— mootdx 板块 K 线的 volume 是指数型加总，波动率低导致 vol_ratio 全部 < 1.2
- ❌ `capital_flow` 全部 clip 到 ±1.0（10% 权重报废）— 概念板块成交额日变化轻松超 5 亿，固定 clip 范围太小
- ❌ 40% 评分权重失效 → 两天分数四舍五入后一模一样
- ❌ 行业板块与概念板块混杂评分，成分股高度重叠（AIGC vs 人工智能 5/6 重叠）
- ❌ `_FRAME_GROUP_KEYWORDS` 硬编码合并粒度不合理（黄金+有色合并、华为系聚合错误）
- ❌ DataQuality 内部追踪但不输出到 reasoning.md，降级不可见

**核心变更**：
- ✅ 砍掉失效维度（volume_price + capital_flow），引入 turnover_expansion + breadth_health
- ✅ 所有维度改为**截面排名百分位**（参考 STMS 模型），自适应牛熊市
- ✅ 行业/概念**双视角分离**：独立评分、独立排名、不混合（参考 sector-rotation-v2）
- ✅ 板块聚合改为**主线家族制**（~10 个家族，关键词自动归类），消除硬编码重叠
- ✅ 降级信息输出到 reasoning.md 顶部（数据质量摘要 + 可信度标记）

**新版评分体系（4 维，全部有效）**：
```
price_momentum     30%  ← 5 日涨幅的截面百分位排名
turnover_expansion 25%  ← 成交额比（5d均值/20d均值）的截面百分位排名
relative_strength  25%  ← 20 日涨幅的截面百分位排名
breadth_health     20%  ← 板块内上涨个股占比的截面百分位排名
```

**主线家族（Level 1，~10 个）**：
AI硬件 / AI软件 / 机器人 / 智能驾驶 / 新能源 / 医药 / 金融 / 周期资源 / 军工 / 消费

**题材地位判定（Level 2）**：
主线核心（score>70 + resonance>0.5）/ 主线扩散 / 独立异动 / 修复观察 / 高位降温

**数据源验证**：
- mootdx `get_board_kline` 支持概念（880xxx）+ 行业（881xxx）✅
- akshare `stock_board_industry_name_em()` 提供行业板块列表 ✅
- breadth_health 依赖 mootdx K 线是否返回 up_count/down_count（待验证）
- 行业板块的 breadth_health 暂缺，用其他 3 维权重归一化降级

**Node 2 v2 文件改动清单**（待执行）：
- `src/got/indicators.py`（+10 行）：新增 `percentile_rank()` 函数
- `src/got/schemas.py`（+20 行）：新增 `ThemeFamily`，`SectorContext` 加 `industry_top`/`theme_families`/`confidence_level` 字段
- `src/got/nodes/sector_analysis.py`（核心重写）：评分体系 + 双视角 + 主线家族 + 降级可见性
- `src/got/archive.py`（+10 行）：reasoning.md 加降级摘要
- `agent_cot_design.md`（更新）：§3.2 设计文档同步

**Node 2 白名单补全（2026-08-07）：**

> 基于用户反馈：元件、电子化学品、半导体等板块未出现在主线中。经系统审查，白名单（CONCEPT_UNIVERSE + TDX_BOARD_REGISTRY）覆盖不全，大量东财活跃概念板块未被追踪。

**问题诊断**：
- ❌ 元件、电子化学品：完全不在白名单中
- ❌ 半导体：虽在注册表但被归入过大（19 个成员）的家族，共振率仅 0.11
- ❌ ~30 个当前热点板块缺失（IGBT、碳化硅、BC电池、PEEK材料等）
- ❌ ~15 个冗余/过时条目（CPO/共封装光学重复、电力物联网过时、MR/AR/VR冗余）
- ❌ ~10 个命名错位（EDA vs EDA概念、6G vs 6G概念、鸿蒙 vs 鸿蒙概念）

**核心变更**：
- ✅ 新增 ~30 个板块（半导体上游/电池技术/新材料/数字经济/新内容/消费等）
- ✅ 剔除 ~15 个冗余/过时条目
- ✅ 修正 ~10 个命名对齐（匹配东财实际板块名）
- ✅ 新增 8 个 category 子分类（electronic_components/power_semiconductor/battery_tech/new_materials/digital_economy/new_content/frontier/consumer）
- ✅ FAMILY_KEYWORDS 同步更新，新增华为生态 + 前沿科技家族
- ✅ _FRAME_GROUP + _FRAME_GROUP_KEYWORDS 同步更新
- ✅ TDX_BOARD_REGISTRY 添加 TODO 注释（~25 个板块代码待查找）

**待完成**：
- [ ] 运行 `scripts/find_missing_board_codes.py` 查找缺失板块的 TDX 代码
- [ ] 填入 TDX_BOARD_REGISTRY 中对应的 TODO 条目
- [ ] 重新运行 Node 2 验证输出

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：CONCEPT_UNIVERSE + FAMILY_KEYWORDS + _CATEGORY_GROUP + _FRAME_GROUP + _FRAME_GROUP_KEYWORDS
- `data_provider/tdx_board_mapping.py`：TDX_BOARD_REGISTRY（添加 TODO 注释）
- `scripts/find_missing_board_codes.py`（新建）：查找缺失板块 TDX 代码的工具脚本

**Node 2 状态机 HI3 移除（2026-08-24）：**

> HI3（大盘暴跌全局拦截）与输出层主线裁剪职责重复，且破坏状态机马尔可夫性——未破位板块（如白银 96.4 分、leading）也被强制踢成 exhausted，回主线需重走 2 天确认。暴跌日的风险控制已统一到 attack_power → 主线裁剪层。

**问题诊断**：
- ❌ HI3 在状态机最前面拦截（`market_crash=True → exhausted_mainline`），让主线裁剪层在暴跌日整体失效
- ❌ 与 8/20 的 floor=1 + 独立行情豁免修复矛盾——同一堵墙上开了个绕过的后门
- ❌ 候选池归零陷阱的第三次变体（Node 2 埋伏闸门归零 → 主线 DDE 全灭 → HI3 全杀）

**核心变更**：
- ✅ 删除 `_apply_state_machine` 的 `market_crash` 参数和分支
- ✅ 删除 `_determine_state_v3` 的 `market_crash` 变量计算
- ✅ 保留主线裁剪层（`attack_power < 0.25 → max_mainline=1`）作为暴跌日唯一防守
- ✅ 保留独立行情豁免（组分数≥90 + S/A≥3 → 豁免裁剪，最多 2 个家族）
- ✅ 暴跌日未破位强者走正常破位判据（broken = 5日涨幅≤0 / 跌破MA20 / ...），未被破位的被裁剪层限制到最多 1 条

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：`_apply_state_machine` 删 `market_crash` 参数+分支 + `_determine_state_v3` 删 `market_crash` 计算

**v3.3.1 硬闸门消除 + 埋伏评分质量修复（2026-08-25）：**

> 基于 plan_v3_hard_gate_elimination.md 的 5 个任务 + 08-24 回归暴露的双轨合并伪并集 + 评分区分度倒挂问题。

**v3.3.1 计划实施（5 个任务）**：
- ✅ P0-1: 埋伏 6 AND 硬闸门 → 6 维加权评分（rs_ratio/rs_momentum/turnaround/accumulation/volume_position/strength_cap）+ 排名截断（Top-K + floor）
- ✅ P0-2: 问财 DDE 双 AND → 单条件 0.8%→0.5%，排序改 DDE，Top 10→15
- ✅ P1-1: Node 4 原始新闻持久化（`node4_raw_news` 表 + title 归一化 + Jaccard 相似度去重 + 跨日发酵检测）
- ✅ P1-2: 蓄水池跨日合并（`_merge_reservoir()` + `days_in_reservoir` 递增 + ≥20 交易日自动除名 + 升格重走闸门）
- ✅ TODO 标记：催化剂置信度/蓄水池过期/升格天数/埋伏退出 4 组参数标记待校准

**08-24 回归修复（双轨合并 + 评分质量）**：

问题诊断：
- ❌ 伪并集：Track1(状态机) 15 个候选被合并后全局排序截断到 Top 8，7+ 个被 Track2 竞选淘汰
- ❌ 评分倒挂：风格板块(昨成交20/基金减仓/高市净率)霸占"越低越好"维度，冷启动 RRG 默认值(rs_ratio=0.00/rs_momentum=0.50)同时拿两维高分
- ❌ 方向约束丢失：旧 AND 闸门"近 3 日不跌"在评分化时未迁移，暴跌板块照样排前列

核心变更：
- ✅ 真并集合并：Track1 无条件保留（上限 AMBUSH_TRACK1_MAX=16），Track2 只补位剩余名额 `max(0, TOP_K - len(T1))`
- ✅ Track1 成员不受 floor 过滤（状态机权威 > 单一截面分）
- ✅ 冷启动惩罚：`strength_trajectory` 不足 5 天 → 线性渐入系数 `len(trajectory)/5.0`，0 天轨迹 → 分数归零
- ✅ 方向惩罚：近 3 日累计跌幅每 1% → 总分打 0.9 折（乘性，下限 0.3），找回"近 3 日不跌"方向约束
- ✅ 噪声过滤增强：新增"昨成交/今日成交/涨幅排名/跌幅排名/换手率排名"关键词
- ✅ Schema 更新：`AmbushReservoirEntry` 新增 `days_in_reservoir` + `entered_strength`；`SectorContext` 新增 `strength_lookup`

验证结果（单元测试 5/5 通过）：
- 冷启动(0天轨迹) → score=0.0000（完全归零）
- 近 3 日暴跌 -5% → score 打 0.61 折（0.9^5≈0.59，符合设计）
- 完美 RRG + 0 天轨迹 → 0.0000（即使 RRG 满分，无轨迹=零分）

文件改动清单：
- `src/got/nodes/sector_analysis.py`：6 维评分常量 + 冷启动惩罚 + 方向惩罚 + 真并集合并 + 噪声过滤增强
- `src/got/nodes/aggregator.py`：蓄水池跨日合并 + 升格重走闸门 + `_run_ambush_gates` 提取
- `src/got/nodes/news_analysis.py`：原始新闻持久化 + 跨日发酵检测
- `src/got/schemas.py`：`AmbushReservoirEntry` 2 字段 + `SectorContext.strength_lookup`
- `src/services/stock_screener.py`：DDE 放松 + 排序改 DDE + Top 15
- `src/storage.py`：`Node4RawNewsRecord` 表
- `src/got/catalyst_config.py`：TODO 标记 + `RESERVOIR_EXPIRY_DAYS`/`RESERVOIR_PROMOTION_MIN_DAYS`
- `src/got/nodes/portfolio_decision.py`：TODO 标记（4 个退出参数）
- `scripts/test_ambush_fix.py`（新建）：5 项验证测试

**埋伏评分修复 + 蓄水池收紧 + 北向豁免（2026-08-25）：**

> 基于 0824/0825 运行回归。半导体强度分从 49.7 崩到 11.2（momentum 从 0.726 崩到 0.106），却被 `_score_turnaround` 反给满分误入 ambush 且锁死出不来（ambush_days 累积到 4）。同时蓄水池无上限膨胀（14→22 条），capital 每天恒定 degraded。

**问题诊断**：
- ❌ turnaround 逻辑反转：`below_count/len(recent)` 把"从未转正"当"刚转正"给满分
- ❌ 状态机 E14 锁死：退出要求 `not is_ambush`，但"越低越好"三维（rs_ratio/turnaround/strength_cap）让崩塌板块 is_ambush 恒 True
- ❌ 蓄水池无上限 + 结转无再评估：14→22 条，低质量条目挂 20 天才走
- ❌ 北向资金 2024-08 起官方停披，每天恒定 degraded 拖累置信度

**核心变更**：
- ✅ P0-1 turnaround 上穿检测：`_score_turnaround` 改检测 `<0.5→≥0.5` 翻转点 + 距今天数衰减（1天=1.0/2天=0.7/3天=0.4），无上穿→0 分
- ✅ P0-2 飞刀驱逐 E12b：ambush 分支新增硬退出 `rs_momentum<0.2 且 strength<25 → neutral`，不依赖 is_ambush 翻假
- ✅ 病因D trajectory 斜率约束：强度轨迹连续 3+ 天单调递减 → 乘性惩罚（0.8/0.6/0.4）
- ✅ P1 蓄水池收紧：`_merge_reservoir` 增加再评估（今日不在近失名单+驻留≥3天→除名）+ 容量上限 `RESERVOIR_MAX_SIZE=15` + 过期 20→10 交易日
- ✅ P2 浮点格式化：`stop_loss`/`risk_reward_ratio` 计算后 `round(,2)`，消除 reasoning 中的 `6.602999999999999` 泄漏
- ✅ P2 日历板块名对齐：`_normalize_sector_name` 双向别名映射（"半导体概念"↔"半导体"等 10 组）+ `_flatten_concept_universe` 双向展开
- ✅ P3 北向永久豁免：`EXEMPT_DIMENSIONS` 集合 + `confidence_multiplier` 分母排除豁免维度（3→2 有效维度）

**验证结果（0825 全量运行）**：
- 半导体：ambush→neutral（ambush_days 归零），落入蓄水池 near_miss(score 0.29<0.3)
- 蓄水池：22→10 条（低于上限 15）
- Capital：北向缺失不再计入，missing_dimensions 从"北向"变为"DDE"（真正缺失项）
- Phase 2：从"误买大为股份 ¥49k"转为"维持空仓"（评分更保守）

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：turnaround 上穿检测 + E12b 飞刀驱逐 + trajectory 斜率约束
- `src/got/nodes/aggregator.py`：蓄水池再评估 + 容量上限 + 调用方传入 near_miss_names
- `src/got/catalyst_config.py`：`RESERVOIR_EXPIRY_DAYS` 20→10 + `RESERVOIR_MAX_SIZE=15`
- `src/got/nodes/portfolio_decision.py`：stop_loss/risk_reward_ratio round(,2)
- `src/got/catalyst_calendar.py`：板块名双向别名 + `_normalize_sector_name`
- `src/got/nodes/capital_analysis.py`：`EXEMPT_DIMENSIONS` + confidence 分母修正
- `src/got/archive.py`：trading_recommendations.md 止损/风险收益比 `.2f` 格式化
- `src/services/stock_screener.py`：P5 催化事件 individual vs sector 互斥 + 利空不置顶

**板块噪声过滤升级 v2（2026-08-25）：**

> TDX 概念板块列表（880xxx）共 ~650 条，混入大量非可交易条目（统计指数/行情事件/财务标签/机构持仓/风格因子/公司财务动作）。旧版 `_is_noise_board` 仅过滤 49 条（7.5%），"创业涨跌/科创涨跌/北证涨跌/昨日突涨/昨成交20"等漏网进入 Node 2 板块分析，污染主线/埋伏判定。

**核心变更**：
- ✅ 全量抓取 TDX 板块列表 652 条逐条分析
- ✅ `_NOISE_BOARD_EXACT` 精确集合：222 条（7 族分类：N1 统计指数 / N2 行情事件 / N3 财务标签 / N4 机构持仓 / N5 风格市值 / N6 公司动作 / N7 边界）
- ✅ `_is_noise_pattern` 语义模式兜底：统计指数族后缀（`{主板|创业|科创|北证|全A|ETF}+{总值|流通|...}`）、事件状态前缀（`昨日|近期|最近|历史|即将|连续|预计`）、TDX 前缀
- ✅ 三层判定：精确集合 + 语义模式 + 动态发现（不写白名单，新产业概念自动进入）
- ✅ 地域板块（32 省）保留
- ✅ 验证结果（652 条真实数据）：噪声 222（34%）/ 保留 430（66%）/ 0 误杀 / 0 漏网

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：`_is_noise_board` 升级 v2（三层判定）+ `_NOISE_BOARD_EXACT` + `_is_noise_pattern`

**Phase 2 问财板块查询修复（2026-08-25）：**

> 0825 全流程 `unified_scoring: []`，Phase 2 选股池 0 只股票。主线A有水产品（98分）、埋伏有汽车芯片（55分），actionable 板块 2 个，但问财初筛全空。

**根因（三层叠加）**：
- ❌ **问词语法错误**：`所属同花顺板块是X` 只匹配行业板块，Node 2 输出的概念板块全部查空。`所属同花顺概念板块是X` 才能匹配概念板块
- ❌ **L2 缓存被股票代码污染**：`akshare.stock_board_concept_name_ths()` 返回列 `['name','code']`，代码 `df.columns[1]` 取到 code 列，缓存的 375 个"板块名"全是股票代码，L2 从未工作
- ❌ **TDX→THS 映射缺口**：水产品在 THS 概念列表中不存在，需跨类别映射到行业板块"养殖业"

**核心变更**：
- ✅ `board_name_resolver.py` 重写：取列修复（按优先级 `name/名称/概念名称/板块名称`）、同时拉取行业板块列表、缓存格式升级（`concept_names` + `industry_names` 分离）、脏缓存检测（>50% 6位纯数字→重建）、新增 `resolve_with_kind()` 返回 `(name, kind)`、`confirm()` 支持 kind 持久化
- ✅ `stock_screener.py` 双语法多轮：按 kind 排序 board_clauses（concept 优先 or industry 优先），第 1 轮带 DDE + 第 2 轮降级无 DDE，每轮按序尝试各 clause 命中即返回。新增 水产品/汽车芯片 行业关键词
- ✅ `portfolio_decision.py` 两处成分股查询同步双语法化
- ✅ `wencai_board_aliases.json` 新增 水产品→养殖业（kind=industry）

**验证结果（0825 数据重放）**：
- 水产品（kind=industry）→ 行业语法命中"养殖业" → 1只（DDE 4.00%）
- 汽车芯片（kind=concept）→ 概念语法命中 → 28只 → 行业过滤 22只 → Top 15
- 宇宙从 0 → 16 只，Phase 2 管道恢复

**文件改动清单**：
- `src/services/board_name_resolver.py`（重写）：取列修复 + 行业列表 + resolve_with_kind + confirm(kind) + 脏缓存检测
- `src/services/stock_screener.py`（核心改动）：双语法多轮查询 + 水产品/汽车芯片行业关键词
- `src/got/nodes/portfolio_decision.py`（两处改动）：成分股查询双语法化
- `data/wencai_board_aliases.json`（+1 条）：水产品→养殖业(kind=industry)
- `data/ths_concept_boards.json`（删除）：旧脏缓存重建
- `tests/test_board_name_resolver.py`（重写 23 用例）：kind 相关 + 列名适配 + 脏缓存检测

**选股漏斗 v2.1 改进（2026-08-25）：**

> 与问财修复同步提交的多项漏斗层改进。

**核心变更**：
- ✅ R4 软保底机制：主通道 Top 6（risk>50）+ 保底至多 2 只（risk>35），仅对 actionable 板块提供保底，防止强势板块垄断全部名额
- ✅ 风险评分梯度化：RSI 从硬阈值(-30)→3 级梯度(>85/-30, >80/-20, >75/-10)；5 日涨幅从 2 级→4 级；波动率从 1 级→2 级
- ✅ 新增风险维度：换手率衰减（近3日/前7日<0.5 → -10）、MA20 偏离度（abs >15%/-15, >10%/-8, >7%/-3）
- ✅ 布林带超卖修正：`boll_pos < 0` 从 +0 → +3（触下轨逆向策略正超额收益，Ni et al. 2020）
- ✅ 横盘过滤校准标记：`_STAGNATION_CALIBRATION_DATE` + `_STAGNATION_SAMPLE_COUNT` 标记回测时效，建议每 3 个月重新校准
- ✅ 资金面数据源标记：`sector_flow_all`(~300 板块) vs `top_concepts`(~8 板块) 区分日志，窄截面时输出警告
- ✅ 问财 DDE 放松轮（v1.5）：主线板块 DDE≥0.5% 仅返回 ≤2 只时，自动放松至 ≥0.3% 补充

**文件改动清单**：
- `src/services/stock_screener.py`：`_apply_r4_filter` + `_score_risk` 梯度化 + 布林带修正 + stagnation 标记 + DDE 放松轮
- `tests/services/test_stock_screener.py`（+323 行）：R4 软保底 5 个场景测试

**历史档系统（2026-07-24）：**
- ✅ `src/got/archive.py`（新建）：每日历史档存档模块
- ✅ `main.py`（修改）：新增 `--got` 入口（`python main.py --got`）
- ✅ `src/got/orchestrator.py`（修改）：`run()` 末尾自动调用 `archive_daily_run()`
- ✅ 目录结构：`data/got_daily/YYYYMMDD/` 包含 `market.json` / `sector.json` / `capital.json` / `news.json` / `sentiment.json` / `unified.json` / `reasoning.md` / `meta.json`
- ✅ 每个 JSON = `ctx.model_dump(mode="json")` 全量存档（含 None 字段，方便分析数据缺失模式）
- ✅ `reasoning.md` 汇总所有 6 个节点的 LLM 推理文本（人读友好）
- ✅ `meta.json` 记录运行元数据（耗时 / 数据质量摘要 / 缺失维度 / LLM 模型 / git commit）
- ✅ 永久保留（单档 ~50KB，一年 ~12MB）
- ✅ `data/got_daily/` 已被 `.gitignore` 的 `/data/` 规则覆盖

**GOT 驱动选股系统（2026-07-29）：**

> 基于每日 Phase 1 GOT 输出，从主线/埋伏板块成分股中精选 Top 2-3 只，用于波段操作（持仓周期约 2 周）

**核心设计**：
- ✅ 动态加载：读取 `data/got_daily/YYYYMMDD/` 下的 6 个节点 JSON，不硬编码任何板块
- ✅ 选股池提取：从 `sector_context` 提取主线（`active_mainline`）+ 埋伏（`ambush`）板块成分股
- ✅ 5 轮漏斗筛选：信号过滤 → 资金面 Top 15 → 波段技术 Top 8 → 风险排雷 Top 5 → 板块分散 Top N
- ✅ 评分体系（消息+资金主导）：资金面 30% + 消息面 20% + 板块强度 20% + 技术面(波段) 15% + 风险因子 15%
- ✅ 波段技术指标：MA20趋势/布林带位置/周线趋势/RSI12/20日突破/MACD金叉/均线多头排列
- ✅ 回测支持：保存买入价/目标价(+10%)/止损价(-7%)到 `data/got_screener/YYYYMMDD.json`，次日可回测胜率

**关键方法**（`src/services/stock_screener.py`）：
- `run_got_mode(got_date, top_n)` — GOT 选股入口
- `_load_got_context(got_date)` — 动态加载 6 个节点 JSON
- `_extract_got_universe(got_context)` — 提取主线/埋伏板块成分股
- `_get_sector_constituents(sector_name)` — 成分股获取（三级降级：问财→东财→空）
- `_filter_got_finalists(universe, got_context, top_n)` — 5 轮漏斗筛选
- `_score_capital()` — 资金面评分（DDE百分位 + 板块资金流百分位）
- `_score_news()` — 消息面评分（LLM 语义匹配 + CONCEPT_UNIVERSE 展开降级）
- `_llm_score_sector_news()` — LLM 调用：事件→板块情感映射
- `_fallback_sector_match()` — 降级方案：CONCEPT_UNIVERSE 展开 + 子串匹配
- `_parse_sector_flow()` — 解析 Node 3 sector_flow 字符串列表
- `_score_technical_wave/risk()` — 技术面/风险评分
- `_compute_got_composite_score()` — 综合评分
- `_generate_got_reasoning()` — 推荐理由生成

**运行脚本**：
```powershell
# 使用今日 GOT 输出选股
python scripts/run_got_screener_v2.py

# 使用指定日期 GOT 输出
python scripts/run_got_screener_v2.py --date 2026-07-29 --top 3

# 次日回测
python scripts/backtest_got_screener.py --date 2026-07-29
```

**文件清单**：
- `src/services/stock_screener.py`（+500 行）：新增 GOT 选股模式（12 个方法）
- `scripts/run_got_screener_v2.py`（新建 ~150 行）：GOT 选股运行脚本
- `scripts/backtest_got_screener.py`（新建 ~200 行）：回测脚本
- `scripts/test_got_data_loading.py`（新建 ~80 行）：数据加载测试

**已知限制**：
- 成分股获取依赖问财/东财，反爬环境下可能失败
- 回测脚本假设次日可获取行情数据，非交易日无法回测

**选股评分重构（2026-08-10）：**

> 修复资金面和消息面评分区分度为零的问题

**资金面 `_score_capital()` 重构**：
- ✅ 砍掉死代码：龙虎榜(+40) — `CapitalEvidence` schema 无此字段
- ✅ 砍掉失效维度：北向资金(2024-08停披)、大宗交易(信号极少)
- ✅ 砍掉废弃参数：`fetcher_manager` 不再传入
- ✅ 新公式：DDE百分位(0-50) + 板块资金流百分位(0-50) = 0-100
- ✅ 新增 `_parse_sector_flow()` 方法：解析 Node 3 的 `sector_flow.top_concepts/industries` 字符串列表（格式 `"板块名(金额亿)"`）
- 效果：分数分布从 0-30 拓宽到 5-47.5

**消息面 `_score_news()` 重构**：
- ✅ LLM 语义匹配：将 Node 4 事件 + `CONCEPT_UNIVERSE`（~100个合法板块名）交给 LLM
- ✅ LLM 输出 `{sector: relevance × sentiment}` 映射，候选股 sector 精确匹配
- ✅ 新增 `_llm_score_sector_news()` 方法：LLM 调用 + JSON 解析
- ✅ 新增 `_fallback_sector_match()` 降级：CONCEPT_UNIVERSE 展开 + 子串匹配
- ✅ 砍掉 `_search_individual_stock_news()` + 关键词列表（依赖 SearchService API Key，匹配粗糙）
- ✅ 砍掉 `_sector_matches()` 静态方法（无调用方）
- ✅ 基础分从 50 降到 40（更保守先验）
- 效果：从全部 50/100 (10/20) → 40-100/100 (8-20/20)，AIGC概念等可命中满分

**Phase 2 架构评审与优化（2026-08-03）：**

> 基于 4 篇论文（FINCON NeurIPS 2024, RAPTOR NeurIPS 2025, FinCoT 2025, LLM-G-DRL 2025）的架构评审

**问题识别**：
- ❌ 原串行设计（持仓判断 → 选股 → 建议）存在循环依赖
- ❌ 候选股筛选条件过宽松（80-100 只），Stage 2 资金面评分耗时 100s+
- ❌ 问财"最低价"条件无意义且有害（限制波动在 0-2%，过滤掉活跃股）

**解决方案**：
- ✅ 采用 FINCON 风格的**统一决策节点**（3 Step 封装在 1 个节点，1 次 LLM 调用）
  - Step 1: 风险评估（约束设定）→ 仓位上限/止损线/板块限制
  - Step 2: 统一打分（持仓 + 候选股一起评）→ 消除循环依赖
  - Step 3: 组合优化（规则 + LLM 混合）→ 在约束下生成最优配置
- ✅ 问财初筛改为 **DDE 策略**（`dde大单净量>=0.8%`，替代宽松的`主力资金净流入>0`）
- ✅ 移除无意义的"最低价"条件，保留 DDE + 上市天数 + 非ST
- ✅ 每板块从 10 只降到 5 只，总候选从 80-100 降到 30-40
- ✅ Stage 2 耗时从 100s 降到 ~50s
- ✅ **板块类型适配 DDE 筛选**（2026-08-03）：
  - 主线板块：`dde>=0.8%`（当天强势，确认主力介入）
  - 埋伏板块：`dde连续3天为正`（持续吸筹信号，不要求当天爆发）
  - 学术依据：DDE 连续性比单日阈值更重要（主力可操纵单日 DDE，但难以连续 3 天伪装）
  - 测试效果：主线板块平均 DDE=4.11%，埋伏板块平均 DDE=0.03%（连续为正）

**文件改动清单**：
- `src/services/stock_screener.py`（修改）：
  - `_get_sector_constituents_filtered()` 改为 DDE 策略
  - 新增板块类型适配：主线用 `dde>=0.8%`，埋伏用 `dde连续3天为正`
- `src/services/ths_screener.py`（修改）：
  - 扩展 `THSStock` 数据模型，增加 `price/change_pct/dde_net_pct` 字段
  - 实现 `_parse_json_api()` 解析问财 JSON API 返回的完整表格数据
- `src/got/nodes/portfolio_decision.py`（新建 ~500 行）：统一决策节点
- `src/got/schemas.py`（+4 个 Pydantic 模型）：Phase 2 Schema
- `src/got/orchestrator.py`（修改）：集成 Phase 2 调用
- `src/got/archive.py`（+2 个方法）：`archive_phase2()` + `load_phase2_decision()`
- `main.py`（修改）：`--got` 入口显示 Phase 2 输出
- `scripts/run_phase2.py`（新建 ~100 行）：独立运行脚本

**学术依据**：
- **FINCON (NeurIPS 2024)**：Manager-Analyst 层级，单一决策点同时处理选股和配置
- **RAPTOR (NeurIPS 2025)**：组合构建是优化问题，不是顺序决策流水线
- **FinCoT (2025)**：CoT 结构必须匹配领域实际工作流
- **LLM-G-DRL (2025)**：CoT 用于感知层（信息处理），RL/规则用于决策层（数值优化）

---

## 数据源架构升级（2025-07-23）

### 背景
push2.eastmoney.com HTTP 接口被东财反爬封锁（RemoteDisconnected），导致 EfinanceFetcher(P0) 和 AkshareFetcher(P1) 的东财源频繁失败。

### 解决方案：组合方案 B+D
- **Phase B**：引入 mootdx（通达信 TCP 7709）作为 P0 数据源 — TCP 协议不受 HTTP 反爬影响
- **Phase D**：重构 GOT 节点统一走 DataFetcherManager + 东财 HTTP 统一限流

### 数据源优先级（升级后）

| 用途 | P0 (TCP) | P0 (HTTP) | P1 | P2 | P3 | P4 |
|------|----------|-----------|----|----|----|-----|
| **K线** | MootdxFetcher | EfinanceFetcher | AkshareFetcher | PytdxFetcher | Baostock | Yfinance |
| **行情** | MootdxFetcher | EfinanceFetcher | AkshareFetcher(EM→Sina→Tencent) | — | — | — |
| **板块** | — | — | AkshareFetcher(via Manager) | — | — | — |
| **资金面** | — | — | AkshareFetcher(via Manager) | — | — | — |

### 关键文件

| 文件 | 作用 |
|------|------|
| `data_provider/mootdx_fetcher.py` | mootdx TCP 数据源（P0，BESTIP 自动探测服务器） |
| `data_provider/em_rate_limiter.py` | 东财 HTTP 统一限流（1s间隔+随机抖动+3次重试+连接池） |
| `data_provider/base.py` | 7 个新接口 + Manager 代理方法 |
| `data_provider/akshare_fetcher.py` | 实现 7 个新方法（北向/龙虎榜/融资融券/大宗/市场宽度/板块K线/板块排名） |
| `src/got/nodes/sector_analysis.py` | Node 2 重构：走 DataFetcherManager |
| `src/got/nodes/capital_analysis.py` | Node 3 重构：走 DataFetcherManager |
| `src/got/nodes/sentiment_analysis.py` | Node 5 重构：市场宽度走 DataFetcherManager |

### 技术亮点
- **TCP vs HTTP**：通达信 TCP 7709 协议与 push2 HTTP 是完全不同的协议栈，不在同一反爬战场
- **mootdx BESTIP**：自动探测最优服务器（带数据验证），解决旧 TDX 服务器 IP 过期问题
- **统一限流**：`em_get()` 入口保证所有东财 HTTP 请求串行化（threading.Lock + 1s间隔 + 抖动）
- **GOT 节点统一**：Node 2/3/5 从直调 akshare 改为走 DataFetcherManager，支持多源自动降级
- **BaseFetcher 扩展**：7 个新可选方法（北向/龙虎榜/融资融券/大宗/市场宽度/板块K线/板块排名）

### 已知限制
- **push2 HTTP 被封**：当前网络环境下 push2/push2his 返回 RemoteDisconnected（东财反爬）
- **mootdx TCP 可用**：TCP 7709 不受 HTTP 反爬影响，K线+行情正常
- **北向资金停披**：2024-08 后 `当日成交净买额` 全 NaN，Node 3 自动降级
- **ETF 申赎无历史**：akshare 仅返回快照，无法计算日度变化
- **雪球热榜保留直调**：Node 5 的雪球 3 榜仍需特定过滤逻辑，暂未走 Manager

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
- **路径级失败**：技术分析路/独立数据路任一完全失败 → 聚合节点报错
- **非交易日跳过**：`trading_calendar.is_market_open()` 判断
- **上下文持久化**：`data/aggregator_contexts/` 按交易日存储 JSON，支持时间一致性校验
