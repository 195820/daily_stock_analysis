# GOT 0902 全链路审核报告

> 审核日期：2026-09-02  
> 审核范围：`data/got_daily/20260902/` 全部 10 个输出文件  
> 审核方法：数据交叉比对 + 代码逻辑追踪（aggregator.py / sector_analysis.py / portfolio_decision.py / sentiment_analysis.py / archive.py）

---

## 一、问题总览

| 级别 | 数量 | 说明 |
|------|------|------|
| 🔴 P0 | 5 | 影响决策正确性，需立即修复 |
| 🟡 P1 | 5 | 影响分析质量，需排期修复 |
| ⚪ P2 | 5 | 设计缺陷/数据质量，可优化 |

> **2026-09-02 复核**：P0-3 / P0-4 的根因与修复方案已按代码实测结果修订，
> 并新增 P2-5（破位信号不落盘）。修订处均以「修订记录」标注。

---

## 二、P0 级问题（影响决策正确性）

### P0-1：`capital_check` 的 "divergent" 值从未被生产

**现象**：全部 20 个确认板块的 `capital_check` 均为 `"neutral"`，即使银行净流出 7.4 亿、粮食概念净流出 30.1 亿。

**代码追踪**：

```
文件：src/got/nodes/aggregator.py
行 870-914 _cross_check_capital()
```

| 条件 | 返回值 | 实际生产? |
|------|--------|----------|
| 净流入 > 0 亿 | `"aligned"` | ✅ 有 |
| 净流出 / 不在 top | `"neutral"` | ✅ 有 |
| 资金面明确背离 | `"divergent"` | ❌ **从未 return** |

代码消费端有 4 处检查 `"divergent"`（行 768、1168、2909、3118），但生产端没有任何路径返回此值。`"divergent"` 是死代码分支。

**影响**：
- 板块大额净流出时，`capital_check` 仍为 `"neutral"`，不触发任何惩罚
- 续航评估（行 2905-2909）中 `capital_checks` 永远不会出现 `"divergent"` → `capital_stance` 永远不可能为 `"rejected"`
- 结果：所有板块的 `capital_stance` 只可能是 `"passed"` 或 `"missing"`，资金面验证形同虚设

**修复建议**：在 `_cross_check_capital` 中增加净流出阈值判断：
```python
if amount < -50:  # 净流出超过50亿
    return "divergent", f"{name}大幅净流出{amount:.1f}亿"
```

#### 实施修订：绝对阈值 -50 亿在普跌日灾难性误伤，改为全市场分位自适应

上表建议的 -50 亿阈值是未校准的拍定值。实施前跨 6 日实测其触发面：

| 日期 | 全量板块 | 命中 `<-50亿` | 全市场水位（板块中位净流出） |
|------|---------|--------------|------------------------|
| 2026-08-26 | 409 | 1 | -0.4 亿 |
| 2026-08-27 | 387 | 1 | +8.9 亿 |
| 2026-08-28 | 364 | 36 | -3.9 亿 |
| 2026-08-31 | 477 | 6 | +0.2 亿 |
| 2026-09-01 | 477 | **89** | -6.1 亿 |
| 2026-09-02 | 373 | **53** | -12.2 亿 |

0902 全市场大单净流出 255.57 亿、板块中位流出 12.2 亿时，**53/373 = 14% 的板块
会被判 divergent**，触发 `composite -= 15` 与 `capital_stance="rejected"`。
`divergent` 的语义是"该板块资金面背离于大市"，而普跌日的大额流出是**常态**，
不是背离。绝对金额同时编码了「板块体量」与「市场水位」两个无关因子，
与 P0-5 吸筹闸门"一刀切"属同型缺陷。

**实际实施方案**（`aggregator.py`）：改为随市场水位自适应的相对判据 ——
板块净流出落在当日全市场最差 5% 分位才算背离，样本不足（<60 板块）时回退 -80 亿兜底。

```python
_CAPITAL_DIVERGENT_TAIL_PCT = 0.05       # 全市场最差 5% 视为背离
_CAPITAL_DIVERGENT_MIN_SAMPLES = 60      # 分位判定所需最小板块样本数
_CAPITAL_DIVERGENT_FALLBACK_YI = -80.0   # 样本不足时的绝对兜底阈值（亿）
```

跨日命中数对比（自适应后平静日归零、普跌日收敛到真异常）：

| 日期 | 旧（绝对 -50亿） | 新（最差 5% 分位） | 自适应阈值 |
|------|-----------------|------------------|-----------|
| 2026-08-26 | 1 | 0 | -80.0（回退） |
| 2026-08-27 | 1 | 0 | -80.0（回退） |
| 2026-08-28 | 36 | 17 | -80.0（回退） |
| 2026-08-31 | 6 | 1 | -80.0（回退） |
| 2026-09-01 | 89 | 23 | -163.4 |
| 2026-09-02 | 53 | 18 | -91.3 |

0902 实盘验证：深股通(-382.9亿)、芯片概念(-167.8亿) → `divergent`；
银行(-7.4亿)、粮食概念(-30.1亿) → `neutral`（旧方案下粮食概念会被误判为背离）。

---

### P0-2：板块置信度隐式硬编码为 0.7

**现象**：20 个 confirmed_sectors 的 `confidence` 字段全部为 0.7，无一例外。

**代码追踪**：

```
文件：src/got/nodes/aggregator.py
行 790-800 _compute_sector_scores()
```

```python
confidence = 0.5
if detail:                        # 有 Node 2 数据
    confidence += 0.2             # → 0.7
if capital_check != "neutral":    # 资金面有信号
    confidence += 0.1             # → 0.8
if news_check != "neutral":       # 消息面有信号
    confidence += 0.1             # → 0.9
if sentiment_check != "low":      # 情绪面有信号
    confidence += 0.1             # → 1.0
```

当三个 cross-check 全部为中性（P0-1 的必然结果），confidence = 0.5 + 0.2 = **0.7**。

**影响**：
- 置信度丧失区分度：旅游（98 分）和建材（30 分）的 confidence 都是 0.7
- Node 6 的 `overall_confidence = 0.86` 来自 `avg(0.7) × 0.8 + 0.1(bear bonus) + 0.2 = 0.86`
- 用户看到的"置信度 86%"实际上只反映"有 Node 2 数据"这一个维度

**修复建议**：
1. 提升基础分：有 Node 2 数据时从 +0.2 改为 +0.3
2. 增加强度维度：strength_score > 80 时 +0.05，exit_pressure > 0 时 -0.05
3. 接入 Node 1 confidence 作为上限：`min(node1_confidence, computed_confidence)`

---

### P0-3：粮食概念 today_change=-4.26%、breadth_health=0.133，仍为 active_mainline A-tier

**现象**：粮食概念当天暴跌 4.26%，仅 13.3% 成分股跟涨，趋势下行，已有 2 天退出压力，但状态仍为 `active_mainline`。

**代码追踪**：

```
文件：src/got/nodes/sector_analysis.py
行 2237-2243 broken 判据
行 2262-2275 active_mainline 转换逻辑
行 2359-2383 _compute_exit_pressure()
```

#### 修订记录：原分析结论错误

原报告判断"exit_pressure=2 < 3，降低 A-tier 容忍度即可修复"。**此结论错误，且按此修复无效。**

真实根因在 `_compute_exit_pressure()` 的归零逻辑：

```python
# sector_analysis.py:2372-2383
if strength_score < 50:
    pressure = prev_exit_pressure + 1   # 累积
else:
    pressure = 0                        # ← strength >= 50 时先归零
if today_change < -3.0:
    pressure += 2                       # 单日跌幅惩罚上限 = +2
elif today_change < -1.5:
    pressure += 1
```

**计数器在 `strength >= 50` 时跨日无法累积**，而单日跌幅惩罚上限为 +2，因此
**S/A-tier 板块（容忍度 3）的 exit_pressure 上限恒为 2，驱逐路径结构性不可达。**

实测全库 518 个 `active_mainline` sector-day：

| exit_pressure | sector-day 数 |
|---------------|--------------|
| 0 | 475 |
| 1 | 26 |
| 2 | 17 |
| **≥ 3** | **0** |

结论：不是"连续 3 天没凑够"，而是**凑不满**。粮食概念即使连续十天 -4%，
只要 strength_score 保持在 50 以上（当前 82.8），计数器就一直在 `0 → 2 → 0 → 2` 之间跳动，
永远不会触发 A-tier 驱逐。降低容忍度到 2 天虽能让它触发，但会连带误伤所有
单日跌幅 1.5%-3% 的正常回调板块（该区间 exit_pressure=1，容忍度降为 2 后仍安全，
但叠加两次即被误杀），属于用错误机制打补丁。

#### 补充：breadth_health 是截面百分位，非原始占比

原报告建议用 `breadth_health < 0.15` 作辅助判据。需注意该字段经
`_apply_cross_sectional_ranking()`（行 1547-1581）转为**全市场截面百分位**：

| 板块 | breadth_health |
|------|---------------|
| 渔业 / 焦炭加工 / 林业 | 0.006 |
| 煤炭 | 0.022 |
| 智能穿戴（中位） | 0.536 |
| 旅游景点 | 0.994 |

96 个板块中位数 0.536，粮食概念的 0.133 约排第 22 位，**并非极端值**。
该信号偏弱，不建议作为独立的破位判据。

#### 修订后的修复方案

在 `broken` 判据的 OR 链中增加单日暴跌触发器（行 2238-2243）：

```python
broken = bool(
    (price_chg_5d is not None and price_chg_5d <= 0)
    or (not above_ma20)
    or (overheat_level == "exhausted" and high20_pos < 0.60)
    or (score_drop >= 20)
    or (today_change is not None and today_change < -4.0)   # 新增
)
```

**阈值校准**（0902 全市场 96 板块实测，`today_change < -4%` 仅命中 2 个）：

| 板块 | today_change | 当前状态 |
|------|-------------|---------|
| 粮食概念 | -4.26% | active_mainline ← 应触发 |
| 种植业 | -7.70% | exhausted_mainline（已在位） |

其余池（ambush 10 / near_miss 5 / defensive 2 / cooling 5 / top_by_score 5 /
industry_top 10）均无命中，**不误伤**。触发后走 E3 破位（行 2264-2265）当天踢出。

#### 实施修订（二）：单日校准样本不足，跨 29 日回放发现主线误伤

上表结论基于 0902 单日。实施后跨 **29 个归档交易日 / 930 个 sector-day** 回放校准，
发现 `active_mainline` 池中有 3 次命中，其中 2 次是真实主线的深回调：

| 日期 | 板块 | today_change | 5 日涨幅 | 性质 |
|------|------|-------------|---------|------|
| 2026-08-11 | 黄金 | -5.88% | **+4.1%** | 主线深回调，趋势未破 |
| 2026-08-21 | 种业 | -7.14% | **+1.2%** | 主线深回调，趋势未破 |
| 2026-09-02 | 粮食概念 | -4.26% | **-8.3%** | 趋势下行，应驱逐 |

若按上表方案并入 `broken`，黄金与种业会走 E3 **当天踢出**——
跳过 `high_position_cooling` 缓冲，把正常回调误杀为衰竭。
单日暴跌本身不能证明趋势终结，需趋势佐证。

**实际实施方案**（`sector_analysis.py`）：单日暴跌**不并入** `broken`，
在主线分支单列分级处理：

```python
one_day_drop = today_change < _BROKEN_ONE_DAY_DROP_PCT          # < -4.0%
trend_damaged = (price_chg_5d <= 0) or (not above_ma20)         # 趋势佐证

if prev_state == "active_mainline":
    if one_day_drop and trend_damaged:
        return "exhausted_mainline"      # 暴跌+趋势破坏 → 当天驱逐
    if one_day_drop:
        return "high_position_cooling"   # 暴跌但趋势尚存 → 降温观察
    if broken:
        return "exhausted_mainline"      # E3 原有破位路径
```

降温后若在 `high_position_cooling` 再次破位，由原 E7 分支转 exhausted，
驱逐路径仍然可达（原缺陷已修复），但给了真实回调一天的缓冲。

回放验证区分度：

| 板块 | today_change | 5 日涨幅 | 旧方案 | 新方案 |
|------|-------------|---------|--------|--------|
| 粮食概念 | -4.26% | -8.3% | exhausted | **exhausted_mainline**（驱逐） |
| 黄金(0811) | -5.88% | +4.1% | exhausted | **high_position_cooling**（观察） |
| 种业(0821) | -7.14% | +1.2% | exhausted | **high_position_cooling**（观察） |

> 注：校准样本中 `科创涨跌 -53.71%`、`北证涨跌 -65.23%`、`成交均价 -6.34%` 等
> 极端值已在 `_NOISE_BOARD_EXACT` 中被过滤，实测 100 个输出板块中噪声命中 0 个，
> 不进入状态机，故不影响上述结论。

---

### P0-4：液冷服务 tier=S + quality=strong + lifecycle=acceleration → 却标记为 exhausted

**现象**：

```json
{
  "name": "液冷服务",
  "state": "exhausted_mainline",
  "tier": "S",
  "mainline_quality": "strong",
  "lifecycle": { "phase": "acceleration" },
  "today_change": 0.28,
  "trend_direction": "increasing",
  "strength_score": 67.7
}
```

五项指标全部利好，却被标记为 exhausted。

#### 修订记录：原分析方向错误，修复方案无效

原报告假设液冷服务是"从主线被 broken 踢出"，并建议「broken 判据加入 tier 保护」。
**状态机历史证伪了这一假设**：

| 交易日 | state | strength | today_change | exit_pressure |
|--------|-------|----------|-------------|---------------|
| 2026-08-28 | active_mainline | 61.1 | — | 0 |
| 2026-08-31 | active_mainline | 77.9 | — | 0 |
| **2026-09-01** | **exhausted_mainline** | 55.7 | -1.18 | 0 |
| **2026-09-02** | **exhausted_mainline** | 67.7 | +0.28 | 0 |

09-02 的 `prev_state` 是 `exhausted_mainline`，**不是 active_mainline**。
它走的是 exhausted→active 的**回归**路径（行 2316-2323），
而非主线→exhausted 的**退出**路径（行 2263-2275）。

因此原建议的 tier 保护**完全无效**：该保护作用于退出路径，
而液冷服务卡在回归路径上，加多少 tier 保护都不改变结果。

#### 回归条件实测结果

行 2316-2323 的四项回归条件复算（0902 归档数据）：

| 条件 | 实测值 | 是否成立 |
|------|--------|---------|
| `q == "leading"` | leading | ✅ |
| `strength_score > 60` | 67.7 | ✅ |
| `today_change > 0` | +0.28% | ✅ |
| `prev_dwell >= 2` | 2 | ✅ |
| `not broken` | — | ❌ **唯一卡点** |

三项显性条件全部满足，唯一未通过的是 `not broken`。
即 `broken == True`，但具体由 OR 链中哪一项触发**无法离线判定**
（`price_chg_5d` / `above_ma20` / `high20_pos` / `overheat_level` 四项均不落盘，见 P2-5）。

#### 结论：当前行为正确，无需修复状态机

`today_change = +0.28%` 属于弱反弹，此类幅度本就不应触发当日回归主线。
当前"卡在 exhausted 观察"是**合理的保守行为**，而非缺陷。

同时需澄清原报告的一处表述：报告称「tier=S 只影响 exit_pressure 容忍度，不豁免 broken 判据」，
这一观察本身正确，但结合上表可知 tier 在本案例中根本未参与判定——
exit_pressure 恒为 0（液冷服务 strength 55.7/67.7 均 ≥ 50，按 P0-3 的归零逻辑恒不累积），
tier 的容忍度分支从未被执行。

#### 修订后的行动项

**不修改状态机逻辑**，改为提升可观测性后重新评估：

1. 先落盘破位信号（见 P2-5），重跑一天后确认 `broken` 由哪项触发
2. 若确认是 `price_chg_5d <= 0` 或 `not above_ma20`，再评估是否需要
   为 tier=S 的回归路径放宽该单项判据——但需作为独立设计议题，不与 P0-3 混合处理

---

### P0-5：ambush_pool 为空——所有埋伏候选被闸门拦截

**现象**：sector.json 有 8 个 ambush 板块（华为算力、半导体、航空等），但 unified.json 的 `ambush_pool=[]`。

**代码追踪**：

```
文件：src/got/nodes/aggregator.py
行 1656-1920 _build_ambush_channel()
行 1366-1420 _run_ambush_gates()
```

**拦截路径分析**：

| 闸门 | 条件 | 被拦截板块 |
|------|------|-----------|
| 闸门 1：利空 | ★★★ 利空事件 | 半导体（央行鹰派）、多模态AI |
| 闸门 4：吸筹 | 当日净流出 | 华为算力(-18.9亿)、半导体(-46.3亿)、玻璃基板(-16.5亿)、航空(-4.4亿)、OLED(-3.5亿)、多模态AI(-133.1亿)、旅游服务(-48.3亿) |
| 无合格催化剂 | confidence < 0.7 的事件 | 光刻机（无匹配事件） |

**否决日志**（`ambush_veto_log`）记录了 8 条吸筹观察记录，全部因当日净流出被拦截。

**结论**：不是代码 bug，而是闸门设计过严。在全市场大单净流出 255 亿的背景下，几乎所有板块都当日净流出，导致闸门 4 成为"一刀切"。

**修复建议**：
1. 增加市场环境自适应：当全市场净流出 > 200 亿时，放宽吸筹闸门阈值（如从"净流出即拦截"改为"净流出 < -5 亿才拦截"）
2. 或增加"相对吸筹"判断：板块净流出但排名全市场前列（如 top 20%），仍可入池

---

## 三、P1 级问题（影响分析质量）

### P1-1：持仓股票名称丢失

**现象**：`600250` 和 `605566` 的 name 字段只有代码，无中文名称。候选股（西安饮食等）名称正常。

**代码追踪**：

```
文件：src/got/nodes/portfolio_decision.py
行 409, 440: name = position.get("name", symbol)  # 回退到 symbol

文件：src/services/portfolio_service.py
行 1063-1092 _get_stock_name()
  → 1. 数据库 PortfolioPosition 表查 name → 无记录
  → 2. get_realtime_quote(symbol) 取 name → 失败（停牌/接口异常）
  → 3. 回退返回 symbol 代码本身
```

候选股名称来自问财查询结果（自带 name 字段），持仓名称来自数据库+实时行情，两条数据链路不同。

**修复建议**：
1. 在持仓导入时（OCR/手动）强制要求 name 字段
2. 或在 Phase 2 开始时批量预热 name_cache（用 DataFetcherManager 批量查询）

---

### P1-2：板块匹配重复——"旅游(98分), 旅游(98分), 旅游(98分)"

**现象**：600250 的 reasoning 中同一板块出现 3 次。

**代码追踪**：

```
文件：src/got/nodes/portfolio_decision.py
行 1396-1435 _reverse_lookup_stock_sector()
```

```python
boards = manager.get_belong_boards(symbol)  # 返回多个板块
for board in boards:
    canon = normalize_sector_name(board_name)
    if canon in confirmed_map:
        matched.append({...})  # ← 每个 board 单独追加，无去重
```

600250 属于"旅游概念"、"旅游景区"、"旅游"等多个板块，归一化后全部映射到"旅游"，但代码为每个原始板块单独创建条目。

**修复建议**：在 matched 追加前，对 `canon` 做 set 去重：
```python
seen_canons = set()
for board in boards:
    canon = normalize_sector_name(board_name)
    if canon in confirmed_map and canon not in seen_canons:
        seen_canons.add(canon)
        matched.append({...})
```

---

### P1-3：情绪面 hot_stocks 的 mention_count 异常均匀

**现象**：50 只热门股的 mention_count 范围仅 92800-95600（差异 < 3%）。

**代码追踪**：

```
文件：src/got/nodes/sentiment_analysis.py
行 787-793 东财路径
```

```python
mention_count=int(float(row.get("关注指数", 0) or 0) * 1000)
```

东财的"关注指数"本身就在 92.8-95.6 之间（差异极小），乘以 1000 后变成 92800-95600。

**根因**：东财热榜的"关注指数"是一个归一化后的值（0-100 区间）， top 50 的股票分值非常接近。乘以 1000 放大了绝对值但没有放大相对差异。

**修复建议**：
1. 使用原始排名权重而非关注指数：`mention_count = (51 - rank) * 10000`
2. 或标注数据来源：`mention_source: "eastmoney_attention_index"`，避免误解为真实提及次数

---

### P1-4：hot_stocks 的 associated_sectors 硬编码为空

**现象**：所有 50 只热门股的 `associated_sectors: []`。

**代码追踪**：

```
文件：src/got/nodes/sentiment_analysis.py
行 787-793 东财路径: associated_sectors=[]  # 硬编码
行 816-824 雪球路径: associated_sectors=[]  # 硬编码
行 541-555 NGA路径: 未传递 associated_sectors 字段
```

所有数据路径都没有填充此字段。Schema 定义（schemas.py 行 502-508）中 `associated_sectors: list[str] = Field(default_factory=list)` 只是默认值，不是 bug。

**结论**：功能缺失——从未实现"热门股→板块"的关联逻辑。

**修复建议**：
1. 短期：在 HotStock 构建后，用 `manager.get_belong_boards(code)` 批量查询关联板块
2. 或从 Schema 中移除此字段，避免误导

---

### P1-5：reasoning.md 中 Node 7 交易建议出现两次

**现象**：reasoning.md 第 258 行和第 283 行各出现一次 "Node 7 交易建议"。

**代码追踪**：

```
文件：src/got/archive.py
行 119-120: Phase 1 写 reasoning.md（含 Node 1-6）
行 714-718: Phase 2 追加 reasoning.md（模式 "a"）
```

```python
# Phase 2 追加
with open(reasoning_path, "a", encoding="utf-8") as f:
    f.write(f"\n## Node 7 交易建议\n\n")
    f.write(f"{decision.reasoning}\n\n")
```

如果 `archive_phase2` 被调用两次（手动重跑/调试/异常重试），reasoning.md 会追加两次 Node 7 内容。Phase 1 会清理旧 reasoning.md，但 Phase 2 不会。

**修复建议**：在 `archive_phase2` 中先清理旧的 Phase 2 内容，或改用写入模式（"w"）+ 重新拼接完整 reasoning.md。

---

## 四、P2 级问题（设计缺陷/数据质量）

### P2-1：消息面 affected_sectors 不区分正负面

**现象**："全球央行鹰派" 事件的 `affected_sectors = ["科技成长", "AI", "半导体", "机器人", "新能源"]`，但这些都是负面影响的板块。

**根因**：`affected_sectors` 字段只标记"受影响板块"，不标记影响方向。方向由 `sentiment` 字段（positive/negative/neutral）承载，但扇出到板块后丢失了。

**修复建议**：将 `affected_sectors` 改为 `list[dict]`，每个元素包含 `{name, direction}`。

---

### P2-2：催化剂日历事件膨胀——92 个事件

**现象**：英伟达财报被拆分为 6 个独立事件（CPO、光模块、PCB、AI服务器、液冷服务器、半导体各一个），每个 event_id 不同。

**根因**：全球事件映射时，一个全球事件按 `affected_sectors` 扇出为多个日历条目。这是设计意图（每个板块独立追踪催化效果），但导致事件数量膨胀。

**影响**：92 个事件中 36 个 ★★★，密度过高，降低筛选价值。

**修复建议**：增加 `parent_event_id` 字段，展示时可按父事件聚合。

---

### P2-3：仓位数据矛盾——current_position_ratio=1.0 vs "当前仓位 380%"

**现象**：

| 字段 | 值 | 来源 |
|------|-----|------|
| `current_position_ratio` | 1.0 | `min(1.0, raw_position)` 截断 |
| `constraint_reasoning` | "当前仓位 380%" | `raw_position` 原始值 |

**代码追踪**：

```
文件：src/got/nodes/portfolio_decision.py
行 178: current_position = total_market_value / total_equity
行 267: f"当前仓位 {current_position:.0%}"  # 原始值
行 272: current_position_ratio=min(1.0, current_position)  # 截断值
```

380% 意味着持仓市值是净资产的 3.8 倍。如果有融资/杠杆，这是合理的；否则可能是 `total_equity` 计算有误。

**修复建议**：统一使用截断值，或在 `constraint_reasoning` 中标注"含杠杆"。

---

### P2-4："其他" 家族变成垃圾箱——风沙治理与军贸概念同组

**现象**：MainlineGroup 的 `group_name="军贸概念"` 包含风沙治理、粮食概念等不相关板块。

**代码追踪**：

```
文件：src/got/nodes/sector_analysis.py
行 3205-3238 _build_mainline_output()
```

两者 `frame_group` 都是 `"其他"`（FAMILY_KEYWORDS 中无匹配关键词），被聚合到同一组。组名取 `sorted_boards[0].name`（最强成员名），产生"军贸概念包含风沙治理"的视觉错觉。

**修复建议**：
1. 在 FAMILY_KEYWORDS 中增加"环保/风沙/农业/粮食"相关关键词
2. 或"其他"家族内做二次聚类（已有 `_build_theme_families`，但未用于 MainlineGroup）

**与 P0-3 的关联**：粮食概念之所以出现在"军贸概念"组下，根因就是本项——
它自身 `frame_group="其他"`，被聚合进同一组并经**成员继承**进入 active_mainline
（active_mainline 顶层仅旅游 / 军贸概念 / 银行 3 个组，粮食概念不在其中）。
修复本项后，P0-3 的现象会同步消失，两项应合并评估。

#### 实施评估（2026-09-02 已完成）

新增「农业」（粮食/种业/水产/土地流转…）与「生态环保」（风沙/环保/水务…）
两个家族，军工族补「军贸」。"其他" 家族占比 64 → 54。

**0902 主线组重构结果**（active_mainline 5 成员）：

| 拆组前 | 拆组后 |
|--------|--------|
| 旅游(94) | 旅游(94) — 消费 |
| 军贸概念(93) **含** 风沙治理(92) + 粮食概念(83) | 军贸概念(93) — 军工 |
| 银行(87) | 风沙治理(92) — 生态环保 |
| — | 银行(87) — 金融 |
| — | 粮食概念(83) — 农业 |

**净效应优于预期**：0902 `attack_power = 0.19` → `max_mainline = 2`，
裁剪按**组数**而非成员数。拆组后粮食概念单独成组排第 5，在 max_mainline=2 下
**被直接裁掉**——它不再能靠"蹭进强组"绕过裁剪。
这比 P0-3 的破位驱逐更根本地解决了问题：弱市本就不该输出 5 条主线。
两项修复在此收敛。

---

### P2-5：破位信号不落盘，状态机决策无法离线复现（2026-09-02 新增）

**现象**：`_compute_break_signals()` 返回的四个判据输入均不写入归档 JSON，
sector.json 中对应字段恒为 `null`：

| 判据字段 | sector.json 实际值 | 参与判定 |
|---------|------------------|---------|
| `price_chg_5d` | `null` | 行 2239 |
| `above_ma20` | `null` | 行 2240 |
| `high20_pos` | `null` | 行 2241 |
| `overheat_level` | 无字段 | 行 2241 |

**代码追踪**：

```
文件：src/got/nodes/sector_analysis.py
行 2129-2156 _compute_break_signals()：仅在栈内使用，返回值不进 SectorDetail
行 2429-2433：调用点，四个返回值仅传给 _apply_state_machine()
```

**影响**：
- `broken` 是状态机最主要的分支变量（行 2238-2243，OR 链四项），
  但其输入全部不落盘 → **事后无法回答"该板块为什么是这个状态"**
- P0-4 的排查因此受阻：已确认 `broken=True`，但无法定位由 OR 链哪一项触发
- 同类问题（主线异常退出 / 衰竭板块长期不回归）均无法离线复现

**修复建议**：将四个判据值写入 `SectorDetail` 的调试字段并落盘
（`price_chg_5d` / `above_ma20` / `high20_pos` / `overheat_level`）。

**优先级说明**：虽列为 P2，但**建议先于 P0-3 / P0-4 实施**——
纯字段新增、零行为变更，是另外两项能够验证修复效果的前提。

---

## 五、修复优先级矩阵

| 优先级 | 编号 | 问题 | 修复复杂度 | 影响范围 | 建议排期 |
|--------|------|------|-----------|---------|---------|
| ⚪ P2 | **P2-5** | **破位信号不落盘** | **低（纯字段新增）** | **可观测性** | **最先做（另两项前提）** |
| 🔴 P0 | P0-1 | `divergent` 从未被生产 | 低（加 3 行代码） | 资金面验证 | 立即 |
| 🔴 P0 | P0-3 | 粮食概念 -4.26% 未触发 exhausted | 低（broken 加 1 条 OR 分支） | 板块状态机 | 立即（P2-5 之后） |
| 🔴 P0 | P0-2 | 置信度隐式硬编码 0.7 | 中（重构 confidence 计算） | 所有板块分类 | 立即 |
| 🔴 P0 | P0-5 | ambush_pool 全被拦截 | 中（闸门自适应） | 埋伏通道 | 1 周内 |
| 🔴 P0 | P0-4 | 液冷服务 exhausted | **不修（行为正确）** | 板块状态机 | 观察，待 P2-5 后复评 |
| 🟡 P1 | P1-1 | 持仓名称丢失 | 低（预热 name_cache） | 决策展示 | 1 周内 |
| 🟡 P1 | P1-2 | 板块匹配重复 | 低（加 set 去重） | 持仓评分 | 1 周内 |
| 🟡 P1 | P1-3 | mention_count 异常均匀 | 低（改用排名权重） | 情绪面 | 2 周内 |
| 🟡 P1 | P1-4 | associated_sectors 全空 | 中（实现关联逻辑） | 情绪面 | 2 周内 |
| 🟡 P1 | P1-5 | reasoning.md 重复 | 低（清理旧内容） | 归档 | 2 周内 |
| ⚪ P2 | P2-1 | affected_sectors 不分正负 | 中（改 Schema） | 消息面 | 按需 |
| ⚪ P2 | P2-2 | 催化事件膨胀 | 低（加 parent_event_id） | 催化剂 | 按需 |
| ⚪ P2 | P2-3 | 仓位数据矛盾 | 低（统一截断值） | Phase 2 | 按需 |
| ⚪ P2 | P2-4 | "其他" 家族垃圾箱 | 低（补充关键词） | 板块分组 | 按需（与 P0-3 合并评估） |

**执行顺序**：P2-5 → P0-1 → P0-3 →（P2-4 合并评估）→ 其余项。
P0-4 暂不动状态机，仅提升可观测性后复评。

---

## 六、结论

### 核心发现

0902 运行数据质量总体正常（6 节点均为 "normal"），但存在以下系统性问题：

1. **资金面验证失效**：`capital_check` 的 `"divergent"` 分支从未被生产，导致板块大额净流出时不触发任何惩罚。这是最关键的修复项。

2. **置信度丧失区分度**：由于 P0-1 的连锁效应，所有板块 confidence 都坍缩为 0.7，`overall_confidence=0.86` 实际只反映"有 Node 2 数据"。

3. **状态机驱逐路径结构性锁死**（P0-3，修订）：原报告判断为"状态机过度敏感、两个方向都误判"。
   实测结论修正为——**只有一侧是真缺陷**：
   - 粮食概念一侧确为缺陷，但根因不是 exit_pressure 容忍度，而是该计数器在
     `strength >= 50` 时归零，导致 S/A-tier 的 exit_pressure 上限恒为 2 < 容忍度 3，
     驱逐路径**结构性不可达**（全库 518 个 sector-day 实测从未出现 ≥3）。
   - 液冷服务一侧**不是缺陷**：它 09-01 已是 exhausted，09-02 走的是回归路径，
     四项回归条件中三项满足、仅 `not broken` 未过；而 `today_change=+0.28%` 属弱反弹，
     不触发当日回归是合理的保守行为。原建议的 tier 保护作用于退出路径，对此无效。

4. **埋伏通道空转**：全市场大幅净流出背景下，吸筹闸门成为一刀切，所有 ambush 候选被拦截。需要市场环境自适应。

5. **状态机决策不可离线复现**（P2-5，新增）：`broken` 是状态机主分支变量，
   但其四个判据输入（price_chg_5d / above_ma20 / high20_pos / overheat_level）均不落盘，
   导致 P0-4 无法定位触发项，同类问题亦无法事后复现。

### 数据亮点

- 催化剂日历 92 事件覆盖全面，全球事件映射完整
- RRG 状态机运行正常（Leading 35 / Improving 19 / Weakening 16 / Lagging 11）
- 跨节点时间线共振信号（range_reversal, 置信 84%）正确识别了高震荡+低持续性的反转环境
- 续航评估正确标记了"其他"家族进入 `topping_risk` 区间

### 下一步行动

1. **先做 P2-5**（破位信号落盘）：纯字段新增、零行为变更，是后续两项能验证修复效果的前提
2. **修复 P0-1**（`divergent` 死代码）：加 3 行代码即可恢复资金面验证
3. **修复 P0-3**（broken 加单日暴跌 OR 分支，-4.0% 阈值已校准，全市场仅命中 2 个板块）：
   注意修复机制是新增破位判据，而非调整 exit_pressure 容忍度
4. **P0-4 暂不修改状态机**：待 P2-5 落盘后复评，确认 broken 触发项再决定
5. **P0-2 / P0-5 排期**：需要更多设计讨论
6. **1 周内修复 P1 级问题**：改善分析质量和用户体验
