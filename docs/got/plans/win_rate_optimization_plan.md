# GOT 选股胜率优化计划（9月回测驱动）
> **原位置**：`仓库根 `选股胜率优化_PLAN.md``（2026-09-17 文档结构重排移入当前位置）

> 状态：**4 步代码全部已落地，当前为生产数据收口阶段**（2026-09-28 状态头补记，原无状态头易误读为未实施）——
> Step 1–4 均已实现并带环境开关（默认开、可一键回滚），代码实测：`GOT_WINRATE_STEP1_ENABLED`（`aggregator.py:525`）、
> `STEP2`（`portfolio_decision.py:2168`）、`STEP3` + `CAPITAL_GATE`（`:3615-3621`）、`STEP4`（`:3565`）。
> 本计划不再有「待写代码」含义，后续动作是收齐生产样本（T+3/T+5 胜率）判定不劣化后收口。

## Context

9月回测（11 个交易日、40 只新推荐、T+3 胜率 28.1%、T+5 胜率 14.8%）显示负 alpha：T+3 -1.67%、T+5 -2.90%，选股本身是主要亏损来源。三路取证（Node 7 打分逻辑 + 失败/成功批次归档对比 + 流水线漏斗分析）收敛到 4 个可修复根因：

1. **Node 6 覆盖 Node 1 bear 信号**：9/7-9/9 Node 1 连续判 bear（attack_power 0.16-0.17），Node 6 判 bull，仓位上限 50-64% vs 应有 33-36%——bear 市场采 bull 仓位。
2. **acceleration 无 day 衰减**：众兴菌业（板块 day 14）与海通发展（day 1）拿同样的 lifecycle_priority 70。
3. **无资金确认闸门**：失败股 capital 分 18-56（多数 <40）仍获 buy_trend。
4. **过热降权只乘 sector 维度**（25% 权重），其余 4 维（75% 权重）不受影响。

## Approach

按风险从低到高排序，每步独立可验证。所有新逻辑均带环境开关（env var 默认开），可一键回滚。

### Step 1：Node 6 消费 attack_power，双档熔断覆盖（最关键）

**文件**：`src/got/nodes/aggregator.py:400-495`（`_compute_market_regime`）

**背景校准（grep 核查后修正）**：
- `attack_power` 由 Pydantic `MarketStateSignal` 保证范围 0–1（`src/got/schemas.py:116`，`ge=0.0, le=1.0`），生成于 `market_analysis.py:_build_market_state`（动量50% + 风险预算50%，crash_override 上限 0.15）。
- `MarketContext.market_state` 是 Optional，Node 2 已有 `_extract_attack_power` 降级到 0.5 的兼容模式（`sector_analysis.py:3400-3410`），本步骤复用同一模式。
- Node 2 已存在 5 档 attack_power 主线裁剪（`sector_analysis.py:3226-3235`）：`<0.15→1 / <0.30→2 / <0.50→3 / <0.70→4 / ≥0.70→5`。Step 1 阈值向该阶梯对齐。

**改动 1：attack_power 提取（在行 409-411 Node 1 数据提取区，只提取一次）**：

```python
# Node 1: 大盘
adx_value = market.tech_growth.regime_metadata.adx_value
cross_index_consistency = getattr(market.tech_growth, 'cross_index_consistency', 0.5)
# attack_power（Node 1 综合进攻强度 0-1，Optional 兼容，缺失时 None）
_market_state = getattr(market, 'market_state', None)
attack_power = getattr(_market_state, 'attack_power', None) if _market_state else None
```

**改动 2：regime_score 加入 attack_power 偏移项（Node 1 段末尾，在 ADX if/else **之外**，作为独立子项叠加）**：

```python
# Node 1: ADX + 趋势
if adx_value > 25:  # 有趋势
    if market.tech_growth.score > 0.3:
        regime_score += 0.3
    elif market.tech_growth.score < -0.3:
        regime_score -= 0.3
else:  # 无趋势
    regime_score += market.tech_growth.score * 0.2

# attack_power 作为 Node 1 第二信号（独立子项，与 ADX 分支叠加）
# 0.5 中性，±0.1 幅度：attack_power=0.15 → -0.07；attack_power=0.85 → +0.07
if attack_power is not None:
    regime_score += (attack_power - 0.5) * 0.2
```

**改动 3：双档熔断（插入点在行 477 `clip` **之后**、行 479 分类之前，避免偏移项被 clip 削掉再熔断）**：

```python
# 分类 regime
regime_score = clip(regime_score, -1.0, 1.0)

# Node 1 attack_power 双档熔断：技术面极弱时禁止判 bull
# 依据：Node 2 已有 5 档主线裁剪（<0.15→1 / <0.30→2），Step 1 与之对齐
# 9月回测：attack_power 0.16-0.17 时 Node 6 判 bull 导致 bear 市场采 bull 仓位
# 单档熔断只能压到 transition（bear 需 score<-0.3），无法触发 bear -5% stop_loss
if os.getenv("GOT_WINRATE_STEP1_ENABLED", "1") == "1" and attack_power is not None:
    if attack_power < 0.15 and regime_score > -0.35:
        # 档 1：crash_override 区间 → 强制 bear
        logger.info(
            f"[Node 6] attack_power={attack_power:.2f} < 0.15 熔断 → bear "
            f"(原 regime_score={regime_score:.2f})"
        )
        regime_score = min(regime_score, -0.35)
    elif attack_power < 0.30 and regime_score > 0.25:
        # 档 2：Node 2 主线裁剪第 2 档 → 压回 transition
        logger.info(
            f"[Node 6] attack_power={attack_power:.2f} < 0.30 熔断 bull → transition "
            f"(原 regime_score={regime_score:.2f})"
        )
        regime_score = min(regime_score, 0.25)

if regime_score > 0.3:
    regime = "bull"
elif regime_score < -0.3:
    regime = "bear"
else:
    regime = "transition"
```

**阈值来源**：0.15 / 0.30 直接复用 Node 2 阶梯第 1、2 档（`sector_analysis.py:3226-3231`），避免自定阈值。9月4 天失败样本 attack_power 0.16-0.17 落在档 2（→ transition）；如未来出现 <0.15 的暴跌日，档 1 会将其压到 bear 触发 -5% 硬止损。

### Step 2：acceleration 生命周期 day 衰减

**文件**：`src/got/nodes/portfolio_decision.py:1797-1813`（`_lifecycle_priority_score`）

**改动**：acceleration 从固定 70 分改为按 day 衰减（env 开关 `GOT_WINRATE_STEP2_ENABLED` 默认开，关闭时回退固定 70）：

```python
elif lifecycle_phase == "acceleration":
    # 加速期按 day 衰减：day 1-3 = 75（接近 launch），day 4-7 = 65，day 8+ = 50
    # 依据：9月回测失败股集中 day 6-14（众兴菌业 day14 T+3 -10.17%，湘佳股份 day4 -7.92%），
    # 成功股集中在 day 1-10（海通发展 day1 +9.84%）
    if os.getenv("GOT_WINRATE_STEP2_ENABLED", "1") == "1" and lifecycle_day is not None:
        if lifecycle_day <= 3:
            priority = 75
        elif lifecycle_day <= 7:
            priority = 65
        else:
            priority = 50
    else:
        priority = 70  # day 未知或开关关闭，保持原值
```

### Step 3：资金确认硬闸门（capital < 35 不得 buy_trend）

**文件**：`src/got/nodes/portfolio_decision.py:2839-2845`（`_score_candidates` 的 action 判定）

**改动**：在 buy_trend 判定中加入 capital 闸门（**显式处理 None，避免 TypeError**）：

```python
# 动作建议：基于生命周期优先级
# 资金确认闸门：capital < 35 时降级为 watch（不禁止入选，只禁止激进买入建议）
# 依据：9月失败股 capital 18-56 仍获 buy_trend（湘佳股份 capital=18.2 → T+3 -7.92%）
# capital_score 缺失（None）时不触发闸门（与 Assumption 2 一致）
CAPITAL_GATE = float(os.getenv("GOT_WINRATE_CAPITAL_GATE", "35.0"))
capital_ok = (
    capital_score is None
    or not stock.capital_available
    or capital_score >= CAPITAL_GATE
)
if os.getenv("GOT_WINRATE_STEP3_ENABLED", "1") == "1":
    if lifecycle_priority > 60 and lifecycle_phase in ["launch", "acceleration"] and capital_ok:
        action = "buy_trend"
    elif lifecycle_priority > 50 and capital_ok:
        action = "buy_dip"
    else:
        action = "watch"
else:
    # 回退旧逻辑
    if lifecycle_priority > 60 and lifecycle_phase in ["launch", "acceleration"]:
        action = "buy_trend"
    elif lifecycle_priority > 50:
        action = "buy_dip"
    else:
        action = "watch"
```

**量纲校验（grep 后需确认）**：`capital_score = stock.capital_score`（`portfolio_decision.py:2775`）直接进 `weighted_score_with_redistribution`。实施前用一行断言 `assert 0 <= capital_score <= 100` 或阅读 ScreenerStock 定义确认量纲为 0–100（而非 0–1），否则 35 阈值失效。

闸门只影响 `action_recommendation` 字段，不影响排序和入选——保守设计，避免候选池归零（与埋伏检测 6 维加权评分替代 AND 闸门的设计哲学一致，CLAUDE.md 关键决策表）。

### Step 4：过热降权扩展到 composite

**文件**：`src/got/nodes/portfolio_decision.py:2783-2792`（`_score_candidates` 过热降权段）

**改动**：过热惩罚从仅乘 `adjusted_sector_score` 改为额外乘 composite（env 开关 `GOT_WINRATE_STEP4_ENABLED`）：

```python
# 位置 1（行 2787-2792 原有）保持不变：
if overheat_penalty < 1.0:
    logger.info(...)
    adjusted_sector_score *= overheat_penalty

# 位置 2：在 adjusted_composite 计算后（行 2809-2811 之后）新增：
if (
    os.getenv("GOT_WINRATE_STEP4_ENABLED", "1") == "1"
    and overheat_penalty < 1.0
):
    # 半幅惩罚：0.5 + 0.5 * overheat_penalty
    # overheat_penalty=0.75 → composite × 0.875；overheat_penalty=0.9 → composite × 0.95
    composite_penalty = 0.5 + 0.5 * overheat_penalty
    adjusted_composite *= composite_penalty
    logger.info(
        f"[Phase 2] [过热降权-Composite] {stock.code} {stock.name} "
        f"板块={stock_sector} 半幅系数={composite_penalty:.3f}"
    )
```

即 overheat_penalty=0.75 时 composite 乘 0.875，overheat_penalty=0.9 时乘 0.95。半幅设计：过热板块仍有资金/技术面支撑时不过度降权，但确保过热信号传导到最终排序。**与 Step 3 正交**：Step 4 影响排序（composite），Step 3 影响 action 建议（capital 闸门），两者不交互。

## Critical files & anchors

| 文件 | 位置 | 改动 |
|------|------|------|
| `src/got/nodes/aggregator.py` | 400-495 `_compute_market_regime` | Step 1：attack_power 提取（行409-411） + 偏移项（Node 1 段末，ADX if/else 外） + 双档熔断（行 477 clip 之后、行 479 分类之前） |
| `src/got/nodes/portfolio_decision.py` | 1797-1813 `_lifecycle_priority_score` | Step 2：acceleration day 衰减 |
| `src/got/nodes/portfolio_decision.py` | 2839-2845 action 判定 | Step 3：CAPITAL_GATE=35 |
| `src/got/nodes/portfolio_decision.py` | 2783-2811 过热降权段 | Step 4：composite 半幅惩罚 |

测试基础：`tests/test_node6_regime_state.py`（regime 状态机，结构可参考但测试的是 `_apply_regime_state_machine` 而非 `_compute_market_regime`）、`tests/test_ambush_pool_consumption.py`（`_score_candidates` 的 mock 模式：MagicMock contexts + patch StockScreenerPipeline，行 154-206 可直接复用）。

## Verification

每步完成后运行对应验证，最后跑全量回归：

```bash
# Step 1 验证：构造两组 bear 输入
# (a) attack_power=0.17, tech score=-0.4, 普涨板块, institutional=0.5 → 期望 regime=transition（档 2）
# (b) attack_power=0.10（crash_override）, 同上 → 期望 regime=bear（档 1，触发 -5% stop_loss）
# (c) attack_power=None（market_state 缺失） → 期望不熔断，与旧逻辑一致
D:/ProgramData/miniconda3/envs/daily_stock_analysis/python.exe -m pytest tests/test_node6_regime_state.py -x -q

# Step 2/3/4 验证：新增测试文件 tests/test_winrate_optimization.py
# 用 test_ambush_pool_consumption.py 的 mock 模式：
# - lifecycle day 14 acceleration → priority 50（day 1 → 75）
# - capital_score=20 + launch 阶段 → action="watch"（非 buy_trend）
# - overheat_penalty=0.75 → composite 乘 0.875
D:/ProgramData/miniconda3/envs/daily_stock_analysis/python.exe -m pytest tests/test_winrate_optimization.py -x -q

# 全量回归（191 个测试文件，跑 GOT 相关子集）
D:/ProgramData/miniconda3/envs/daily_stock_analysis/python.exe -m pytest tests/ -x -q -k "node or got or portfolio or ambush"

# 新测试的准入标准：测试必须能反证——
# - 若移除 Step 1 双档熔断，(a) 应产出 bull、(b) 应产出 transition 或 bull（测试失败）
# - 若只保留档 2（移除档 1），(b) attack_power=0.10 只能到 transition，无法到 bear（测试失败）
# - 若回退 Step 2，day 14 应得 70（测试失败）
# - 若无 CAPITAL_GATE，capital=20 应得 buy_trend（测试失败）；capital=None 应不触发闸门（反证 None 处理）
```

## Assumptions

1. **attack_power 双档熔断阈值 0.15 / 0.30**：直接复用 Node 2 主线裁剪阶梯（`sector_analysis.py:3226-3235`），不自定阈值。9月4 天失败样本 attack_power 0.16-0.17 落在档 2（→ transition），若未来出现 crash_override（<0.15）则档 1 触发 bear（-5% 硬止损、risk_budget ≤ 0.5、REGIME_DWELL_SCALE bear 系数 0.5-0.7）。**旧版本单档 0.25 只能压到 transition，无法触发 bear 传导链，已废弃**。参数与 env 开关（`GOT_WINRATE_STEP1_ENABLED`）集中在函数开头，改一处即可。
2. **CAPITAL_GATE=35**：失败样本 capital 最低 18.2、多数 <40；成功股四方达 64.5。35 是失败/成功分界线中点，保守取值。capital_score 缺失（None）时不触发闸门（dimension_available 逻辑已有缺失维度归一化，缺失≠零分）。
3. **不修改 5 维权重**（capital 15% → 20% 等调整）：权重是全系统口径，动它会改变所有历史对比基线。闸门 + 生命周期衰减已覆盖权重问题的大部分场景，且行为可单独验证。若后续回测仍不达标再调权重。
4. **不新增"入场时机"独立维度**：工程量大（需新 schema 字段 + 数据管道），且 Step 2/3/4 已解决"追高"问题的 80%。先用现有数据验证这 4 步的改善幅度。
5. **Bear 市场禁止推荐 late-cycle 主线**（BadDayForensics 建议的根因 4）并入 Step 1 处理。**传导链（grep 核查后精确表述）**：
   - `REGIME_MULTIPLIER`（`portfolio_decision.py:249`，bear 0.35）**只作用于 sector_limits**（`base * regime_m * role_m * conf_w * catalyst_coeff`，行 290），late-cycle 主线板块上限缩水到 bull 的 35%。
   - `stop_loss`（`portfolio_decision.py:227-235`）走**独立 regime 分支**：bear -5% / transition -7% / bull -10%，且被 `_should_sell` 硬止损消费（行 1752-1753，`pnl_pct_decimal < stop_loss_threshold → return "sell"`）——不是展示字段，是真触发器。
   - `position_limit` 由 Node 6 的 `final_position` 决定（`aggregator.py:3172-3198`）：基础公式统一 bull 口径，然后乘 `REGIME_DWELL_SCALE`（bear established 0.5 / bear fresh 0.7 / transition 0.6 / bull established 1.0），最后 `min(position, regime.risk_budget)` 兜底；risk_budget 由 `_compute_market_regime` 生成（bull 0.6-0.8 / transition 0.4-0.6 / bear 0.3-0.5，行 486-494）。
   - 综上：**bear regime 触发 4 条独立收紧链路**（sector_limits 0.35× / stop_loss -5% / REGIME_DWELL_SCALE 0.5-0.7× / risk_budget ≤ 0.5），Step 1 档 1 熔断（attack_power<0.15）才能激活全部 4 条；档 2 熔断（<0.30）只到 transition，激活其中 3 条较温和档位。不单独加规则。
