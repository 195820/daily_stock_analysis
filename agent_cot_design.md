# 炒股 Agent 设计文档（CoT + GOT 混合架构）

> 创建日期：2026-07-21
> 最后更新：2026-07-22
> 状态：草案（Draft）
> 版本：v0.4

### v0.4 变更记录（2026-07-22 实现阶段修复）

**数据获取层 Bug 修复：**
- **A 股指数数据获取 KeyError 修复**：`data_provider/base.py` 的 `normalize_stock_code()` 在传给 akshare fetcher 前剥掉了 `.SH/.SZ` 后缀，导致 akshare fetcher 的 `_is_index_code()` 无法识别指数代码，错误调用 `ak.stock_zh_a_hist()` 而非 `ak.stock_zh_index_daily()`，返回数据缺少 `volume` 列引发 KeyError。修复方案：在 `get_daily_data()` 中归一化前检测 A 股指数（`_is_a_share_index()`），归一化后恢复后缀（`stock_code + suffix`），确保 akshare fetcher 能正确路由到 `_fetch_index_data()`。涉及文件：`data_provider/base.py`、`data_provider/akshare_fetcher.py`

**Node 1 多时间框架信号修复：**
- **5 日评分为 0 的 Bug**：初始实现用 `df.tail(5)` 切片后计算指标，但 5 行数据无法计算 MA60/MA120/MA250、MACD(12,26,9)、ADX(14) 等需要较长历史窗口的指标，全部返回 0。修复方案：使用**完整数据集**计算指标，不同时间框架通过**不同参数敏感度**区分：
  - 短期（5日）：MA 周期 `[5, 10, 20]`，量价 lookback 5 日 → 对近期波动敏感
  - 中期（20日）：MA 周期 `[5, 10, 20, 60]`，量价 lookback 20 日 → 确认趋势方向
  - 长期（60日）：MA 周期 `[10, 20, 60, 120, 250]`，量价 lookback 60 日 → 反映大级别趋势
- **真实数据验证结果**（2026-07-22）：短期 bearish(-0.65)、中期 bearish(-0.65)、长期 neutral(-0.34)，信号分层合理，统一趋势 bearish

**涉及文件：** `src/got/nodes/market_analysis.py`、`src/got/indicators.py`

### v0.3 变更记录（2026-07-22 设计审查修复）

**Bug 修复：**
- 资金面 `confidence_multiplier` 分母从 4 修正为 5（适配 P3-5 大宗交易维度）
- 板块防守轮动逻辑移除对不存在字段 `market_context.tech_growth.capital_flow` 的引用，改为 `trend == "bearish"`
- 大盘 `index_codes` 中 `000905.SZ` 修正为 `000905.SH`
- 删除 `classify_macd_phase()` 重复调用（变量 `macd_phase` 未使用）
- 板块强度 `price_momentum` / `relative_strength` 添加 `clip(-0.15, 0.15)` 防止极端值主导评分

**架构补全：**
- 聚合节点新增路径级输入校验（`validate_path_inputs`），任一路径失败直接报错，不做降级
- 聚合节点矛盾检测从 8 对扩展至 10 对（新增 `medium_tech_vs_news`、`long_tech_vs_capital`）
- 大盘 LLM Prompt 补充多时间框架信号 + 市场状态元数据（ADX/regime/volatility_regime）
- 情绪反身性阈值添加说明（初始值，运行 3 个月后回测调整）
- Node 1 内部新增**时间框架 mini GOT**：short/medium/long 三路并行信号 → 规则检测跨框架矛盾（pullback_in_uptrend / bear_market_rally 等 5 种模式）→ 有矛盾时 LLM 研判 → 输出 `unified_trend` + `timeframe_confidence` 驱动仓位上限，替代旧的单一 trend 硬编码逻辑
- 消息面评分归一化重构：采用**阈值过滤 + 显著度加权**策略（参考 [MANA-Net, Wang et al. 2024](https://arxiv.org/abs/2409.05698) 的反均质化思路），low-impact 事件不参与评分计算，medium/high 按 impact_level 加权，分母为加权事件数而非原始事件数，避免大量噪音事件稀释高影响信号
- 时间一致性校验持久化方案明确：JSON 文件按交易日日期存储于 `data/aggregator_contexts/`，跨节假日取最近交易日数据，首日运行跳过校验

**设计决策：**
- 非交易日（周末/法定节假日/临时休市）不触发分析，系统自动跳过
- 板块精选概念列表仅覆盖科技成长方向，不做消费/地产/周期扩展

### v0.2 变更记录（2026-07-22 设计评审）

**P0 必修（3 项）：**
- **P0-1 情绪评分 Bug 修复**：LLM 输出改为 count 基础，后处理计算 ratio；消除 `total_posts` 恒等于 1.0 的无效归一化
- **P0-2 资金面增加北向资金**：新增沪/深股通维度（权重 0.25），龙虎榜降权至 0.30
- **P0-3 启动信号重构**：`all()` 12 条件 → 3 硬门槛 + 5 维加权打分，两级分级（strong_launch ≥ 0.75 / weak_launch ≥ 0.55）

**P1 核心架构（5 项）：**
- **P1-1 短板逻辑优化**：`min(创业板, 科创50)` → 加权平均（60/40）+ 一致性折扣（反向 7 折）
- **P1-2 多时间框架**：每个节点输出 short/medium/long 三套信号，聚合节点按时间框架分组检测矛盾
- **P1-3 ADX 动态调权**：引入 ADX 判断趋势/震荡状态，动态调整 5 指标权重（经典阈值 30/20/20）
- **P1-4 消息面 LLM 拆分**：单次调用 → 3 个独立 prompt 并发执行（事件提取/概念发现/风险总结），风格差异化
- **P1-5 消息面覆盖三层防护**：系统性政策筛选 + ≥3 家多源确认 + 历史验证（准确率 > 60%）+ 仓位日限幅 ±0.2

**P2 增强（5 项）：**
- **P2-1 板块生命周期 + 主线分级**：active_mainline 内部分 launch/acceleration/mature/late_stage 四阶段；主线 S/A/B 三级（系统输出依据，人工定级）
- **P2-2 情绪反身性逻辑**：5 阶段分类（euphoria/optimism/neutral/pessimism/capitulation），极端情绪为反向信号
- **P2-3 矛盾批处理**：LLM 逐对分析 → 一次调用处理所有矛盾，识别共同根因
- **P2-4 数据降级机制**：权重自动归一化 + 置信度按可用维度比例下调；降级维度不参与矛盾检测
- **P2-5 时间一致性校验**：4 类异常检测（趋势翻转/置信度波动/主线切换/仓位变化），critical 强制人工审核，仓位日限幅 ±20%

**P3 可选（逐项决策中）：**
- **P3-1 MACD 阶段标记**：`classify_macd_phase()` 四阶段分类（emerging_golden_cross / running_bullish / divergence_warning / death_cross_confirmed），输出含 `phase` + `phase_since_days`，聚合节点可据此做更精细判断
- **P3-2 Volume Profile**：新增 `calculate_volume_profile()` 函数，按价格 bin 聚合 60 日成交量，输出 POC（筹码最密集价格）+ Value Area（70% 成交量覆盖区间），整合到 `key_levels` 输出
- **P3-3 数据源切换**：NGA 板块关闭，情绪面数据源从 NGA 切换为雪球（API 相对友好，覆盖价值高）
- **P3-4 置信度校准**：暂不做。待运行 3-6 个月积累历史数据后，用 Platt Scaling 或 Isotonic Regression 对 LLM 输出的 confidence 做离线校准（需 100+ 样本）。当前用 P2-4 `confidence_multiplier` 过渡
- **P3-5 大宗交易维度**：新增第 5 个资金面维度（权重 0.10），折价 > 5% 为甩货信号、溢价 > 2% 为抢筹信号；缺失时复用降级机制。权重调整：龙虎榜 0.30→0.25、融资 0.20→0.15

---

## 1. 概述

本文档定义一个基于 **Chain-of-Thought（CoT）** 和 **Graph of Thought（GOT）** 混合架构的炒股 Agent。

**核心思路：**
- **前半段（GOT）**：两路并行 → 聚合
  - **独立数据路**：消息面 + 情绪面（并行采集，不依赖其他节点）
  - **技术分析路**：大盘 → 板块 → 资金面（串行，后者依赖前者）
- **后半段（CoT）**：线性流水线（持仓判断→选股评分→交易建议）

**交易风格：**
- A股为主，聚焦科技成长（AI、机器人、半导体）
- 中短线持仓（数周），全仓追随主线，不做T

**运行条件：**
- **仅交易日运行**：非交易日（周末、法定节假日、临时休市）不触发分析，系统自动跳过
- **板块精选概念列表**：仅覆盖科技成长相关板块（AI、机器人、半导体及其产业链），不做消费/地产/周期等方向

**输出：**
- 市场分析报告（给人看，Markdown 格式）
- 交易建议（买入/卖出/持有，人工审核后执行）

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  输入层                                                      │
│  - 用户指令（"分析今天市场" / "推荐短线机会"）                │
│  - 市场数据（通过工具获取）                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: GOT 市场分析（两路并行 + 聚合）                     │
│                                                              │
│  ┌─────────────── 独立数据路（并行） ───────────────┐       │
│  │  ┌──────────┐         ┌──────────┐              │       │
│  │  │ 消息面   │         │ 情绪面   │              │       │
│  │  │  Node 4  │         │  Node 5  │              │       │
│  │  │ 3并发LLM │         │ 反身性   │              │       │
│  │  │ +多源确认 │         │ 阶段分类  │              │       │
│  │  └──────────┘         └──────────┘              │       │
│  │       ↓                      ↓                  │       │
│  │  news_context         sentiment_context          │       │
│  │  (含source_count)     (含sentiment_stage)        │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│  ┌─────────────── 技术分析路（串行） ───────────────┐       │
│  │  ┌──────────┐    ┌──────────┐    ┌──────────┐  │       │
│  │  │ 大盘分析  │ → │ 板块分析  │ → │ 资金面   │  │       │
│  │  │  Node 1  │    │  Node 2  │    │  Node 3  │  │       │
│  │  │ ADX调权  │    │ 启动分级  │    │ 北向资金  │  │       │
│  │  │ 多时间框架│    │ 生命周期  │    │ 降级机制  │  │       │
│  │  │ 一致性折扣│    │ 主线分级  │    │ 4维重构   │  │       │
│  │  │ mini-GOT │    │          │    │          │  │       │
│  │  │(时间框架  │    │          │    │          │  │       │
│  │  │ 矛盾检测) │    │          │    │          │  │       │
│  │  └──────────┘    └──────────┘    └──────────┘  │       │
│  │       ↓               ↓               ↓        │       │
│  │  market_context   sector_context  capital_ctx   │       │
│  │  (unified_trend/  (lifecycle/     (northbound/  │       │
│  │   contradiction/   tier/phase)     degraded)    │       │
│  │   timeframe_got)                                │       │
│  └──────────────────────────────────────────────────┘       │
│                                                              │
│      ┌──────────────────────────────────────┐               │
│      │         聚合节点（Node 6）            │               │
│      │  - 8对矛盾检测（多时间框架对齐）      │               │
│      │  - LLM 批处理分析矛盾                 │               │
│      │  - 消息面三层覆盖防护                 │               │
│      │  - 时间一致性校验（4类异常）          │               │
│      │  - 降级感知 + 仓位日限幅±0.2          │               │
│      └──────────────────────────────────────┘               │
│                      ↓                                      │
│              unified_context                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: CoT 决策流水线（线性）                              │
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ 持仓判断  │ → │ 选股评分  │ → │ 交易建议  │              │
│  │  Node    │    │  Node    │    │  Node    │              │
│  └──────────┘    └──────────┘    └──────────┘              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  输出层                                                      │
│  - 市场分析报告（Markdown）                                   │
│  - 交易建议列表（股票 + 方向 + 仓位 + 理由）                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Phase 1: GOT 市场分析节点

### 3.1 节点 1：大盘分析（v2 — 4 维评分 + 跨指数一致性）

**职责：** 基于技术指标规则，判断各风格方向趋势，决定仓位上限。

**设计理念（v2 改进）：**
- **4 维评分**（原 5 维）：移除 market_breadth（涨跌家数数据源被封），MA/MACD/量价/布林归一化
- **跨指数一致性**（替代 market_breadth）：5 指数方向分类（broad_advance/decline/growth_lead/value_lead/divergent）
- **三因素 confidence**：跨指数一致性 35% + 时间框架一致性 35% + ADX 强度 30%（替代旧版 |score|）
- **score 级矛盾检测**：时间框架 mini GOT 改为 score 幅度差异检测 + 模式分类（pullback_in_uptrend 等）
- **T-0 实时修正**：技术面方向与当日实际方向不一致 → confidence ×0.7
- **LLM reasoning**：1 次调用生成 100 字研判（失败降级到规则拼接）
- **score→trend 阈值收窄**：±0.5 → ±0.3（提高区分度）

**覆盖指数：**
- 上证指数（000001.SH）：纯展示，不参与评分
- 沪深300（000300.SH）：大盘蓝筹方向
- 中证500（000905.SH）：中小盘方向
- 创业板指（399006.SZ）：科技成长方向（核心）
- 科创50（000688.SH）：科技成长方向（核心）

**风格分组：**
```yaml
style_groups:
  tech_growth:        # 科技成长（核心关注）
    indices: ["399006.SZ", "000688.SH"]  # 创业板指 + 科创50
    weights: {chinext: 0.6, star50: 0.4}  # 按市场影响力加权
    aggregation: weighted_average_with_consistency  # 加权平均 + 一致性折扣
  mid_small_cap:      # 中小盘
    indices: ["000905.SH"]  # 中证500
    aggregation: single
  large_cap:          # 大盘蓝筹
    indices: ["000300.SH"]  # 沪深300
    aggregation: single
  display:            # 纯展示
    indices: ["000001.SH"]  # 上证指数
    aggregation: none
```

**输入 Schema：**
```yaml
market_analysis_input:
  lookback_days: int  # 250（一年，确保MA250等长周期指标可算）
```

**工具调用：**
```python
# 获取5个指数的K线和成交量数据
# 注意：指数代码必须带 .SH/.SZ 后缀，否则 akshare fetcher 无法识别为指数
# 详见 v0.4 变更记录中的数据获取层 Bug 修复
index_codes = ["000001.SH", "000300.SH", "000905.SH", "399006.SZ", "000688.SH"]
index_data = await DataFetcher.get_index_kline(index_codes, lookback_days=250)
volume_data = await DataFetcher.get_index_volume(index_codes, lookback_days=250)

# 获取全市场涨跌家数和涨停/跌停统计（市场宽度）
market_breadth = await DataFetcher.get_market_breadth(lookback_days=5)
```

> **数据获取实现说明（v0.4）：**
> - `DataFetcherManager.get_daily_data()` 内部先检测 A 股指数（`_is_a_share_index()`），归一化后恢复 `.SH/.SZ` 后缀
> - akshare fetcher 通过后缀识别指数，路由到 `_fetch_index_data()` → `ak.stock_zh_index_daily(symbol="sz399006")`
> - efinance fetcher 不支持指数代码（带后缀会报错），自动降级到 akshare fetcher
> - 当前 5 个指数均通过 akshare fetcher 的新浪指数接口成功获取（2026-07-22 验证）

**技术指标计算（每个指数独立计算）：**

> **前置步骤：计算 ADX 判断市场状态，动态调整指标权重（P1-3 决策）**

```python
def calculate_adaptive_weights(index_data):
    """根据 ADX 判断趋势/震荡状态，动态调整指标权重"""

    adx_value = calculate_adx(index_data, period=14)

    if adx_value > 30:
        regime = "strong_trending"
        weights = {
            "ma_alignment": 0.20,    # 趋势中 MA 已充分反映，降权
            "macd": 0.30,            # 趋势持续性判断最重要
            "volume_price": 0.25,    # 量能验证趋势健康度
            "market_breadth": 0.15,
            "bollinger": 0.10,
        }
    elif adx_value > 20:
        regime = "mild_trending"
        weights = {
            "ma_alignment": 0.25,
            "macd": 0.25,
            "volume_price": 0.20,
            "market_breadth": 0.15,
            "bollinger": 0.15,
        }
    else:
        regime = "ranging"
        weights = {
            "ma_alignment": 0.10,    # 震荡市 MA 频繁翻转，大幅降权
            "macd": 0.20,
            "volume_price": 0.20,
            "market_breadth": 0.15,
            "bollinger": 0.35,       # 布林收口→张口是震荡市最可靠信号
        }

    # 极端波动率修正
    vol_regime = classify_volatility_regime(index_data)
    if vol_regime == "extreme_high":
        weights["market_breadth"] = min(weights["market_breadth"] + 0.10, 0.40)
    elif vol_regime == "extreme_low":
        weights["bollinger"] = min(weights["bollinger"] + 0.10, 0.45)

    # 归一化确保权重和为 1
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    return weights, regime, adx_value, vol_regime


def calculate_adx(index_data, period=14):
    """ADX：趋势强度指标（0-100）
    - >30: 强趋势
    - 20-30: 温和趋势
    - <20: 震荡
    """
    # ... True Range / +DM / -DM / DX / ADX 标准计算
    # 使用 Wilder's smoothing（RMA）
    pass  # 实现省略


def classify_volatility_regime(index_data, lookback=60):
    """波动率状态分类（当前 HV vs 历史百分位）"""
    returns = np.log(index_data.close / index_data.close.shift(1))
    current_hv = returns.tail(20).std() * np.sqrt(252)
    percentile = percentile_rank(current_hv, lookback)

    if percentile > 80:
        return "extreme_high"
    elif percentile < 20:
        return "extreme_low"
    else:
        return "normal"


def classify_macd_phase(index_data):
    """MACD 四阶段分类
    区分"刚金叉"与"金叉已运行多日"，为聚合节点提供更精细的信号语义。
    """
    macd_line = index_data.macd
    signal_line = index_data.macd_signal
    histogram = index_data.macd_hist
    close = index_data.close

    # 金叉/死叉判定（当前 vs 前一日）
    golden_cross = (macd_line.iloc[-1] > signal_line.iloc[-1] and
                    macd_line.iloc[-2] <= signal_line.iloc[-2])
    death_cross = (macd_line.iloc[-1] < signal_line.iloc[-1] and
                   macd_line.iloc[-2] >= signal_line.iloc[-2])

    if golden_cross:
        return {"phase": "emerging_golden_cross", "since_days": 1}
    elif death_cross:
        return {"phase": "death_cross_confirmed", "since_days": 1}
    elif macd_line.iloc[-1] > signal_line.iloc[-1]:
        # 检查顶背离：价格近 20 日新高，但 MACD 柱未创新高
        price_new_high = close.iloc[-1] >= close.tail(20).max()
        macd_not_new_high = histogram.iloc[-1] < histogram.tail(20).max()
        if price_new_high and macd_not_new_high:
            return {"phase": "divergence_warning", "since_days": 1}
        # 计算当前多头阶段持续天数（从最近一次死叉/金叉算起）
        since = count_days_since_cross(macd_line, signal_line, direction="up")
        return {"phase": "running_bullish", "since_days": since}
    else:
        since = count_days_since_cross(macd_line, signal_line, direction="down")
        return {"phase": "running_bearish", "since_days": since}
```

**技术指标计算（每个指数独立计算）：**

```python
def calculate_indicators(index_data, market_breadth):
    """计算 5 个技术指标 + ADX 动态调权，返回加权得分"""

    # 前置：ADX 动态调权
    weights, regime, adx_value, vol_regime = calculate_adaptive_weights(index_data)

    # 1. MA排列
    ma_score = calculate_ma_alignment(
        data=index_data,
        periods=[5, 10, 20, 60, 120, 250]
    )
    # 多头排列（MA5>10>20>60>120）→ +1
    # 空头排列（MA5<10<20<60<120）→ -1
    # 混乱纠缠 → 0

    # 2. MACD - 分层判断 + 阶段标记（权重由 ADX 动态决定）
    macd_score = calculate_macd_layered(index_data)
    macd_phase_info = classify_macd_phase(index_data)  # 返回 {"phase": "...", "since_days": N}
    # 第一层：0轴位置
    #   双线在0轴上方 → 偏多
    #   双线在0轴下方 → 偏空
    # 第二层：红绿柱动能
    #   红柱扩张 → +0.5
    #   红柱缩短 → 0
    #   绿柱缩短 → 0
    #   绿柱扩张 → -0.5
    # 第三层：金叉死叉确认
    #   0轴上方金叉 → 额外+0.5
    #   0轴下方死叉 → 额外-0.5
    # 第四层：阶段标记（新增）
    #   emerging_golden_cross → 即将/刚金叉，关注后续确认
    #   running_bullish       → 金叉后持续运行，趋势延续
    #   divergence_warning    → 价格新高但 MACD 未新高，顶背离风险
    #   death_cross_confirmed → 死叉确认，趋势转弱

    # 3. 量价关系
    volume_price_score = calculate_volume_price(index_data)
    # 放量上涨 / 缩量回调 → +1
    # 放量下跌 / 缩量反弹 → -1
    # 缩量横盘 → 0

    # 4. 市场宽度
    breadth_score = calculate_market_breadth(market_breadth)
    # 涨停 > 跌停×3 且 涨跌比 > 2:1 → +1
    # 跌停 > 涨停×2 → -1
    # 正常 → 0

    # 5. 布林带
    bollinger_score = calculate_bollinger(index_data)
    # 站上中轨 + 张口向上 → +1
    # 跌破中轨 + 张口向下 → -1
    # 收口 → 0

    # 加权求和（使用 ADX 动态调整的权重）
    weighted_score = (
        ma_score * weights["ma_alignment"] +
        macd_score * weights["macd"] +
        volume_price_score * weights["volume_price"] +
        breadth_score * weights["market_breadth"] +
        bollinger_score * weights["bollinger"]
    )

    return {
        "ma_alignment": ma_score,
        "macd": {
            "score": macd_score,
            "phase": macd_phase_info["phase"],          # emerging_golden_cross / running_bullish / divergence_warning / death_cross_confirmed / running_bearish
            "phase_since_days": macd_phase_info["since_days"],
        },
        "volume_price": volume_price_score,
        "market_breadth": breadth_score,
        "bollinger": bollinger_score,
        "weighted_score": weighted_score,
        # ADX 元信息（透传给下游）
        "adx_value": adx_value,
        "regime": regime,
        "volatility_regime": vol_regime,
        "weights_used": weights,  # 便于归因和回测
    }
```

**风格组合并逻辑（加权平均 + 一致性折扣）：**

```python
def aggregate_style_group(indicator_results, style_group):
    """风格组合并：加权平均 + 一致性折扣"""

    if style_group.aggregation == "weighted_average_with_consistency":
        # 按市场影响力加权（如 tech_growth：创业板 0.6 + 科创50 0.4）
        weights = style_group.weights  # {chinext: 0.6, star50: 0.4}
        scores = [indicator_results[idx].weighted_score for idx in style_group.indices]
        weighted_scores = [s * w for s, w in zip(scores, weights.values())]
        base_score = sum(weighted_scores) / sum(weights.values())

        # 一致性检测：两者是否同向
        signs = [1 if s > 0.1 else (-1 if s < -0.1 else 0) for s in scores]
        same_direction = all(s == signs[0] for s in signs) and signs[0] != 0

        # 同向：满分；反向：打 7 折
        consistency_discount = 1.0 if same_direction else 0.7
        final_score = base_score * consistency_discount
        consistency_label = "aligned" if same_direction else "divergent"

    elif style_group.aggregation == "single":
        final_score = indicator_results[style_group.indices[0]].weighted_score
        consistency_label = "single"
    else:
        final_score = None
        consistency_label = "none"

    # 趋势判断
    if final_score > 0.5:
        trend = "bullish"
    elif final_score < -0.5:
        trend = "bearish"
    else:
        trend = "neutral"

    return {
        "trend": trend,
        "score": final_score,
        "consistency": consistency_label,  # 透传一致性信息给聚合节点
    }
```

**多时间框架信号计算（v0.4 修复）：**

> **v0.4 修复说明：** 初始设计用 `df.tail(N)` 切片后计算指标，但短窗口（5日）数据不足以计算 MA60/MA250、MACD(12,26,9)、ADX(14) 等长周期指标，全部返回 0。
> 正确方案：使用**完整数据集**计算指标，不同时间框架通过**不同参数敏感度**区分。

```python
def calculate_timeframe_signals(df: pd.DataFrame) -> dict:
    """多时间框架信号：用完整数据，不同框架用不同参数敏感度

    设计理念：
    - 短期（5日）：MA 周期 [5, 10, 20]，量价 lookback 5 日 → 对近期波动敏感
    - 中期（20日）：MA 周期 [5, 10, 20, 60]，量价 lookback 20 日 → 确认趋势方向
    - 长期（60日）：MA 周期 [10, 20, 60, 120, 250]，量价 lookback 60 日 → 反映大级别趋势

    真实数据验证（2026-07-22）：
    - 短期 bearish(-0.65)、中期 bearish(-0.65)、长期 neutral(-0.34)
    - 信号分层合理：短中期一致看空，长期偏中性（回调格局而非趋势反转）
    """

    def _score_timeframe(df, ma_periods, recent_days):
        ma_score = calculate_ma_alignment_score(df, periods=ma_periods)
        macd_score = calculate_macd_score(df)
        volume_price_score = calculate_volume_price_score(df, lookback=recent_days)
        bollinger_score = calculate_bollinger_score(df)
        return (
            ma_score * 0.30 +
            macd_score * 0.30 +
            volume_price_score * 0.25 +
            bollinger_score * 0.15
        )

    return {
        "short_term": _score_timeframe(df, ma_periods=[5, 10, 20], recent_days=5),
        "medium_term": _score_timeframe(df, ma_periods=[5, 10, 20, 60], recent_days=20),
        "long_term": _score_timeframe(df, ma_periods=[10, 20, 60, 120, 250], recent_days=60),
    }
```

**仓位上限计算（多时间框架 mini GOT）：**

> **设计理念：** 三个时间框架（short/medium/long）作为三路并行信号输入，规则检测跨框架矛盾，有矛盾时触发 LLM 研判，无矛盾时按规则合成。

```python
# === 时间框架 mini GOT 结构 ===
#
#  short(5d)    medium(20d)    long(60d)    ← 三路并行输入
#       \            |            /
#        \           |           /
#    规则检测跨时间框架矛盾（6 对）
#           ↓
#      有矛盾？──否──→ 规则合成（同向取最强/最弱）
#           ↓ 是
#    LLM 研判（一次调用，带完整技术上下文）
#           ↓
#      unified_trend + confidence_adjustment
#           ↓
#    calculate_position_limit（风格间折扣）


def aggregate_timeframe_signals(signals, regime_metadata):
    """时间框架 mini GOT：跨框架矛盾检测 + LLM 合成

    Args:
        signals: {short_term: {trend, score}, medium_term: {trend, score}, long_term: {trend, score}}
        regime_metadata: {adx_value, regime, volatility_regime}

    Returns:
        {unified_trend, unified_score, contradiction_type, llm_reasoning}
    """

    short = signals["short_term"]
    medium = signals["medium_term"]
    long_ = signals["long_term"]

    # === 规则检测：跨时间框架矛盾 ===
    contradictions = []

    # 短期 vs 中期
    if short["trend"] != medium["trend"] and short["trend"] != "neutral" and medium["trend"] != "neutral":
        contradiction_type = _classify_tf_contradiction("short", short["trend"], "medium", medium["trend"])
        contradictions.append({
            "pair": "short_vs_medium",
            "short": short["trend"],
            "medium": medium["trend"],
            "pattern": contradiction_type,
        })

    # 中期 vs 长期
    if medium["trend"] != long_["trend"] and medium["trend"] != "neutral" and long_["trend"] != "neutral":
        contradiction_type = _classify_tf_contradiction("medium", medium["trend"], "long", long_["trend"])
        contradictions.append({
            "pair": "medium_vs_long",
            "medium": medium["trend"],
            "long": long_["trend"],
            "pattern": contradiction_type,
        })

    # 短期 vs 长期
    if short["trend"] != long_["trend"] and short["trend"] != "neutral" and long_["trend"] != "neutral":
        contradiction_type = _classify_tf_contradiction("short", short["trend"], "long", long_["trend"])
        contradictions.append({
            "pair": "short_vs_long",
            "short": short["trend"],
            "long": long_["trend"],
            "pattern": contradiction_type,
        })

    # === 无矛盾：规则合成 ===
    if not contradictions:
        # 三框架同向 → 取加权分（长期权重大，短期权重小）
        unified_score = (
            short["score"] * 0.20 +
            medium["score"] * 0.35 +
            long_["score"] * 0.45
        )
        unified_trend = "bullish" if unified_score > 0.5 else ("bearish" if unified_score < -0.5 else "neutral")
        return {
            "unified_trend": unified_trend,
            "unified_score": unified_score,
            "contradiction_type": None,
            "timeframe_confidence": 1.0,  # 三框架一致，置信度最高
        }

    # === 有矛盾：LLM 研判 ===
    return _resolve_timeframe_contradiction(contradictions, signals, regime_metadata)


def _classify_tf_contradiction(tf_a, trend_a, tf_b, trend_b):
    """识别跨时间框架矛盾的交易语义"""
    # 短期回调 + 中期多头 = 回调买点（看多）
    if tf_a == "short" and trend_a == "bearish" and tf_b == "medium" and trend_b == "bullish":
        return "pullback_in_uptrend"
    # 短期反弹 + 中期空头 = 反弹诱多（看空）
    if tf_a == "short" and trend_a == "bullish" and tf_b == "medium" and trend_b == "bearish":
        return "bear_market_rally"
    # 中期回调 + 长期多头 = 趋势内修正（偏多）
    if tf_a == "medium" and trend_a == "bearish" and tf_b == "long" and trend_b == "bullish":
        return "correction_in_long_uptrend"
    # 中期多头 + 长期空头 = 逆趋势反弹（偏空）
    if tf_a == "medium" and trend_a == "bullish" and tf_b == "long" and trend_b == "bearish":
        return "counter_trend_rally"
    return "unknown_divergence"


async def _resolve_timeframe_contradiction(contradictions, signals, regime_metadata):
    """LLM 研判跨时间框架矛盾，输出统一信号"""
    contradictions_text = "\n".join([
        f"- {c['pair']}: {c['short' if 'short' in c['pair'] else c['medium']]} vs "
        f"{c.get('medium', c.get('long'))}（{c['pattern']}）"
        for c in contradictions
    ])

    prompt = f"""
你是资深技术分析师。检测到跨时间框架信号矛盾，请研判后给出统一判断。

三框架信号：
- 短期（5日）：{signals['short_term']['trend']}（{signals['short_term']['score']:.2f}）
- 中期（20日）：{signals['medium_term']['trend']}（{signals['medium_term']['score']:.2f}）
- 长期（60日）：{signals['long_term']['trend']}（{signals['long_term']['score']:.2f}）

市场状态：ADX={regime_metadata.adx_value}（{regime_metadata.regime}），波动率={regime_metadata.volatility_regime}

矛盾列表：
{contradictions_text}

请输出（JSON）：
1. unified_trend: "bullish" | "bearish" | "neutral"
2. unified_score: float（-1.0 ~ 1.0）
3. timeframe_confidence: float（0~1，框架一致性越高越接近 1）
4. reasoning: str（100字以内，说明为什么取这个方向）
"""
    result = await llm.call(prompt)
    result["contradiction_type"] = contradictions[0]["pattern"]
    return result


def calculate_position_limit(timeframe_got_result, mid_small_cap_trend, large_cap_trend):
    """基于时间框架 mini GOT 的统一信号计算仓位上限"""
    unified_trend = timeframe_got_result["unified_trend"]
    tf_confidence = timeframe_got_result.get("timeframe_confidence", 1.0)

    # 基础仓位由 unified_trend 决定
    if unified_trend == "bullish":
        base_limit = 1.0
    elif unified_trend == "neutral":
        base_limit = 0.5
    else:  # bearish
        base_limit = 0.0

    # 时间框架矛盾时，置信度折扣（LLM 已给 timeframe_confidence）
    base_limit *= tf_confidence

    # 极端风险折扣：仅当中小盘+大盘蓝筹同时空头（系统性风险）
    if mid_small_cap_trend == "bearish" and large_cap_trend == "bearish":
        base_limit *= 0.5

    return base_limit
```

**置信度计算（指标一致性）：**

```python
def calculate_confidence(tech_growth_indicators):
    """置信度 = |加权得分| / 最大可能分"""
    max_possible_score = (
        1.0 * 0.30 +  # MA
        1.0 * 0.25 +  # MACD
        1.0 * 0.20 +  # 量价
        1.0 * 0.15 +  # 市场宽度
        1.0 * 0.10    # 布林带
    )  # = 1.0

    confidence = abs(tech_growth_indicators.weighted_score) / max_possible_score
    return confidence
```

**支撑/阻力位计算：**

```python
def calculate_volume_profile(index_data, lookback=60, num_bins=50):
    """Volume Profile：按价格 bin 聚合成交量，找筹码密集区
    - POC（Point of Control）：成交量最大的价格 → 最强支撑/阻力
    - Value Area：覆盖 70% 成交量的价格区间（上下界）
    """
    highs = index_data.high.tail(lookback)
    lows = index_data.low.tail(lookback)
    closes = index_data.close.tail(lookback)
    volumes = index_data.volume.tail(lookback)

    price_min, price_max = lows.min(), highs.max()
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    volume_profile = np.zeros(num_bins)

    for i in range(lookback):
        typical_price = (highs.iloc[i] + lows.iloc[i] + closes.iloc[i]) / 3
        bin_idx = np.digitize(typical_price, bin_edges) - 1
        volume_profile[bin_idx] += volumes.iloc[i]

    # POC：成交量最密集的价格
    poc_idx = np.argmax(volume_profile)
    poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2

    # Value Area：从 POC 向两侧扩展，直到覆盖 70% 总成交量
    total_volume = volume_profile.sum()
    va_volume = volume_profile[poc_idx]
    va_low_idx, va_high_idx = poc_idx, poc_idx
    while va_volume / total_volume < 0.70:
        expand_low = volume_profile[va_low_idx - 1] if va_low_idx > 0 else 0
        expand_high = volume_profile[va_high_idx + 1] if va_high_idx < num_bins - 1 else 0
        if expand_low >= expand_high and va_low_idx > 0:
            va_low_idx -= 1
            va_volume += volume_profile[va_low_idx]
        elif va_high_idx < num_bins - 1:
            va_high_idx += 1
            va_volume += volume_profile[va_high_idx]
        else:
            break

    return {
        "poc": poc_price,
        "value_area_high": bin_edges[va_high_idx + 1],
        "value_area_low": bin_edges[va_low_idx],
    }


def calculate_key_levels(index_data):
    """短期（1-2周）+ 中期（1-3月）两组支撑阻力 + Volume Profile"""

    # 短期支撑阻力
    short_term_support = max(
        calculate_ma(index_data, period=20),  # MA20
        min(index_data.close[-20:])           # 近20日最低价
    )
    short_term_resistance = max(
        index_data.high[-20:],                # 近20日最高价
        calculate_bollinger_upper(index_data) # 布林上轨
    )

    # 中期支撑阻力
    mid_term_support = min(
        calculate_ma(index_data, period=60),  # MA60
        find_volume_dense_zone(index_data, direction="support")  # 成交密集区下沿
    )
    mid_term_resistance = max(
        max(index_data.high[-60:]),           # 近60日最高价
        find_volume_dense_zone(index_data, direction="resistance")  # 成交密集区上沿
    )

    # Volume Profile（P3-2 决策）
    volume_profile = calculate_volume_profile(index_data, lookback=60)

    return {
        "short_term": {
            "support": short_term_support,
            "resistance": short_term_resistance
        },
        "mid_term": {
            "support": mid_term_support,
            "resistance": mid_term_resistance
        },
        "volume_profile": volume_profile,  # poc / value_area_high / value_area_low
    }
```

**输出 Schema：**
```yaml
market_analysis_output:
  tech_growth:
    trend: str          # "bullish" | "bearish" | "neutral"
    score: float        # 加权得分（-1.0 ~ 1.0）
    indicators:
      ma_alignment: float
      macd:
        score: float            # 分层评分（-1.0 ~ 1.0）
        phase: str              # "emerging_golden_cross" | "running_bullish" | "divergence_warning" | "death_cross_confirmed" | "running_bearish"
        phase_since_days: int   # 当前阶段已持续天数
      volume_price: float
      market_breadth: float
      bollinger: float
    constituents:
      chinext: float    # 创业板指得分
      star50: float     # 科创50得分
    consistency: str    # "aligned" | "divergent"（创业板/科创一致性）

    # === 多时间框架信号（P1-2 决策）===
    signals:
      short_term:       # 5 日
        trend: str
        score: float
      medium_term:      # 20 日
        trend: str
        score: float
      long_term:        # 60 日
        trend: str
        score: float
    # === 时间框架 mini GOT 输出（v0.3 新增）===
    timeframe_got:
      unified_trend: str              # 经矛盾检测后的统一趋势（bullish/bearish/neutral）
      unified_score: float            # 统一评分（-1.0 ~ 1.0）
      contradiction_type: str | null  # "pullback_in_uptrend" | "bear_market_rally" | "correction_in_long_uptrend" | "counter_trend_rally" | null
      timeframe_confidence: float     # 框架一致性置信度（0~1，三框架同向=1.0）
      llm_reasoning: str              # 有矛盾时的 LLM 研判理由（无矛盾时为空）

    # === 市场状态元信息（P1-3 决策）===
    regime_metadata:
      adx_value: float             # ADX 数值（0-100）
      regime: str                  # "strong_trending" | "mild_trending" | "ranging"
      volatility_regime: str       # "extreme_high" | "normal" | "extreme_low"
      weights_used:                # 实际使用的权重，便于归因
        ma_alignment: float
        macd: float
        volume_price: float
        market_breadth: float
        bollinger: float

  mid_small_cap:
    trend: str
    score: float
    signals:                       # 多时间框架（同上结构）
      short_term: {trend, score}
      medium_term: {trend, score}
      long_term: {trend, score}
  large_cap:
    trend: str
    score: float
    signals:
      short_term: {trend, score}
      medium_term: {trend, score}
      long_term: {trend, score}
  display:              # 纯展示，不参与评分
    shanghai_composite:
      current: float
      change_pct: float
      trend_desc: str
  position_limit: float # 最终仓位上限（经时间框架 mini GOT + 风格风险折扣后）
  confidence: float     # 指标一致性（0.0 ~ 1.0）
  key_levels:
    short_term:
      support: float
      resistance: float
    mid_term:
      support: float
      resistance: float
    volume_profile:           # P3-2 决策：筹码密集区
      poc: float              # Point of Control（成交量最密集价格，核心支撑/阻力）
      value_area_high: float  # Value Area 上界（覆盖 70% 成交量）
      value_area_low: float   # Value Area 下界
  reasoning: str        # LLM生成的推理文本（基于上述数值结论）
```

**LLM Prompt 模板（仅生成推理文本）：**
```
你是一个专业的大盘分析师。根据以下技术分析结果，撰写一段简洁的推理报告。

技术分析结果：
- 科技成长：{tech_growth.trend}（得分：{tech_growth.score}）
  - 创业板指得分：{tech_growth.constituents.chinext}
  - 科创50得分：{tech_growth.constituents.star50}
  - MA排列：{tech_growth.indicators.ma_alignment}
  - MACD：{tech_growth.indicators.macd}
  - 量价关系：{tech_growth.indicators.volume_price}
  - 市场宽度：{tech_growth.indicators.market_breadth}
  - 布林带：{tech_growth.indicators.bollinger}
  - 市场状态：ADX={tech_growth.regime_metadata.adx_value}，{tech_growth.regime_metadata.regime}，波动率={tech_growth.regime_metadata.volatility_regime}
  - 多时间框架信号：
    - 短期（5日）：{tech_growth.signals.short_term.trend}（{tech_growth.signals.short_term.score}）
    - 中期（20日）：{tech_growth.signals.medium_term.trend}（{tech_growth.signals.medium_term.score}）
    - 长期（60日）：{tech_growth.signals.long_term.trend}（{tech_growth.signals.long_term.score}）
    - 主导框架：{tech_growth.primary_timeframe}
- 中小盘：{mid_small_cap.trend}（得分：{mid_small_cap.score}）
- 大盘蓝筹：{large_cap.trend}（得分：{large_cap.score}）
- 上证指数：{display.shanghai_composite.trend_desc}
- 建议仓位上限：{position_limit * 100}%
- 短期支撑/阻力：{key_levels.short_term.support} / {key_levels.short_term.resistance}
- 中期支撑/阻力：{key_levels.mid_term.support} / {key_levels.mid_term.resistance}
- 筹码密集区（Volume Profile）：POC = {key_levels.volume_profile.poc}，Value Area = {key_levels.volume_profile.value_area_low} ~ {key_levels.volume_profile.value_area_high}

请输出：
1. 一段200字以内的推理文本，解释为什么得出这个结论
2. 重点说明科技成长方向的趋势判断依据
3. 说明多时间框架信号是否一致，如有矛盾需解释
4. 提示关键风险点

输出格式：纯文本（不要JSON）
```

---

### 3.2 节点 2：板块分析

**职责：** 识别板块状态（主线/衰竭/埋伏/防守），判断启动和过热信号，为选股提供方向。

**设计理念：**
- **分状态输出**：不是简单按强度排序，而是按4种状态分层
- **核心目标**：找主线（判断持续性）+ 找埋伏（高切低提前布局）
- **纯规则驱动**：强度评分、状态判断、信号识别均由规则计算，LLM只生成推理文本
- **龙头识别后置**：龙头股需要舆情数据，放到聚合节点统一计算

**精选概念列表（按产业链组织）：**
```yaml
concept_universe:
  # === AI主线（进攻） ===
  ai_mainline:
    upstream:
      - semiconductor_equipment  # 半导体设备
      - semiconductor_materials  # 半导体材料
      - ai_chips                 # AI芯片

    infrastructure:
      - server_hardware          # 服务器/算力
      - aidc                     # AI数据中心
      - liquid_cooling           # 液冷散热
      - computing_power_electricity # 算电协同

    midstream:
      - llm_models               # 大模型
      - embodied_ai              # 具身智能

    downstream:
      - humanoid_robots          # 人形机器人整机
      - ai_applications          # AI应用
      - ai_agents                # AI Agent

    components:
      - reducers                 # 减速器
      - screws                   # 丝杠
      - sensors                  # 传感器
      - motors                   # 电机

  # === 科技防守板块 ===
  tech_defensive:
    - innovative_drugs           # 创新药
    - power_grid                 # 电网
    - electricity                # 电力
    - energy_storage             # 储能
```

**输入 Schema：**
```yaml
sector_analysis_input:
  market_context:          # 来自"大盘分析"节点的输出
    tech_growth:
      trend: str
      score: float
    position_limit: float
  concept_list: list[str]  # 精选概念列表（约25-30个）
```

**工具调用：**
```python
# 获取每个概念板块的K线、成交量、成分股数据
sector_data = {}
for concept in concept_list:
    kline = await DataFetcher.get_sector_kline(concept, lookback_days=60)
    volume = await DataFetcher.get_sector_volume(concept, lookback_days=60)
    constituents = await DataFetcher.get_sector_constituents(concept)
    sector_data[concept] = {"kline": kline, "volume": volume, "constituents": constituents}
```

**板块强度评分（5维加权）：**
```python
def calculate_sector_strength(sector_data, market_context):
    """计算板块综合强度分"""

    # 1. 量价关系（权重0.30）
    volume_price_score = calculate_volume_price(sector_data)
    # 放量上涨/缩量回调 → +1
    # 放量下跌/缩量反弹 → -1

    # 2. 价格动量（权重0.25）
    price_momentum = clip(
        sector_data.kline.close[-1] / sector_data.kline.close[-5] - 1,  # 近5日涨幅
        -0.15, 0.15  # clip 到合理范围，避免极端值主导评分
    )

    # 3. 市场宽度（权重0.20）
    breadth_score = (
        sum(1 for stock in sector_data.constituents if stock.close > stock.ma20)
        / len(sector_data.constituents)
    )

    # 4. 相对强度（权重0.15）
    relative_strength = clip(
        (sector_data.kline.close[-1] / sector_data.kline.close[-20] - 1)  # 板块近20日涨幅
        - (market_context.tech_growth.index_close[-1] / market_context.tech_growth.index_close[-20] - 1),  # 大盘同期涨幅
        -0.15, 0.15
    )

    # 5. 资金流向（权重0.10，辅助参考）
    capital_flow_score = calculate_capital_flow(sector_data)

    # 加权求和
    strength_score = (
        volume_price_score * 0.30 +
        price_momentum * 0.25 +
        breadth_score * 0.20 +
        relative_strength * 0.15 +
        capital_flow_score * 0.10
    )

    return {
        "volume_price": volume_price_score,
        "price_momentum": price_momentum,
        "market_breadth": breadth_score,
        "relative_strength": relative_strength,
        "capital_flow": capital_flow_score,
        "strength_score": strength_score
    }
```

**板块分析完整流程：**
```python
def analyze_sector(sector_data, market_context):
    """板块分析：指标→信号→状态"""
    
    # Step 1: 计算5维强度指标
    indicators = calculate_sector_strength(sector_data, market_context)
    
    # Step 2: 计算启动信号（三维共振）
    launch_signal = detect_launch_signal(sector_data)
    
    # Step 3: 计算过热信号
    overheat_signal, overheat_type = detect_overheat_signal(sector_data)
    
    # Step 4: 综合判断状态（信号作为输入参数）
    state = determine_sector_state(
        indicators=indicators,
        launch_signal=launch_signal,
        overheat_signal=overheat_signal,
        sector_data=sector_data,
        market_context=market_context
    )
    
    # Step 5: 组装输出
    return {
        "state": state,
        "indicators": indicators,
        "days_since_launch": calculate_days_since_launch(sector_data) if state == "active_mainline" else None,
        "days_in_exhaustion": calculate_days_since_overheat(sector_data) if state == "exhausted_mainline" else None,
        # ... other state-specific fields
    }
```

**板块状态判断（适配新启动信号 + 生命周期，P2-1 决策）：**
```python
def determine_sector_state(indicators, launch_signal, overheat_signal, sector_data, market_context):
    """状态判断逻辑（适配新的启动信号格式）"""

    # 优先级：启动信号 > 过热信号 > 其他条件

    # 1. 启动信号触发 → 刚进入 active_mainline
    if launch_signal["triggered"]:
        return "active_mainline", launch_signal["level"]  # 返回 strong_launch/weak_launch

    # 2. 过热信号触发 → 进入 exhausted_mainline
    if overheat_signal:
        return "exhausted_mainline", None

    # 3. 持续强势（非刚启动）→ active_mainline
    if (
        indicators.strength_score > 70
        and indicators.volume_price > 0.5
        and indicators.market_breadth > 0.7
    ):
        return "active_mainline", "sustained"

    # 4. 持续衰弱（非刚过热）→ exhausted_mainline
    if (
        sector_data.kline.close[-1] / sector_data.kline.close[-20] > 1.15
        and indicators.volume_price < -0.3
    ):
        return "exhausted_mainline", None

    # 5. 埋伏条件
    if (
        calculate_volatility(sector_data, days=20) < 0.10
        and sector_data.volume[-1] > sector_data.volume_ma20 * 1.3
        and calculate_bollinger_width(sector_data) < percentile_20_60d
    ):
        return "ambush", None

    # 6. 防守轮动（仅用 Node 1 已有信号，资金面数据在 Node 3 尚未产生）
    if (
        market_context.tech_growth.trend == "bearish"
        and indicators.capital_flow > 2e8
        and calculate_correlation(sector_data, market_context.tech_growth) < 0.3
    ):
        return "defensive_rotation", None

    return "neutral", None


def classify_mainline_tier(sector_data, news_context):
    """主线级别判定（系统输出依据，人工最终定级，P2-1 决策）"""
    score = 0
    reasons = []

    # 政策支撑（+3 分 = S 级候选）
    if has_national_policy_support(sector_data, news_context):
        score += 3
        reasons.append("国家级政策驱动")

    # 市场空间（+2 分 = 千亿级以上）
    if estimated_market_size(sector_data) > 1e11:
        score += 2
        reasons.append("市场空间>千亿")

    # 产业链长度（+1 分 = 跨 3 个以上子产业）
    if count_sub_industries(sector_data) >= 3:
        score += 1
        reasons.append("产业链长度≥3")

    # 龙头公司市值（+1 分 = 有千亿市值龙头）
    if has_mega_cap_leader(sector_data):
        score += 1
        reasons.append("有千亿市值龙头")

    # 持续性（+1 分 = 已运行 > 20 天且仍强势）
    if sector_data.days_since_launch > 20 and sector_data.strength_score > 70:
        score += 1
        reasons.append("已运行>20天仍强势")

    if score >= 5:
        suggested_tier = "S"
    elif score >= 3:
        suggested_tier = "A"
    else:
        suggested_tier = "B"

    return {"suggested_tier": suggested_tier, "score": score, "reasons": reasons}


def get_lifecycle_thresholds(tier):
    """根据主线级别返回生命周期阈值"""
    return {
        "S": {"launch": 7, "acceleration": 30, "mature": 60},
        "A": {"launch": 5, "acceleration": 15, "mature": 30},
        "B": {"launch": 3, "acceleration": 10, "mature": 20},
    }[tier]


def classify_lifecycle_phase(days_since_launch, tier, overheat_signals):
    """板块生命周期分层（P2-1 决策）"""
    if overheat_signals:
        return "mature_exhausted"

    thresholds = get_lifecycle_thresholds(tier)
    if days_since_launch <= thresholds["launch"]:
        return "launch"
    elif days_since_launch <= thresholds["acceleration"]:
        return "acceleration"
    elif days_since_launch <= thresholds["mature"]:
        return "mature"
    else:
        return "late_stage"
```

**启动信号检测（3 硬门槛 + 5 维加权，P0-3 决策）：**
```python
def detect_launch_signal(sector_data):
    """启动信号：核心门槛 + 加权打分（替代旧 all() 逻辑）"""

    # === 核心门槛条件（必须全部满足，否则直接 False）===
    hard_gates = [
        # 必须脱离横盘（已突破）
        sector_data.kline.close[-1] > max(sector_data.kline.high[-15:]),
        # 必须有量能配合（不能是缩量上涨，放宽到 1.3 倍）
        sector_data.volume[-1] > sector_data.volume_ma3 * 1.3,
        # 必须站上 MA20
        sector_data.kline.close[-1] > sector_data.ma20,
    ]
    if not all(hard_gates):
        return {"triggered": False, "score": 0.0, "level": "none"}

    # === 软性维度打分（0~1 之间的分数）===
    dimensions = {
        "consolidation": calculate_consolidation_score(sector_data),
        "volume_expansion": calculate_volume_expansion_score(sector_data),
        "price_strength": calculate_price_strength_score(sector_data),
        "trend_alignment": calculate_trend_alignment_score(sector_data),
        "breadth": calculate_breadth_score(sector_data),
    }

    weights = {
        "consolidation": 0.25,
        "volume_expansion": 0.25,
        "price_strength": 0.20,
        "trend_alignment": 0.15,
        "breadth": 0.15,
    }
    launch_score = sum(dimensions[k] * weights[k] for k in dimensions)

    # 阈值分级（P0-3 决策：两级分级）
    if launch_score >= 0.75:
        level = "strong_launch"    # 强启动（可重仓追）
    elif launch_score >= 0.55:
        level = "weak_launch"      # 弱启动（试探性买入）
    else:
        level = "none"

    return {
        "triggered": level != "none",
        "score": launch_score,
        "level": level,
        "dimensions": dimensions,  # 透传维度明细供下游参考
    }


def calculate_consolidation_score(sector_data):
    """横盘整理度：近 15-30 日波动越小、时间越长 → 分数越高"""
    range_15d = (max(sector_data.kline.high[-15:]) -
                 min(sector_data.kline.low[-15:])) / sector_data.kline.close[-15]
    # 15 日振幅 < 8% 得满分，> 15% 得 0 分
    range_score = clip((0.15 - range_15d) / 0.07, 0, 1)
    # 布林带宽也作为横盘参考
    boll_width = calculate_bollinger_width(sector_data)
    boll_percentile = percentile_rank(boll_width, lookback=60)
    boll_score = clip((30 - boll_percentile) / 30, 0, 1)
    return 0.6 * range_score + 0.4 * boll_score


def calculate_volume_expansion_score(sector_data):
    """量能扩张度：倍量但非天量"""
    volume_ratio = sector_data.volume[-1] / sector_data.volume_ma3
    if volume_ratio < 1.5:
        return 0.0
    elif volume_ratio <= 3.0:
        return (volume_ratio - 1.5) / 1.5
    elif volume_ratio <= 5.0:
        return 1.0
    else:
        return max(0, 1.0 - (volume_ratio - 5.0) / 5.0)  # 天量扣分


def calculate_breadth_score(sector_data):
    """广度：成分股站上 MA20 的比例"""
    ratio = (sum(1 for s in sector_data.constituents
                 if s.close > s.ma20)
             / len(sector_data.constituents))
    # 0.4 起步，0.7 满分
    return clip((ratio - 0.4) / 0.3, 0, 1)
```

**过热信号检测（任一触发）：**
```python
def detect_overheat_signal(sector_data):
    """过热信号（任一触发即返回True）"""
    signals = []

    # 强MACD背离
    if (
        sector_data.kline.close[-1] > max(sector_data.kline.close[-20:])  # 价格新高
        and sector_data.macd_dif[-1] < max(sector_data.macd_dif[-20:]) * 0.8  # DIF明显走低
    ):
        signals.append("strong_divergence")

    # 量价背离
    if (
        sector_data.kline.close[-1] > max(sector_data.kline.close[-20:])
        and sector_data.volume[-1] < sector_data.volume_at_previous_high * 0.7
    ):
        signals.append("volume_divergence")

    # 广度极端（80%以上创新高）
    new_high_ratio = sum(1 for s in sector_data.constituents if s.close == max(s.close[-20:])) / len(sector_data.constituents)
    if new_high_ratio > 0.8:
        signals.append("breadth_extreme")

    # 换手极端
    if sector_data.turnover[-1] > percentile_90_60d:
        signals.append("turnover_extreme")

    # 返回结果
    if signals:
        return True, signals[0]  # 返回第一个触发的信号类型
    else:
        return False, None
```

**输出 Schema：**
```yaml
sector_analysis_output:
  active_mainline: list[          # 活跃主线（最多2个）
    code: str
    name: str
    state: "active_mainline"
    strength_score: float         # 综合强度分（0-100）
    indicators:
      volume_price: float
      price_momentum: float
      market_breadth: float
      relative_strength: float
      capital_flow: float

    # === 启动信号（P0-3 决策）===
    launch_signal:
      level: "strong_launch" | "weak_launch" | "sustained"
      score: float
      dimensions:                  # 5 维打分明细
        consolidation: float
        volume_expansion: float
        price_strength: float
        trend_alignment: float
        breadth: float

    # === 主线级别（P2-1 决策：系统输出依据，人工定级）===
    tier_evidence:
      has_national_policy: bool
      estimated_market_size: float    # 亿
      sub_industry_count: int
      max_leader_market_cap: float    # 亿
      consecutive_strong_days: int
    mainline_tier: "S" | "A" | "B"   # 人工最终定级
    system_suggested_tier: "S" | "A" | "B"  # 系统建议

    # === 生命周期（P2-1 决策）===
    lifecycle_phase: "launch" | "acceleration" | "mature" | "late_stage" | "mature_exhausted"
    days_since_launch: int
    lifecycle_thresholds:
      launch_end: int
      acceleration_end: int
      mature_end: int

    # === 交易建议（基于生命周期）===
    adjusted_strength_score: float    # 生命周期调整后的强度分
    recommended_action:
      launch: "aggressive_buy"
      acceleration: "buy"
      mature: "reduce_or_hold"
      late_stage: "exit_only"
      mature_exhausted: "exit"
  ]
  exhausted_mainline: list[       # 衰竭主线（最多2个）
    code: str
    name: str
    state: "exhausted_mainline"
    strength_score: float
    indicators: ...               # 同上
    days_in_exhaustion: int
    mainline_tier: "S" | "A" | "B"
  ]
  ambush: list[                   # 埋伏期（最多4个）
    code: str
    name: str
    state: "ambush"
    strength_score: float
    indicators: ...
    consolidation_days: int
    volume_expansion_ratio: float
  ]
  defensive_rotation: list[       # 防守轮动（最多1个）
    code: str
    name: str
    state: "defensive_rotation"
    strength_score: float
    indicators: ...
    correlation_with_mainline: float
  ]
  market_direction: str           # "分化" | "普涨" | "普跌"
  reasoning: str                  # LLM生成的推理文本
```

**LLM Prompt 模板（仅生成推理文本）：**
```
你是一个专业的板块分析师。根据以下板块分析结果，撰写一段简洁的分析报告。

板块分析结果：
- 活跃主线：{active_mainline}
- 衰竭主线：{exhausted_mainline}
- 埋伏期：{ambush}
- 防守轮动：{defensive_rotation}
- 市场方向：{market_direction}

请输出：
1. 一段200字以内的分析文本，解释当前板块轮动格局
2. 重点说明哪些板块是主线、持续性如何
3. 提示哪些板块处于埋伏期，可能成为下一个主线
4. 风险提示（哪些板块过热需要回避）

输出格式：纯文本（不要JSON）
```

---

### 3.3 节点 3：资金面分析

**职责：** 追踪机构资金行为，为其他节点提供辅助验证信号。

**设计理念：**
- **精简聚焦**：砍掉噪音大的主力资金流向数据，只保留高价值维度
- **机构视角**：所有维度都指向机构行为——龙虎榜机构席位、ETF申赎（机构配置）、融资融券（杠杆资金参考）
- **信号聚合**：输出统一的"机构态度"结论 + 支撑证据，方便聚合节点交叉验证
- **游资不跟踪**：游资已被量化收割，信号价值低

**数据维度（P0-2 决策：增加北向资金）：**

| 维度 | 机构信号 | 权重 | 噪音 |
|------|---------|------|------|
| 龙虎榜机构席位 | 机构专用席位净买入额 | 0.25（↓） | 低 |
| 北向资金（沪深股通） | 日度净流入，覆盖面广 | 0.25 | 低 |
| ETF申赎 | 净申购额（机构配置意图） | 0.25 | 低 |
| 融资融券 | 融资余额变化（杠杆资金方向） | 0.15（↓） | 中 |
| 大宗交易 | 折价甩货 / 溢价抢筹 | 0.10（P3-5 新增） | 中 |

**输入 Schema：**
```yaml
capital_flow_input:
  market_context:          # 来自"大盘分析"
    tech_growth:
      trend: str
  sector_context:          # 来自"板块分析"
    active_mainline: list  # 主线板块列表（用于过滤龙虎榜个股）
```

**工具调用：**
```python
# 北向资金（沪深股通）
north_flow = await DataFetcher.get_northbound_flow(lookback_days=5)

# 龙虎榜（重点关注机构席位）
dragon_tiger = await DataFetcher.get_dragon_tiger_list(lookback_days=5)
institutional_trades = filter_institutional_seats(dragon_tiger)

# ETF申赎
etf_flow = await DataFetcher.get_etf_shares_change(lookback_days=5)

# 融资融券
margin_data = await DataFetcher.get_margin_data(lookback_days=5)

# 大宗交易（P3-5 新增，缺失时 block_trade=None）
block_trade = await DataFetcher.get_block_trade(lookback_days=5)
```

**机构态度评分（P0-2 + P2-4 + P3-5 决策：五维度 + 降级机制）：**
```python
def calculate_institutional_score(north_flow, dragon_tiger, etf_flow, margin_data, block_trade=None):
    """综合机构态度评分（-1 ~ 1），支持降级模式"""

    evidence = {}
    available_scores = []
    available_weights = []
    missing_dimensions = []

    # 维度 1：北向资金（权重 0.25）
    try:
        cumulative_5d = sum(d.net_buy for d in north_flow[-5:])
        base_score = clip(cumulative_5d / 5e9, -1, 1)
        today = north_flow[-1].net_buy
        avg_5d = cumulative_5d / 5
        acceleration = clip((today - avg_5d) / 2e9, -0.3, 0.3)
        concentration_bonus = 0.1 if today > 1e10 else 0
        north_score = clip(base_score + acceleration + concentration_bonus, -1, 1)

        evidence["northbound"] = {
            "score": north_score,
            "cumulative_5d": cumulative_5d / 1e8,
            "today": today / 1e8,
            "acceleration": "加速流入" if acceleration > 0.1 else ("减速" if acceleration < -0.1 else "稳定"),
        }
        available_scores.append(north_score)
        available_weights.append(0.25)
    except DataFetchError as e:
        missing_dimensions.append("北向资金")
        logger.warning(f"北向资金数据获取失败: {e}")

    # 维度 2：龙虎榜机构净买入（权重 0.25）
    try:
        dt_score = clip(dragon_tiger.institutional_net_buy / 5e8, -1, 1)
        evidence["dragon_tiger"] = {"score": dt_score, "net_buy": dragon_tiger.institutional_net_buy / 1e8}
        available_scores.append(dt_score)
        available_weights.append(0.25)
    except DataFetchError as e:
        missing_dimensions.append("龙虎榜")
        logger.warning(f"龙虎榜数据获取失败: {e}")

    # 维度 3：ETF净申购（权重 0.25）
    try:
        etf_score = clip(etf_flow.net_subscription / 1e9, -1, 1)
        evidence["etf_flow"] = {"score": etf_score, "net_subscription": etf_flow.net_subscription / 1e8}
        available_scores.append(etf_score)
        available_weights.append(0.25)
    except DataFetchError as e:
        missing_dimensions.append("ETF 申赎")
        logger.warning(f"ETF 申赎数据获取失败: {e}")

    # 维度 4：融资余额变化（权重 0.15）
    try:
        margin_score = clip(margin_data.balance_change / 2e9, -0.5, 0.5)
        evidence["margin_trading"] = {"score": margin_score, "balance_change": margin_data.balance_change / 1e8}
        available_scores.append(margin_score)
        available_weights.append(0.15)
    except DataFetchError as e:
        missing_dimensions.append("融资融券")
        logger.warning(f"融资融券数据获取失败: {e}")

    # 维度 5：大宗交易（权重 0.10，P3-5 新增）
    # 折价 > 5% → "甩货"信号（看空）；溢价 → "抢筹"信号（看多）
    # 缺失时该维度权重归零，其余自动归一化（复用 P2-4 降级机制）
    if block_trade is not None:
        try:
            premium_ratio = block_trade.premium_to_close  # 正=溢价，负=折价
            if premium_ratio < -0.05:
                # 大幅折价 → 甩货信号
                block_score = clip(premium_ratio * 10, -1, -0.3)
                signal_type = "甩货（折价 > 5%）"
            elif premium_ratio > 0.02:
                # 溢价 → 抢筹信号
                block_score = clip(premium_ratio * 8, 0.3, 1)
                signal_type = "抢筹（溢价）"
            else:
                block_score = clip(premium_ratio * 5, -0.3, 0.3)
                signal_type = "平价成交"
            evidence["block_trade"] = {
                "score": block_score,
                "premium_ratio": premium_ratio,
                "signal_type": signal_type,
                "amount": block_trade.amount / 1e8,
            }
            available_scores.append(block_score)
            available_weights.append(0.10)
        except DataFetchError as e:
            missing_dimensions.append("大宗交易")
            logger.warning(f"大宗交易数据获取失败: {e}")
    else:
        missing_dimensions.append("大宗交易")

    # === 降级处理（P2-4 决策）===
    if len(available_scores) < 2:
        return {
            "status": "failed",
            "reason": f"可用数据不足（仅 {len(available_scores)} 个维度）",
            "missing_dimensions": missing_dimensions,
        }

    # 权重归一化
    total_weight = sum(available_weights)
    normalized_weights = [w / total_weight for w in available_weights]
    institutional_score = sum(s * w for s, w in zip(available_scores, normalized_weights))

    # 置信度乘数（可用维度比例，总分母为 5：北向/龙虎榜/ETF/融资/大宗）
    confidence_multiplier = len(available_scores) / 5
    degraded = len(missing_dimensions) > 0

    # 态度判断
    if institutional_score > 0.3:
        attitude = "bullish"
    elif institutional_score < -0.3:
        attitude = "bearish"
    else:
        attitude = "neutral"

    return {
        "status": "degraded" if degraded else "normal",
        "institutional_score": institutional_score,
        "institutional_attitude": attitude,
        "evidence": evidence,
        "missing_dimensions": missing_dimensions,
        "confidence_multiplier": confidence_multiplier,
        "degraded": degraded,
    }
```

**输出 Schema：**
```yaml
capital_flow_output:
  status: "normal" | "degraded" | "failed"  # P2-4 降级状态
  institutional_attitude: str        # "bullish" | "bearish" | "neutral"
  institutional_score: float         # 综合评分（-1.0 ~ 1.0）
  confidence_multiplier: float       # 置信度乘数（0-1，= 可用维度/5）
  missing_dimensions: list[str]      # 缺失的数据维度
  evidence:
    northbound:                      # P0-2 新增
      score: float
      cumulative_5d: float           # 5 日累计净流入（亿）
      today: float                   # 今日净流入（亿）
      acceleration: str              # "加速流入" | "稳定" | "减速"
    dragon_tiger:
      score: float
      institutional_net_buy: float   # 机构席位净买入（亿）
      mainline_stocks_bought: list[str]
    etf_flow:
      score: float
      net_subscription: float        # ETF净申购额（亿份）
      top_etf_changes: list[
        etf_code: str
        etf_name: str
        shares_change: float
        direction: str               # "申购" | "赎回"
      ]
    margin_trading:
      score: float
      balance_change: float          # 融资余额日变化（亿）
    block_trade:                     # P3-5 新增（缺失时可能不存在）
      score: float
      premium_ratio: float           # 溢价率（正=溢价，负=折价）
      signal_type: str               # "甩货（折价 > 5%）" | "抢筹（溢价）" | "平价成交"
      amount: float                  # 成交额（亿）
  reasoning: str                     # LLM生成的推理文本
```

**LLM Prompt 模板（仅生成推理文本）：**
```
你是一个专业的资金流分析师。根据以下机构资金数据，撰写一段简洁的分析报告。

机构态度评分：{institutional_score}（{institutional_attitude}）

支撑证据：
- 龙虎榜：机构席位净买入 {dragon_tiger.institutional_net_buy} 亿
  - 主线板块被机构买入的个股：{dragon_tiger.mainline_stocks_bought}
- ETF申赎：净申购 {etf_flow.net_subscription} 亿份
  - 头部ETF变化：{etf_flow.top_etf_changes}
- 融资融券：融资余额变化 {margin_trading.balance_change} 亿
- 大宗交易：{block_trade.signal_type}（溢价率 {block_trade.premium_ratio}%，成交额 {block_trade.amount} 亿）

大盘背景：{market_context}
板块背景：{sector_context}

请输出：
1. 一段150字以内的分析文本，解释机构资金的整体态度
2. 重点说明机构对主线板块的态度（是否在加仓）
3. 提示风险信号（如有）

输出格式：纯文本（不要JSON）
```

---

### 3.4 节点 4：消息面分析

**职责：** 采集和分析影响市场的政策、行业、国际新闻，评估整体影响，发现新概念建议。

**设计理念：**
- **LLM是核心分析引擎**：新闻是非结构化数据，必须由LLM提取事件、判断影响、识别情绪
- **聚焦4类来源**：政策、行业、国际市场、新概念发现（公司公告砍掉，留给选股节点）
- **信号聚合输出**：整体影响评分 + 事件列表 + 新概念建议
- **新概念反馈管道**：消息面 → 板块节点精选概念列表（人工审核后加入）

**数据来源：**

| 来源 | 关注点 | 示例 |
|------|--------|------|
| 政策 | 国务院/工信部/科技部产业政策、财政货币政策 | "人形机器人产量2026年突破10万台" |
| 行业 | AI/机器人/半导体产业链动态 | "特斯拉Optimus Gen-3启动量产" |
| 国际市场 | 美股三大指数 + 费城半导体 + 纳斯达克、地缘冲突 | "英伟达财报超预期，费半指数涨3%" |
| 公司公告 | ❌ 不采集 | — |

**⚠️ 已知问题（2026-07-27）：**

当前 Node 4 只依赖 `SearchService.search_market_news()`（Web 搜索引擎），存在两个缺陷：
1. **搜索词太泛**：全是科技/AI 方向，没有覆盖 IPO、宏观经济、具体公司事件等
2. **无热门信息流**：所有新闻获取都是"搜索型"（需传入 query），没有"当日热门消息"信息流

**后果**：2026-07-27 Phase 1 运行中，"长鑫上市"这种当日最大消息完全没有被 Node 4 捕获，`major_events=[]`。

---

**方案二：接入热门信息流（理想方案）**

**目标**：先获取当日热门消息（信息流），再做 LLM 事件提取。不再依赖搜索词覆盖度。

**新增数据源（3 层）：**

| 层级 | 数据源 | 接口 | 优先级 | 说明 |
|------|--------|------|--------|------|
| **L1 实时快讯** | 财联社电报 | `https://www.cls.cn/nodeapi/updateTelegraph` | P0 | 实时快讯流（分钟级），覆盖所有重大事件 |
| **L2 新闻聚合** | akshare 新闻 API | `stock_news_em()` / `js_news()` / `news_economic_baidu()` | P0 | 东财个股新闻 + 金十宏观 + 百度财经 |
| **L3 搜索补充** | SearchService | `search_market_news()` | P1 | 保留现有搜索，作为补充和深度采集 |

**改造后的 Node 4 流程：**

```
Step 1: 热门信息流采集（并发）
├── L1: 财联社电报（实时快讯，最近 50 条）
├── L2: akshare 新闻 API（3 个接口并发）
│   ├── stock_news_em() — 东财热门股新闻
│   ├── js_news() — 金十数据（宏观经济）
│   └── news_economic_baidu() — 百度财经
└── L3: 搜索补充（保留现有 6 个 query）

Step 2: 去重 + 合并
- 基于 title 相似度去重（阈值 0.45）
- 多源确认（同一事件被财联社 + 东财 + 搜索同时报道 → source_count=3）

Step 3: LLM 事件提取（不变）
Step 4: 评分计算（不变）
Step 5: reasoning 生成（不变）
```

**实现步骤：**

1. **新建 `src/got/news_feeds.py`**（~200 行）
   - `fetch_cls_telegraph(limit=50)` — 财联社电报实时快讯
   - `fetch_akshare_news()` — akshare 3 个新闻接口并发采集
   - `merge_news(cls_news, ak_news, search_news)` — 多源合并 + 去重

2. **修改 `src/got/nodes/news_analysis.py`**
   - `_fetch_news()` 新增 L1/L2 采集，与 L3 搜索并发
   - 保留现有 `SEARCH_QUERIES` 作为 L3 补充

3. **新建 `src/patches/cls_patch.py`**（可选）
   - 财联社反爬补丁（User-Agent + 请求间隔）

4. **更新 `src/got/schemas.py`**
   - `NewsEvent` 新增 `source_type: str` 字段（`"cls"` / `"eastmoney"` / `"jin10"` / `"search"`）
   - `DataQuality` 的 `available_fields` 记录哪些数据源成功

**文件改动清单：**

| 文件 | 类型 | 改动 |
|------|------|------|
| `src/got/news_feeds.py` | 新建 | 热门信息流采集模块（~200 行） |
| `src/got/nodes/news_analysis.py` | 修改 | `_fetch_news()` 集成 L1/L2 采集 |
| `src/got/schemas.py` | 修改 | `NewsEvent` 新增 `source_type` 字段 |
| `src/patches/cls_patch.py` | 新建（可选） | 财联社反爬补丁 |

**验证方案：**

```powershell
# 1. 跑 Phase 1
python main.py --got

# 2. 检查 news.json 中 major_events 非空
python -c "import json; d=json.load(open('data/got_daily/YYYYMMDD/news.json')); print(f'events: {len(d[\"major_events\"])}'); [print(f'  - {e[\"title\"]}') for e in d['major_events'][:5]]"

# 3. 检查 source_type 字段
python -c "import json; d=json.load(open('data/got_daily/YYYYMMDD/news.json')); sources=set(e.get('source_type','?') for e in d['major_events']); print(f'sources: {sources}')"

# 4. 检查 reasoning.md 是否包含具体事件（如"长鑫上市"）
grep -i "长鑫\|上市\|IPO" data/got_daily/YYYYMMDD/reasoning.md
```

**已知风险：**

- 财联社电报可能有反爬限制（需测试可用性）
- akshare 新闻 API 可能不稳定（需降级处理）
- 多源合并后数据量可能过大（限制 L1=50 条 + L2=30 条 + L3=20 条）

---

**输入 Schema：**
```yaml
news_analysis_input:
  concept_list: list[str]  # 当前板块精选概念列表（用于匹配新闻中的概念关键词）
  lookback_hours: int      # 回溯小时数（默认 24）
```

**工具调用：**
```python
# 采集4类新闻
policy_news = await NewsService.get_policy_news(lookback_hours)
industry_news = await NewsService.get_industry_news(lookback_hours)
global_news = await NewsService.get_global_market_news(lookback_hours)  # 国际市场

# 新概念发现：从政策和行业新闻中提取
all_news = policy_news + industry_news
```

**LLM 核心分析（P1-4 决策：拆 3 个并发调用，独立 prompt 风格差异化）：**
```python
async def analyze_news_with_llm(all_news, concept_list):
    """三个任务拆分为三个并发 LLM 调用（P1-4 决策）"""

    # 任务 1：事件提取（严谨、结构化）
    events_task = asyncio.create_task(
        llm.call(EVENT_EXTRACTION_PROMPT.format(
            news_data=all_news,
            concept_list=concept_list
        ))
    )

    # 任务 2：新概念发现（发散、创造性）
    concepts_task = asyncio.create_task(
        llm.call(CONCEPT_DISCOVERY_PROMPT.format(
            news_data=all_news,
            current_concepts=concept_list
        ))
    )

    # 任务 3：风险总结（审慎、权衡）
    risks_task = asyncio.create_task(
        llm.call(RISK_SUMMARY_PROMPT.format(
            news_data=all_news
        ))
    )

    # 并发执行，独立失败不影响其他
    results = await asyncio.gather(events_task, concepts_task, risks_task, return_exceptions=True)

    # 降级处理
    major_events = results[0] if not isinstance(results[0], Exception) else []
    concept_suggestions = results[1] if not isinstance(results[1], Exception) else []
    key_risks = results[2] if not isinstance(results[2], Exception) else ["新闻分析部分失败"]

    # 多源确认（P1-5 决策）：为每个事件统计独立来源数
    for event in major_events:
        event["source_count"] = count_independent_sources(event)

    return major_events, concept_suggestions, key_risks


# === 三个独立 Prompt ===

EVENT_EXTRACTION_PROMPT = """
你是严谨的新闻编辑。从以下新闻中提取影响A股的重要事件。

当前板块精选概念列表：{concept_list}

要求：
- 每个事件必须包含：title、summary（2-3句）、event_type（policy/industry/global）、
  impact_level（high/medium/low）、sentiment（positive/negative/neutral）、affected_sectors
- 不要遗漏重要事件，也不要重复
- 同一事件被多家媒体报道时，合并为一个事件并记录 source_count
- 输出 JSON 格式

新闻数据：
{news_data}
"""

CONCEPT_DISCOVERY_PROMPT = """
你是敏锐的市场观察者。从以下新闻中识别近期频繁提及但不在当前概念列表中的新主题。

当前概念列表：{current_concepts}

要求：
- 只输出真正的新概念（近 30 天内首次出现或突然升温）
- 不要输出已有概念的变体（如"AI芯片"不算新概念，如果"AI"已在列表中）
- 对每个概念说明：concept_name、evidence（引用具体新闻）、mention_frequency
- 输出 JSON 格式

新闻数据：
{news_data}
"""

RISK_SUMMARY_PROMPT = """
你是审慎的风险管理师。根据以下新闻，总结当前市场的 2-3 个关键风险点。

要求：
- 每个风险点说明：description、probability（high/medium/low）、impact（high/medium/low）
- 优先输出概率高且影响大的风险
- 不要输出"投资需谨慎"这类套话
- 输出 JSON 格式

新闻数据：
{news_data}
"""
```

**综合影响评分（规则计算，v0.3 采用阈值过滤+显著度加权，参考 MANA-Net 反均质化思路）：**

> **设计理念（参考 [MANA-Net, Wang et al. 2024](https://arxiv.org/abs/2409.05698)）：**
> 传统等权聚合会导致"情绪均质化"——大量 low-impact 中性事件稀释少数 high-impact 信号的强度。
> 本方案采用**阈值过滤 + 显著度加权**：low-impact 事件直接不参与评分（仅保留在事件列表中），
> medium/high 事件按 impact_level 加权，分母为加权事件数而非原始事件数。

```python
def calculate_impact_score(major_events):
    """基于LLM提取的事件列表，阈值过滤 + 显著度加权计算综合影响评分

    策略（v0.3）：
    - low-impact 事件：直接丢弃（不影响分数，仅保留在事件列表供展示）
    - medium/high-impact 事件：按 impact_level 加权（high=1.0, medium=0.5）
    - 归一化分母为加权事件数（sum of weights），而非原始事件数
    """
    # 显著度权重映射（low 不参与评分）
    IMPACT_WEIGHTS = {"high": 1.0, "medium": 0.5}
    SENTIMENT_VALUES = {"positive": 1, "negative": -1, "neutral": 0}

    # 过滤 low-impact，只对 medium/high 事件计算
    significant_events = [e for e in major_events if e.impact_level in IMPACT_WEIGHTS]

    if not significant_events:
        return 0.0, "neutral"

    weighted_score_sum = 0.0
    weight_sum = 0.0

    for event in significant_events:
        weight = IMPACT_WEIGHTS[event.impact_level]
        sentiment = SENTIMENT_VALUES[event.sentiment]
        weighted_score_sum += weight * sentiment
        weight_sum += weight

    # 归一化到 -1 ~ 1（分母为加权事件数，low-impact 不参与稀释）
    normalized_score = clip(weighted_score_sum / weight_sum, -1, 1)

    # 态度判断
    if normalized_score > 0.3:
        impact = "positive"
    elif normalized_score < -0.3:
        impact = "negative"
    else:
        impact = "neutral"

    return normalized_score, impact
```

**输出 Schema：**
```yaml
news_analysis_output:
  overall_impact: str            # "positive" | "negative" | "neutral"
  impact_score: float            # 综合影响评分（-1.0 ~ 1.0）
  major_events: list[            # 重要事件列表（按影响级别排序）
    event_type: str              # "policy" | "industry" | "global"
    title: str
    summary: str
    impact_level: str            # "high" | "medium" | "low"
    sentiment: str               # "positive" | "negative" | "neutral"
    affected_sectors: list[str]  # 受影响板块（映射到概念列表）
    timestamp: str
    source_count: int            # P1-5 新增：独立报道来源数（用于多源确认）
  ]
  concept_suggestions: list[     # 新概念建议（人工审核后加入板块精选列表）
    concept_name: str
    evidence: str
    mention_frequency: int
  ]
  key_risks: list[str]           # 关键风险点
  reasoning: str                 # LLM生成的推理文本
```

**LLM Prompt 模板（推理文本 + 完整分析）：**
```
消息面分析结果：
- 整体影响：{overall_impact}（评分：{impact_score}）
- 重要事件：{major_events}
- 新概念建议：{concept_suggestions}
- 关键风险：{key_risks}

请输出：
1. 一段200字以内的分析文本，解释当前消息面的整体格局
2. 重点说明哪些事件对市场影响最大
3. 提示新概念机会和关键风险

输出格式：纯文本（不要JSON）
```

---

### 3.5 节点 5：情绪分析

**职责：** 采集论坛舆情，量化散户情绪，输出情绪评分和正面热门股。

**设计理念：**
- **聚焦散户情绪**：资金面追踪机构行为，情绪面追踪散户情绪，两者在聚合节点形成对照
- **纯原始数据 + LLM分析**：不依赖其他节点输出，独立采集分析
- **信号聚合输出**：情绪评分 + 正面热门股 + 热门话题
- **排除黑天鹅**：热门股只输出正面讨论 + 有行情配合的，排除暴雷/暴跌讨论

**数据源（P3-3 决策：NGA 板块关闭，切换为雪球）：** 雪球热帖（API 相对友好，覆盖价值高）

**输入 Schema：**
```yaml
sentiment_analysis_input:
  lookback_hours: int  # 回溯小时数（默认 24）
```

**工具调用：**
```python
# 采集论坛舆情数据
forum_data = await ForumSentimentService.get_sentiment(lookback_hours)
# 返回：帖子列表、评论列表、点赞/踩数据等
```

**LLM 核心分析（情绪量化 + 热门股提取）：**
```python
def analyze_sentiment_with_llm(forum_data):
    """LLM 分析论坛数据"""

    prompt = f"""
你是一个专业的A股市场情绪分析师。根据以下论坛数据，完成三项任务。

论坛数据（近24小时）：
{forum_data}

请完成以下任务：

任务1：量化整体情绪（P0-1 决策：输出 count 而非 ratio）
请统计（数量，不是比例）：
- positive_count: 正面帖子数量
- negative_count: 负面帖子数量
- neutral_count: 中性帖子数量
- very_positive_count: 极度乐观帖子数（如"冲""梭哈""满仓干"）
- very_negative_count: 极度悲观帖子数（如"崩了""割肉""牛市结束了"）
- forum_mood: "极度乐观" | "乐观" | "中性" | "悲观" | "极度悲观"

任务2：提取正面热门股
从论坛中提取被正面讨论的热门股票，排除黑天鹅（暴雷/暴跌/负面事件）：
对每只股票输出：
- stock_code: 股票代码
- stock_name: 股票名称
- mention_count: 提及次数
- sentiment: "positive"（只输出正面讨论的）
- associated_sectors: 关联板块（映射到概念列表）

任务3：提取热门话题
列出论坛中2-3个热门话题关键词

输出格式：JSON
"""

    llm_output = await llm.call(prompt)
    return llm_output
```

**情绪评分计算（P0-1 修复 Bug + P2-2 反身性逻辑）：**
```python
def calculate_sentiment_score(llm_output):
    """情绪评分 + 反身性阶段分类"""

    # P0-1 修复：基于 count 计算，而非直接使用 LLM 的 ratio
    positive_count = llm_output.positive_count
    negative_count = llm_output.negative_count
    neutral_count = llm_output.neutral_count
    total = positive_count + negative_count + neutral_count

    positive_ratio = positive_count / max(total, 1)
    negative_ratio = negative_count / max(total, 1)

    # 基础情绪分
    raw_score = positive_ratio - negative_ratio  # -1 ~ 1

    # 极端情绪统计
    very_positive_count = llm_output.very_positive_count
    very_negative_count = llm_output.very_negative_count
    extreme_positive_ratio = very_positive_count / max(total, 1)
    extreme_negative_ratio = very_negative_count / max(total, 1)

    # === P2-2 反身性：情绪阶段分类 ===
    # 阈值说明：0.5/0.2 为初始值，运行 3 个月后需根据历史数据回测调整
    # 避免边界跳变：极端情绪占比 < 阈值时不触发，而非硬切

    # 极度乐观 → 反向卖出信号（散户狂热 = 顶部信号）
    if raw_score > 0.5 and extreme_positive_ratio > 0.25:
        sentiment_stage = "euphoria"
        trading_signal = "contrarian_sell"
        signal_confidence = 0.7

    # 温和乐观 → 趋势跟随（含边界平滑：接近 euphoria 阈值时 confidence 递减）
    elif raw_score > 0.2:
        sentiment_stage = "optimism"
        trading_signal = "trend_following"
        signal_confidence = 0.5

    # 中性
    elif abs(raw_score) <= 0.2:
        sentiment_stage = "neutral"
        trading_signal = "neutral"
        signal_confidence = 0.3

    # 温和悲观 → 趋势跟随
    elif raw_score < -0.2:
        sentiment_stage = "pessimism"
        trading_signal = "trend_following"
        signal_confidence = 0.5

    # 极度悲观 → 反向买入信号（散户恐慌出清 = 底部信号）
    else:
        sentiment_stage = "capitulation"
        trading_signal = "contrarian_buy"
        signal_confidence = 0.7

    # 态度判断
    if raw_score > 0.3:
        overall_sentiment = "bullish"
    elif raw_score < -0.3:
        overall_sentiment = "bearish"
    else:
        overall_sentiment = "neutral"

    return {
        "raw_score": raw_score,
        "overall_sentiment": overall_sentiment,
        "sentiment_stage": sentiment_stage,
        "trading_signal": trading_signal,
        "signal_confidence": signal_confidence,
        "extreme_positive_ratio": extreme_positive_ratio,
        "extreme_negative_ratio": extreme_negative_ratio,
    }
```

**输出 Schema：**
```yaml
sentiment_analysis_output:
  status: "normal" | "degraded" | "failed"  # P2-4 降级状态
  overall_sentiment: str         # "bullish" | "bearish" | "neutral"
  sentiment_score: float         # 情绪评分（-1.0 ~ 1.0）

  # === P2-2 反身性（核心新增）===
  sentiment_stage: str           # "euphoria" | "optimism" | "neutral" | "pessimism" | "capitulation"
  trading_signal: str            # "contrarian_sell" | "trend_following" | "neutral" | "contrarian_buy"
  signal_confidence: float       # 信号置信度（0-1）

  # 极端情绪占比
  extreme_positive_ratio: float  # 极度乐观帖子占比
  extreme_negative_ratio: float  # 极度悲观帖子占比

  forum_mood: str                # "极度乐观" | "乐观" | "中性" | "悲观" | "极度悲观"
  hot_stocks: list[              # 正面热门股（排除黑天鹅）
    stock_code: str
    stock_name: str
    mention_count: int
    sentiment: str               # "positive"
    associated_sectors: list[str]
  ]
  hot_topics: list[str]          # 论坛热门话题
  missing_data_sources: list[str]  # P2-4：缺失的数据源
  reasoning: str                 # LLM生成的推理文本
```

**LLM Prompt 模板（推理文本）：**
```
情绪分析结果：
- 整体情绪：{overall_sentiment}（评分：{sentiment_score}）
- 论坛情绪：{forum_mood}
- 极端情绪占比：{extreme_emotion_ratio}
- 正面热门股：{hot_stocks}
- 热门话题：{hot_topics}

请输出：
1. 一段150字以内的情绪分析文本
2. 重点说明散户情绪状态（是否过热/过冷）
3. 提示热门股和热门话题

输出格式：纯文本（不要JSON）
```

---

### 3.6 节点 6：聚合节点（v2 — 置信度加权聚合 + 条件迭代精炼）

**职责：** 交叉验证多维度分析结果，生成统一市场上下文（unified_context）供Phase 2使用。

**设计理念（基于 15+ 篇论文）：**
- **投票优于辩论**（Debate-or-Vote, NeurIPS 2025）：置信度加权投票覆盖多轮辩论大部分收益
- **质量评估优于计数**（AoR, LREC 2024）：正确结论可能在少数，需评估推理质量
- **概率聚合在 LLM 外部完成**（ThinkSum, ACL 2023）：规则推断比 LLM 推断更稳定
- **反 Sycophancy**（ConsensAgent, ACL 2025）：LLM 精炼阶段引入批判者角色

**v2 三阶段流水线：**
```
Phase A: 信号标准化 + 置信度加权聚合（纯规则，零 LLM）
  5 个 ctx → 统一信号空间投影 → 置信度加权 → 收敛剖面分类
       ↓
Phase B: 异常检测（规则 + 自适应阈值）
  自动矛盾检测（替代硬编码 10 对）+ 矛盾分类（factual/temporal/opinion）
  EMA 时间一致性（替代固定 ±0.4 阈值）
  消息面覆盖（简化：关键词 + 多源确认 + LLM 判断）
       ↓
Phase C: 条件 LLM 精炼（最多 2 次调用）
  触发条件：agreement < 0.6 OR high 异常 OR 消息面覆盖
  ├─ C1 生成：聚合结论 + 分歧模式 → 初始研判
  └─ C2 批判：初始研判 → 找盲点 → 最终调整
  不触发时：直接规则输出，省掉 LLM 调用
       ↓
  unified_context
```

**输入 Schema：**
```yaml
aggregator_input:
  market_context:          # 来自"大盘分析"
    tech_growth:
      trend: str
      score: float
      indicators: dict
    mid_small_cap:
      trend: str
      score: float
    large_cap:
      trend: str
      score: float
    position_limit: float
    confidence: float
    key_levels: dict
  sector_context:          # 来自"板块分析"
    active_mainline: list
    exhausted_mainline: list
    ambush: list
    defensive_rotation: list
    market_direction: str
  capital_context:         # 来自"资金面分析"
    institutional_attitude: str
    institutional_score: float
    evidence: dict
  news_context:            # 来自"消息面分析"
    overall_impact: str
    impact_score: float
    major_events: list
    concept_suggestions: list
    key_risks: list
  sentiment_context:       # 来自"情绪分析"
    overall_sentiment: str
    sentiment_score: float
    extreme_emotion_ratio: float
    forum_mood: str
    hot_stocks: list
    hot_topics: list
```

**Step 1：规则检测矛盾（P1-2：按时间框架分组 + 扩展到 8 对 + P2-4：降级维度跳过）：**
```python
# === 路径级失败检测（任一主路径失败则直接报错，不做降级）===
def validate_path_inputs(market, sector, capital, news, sentiment):
    """两路并行 + 聚合架构中，任一路径失败直接报错退出"""
    errors = []

    # 技术分析路：大盘 → 板块 → 资金面（串行依赖）
    tech_path_failed = False
    if market is None or getattr(market, "error", None):
        errors.append(f"大盘分析节点失败: {getattr(market, 'error', '输入为空')}")
        tech_path_failed = True
    if sector is None or getattr(sector, "error", None):
        errors.append(f"板块分析节点失败: {getattr(sector, 'error', '输入为空')}")
        tech_path_failed = True
    if capital is None or getattr(capital, "status", "") == "failed":
        errors.append(f"资金面分析节点失败: {getattr(capital, 'error', '状态为 failed')}")
        tech_path_failed = True

    # 独立数据路：消息面 + 情绪面（并行，不依赖技术路）
    data_path_failed = False
    if news is None or getattr(news, "error", None):
        errors.append(f"消息面分析节点失败: {getattr(news, 'error', '输入为空')}")
        data_path_failed = True
    if sentiment is None or getattr(sentiment, "error", None):
        errors.append(f"情绪面分析节点失败: {getattr(sentiment, 'error', '输入为空')}")
        data_path_failed = True

    # 任一路径失败 → 直接报错，不做降级
    if tech_path_failed or data_path_failed:
        raise AggregatorInputError(
            f"聚合节点输入校验失败（不做降级）:\n" + "\n".join(errors)
        )


def detect_contradictions(market, sector, capital, news, sentiment):
    """规则检测维度间矛盾（多时间框架 + 降级感知）"""
    contradictions = []

    # === P2-4：降级维度不参与矛盾检测 ===
    capital_available = capital.status != "failed"
    news_available = news.status != "failed" if hasattr(news, "status") else True
    sentiment_available = sentiment.status != "failed" if hasattr(sentiment, "status") else True

    # === 短期矛盾（5 日技术 vs 24 小时消息/情绪）===
    if capital_available:
        # ① 短期技术面 vs 消息面
        if market.tech_growth.signals.short_term.trend == "bearish" and news.impact_score > 0.3:
            contradictions.append({
                "pair": "short_tech_vs_news",
                "timeframe": "short_term",
                "description": "短期技术面偏空但消息面利好",
                "severity": "high",
            })

        # ② 短期技术面 vs 资金面（机构）
        if market.tech_growth.signals.short_term.trend == "bearish" and capital.institutional_score > 0.3:
            contradictions.append({
                "pair": "short_tech_vs_capital",
                "timeframe": "short_term",
                "description": "短期技术面偏空但机构在加仓",
                "severity": "medium",
            })

    if sentiment_available:
        # ③ 短期技术面 vs 情绪面（散户）
        if market.tech_growth.signals.short_term.trend == "bullish" and sentiment.sentiment_score < -0.3:
            contradictions.append({
                "pair": "short_tech_vs_sentiment",
                "timeframe": "short_term",
                "description": "技术面偏多但散户恐慌",
                "severity": "low",
            })

    # === 中期矛盾（20 日技术 vs 5 日资金 / 24 小时消息）===
    if capital_available:
        # ④ 中期技术面 vs 资金面
        if market.tech_growth.signals.medium_term.trend == "bearish" and capital.institutional_score > 0.3:
            contradictions.append({
                "pair": "medium_tech_vs_capital",
                "timeframe": "medium_term",
                "description": "中期技术面偏空但机构在加仓",
                "severity": "high",
            })

    if news_available:
        # ④b 中期技术面 vs 消息面
        if market.tech_growth.signals.medium_term.trend == "bearish" and news.impact_score > 0.3:
            contradictions.append({
                "pair": "medium_tech_vs_news",
                "timeframe": "medium_term",
                "description": "中期技术面偏空但消息面持续利好（可能是底部反转前兆）",
                "severity": "medium",
            })

    # === 长期矛盾（60 日技术 vs 资金面）===
    if capital_available:
        # ④c 长期技术面 vs 资金面
        if market.tech_growth.signals.long_term.trend == "bearish" and capital.institutional_score > 0.3:
            contradictions.append({
                "pair": "long_tech_vs_capital",
                "timeframe": "long_term",
                "description": "长期趋势偏空但机构仍在加仓（可能是左侧布局或机构误判）",
                "severity": "high",
            })

    # === 跨维度矛盾 ===
    if capital_available and sentiment_available:
        # ⑤ 机构 vs 散户（最有价值的矛盾）
        if capital.institutional_attitude == "bullish" and sentiment.overall_sentiment == "bearish":
            contradictions.append({
                "pair": "institutional_vs_retail",
                "timeframe": "cross",
                "description": "机构看多但散户恐慌（可能是底部信号）",
                "severity": "high",
            })
        if capital.institutional_attitude == "bearish" and sentiment.overall_sentiment == "bullish":
            contradictions.append({
                "pair": "institutional_vs_retail",
                "timeframe": "cross",
                "description": "机构在卖但散户在追（可能是顶部信号）",
                "severity": "high",
            })

        # ⑥ 资金 vs 情绪（新增，P1-2 扩展）
        if capital.institutional_score > 0.3 and sentiment.sentiment_stage == "euphoria":
            contradictions.append({
                "pair": "capital_vs_euphoria",
                "timeframe": "cross",
                "description": "机构买入但散户极度狂热（高位接盘风险）",
                "severity": "high",
            })

    if news_available:
        # ⑦ 消息 vs 板块（新增）
        if news.impact_score > 0.3 and len(sector.active_mainline) == 0:
            contradictions.append({
                "pair": "news_vs_sector",
                "timeframe": "cross",
                "description": "利好政策但无活跃主线板块（利好出尽？）",
                "severity": "medium",
            })

    # === 跨时间框架背离（作为独立信号，不是矛盾）===
    if (market.tech_growth.signals.long_term.trend == "bullish" and
        market.tech_growth.signals.short_term.trend == "bearish"):
        contradictions.append({
            "pair": "long_short_divergence",
            "timeframe": "cross_timeframe",
            "description": "长期多头但短期回调（可能是回调买点）",
            "severity": "low",
            "signal_type": "pullback_opportunity",
        })

    return contradictions
```

**Step 2：LLM 批处理分析所有矛盾（P2-3 决策：一次调用代替逐个串行）：**
```python
async def analyze_contradictions_batch(contradictions, all_contexts):
    """所有矛盾合并为一个 prompt，一次 LLM 调用"""

    if not contradictions:
        return []

    contradictions_text = "\n\n".join([
        f"矛盾 {i+1}（{c['pair']}，严重度: {c['severity']}，时间框架: {c['timeframe']}）:\n"
        f"  描述: {c['description']}"
        for i, c in enumerate(contradictions)
    ])

    prompt = f"""
你是资深投资顾问。检测到以下 {len(contradictions)} 个维度间矛盾，请逐一分析并寻找可能的共同根因。

完整市场数据：
{all_contexts}

矛盾列表：
{contradictions_text}

请输出：
1. 对每个矛盾的分析（resolution / confidence_adjustment / reasoning）
2. 这些矛盾是否有共同根因？如果有，说明
3. 综合所有矛盾后，对整体市场判断的影响

输出 JSON：
{{
  "analyses": [
    {{"pair": "...", "resolution": "...", "confidence_adjustment": 0.0, "reasoning": "..."}},
    ...
  ],
  "common_root_cause": "..." | null,
  "overall_impact": "..."
}}
"""

    result = await llm.call(prompt)
    return result.analyses
```

**Step 3：消息面覆盖检查（P1-5 决策：三层防护）：**
```python
async def check_news_override(contradictions, news_context, market_context):
    """判断是否有重大事件需要覆盖技术面判断（三层防护）"""

    # === 第一层：系统性政策筛选 ===
    systemic_keywords = [
        "降息", "降准", "加息", "IPO", "注册制", "退市",
        "禁止", "禁令", "监管", "制裁", "关税", "财政刺激", "国债",
    ]
    overridable_events = []
    for event in news_context.major_events:
        is_systemic = (
            event.event_type == "policy" and
            event.impact_level == "high" and
            any(kw in event.title or kw in event.summary for kw in systemic_keywords)
        )
        # 多源确认（P1-5 决策：≥ 3 家独立媒体）
        has_multi_source = event.source_count >= 3
        if is_systemic and has_multi_source:
            overridable_events.append(event)

    if not overridable_events:
        return None

    # === 第二层：历史验证 ===
    historical_db = {
        "降息": {"accuracy": 0.85},
        "行业禁令": {"accuracy": 0.75},
        "IPO政策": {"accuracy": 0.65},
        "产业利好": {"accuracy": 0.30},  # 低准确率，不应覆盖
    }
    validated_events = [
        e for e in overridable_events
        if historical_db.get(classify_event_type(e), {}).get("accuracy", 0.5) > 0.6
    ]
    if not validated_events:
        return None

    # === 第三层：LLM 判断（带幅度限制）===
    original_limit = market_context.position_limit
    prompt = f"""
技术面判断：{market_context.tech_growth}
当前仓位上限：{original_limit}

经过验证的重大事件：{validated_events}

问题：这些事件是否足以覆盖技术面判断？

约束：
- 仓位调整幅度不得超过 ±0.2
- 必须说明历史先例
- 如果不确定，维持技术面判断

输出 JSON：
{{
  "override": bool,
  "overridden_position_limit": float,
  "reason": str,
  "historical_precedent": str
}}
"""
    llm_result = await llm.call(prompt)

    # 硬性约束：幅度限制 ±0.2
    proposed = llm_result.overridden_position_limit
    clamped = clip(proposed, original_limit - 0.2, original_limit + 0.2)

    return {
        "override": llm_result.override,
        "overridden_position_limit": clamped,
        "original_position_limit": original_limit,
        "validated_events": validated_events,
        "historical_precedent": llm_result.historical_precedent,
        "reason": llm_result.reason,
        "amplitude_clamped": clamped != proposed,
    }
```

**Step 4：时间一致性校验（P2-5 决策：4 类异常检测）：**

> **持久化方案（v0.3 决策）：**
> - 存储介质：JSON 文件，按交易日日期命名（如 `previous_context_20260721.json`），存放在 `data/aggregator_contexts/` 目录
> - 跨节假日处理：仅取交易日数据，周一运行自动加载上周五的 JSON；节假日休市日不触发分析，不产生新 JSON
> - 首日运行：无 `previous_context` 文件，跳过时间一致性校验（当天不保护，仅记录基线）

```python
import json
from pathlib import Path

CONTEXTS_DIR = Path("data/aggregator_contexts")


def load_previous_context(trading_date: str) -> dict | None:
    """加载最近一个交易日的聚合输出

    Args:
        trading_date: 当前交易日 YYYYMMDD，用于查找前一交易日文件
                      （由调用方通过交易日历确定前一交易日日期）

    Returns:
        前一交易日的 unified_context，不存在时返回 None
    """
    filepath = CONTEXTS_DIR / f"previous_context_{trading_date}.json"
    if not filepath.exists():
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_current_context(context: dict, trading_date: str):
    """保存当前交易日的聚合输出，供下一交易日比对"""
    CONTEXTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = CONTEXTS_DIR / f"previous_context_{trading_date}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)


def detect_temporal_inconsistencies(current_context, previous_context):
    """检测信号时间一致性（需前一交易日 JSON 文件）"""

    if previous_context is None:
        return []  # 首日运行，跳过校验，仅记录当天基线

    inconsistencies = []

    # 检测 1：技术面趋势翻转
    cur_trend = current_context.market.tech_growth.trend
    prev_trend = previous_context.market.tech_growth.trend
    if ((cur_trend == "bullish" and prev_trend == "bearish") or
        (cur_trend == "bearish" and prev_trend == "bullish")):
        score_delta = abs(current_context.market.tech_growth.score -
                         previous_context.market.tech_growth.score)
        if score_delta > 0.8:
            inconsistencies.append({
                "type": "sudden_trend_flip",
                "description": f"技术面 overnight 翻转: {prev_trend} → {cur_trend}（分差 {score_delta:.2f}）",
                "severity": "critical",
                "action": "manual_review_required",
            })
        elif score_delta > 0.5:
            inconsistencies.append({
                "type": "trend_flip",
                "description": f"技术面趋势翻转: {prev_trend} → {cur_trend}",
                "severity": "high",
                "action": "investigate_root_cause",
            })

    # 检测 2：置信度剧烈波动
    conf_delta = abs(current_context.overall_confidence - previous_context.overall_confidence)
    if conf_delta > 0.4:
        inconsistencies.append({
            "type": "confidence_collapse",
            "description": f"置信度剧烈波动: {previous_context.overall_confidence:.2f} → {current_context.overall_confidence:.2f}",
            "severity": "high",
            "action": "reduce_position",
        })

    # 检测 3：主线快速切换
    cur_mainline = set(s.code for s in current_context.sector.active_mainline)
    prev_mainline = set(s.code for s in previous_context.sector.active_mainline)
    if prev_mainline:
        overlap_ratio = len(cur_mainline & prev_mainline) / len(prev_mainline)
        if overlap_ratio < 0.3 and len(prev_mainline) >= 2:
            inconsistencies.append({
                "type": "rapid_mainline_shift",
                "description": f"主线快速切换: {prev_mainline} → {cur_mainline}",
                "severity": "medium",
                "action": "verify_sector_analysis",
            })

    # 检测 4：仓位上限剧烈变化
    limit_delta = abs(current_context.final_position_limit - previous_context.final_position_limit)
    if limit_delta > 0.4:
        inconsistencies.append({
            "type": "position_limit_swing",
            "description": f"仓位上限剧烈变化: {previous_context.final_position_limit:.0%} → {current_context.final_position_limit:.0%}",
            "severity": "high",
            "action": "cap_adjustment",
        })

    return inconsistencies
```

**Step 5：最终综合研判（整合所有异常信号）：**
```python
async def final_synthesis(all_contexts, contradictions, contradiction_analyses,
                          news_override, temporal_inconsistencies, previous_context):
    """最终综合研判（含时间一致性 + 降级信息）"""

    # 降级通知
    degradation_notice = ""
    for name, ctx in [("资金面", all_contexts.capital),
                      ("消息面", all_contexts.news),
                      ("情绪面", all_contexts.sentiment)]:
        if getattr(ctx, "degraded", False):
            degradation_notice += f"- {name}: 缺失 {ctx.missing_dimensions}\n"

    # Critical 异常 → 强制人工审核
    critical = [i for i in temporal_inconsistencies if i["severity"] == "critical"]
    if critical:
        return {
            "status": "manual_review_required",
            "anomalies": critical,
            "reason": "检测到 critical 级别时间异常，需人工审核",
        }

    prompt = f"""
你是资深投资顾问。根据以下多维度分析结果，进行最终综合研判。

各维度信号：
- 大盘技术面（含多时间框架）：{all_contexts.market_context}
- 板块状态（含生命周期）：{all_contexts.sector_context}
- 机构态度：{all_contexts.capital_context}
- 消息面：{all_contexts.news_context}
- 散户情绪（含反身性信号）：{all_contexts.sentiment_context}

矛盾分析（批处理）：{contradiction_analyses}
消息面覆盖：{news_override}
时间一致性异常：{temporal_inconsistencies}
{"降级通知：" + degradation_notice if degradation_notice else ""}

请输出最终综合判断（JSON）：
1. unified_signal: "bullish" | "bearish" | "neutral" | "cautious"
2. overall_confidence: float (0~1)
3. final_position_limit: float (0~1)
4. contradictions_summary: str
5. key_risks: list[str]
6. key_opportunities: list[str]
7. reasoning: str（200字以内）
"""

    return await llm.call(prompt)
```

**聚合节点完整流程（v2）：**
```
Phase A: 信号标准化 + 置信度加权聚合
  ├─ 5 个 ctx → DimensionSignal 投影（score × confidence）
  ├─ failed 维度自动跳过，剩余归一化
  ├─ 置信度加权聚合 → weighted_score
  └─ 收敛剖面分类（full_convergence / mild_divergence / ...）
       ↓
Phase B: 异常检测
  ├─ 自动矛盾检测（O(n²) 两两对比，n≤5 → 最多 10 对）
  ├─ 矛盾分类（factual / temporal / opinion）
  ├─ EMA 时间一致性（z-score > 2σ → high，> 3σ → critical）
  └─ 消息面覆盖（policy + high + 多源确认 → LLM 判断）
       ↓
  需要 LLM 精炼？──否──→ 规则映射输出（零 LLM 调用）
       ↓ 是
Phase C: 条件 LLM 精炼
  ├─ C1 生成：聚合结论 + 分歧模式 → 初始研判
  └─ C2 批判：初始研判 → 找盲点 → 最终调整（反 sycophancy）
       ↓
  仓位日限幅 ±0.2 + EMA 基线更新
       ↓
  unified_context（含 convergence_profile + dimension_contributions）
```

**输出 Schema（v2）：**
```yaml
aggregator_output:
  status: str                      # "normal" | "manual_review_required"
  unified_signal: str              # "bullish" | "bearish" | "neutral" | "cautious"
  overall_confidence: float        # 综合置信度（0.0 ~ 1.0）
  final_position_limit: float      # 最终仓位上限（日限幅 ±0.2）

  # === 矛盾分析（v2：自动检测 + 矛盾分类）===
  contradictions: list[
    pair: str                      # "market_vs_capital" 等（自动生成）
    timeframe: str                 # "cross"
    description: str
    severity: str                  # "high" | "medium"
    conflict_type: str             # "factual" | "temporal" | "opinion"（参考 ConflictRAG）
    resolution: str
    confidence_adjustment: float
  ]
  common_root_cause: str | null

  # === 消息面覆盖（v2：简化）===
  news_override:
    override: bool
    overridden_position_limit: float
    original_position_limit: float
    validated_events: list
    reason: str

  # === 时间一致性（v2：EMA 基线）===
  temporal_inconsistencies: list[
    type: str                      # "sudden_trend_flip" | "trend_flip"
    description: str
    severity: "critical" | "high"
    action: str
  ]

  key_risks: list[str]
  key_opportunities: list[str]
  concept_suggestions: list        # 透传自消息面节点
  reasoning: str                   # LLM 综合推理文本

  # === v2 新增字段（置信度加权聚合）===
  convergence_profile:
    pattern: str                   # "full_convergence" | "mild_divergence" | "strong_divergence" | "lone_outlier" | "low_confidence"
    agreement_score: float         # 0~1
    investigate_outlier: bool      # Surprising Popularity 标记
  weighted_score: float            # 置信度加权方向分（-1~+1）
  dimension_contributions: list[   # 各维度贡献明细
    name: str
    score: float
    confidence: float
    contribution: float
  ]
  llm_refinement_used: bool        # 是否使用了 Phase C LLM 精炼
  ema_baseline: float | null       # 当前 EMA 基线值
```

---

## 4. Phase 2: CoT 决策流水线

### 4.1 节点 4：持仓判断

**职责：** 判断当前持仓是否需要调整（加仓/减仓/换仓）。

**输入 Schema：**
```yaml
portfolio_judgment_input:
  unified_context:         # 来自"聚合节点"
    unified_signal: str
    overall_confidence: float
    recommended_position: float
    key_risks: list
    key_opportunities: list
  market_context:          # 来自"大盘分析"（保留用于细节参考）
    trend: str
    position_limit: float
  sector_context:          # 来自"板块分析"
    top_sectors: list
  capital_context:         # 来自"资金面分析"
    main_force_flow: dict
  news_context:            # 来自"消息面分析"
    major_events: list
  sentiment_context:       # 来自"情绪分析"
    timing_signal: str
  current_portfolio:       # 当前持仓
    holdings: list[
      stock_code: str
      stock_name: str
      quantity: int
      cost_price: float
      current_price: float
      pnl_ratio: float     # 盈亏比例
    ]
    total_assets: float
    cash: float
    position_ratio: float  # 当前仓位比例
```

**判断逻辑（混合：规则引擎 + LLM）：**

**规则引擎部分：**
```python
# 强制减仓规则
if market_trend == "bearish" and position_ratio > 0.5:
    return "reduce_position"

# 强制止损规则
if any(stock.pnl_ratio < -0.10 for stock in holdings):
    return "stop_loss"

# 强制调仓规则
if position_ratio > market_position_limit:
    return "rebalance"
```

**LLM 判断部分：**
```python
# 灰色地带（规则未覆盖的情况）
prompt = f"""
当前持仓：
{portfolio_summary}

市场背景：
- 大盘：{market_context}
- 板块：{sector_context}
- 情绪：{sentiment_context}

规则引擎建议：{rule_engine_suggestion}

请判断：是否需要调整持仓？如果需要，怎么调整？

输出：
1. 判断结果（持有/减仓/加仓/换仓）
2. 调整理由
3. 具体操作建议
"""
```

**输出 Schema：**
```yaml
portfolio_judgment_output:
  action: str              # "hold" | "reduce" | "add" | "rebalance"
  reason: str              # 调整理由
  target_position_ratio: float  # 目标仓位比例
  priority_stocks: list[str]    # 优先处理的股票
  reasoning: str                # LLM 推理过程
```

---

### 4.2 节点 5：选股评分

**职责：** 根据市场背景和持仓情况，筛选并评分候选股票。

**输入 Schema：**
```yaml
stock_scoring_input:
  market_context:          # 来自"大盘分析"
  sector_context:          # 来自"板块分析"
    top_sectors: list
  sentiment_context:       # 来自"情绪分析"
    hot_stocks: list
  portfolio_action:        # 来自"持仓判断"
    action: str
    priority_stocks: list
  candidate_pool:          # 候选股票池
    source: str            # "ths_screener" | "eastmoney" | "custom"
    filters: dict          # 筛选条件
  top_n: int               # 返回 Top N（默认 10）
```

**工具调用：**
```python
# 复用现有服务
candidates = await THSScreenerService.screen(filters)
technical_scores = await TechnicalIndicatorService.score(candidates)
screener_results = await StockScreenerPipeline.run(candidates, technical_scores)
```

**评分逻辑（五维综合）：**
```python
composite_score = (
    strategy_score * 0.20 +
    technical_score * 0.30 +
    sector_score * 0.25 +
    sentiment_score * 0.15 +
    (100 - risk_score) * 0.10
)
```

**输出 Schema：**
```yaml
stock_scoring_output:
  recommendations: list[
    stock_code: str
    stock_name: str
    composite_score: float      # 综合评分（0-100）
    dimension_scores:
      strategy: float
      technical: float
      sector: float
      sentiment: float
      risk: float
    signals: list[str]          # 信号标签（"主力强攻"、"多头排列"等）
    target_price: float         # 目标价
    stop_loss_price: float      # 止损价
    reasoning: str              # 推荐理由
  ]
  reasoning: str                # LLM 推理过程
```

---

### 4.3 节点 6：交易建议生成

**职责：** 根据评分结果和持仓判断，生成最终交易建议。

**输入 Schema：**
```yaml
trade_suggestion_input:
  portfolio_judgment:        # 来自"持仓判断"
    action: str
    target_position_ratio: float
  stock_recommendations:     # 来自"选股评分"
    recommendations: list
  current_portfolio:         # 当前持仓
    holdings: list
    cash: float
```

**生成逻辑：**
```python
if portfolio_action == "reduce":
    # 优先卖出亏损股票
    suggestions = generate_sell_suggestions(holdings, target_ratio)
elif portfolio_action == "add":
    # 优先买入高分股票
    suggestions = generate_buy_suggestions(recommendations, cash)
elif portfolio_action == "rebalance":
    # 卖出弱势股票，买入强势股票
    suggestions = generate_rebalance_suggestions(holdings, recommendations)
else:  # hold
    suggestions = []
```

**输出 Schema：**
```yaml
trade_suggestion_output:
  suggestions: list[
    action: str              # "buy" | "sell" | "hold"
    stock_code: str
    stock_name: str
    quantity: int            # 建议数量（股）
    price_range:             # 建议价格区间
      min: float
      max: float
    reason: str              # 操作理由
    confidence: float        # 置信度（0-1）
    expected_return: float   # 预期收益（%）
    risk_reward_ratio: float # 风险收益比
  ]
  summary: str               # 操作总结
  warnings: list[str]        # 风险提示
```

---

## 5. 输出层

### 5.1 市场分析报告（Markdown）

```markdown
# 市场分析报告（2026-07-21）

## 一、大盘分析

**趋势判断：** 震荡偏空  
**置信度：** 65%  
**建议仓位：** ≤ 50%

**关键点位：**
- 支撑位：3200 点
- 阻力位：3300 点

**推理过程：**  
上证指数近 5 日成交量萎缩，MACD 死叉，短期偏弱...

---

## 二、板块分析

**市场方向：** 分化  

**Top 5 强势板块：**
1. 半导体（强度：85，轮动：流入）
   - 龙头股：002935 天奥电子、300377 赢时胜
2. 新能源车（强度：78，轮动：稳定）
   - 龙头股：...

**推理过程：**  
半导体板块近 3 日资金净流入 15 亿，板块指数突破前高...

---

## 三、情绪分析

**整体情绪：** 中性  
**情绪评分：** 52/100  

**散户情绪：** 论坛情绪偏谨慎，热门股票集中在 AI 板块  
**机构情绪：** 北向资金近 3 日净流出 20 亿  

**时机信号：** 观望  

---

## 四、持仓建议

**当前仓位：** 60%  
**建议仓位：** 50%  
**操作：** 减仓 10%  

**理由：**  
大盘偏空，建议降低仓位...

---

## 五、交易建议

### 建议 1：买入 002935 天奥电子
- **数量：** 1000 股
- **价格区间：** 25.0 - 25.5 元
- **理由：** 半导体龙头，技术面多头排列，主力资金净流入
- **预期收益：** 10%
- **止损价：** 23.5 元
- **风险收益比：** 2.5

### 建议 2：卖出 603335 迪生力
- **数量：** 500 股
- **理由：** 板块轮动流出，技术面破位

---

**风险提示：**  
本报告仅供参考，不构成投资建议。股市有风险，入市需谨慎。
```

### 5.2 交易建议（JSON，可选）

```json
{
  "date": "2026-07-21",
  "suggestions": [
    {
      "action": "buy",
      "stock_code": "002935",
      "stock_name": "天奥电子",
      "quantity": 1000,
      "price_range": {"min": 25.0, "max": 25.5},
      "reason": "半导体龙头，技术面多头排列",
      "confidence": 0.75,
      "expected_return": 0.10,
      "risk_reward_ratio": 2.5
    }
  ]
}
```

---

## 6. 复用策略

### 6.1 直接调用（核心服务）

以下服务直接调用，不包装成 Tool：

| 服务 | 用途 | 调用方式 |
|------|------|---------|
| `DataFetcher` | 获取 K 线、指数、资金流向、龙虎榜、ETF申赎 | 直接调用 |
| `SectorRotationService` | 板块轮动分析 | 直接调用 |
| `ForumSentimentService` | 雪球舆情采集 | 直接调用 |
| `NewsService` | 新闻采集（政策/行业/公司/国际） | 直接调用 |
| `THSScreenerService` | 同花顺问财选股 | 直接调用 |
| `TechnicalIndicatorService` | 技术指标评分 | 直接调用 |
| `StockScreenerPipeline` | 选股流水线 | 直接调用 |
| `PortfolioService` | 持仓管理 | 直接调用 |

**理由：** 这些服务的输入输出已经是结构化数据，直接调用效率最高。

### 6.2 包装成 Tool（复杂分析）

以下场景包装成 LLM 可调用的 Tool：

| Tool | 输入 | 输出 | 用途 |
|------|------|------|------|
| `analyze_market` | 指数数据 | 市场趋势判断 | 大盘分析节点 |
| `analyze_sector` | 板块数据 + 市场背景 | 强势板块列表 | 板块分析节点 |
| `analyze_capital_flow` | 资金数据 + 市场背景 | 资金流向判断 | 资金面分析节点 |
| `analyze_news` | 新闻数据 + 市场背景 | 事件影响评估 | 消息面分析节点 |
| `analyze_sentiment` | 舆情 + 资金流 + 新闻 | 情绪评分 | 情绪分析节点 |
| `aggregate_contexts` | 多维度分析结果 | 综合研判 | 聚合节点 |
| `judge_portfolio` | 持仓 + 市场背景 | 调整建议 | 持仓判断节点 |
| `score_stocks` | 候选股 + 市场背景 | 评分列表 | 选股评分节点 |
| `generate_suggestions` | 评分 + 持仓 | 交易建议 | 交易建议节点 |

**理由：** 这些节点需要 LLM 推理，包装成 Tool 可以让 LLM 灵活调用。

---

## 7. MVP 规划

### 7.1 Phase 1：跑通数据流（2-3 天）

**目标：** 验证"大盘→板块→资金面→消息面→情绪→聚合"串行链能跑通。

**任务：**
1. 定义所有节点的输入输出 Schema（本文档已完成）
2. 实现"大盘分析"节点（mock 数据）
3. 实现"板块分析"节点（mock 数据）
4. 实现"资金面分析"节点（mock 数据）
5. 实现"消息面分析"节点（mock 数据）
6. 实现"情绪分析"节点（mock 数据）
7. 实现"聚合节点"（mock 数据）
8. 串联六个节点，打印输出

**验收标准：**
- 能跑通完整流程
- 输出格式符合 Schema
- 聚合节点能检测维度间矛盾

### 7.2 Phase 2：接入真实数据（2-3 天）

**目标：** 用真实数据替换 mock 数据。

**任务：**
1. 接入 `DataFetcher` 获取指数数据
2. 接入 `SectorRotationService` 获取板块数据
3. 接入 `ForumSentimentService` 获取舆情数据
4. 调优 LLM Prompt

**验收标准：**
- 能输出真实的市场分析报告

### 7.3 Phase 3：接入持仓和选股（2-3 天）

**目标：** 实现后半段流水线。

**任务：**
1. 实现"持仓判断"节点
2. 实现"选股评分"节点
3. 实现"交易建议"节点
4. 生成最终报告

**验收标准：**
- 能输出完整的交易建议

### 7.4 Phase 4：优化和测试（持续）

**目标：** 调优规则、测试准确性。

**任务：**
1. 调优规则引擎的阈值
2. 回测历史数据，验证准确率
3. 收集用户反馈，迭代优化

---

## 8. 待讨论事项

以下事项需要在实现过程中逐步明确：

1. **节点间数据传递：** 每个节点的具体输出字段，下游如何使用？
2. **规则引擎阈值：** 强制减仓/止损的具体数值？
3. **评分权重：** 五维综合评分的权重是否需要动态调整？
4. **LLM 降级策略：** LLM 不可用时，是否用规则引擎替代？
5. **异常处理：** 数据获取失败时，如何处理？

---

## 9. 附录

### 9.1 相关文档

- 选股系统设计：`docs/custom/screener_design.md`
- 项目总览：`CLAUDE.md`

### 9.2 参考代码

- 板块轮动：`src/services/sector_rotation.py`
- 舆情服务：`src/services/forum_sentiment.py`
- 选股流水线：`src/services/stock_screener.py`
- 技术指标：`src/services/technical_indicator.py`
- 持仓管理：`src/services/portfolio.py`
