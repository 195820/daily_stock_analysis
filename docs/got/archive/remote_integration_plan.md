# 远端优秀模块整合计划
> **原位置**：`docs/archive/got/remote_integration_plan.md`（2026-09-17 文档结构重排移入当前位置）

> **背景**：远端 `origin/main`（`ac8bd29`）比本地领先 200 个 commit，经历了架构级重构。本文档从中筛选可被 GOT 流程消费的优秀模块，制定整合方案。
>
> **日期**：2026-09-01
> **本地基准**：`e96e59a`（v3.5 波次化生命周期）
> **远端基准**：`ac8bd29`
>
> ⚠️ **实施路线图已并入 [`agent_glue_plan_0903.md`](./agent_glue_plan_0903.md) §10（2026-09-03 整合）。**
> 本文仍是各远端模块的**实现细节权威来源**（Tencent 规格 / AlphaSift 策略 / Intelligence API / 指数注册表结构等），
> 但**跨模块的实施顺序、与 Node 6 glue 的冲突裁定、Phase 0 共同地基**以 0903 文档为准。
> 两处「现状问题」描述已被 glue 代码核查推翻并**于本次修订落地**：§14.1 A4（call_type 实为「完全不落库」非「命名不统一」）、
> §14.2 B1（超时真问题是 `call_text` 无 timeout 参数，非 `agent_orchestrator_timeout_s` 默认 0）；另补 §14.1/§14.2 适用边界注、§15.6 限定语。详见 0903 文档 §10.1。

---

## 1. 数据源可用性验证

> 验证脚本：`scripts/verify_remote_data_sources.py`（2026-09-01 16:11 执行）

| 数据源 | 状态 | 延迟 | 备注 |
|--------|------|------|------|
| Tencent K线 (`web.ifzq.gtimg.cn`) | ✅ 可用 | 0.3s | 免费，无需 Key |
| Tencent 行情 (`qt.gtimg.cn`) | ✅ 可用 | 0.2s | 3 大指数实时正常 |
| mootdx TCP | ✅ 可用 | 0.2s | 本地 P0 基线 |
| 东财 push2 列表 (`clist/get`) | ✅ 可用 | 0.2s | total=1640 |
| Yfinance 指数 | ✅ 可用 | 12.3s | 慢，适合末端 fallback |
| TickFlow 包 | ✅ 已安装 v0.1.18 | — | 缺 `TICKFLOW_API_KEY` |
| 东财 push2 单股 (`stock/get`) | ❌ 被封 | — | `RemoteDisconnected` |
| Akshare 指数日线 (`push2his`) | ❌ 被封 | — | 同上 |
| 东财板块排名（概念/行业） | ❌ 被封 | — | 同上 |
| futu-api | ❌ 未安装 | — | 港股场景 |

**结论**：Tencent 端点是当前最稳定的免费补充源，可立即整合。

---

## 2. 整合模块总览

按 **GOT 可消费性 × 实现独立度 × 数据源可用性** 综合排序：

| 优先级 | 模块 | 远端来源 | GOT 消费点 | 实施复杂度 | 依赖 |
|--------|------|---------|-----------|-----------|------|
| **P0** | Tencent 指数 fallback | `data_provider/base.py` + `tencent_fetcher.py` | Node 1 大盘分析 | 低 | 无（已验证可用） |
| **P0** | 指数注册表 + 身份解析 | `src/data/stock_index_loader.py` + `stock_list_parser.py` | 全链路指数身份统一 | 中 | 无 |
| **P1** | MarketThemeContext | `src/services/market_hotspot_service.py` | Node 2 板块阶段增强 | 中 | 需解决板块排名数据源（push2 被封） |
| **P1** | Intelligence 情报系统 | `src/services/intelligence_service.py` | Node 4 消息面升级 | 中 | SQLite 新表 |
| **P2** | AlphaSift 选股引擎 | `src/services/screening/` | 替代 GOT 5 轮漏斗 | 高 | 10 个 YAML 策略 + LLM |
| **P2** | 分歧摘要 + 审议机制 | `src/agent/disagreement.py` + `skills/deliberation.py` | Node 6 T3 仲裁 / Phase 2 | 中 | Agent 框架适配 |
| **P3** | canonical_id 双写 | `src/storage.py` | 全链路数据查询稳定性 | 中 | 数据库迁移 |
| **P3** | TickFlow 数据源 | `data_provider/tickflow_fetcher.py` | Node 1/2/3 数据获取 | 低 | 需申请 API Key |

---

## 3. P0 — Tencent 指数 Fallback

### 3.1 目标

Node 1 大盘分析的 5 指数 K 线获取，当前依赖 mootdx TCP + Akshare。Akshare 的 `push2his` 路径已被封，Tencent 作为稳定补充源加入 fallback 链。

### 3.2 远端设计

远端 `DataFetcherManager` 新增指数专用路由，与个股数据获取分离：

```python
_CN_INDEX_DAILY_SOURCE_ORDER = (
    "TencentFetcher",      # 1. 腾讯直连（免费，已验证可用）
    "AkshareFetcher",      # 2. AkShare（push2his 已封，保留作降级）
    "TickFlowFetcher",     # 3. TickFlow（需 API Key）
    "YfinanceFetcher",     # 4. YFinance（慢，末端兜底）
)

_CN_INDEX_REALTIME_SOURCE_ORDER = (
    ("AkshareFetcher", "tencent"),     # 1. 腾讯 via AkShare
    ("AkshareFetcher", "sina"),        # 2. 新浪
    ("EfinanceFetcher", "index"),      # 3. 东财 secid
    ("TickFlowFetcher", "tickflow"),   # 4. TickFlow
)
```

每个 provider 有专属 symbol 格式：

| Fetcher | SH/SZ 指数 | CSI 指数 |
|---------|-----------|---------|
| Tencent | `sh000001` | 不支持 |
| Akshare | `sh000001` | `csi930955` |
| TickFlow | `000001.SH` | 不支持 |
| Yfinance | `000001.SS` | 不支持 |

### 3.3 TencentFetcher 核心规格

```python
class TencentFetcher(BaseFetcher):
    name = "TencentFetcher"
    priority = 5  # 可通过 TENCENT_PRIORITY 环境变量覆盖

    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _QUOTE_ENDPOINT = "https://qt.gtimg.cn/q=..."
    _HTTP_TIMEOUT_SECONDS = 8
```

**方法**：
- `_fetch_raw_data(stock_code, start_date, end_date)` → DataFrame
- `_normalize_data(df, stock_code)` → STANDARD_COLUMNS
- `get_stock_name(stock_code)` → Optional[str]

**Symbol 转换**（`_to_tencent_symbol`）：
- `600519.SH` / `600519.SS` → `sh600519`
- `000001.SZ` → `sz000001`
- BSE 代码 → `bj{code}`
- 仅支持 6 位 A 股代码

**K 线响应解析**：
- 路径：`payload["data"][symbol]["qfqday"]` 或 `["day"]`
- 每行：`[date, open, close, high, low, volume_lots, amount?]`
- volume 转换：手 → 股（×100）
- 上限 800 bars

### 3.4 实施步骤

1. **新增 `data_provider/tencent_fetcher.py`**
   - 从远端 copy `TencentFetcher` 类（~220 行）
   - 适配本地 `BaseFetcher` 接口（方法签名、能力声明、`priority` 语义）
   - 声明指数数据能力；`_filter_fetchers_for_a_index` 仅跳过 Mootdx/Efinance，Tencent 会被自然保留在指数路由中

2. **修改 `data_provider/base.py`（轻量化）**
   - 仅将 `TencentFetcher` 注册进现有优先级链（`_fetchers` 按 `priority` 排序），优先级置于 mootdx 与 akshare 之间
   - **不引入** `_CN_INDEX_DAILY_SOURCE_ORDER` / `_get_cn_index_daily_data()`，指数路由沿用现有能力过滤路径（`_filter_fetchers_by_capability` + `_filter_fetchers_for_a_index`）
   - 保留现有 `get_daily_data()` 不变（个股路径不受影响）

3. **Node 1 `market_analysis.py` 原则上无需改动**
   - `_get_index_kline()` 走 `get_daily_data()` 自动受益新源，可选增加 fallback 源命中日志

> **事实核查修正（2026-09-01）**：本地 `DataFetcherManager` 与远端架构已分叉（`base.py` 差异 +1651/-466 行）：本地为「优先级排序 + 能力过滤（`_filter_fetchers_by_capability`）+ A 股指数专用过滤（`_filter_fetchers_for_a_index`）」，远端为 `SOURCE_ORDER` 元组路由。**不移植远端路由层**，避免 `base.py` 并存两套路由机制；只移植 `TencentFetcher` 类本体接入本地范式。
>
> 另：本地 `_filter_fetchers_for_a_index` 已对 A 股指数跳过 Mootdx/Efinance，叠加 Akshare `push2his` 被封，Node 1 指数 K 线实际已处于降级状态（仅剩慢速 yfinance 兜底），本项紧迫性高于最初评估。

4. **测试验证**
   - 单元测试：5 个指数 × 250 日 K 线获取（实际消费的 5 个指数均为 SH/SZ 交易所指数，Tencent 全部支持，CSI 缺口与当前消费点无关）
   - 集成测试：Node 1 端到端运行

### 3.5 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `data_provider/tencent_fetcher.py` | 新增 | ~220 行 |
| `data_provider/base.py` | 修改 | ~30 行新增（仅注册） |
| `src/got/nodes/market_analysis.py` | 修改 | ~10 行（可选：fallback 日志） |
| `tests/test_tencent_fetcher.py` | 新增 | ~100 行 |

### 3.6 风险

- Tencent 端点为免费公共服务，无 SLA 保障，可能随时限流或变更
- 前复权 K 线历史上限 800 bars（~3 年），Node 1 需要 250 日绰绰有余
- CSI 指数不支持（`csi930955` 等），后续需 TickFlow 补位

---

## 4. P0 — 指数注册表 + 身份解析

### 4.1 目标

统一指数身份解析。远端建立了 `canonical_id` 体系（`sh000001` vs `sz000001`），消除歧义。

> **事实核查修正（2026-09-01）**：Node 1 的 `INDEX_CONFIG` 已全部使用带交易所后缀的代码（`000300.SH`、`399006.SZ` 等），GOT 指数链路**不存在**裸码传递问题。裸码口径问题实际散落在非 GOT 链路：`src/core/market_profile.py` 的 `mood_index_code="000001"`（语义为指数），而 `src/data/stock_mapping.py` 中 `"000001"` 映射为平安银行（股票），同一代码在不同模块承载双重身份。因此本模块目标修正为：统一 `market_profile` / `stock_mapping` 等散落裸码口径至 `canonical_id`，GOT 指数链路仅为次要受益方。

### 4.2 远端设计

**JSON 注册表**（`apps/dsa-web/public/stocks.index.json`）：

```json
// 每行 10 元素数组:
// [canonical_id, display_code, name_zh, pinyin_full, pinyin_abbr, aliases, market, asset_type, active, popularity]

["sh000001", "sh000001", "上证指数", "shangzhengzhishu", "szzs", ["000001.SH"], "CN", "index", true, 100]
["csi930955", "930955.CSI", "红利低波100", "honglidibo100", "hldb100", [], "CN", "index", true, 100]
["000001.SZ", "000001", "平安银行", "pinganyinhang", "payh", ["平银"], "CN", "stock", true, 100]
```

**核心数据结构**：

```python
@dataclass(frozen=True)
class IndexEntry:
    bare_code: str          # "000001"
    exchange: str           # "SH" / "SZ" / "CSI"
    canonical_id: str       # "sh000001" / "csi930955"
    display_name: str       # "上证指数"
    aliases: Tuple[str, ...]  # ("000001.SH",)

@dataclass(frozen=True)
class AnalysisTarget:
    raw_input: str
    asset_type: str         # "stock" | "index" | "unsupported"
    canonical_id: str       # "sh000001" / "600519.SH"
    exchange: str           # "SH" / "SZ" / "CSI"
    matched_index: Optional[IndexEntry]  # 命中指数条目时有值
```

**入口函数**：`parse_analysis_target(raw_input)` → `AnalysisTarget`

**冲突处理**：`000001` 既是上证指数又是平安银行。裸码默认解析为股票，只有显式前缀（`sh000001` / `000001.SH`）才解析为指数。

### 4.3 实施步骤

1. **提取指数注册表**
   - 从远端 copy `apps/dsa-web/public/stocks.index.json` 中 `asset_type=="index"` 的行
   - 简化为 GOT 专用的小型注册表（~50 个指数）

2. **新增 `src/got/index_registry.py`**
   - `IndexEntry` + `AnalysisTarget` 数据结构
   - `parse_analysis_target()` 解析函数
   - `IndexRegistry` 查找类（by canonical / by prefix / by bare code）

3. **修改裸码消费点**
   - `market_profile.py` 的 `mood_index_code` 等裸码改为经 `parse_analysis_target()` 解析，消除指数/股票双身份
   - Node 1 `market_analysis.py` 的 `INDEX_CONFIG` 已用带后缀代码，仅换成 `canonical_id` 表示（无行为变化）

4. **修改 `data_provider/base.py`（与 P0-Tencent 协同）**
   - 各 fetcher 内部 symbol 转换可查询 `IndexRegistry` 消歧（如裸码经 `parse_analysis_target()` 解析）
   - 指数路由机制本身不变（沿用能力过滤路径，见 §3.4 修正）

### 4.4 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `data/got_index_registry.json` | 新增 | ~50 行 |
| `src/got/index_registry.py` | 新增 | ~200 行 |
| `src/core/market_profile.py` 等裸码消费点 | 修改 | ~20 行（口径统一） |
| `src/got/nodes/market_analysis.py` | 修改 | ~10 行（仅替换为 canonical_id 表示） |
| `data_provider/base.py` | 修改 | 与 P0-Tencent 合并 |

---

## 5. P1 — MarketThemeContext（市场结构上下文）

### 5.1 目标

为 Node 2 板块分析补充**题材阶段**（warming / accelerating / cooling）和**龙头股角色**维度，替代当前纯截面百分位排名。

### 5.2 远端设计

`MarketHotspotService` 构建 `MarketThemeContext`：

```python
class MarketThemeContext(BaseModel):
    status: "ok" | "partial" | "unknown"
    active_themes: List[MarketThemeItem]       # 活跃题材（含阶段+强度）
    leading_industries: List[RankedThemeItem]  # 行业 Top
    leading_concepts: List[RankedThemeItem]    # 概念 Top
    lagging_themes: List[RankedThemeItem]      # 落后题材
    theme_breadth: ThemeBreadth                # 宽度统计
    data_quality: MarketStructureDataQuality   # 数据质量标记

class MarketThemeItem(RankedThemeItem):
    phase: "warming" | "accelerating" | "cooling" | "unknown"
    strength_score: Optional[int]
    reason: Optional[str]
```

**缓存策略**：内存缓存，成功 TTL=60s，失败 TTL=30s，并发保护（BoundedSemaphore=2）。

### 5.3 数据源问题

`MarketHotspotService` 依赖 `fetcher_manager.get_sector_rankings()` / `get_concept_rankings()`，底层走东财 push2 板块排名接口——**当前已被封**。

**解决方案**：
- **方案 A**：用 mootdx TCP 的板块 K 线数据自行计算排名（`get_board_kline` 验证可用）
- **方案 B**：用 Node 2 已有的板块评分数据直接构造 `MarketThemeContext`（零额外 IO）
- **方案 C**：等待 push2 解封或找到替代数据源

> **事实核查修正（2026-09-01）**：方案 B **零信息增益**——Node 6 是 DAG 汇聚点，本就消费 Node 2 的完整输出；将同一份 `SectorContext` 数据重新包装为 `MarketThemeContext` 再交给 Node 6，等于同源数据换格式，交叉验证失去意义，「阶段与状态机不一致时降 confidence」也永远不会触发。
>
> **建议（修订）**：分两步走：
> - **第一步（接口预留）**：仅定义 `MarketThemeContext` 模型并在 Node 6 预留消费点（`status="unknown"` 时不参与评分），**不实现**从 Node 2 数据构造。
> - **第二步（方案 A）**：用 mootdx TCP 板块 K 线（`get_board_kline` 已验证可用）自行计算独立板块排名后接入，才产生真正的独立信号。

### 5.4 GOT 消费点

| Node 2 现有数据 | → | MarketThemeContext 字段 |
|----------------|---|------------------------|
| 板块 composite_score 百分位 | → | `RankedThemeItem.rank` + `change_pct` |
| 状态机 RRG 象限 | → | `MarketThemeItem.phase`（leading→accelerating, weakening→cooling, lagging→warming） |
| 主线家族成员 | → | `active_themes` 列表 |
| DataQuality 追踪 | → | `data_quality` 字段 |

### 5.5 实施步骤

1. **新增 `src/got/market_theme.py`**
   - 定义 `MarketThemeContext` 等 Pydantic 模型（从远端 copy `market_structure.py`）
   - 第一步仅提供模型定义 + `unknown` 状态默认构造；**不实现 `_build_from_sector_context()`**

2. **修改 Node 6 `aggregator.py`（预留消费点）**
   - 预留 `MarketThemeContext` 消费接口，`status != "ok"` 时不参与评分与 confidence 调整
   - 第二步数据接入后：题材阶段与状态机信号不一致时降低 confidence

3. **第二步：方案 A 独立数据源（另行排期）**
   - 用 mootdx `get_board_kline` 自算板块排名，产出 `active_themes` / `phase`
   - 验收：该排名与 Node 2 截面排名相关性显著小于 1（确认为独立信号源）

4. **修改 Archive `archive.py`**
   - `reasoning.md` 新增市场结构摘要段

### 5.6 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/got/market_theme.py` | 新增 | ~150 行 |
| `src/got/schemas.py` | 修改 | ~30 行（或独立 model 文件） |
| `src/got/nodes/aggregator.py` | 修改 | ~40 行 |
| `src/got/archive.py` | 修改 | ~15 行 |

---

## 6. P1 — Intelligence 情报系统

### 6.1 目标

替代 `news_feeds.py` 的 5 源推送采集，提供结构化、可配置、带 retention 的情报摄入。

### 6.2 远端设计

`IntelligenceService` 核心 API：

```python
# 源管理
create_source(payload) → Dict
list_sources(**filters) → Dict
create_default_sources() → Dict                    # 批量创建内建源
fetch_enabled_sources() → Dict                     # 拉取所有启用源

# 情报查询（GOT 消费入口）
list_items(
    scope_type="symbol"|"market"|"sector",
    scope_value="600519.SH",
    market="cn",
    query="半导体",
    days=7,
) → {"items": [...], "total": int}
```

**内建源模板**（A 股相关）：

| template_id | source_type | 说明 |
|-------------|-------------|------|
| `newsnow-cls-hot` | newsnow | 财联社热门 |
| `newsnow-xueqiu-hotstock` | newsnow | 雪球热门股票 |
| `newsnow-weibo` | newsnow | 微博热搜 |
| `newsnow-baidu` | newsnow | 百度热搜 |

**安全特性**：DNS 防护（拒绝私有 IP）、URL 校验、响应限制（2MB）、代理禁用。

### 6.3 与 GOT Node 4 的集成

**当前 Node 4 数据流**：
```
news_feeds.fetch_all_news() → 5 源推送 → 去重 → LLM 事件提取 → NewsContext
```

**整合后数据流**：
```
IntelligenceService.fetch_enabled_sources() → RSS/NewsNow → 持久化到 SQLite
                                                    ↓
Node 4 消费: IntelligenceService.list_items(scope_type="market", market="cn", days=1)
                                                    ↓
                                     LLM 事件提取 → NewsContext（不变）
```

**关键差异**：
- 持久化到 `intelligence_sources` + `intelligence_items` 两张 SQLite 表
- 支持 scope 查询（按 symbol/market/sector 过滤）
- 带 retention 管理（自动清理过期条目）
- NewsNow 源已在远端验证，覆盖 cls-hot / xueqiu-hotstock / weibo / baidu 等

### 6.4 实施步骤

1. **新增 SQLite 表**（`src/storage.py`）
   - `IntelligenceSource`：源配置（type/url/scope/market/enabled）
   - `IntelligenceItem`：情报条目（title/summary/url/source/published_at/scope）

2. **新增 `src/got/intelligence_service.py`**
   - 从远端 copy 核心逻辑（~500 行）
   - 适配本地 `storage.py` ORM

3. **修改 Node 4 `news_analysis.py`**
   - `_fetch_news()` 增加 Intelligence 通道
   - 与现有 5 源并行，合并去重后送 LLM

4. **配置内建 A 股源**
   - NewsNow: cls-hot / xueqiu-hotstock / weibo / baidu
   - RSS: 按需添加

### 6.5 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/storage.py` | 修改 | ~60 行（2 张表） |
| `src/got/intelligence_service.py` | 新增 | ~500 行 |
| `src/got/nodes/news_analysis.py` | 修改 | ~50 行 |
| `data/intelligence_sources/` | 新增 | 内建模板 JSON |

---

## 7. P2 — AlphaSift 选股引擎

### 7.1 目标

用 YAML 策略驱动的选股引擎替代 GOT 的硬编码 5 轮漏斗。保留 GOT 独有能力（催化重排 P5 + Agent 终审 T1）作为后处理。

### 7.2 远端设计

10 个内置策略：

| 策略 | 风格 | 因子侧重 |
|------|------|---------|
| `momentum_quality` | 趋势质量 | momentum 28% + liquidity 18% |
| `dual_low` | 双低 | value + low reversal |
| `oversold_reversal` | 超卖反弹 | reversal + stability |
| `volume_breakout` | 放量突破 | activity + momentum |
| `capital_heat` | 资金热度 | liquidity + activity |
| `shrink_pullback` | 缩量回踩 | stability + reversal |
| `low_volatility_quality` | 低波质量 | stability + value |
| `quality_value` | 质量价值 | value + quality |
| `balanced_alpha` | 均衡 | 均匀分布 |
| `blue_chip_income` | 蓝筹收息 | value + stability |

**Pipeline 流程**：
```
snapshot → hard_filters → daily_enrich → factor_scores → LLM_rank → risk_overlay → portfolio_diversity → post_analysis
```

**策略 YAML 核心结构**：
```yaml
screening:
  hard_filters:          # L1: 绝对门槛
  factor_weights:        # L2: 因子打分权重
  scoring_profile:       # 打分微调
  risk_profile:          # 风险惩罚阈值
  portfolio_profile:     # 组合分散（板块 bucket + 集中度惩罚）
  scorecard_profile:     # L3: 后分析加分
```

### 7.3 与 GOT 选股的整合架构

```
GOT Phase 1 输出 (sector_context)
    ↓
确定 actionable 板块 (主线 + 埋伏)
    ↓
[新增] AlphaSift screen(strategy=auto, context={sector_filter: actionable_sectors})
    ↓  替代 5 轮漏斗 (R0→R4)
    ↓  输出: ScreenResult.picks (含 factor_scores, risk_flags, llm_tags)
    ↓
[保留] P5: 催化剂关联度重排 (仅 ambush 板块)
    ↓
[保留] T1: Agent 终审（已实现但默认关闭，修复与持仓侧扩展见 §15）
    ↓
最终推荐
```

**策略自动选择逻辑**：
```python
def _auto_select_strategy(market_regime, sector_type):
    if market_regime == "risk_on" and sector_type == "mainline":
        return "momentum_quality"
    elif market_regime == "risk_off" and sector_type == "ambush":
        return "oversold_reversal"
    elif "high_volatility" in market_regime:
        return "low_volatility_quality"
    else:
        return "balanced_alpha"
```

### 7.4 实施步骤

1. **copy `src/services/screening/` 子包**（~3000 行）
   - pipeline / filter / scorer / ranker / risk / strategy / snapshot
   - 10 个 YAML 策略文件

2. **适配层 `src/got/screening_adapter.py`**
   - `run_got_screening(sector_context, got_context)` 入口
   - 策略自动选择
   - GOT context → screening context 转换
   - ScreenResult → GOT ScreenerStock 转换

3. **修改 `src/services/stock_screener.py`**
   - `run_got_mode()` 增加策略引擎分支（影子模式先行）
   - 保留原 5 轮漏斗作为 fallback

4. **影子模式验证**
   - `SCREENING_ENGINE_SHADOW=true` 默认开启
   - 并行运行新旧管道，比较输出差异
   - 2 周后评估是否转正
   - **验收标准（强制项，不可只看推荐重合度）**：
     - **候选池归零率**：新管道每日「actionable 板块 → 0 只入选」的发生次数不得高于旧管道（旧漏斗刚完成 DDE 降档/宽进严筛的归零修复，此为回归红线）
     - **near_miss 覆盖率**：对比 near_miss 兜底候选进入最终推荐的触发率；新引擎若无等价兜底机制，须在进影子前先补齐
     - **快照数据源前置**：`fetch_snapshot_with_fallback` 需先接入 mootdx 替代路径（push2 被封），否则影子数据无效

### 7.5 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/services/screening/` | 新增（整个子包） | ~3000 行 |
| `src/services/screening/strategies/*.yaml` | 新增 | 10 个文件 |
| `src/got/screening_adapter.py` | 新增 | ~200 行 |
| `src/services/stock_screener.py` | 修改 | ~50 行 |

### 7.6 风险

- 依赖全市场快照（`fetch_snapshot_with_fallback`），push2 被封时需走 mootdx 替代
- LLM L2 排名增加 LLM 调用成本
- 策略参数需要 A 股回测校准

---

## 8. P2 — 分歧摘要 + 审议机制

### 8.1 目标

用结构化的分歧摘要替代 Node 6 当前的硬编码矛盾对检测，用审议机制增强 Phase 2 的决策质量。

> **事实核查修正（2026-09-01，本地 Agent 模块审计）**：本地 `src/agent/` 已具备冲突聚合基础设施——`SkillAggregator`（信号 1~5 分制加权共识 + 回测胜率自动加权，见 `src/agent/skills/aggregator.py`）与 `AgentOrchestrator`（Technical → Intel → Risk → Decision 多代理 + `AgentOpinion`/`StageResult` 结构化输出）。**不从远端整抄 `disagreement.py`**，改为：以本地 `SkillAggregator` 加权共识为基座，吸收远端的 11 种冲突分类学与 `RiskOverridePlan` 设计，避免造出第三套冲突聚合。另注：GOT 全部节点（24 处 `call_text`）当前仅使用 `src/agent/llm_adapter.py`，执行循环/记忆/技能体系均未接入，本项是 GOT 与 Agent 体系的首次深度对接。

### 8.2 远端设计

**分歧摘要**（`build_agent_disagreement_summary`）：

```python
{
    "bullish_agents": [{"agent_name": str, "signal": str, "confidence": float}],
    "bearish_agents": [...],
    "neutral_agents": [...],
    "conflict_type": str,              # 11 种冲突类型
    "decision_path_hint": str,         # 决策路径建议
    "risk_override_present": bool,
    "risk_control": {...},             # RiskOverridePlan
}
```

**11 种冲突类型**：

| conflict_type | 含义 | decision_path_hint |
|---------------|------|-------------------|
| `risk_override` | 风险覆盖触发 | 优先风控 + 限制买入 |
| `mixed_directional_signals` | 多空分歧 | 解释跨 Agent 冲突 |
| `aligned_bullish` | 一致看多 | 确认价格/风险后执行 |
| `aligned_bearish` | 一致看空 | 保持防守控制 |
| `aligned_neutral` | 一致中性 | 持仓观望 |
| `partial_bullish_with_degraded_inputs` | 看多但数据降级 | 声明数据限制 |
| `degraded_only` | 仅数据降级 | 声明数据限制 |
| ... | ... | ... |

**RiskOverridePlan**：

```python
@dataclass
class RiskOverridePlan:
    veto_buy: bool           # 否决买入
    adjustment: str          # "veto" | "downgrade_one" | "downgrade_two" | ""
    target_signal: str       # 降级后信号
    reason: str              # "high_severity_flag" | "risk_veto" | ...
```

### 8.3 GOT 消费点

**Node 6 聚合层**：
- 当前：10 对硬编码矛盾规则 + LLM 仲裁
- 替换为：`_classify_conflict_type()` 自动分类 → `decision_path_hint` 指导 LLM 精炼

**Phase 2 统一决策**：
- 当前：5 步流水线直接生成
- 增强：在 Step 5 LLM 精炼前注入 `RiskOverridePlan`，确保风控优先

### 8.4 实施步骤

1. **新增 `src/got/disagreement.py`（本地基座 + 远端设计移植）**
   - 复用 `src/agent/protocols.py` 的 `AgentOpinion` / `normalize_decision_signal`，不新造数据结构；各节点信号映射为 `AgentOpinion`（signal + confidence）
   - 聚合逻辑复用 `SkillAggregator` 加权共识模式（信号分制 + 权重归一），权重来源可对接 `AgentMemory` 校准（前置条件见 §14）
   - 从远端移植 `_classify_conflict_type()`（11 类）+ `RiskOverridePlan` 设计，适配 GOT 输入（`UnifiedContext` 中的各节点信号）

2. **修改 Node 6 `aggregator.py`**
   - 矛盾检测阶段改用 `_classify_conflict_type()` 分类
   - `decision_path_hint` 注入 LLM 精炼 prompt

3. **修改 Phase 2 `portfolio_decision.py`**
   - Step 5 前注入 `RiskOverridePlan`，与现有护栏分层确认优先级（风控否决 > 既有降级规则）

### 8.5 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/got/disagreement.py` | 新增 | ~200 行（含本地基座适配） |
| `src/got/nodes/aggregator.py` | 修改 | ~30 行 |
| `src/got/nodes/portfolio_decision.py` | 修改 | ~20 行 |
| `src/agent/skills/aggregator.py` | 只读复用 | 0 行（不修改） |

---

## 9. P3 — canonical_id 双写

### 9.1 目标

`stock_daily` 表新增 `canonical_id` 列，为跨别名聚合打基础。

### 9.2 远端设计

- 新列：`canonical_id` VARCHAR，如 `sh000300` / `sh600519` / `AAPL`
- 指数感知：裸码 `000300` → `sh000300`（指数），而非 `sz000300`（股票）
- 自愈式迁移 + backfill + 双写
- 当前只写不读，后续 PR 切读路径

### 9.3 实施步骤

1. `src/storage.py`：`StockDaily` 模型加 `canonical_id` 列 + 迁移逻辑
2. 数据写入路径：所有 `get_daily_data()` 返回时附带 `canonical_id`
3. GOT 节点：暂时不改，等读路径切换后统一迁移

### 9.4 文件清单

| 文件 | 操作 | 改动量 |
|------|------|--------|
| `src/storage.py` | 修改 | ~80 行 |
| `data_provider/base.py` | 修改 | ~30 行 |

---

## 10. P3 — TickFlow 数据源

### 10.1 目标

作为 A 股数据的第二 P0 源（仅次于 mootdx TCP），补充指数日线 + 板块排名 + 批量预取。

### 10.2 前置条件

- ✅ `tickflow` 包已安装（v0.1.18）
- ❌ `TICKFLOW_API_KEY` 未配置 → 需到 TickFlow 官网申请

### 10.3 实施步骤

1. 申请 API Key，添加到 `.env`
2. 从远端 copy `data_provider/tickflow_fetcher.py`（~1200 行）
3. 注册到 `DataFetcherManager` 优先级链
4. 验证指数日线 + 实时行情 + 板块排名

---

## 11. 实施路线图

```
Week 1: P0 数据源层
├─ Day 1-2: TencentFetcher + 指数 fallback 链
├─ Day 3:   指数注册表 + AnalysisTarget
└─ Day 4-5: Node 1 集成 + 测试验证

Week 2: P1 Node 增强
├─ Day 1-2: MarketThemeContext（接口预留；方案 A mootdx 独立排名另行排期）
├─ Day 3-5: Intelligence 情报系统 + Node 4 集成
└─ 验收: 全流程 GOT 运行 + 归档对比

Week 3-4: P2 架构增强（影子模式）
├─ Week 3: AlphaSift 选股引擎 copy + 适配层
│   └─ 影子模式: 新旧管道并行运行
├─ Week 4: 分歧摘要 + 审议机制（本地 SkillAggregator 基座）
│   ├─ 前置: §14.1 A 类修复（AgentMemory 校准/回填/用量归因）
│   └─ 影子模式: 矛盾检测对比
└─ 验收: 2 周影子数据评估

Week 5+: P3 基础设施 + 个股审查层
├─ canonical_id 双写
├─ TickFlow（待 API Key）
├─ §15 个股 Agent 审查层: 共用基座 + T1 修复（影子）+ T1-H 持仓审查
└─ 持续校准
```

---

## 12. 风险评估

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|---------|
| Tencent 端点限流/变更 | P0 fallback 失效 | 中 | mootdx TCP 仍为主力，Tencent 仅作补充 |
| push2 持续被封 | MarketHotspotService 缺数据 | 高 | 第一步接口预留（不受缺数据影响）；第二步方案 A 用 mootdx `get_board_kline` 自算排名 |
| AlphaSift 选股参数未校准 | 推荐质量下降 | 中 | 影子模式先行，以候选池归零率 + near_miss 覆盖率为强制验收指标，2 周评估后再转正 |
| 远端模块与本地框架不兼容 | 集成失败 | 低 | 适配层隔离，不修改 GOT 核心逻辑 |
| TickFlow API Key 申请失败 | P3 无法实施 | 低 | 不影响 P0/P1/P2 |

---

## 13. 不整合的模块

| 模块 | 原因 |
|------|------|
| Futu OpenD (`futu_fetcher.py`) | 港股场景，GOT 当前不涉及 |
| GenerationBackend 抽象层 | Web 生成后端，与 GOT 无关 |
| Web/Dashboard 前端变更 | ~80 个 commit，纯 UX |
| 多市场支持（JP/KR/TW） | GOT 聚焦 A 股 |
| Decision Signal 生命周期 | P6 已覆盖类似功能，待评估后再决定 |
| Codex/OpenCode 生成后端 | 与 GOT 无关 |
| 远端 `disagreement.py` 整体 | 与本地 `SkillAggregator`/`AgentOrchestrator` 重叠，仅吸收其冲突分类学与 `RiskOverridePlan` 设计（见 §8 修正） |

---

## 14. 本地 Agent 模块配套修复清单（2026-09-01 审计新增）

> **来源**：对 `src/agent/` 全模块只读审计。这些修复分两类：**A 类**是 P2 分歧摘要（§8）及后续 GOT 深度对接的前置条件；**B 类**是独立改进项，可单独排期。
>
> 审计基线事实：GOT 全部节点（24 处）仅使用 `src/agent/llm_adapter.py` 的 `call_text`，执行循环（`runner.py`）、记忆（`memory.py`）、技能聚合（`skills/`）、编排（`orchestrator.py`）均未接入 GOT。

### 14.1 A 类：GOT 对接前置（必须在 §8 实施前完成）

> **适用边界〔0903 核查修订，详见 0903 文档 §6.3 R4 / §10.1 B-1〕**：A1/A2/A3 针对 `AgentMemory`（multi 架构），**仅是本节 §8 分歧摘要的前置**；Node 6 glue 走**单次 `call_text`**、不触及 `AgentMemory`，故 **A1/A2/A3 不阻塞 glue**。唯 **A4 是 glue 与 §8/§15.6 的共同前置，已提前到 Phase 0**。

| # | 修复项 | 现状问题 | 修法 |
|---|--------|---------|------|
| A1 | `AgentMemory` 校准分母伪造 | `_get_accuracy_stats` 中 `avg_confidence` 硬编码 `0.6`（注释自认 approximate），导致 `calibration_factor = accuracy/0.6` 系统性偏差 | 改为真实统计或移除该因子；修复前校准数据**不可供 GOT 消费** |
| A2 | 记忆结果回填链路缺失 | `AnalysisMemoryEntry.outcome_5d/20d/was_correct` 恒为 `None`，无后台回填任务，"从历史学习"闭环只完成一半 | 新增回填任务（可复用 GOT 兑现率数据源 `catalyst_realization.py` 的思路） |
| A3 | 单代理路径不消费记忆 | 记忆注入与校准仅在 `BaseAgent`（multi 架构），默认架构 `single` 下全部休眠 | 明确架构选型：若 GOT 对接走 multi 架构则自然解决；否则在 executor 路径接入 |
| A4 | `call_type` 用量**完全不落库**〔0903 核查修订〕 | 原述「24 处各自随意传 `call_type`」**有误**：`call_text`（`llm_adapter.py` L486-494）**无 `call_type` 形参**，`src/got/` 下 `persist_llm_usage` **0 处调用** → GOT 用量完全不落库（非命名不统一） | **适配器层加落库**：`_call_llm_async`（L3826-3855）加 `call_type` 形参并调 `persist_llm_usage(response.usage, model, call_type=...)`，按 `got_node{n}_{stage}` 命名，复用 `record_llm_usage`/`get_llm_usage_summary`（L3316/L3337，by_call_type 聚合 L3363-3391）。**glue 与 §8/§15.6 共同前置 → 提前到 Phase 0** |

### 14.2 B 类：独立改进（可单独排期）

> **排期修订〔0903，详见 §10.1 C 类〕**：**B1（超时）+ B2（temperature）是 glue 与 §15 审查层的共同地基，已提前到 Phase 0**，不再「可单独排期」；B3-B8 仍可单独排期。B3 另需补「筛选≠截断」区分（glue §6.3 R2）。

| # | 修复项 | 现状问题 | 修法 | 优先级 |
|---|--------|---------|------|--------|
| B1 | 超时**未透传到底层调用**〔0903 核查修订〕 | 原述「`agent_orchestrator_timeout_s` 默认 0」**有误**：该值 config 默认 **600**，但 single 架构不读它；**真问题**是 `call_text` **无 `timeout` 形参**，`_call_llm_async` 经 `asyncio.to_thread(call_text)` 未传超时 → 断网挂死 | `call_text` 加 `timeout` 形参，`_call_llm_async` 透传；超时返回 `""` 触发降级。**glue F1 与 §15.2 共同前置 → 提前到 Phase 0** | 高 |
| B2 | 分析路径 temperature 未设 | `call_with_tools` 不传 temperature，用供应商默认值，结果跨运行波动（GOT 的 `call_text` 倒是都显式传 0.3） | 适配器支持传参，分析路径设 0.2~0.3 | 高 |
| B3 | 工具结果无大小上限 | `serialize_tool_result` 不截断，与 `base_agent` 的 8000 字符策略不一致，大 K 线 JSON 撑爆上下文 | 统一截断策略 | 中 |
| B4 | 仪表盘解析失败无重试 | "agent weak integrity: placeholder fill only, no LLM retry"，单次解析失败即占位补全 | 加一次"仅补缺失字段"的低成本修复调用 | 中 |
| B5 | 双轨 Prompt 维护负担 | `LEGACY_DEFAULT_*` 与新版 Prompt 约 95% 重复（含 ~150 行 JSON schema），4 处模板易漂移 | 提取公共 schema 模板，删除 LEGACY 轨 | 低 |
| B6 | 阶段顺序靠 Prompt 约束 | "禁止合并阶段"是概率性约束，而 `run_agent_loop` 本身支持并行工具批 | 改为代码侧分阶段供给工具声明（呼应"确定性代码兜底"原则） | 低 |
| B7 | 上下文压缩只覆盖 chat | `agent_context_compression_enabled` 默认关，且只作用于可见历史，分析路径工具结果不受控 | 扩展压缩范围至分析路径 | 低 |
| B8 | stock_scope 护栏只在 chat 路径 | 分析路径（`run()`）无越权代码防护 | 分析路径接入 `resolve_stock_scope` | 低 |

### 14.3 可复用性三分类（供后续整合引用）

| 分类 | 模块 |
|------|------|
| **可直接复用** | `LLMToolAdapter`（GOT 已在用）、`run_agent_loop` 执行循环、`parse_dashboard_json`/`try_parse_json`、`protocols.py` 数据结构 + `normalize_decision_signal`、factory 缓存模式 |
| **需适配后复用** | `AgentMemory` 校准/技能自动加权（待 A1/A2 修复）、`AgentOrchestrator` 分档模式（适配 GOT T1 仲裁分级调用）、`SkillAggregator` 加权共识（§8 基座）、后置确定性护栏链（`stabilize/guardrail/placeholder-fill` 三段式）、`ToolRegistry` 工具子集过滤 |
| **仅作设计参考** | 多代理意见/冲突结构化（与远端 `disagreement.py` 合并评估）、`calibration_factor` 公式（实现有缺陷）、provider_trace 逐轮持久化（理念可用于 GOT 归档审计） |
| **反面教材** | LEGACY/新双轨 Prompt 开关，新模块不要复制该模式 |

---

## 15. 个股 Agent 审查层：T1 修复 + T1-H 持仓新增（2026-09-02 新增）

> **动机**：为持仓股与候选股再加一层 Agent 分析。事实核查：候选股侧 T1 Agent 终审**已实现但默认关闭**（`stock_screener._agent_final_review`，开关 `AGENT_STOCK_REVIEW_ENABLED=false`，预算 `AGENT_STOCK_REVIEW_MAX=2`）；持仓侧无逐股 Agent 层（Phase 2 持仓为纯规则打分 `_score_holdings` + 一次全局 `_llm_refinement`）。本节是 §7.3 “[保留] T1: Agent 终审”的实体化方案，并向持仓侧扩展。

### 15.1 现状与缺陷（候选股侧 T1）

现有设计（保留）：走 `build_agent_executor()` 的完整工具 ReAct Agent；只有否决权（reject 不删除、降级移末位并标注）；串行 + 空响应重试 3 次；失败降级 warn 等同现状。

三处缺陷（至今未敢开启的根因）：

| # | 缺陷 | 后果 |
|---|------|------|
| D1 | `executor.run(prompt)` 复用决策仪表盘系统提示词（要求 dashboard JSON 输出），与审查任务 `{"verdict","reasoning"}` 输出格式冲突 | 输出不稳定，靠模型违背系统提示完成任务 |
| D2 | `_parse_agent_verdict` 用正则 `\{[^}]+\}` 提取 JSON，不支持嵌套 | reasoning 含 `{` 即解析失败静默降级 warn |
| D3 | 完整 executor = 4 阶段工具流 + 最多 10 步，单次审查 ≈ 4~6 次 LLM 调用；且 §14.2 B1（超时）/B2（temperature）未修 | 成本与稳定性都不可控 |

### 15.2 共用基座（先修再开）

1. **review 专用 profile**：factory 新增轻量构建路径——审查专用系统提示词（输出仅 verdict/reasoning/risk_flags）+ 工具子集（`get_realtime_quote`、`get_daily_history`、`search_stock_news`，复用 `_filtered_registry` 机制），步数上限 4。解决 D1/D3。
2. **解析换用 `try_parse_json`**（runner 现成四策略解析器，§14.3 可直接复用项）。解决 D2。
3. **前置依赖**：§14.2 B1（超时默认开启）+ B2（temperature 0.2）。

### 15.3 候选股侧：T1 修复 → 影子 → 转正

- 按 §15.2 基座改造 `_agent_final_review`（保留只有否决权与预算截断设计）
- **影子模式先行**：跑审查、记录 verdict，但不执行降级；2 周后用兑现数据（5 日收益）对比 reject 命中率，达标再让否决权生效
- verdict/outcome 数据同时作为 §14.1 A2 记忆回填的天然数据源

### 15.4 持仓侧：新增 T1-H（Phase 2）

| 维度 | 设计 |
|------|------|
| 位置 | `PortfolioDecisionNode.run` Step 2 `_score_holdings` 之后、Step 3 分配之前 |
| 预算 | 最多审 2~3 只；优先级：埋伏持仓 > 有 P4 退出信号 > 浮亏超阈值 |
| 输入 | 持仓快照（成本价/数量/P4 催化剂快照）+ 统一打分结果 + 买入以来新闻 |
| 输出 | verdict ∈ `hold / add / reduce / exit` + reasoning + confidence |
| 权限边界 | 遵守“规则守决策、Agent 做证据收集与语义仲裁”：Agent 只有降档权（reduce/exit 标注 + 打分惩罚系数），仓位计算与最终分配仍由规则层完成；`confidence < 0.6` 忽略（与 T3 仲裁一致） |
| 降级 | 调用失败/解析失败 → hold（等同现状，R4 原则） |

### 15.5 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `src/agent/factory.py` | 新增 review 专用 profile 构建路径（~60 行） |
| 修改 | `src/services/stock_screener.py` | `_agent_final_review` 改用 review profile + `try_parse_json` + 影子开关（~40 行） |
| 修改 | `src/got/nodes/portfolio_decision.py` | 新增 `_agent_holding_review`（T1-H）接入 Step 2/3（~120 行） |
| 修改 | `src/got/catalyst_config.py` | 新增 `AGENT_HOLDING_REVIEW_ENABLED` / `AGENT_HOLDING_REVIEW_MAX` / 影子开关（~10 行） |
| 修改 | `src/config.py` | review profile 超时/max_steps/temperature 默认值（~10 行） |

### 15.6 验收标准

- T1 影子期：verdict JSON 解析成功率 ≥ 95%（消除 D2 静默失效）；reject 命中率显著高于随机基线后否决权才生效
- T1-H：Agent 缺席时持仓降档数量为 0（护栏：无审查则不惩罚）；Phase 2 总耗时不增加（预算截断生效）
- 成本：**审查层（仅 T1/T1-H，不含 Node 6 glue）**每日 LLM 调用总数 ≤ 20（预算截断兜底，call_type 按 §14.1 A4 **落库**归因）。〔0903 核查修订，详见 §10.1 B-2〕Node 6 glue 是单次全量调用，成本另计（0903 文档 §5.4），**不受本条 ≤20 约束**
