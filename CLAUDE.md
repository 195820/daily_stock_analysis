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
│  GOT 层（市场分析，新）                                          │
│  Phase 1: GOT 六节点（大盘→板块→资金‖消息‖情绪 → 聚合）         │
│  Phase 2: CoT 三节点（持仓→选股→交易建议）                      │
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
│   ├── got/                     # GOT 架构（市场分析，新建）
│   │   ├── nodes/               # 6 个 GOT 节点
│   │   │   ├── market_analysis.py    # Node 1: 大盘（ADX调权/多时间框架 mini GOT）
│   │   │   ├── sector_analysis.py    # Node 2: 板块（启动信号/生命周期/主线分级）
│   │   │   ├── capital_analysis.py   # Node 3: 资金面（5维机构评分/降级机制）
│   │   │   ├── news_analysis.py      # Node 4: 消息面（3并发LLM/多源确认）
│   │   │   ├── sentiment_analysis.py # Node 5: 情绪面（反身性5阶段）
│   │   │   └── aggregator.py         # Node 6: 聚合（10对矛盾/时间一致性）
│   │   ├── schemas.py           # 节点输入输出 Pydantic Schema
│   │   ├── orchestrator.py      # GOT 编排器（并行/串行调度）
│   │   ├── indicators.py        # 纯规则指标函数
│   │   ├── persistence.py       # 上下文持久化（data/aggregator_contexts/，Node 6 时间一致性用）
│   │   ├── archive.py           # 每日历史档（data/got_daily/YYYYMMDD/，全量 ctx + reasoning）
│   │   └── news_feeds.py        # Node 4 新闻采集 v2（新浪/CLS/华尔街见闻/东财/NewsNow 8平台热榜，5 源推送型）
│   │
│   ├── services/                # 核心服务
│   │   ├── technical_indicator.py    # 短线技术指标（需扩展 ADX/MACD）
│   │   ├── sector_rotation.py        # 板块轮动分析
│   │   ├── forum_sentiment.py        # NGA 舆情（后续切雪球）
│   │   ├── stock_screener.py         # 选股流水线（含 LLM AI 评分）
│   │   ├── ths_screener.py           # 同花顺问财
│   │   ├── portfolio_service.py      # 持仓管理
│   │   ├── risk_assessment.py        # 风险评估
│   │   ├── strategy_filter.py        # YAML 策略筛选
│   │   ├── dde_fetcher.py            # DDE 大单净量
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
└── agent_cot_design.md          # GOT 架构设计文档（v0.3）
```

---

## GOT 架构概览

> 设计文档：`agent_cot_design.md`（v0.3）

**核心理念**：前半段 GOT（并行+聚合），后半段 CoT（线性流水线）

### Phase 1: GOT 市场分析

```
独立数据路（并行）          技术分析路（串行）
┌──────────┐ ┌──────────┐   ┌──────────┐ ┌──────────┐ ┌──────────┐
│ 消息面   │ │ 情绪面   │   │ 大盘     │→│ 板块     │→│ 资金面   │
│ Node 4   │ │ Node 5   │   │ Node 1   │ │ Node 2   │ │ Node 3   │
│ 3次LLM   │ │ 反身性   │   │ ADX调权  │ │ 启动分级 │ │ 北向资金 │
│ 多源确认 │ │ 雪球+搜索 │   │ 多时间框 │ │ 生命周期 │ │ 5维评分  │
└──────────┘ └──────────┘   └──────────┘ └──────────┘ └──────────┘
      ↓              ↓              ↓            ↓            ↓
   news_ctx    sentiment_ctx   market_ctx   sector_ctx   capital_ctx
                                                        ↓
                                              ┌──────────────────┐
                                              │   聚合节点 Node 6  │
                                              │  10对矛盾检测     │
                                              │  LLM 批处理       │
                                              │  消息面覆盖       │
                                              │  时间一致性校验   │
                                              └──────────────────┘
                                                        ↓
                                                unified_context
```

### Phase 2: CoT 决策流水线（待实现）

```
持仓判断 → 选股评分 → 交易建议
```

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

> 详见 `agent_cot_design.md` Section 7

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
- `_score_capital/technical_wave/risk()` — 三维度评分
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
- 消息面评分（`news_score`）暂未实现，当前设为 0（权重 20% 空缺）
- 成分股获取依赖问财/东财，反爬环境下可能失败
- 回测脚本假设次日可获取行情数据，非交易日无法回测

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

- **Conda**: `dsa`（Python 3.12.13）
- **数据库**: SQLite `data/stock_analysis.db`（自动建表）
- **配置**: `.env`（LLM API Key、NGA Cookie、东财反爬等）

```powershell
conda activate dsa
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
