# 0827 GOT 全流程诊断报告

> 运行时间：2026-08-27 10:48~10:54  
> 诊断范围：Phase 1 全节点 + Phase 2 选股/持仓评分  
> 触发原因：用户反馈三个问题——①贵金属涨3.9%却只给44分 ②神农种业涨10%仍被推荐买入 ③黄金走势好却被踢出主线

---

## 〇、代码核查补充发现

经逐项对照代码核实，补充两个文档初版未覆盖的发现：

**发现 1：P0-2 修复比预期更简单。** `portfolio_decision.py` L384 已构建 `_build_sector_strength_map()`（覆盖主线/埋伏/衰竭/防守/降温五类板块），但全文件无消费方——是死代码。P0-2 只需将此 map 传入 `_find_stock_sector` 降级路径即可。另外 `sector_context.strength_lookup`（L746-749 生成，覆盖全量板块）是现成双保险。

**发现 2：独立行情豁免机制救不了黄金家族。** 代码中已有豁免通道（`sector_analysis.py` L2980-2993），但门槛是 `group_score ≥ 90 且 S/A 成员 ≥ 3`。黄金家族 69.7 分远不够格。即使只修 P0-1（放宽名额），黄金也只能靠排名进入；若想让家族共振（0.83）参与裁剪，需单独设计。

**P5 补充——决策层后果比评分更致命：** 默认 50 分不仅压低 composite，还触发 `portfolio_decision.py` L506-511 的分层决策——`lifecycle_phase is None` + `strength < 60` 直接落入 `reduce` 分支。**这才是"昨天买今天卖"的直接执行路径**，比评分影响更致命。

---

## 一、问题总览

| # | 现象 | 根因分类 | 影响 |
|---|------|---------|------|
| P1 | 000426 持仓评分 44.3，板块均价涨 3.9% | 持仓评分体系不纳入当日行情 | 赚钱的被卖出 |
| P2 | 神农种业(300189) 涨 10% 仍被推荐买入 | 创业板涨停阈值 20%，无"过热保护" | 推荐买不到的票 |
| P3 | 黄金/白银强度 71~99 分，状态却是 neutral | attack_power=0.2463 硬阈值砍到 1 主线 | 强势板块被无视 |
| P4 | Node 1 regime=bear vs Node 6 regime=bull | 两节点 regime 定义不同，主线裁剪只用 Node 1 | 信号矛盾 |
| P5 | 被踢出板块 sector 默认 50 分 | Phase 2 不查 strength_lookup | 评分断崖 |

---

## 二、逐项诊断

### P1：持仓评分忽略当日行情

**现象**：000426（兴业银锡）所属贵金属板块当日均价涨 3.9%，但持仓评分只有 44.3。

**评分拆解**：

| 维度 | 得分 | 权重 | 说明 |
|------|------|------|------|
| capital | 35.1 | 15% | DDE 百分位低 |
| news | 40.0 | 15% | 无消息催化 |
| sector | 50.0 | 25% | **"板块已踢出"→ 默认 50** |
| tech | 26.1 | 25% | 多日波段指标，不看当日涨跌 |
| risk | 70.0 | 20% | P&L +1.5%，微弱正贡献 |

**根因**：`_score_holdings()` 的 5 个维度全部基于多日指标或截面排名，**当日板块涨幅零体现**。tech 维度用 MA20/布林/RSI 等波段指标，今天涨 3.9% 只算"超跌反弹"，不算"趋势走强"。

**昨天 vs 今天对比**：

| 指标 | 0826（买入日） | 0827（今日） |
|------|---------------|-------------|
| 板块状态 | 黄金=主线-A（96分） | 被踢出 confirmed_sectors |
| sector 分 | 92.9 | 50.0（默认值） |
| tech 分 | 63.4 | 26.1 |
| 综合评分 | 69.5 | 44.3 |
| 建议 | BUY 1200股 | SELL 1000股 |

**结论**：一天之间板块从主线变成"踢出"，评分从 69.5 暴跌到 44.3，完全不考虑实际盈亏（+1.5%）和板块当日表现（+3.9%）。缺乏最小持仓期保护。

---

### P2：创业板涨 10% 无过热保护

**现象**：神农种业(300189) 当日涨 ~10%，系统仍推荐买入（预算 ¥22,270）。

**涨停过滤逻辑**（`stock_screener.py` L1436-1443）：

```python
def _is_limit_up(c: dict) -> bool:
    pct = c.get("change_pct", 0)
    code = c.get("code", "")
    if code.startswith(("300", "688", "689")):
        return pct >= 19.5   # 创业板 20% 限制
    return pct >= 9.5        # 主板 10% 限制
```

300189 是创业板（300xxx），涨停限制 20%。涨 10% 不触发过滤。

**更深层问题**：波段技术指标对涨 10% 的股票反而给高分——

| 指标 | 涨 10% 时表现 | 评分效果 |
|------|-------------|---------|
| MA20 正乖离 | 大幅领先 | 满分 |
| 布林带突破 | 突破上轨 | 高分 |
| 20 日突破 | 创新高 | 满分 |
| 均线多头排列 | 完美排列 | 满分 |

**追涨信号变成买入信号**。`_score_risk()` 有 5 日涨幅惩罚（-5~-30），但缺少**当日涨幅**梯度惩罚。

---

### P3：attack_power 硬阈值砍杀强势板块

**现象**：白银 99.3 分、黄金 71.5 分、黄金概念 81.3 分，全部被分类为 neutral。

**数据**：

| 板块 | 强度分 | 4维指标 | 状态 |
|------|--------|---------|------|
| 白银 | 99.3 | momentum=0.991, turnover=0.983, RS=1.0, breadth=1.0 | ❌ neutral |
| 其他养殖 | 99.6 | — | ❌ neutral |
| 种子 | 99.2 | — | ❌ neutral |
| 光通信 | 82.6 | — | ❌ neutral |
| PCB概念 | 80.3 | — | ❌ neutral |
| 黄金 | 71.5 | — | ❌ neutral |
| **种业** | **100.0** | — | ✅ **active_mainline**（唯一） |

**根因**：Node 1 `attack_power = 0.2463`，刚好 < 0.25 阈值。

```python
# sector_analysis.py L2971-2978
if attack_power < 0.25:
    max_mainline = 1    # ← 0.2463 命中这个分支
elif attack_power < 0.40:
    max_mainline = 1
elif attack_power < 0.70:
    max_mainline = 3
else:
    max_mainline = 5
```

**0.2463 vs 0.2500 差 0.004**，结果从 max_mainline=1 变成 max_mainline=3。连续的信号被离散的阈值切割。

**黄金家族实际表现**：
- family_score: 69.7
- family_resonance: 0.83（6 板块高联动）
- theme_status: "主线扩散"
- 但家族分数未参与主线裁剪判定

---

### P4：Node 1 vs Node 6 regime 矛盾

**现象**：

| 节点 | regime | 依据 |
|------|--------|------|
| Node 1 `market_state.regime` | **bear** | 技术面：score=-0.41，trend=bearish |
| Node 6 aggregator `regime` | **bull** | 综合：regime_score=0.58，含情绪/消息/资金 |

Node 2 主线裁剪只用 Node 1 的 `attack_power`（技术面），不考虑 Node 6 综合 regime。当技术面看空但综合面看多时，主线裁剪过度保守。

**Node 1 技术面细节**：

| 时间框架 | trend | score |
|---------|-------|-------|
| 短期 | bearish | -0.33 |
| 中期 | neutral | -0.28 |
| 长期 | neutral/bearish | -0.18~-33 |

ADX=17（弱趋势），regime_metadata.regime="ranging"（震荡）。技术上确实在震荡区间，但 attack_power=0.2463 被压到防守线以下。

---

### P5：Phase 2 对踢出板块一刀切

**现象**：黄金强度 71.5 分，Phase 2 给 sector 默认 50 分。

**机制**：`_score_holdings()` 中，当板块不在 `confirmed_sectors` 时：

```python
# portfolio_decision.py（_reverse_lookup_stock_sector 降级路径）
strength = 50.0  # 硬编码默认值
```

不查 `sector_context.strength_lookup`（里面有黄金 71.5、白银 99.3 等实际分数）。

**影响**：从 92.9（昨天主线）到 50.0（今天默认），sector 维度直接砍半。

---

## 三、系统级问题归纳

### 1. 离散阈值 vs 连续信号

| 阈值 | 问题 |
|------|------|
| attack_power < 0.25 → max_mainline=1 | 0.2463 和 0.2500 差了 0.004，结果差 2 个主线名额 |
| 创业板涨停 ≥ 19.5% | 涨 10% 完全无惩罚，涨 19% 也无惩罚 |
| confirmed_sectors 硬边界 | 不在名单 → 50 分，不管实际强度 |

### 2. 板块状态切换无惯性

昨天是主线（96分），今天踢出（50分）。没有过渡带，没有"降级观察期"。一天的板块轮动就能翻转整个持仓建议。

### 3. 评分体系与真实市场脱节

- 持仓评分不看当日行情
- 技术面追涨给高分
- 风险维度只微调不翻转
- 板块强度用硬默认值

### 4. 节点间信息孤岛

Node 6 综合判断 bull，Node 2 只看 Node 1 的 bear 信号做裁剪。聚合层的智慧没有反馈到裁剪层。

---

## 四、修复建议（按优先级）

### P0-1：attack_power 连续化

**现状**：离散阈值（0.25/0.40/0.70）

**~~方案 A（废弃）~~**：`round(attack_power * 10)` — 0.70 时给 7 个名额，超现有上限 5，冲击下游 Node 6 和持仓板块限仓逻辑。

**方案 B（推荐）：细粒度阶梯，封顶 5**

```python
# sector_analysis.py L2971-2978 替换为：
if attack_power < 0.15:
    max_mainline = 1
elif attack_power < 0.30:
    max_mainline = 2    # 0.2463 → 2 名额，救回黄金
elif attack_power < 0.50:
    max_mainline = 3
elif attack_power < 0.70:
    max_mainline = 4
else:
    max_mainline = 5    # 封顶不变
```

**回归风险**：attack_power < 0.25 分支有历史由来（08-20 为防"20→0 全灭"加的 `_MAINLINE_PRUNE_FLOOR` 保底）。放宽后需确认不会退回"熊市开 5 条主线"的过度开仓。新方案 < 0.15 仍保底 1 条，0.15~0.30 给 2 条，比原版宽松但仍有节制。

**下游验证**：主线名额扩容后需确认 Node 6 `confirmed_sectors` 和 `sector_exposure_limits` 能正常消费更多板块。

### P0-2：Phase 2 sector 分用实际强度

**现状**：不在 confirmed_sectors → 默认 50

**方案**：优先复用死代码 `_build_sector_strength_map()`（`portfolio_decision.py` L384，已构建但无消费方），传入 `_find_stock_sector` 降级路径。双保险：`sector_context.strength_lookup`（全量板块覆盖）。

```python
# portfolio_decision.py 降级路径修改
if sector in confirmed_sectors:
    strength = confirmed_sector_score
else:
    # 先查死代码 map → 再查 strength_lookup → 最后回退 50
    strength = sector_strength_map.get(sector)
    if strength is None:
        strength = strength_lookup.get(sector, 50.0)
```

**板块别名处理**：持仓股的板块名来自 `get_belong_boards`（通达信/问财口径），与 Node 2 板块名可能存在别名差异。查表失败时复用项目已有的 `wencai_board_aliases.json` 机制做映射，仍失败才回退 50。

**位置**：`src/got/nodes/portfolio_decision.py`，`_reverse_lookup_stock_sector()` 降级路径

### P0-3：选股加入当日涨幅惩罚

**现状**：只在硬涨停时过滤（创业板 ≥19.5%, 主板 ≥9.5%）

**方案**：双层——扣分 + 软过滤

```python
# stock_screener.py _score_risk() 追加
if change_pct >= 8.0:
    risk_score -= 20     # 大涨后追高风险
elif change_pct >= 5.0:
    risk_score -= 10

# 软过滤（Round 0 追加，不依赖涨停阈值）
# 涨 ≥8% 的票不参与买入排序（类似涨停过滤的软版本）
candidates = [c for c in candidates if c.get("change_pct", 0) < 8.0]
```

**注意**：risk_score 是 15% 权重的"越高越安全"分数，-20 扣分约影响综合分 -3，仅靠扣分对涨 10% 的追高票惩罚力度偏弱。追加软过滤确保大涨票不进入最终排序。

### P1-1：持仓评分加入当日动量

**现状**：5 维度不看当日行情

**方案**：sector 或 tech 维度追加板块当日涨幅 bonus

```python
# portfolio_decision.py _score_holdings()
sector_today_change = get_sector_today_change(sector)
if sector_today_change > 0.03:  # 板块涨 >3%
    tech_score += 10           # tech 维度 +10
elif sector_today_change < -0.03:
    tech_score -= 10
```

**数据源**：`get_sector_today_change()` 不存在。`strength_lookup` 只有强度分没有涨幅。板块当日涨跌幅需从板块行情快照获取（如 Node 2 的 `today_change` 字段或 akshare 板块实时行情）。工作量"中"评估合理。

### P1-2：最小持仓期保护

**现状**：昨天买入，今天可以卖出

**方案**：新建仓至少持有 N 个交易日

```python
# portfolio_decision.py
if holding_days < MIN_HOLDING_DAYS:  # 如 3 天
    action = "hold"  # 强制持有，不允许 sell/reduce
```

**前置依赖**：当前持仓数据结构是否带买入日期字段。如果没有，需先在持仓同步链路里补字段（`PortfolioService` → SQLite），工作量可能被低估。

### P2-1：家族共振加成（替代跨节点 regime 反馈）

**现状**：裁剪只用 Node 1 attack_power，Node 6 regime=bull 无法反馈

**~~原方案（跨节点反馈）~~**：需要 DAG 架构调整（Node 6 在 Node 2 之后运行），成本高。

**替代方案**：让 Node 2 裁剪直接消费板块层面的共振数据。`family_resonance` 已在 Node 2 计算（黄金家族 0.83），可折算成等效攻击力加成：

```python
# sector_analysis.py 主线裁剪阶段
# 家族共振 > 0.7 的板块组获得等效 attack_power 加成
family_resonance_bonus = max(0, (family_resonance - 0.7) * 0.5)
adjusted_attack_power = attack_power + family_resonance_bonus
```

这样黄金家族共振 0.83 → bonus 0.065 → adjusted_attack_power 从 0.2463 升至 0.31，可直接进入 max_mainline=3 区间。

**优点**：不需要跨节点数据流改造，`family_resonance` 已在 Node 2 内部计算完成。

---

## 五、修复影响评估

| 修复项 | 涉及文件 | 工作量 | 风险 | 核查修正 |
|--------|---------|--------|------|---------|
| P0-1 attack_power 细粒度阶梯 | sector_analysis.py | 小 | 低（需验证回归） | 方案 B 封顶 5，弃方案 A |
| P0-2 复用死代码 sector_strength_map | portfolio_decision.py | 小 | 低 | 复用 L384 死代码 + 板块别名 |
| P0-3 当日涨幅扣分+软过滤 | stock_screener.py | 小 | 低 | 追加 ≥8% 不参与排序 |
| P1-1 持仓当日动量 | portfolio_decision.py | 中 | 中 | 需找板块涨幅数据源 |
| P1-2 最小持仓期 | portfolio_decision.py | 中~大 | 中 | 前置：持仓需补买入日期字段 |
| P2-1 家族共振加成 | sector_analysis.py | 中 | 中 | 替代跨节点反馈，用 family_resonance |

**建议先做 P0-1 + P0-2 + P0-3**（三个小改动，解决最大痛点），再观察效果决定 P1/P2。

---

## 六、附录：关键数据快照

### 0827 板块强度 Top 10 vs 状态

| 排名 | 板块 | 强度分 | 状态 | 是否主线 |
|------|------|--------|------|---------|
| 1 | 种业 | 100.0 | active_mainline | ✅ |
| 2 | 其他养殖 | 99.6 | neutral | ❌ |
| 3 | 白银 | 99.3 | neutral | ❌ |
| 4 | 种子 | 99.2 | neutral | ❌ |
| 5 | 焦炭加工 | 85.7 | exhausted_mainline | ❌ |
| 6 | 光通信 | 82.6 | neutral | ❌ |
| 7 | 黄金概念 | 81.3 | neutral | ❌ |
| 8 | PCB概念 | 80.3 | neutral | ❌ |
| 9 | 稀缺资源 | 80.5 | neutral | ❌ |
| 10 | CPO概念 | 77.9 | neutral | ❌ |

### 0826 vs 0827 持仓 000426 评分对比

| 维度 | 0826 | 0827 | 变化 |
|------|------|------|------|
| capital | 33.3 | 35.1 | +1.8 |
| news | 40.0 | 40.0 | 0 |
| sector | 92.9 | 50.0 | **-42.9** |
| tech | 63.4 | 26.1 | **-37.3** |
| risk | 97.0 | 70.0 | -27.0 |
| **composite** | **69.5** | **44.3** | **-25.2** |
