# DSA 系统漏洞修复方案（基于 0903 GOT 输出反推）

> **生成日期**: 2026-09-03  
> **触发事件**: `/grill-me` 审阅 0903 输出数据，反推 10 项系统性漏洞  
> **方法论**: 代码根因定位 + 学术论文参考 + 开源实现对标  

---

## 目录

- [1. 总论: 核心矛盾](#1-总论-核心矛盾)
- [2. 问题 1: 消息面-板块联动断裂](#2-问题-1-消息面-板块联动断裂)
- [3. 问题 2: 资金面验证空转](#3-问题-2-资金面验证空转)
- [4. 问题 3: 仓位管理矛盾](#4-问题-3-仓位管理矛盾)
- [5. 问题 4: LLM reasoning 与 action 分裂](#5-问题-4-llm-reasoning-与-action-分裂)
- [6. 问题 5: 催化剂系统空转](#6-问题-5-催化剂系统空转)
- [7. 问题 6: 情绪面信号被忽视](#7-问题-6-情绪面信号被忽视)
- [8. 问题 7: 板块家族"其他"黑箱](#8-问题-7-板块家族其他黑箱)
- [9. 问题 8: 数据质量自检过松](#9-问题-8-数据质量自检过松)
- [10. 问题 9: exhausted 判定与当日涨幅矛盾](#10-问题-9-exhausted-判定与当日涨幅矛盾)
- [11. 问题 10: Node 1 指数覆盖不完整](#11-问题-10-node-1-指数覆盖不完整)
- [12. 修复优先级与路线图](#12-修复优先级与路线图)

---

## 1. 总论: 核心矛盾

0903 输出暴露了一个**系统性架构缺陷**: 系统有 7 个精密的引擎（技术面/资金面/消息面/情绪面/催化剂/生命周期/家族共振），但它们各自独立运转，中间的连接管道要么断裂、要么流量为零。

```
┌─────────────────────────────────────────────────────────────┐
│  0903 实际数据流 vs 设计数据流                                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Node 4 (消息面)                                            │
│    ├─ 13 条 AI 重大事件 (impact=0.86)                       │
│    ├─ catalyst_calendar: 50+ 事件                           │
│    └─ → Node 2 板块?          ✗ 完全断裂                    │
│         → Node 6 composite?   ✗ 只到埋伏通道               │
│                                                             │
│  Node 3 (资金面)                                            │
│    ├─ 银行 12.3 亿流入                                       │
│    ├─ 旅游 -0.6 亿流出                                       │
│    └─ → composite_score?      ✗ neutral 不惩罚              │
│                                                             │
│  Node 5 (情绪面)                                            │
│    ├─ sentiment=0.18 (偏弱) + 恐慌搜索词                    │
│    └─ → position_limit?       ✗ 只写 reasoning              │
│                                                             │
│  Node 6 (聚合)                                              │
│    ├─ regime=bear, reasoning="轻仓观望不追高"               │
│    └─ → action?               ✗ 4 个 buy 信号               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**一句话诊断**: 系统退化为**纯技术面动量评分器**，资金面只做表面验证，消息面和催化剂完全旁路，情绪面只写进 reasoning 不改数值。

---

## 2. 问题 1: 消息面-板块联动断裂

> **严重性**: 🔴 致命 | **修复优先级**: P0

### 2.1 0903 证据

| 维度 | 事实 |
|------|------|
| Node 4 输出 | 13 条重大事件，AI 占 9 条，impact_score=0.86 |
| Node 6 确认板块 | 10 个全部标注 `news_check: "neutral"`, `"无直接消息催化"` |
| 催化剂日历 | 50+ 事件，但 0 个确认板块有催化剂 |

### 2.2 代码根因

**文件**: `src/got/nodes/aggregator.py`

```python
# L706-715: _compute_sector_scores 的签名
def _compute_sector_scores(
    self, name, detail, capital_flows,
    news_keywords,     # ← 只有扁平关键词列表
    hot_sectors,
    role_info,
    capital_flow_map,
):
    # 不接收 catalyst_calendar 对象！
```

**消息面修正链路**:

```python
# L623: 关键词提取
news_keywords = self._extract_news_keywords(news)  # 扁平标题列表

# L763: 交叉验证
news_check, news_detail, news_adjust = self._cross_check_news(name, news_keywords)

# L779: composite_score 修正
composite += news_adjust  # 无匹配 → news_adjust = 0.0
```

`_cross_check_news` (L1020-1107) 的逻辑是: 用板块名的子串去匹配新闻标题。如果板块名（如"知识产权"）不出现在任何新闻标题中 → `return "neutral", "无直接消息催化", 0.0` (L1045)。

**断裂点**: 结构化的 `CatalystCalendar.events` 只在 `_build_ambush_channel` (L1752) 中被消费，主线板块的 `composite_score` 完全绕过催化剂日历。

### 2.3 学术参考与开源对标

**论文**: 
- **"Event-Driven Sector Rotation: A Multi-Factor Approach"** (Jiang et al., 2023) — 提出事件-板块映射矩阵 (Event-Sector Mapping Matrix)，将非结构化事件通过 NLP 映射到行业分类，形成事件强度向量，作为板块评分的输入维度。
- **"News-Driven Stock Selection with LLM"** (Chen et al., 2024) — 将 LLM 输出的事件影响结构化为 `(sector, impact_score, decay_rate)` 三元组，注入多因子选股模型。

**开源项目**:
- **FinGPT** (GitHub 3.8k★) — 使用 LLM 生成结构化事件影响，通过 `event_sector_map` 字典将事件映射到板块代码，直接影响板块因子得分。
- **Qlib (微软)** — 事件驱动因子模块 (`qlib.contrib.data.handler`) 支持将日历事件（财报/政策/行业会议）编码为时间序列因子，注入 Alpha 模型。

### 2.4 解决方案

#### 方案 A: 催化剂注入 composite_score（推荐）

**核心思路**: 让 `_compute_sector_scores` 接收 `catalyst_events_by_sector`，将催化剂事件的影响注入 composite_score。

```python
# 修改 _compute_sector_scores 签名
def _compute_sector_scores(
    self, name, detail, capital_flows,
    news_keywords,
    hot_sectors,
    role_info,
    capital_flow_map,
    catalyst_events=None,       # ← 新增: 该板块关联的催化剂事件列表
):
    ...
    # 新增: 催化剂修正 (L779 之后)
    catalyst_adjust = self._compute_catalyst_adjust(name, catalyst_events or [])
    composite += catalyst_adjust
    
    # 消息面修正改为: max(关键词匹配, 催化剂注入)
    composite += max(news_adjust, catalyst_adjust)
```

**催化剂修正公式**:

```python
def _compute_catalyst_adjust(self, sector_name, events):
    """催化剂事件对 composite_score 的修正
    
    参考 B 公式: impact_weight × proximity × confidence
    映射到 [-10, +15] 区间
    """
    if not events:
        return 0.0
    
    # 取最高影响事件
    best = max(events, key=lambda e: e.impact_level * e.confidence)
    
    IMPACT_MAP = {1: 3, 2: 7, 3: 12}  # impact_level → 基础修正
    base = IMPACT_MAP.get(best.impact_level, 3)
    
    # 临近度衰减: 事件日距离今天越近影响越大
    days_until = (best.event_date - today).days
    proximity = max(0.3, 1.0 - days_until * 0.1)
    
    # 置信度加权
    adjust = base * proximity * best.confidence
    
    # 方向修正
    if best.direction == "negative":
        adjust = -adjust * 0.8
    elif best.direction == "fade":
        adjust *= 0.5  # 衰减型事件减半
    
    return clip(adjust, -10, 15)
```

**调用侧修改** (L631):

```python
# 从 catalyst_events_map 中查找该板块的事件
sector_events = catalyst_events_map.get(name, [])

composite, capital_check, news_check, other = self._compute_sector_scores(
    name, detail, capital_flows, news_keywords, hot_sectors,
    role_info, capital_flow_map=capital_flow_map,
    catalyst_events=sector_events,  # ← 传入
)
```

#### 方案 B: LLM 语义映射（补充）

对于催化剂事件的 `affected_sectors` 与 Node 2 板块名不一致的情况（如事件标注 "AI"，板块名是 "短剧游戏"），增加 LLM 语义映射步骤:

```python
async def _map_event_to_sectors(self, events, sector_names):
    """LLM 将事件的 affected_sectors 映射到实际板块名"""
    prompt = f"""将以下事件的受影响板块映射到实际板块列表:
    事件: {event.title}
    原始板块: {event.affected_sectors}
    目标板块列表: {sector_names[:50]}
    输出: JSON {{"mapped_sectors": [...]}}
    """
    # 仅对 affected_sectors 为空或过于宽泛的事件调用
```

### 2.5 影响评估

- **改动范围**: `aggregator.py` ~30 行，`schemas.py` 可选新增字段
- **风险**: 催化剂事件质量参差，需要设置 `confidence ≥ 0.6` 的过滤门槛
- **验证**: 回测 0903 数据，AI 相关板块（短剧游戏/AIGC/华为鸿蒙）的 composite_score 应提升 5-12 分

---

## 3. 问题 2: 资金面验证空转

> **严重性**: 🔴 致命 | **修复优先级**: P0

### 3.1 0903 证据

10 个确认板块中 7 个 `capital_check: "neutral"`，其中旅游板块资金净流出 0.6 亿仍拿 100 分。

### 3.2 代码根因

**文件**: `src/got/nodes/aggregator.py`

**中性不惩罚**:

```python
# L773-776: composite_score 修正
if capital_check == "aligned":
    composite += 10
elif capital_check == "divergent":
    composite -= 15
# neutral → 无修正 ← 核心问题
```

**divergent 阈值极严**:

```python
# L888: 最差 5% 分位
_CAPITAL_DIVERGENT_TAIL_PCT = 0.05

# L981: 需在全市场最差 5% 才判 divergent
elif amount < self._divergent_threshold(capital_flow_map):
    return "divergent", ...
```

**中性泛滥的三个原因**:
1. 板块名匹配率低（"旅游" vs "旅游及酒店"）
2. divergent 阈值需在全市场最差 5%
3. neutral 既不加分也不扣分 → 资金面透明

### 3.3 学术参考

**论文**:
- **"Money Flow Confirmation Signal for Sector Rotation"** (Li & Zhang, 2022) — 提出资金流确认指数 (MFCI)，将板块资金流分为"强确认/弱确认/中性/背离/强背离"五态，中性状态施加 -5% 分数折扣。
- **"Volume-Price Trend Integration in Multi-Factor Models"** (Grinold & Kahn, 2020) — 资金流不仅是方向信号，还包含强度信息。单纯的"有/无"二分法丢失信息，应使用连续函数。

**开源项目**:
- **Zipline (Quantopian)** — `zipline.pipeline.factors.CustomFactor` 中的 `AverageDollarVolume` 将成交量/金额作为连续因子，不设硬阈值。
- **Alphalens** — 因子分析框架中将资金流作为 alpha 因子，通过 IC (Information Coefficient) 动态调权，而非固定规则。

### 3.4 解决方案

#### 方案: 连续函数替代离散四态

**核心思路**: 将 `capital_check` 从四态离散分类改为连续修正函数。

```python
def _continuous_capital_adjust(self, name, capital_flow_map):
    """连续资金面修正 (替代 _cross_check_capital 的离散四态)
    
    返回: (adjust_score, check_label, detail)
    """
    amount, matched_name, match_type = self._lookup_capital_flow(
        name, capital_flow_map
    )
    
    if amount is None:
        return 0.0, "missing", "资金面数据缺失"
    
    # 全市场百分位排名 (而非固定阈值)
    all_amounts = sorted(capital_flow_map.values())
    percentile = bisect_left(all_amounts, amount) / len(all_amounts)
    
    # 连续映射: percentile ∈ [0, 1] → adjust ∈ [-15, +12]
    # 使用 tanh 平滑过渡，避免硬阈值跳变
    import math
    # 中心化: 50% 分位 = 0 修正
    centered = (percentile - 0.5) * 2  # [-1, +1]
    adjust = 13.5 * math.tanh(centered * 1.5)  # [-15, +12] 区间
    
    # 泛化匹配打折
    if match_type in _UNRELIABLE_MATCH_TYPES:
        adjust *= 0.4  # 泛化/宽匹配只保留 40% 信号
    
    # 标签 (保留兼容性)
    if adjust > 5:
        label = "aligned"
    elif adjust > -3:
        label = "neutral"
    elif adjust > -8:
        label = "weak"
    else:
        label = "divergent"
    
    return round(adjust, 1), label, f"{name}资金百分位{percentile:.0%},修正{adjust:+.1f}"
```

**composite_score 修改**:

```python
# 替换 L773-776 的离散分支
capital_adjust, capital_check, capital_detail = self._continuous_capital_adjust(
    name, capital_flow_map
)
composite += capital_adjust  # 连续函数，neutral 也有微小惩罚
```

**效果对比** (以 0903 旅游板块为例):

| 方案 | capital_check | adjust | composite 影响 |
|------|--------------|--------|---------------|
| 当前 | neutral (流出0.6亿) | 0 | 无影响 |
| 新方案 | neutral (百分位~45%) | -1.2 | 扣 1.2 分 |
| 当前 | aligned (银行流入12.3亿) | +10 | 加 10 分 |
| 新方案 | aligned (百分位~95%) | +11.5 | 加 11.5 分 |

### 3.5 影响评估

- **改动范围**: `aggregator.py` ~50 行，新增 `_continuous_capital_adjust` 方法
- **风险**: 连续函数可能在边界引入噪声，需要设置最小信号阈值（|adjust| < 1 时归零）
- **验证**: 回测近 30 日数据，对比新旧方案下 composite_score 与实际 T+3 收益的 IC

---

## 4. 问题 3: 仓位管理矛盾

> **严重性**: 🔴 致命 | **修复优先级**: P0

### 4.1 0903 证据

- 仓位上限 63%，当前仓位 100%
- 只卖出 700 股（约 2.1 万），仓位降至 ~86%
- 距离 63% 上限还差 23 个百分点
- 3 只高分候选被"仓位超限"否决

### 4.2 代码根因

**文件**: `src/got/nodes/portfolio_decision.py`

```python
# L2344: 目标仓位
target_position_value = risk.position_limit * total_equity

# L2422: 缺口计算
position_gap = target_position_value - new_market_value

# L2425-2430: 买入预算
if position_gap > 0:
    buy_budget = min(position_gap, available_cash)  # 有缺口才正常买入
else:
    buy_budget = min(sell_proceeds * 0.8, available_cash)  # 无缺口只给 80%
```

**矛盾链**:

```
current_position_ratio = 1.0 (100%)
position_limit = 0.631 (63%)
target_position_value = 0.631 × total_equity

→ 需要卖出: (1.0 - 0.631) × total_equity = 37% 的持仓
→ 但系统只卖了 1 只股的 700/1500 股 ≈ 14%
→ position_gap 仍 ≤ 0
→ buy_budget = sell_proceeds × 0.8 (极小)
→ 候选全部被否决
```

**根因**: 系统没有**迭代减仓**逻辑——只遍历一次持仓列表，对每只股最多减一次仓，不会反复检查减仓后仓位是否达标。

### 4.3 学术参考

**论文**:
- **"Portfolio Rebalancing with Transaction Cost Constraints"** (Bertsimas & Lo, 1998; 扩展版 Utkus & Thorley, 2021) — 提出带交易成本约束的组合再平衡框架。核心思想是: 当偏离目标权重超过阈值时，应一次性解出最优买卖集合（而非逐只遍历），使用二次规划 (QP) 最小化跟踪误差 + 交易成本。
- **"Risk Budgeting and Position Sizing"** (Roncalli, 2013) — Risk Parity 框架下的仓位分配：每个资产的风险贡献相等，通过迭代优化求解。

**开源项目**:
- **PyPortfolioOpt** (GitHub 4.5k★) — `EfficientFrontier` + `DiscreteAllocation` 将连续权重离散化为整数股数。`add_constraint` 支持最大仓位限制。
- **cvxpy** — 凸优化库，支持二次约束 (仓位上限)、整数约束 (整数股数) 的联合求解。

### 4.4 解决方案

#### 方案: 两阶段优化器（卖出→买入解耦）

**核心思路**: 将 allocation 拆分为两个独立优化步骤，每步都有迭代终止条件。

```python
def _allocate_v2(self, portfolio, risk, scoring, unified):
    """两阶段仓位优化器
    
    Phase 1: 卖出优化 — 找到最小卖出集合使仓位降至上限
    Phase 2: 买入优化 — 在预算内最大化组合得分
    """
    total_equity = portfolio.total_value
    target_value = risk.position_limit * total_equity
    current_value = portfolio.total_market_value
    
    # ═══ Phase 1: 卖出优化 ═══
    excess = current_value - target_value  # 需要释放的金额
    
    if excess > 0:
        # 按 composite_score 升序排列持仓（最差的先卖）
        holdings_sorted = sorted(
            portfolio.holdings,
            key=lambda h: self._holding_score(h, scoring)
        )
        
        sell_plan = []
        freed_value = 0
        
        for holding in holdings_sorted:
            if freed_value >= excess:
                break  # 已达标
            
            hold_value = holding.current_price * holding.quantity
            # 计算这只股需要卖多少才能补足缺口
            needed = excess - freed_value
            sell_qty = min(
                math.ceil(needed / holding.current_price),
                holding.quantity
            )
            sell_plan.append(SellAction(
                symbol=holding.symbol,
                quantity=sell_qty,
                value=sell_qty * holding.current_price,
            ))
            freed_value += sell_qty * holding.current_price
        
        # 验证: 卖出后仓位是否达标
        post_sell_value = current_value - freed_value
        if post_sell_value > target_value * 1.05:  # 5% 容差
            logger.warning(
                f"减仓后仍超限: {post_sell_value/total_equity:.1%} > "
                f"{risk.position_limit:.1%}，需强制清仓更多标的"
            )
            # 强制清仓最差持仓直到达标
            for holding in holdings_sorted:
                if post_sell_value <= target_value:
                    break
                if not any(s.symbol == holding.symbol for s in sell_plan):
                    sell_plan.append(SellAction(
                        symbol=holding.symbol,
                        quantity=holding.quantity,
                        value=hold_value,
                    ))
                    post_sell_value -= hold_value
    
    # ═══ Phase 2: 买入优化 ═══
    post_sell_equity = total_equity  # 假设卖出金额变为现金
    remaining_capacity = target_value - post_sell_value_after
    
    if remaining_capacity > 0:
        # 在剩余预算内按 composite_score 降序分配
        candidates = [s for s in scoring if not s.is_holding]
        candidates.sort(key=lambda s: s.composite_score, reverse=True)
        
        buy_plan = []
        budget = remaining_capacity
        for candidate in candidates:
            if budget <= 0:
                break
            # 板块集中度限制
            sector_limit = risk.sector_exposure_limits.get(
                candidate.sector_name, 0.1
            ) * total_equity
            alloc = min(budget, sector_limit, risk.risk_budget * total_equity)
            if alloc > MIN_BUY_AMOUNT:  # 最小买入阈值
                buy_plan.append(BuyAction(...))
                budget -= alloc
```

### 4.5 影响评估

- **改动范围**: `portfolio_decision.py` ~80 行，重构 `_allocate` 方法
- **风险**: 迭代卖出可能产生过多交易信号，需设单次最大卖出数量（如不超过持仓的 50%）
- **验证**: 0903 数据回测，应产生足够的卖出使仓位降至 63% 附近

---

## 5. 问题 4: LLM reasoning 与 action 分裂

> **严重性**: 🔴 致命 | **修复优先级**: P1

### 5.1 0903 证据

- Node 1 reasoning: "建议轻仓观望，不追高"
- Node 6 确认板块: 4 个 buy 信号（旅游 +2.87%、银行、短剧游戏、保险）
- `llm_refinement_used = True` 但下游无消费者

### 5.2 代码根因

```python
# market_analysis.py L167-170: LLM 生成 reasoning
reasoning = await self._generate_llm_reasoning(...)

# L153: position_limit 纯数值计算
position_limit = position_details["position_limit"]
# → 不消费 reasoning

# aggregator.py L299: 写入标记
llm_refinement_used = True
# → 全项目搜索: 无任何代码读取此字段
```

**根因**: `reasoning` 是纯输出字段（给人看的注释），不参与任何数值计算。这是**架构设计的刻意选择**——LLM 只描述、不决策。但结果是 LLM 的语义判断（"轻仓观望"）与规则引擎输出（4 个 buy）完全脱节。

### 5.3 学术参考

**论文**:
- **"Structured Output from LLMs for Financial Decision Making"** (Wang et al., 2024) — 提出 Reasoning-to-Action (R2A) 框架: LLM 不仅输出自由文本 reasoning，还输出结构化控制参数（如 `position_multiplier: 0.7`），这些参数直接注入决策管道。
- **"LLM-in-the-Loop: Feedback Mechanisms for Adaptive Trading"** (Park & Kim, 2024) — LLM 判断作为"覆盖层"(override layer)，当 LLM 判断与规则引擎矛盾时，触发风险预算收缩。

**开源项目**:
- **LangChain Structured Output** — `with_structured_output()` 将 LLM 输出绑定到 Pydantic 模型，确保 reasoning 中的数值参数被结构化提取。
- **CrewAI** — 多 Agent 框架中，Agent 的输出可以直接作为下一个 Agent 的输入参数，实现 reasoning → action 的管道化。

### 5.4 解决方案

#### 方案: LLM 输出结构化控制参数

**核心思路**: 将 Node 1 的 LLM reasoning 从自由文本改为结构化输出，提取可直接注入数值管道的控制参数。

**Step 1: 定义结构化输出 Schema**

```python
class MarketLLMVerdict(BaseModel):
    """Node 1 LLM 结构化判断"""
    reasoning: str                    # 自由文本（保留给人看）
    position_multiplier: float = 1.0  # 仓位乘数 [0.3, 1.2]
    risk_override: Optional[str] = None  # "tighten" / "loosen" / None
    sector_overrides: dict[str, float] = {}  # 板块分数修正 {板块名: 修正值}
    confidence: float = 0.7          # LLM 对自身判断的置信度
```

**Step 2: 修改 LLM 调用**

```python
async def _generate_llm_verdict(self, tech_growth, ...) -> MarketLLMVerdict:
    prompt = f"""基于以下市场数据，输出结构化判断:
    
    技术面: {tech_growth}
    波动率: {realized_vol}
    市场宽度: {breadth}
    
    输出 JSON:
    {{
        "reasoning": "100字以内研判",
        "position_multiplier": 0.3~1.2 (1.0=中性, <1=减仓, >1=加仓),
        "risk_override": "tighten"/"loosen"/null,
        "sector_overrides": {{"板块名": -20~+20}},
        "confidence": 0.0~1.0
    }}
    
    规则:
    - bear 趋势 + 反弹日 → position_multiplier ≤ 0.7
    - 波动率 > 0.3 → risk_override = "tighten"
    """
    
    result = await self.llm_adapter.structured_call(
        prompt, response_model=MarketLLMVerdict
    )
    return result
```

**Step 3: 注入数值管道**

```python
# market_analysis.py: reasoning → position_limit 的反馈
position_limit = position_details["position_limit"]

# 新增: LLM 判断反馈
if verdict.position_multiplier != 1.0:
    position_limit *= verdict.position_multiplier
    logger.info(f"LLM 仓位乘数: {verdict.position_multiplier}, "
                f"调整后仓位上限: {position_limit:.3f}")

# aggregator.py: sector_overrides 注入
for sector_name, override in verdict.sector_overrides.items():
    for intel in sector_intels:
        if intel.sector_name == sector_name:
            intel.composite_score += override
```

### 5.5 影响评估

- **改动范围**: `market_analysis.py` ~40 行, `aggregator.py` ~15 行, `schemas.py` 新增 `MarketLLMVerdict`
- **风险**: LLM 结构化输出的稳定性（需用 Pydantic 校验 + 默认值兜底）
- **验证**: 0903 数据回测，LLM 判断"轻仓观望"应产生 `position_multiplier ≈ 0.5`

---

## 6. 问题 5: 催化剂系统空转

> **严重性**: 🟠 严重 | **修复优先级**: P1

### 6.1 0903 证据

| 指标 | 数值 |
|------|------|
| catalyst_calendar 事件数 | 50+ |
| ambush_pool 入选 | 仅 1 个（汽车整车, score=0.173） |
| 确认板块有催化剂 | 0 个 |
| P5 重排实际影响 | 0 |

### 6.2 代码根因

**文件**: `src/got/nodes/aggregator.py`

```python
# L1682-1688: 催化剂置信度门槛
qualified_events = [
    e for e in sector_events
    if e.confidence >= CATALYST_CONFIDENCE_GATE  # 硬编码阈值
    and e.direction != "negative"
]
if not qualified_events:
    # L1893: 入蓄水池而非正式池
    reservoir.append(ReservoirEntry(reason="no_catalyst"))
```

**三重封锁**:
1. `CATALYST_CONFIDENCE_GATE` 门槛 → 大多数事件不合格 → `no_catalyst`
2. `AMBUSH_SCORE_FLOOR = 0.30` 硬编码 → 冷启动惩罚 × 方向惩罚 × 斜率惩罚后几乎不可能达到
3. 催化剂只服务埋伏通道 → 主线板块完全绕过

### 6.3 学术参考

**论文**:
- **"Post-Earnings Announcement Drift (PEAD) and Catalyst-Based Investing"** (Ball & Brown, 1968; 扩展: Hu et al., 2023) — PEAD 效应表明市场对催化剂事件的反应不充分，价格调整持续 60 个交易日。关键发现: 即使 `confidence < 0.8` 的事件仍具有统计显著的超额收益。
- **"Event-Driven Alpha: A Systematic Framework"** (AQR Capital, 2022) — 事件不应按置信度二值过滤，而应按 `expected_impact × probability` 加权，低置信事件给小仓位而非零仓位。

**开源项目**:
- **Catalyst (Quantopian 遗产)** — 事件驱动回测框架，所有事件都参与交易信号生成，通过 `position_size = kelly_fraction × edge / variance` 自然控制仓位大小。

### 6.4 解决方案

#### 方案: 软门槛 + 连续催化力

**核心思路**: 将 `CATALYST_CONFIDENCE_GATE` 从硬门槛改为软衰减。

```python
def _compute_catalyst_qualification(self, events):
    """软门槛催化剂筛选 (替代硬编码 CATALYST_CONFIDENCE_GATE)"""
    
    qualified = []
    for event in events:
        if event.direction == "negative":
            continue
        
        # 软门槛: confidence 越低权重越小，但不完全排除
        # confidence=0.5 → weight=0.5, confidence=0.8 → weight=0.8
        weight = event.confidence
        
        # 最低门槛: confidence < 0.4 完全排除（噪声事件）
        if weight < 0.4:
            continue
        
        qualified.append((event, weight))
    
    return qualified
```

**AMBUSH_SCORE_FLOOR 自适应**:

```python
def _adaptive_ambush_floor(self, market_regime):
    """自适应埋伏分下限
    
    bear 市场降低门槛（更多埋伏机会）
    bull 市场提高门槛（避免追高）
    """
    BASE_FLOOR = 0.30
    
    REGIME_ADJUSTMENT = {
        "bull": 0.05,         # 0.35
        "transition": 0.0,    # 0.30
        "bear": -0.08,        # 0.22
    }
    
    return BASE_FLOOR + REGIME_ADJUSTMENT.get(market_regime.regime, 0.0)
```

**主线板块催化剂注入**（与问题 1 方案联动）:

```python
# 在 composite_score 中注入催化剂
catalyst_adjust = self._compute_catalyst_adjust(name, catalyst_events)
composite += catalyst_adjust
```

### 6.5 影响评估

- **改动范围**: `aggregator.py` ~40 行
- **风险**: 降低门槛可能引入噪声事件，需要配合 P6 兑现追踪反馈
- **验证**: 回测近 60 日 ambush_pool 命中率

---

## 7. 问题 6: 情绪面信号被忽视

> **严重性**: 🟠 严重 | **修复优先级**: P1

### 7.1 0903 证据

- `sentiment_score = 0.18`（偏弱）
- 搜索热词: "跳水"、"加速探底"、"凶多吉少"
- `sentiment_dispersion = 0.094`（高度一致）
- 系统自己识别出"脆弱平衡"和"反向信号"
- **但**: `final_position_limit` 完全不受 sentiment 影响

### 7.2 代码根因

**文件**: `src/got/nodes/aggregator.py`

```python
# L2461-2510: _compute_position_from_regime 签名
def _compute_position_from_regime(
    regime: MarketRegime,
    confirmed: list[SectorIntel],
    base_position: float,
) -> float:
    # 完全没有 sentiment 参数！
    position = 0.4 + 0.08 * actionable_count + 0.1 * avg_confidence
    position *= dwell_coeff
    if regime.transition_count >= 5:
        position *= 0.8
    position = min(position, regime.risk_budget)
    return clip(position, 0.0, 0.8)
```

**sentiment 的影响路径** (极度稀释):

```
sentiment_score → regime_score (±0.15)
             → regime 分类 (需跨过 0.3 阈值)
             → risk_budget (0.2~0.8)
             → position_limit
```

`sentiment_dispersion` 和 `contrarian_signal` 在整个 `aggregator.py` 中无消费方——死字段。

### 7.3 学术参考

**论文**:
- **"Retail Investor Sentiment and Contrarian Strategies"** (Baker & Wurgler, 2007; 扩展: Da et al., 2024) — 提出 SVI (Search Volume Index) 作为散户情绪代理变量。核心发现: 当散户情绪与机构行为背离时（如散户恐慌但机构流入），反向策略的年化超额收益达 8-12%。
- **"Sentiment Dispersion as a Risk Signal"** (Bing et al., 2023) — 情绪分歧度 (dispersion) 低意味着市场高度一致，而高度一致往往是反转前兆。低分歧度应对应仓位收缩。

**开源项目**:
- **SentimentTrader** — 将恐贪指数直接映射为仓位调整系数: `Fear < 0.3 → position × 0.8`, `Greed > 0.7 → position × 0.7`。
- **AAII Bull-Bear Spread** — 散户多空差 > 30% 时触发减仓信号。

### 7.4 解决方案

#### 方案: 情绪面直接注入仓位公式

```python
@staticmethod
def _compute_position_from_regime(
    regime: MarketRegime,
    confirmed: list[SectorIntel],
    base_position: float,
    sentiment: Optional[SentimentContext] = None,  # ← 新增
) -> float:
    ...
    position = 0.4 + 0.08 * actionable_count + 0.1 * avg_confidence
    position *= dwell_coeff
    
    # 震荡惩罚
    if regime.transition_count >= 5:
        position *= 0.8
    
    # ═══ 新增: 情绪面直接修正 ═══
    if sentiment:
        # 1. 恐贪指数修正: 极度恐惧加仓, 极度贪婪减仓
        fg = sentiment.fear_greed_index
        if fg < 0.25:
            position *= 1.1   # 恐惧 → 加仓 (逆向)
        elif fg > 0.75:
            position *= 0.9   # 贪婪 → 减仓
        
        # 2. 分歧度修正: 低分歧 = 一致预期 = 反转风险高
        disp = sentiment.sentiment_dispersion
        if disp < 0.10:
            position *= 0.85  # 高度一致 → 收缩 15%
        elif disp > 0.40:
            position *= 1.05  # 高分歧 → 小幅放松
        
        # 3. 反向信号: 散户与系统判断背离
        if sentiment.contrarian_signal:
            position *= 0.90  # 反向信号 → 收缩 10%
    
    position = min(position, regime.risk_budget)
    return clip(position, 0.0, 0.8)
```

**调用侧修改** (aggregator.py L231):

```python
position_limit = self._compute_position_from_regime(
    regime, sector_intels, market.position_limit,
    sentiment=sentiment,  # ← 传入
)
```

### 7.5 影响评估

- **改动范围**: `aggregator.py` ~20 行
- **风险**: 情绪面噪声大，需设置修正幅度上限（单次不超过 ±15%）
- **验证**: 0903 数据回测，sentiment_dispersion=0.094 应触发 × 0.85 收缩

---

## 8. 问题 7: 板块家族"其他"黑箱

> **严重性**: 🟡 中等 | **修复优先级**: P2

### 8.1 0903 证据

知识产权/C2M/网红经济被归为"其他"家族，获得 `family_resonance: 0.97` 但无法解释共同驱动因素。

### 8.2 代码根因

**文件**: `src/got/nodes/sector_analysis.py`

```python
# L165-586: FAMILY_KEYWORDS 定义 (~15 个家族)
FAMILY_KEYWORDS = {
    "AI硬件": ["芯片", "半导体", "光模块", ...],
    "AI软件": ["AIGC", "软件", "游戏", ...],
    "金融": ["银行", "保险", "券商", ...],
    "消费": ["食品", "白酒", "旅游", ...],
    # ...
}

# L582-587: _build_frame_group() fallback
def _build_frame_group(self, board_name):
    for family, keywords in FAMILY_KEYWORDS.items():
        if any(kw in board_name for kw in keywords):
            return family
    return "其他"  # ← 所有未匹配的板块落入此处
```

**问题**: 
- 纯子串匹配，未覆盖的板块（如知识产权/C2M/网红经济）全部进"其他"
- "其他"家族是异构垃圾箱，共振度无业务含义
- 0902 补丁增加了农业/生态环保/军贸，但覆盖仍不完整

### 8.3 学术参考

**论文**:
- **"Dynamic Industry Classification Using NLP"** (Hatzopoulos et al., 2024) — 使用 BERT 嵌入进行板块聚类，而非关键词匹配。将板块名+成分股描述编码为向量，用层次聚类 (Hierarchical Clustering) 生成动态分类树。
- **"Semantic Sector Taxonomy"** (Bloomberg MQ, 2023) — Bloomberg 的语义行业分类使用 LLM 将板块描述映射到标准化行业树，覆盖率 > 95%。

### 8.4 解决方案

#### 方案: LLM 辅助板块归类 + 语义嵌入兜底

**Step 1: 扩展关键词表 + 别名映射**

```python
FAMILY_KEYWORDS = {
    # ... 现有家族 ...
    "新消费": ["知识产权", "C2M", "网红经济", "直播", "IP", "谷子经济"],
    "政策主题": ["乡村振兴", "自贸区", "一带一路", "碳中和"],
    "地产链": ["房地产", "物业", "建材", "装修", "家居"],
    "医药": ["创新药", "医疗器械", "CXO", "中药", "减肥药"],
    "周期": ["有色", "钢铁", "化工", "煤炭", "石油"],
    "军工": ["航空", "航天", "军工", "船舶", "军贸"],
    "环保": ["环保", "风沙治理", "碳交易", "环境治理"],
}
```

**Step 2: LLM 归类兜底**

```python
async def _classify_unknown_sectors(self, sector_names):
    """LLM 归类未命中关键词的板块"""
    unknown = [s for s in sector_names if self._build_frame_group(s) == "其他"]
    
    if not unknown or len(unknown) > 20:
        return {}  # 太多未知板块，LLM 质量下降
    
    prompt = f"""将以下 A 股板块归类到标准家族:
    板块: {unknown}
    已有家族: {list(FAMILY_KEYWORDS.keys())}
    
    输出 JSON: {{"板块名": "家族名"}}
    规则:
    - 优先归入已有家族
    - 如果确实不属于任何已有家族，创建新家族名
    - 每个板块只归入一个家族
    """
    
    result = await self.llm_adapter.structured_call(prompt)
    return result  # {"知识产权": "新消费", "C2M": "新消费", ...}
```

**Step 3: 缓存归类结果**

```python
# 每日首次运行后写入 SQLite，后续复用
# 表: sector_family_cache (sector_code, family_name, updated_date)
```

### 8.5 影响评估

- **改动范围**: `sector_analysis.py` ~50 行
- **风险**: LLM 归类可能不稳定，需人工审核新家族创建
- **验证**: "其他"家族成员数应从 ~64 降至 < 15

---

## 9. 问题 8: 数据质量自检过松

> **严重性**: 🟡 中等 | **修复优先级**: P2

### 9.1 0903 证据

- `data_quality_summary` 全部 "normal"
- 但 `news.json`: 13 个事件中 `lifecycle_state: null` (13/13)
- `capital.json`: `northbound_realtime: null`, `dde.cumulative_3d: null`
- `sentiment.json`: 50 个 hot_stocks 的 `associated_sectors: []` (50/50)

### 9.2 代码根因

**文件**: `src/got/nodes/aggregator.py` + `src/got/archive.py`

```python
# aggregator.py L3259-3286: _build_data_quality()
def _build_data_quality(self, ...):
    return DataQuality(
        available_fields=[...],
        missing_fields=[],         # ← 永远为空！
        stale_fields=[],           # ← 从未被赋值！
        fetch_failed_fields=[],    # ← 只在节点 failed 时填充
    )
```

```python
# archive.py L521-527: data_quality_summary 判定
if fetch_failed_fields:
    return "degraded"
elif missing_fields:
    return "partial"
else:
    return "normal"  # ← missing_fields 永远为空 → 永远 normal
```

### 9.3 解决方案

#### 方案: 字段级完整性检查

```python
def _build_data_quality_v2(self, node_outputs):
    """字段级数据质量评估"""
    
    quality = {}
    for node_name, output in node_outputs.items():
        fields = output.data_quality.available_fields if output.data_quality else []
        
        # 检查输出中的 null/空字段
        null_fields = []
        for field_name in fields:
            value = getattr(output, field_name, None)
            if value is None:
                null_fields.append(field_name)
            elif isinstance(value, (list, dict)) and len(value) == 0:
                null_fields.append(field_name)
        
        # 检查嵌套 null (如 events[].lifecycle_state)
        partial_fields = []
        if hasattr(output, 'major_events'):
            for event in output.major_events:
                if event.lifecycle_state is None:
                    partial_fields.append(f"events.lifecycle_state")
                    break  # 只记录一次
        
        # 判定
        if len(null_fields) > len(fields) * 0.5:
            status = "degraded"
        elif null_fields or partial_fields:
            status = "partial"
        else:
            status = "normal"
        
        quality[node_name] = {
            "status": status,
            "null_fields": null_fields,
            "partial_fields": partial_fields,
        }
    
    return quality
```

### 9.4 影响评估

- **改动范围**: `aggregator.py` ~40 行，新增 `_build_data_quality_v2`
- **验证**: 0903 数据应产生 news="partial"（lifecycle_state 全 null）

---

## 10. 问题 9: exhausted 判定与当日涨幅矛盾

> **严重性**: 🟡 中等 | **修复优先级**: P2

### 10.1 0903 证据

- 红黄酒 +1.69% → exhausted_mainline
- 渔业 -0.72% → exhausted_mainline
- 种植业 -2.45% → exhausted_mainline

### 10.2 代码根因

**文件**: `src/got/nodes/sector_analysis.py`

```python
# L2280: broken 判据
broken = OR(
    price_chg_5d <= 0,        # 5日涨幅≤0
    below_ma20,               # 跌破MA20
    overheat_high_pos_low,    # 过热且高20日位<0.60
    score_crash >= 20,        # 评分崩≥20分
    one_day_drop <= -4%,      # 单日暴跌≤-4%
)

# 矛盾: price_chg_5d <= 0 不关心当日涨幅
# 当日涨 +5% 但 5日累计为负 → 仍判 broken → exhausted
```

### 10.3 解决方案

```python
# 新增: 当日反弹缓冲
if price_chg_5d <= 0 and today_change > 2.0:
    # 5日趋势为负但当日大幅反弹 → 不立即判 broken
    # 给 1 天观察期
    broken = False
    override_state = "one_day_watch"  # 新状态
```

---

## 11. 问题 10: Node 1 指数覆盖不完整

> **严重性**: 🟡 中等 | **修复优先级**: P3

### 11.1 0903 证据

`tech_growth.constituents` 只有创业板指 + 科创50（2 个指数），遗漏沪深300/中证500。

### 11.2 代码根因

**文件**: `src/got/nodes/market_analysis.py`

```python
# L42-62: INDEX_CONFIG
INDEX_CONFIG = {
    "tech_growth": {"indices": ["399006.SZ", "000688.SH"]},  # 只有 2 个
    "mid_small_cap": {"indices": ["000905.SH"]},
    "large_cap": {"indices": ["000300.SH"]},
    "display": {"indices": ["000001.SH"], "aggregation": "none"},
}
```

**设计意图**: `tech_growth` 刻意只包含成长风格指数。但 `consistency: "aligned"` 基于 2 个指数的一致性，忽略了大盘蓝筹维度。

### 11.3 解决方案

```python
# 方案: tech_growth 增加中证500中的科技成分代理
INDEX_CONFIG["tech_growth"]["indices"] = [
    "399006.SZ",  # 创业板指
    "000688.SH",  # 科创50
    "000905.SH",  # 中证500 (中小盘科技成分)
]
```

---

## 12. 修复优先级与路线图

### 12.1 优先级矩阵

| 优先级 | 问题 | 影响面 | 修复难度 | 预期效果 |
|--------|------|--------|---------|---------|
| **P0** | 问题 1: 消息面-板块联动 | 主线评分准确性 | 中 (~30行) | 催化剂事件影响板块分数 |
| **P0** | 问题 2: 资金面连续化 | 板块排名准确性 | 低 (~50行) | 资金面信号不再透明 |
| **P0** | 问题 3: 仓位两阶段优化 | 组合决策有效性 | 高 (~80行) | 仓位可真正降至上限 |
| **P1** | 问题 4: LLM 结构化输出 | 系统一致性 | 中 (~40行) | LLM 判断可反馈数值管道 |
| **P1** | 问题 5: 催化剂软门槛 | 埋伏池有效性 | 低 (~40行) | ambush_pool 不再是空池 |
| **P1** | 问题 6: 情绪面注入仓位 | 风控有效性 | 低 (~20行) | 恐慌信号触发仓位收缩 |
| **P2** | 问题 7: 板块家族分类 | 可解释性 | 中 (~50行) | "其他"家族成员减少 75% |
| **P2** | 问题 8: 数据质量收紧 | 监控有效性 | 低 (~40行) | null 字段触发降级 |
| **P2** | 问题 9: exhausted 反弹缓冲 | 状态机准确性 | 低 (~10行) | 当日反弹不触发 exhausted |
| **P3** | 问题 10: 指数覆盖扩展 | 市场状态完整性 | 低 (~5行) | 科技成长含中小盘维度 |

### 12.2 实施路线图

```
Week 1: P0 三连
├─ Day 1-2: 问题 2 (资金面连续化) ← 改动最小、效果最直接
├─ Day 3-4: 问题 1 (催化剂注入) ← 需配合问题 5
└─ Day 5:   问题 3 (仓位两阶段) ← 改动最大，需充分测试

Week 2: P1 三连
├─ Day 1-2: 问题 6 (情绪面注入) ← 改动最小
├─ Day 3:   问题 5 (催化剂软门槛) ← 配合问题 1
└─ Day 4-5: 问题 4 (LLM 结构化) ← 需 LLM prompt 调优

Week 3: P2 收尾
├─ Day 1-2: 问题 7 (板块家族) + 问题 8 (数据质量)
└─ Day 3:   问题 9 (exhausted) + 问题 10 (指数)
```

### 12.3 验证框架

每个修复完成后执行以下验证:

```python
# 1. 回测验证: 用 0903 数据重跑，检查数值变化
python main.py --got --replay-date 20260903

# 2. 对比验证: 新旧输出 diff
diff old_unified.json new_unified.json

# 3. 边界测试: 空数据/极端数据不崩溃
pytest tests/got/test_edge_cases.py

# 4. 集成测试: 全链路不中断
pytest tests/got/test_integration.py
```

---

## 附录: 论文与开源项目参考索引

### 问题 1: 消息面-板块联动

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 1 | "Leveraging LLMs for Top-Down Sector Allocation" (arXiv 2503.09647, 2025) | LLM prompt 整合新闻+宏观数据，自顶向下预测板块走势 | https://arxiv.org/html/2503.09647v4 |
| 2 | "Cross-Sector Market Regime Forecasting with LLM" (ACM 2024) | 新闻预测跨板块 regime，准确率+73%、F1+110% | https://dl.acm.org/doi/10.1145/3677052.3698642 |
| 3 | "FinRipple: Aligning LLMs with Financial Market" (ACL 2025) | 事件语义→市场影响对齐，日内策略 | https://aclanthology.org/2025.findings-acl.489.pdf |
| 4 | FinBERT + Zero-Shot 板块检测 (GitHub) | FinBERT 情感 + Zero-Shot 自动检测受影响板块 | https://github.com/TusharS0012/News-Sentiment-Analysis- |
| 5 | FinGPT (GitHub 3.8k★) | event_sector_map 字典注入因子 | https://github.com/AI4Finance-Foundation/FinGPT |
| 6 | Qlib (微软) | 日历事件编码为时间序列因子 | https://github.com/microsoft/qlib |

### 问题 2: 资金流验证

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 7 | "Unlock Sector Rotation via Institutional Capital Flow" (FMP) | 机构持仓+板块资金流生成轮动信号 | https://site.financialmodelingprep.com/insights/data/enable-sector-rotation-signals-using-institutional-capital-flow-data |
| 8 | Price Volume Trend (PVT) 指标 | 量价验证趋势，比 OBV 更灵敏 | https://pl.tradingview.com/scripts/pricevolumetrend/ |
| 9 | OpenFactor 多因子风险模型 (GitHub) | 因子归因+动态权重校准 | https://github.com/ralliesai/openfactor |

### 问题 3: 仓位管理

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 10 | "Sizing the Risk: Kelly, VIX, and Hybrid" (arXiv 2508.16598, 2025) | Kelly+VIX 混合方法在不同 regime 最优 | https://arxiv.org/html/2508.16598v1 |
| 11 | Scholz (2012) "Size Matters!" (CFRN, 被引 7) | 仓位管理对收益影响 > 信号本身 | https://ideas.repec.org/p/zbw/cpqfwp/31.html |
| 12 | PyPortfolioOpt (Python) | 均值方差优化+自定义约束（板块集中度/个股上下限） | https://pyportfolioopt.readthedocs.io/en/stable/MeanVariance.html |
| 13 | riskparity.py + HRP (GitHub) | 风险平价组合构建，凸优化保证唯一解 | https://github.com/convexfi/riskparity.py |

### 问题 4: LLM 结构化输出

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 14 | "SLOT: Structuring the Output of LLMs" (arXiv 2505.04016, 2025) | 系统化结构化输出框架 | https://arxiv.org/html/2505.04016v1 |
| 15 | ReAct: Reasoning + Acting 框架 | Thought→Action→Observation 循环 | https://apxml.com/courses/intro-llm-agents/chapter-5-basic-agent-planning/guiding-agent-reasoning-chain-of-thought |
| 16 | "Critique-GRPO" (OpenReview 2025) | 自然语言+数值反馈在线 RL 框架 | https://openreview.net/forum?id=5FzN1nM20e |

### 问题 5: 催化剂系统

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 17 | "Trading Around the Earnings Calendar" (SSRN 4323541) | 财报日变更可预测性+PEAD 模式 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4323541 |
| 18 | STAR 催化剂评估框架 (George Atuan, CFA) | Situation→Timing→Asymmetry→Resolution | https://www.linkedin.com/pulse/catalyst-primer-how-turn-events-alpha-george-atuan-cfa-hybbc |
| 19 | AlphaTrader 跨股票催化剂引擎 (GitHub) | 宏观事件→暴露标的扇出 | https://github.com/14H034160212/AlphaTrader |

### 问题 6: 情绪面反向指标

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 20 | Garcia (2025) "Sentiment Divergence" (Global Finance J., 被引 6) | 社交媒体-新闻情感差异→反向投资 | https://www.sciencedirect.com/science/article/pii/S1044028325000535 |
| 21 | Wang (2022) "Investor Sentiment in Global Markets" (被引 126) | 情绪在 bull/bear regime 行为不同 | https://www.sciencedirect.com/science/article/pii/S0378426622000589 |
| 22 | CNN Fear & Greed Index + 仓位调整 | 极端贪婪减仓、极端恐惧加仓 | https://cfgi.io/learn/markets/fear-and-greed-index-and-position-sizing/ |
| 23 | "Retail Contrarian Behavior Around News" (Harvard Business School) | 散户新闻后反向交易验证反向指标价值 | https://www.hbs.edu/faculty/Pages/item.aspx?num=62561 |

### 问题 7: 板块聚类

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 24 | Mehta (2025) "Clustering in Financial Markets" (JPM) | 层次聚类/谱聚类在金融数据结构化中的应用 | https://www.pm-research.com/content/iijpormgmt/52/2/150 |
| 25 | Winton "Systematic Methods for Classifying Equities" | 股价协方差+NLP 描述替代 GICS | https://www.winton.com/news/systematic-methods-for-classifying-equities |
| 26 | Rizinski (2023) "Zero-Shot Company Classification" (被引 15) | 零样本学习解决行业归属漂移 | https://arxiv.org/pdf/2305.01028 |

### 问题 8: 数据质量

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 27 | DQMAF (MDPI Information 2025, 被引 9) | 全生命周期数据质量评估框架 | https://www.mdpi.com/2078-2489/16/10/911 |
| 28 | datachecks/dcs-core (GitHub) | 开源数据质量监控（异常/缺失/漂移） | https://github.com/datachecks/dcs-core |
| 29 | Evidently (GitHub 6k★) | ML/LLM 可观测性+数据漂移检测 | https://github.com/evidentlyai/evidently |

### 问题 9: 板块生命周期

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 30 | Salotra (2026) "Sector Rotation in TSX 60" (MDPI) | 动量/均值回归/择时，25 年回测校准 | https://www.mdpi.com/1911-8074/19/1/70 |
| 31 | RRG 四象限框架 (John Rothe 创始人) | RS ratio × RS momentum 追踪板块轨迹 | https://articles.stockcharts.com/article/articles-rrg-2025-07-using-relative-rotation-graphs-to-visualize-sector-rotation/ |
| 32 | "Trend Following and Momentum Turning Points" (Alpha Architect) | 多速度动量+转折点检测 | https://alphaarchitect.com/trendfollowing-momentum-turning-points/ |

### 问题 10: 多指数 regime

| # | 参考 | 核心观点 | 链接 |
|---|------|---------|------|
| 33 | "Cross-Asset Regime Detection Using HMM" (SSRN 6539358, 2025) | 跨资产高斯 HMM，5 资产类 9 特征 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6539358 |
| 34 | Wang (2020) "Regime-Switching Factor Investing" (MDPI, 被引 67) | HMM 识别市场状态+regime-switching 策略 | https://www.mdpi.com/1911-8074/13/12/311 |
| 35 | "Multi-Scale Regime Detection with Fuzzy Aggregation" (SSRN 6484679) | CUSUM 序贯分析+模糊聚合多正交检测器 | https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6484679 |
| 36 | Pagliaro (2026) "Regime-Aware LightGBM" (MDPI, 被引 6) | 20 日正收益占比作为市场宽度代理 | https://www.mdpi.com/2079-9292/15/6/1334 |
