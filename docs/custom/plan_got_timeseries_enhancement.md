# GOT 时间线能力增强计划

> 版本：v2.1，2026-08-27
> 状态：待评审（v2.1 修订：基于代码事实核查修正 6 处问题——regime 权威归属、仓位三重叠加、不可计算字段、闸门粒度错位、悬空三值态、量纲错误）
> 关联：Node 3/4/5/6 + `src/got/schemas.py` + `src/got/persistence.py` + `src/storage.py`
> 注意：v2.0 的 Node 1 改动已全部撤回，regime 状态机改挂 Node 6 Level 0（见 4.2）

---

## 1. 背景：GOT 各节点时间线能力现状

| 节点 | 现有时间线机制 | 缺口 |
|------|--------------|------|
| **Node 1** 大盘 | 多时间框架 mini-GOT（short/medium/long）、波动率序列 | regime 是单日快照分类，无跨日连续性（持续时长 + 切换轨迹） |
| **Node 2** 板块 | ✅ 最完善：SQLite 趋势表、strength_trajectory、RRG 前日衔接、生命周期状态机（dwell days）、动量 EMA 序列、B-lite 3/5 日累计 | 基本无 |
| **Node 3** 资金面 | ❌ 纯盘中 T+0 快照（板块资金流"即时"、DDE 盘中、北向分钟级） | **缺口最大**：无任何逐日序列，吸筹闸门已标注"待升级" |
| **Node 4** 消息面 | 催化剂日历（前瞻时间线）、7 日历史事件注入、原始新闻跨日发酵检测 | 缺催化剂落地后的回溯验证闭环 |
| **Node 5** 情绪 | sentiment_momentum（vs EMA(5) 偏差） | 反身性 5 阶段是单日快照分类，无阶段停留时长和转换轨迹 |
| **Node 6** 聚合 | EMA 基线、前日蓄水池结转、时间一致性校验 | 基本无 |

**Node 2/6 时间线已较完备，不建议增加。本计划聚焦 Node 3（P0）/ Node 1（P1）/ Node 5（P2）/ Node 4（P2）。**

---

## 2. 学术依据

### 2.1 Node 3 资金面：多日资金流的预测力

| 论文 | 核心发现 |
|------|---------|
| **Nomura (2011)**《Factor Dynamics in CSI 300》 | Capital Inflows Ratio 的 **5-8 日窗口**对未来收益预测相关系数最高，比纯价格动量超额 68%。机构订单拆分周期 5-8 天 |
| **Frijns et al. (2014)**《Institutional Trading in China》 | 机构交易的价格压力效应在事件日前后各持续 **5 天**，证实订单拆分 / 羊群行为 |
| **JFQA (2025)**《Foreign Capital in Chinese Stock Market》 | 外资订单流预测能力延续到未来 **12 周**，信息非瞬时 |
| **Zheng et al. (2015)**《Herding in Institutional Investors》 | **买方羊群**比卖方更强，持续羊群对 1 月/1 季度收益正相关 |

**对 Node 3 的映射**：单日快照是噪声，多日累计才是信号。当前吸筹闸门降级为"当日正向流入即通过"，单日脉冲即可骗过闸门。

### 2.2 Node 1 大盘：regime 持续时长依赖性

| 论文 | 核心发现 |
|------|---------|
| **Liu & Wang (2017)**《Decoding Chinese Stock Market Returns: Three-state HSMM》 | A 股三态 HSMM（bear/sidewalk/bull）：牛市预期持续 **18 个月**，熊市 **34 个月**。A 股特有"无缓冲区"——牛熊可直接切换 |
| **Chen et al. (2011)**《Are Chinese Stock Market Cycles Duration Independent?》 | A 股 **存在持续时长依赖**（duration dependence），拒绝随机游走假设。扩张期的历史长度影响未来转换概率 |
| **Chen et al. (2018)**《Multi-Factor Asset-Pricing under Markov Regime Switches》 | 熊市状态转移概率 0.97（持续 34 个月），牛市 0.94（18 个月）。**不同 regime 下风险溢价显著不同** |
| **Yu et al. (2016)**《Forecasting Bull and Bear Markets: Evidence from China》 | 动态自回归模型预测牛熊效果最好，说明 **regime 具有记忆性** |
| **Fu & Wu (2021)**《Regime-switching Herd Behavior》 | 高波动 regime 下羊群行为显著，说明 regime 持续期间市场微观结构不同 |

**对 Node 1 的映射**：当前 regime 每日独立计算，无法区分"刚转牛 1 天"和"已持续 2 周牛市"——两者应有完全不同的仓位上限和风险预算。学术证据表明 regime 持续时长是强预测因子。

### 2.3 Node 5 情绪：反身性阶段持续与转换

| 论文 | 核心发现 |
|------|---------|
| **Han et al. (2022)**《Persistence of Investor Sentiment and Market Mispricing》 | 情绪存在**两种状态**：高持续期（trending，异象显著）和低持续期（mean-reverting，异象消失）。**情绪持续期本身携带信息** |
| **Da et al. (FEARS)** | 高频情绪冲击预测 **2 日短期反转**。情绪持续性越低，反转越快 |
| **Qasim (2025)**《State-Dependent Pricing of Investor Sentiment》 | 情绪风险在牛市**不被定价**，在熊市/危机期**显著定价**。情绪阶段的切换方向（恶化 vs 改善）比绝对水平更重要 |
| **CNN FG Index Paper (2024)** | FG 指数预测力**随时间衰减**，2014 后显著减弱。说明情绪指标需要持续校准 |
| **Muguto et al. (2025)** | 情绪效应是 **regime-specific 和 time-varying** 的 |

**对 Node 5 的映射**：当前 5 阶段分类（euphoria→capitulation）每天独立打标签。但反身性理论的核心恰恰是阶段转换序列——连续 3 天从 optimism 滑向 euphoria 是顶部预警，而从 euphoria 跌回 optimism 是反转信号。**阶段停留时长和转换方向**是关键缺失信号。

### 2.4 Node 4 消息面：催化剂兑现与 PEAD

| 论文 | 核心发现 |
|------|---------|
| **Lan et al. (2024)**《PEAD: Earnings Surprise Measuring in China》 | A 股 PEAD 效应显著：利好惊喜增强漂移，利空惊喜减弱漂移。**投资者关注度是中介变量** |
| **Cai et al. (2021)**《Institutional Investors and PEAD in China》 | A 股机构**加剧** PEAD（而非加速价格发现），羊群行为导致价格过度反应后缓慢修正 |
| **Chen & Gao (2021)**《Limited Attention and PEAD in China》 | 散户为主的 A 股，注意力受限导致对信息**反应不足**，PEAD 持续更久 |
| **Li & Dempsey (2017)**《PEAD as Priced Risk Factor in China》 | PEAD 在 A 股是**被定价的风险因子**，60 日窗口产生 7.92% 超额收益 |

**对 Node 4 的映射**：催化剂日历已记录前瞻事件，但事件发生后缺少闭环验证——催化剂是否兑现、板块异动持续多久、PEAD 漂移方向是否与预期一致。这既是 B 公式置信度校准的唯一数据源，也是埋伏退出信号的基础。

---

## 3. 总体设计原则

### 3.1 统一持久化层（v2.1：回读优先原则）

code fact：`data/got_daily/<date>/` 每日归档已含 `capital.json`（sector_flow_all 464 板块 + DDE）、`market.json`（regime/attack_power）、`sentiment.json`（score）、`unified.json`（market_regime）。**所有跨日状态一律从现有归档回读，不新建平行存储**：

```
src/got/persistence.py 扩展（只加读工具，不加写路径）：
├─ load_capital_flow_series(days=N) → {板块名: [(日期, 净流入)]}   # Node 3
├─ load_previous_regime_state() → Optional[dict]                  # Node 6，从 unified.json
├─ load_previous_stage_state() → Optional[dict]                   # Node 5，从 sentiment.json
└─ load_history(node_key, days=N) → list[dict]                    # 通用
```

唯一新增存储：`data/got_timeseries/monitoring.json`（生产监控，非状态源）。Node 6/5 的状态机字段随当日 Context 归档自然落盘，无需额外写入。

### 3.2 降级一致性

所有时间线增强的降级策略统一：
- **首次运行**（无前日数据）：冷启动模式，时间线维度输出中性值，不阻塞节点
- **数据缺失**（某日前日档不存在）：跳过该日，不插值
- **连续缺失 ≥5 日**：时间线维度整体降级为 None，权重归一化

### 3.3 与 Node 2 模式对齐

Node 2 已实现的时间线模式作为参考标准：
- SQLite 持久化（`node2_sector_trends`）
- 状态机 + dwell days
- 前日数据衔接
- strength_trajectory 序列

---

## 4. 各节点设计方案

### 4.1 Node 3 资金面（P0）

#### 4.1.1 现状问题

```
板块资金流 → 当日净流入 Top N（akshare "即时"）
DDE 大单  → 当日全市场合计（DdeFetcher 仅当日）
北向资金  → 已停披豁免

吸筹闸门（aggregator.py L1298）注释：
"降级为当日正向流入即通过，待 P6 接入逐日数据后升级"
```

#### 4.1.2 设计

**数据源：归档重建为主，akshare 为辅（v2.1 修正）**

code fact（2026-08-27 验证）：`data/got_daily/` 已有 27 天归档，每日 `capital.json → evidence.sector_flow.sector_flow_all` 含全部 464 板块当日净流入（亿）。**逐日序列可直接从归档重建，无需任何新数据源**。v2.0 依赖 akshare "3日排行/5日排行" 存在两个致命问题：① 窗口累计值无法倒推"连续正流入天数"；② 3 次调用增加反爬面。

```
板块级逐日资金流序列（主数据源）：
├─ 来源：data/got_daily/*/capital.json 的 sector_flow_all
├─ 新工具函数 load_capital_flow_series(days=N) → {板块名: [(日期, 净流入)]}
├─ 派生指标（全部日均口径）：
│   ├─ flow_1d：当日值（现有）
│   ├─ flow_3d_avg / flow_5d_avg：近 3/5 日均值（板块级映射）
│   └─ consistency_days：连续净流入 > 0 天数（逐日序列倒推，可计算）
│
akshare "3日排行/5日排行"（仅辅助交叉校验）：
├─ 归档完整时不调用；仅在试点验证与归档缺失日抽查时使用
└─ 触发限流 → 不影响主流程（归档独立）
```

**多窗口加权（统一日均口径，修正 v2.0 量纲混用）**：

```
flow_score = flow_1d × 0.4 + flow_3d_avg × 0.3 + flow_5d_avg × 0.3
acceleration = flow_5d_avg / flow_1d − 1   （flow_1d ≤ 0 时为 None）
```

**mainline_bonus 梯度化（强度×持续性，上限维持 0.15）**：

```
mainline_bonus = clip(avg_flow_rank_pct × consistency_days × 0.02, 0, 0.15)
# v2.0 提议上限 0.30 撤回：会使 Node 3 总分分布漂移，冲击 Node 6 regime 的
# institutional_score 阈值语义；0.30 留待 Phase 5b 校准后再评估。
```

**DDE 历史序列（不新建表，修正 v2.0）**：`capital.json → evidence.dde` 已逐日归档全市场 DDE，累计 DDE 与连续正流入天数从归档回查（与板块资金流共用 `load_capital_flow_series` 工具族）。`node3_daily_dde` SQLite 表方案撤回。

**吸筹闸门升级**（aggregator.py `_check_accumulation_gate`，三值态 + 明确消费方）：

```
旧：当日净流入 > 0 → 通过（bool 两值态）
新（板块级，先查逐日序列，归档缺失降级当日判定）：
├─ 连续 3 日正流入          → accum_strength="strong"
├─ 3 日累计 > 0（非连续）    → accum_strength="medium"
├─ 仅当日正流入             → accum_strength="weak"（条件通过）
└─ 冷启动（归档 < 3 天）     → 旧逻辑，accum_strength="legacy"

三值态消费方（修正 v2.0 悬空分支）：
├─ AmbushCandidate 新增 accum_strength 字段
├─ Node 6 catalyst_score 乘数：strong 1.0 / medium 0.8 / weak 0.6 / legacy 1.0
└─ reasoning 输出一句话档位依据，可审计可回测
```

#### 4.1.3 Schema 变更

```python
# CapitalEvidence.sector_flow 为 dict 类型，新增键天然向后兼容：
# sector_flow_all_3d: dict[str, float]  # 板块级 3 日滚动均值（日均口径，亿）
# sector_flow_all_5d: dict[str, float]  # 板块级 5 日滚动均值（供 Node 6 闸门消费）
# mainline_consistency_days: int        # 主线板块连续正流入天数（逐日序列倒推）
# acceleration: Optional[float]         # flow_5d_avg / flow_1d − 1
#
# dde 新增键：
# cumulative_3d / cumulative_5d         # 归档回查累计 DDE
# consecutive_positive_days: int        # 连续正流入天数（归档回查）
```

```python
# schemas.py AmbushCandidate 扩展
class AmbushCandidate(BaseModel):
    # ... 现有字段 ...
    accum_strength: str = "legacy"  # strong / medium / weak / legacy（吸筹档位）
```

#### 4.1.4 IO 成本评估（v2.1 大幅优化）

| 数据源 | 调用方式 | 额外 IO |
|--------|---------|---------|
| 板块逐日序列 | 本地读 ≤30 个 capital.json | <50ms |
| DDE 序列 | 归档回查（同上） | <10ms |
| akshare 3d/5d 排行 | 可选抽查，`em_rate_limiter` 限流 | 0~2 次 HTTP |

**反爬风险**：归档为主后几乎为零；akshare 调用仅用于试点期交叉校验。

---

### 4.2 Regime 状态机（P1，v2.1 改挂 Node 6 Level 0）

#### 4.2.1 现状问题（阈值描述已按代码事实修正）

```
code fact：当前系统已有两个 regime 定义，均为单日快照：
├─ Node 1 market_state.regime（market_analysis.py _build_market_state）：
│   ├─ trend=="bullish" 且 attack_power > 0.6 → bull
│   ├─ trend=="bearish" 或 (crash_override 且 attack_power < 0.3) → bear
│   └─ 其余 → transition（注意：不是 v2.0 误写的 "score > 0.3"）
└─ Node 6 Level 0 _compute_market_regime：5 节点综合 → regime/risk_budget

问题：
├─ 两个权威、均无跨日记忆，每日从零重算（v2.0 方案会引入第三个，已撤回）
├─ 无法区分"刚转牛 1 天"和"已持续 2 周牛市"
├─ 仓位在 transition 边缘反复横跳（实测风险）
└─ regime 切换无确认机制（单日翻转即生效）
```

#### 4.2.2 设计：状态机挂在 Node 6 Level 0（唯一权威）

按 DAG 分工（Node 6 是唯一能消费所有证据源的节点），**不在 Node 1 新增第三套 regime**。Node 1 的 `market_state.regime` 保持单日技术信号不变，作为 Node 6 的输入素材。状态机叠加在 Node 6 已有的 `_compute_market_regime` 输出之上。

```
RegimeState（扩展在 Node 6 MarketRegime 上，非新建类）：
├─ current_regime: str       # 当日 _compute_market_regime 输出（经确认后）
├─ dwell_days: int           # 当前 regime 持续交易日数
├─ previous_regime: Optional[str]
├─ transition_count: int     # 近 20 交易日确认切换次数（震荡度）
└─ transition_pending: Optional[str]  # 待确认的新 regime
```

**持久化：不新建目录**，复用现有 `data/got_daily/<前日>/unified.json`（`market_regime` 已随归档落盘）。前日回读沿用现有 `_load_previous_reservoir` 的同款模式（扫描最近归档目录）。

**状态转换规则**（Node 6 在 `_compute_market_regime` 后执行）：

```
├─ 回读前日 unified.json 的 market_regime 扩展字段
├─ raw_regime = 当日 _compute_market_regime 输出（5 节点综合）
├─ 转换确认（防单日翻转）：
│   ├─ raw == previous → dwell_days += 1
│   ├─ raw != previous 且 raw == transition_pending → 确认切换，dwell_days = 1
│   ├─ raw != previous 且 raw != transition_pending → 保持 previous，
│   │   transition_pending = raw（首次出现，待次日确认）
│   └─ 冷启动（无归档/缺字段）→ current = raw，dwell_days = 1，不阻塞
├─ transition_count：近 20 交易日确认切换次数（从归档序列统计）
└─ 写回 market_regime，随 unified.json 当日归档落盘（无需额外持久化层）
```

**仓位上限调整：合并进现有 `_compute_position_from_regime`，禁止新增相乘层**

code fact：仓位已叠三层——Node 1 `position_limit`（含波动率调整 + `regime_scale` Layer 2 + 回撤保护）→ Node 6 `_compute_position_from_regime(regime, sector_intels, market.position_limit)` 再按 regime 缩放。v2.0 提议的 `position_limit *= regime_coefficient` 会造成三重叠加，熊市初期仓位被碾到近零，撤回。

v2.1 方案：将 `_compute_position_from_regime` 内部的 regime 缩放表替换为 dwell 感知版（**替换非相乘**）：

```python
REGIME_DWELL_SCALE = {
    ("bull", "established"): 1.0,   # dwell ≥ 5
    ("bull", "fresh"):      0.9,    # dwell < 5（初期谨慎，比 v2.0 的 0.8 温和）
    ("bear", "established"): 0.5,
    ("bear", "fresh"):       0.7,
    ("transition", "any"):   0.6,
}
# 震荡惩罚（唯一额外乘项）：transition_count ≥ 5 → × 0.8
```

Node 1 的 `position_limit` / `regime_scale`（Layer 2）**完全不动**。

#### 4.2.3 Schema 变更（v2.1 修正：扩展而非新增）

```python
# schemas.py MarketRegime 扩展（Node 6 Level 0 输出，现有类）
class MarketRegime(BaseModel):
    # ... 现有字段 ...
    dwell_days: int = 1
    previous_regime: Optional[str] = None
    transition_count: int = 0
    transition_pending: Optional[str] = None

# MarketContext 不新增任何字段（v2.0 的 regime_state 方案撤回）
```

#### 4.2.4 与现有 EMA 基线的关系

Node 6 已有 `ema_baseline.json`（regime_score 连续值平滑）与本次的离散状态机**同属 Level 0、同为跨日记忆**，前日均从归档回读，不存在 v2.0 担心的"两套体系共存"冲突：
- EMA 基线：连续值平滑，服务时间一致性校验（`_adaptive_temporal_check`）
- RegimeState：离散状态 + 持续时长，服务仓位缩放与矛盾检测（regime 刚切换且 dwell < 2 时，`_detect_contradictions` 可标记低置信度）

---

### 4.3 Node 5 情绪（P2）

#### 4.3.1 现状问题

```
当前反身性 5 阶段每日独立分类：
├─ sentiment_score > 0.5 → euphoria
├─ > 0.2 → optimism
├─ > -0.2 → neutral
├─ > -0.5 → pessimism
└─ else → capitulation

问题：
├─ 无阶段停留时长（dwell days）
├─ 无转换方向（升温 vs 降温）
├─ 单日跳变无法区分"渐进入 euphoria"vs"单日脉冲"
└─ 无法触发"连续 3 天升温 → 顶部预警"信号
```

#### 4.3.2 设计

**情绪阶段状态机**（参考 Node 2 状态机模式）：

```
SentimentStageState:
├─ current_stage: str       # euphoria / optimism / neutral / pessimism / capitulation
├─ dwell_days: int          # 当前阶段持续交易日数
├─ previous_stage: str
├─ momentum_direction: str  # warming / cooling / stable
├─ stage_transitions: list  # 近 10 日阶段转换序列
└─ contrarian_alert: bool   # 反向信号（连续升温到极端）
```

**反向信号规则**（学术依据：Han 2022 + Da FEARS）：

```
反向信号触发条件：
├─ 连续 3 天升温（neutral → optimism → euphoria）→ contrarian_sell
├─ 连续 3 天降温（neutral → pessimism → capitulation）→ contrarian_buy
├─ euphoria 持续 > 5 天 → contrarian_sell（过度贪婪）
├─ capitulation 持续 > 3 天 → contrarian_buy（恐慌耗尽）
└─ 阶段高频切换（10 日 ≥ 5 次）→ 震荡标记，不触发反向
```

**情绪持续性检测**（学术依据：Han 2022 双状态模型）：

```
sentiment_persistence:
├─ 计算近 20 日 sentiment_score 的 AR(1) 系数
├─ AR(1) > 0.7 → high_persistence（trending regime，异象显著）
├─ AR(1) < 0.3 → low_persistence（mean-reverting，反转策略有效）
└─ 输出到 SentimentContext.persistence_regime
```

#### 4.3.3 Schema 变更

```python
# SentimentContext 新增
class SentimentContext(BaseModel):
    # ... 现有字段 ...
    stage_state: Optional[SentimentStageState] = None  # ← 新增
    persistence_regime: Optional[str] = None  # high / low / unknown
    contrarian_alert: bool = False  # 从 stage_state 提取到顶层

class SentimentStageState(BaseModel):
    current_stage: str
    dwell_days: int
    previous_stage: str
    momentum_direction: str  # warming / cooling / stable
    stage_transitions: list[str]  # 近 10 日序列
```

---

### 4.4 Node 4 消息面（P2）

#### 4.4.1 现状问题

```
催化剂日历（前瞻）已完善：
├─ 5 源事件构建
├─ LLM 叙事提取
├─ B 公式排序
└─ P6 催化兑现追踪（catalyst_realizations 表）已存在

缺口：
├─ 事件兑现后的板块收益轨迹未记录
├─ PEAD 漂移方向和幅度未量化
├─ 无法回答"利好事件后板块平均涨几天"
└─ B 公式的置信度参数无校准数据源
```

#### 4.4.2 设计

**催化剂兑现轨迹扩展**（增强 `catalyst_realizations` 表）：

```
CatalystRealizationRecord 扩展字段：
├─ event_date → event_date + window_end_date
├─ 新增：
│   ├─ day_1_return / day_3_return / day_5_return  # 板块 T+1/3/5 收益
│   ├─ max_return_window  # 最大收益出现在第几天
│   ├─ max_return_pct  # 最大收益率
│   ├─ pead_direction  # 漂移方向（与预期一致/相反）
│   └─ attention_decay_days  # 关注度衰减天数
```

**每日兑现检测**（`CatalystRealizationTracker.check_and_record_realization` 增强）：

```
每日 run() 末尾：
├─ 加载所有未过期事件（event_date ≤ today - 1）
├─ 对每个事件：
│   ├─ 查找关联板块
│   ├─ 获取板块 T+1/3/5 收益
│   ├─ 计算 max_return 及出现时间
│   ├─ 判断 pead_direction
│   └─ 更新 catalyst_realizations 表
└─ 输出统计报告
```

**B 公式校准数据**：

```
按 event_type / category_tag 分组：
├─ avg_max_return_window  # 平均最大收益出现在第几天
├─ realization_rate  # 兑现率
├─ avg_pead_magnitude  # 平均 PEAD 幅度
└─ → 反馈到 catalyst_config.py 的 B 公式参数
```

#### 4.4.3 与 P4 埋伏退出的集成

P4 退出信号（`_ambush_exit_signal`）当前用固定规则（fade=次日卖、drift=10日衰竭）。催化剂兑现数据可提供**数据驱动退出**：

```
未来增强（本期不实施，仅预留接口）：
├─ 如果同类事件历史平均 max_return_window = 3 天
│   → drift 退出从 10 天改为 3 天
├─ 如果同类事件 realization_rate < 30%
│   → 降低该事件类型的 B 公式权重
└─ 需要至少 20 个同类事件的校准数据
```

#### 4.4.4 Schema 变更

```python
# CatalystRealizationRecord 扩展（storage.py）
class CatalystRealizationRecord(Base):
    # ... 现有字段 ...
    day_1_return = Column(Float)
    day_3_return = Column(Float)
    day_5_return = Column(Float)
    max_return_window = Column(Integer)  # 最大收益出现在第几天
    max_return_pct = Column(Float)
    pead_direction = Column(String(16))  # aligned / counter / neutral
```

---

### 4.5 跨节点时间线交互（Cross-Node Temporal Patterns）

> 各节点独立增强后，真正的 alpha 来自**跨节点时间线共振**。单节点时间线是基础，跨节点共振才是决策信号。

#### 4.5.1 四种共振模式

| 共振模式 | 触发条件 | 信号含义 | 学术依据 |
|---------|---------|---------|---------|
| **bull_confirmation** | Node 6 regime 确认切牛（dwell=1~2，刚过确认窗口）+ Node 3 flow_5d_avg 加速度 > 0.3 | 牛市初期资金确认——强买入信号 | Liu & Wang (2017)：A 股牛市初期波动大但资金流入持续 |
| **sentiment_top** | Node 5 contrarian_sell + Node 3 市场级 flow_5d_avg 转负 | 情绪顶部 + 资金撤退——强卖出信号 | Han (2022)：高情绪持续期 + 资金流出 = 泡沫破裂 |
| **catalyst_confirmation** | Node 4 催化兑现（day_3_return > 5%）+ Node 3 对应板块 consistency_days ≥ 3 | 催化确认 + 资金持续——加仓信号 | PEAD 文献：催化后资金持续流入延长漂移 |
| **range_reversal** | Node 6 transition_count ≥ 5 + Node 5 persistence_regime = "low" | 震荡市 + 情绪快速反转——短线反转策略有效 | Chen (2011) + Han (2022)：震荡市情绪 mean-reverting |

#### 4.5.2 Schema 定义

```python
# src/got/schemas.py
class CrossNodeTemporalSignal(BaseModel):
    """跨节点时间线共振信号"""
    signal_type: str  # bull_confirmation / sentiment_top / catalyst_confirmation / range_reversal
    confidence: float  # 0-1（各组件置信度的几何平均）
    component_nodes: list[str]  # 参与节点，如 ["node1", "node3"]
    component_evidence: dict  # 各节点的具体证据值
    reasoning: str  # 人读友好的解释
    suggested_action: str  # strong_buy / sell / add / short_term_reversal
```

#### 4.5.3 Node 6 集成

```python
# src/got/nodes/aggregator.py 新增方法
def _detect_cross_node_temporal_signals(
    self,
    market: MarketContext,
    capital: CapitalContext,
    sentiment: SentimentContext,
    catalyst_calendar: Optional[CatalystCalendar],
) -> list[CrossNodeTemporalSignal]:
    """跨节点时间线共振检测。

    在 Node 6 聚合阶段调用，消费各节点的时间线状态：
    - market_regime 扩展字段 dwell_days / transition_count（Node 6 Level 0，当日刚计算）
    - capital.evidence.sector_flow.sector_flow_all_5d / acceleration（Node 3）
    - sentiment.stage_state / persistence_regime（Node 5）
    - catalyst_calendar realizations（Node 4，从 news.catalyst_calendar 取）
    - 本节点自身的 regime_state（Node 6 Level 0 当日计算结果）

    输出写入 UnifiedContext.cross_node_signals，供 Phase 2 消费。
    """
```

#### 4.5.4 UnifiedContext 扩展

```python
class UnifiedContext(BaseModel):
    # ... 现有字段 ...
    cross_node_signals: list[CrossNodeTemporalSignal] = []  # ← 新增
```

#### 4.5.5 共振置信度计算

```python
def _compute_signal_confidence(components: dict) -> float:
    """几何平均各组件置信度。

    要求至少 2 个组件同时确认，否则 confidence < 0.5 → 不触发信号。
    几何平均（而非算术平均）惩罚单组件极端值，要求多节点一致。
    """
    values = [c for c in components.values() if c is not None]
    if len(values) < 2:
        return 0.0
    return math.prod(values) ** (1 / len(values))
```

---

## 5. 分阶段实施

### Phase 1：Node 3 资金面多窗口（P0，2 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 1.1 归档完整性验证脚本（27 天 sector_flow_all 覆盖率 + akshare 3d/5d 排行列名抽查） | `scripts/verify_flow_archive.py` | 0.5h |
| 1.2 `load_capital_flow_series` 序列重建工具（含缺失日跳过/不插值） | `src/got/persistence.py` | 2h |
| 1.3 `_score_sector_flow` 扩展：板块级 3d/5d 映射 + 日均口径加权 | `capital_analysis.py` | 2h |
| 1.4 mainline_bonus 梯度化（强度×持续性，上限 0.15） | `capital_analysis.py` | 1h |
| 1.5 DDE 序列归档回查（不新建表） | `capital_analysis.py` | 1h |
| 1.6 Schema 扩展（sector_flow 新键 + `AmbushCandidate.accum_strength`） | `schemas.py` | 0.5h |
| 1.7 吸筹闸门三档升级（strong/medium/weak/legacy） | `aggregator.py` | 2h |
| 1.8 catalyst_score 档位乘数（1.0/0.8/0.6）+ reasoning 档位输出 | `aggregator.py` | 1h |
| 1.9 单元测试 | `tests/test_node3_multiday.py` | 2h |

**验收标准**：
- [ ] `load_capital_flow_series` 对 27 天归档返回板块级序列，缺失日跳过不插值，连续缺失 ≥5 日返回 None 降级
- [ ] `sector_flow_all_3d/5d` 为板块级映射（非市场级），单位日均（亿）
- [ ] 连续 5 天正向流入的板块 → 吸筹闸门 `accum_strength="strong"`
- [ ] 单日脉冲净流入但前 2 日为负 → `accum_strength="weak"`，catalyst_score ×0.6
- [ ] 归档 < 3 天（冷启动）→ 回退旧逻辑，`accum_strength="legacy"`，不阻塞
- [ ] Node 3 运行时间增幅 < 5%（几乎无额外 HTTP）

### Phase 2：Node 6 Regime 状态机（P1，1.5 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 2.1 MarketRegime 扩展字段 Schema | `schemas.py` | 0.5h |
| 2.2 前日 RegimeState 回读（复用归档扫描模式） | `aggregator.py` | 1h |
| 2.3 Level 0 状态机（转换确认 + dwell + pending） | `aggregator.py` | 2.5h |
| 2.4 transition_count 震荡度（近 20 交易日归档序列统计） | `aggregator.py` | 0.5h |
| 2.5 `_compute_position_from_regime` dwell 感知缩放表（替换非相乘） | `aggregator.py` | 1h |
| 2.6 Reasoning 注入 regime 持续信息 + 矛盾检测低置信标记 | `aggregator.py` | 0.5h |
| 2.7 单元测试 | `tests/test_node6_regime_state.py` | 2h |

**验收标准**：
- [ ] 连续 5 天 bull → `market_regime.dwell_days = 5`
- [ ] 单日 raw_regime 翻转不触发切换（需连续 2 天与 pending 一致才确认）
- [ ] transition_count ≥5 → 仓位 ×0.8，且**不与** Node 1 `regime_scale` 叠加（单测验证最终仓位公式层级）
- [ ] 冷启动（无归档）→ `dwell_days = 1`，不阻塞节点

### Phase 3：Node 5 情绪阶段状态机（P2，1 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 3.1 SentimentStageState Schema 定义 | `schemas.py` | 0.5h |
| 3.2 前日 stage_state 回读（sentiment.json 归档） | `sentiment_analysis.py` | 0.5h |
| 3.3 情绪阶段状态机逻辑 | `sentiment_analysis.py` | 2h |
| 3.4 反向信号规则 | `sentiment_analysis.py` | 1.5h |
| 3.5 情绪持续性检测（AR(1) 系数） | `sentiment_analysis.py` | 1.5h |
| 3.6 SentimentContext 集成 | `sentiment_analysis.py` | 0.5h |
| 3.7 单元测试 | `tests/test_node5_stage.py` | 1.5h |

**验收标准**：
- [ ] 连续升温 3 天 → `contrarian_alert = True`
- [ ] euphoria 持续 > 5 天 → `contrarian_alert = True`
- [ ] AR(1) > 0.7 → `persistence_regime = "high"`
- [ ] 冷启动（首次运行）→ 所有时间线字段中性值

### Phase 4：Node 4 催化剂兑现轨迹（P2，1 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 4.1 `CatalystRealizationRecord` 表扩展 | `storage.py` | 1h |
| 4.2 板块 T+1/3/5 收益计算 | `catalyst_realization.py` | 2h |
| 4.3 PEAD 方向判定 | `catalyst_realization.py` | 1h |
| 4.4 每日兑现检测增强 | `catalyst_realization.py` | 1.5h |
| 4.5 统计报告输出 | `catalyst_realization.py` | 1h |
| 4.6 单元测试 | `tests/test_node4_realization.py` | 1.5h |

**验收标准**：
- [ ] 已过期事件的 day_1/3/5_return 正确计算
- [ ] max_return_window 正确标记
- [ ] pead_direction 与预期方向比对正确
- [ ] 统计报告按 event_type 分组输出

### Phase 5a：小规模试跑（1 天）

code fact（修正 v2.0 的可行性误判）：`data/got_daily/` 27 天归档中 `capital.json`（sector_flow_all 464 板块）、`market.json`（regime/attack_power）、`sentiment.json`（score）齐全，Node 1/3/5 试跑均有数据。

| 任务 | 说明 |
|------|------|
| 5a.1 Node 3 多窗口试跑 | 用 27 天归档对比 1d vs 3d_avg vs 5d_avg 对 T+1/3/5 板块收益的 IC/多空差 |
| 5a.2 Node 6 regime 试跑 | 从 market.json 归档重建 raw_regime 序列，模拟状态机验证 dwell 平滑效果（切换次数降幅） |
| 5a.3 Node 5 反向信号试跑 | 从 sentiment.json 归档模拟 contrarian_alert，统计触发后反转命中率 |
| 5a.4 试点报告 | 输出 `docs/custom/got_timeseries_calibration.md`，**明确标注样本 ≤27 天、不做显著性结论** |
| 5a.5 试跑脚本 | `scripts/backtest_got_timeseries.py`（归档驱动，无需 K 线） |

### Phase 5b：显著性校准（归档 ≥60 交易日后启动，预计 2026-11 下旬）

code fact：27 个样本下 v2.0 的验收标准（IC > 0.05、胜率 > 55%）不可能达到统计显著，且"回测框架 + 网格搜索 + 报告"压 1 天不现实，故拆分。

| 任务 | 说明 |
|------|------|
| 5b.1 参数网格搜索 | `scripts/sensitivity_analysis.py`：regime_confirm_days / AR(1) 阈值 / 多窗口权重 / accum 档位乘数 |
| 5b.2 显著性验收 | IC > 0.05、多空差 > 2%、胜率 > 55%、±1 档鲁棒性（IC 降幅 < 30%） |
| 5b.3 mainline_bonus 上限 0.30 复评 | 依据 5b.2 结果决定是否从 0.15 上调 |

### Phase 6：跨节点共振 + 生产设施（1 天）

| 任务 | 文件 | 工作量 |
|------|------|--------|
| 6.1 CrossNodeTemporalSignal Schema | `schemas.py` | 0.5h |
| 6.2 `_detect_cross_node_temporal_signals` 4 种模式 | `aggregator.py` | 3h |
| 6.3 UnifiedContext.cross_node_signals 集成 | `aggregator.py` | 0.5h |
| 6.4 数据新鲜度验证函数 | `capital_analysis.py` | 1h |
| 6.5 生产监控 JSON（`monitoring.json`） | `persistence.py` | 1.5h |
| 6.6 Rollback 环境变量开关 | 各节点文件 | 0.5h |
| 6.7 Reasoning 注入跨节点信号 | `archive.py` | 0.5h |
| 6.8 单元测试 | `tests/test_cross_node_signals.py` | 2h |

**验收标准**：
- [ ] 模拟数据触发 4 种共振信号各至少 1 次
- [ ] 单组件置信度 → 信号不触发（几何平均 < 0.5）
- [ ] 数据新鲜度验证：陈旧数据被正确标记（场景见 6.3 修正）
- [ ] `monitoring.json` 每日更新，异常规则触发告警
- [ ] `DISABLE_NODE3_MULTIDAY=true` 环境变量回滚到旧逻辑

---

## 6. 风险与边界

### 6.1 风险清单

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| akshare 多日 API 不存在或列名不同 | 🔴 高 | Phase 1 第一步验证脚本，失败则降级方案 |
| 东财反爬：3 次调用触发限流 | 🟡 中 | `em_rate_limiter` 串行 1s 间隔，降级回单日 |
| Regime 状态机过度平滑（延迟响应） | 🟡 中 | 2 天确认窗口 + critical 事件（如跌幅 >5%）强制切换 |
| 情绪 AR(1) 样本量不足（< 20 日） | 🟢 低 | 冷启动标记 `persistence_regime = "unknown"` |
| 催化剂兑现需要板块 K 线（IO 成本） | 🟡 中 | 批量获取 + 缓存到 `sector_history` 表 |

### 6.2 边界约束

- **不替换现有维度**：所有增强是扩展而非重构，向后兼容
- **不增加 LLM 调用**：时间线逻辑全部纯规则
- **降级不阻塞**：任何时间线维度缺失 → 中性值 → 权重归一化
- **与 Node 2 模式一致**：状态机 + dwell days + SQLite 持久化
- **P4 退出信号不变更**：Phase 4 仅预留接口，不实际修改退出规则

### 6.3 数据新鲜度验证（Data Freshness Validation）

防止陈旧数据伪装成当日数据（东财反爬可能导致返回缓存数据）。

```python
# src/got/nodes/capital_analysis.py 新增
def _validate_data_freshness(df: pd.DataFrame, expected_date: str) -> bool:
    """验证 akshare 返回的数据是否为当日。

    检查逻辑：
    1. 如果 df 有 "日期" 列 → 精确匹配
    2. 如果 df 有 "时间" 列 → 取最新时间戳的日期部分
    3. 都没有 → 假设有效（无法验证时不阻塞）

    失败处理：标记 quality.stale_data = True，日志告警，但不阻塞。
    """
```

**触发场景（v2.1 修正，移除不存在的周末场景）**：
- 盘中过早获取（未开盘/延迟开盘日）拿到前日收盘快照（异常，告警）
- 反爬触发后返回缓存数据（异常，告警）
- 注：orchestrator 对非交易日抛 `NonTradingDayError`，"周末运行"场景不存在；v2.0 该行删除

### 6.4 生产监控与漂移检测（Production Monitoring）

时间线逻辑上线后需要持续监控是否正常工作。

**监控文件**：`data/got_timeseries/monitoring.json`

```json
{
  "last_successful_run": "2026-08-27",
  "node1": {
    "regime_dwell_days_history": [1, 2, 3, 4, 5],
    "regime_switch_count_20d": 2,
    "status": "healthy"
  },
  "node3": {
    "flow_5d_non_null_rate_20d": 0.95,
    "mainline_consistency_avg": 2.3,
    "status": "healthy"
  },
  "node5": {
    "contrarian_alert_count_30d": 2,
    "persistence_regime_history": ["high", "high", "low", "low"],
    "status": "healthy"
  },
  "cross_node": {
    "signal_count_30d": {"bull_confirmation": 1, "sentiment_top": 0},
    "status": "healthy"
  },
  "anomalies": []
}
```

**告警规则**：

| 指标 | 阈值 | 含义 |
|------|------|------|
| `node3.flow_5d_non_null_rate_20d < 0.8` | 🔴 | 数据源故障，>20% 天数缺失 |
| `node1.regime_switch_count_20d > 8` | 🟡 | 状态机过于敏感，确认窗口需调大 |
| `node5.contrarian_alert_count_30d > 10` | 🟡 | 反向信号过于频繁，阈值需调整 |
| `cross_node.signal_count_30d 全 0` | 🟡 | 跨节点共振从未触发，检查条件是否过严 |
| `last_successful_run` 距今 > 3 交易日 | 🔴 | 系统运行中断 |

**Node 6 集成**：每日 run() 末尾自动更新 `monitoring.json`，异常时记录到 `anomalies` 列表。

### 6.5 Rollback 策略

如果 Phase 1 上线后发现问题，可快速回滚到旧逻辑（保留数据）。

```powershell
# 回滚开关（PowerShell；bash 下用 export 等价写法）
# 命名与实现对齐（2026-08-27 复审后更新，以代码为准）
$env:DISABLE_NODE3_MULTIDAY="true"            # Node 3 回退到单日模式（闸门回 bool 两值）
$env:DISABLE_REGIME_STATE_MACHINE="true"      # Node 6 回退单日 regime（无 dwell）
$env:DISABLE_SENTIMENT_STAGE_STATE="true"     # Node 5 回退到单日阶段分类（含 AR(1)）
$env:DISABLE_CROSS_NODE_SIGNALS="true"        # 关闭跨节点共振（异常时自动降级为空信号）

python main.py --got  # 使用旧逻辑运行
```

**实现方式**：

```python
# capital_analysis.py 中检查环境变量（开关名与上表一致）
if os.getenv("DISABLE_NODE3_MULTIDAY") == "true":
    return self._score_sector_flow_legacy(data, sector_context)
else:
    return self._score_sector_flow_multiday(data, sector_context)
```

**数据保留**：时间线状态全部落在现有 `got_daily/` 归档中，回滚不影响历史积累，重新启用时无需重建。

### 6.6 向后兼容性

现有 `data/got_daily/` 归档数据（2026-08-27 之前）没有新字段，需明确处理。

**原则**：所有新字段 `Optional[...]  = None`，旧 JSON 加载时自动填充 None。

```python
# schemas.py 中所有新字段示例
class CapitalEvidence(BaseModel):
    sector_flow: Optional[dict] = None
    # 新字段全部 Optional + 默认 None
    # 旧 JSON 没有 persistence 字段 → 加载后 evidence.persistence = None

class MarketRegime(BaseModel):
    # ... 现有字段 ...
    dwell_days: int = 1                    # ← 旧数据无此字段，默认 1（冷启动）
    previous_regime: Optional[str] = None  # v2.0 的 MarketContext.regime_state 方案已撤回
```

**加载旧数据时的行为**：

```python
old_data = load_capital_context("20260820")
assert old_data.evidence.sector_flow.get("sector_flow_all_5d") is None  # 旧数据无多日字段（v2.1 字段名）
# v2.0 示例中的 regime_state 断言已撤回：MarketContext 不新增任何字段（MarketRegime 扩展字段有默认值，旧数据自动兼容）
# Node 6 消费旧数据时：时间线维度中性值，不阻塞
```

**Archive 向前兼容**：新版 `archive_daily_run()` 写入新字段后，旧版代码读取时忽略未知字段（Pydantic 默认行为）。

### 6.7 参数敏感性分析（Parameter Sensitivity）

计划中有多处硬编码参数，需通过网格搜索验证鲁棒性。

**参数网格**：

| 参数 | 候选值 | 对应模块 |
|------|--------|---------|
| `regime_confirm_days` | [1, 2, 3, 5] | Node 6 regime 切换确认窗口（v2.1：Node 1 → Node 6） |
| `transition_count_threshold` | [3, 5, 7] | Node 6 震荡惩罚触发阈值 |
| `sentiment_ar1_high_threshold` | [0.5, 0.6, 0.7, 0.8] | Node 5 高持续性判定 |
| `sentiment_ar1_low_threshold` | [0.2, 0.3, 0.4] | Node 5 低持续性判定 |
| `contrarian_consecutive_days` | [2, 3, 4] | Node 5 反向信号连续天数 |
| `sector_flow_multiday_weights` | [(0.4,0.3,0.3), (0.3,0.3,0.4), (0.5,0.3,0.2)] | Node 3 多窗口权重（日均口径） |
| `accumulation_gate_min_days` | [2, 3, 5] | P3 吸筹闸门连续天数 |
| `accum_strength_multipliers` | [(1.0,0.8,0.6), (1.0,0.9,0.7), (1.0,0.7,0.5)] | 吸筹档位 catalyst_score 乘数（v2.1 新增） |

**回测框架**：

```python
# scripts/sensitivity_analysis.py
"""
参数敏感性分析脚本

方法：
1. 加载 data/got_daily/YYYYMMDD/ 下所有历史数据
2. 对每组参数，重新模拟时间线逻辑
3. 计算 T+1/3/5 板块收益
4. 评估指标：
   - IC（信息系数）：时间线分数与未来收益的 Spearman r
   - 多空收益差：Top 20% vs Bottom 20% 的平均收益差
   - 胜率：多头盈利的比例
   - 最大回撤：信号触发后的最大亏损
5. 输出：docs/custom/parameter_sensitivity.md
   - 每个参数的热力图（参数值 × 评估指标）
   - 推荐参数组合 + 95% 置信区间
   - 鲁棒性排名（结果对参数变化最不敏感的组合）
"""
```

**验收标准（属 Phase 5b 前置，需归档 ≥60 交易日；27 天样本下不做显著性结论，见 Phase 5a/5b 拆分）**：
- [ ] 推荐参数在历史数据上 IC > 0.05
- [ ] 多空收益差 > 2%（经济显著性）
- [ ] 胜率 > 55%（统计显著性）
- [ ] 最优参数 ±1 档变化时，IC 下降 < 30%（鲁棒性）

---

## 7. 文件改动清单（汇总）

### 核心节点代码

| 文件 | 改动类型 | Phase | 预估行数 |
|------|---------|-------|---------|
| `src/got/nodes/capital_analysis.py` | 核心扩展（多窗口 + 新鲜度 + rollback） | 1,6 | +180 |
| `src/got/nodes/market_analysis.py` | **无改动**（v2.0 的 Node 1 方案全部撤回） | — | 0 |
| `src/got/nodes/sentiment_analysis.py` | 核心扩展（阶段状态机 + 反向信号 + AR(1)） | 3 | +120 |
| `src/got/nodes/aggregator.py` | regime 状态机 + 闸门三档 + 档位乘数 + 跨节点共振 | 1,2,6 | +280 |
| `src/got/nodes/portfolio_decision.py` | 消费跨节点信号（预留接口） | 6 | +20 |
| `src/got/catalyst_realization.py` | 兑现轨迹扩展 | 4 | +100 |
| `src/got/archive.py` | Reasoning 注入时间线 + 跨节点信号 | 2-6 | +40 |

### Schema / 持久化 / 存储

| 文件 | 改动类型 | Phase | 预估行数 |
|------|---------|-------|---------|
| `src/got/schemas.py` | Schema 扩展（3/5 节点 + MarketRegime + 跨节点） | 1-6 | +100 |
| `src/got/persistence.py` | 归档回读工具族 + 监控 JSON | 1-3,6 | +100 |
| `src/storage.py` | 催化剂兑现表扩展（DDE 表撤回，归档回查） | 4 | +20 |

### 测试（v2.1 统一迁入 `tests/` pytest 体系）

| 文件 | 改动类型 | Phase | 预估行数 |
|------|---------|-------|---------|
| `scripts/verify_flow_archive.py` | 新建（归档完整性 + akshare 列名抽查） | 1 | +40 |
| `tests/test_node3_multiday.py` | 新建 | 1 | +150 |
| `tests/test_node6_regime_state.py` | 新建 | 2 | +120 |
| `tests/test_node5_stage.py` | 新建 | 3 | +100 |
| `tests/test_node4_realization.py` | 新建 | 4 | +100 |
| `tests/test_cross_node_signals.py` | 新建 | 6 | +150 |

### 回测与分析

| 文件 | 改动类型 | Phase | 预估行数 |
|------|---------|-------|---------|
| `scripts/backtest_got_timeseries.py` | 新建（归档驱动试跑） | 5a | +150 |
| `scripts/sensitivity_analysis.py` | 新建（参数网格搜索） | 5b | +150 |

### 文档

| 文件 | 改动类型 | Phase | 预估行数 |
|------|---------|-------|---------|
| `docs/custom/got_timeseries_calibration.md` | 新建（试点报告） | 5a | ~100 |
| `docs/custom/parameter_sensitivity.md` | 新建（敏感性分析） | 5b | ~100 |

### 数据目录（v2.1 大幅收缩）

| 路径 | 用途 |
|------|------|
| `data/got_timeseries/monitoring.json` | 生产监控状态（唯一新建） |
| `data/got_daily/<date>/capital.json` | 板块逐日资金流序列源（现有，不改结构） |
| `data/got_daily/<date>/unified.json` | RegimeState 持久化（现有归档，扩展字段） |
| `data/got_daily/<date>/sentiment.json` | 情绪阶段状态持久化（现有归档，扩展字段） |
| ~~`data/got_timeseries/node1_regime/`~~ | v2.0 方案撤回（不再新建平行存储） |
| ~~`node3_capital/`、`node5_sentiment/`~~ | 同上，归档回读替代 |

---

**总计**：~1750 行新代码 + 测试 + 文档；Phase 1-4 + 5a + 6 合计 ~6 个工作日，Phase 5b 顺延至归档 ≥60 交易日后。

---

## 8. 参考文献

### Node 3 资金面

1. Nomura (2011). *Factor Dynamics in the China CSI 300 Universe*.
2. Frijns, B., Lai, Q., & Tourani-Rad, A. (2014). *Institutional Trading and Stock Returns: Evidence from China*. RPBFMP, 17(01).
3. Lundstrada, C. T. et al. (2025). *Foreign Capital in the Chinese Stock Market: A Firm-Level Study*. JFQA.
4. Zheng, D., Li, H., & Zhu, X. (2015). *Herding behavior in institutional investors*. J. Multinational Financial Management, 32, 59-76.

### Node 1 大盘

5. Liu, Z. & Wang, S. (2017). *Decoding Chinese stock market returns: Three-state hidden semi-Markov model*. Pacific-Basin Finance Journal, 44(C), 127-149.
6. Chen, J. et al. (2011). *Are Chinese Stock Market Cycles Duration Independent?*. Financial Review, 46(1), 151-164.
7. Chen, J. & Kawaguchi, Y. (2018). *Multi-Factor Asset-Pricing Models under Markov Regime Switches*. IJFS, 6(2), 54.
8. Yu, X. et al. (2016). *Forecasting Bull and Bear Markets: Evidence from China*.
9. Fu, J. & Wu, L. (2021). *Regime-switching herd behavior: Novel evidence from the Chinese A-share market*. Finance Research Letters, 39.

### Node 5 情绪

10. Han, X. et al. (2022). *Persistence of investor sentiment and market mispricing*. The Financial Review.
11. Da, Z. et al. *The Sum of All FEARS: Investor Sentiment and Asset Prices*.
12. Qasim, N. (2025). *State-Dependent Pricing of Investor Sentiment: Evidence from Bull, Bear, and Crisis Regimes*. EcoFin.
13. CNN Fear and Greed Index Paper (2024). *The CNN Fear and Greed Index as a predictor of US equity index returns*.
14. Muguto, T. H. et al. (2025). *The Effects of Investor Sentiment on Stock Return Indices Under Changing Market Conditions*.

### Node 4 催化剂

15. Lan, Q. et al. (2024). *Post earnings announcement drift: A simple earnings surprise measure*. International Review of Financial Analysis, 95.
16. Cai, G. et al. (2021). *The role of institutional investors in post-earnings announcement drift: evidence from China*. Accounting and Business Research, 51(2), 206-236.
17. Chen, Q. & Gao, X. (2021). *Limited Attention and Post-Earnings Announcement Drift: Evidence from China's Stock Market*. IJEFI, 11(1), 1-17.
18. Li, A. & Dempsey, M. (2017). *Is Post Earnings Announcement Drift A Priced Risk Factor in Emerging Markets? Chinese evidence*.

---

## 9. 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| Node 3 增强方式 | 扩展现有维度（非新增维度） | 避免伪独立性（C1 审查问题） |
| Node 3 主数据源（v2.1 新增） | got_daily 归档重建（27 天现成，464 板块逐日）+ akshare 仅交叉校验 | 零额外 IO/反爬；v2.0 依赖的 3日/5日排行累计值无法倒推连续正流入天数 |
| regime 状态机归属（v2.1 修正） | Node 6 Level 0（撤回 v2.0 Node 1 方案） | DAG 分工：Node 6 唯一综合证据消费节点；避免引入第三套 regime 权威；Node 1 实际阈值为 attack_power>0.6 而非 v2.0 误写的 score>0.3 |
| 仓位 regime 系数（v2.1 修正） | 合并进 `_compute_position_from_regime` dwell 缩放表（替换非相乘） | Node 1 已有三层（含 regime_scale）+ Node 6 再缩放，新增相乘层会三重叠加压仓 |
| 加权与 acceleration 口径（v2.1 修正） | 统一日均口径（5d→5d_avg） | v2.0 单日值与窗口累计混加权，量纲错位导致权重失效 |
| DDE 持久化（v2.1 修正） | 归档回查，不新建表 | capital.json 已逐日归档 evidence.dde；撤回 node3_daily_dde |
| mainline_bonus 上限（v2.1 修正） | 维持 0.15 | 避免 Node 3 总分漂移冲击 Node 6 institutional_score 阈值语义；0.30 留待 5b 复评 |
| 条件通过消费（v2.1 新增） | 三档 accum_strength + catalyst_score 乘数 1.0/0.8/0.6 | 消除 v2.0 悬空三值态，弱信号有明确下游惩罚且可审计 |
| 持久化位置（v2.1 修正） | 现有 got_daily 归档回读，仅新建 monitoring.json | 撤回 v2.0 平行存储 data/got_timeseries/{node_key}/，状态随当日 Context 归档自然落盘 |
| Node 5 情绪持续性 | AR(1) 系数 | Han (2022) 验证在 A 股有效；27 天归档刚够 20 日门槛 |
| Node 4 兑现窗口 | T+1/3/5 天 | PEAD 文献标准窗口 |
| 实施优先级 | Node 3 > regime 状态机 > Node 5 > Node 4 > 跨节点 | P0 是已知欠账（代码注释标注），P1 有强学术支撑，P2 增强型，跨节点依赖前 4 个完成 |
| Phase 5 拆分（v2.1 新增） | 5a 试跑（27 天）+ 5b 显著性校准（≥60 交易日后） | 27 样本不支持显著性验收；网格搜索压 1 天不现实 |
| 测试位置（v2.1 修正） | `tests/`（pytest 体系） | 项目已有 192 个测试文件惯例；scripts/ 仅留验证/试跑脚本 |
| 反爬降级 | 单日模式（归档缺失时） | 鲁棒优先，不阻塞节点 |
| 跨节点置信度 | 几何平均 | 惩罚单组件极端值，要求多节点一致 |
| Rollback 策略（v2.1 修正） | 环境变量开关（PowerShell `$env:` 写法；NODE1 变量名更正为 NODE6） | 零代码回滚，数据留存在现有归档中 |
| 向后兼容 | 所有新字段 Optional + 默认 None | 旧 JSON 自动填充 None，不阻塞 |
| 参数选择 | 网格搜索 + 鲁棒性验证 | 避免过拟合单一参数，要求 ±1 档 IC 下降 < 30% |
