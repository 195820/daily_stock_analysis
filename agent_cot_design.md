# A股智能分析系统 GOT 架构设计文档

> **版本**: v1.0（2026-08-11）  
> **基于代码实际实现**（非历史设计稿）  
> **最后更新**: 根据 2026-08-11 代码状态

---

## 目录

1. [架构总览](#1-架构总览)
2. [Phase 1: GOT 六节点](#2-phase-1-got-六节点)
   - [Node 1: 大盘分析](#21-node-1-大盘分析)
   - [Node 2: 板块分析](#22-node-2-板块分析)
   - [Node 3: 资金面分析](#23-node-3-资金面分析)
   - [Node 4: 消息面分析](#24-node-4-消息面分析)
   - [Node 5: 情绪面分析](#25-node-5-情绪面分析)
   - [Node 6: 聚合节点](#26-node-6-聚合节点)
3. [Phase 2: 统一决策节点](#3-phase-2-统一决策节点)
4. [GOT 选股系统](#4-got-选股系统)
5. [数据层架构](#5-数据层架构)
6. [编排器与执行流程](#6-编排器与执行流程)
7. [历史档系统](#7-历史档系统)

---

## 1. 架构总览

### 1.1 核心理念

**GOT (Graph of Thought)**：前半段图结构并行处理，后半段线性流水线决策

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: GOT 六节点（并行 + 串行混合）                       │
│                                                             │
│  独立数据路（并行）        技术分析路（串行）                   │
│  ┌──────────┐ ┌──────────┐   ┌──────────┐ ┌──────────┐ ┌──────────┐
│  │ 消息面   │ │ 情绪面   │   │ 大盘     │→│ 板块     │→│ 资金面   │
│  │ Node 4   │ │ Node 5   │   │ Node 1   │ │ Node 2   │ │ Node 3   │
│  │ 7源采集  │ │ 6维评分  │   │ 4维技术  │ │ 4维百分位│ │ 3维资金  │
│  │ 4次LLM   │ │ 反身性   │   │ 多时间框 │ │ 启动检测 │ │ 降级机制 │
│  └──────────┘ └──────────┘   └──────────┘ └──────────┘ └──────────┘
│        ↓              ↓              ↓            ↓            ↓
│     news_ctx    sentiment_ctx   market_ctx   sector_ctx   capital_ctx
│                                                        ↓
│                                              ┌──────────────────┐
│                                              │   Node 6: 聚合    │
│                                              │  分层 GOT 架构    │
│                                              │  L0: 全局环境     │
│                                              │  L1: 板块mini-GOT │
│                                              │  L2: 组合决策     │
│                                              └──────────────────┘
│                                                        ↓
│                                                unified_context
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: 统一决策节点（FINCON 风格）                         │
│                                                             │
│  Step 1: 风险评估（约束设定）→ 仓位/止损/板块限制              │
│  Step 2: 统一打分（持仓 + 候选股一起评）→ 消除循环依赖        │
│  Step 3: 组合优化（规则驱动）→ 在约束下生成最优配置            │
│  Step 4: 轮动检测 + 拥挤检查 → 吃鱼身 + 高切低策略            │
│  Step 5: LLM 精炼（1次调用）→ 生成可追溯推理总结              │
└─────────────────────────────────────────────────────────────┘
                              ↓
                        PortfolioDecision
```

### 1.2 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **数据源优先级** | mootdx TCP P0 → efinance HTTP P0 → akshare P1 | TCP 不受 HTTP 反爬影响 |
| **评分归一化** | 截面百分位排名（Node 2）| 自适应牛熊市，避免 clip 硬阈值 |
| **矛盾检测** | 规则决策树（Node 6）| 比 LLM 推断更稳定、可解释 |
| **仓位计算** | 连续函数（波动率×状态缩放）| 比离散映射更平滑 |
| **选股消息面** | LLM 板块映射 + 规则股票匹配 | 1次LLM评100板块，非40次LLM评40只股 |
| **框系统** | SQLite 持久化（主线框个体级进出）| 比硬编码更灵活、可回溯 |

---

## 2. Phase 1: GOT 六节点

### 2.1 Node 1: 大盘分析

**职责**：基于纯技术指标规则，判断各风格方向趋势，决定仓位上限

**文件**：`src/got/nodes/market_analysis.py`

#### 2.1.1 指数配置

| 风格组 | 指数代码 | 聚合方式 | 权重 |
|--------|---------|---------|------|
| `tech_growth` | `399006.SZ`(创业板) + `000688.SH`(科创50) | 加权平均 + 一致性折扣 | chinext=0.6, star50=0.4 |
| `mid_small_cap` | `000905.SH`(中证500) | 单指数 | — |
| `large_cap` | `000300.SH`(沪深300) | 单指数 | — |
| `display` | `000001.SH`(上证指数) | 纯展示 | — |

**一致性折扣**：两指数加权平均后，检测方向一致性；若反向（sign 均 >0.1 或均 <-0.1），打 **7 折**

#### 2.1.2 4 维技术指标评分

**实现位置**：`src/got/indicators.py` → `calculate_all_indicators()`

| 维度 | 权重 | 说明 |
|------|------|------|
| `ma_alignment` | 0.29 | 均线排列度 |
| `macd` | 0.29 | MACD 金叉/死叉 + DIF 力度 |
| `volume_price` | 0.24 | 量价配合度 |
| `bollinger` | 0.18 | 布林带位置 |

**⚠️ 重要差异**：设计文档描述 ADX 动态调权，实际代码中 ADX 仅作为元信息透传（`regime_metadata.adx_value`），不参与权重调整

#### 2.1.3 多时间框架 Mini GOT

| 框架 | MA 周期 | 量价 lookback | 用途 |
|------|---------|-------------|------|
| **短期** (short) | `[5, 10, 20]` | 5 日 | 对近期波动敏感 |
| **中期** (medium) | `[5, 10, 20, 60]` | 20 日 | 确认趋势方向 |
| **长期** (long) | `[10, 20, 60, 120, 250]` | 60 日 | 反映大级别趋势 |

所有框架共用完整数据集（250日 K 线），通过 MA 周期和量价 lookback 区分敏感度

**矛盾检测**：`|short_score - long_score| > 0.3` → 标记矛盾

#### 2.1.4 Confidence 三因素加权

```python
confidence = (
    cross_index_consistency * 0.35 +   # 5 指数方向一致性 (0~1)
    tf_conf * 0.35 +                    # 时间框架一致性
    adx_factor * 0.30                   # ADX 趋势强度: min(adx/40, 1.0)
)
# clip(0.05, 1.0)
```

#### 2.1.5 T-0 实时修正

**方法**：`_apply_realtime_adjustment()`

1. 获取腾讯接口 T-0 涨跌幅 → `{"sh": pct, "sz": pct, "cy": pct}`
2. 计算技术面方向：`signal_score > 0 → +1, < 0 → -1`
3. 计算今日方向：`today_avg > 0.5% → +1, < -0.5% → -1, else 0`
4. **反向** → confidence × 0.7
5. **同向** → confidence × 1.1（上限 1.0）

**市场宽度修正**：`_apply_breadth_adjustment()`
- 技术面看空但红盘占优（up > down）→ confidence × 0.7
- 技术面看多但绿盘占优 → confidence × 0.7

#### 2.1.6 仓位上限计算（连续函数）

**两层决策系统** `_calculate_position_limit_v2()`：

```
Layer 1: 波动率调整
  realized_vol = std(log_returns[-20:]) × √252    (年化，最低 5%)
  vol_adjusted = target_vol(15%) / realized_vol    clip(0.5, 1.5)

Layer 2: 状态感知缩放
  regime_scale = tf_factor × 0.40 + cross_factor × 0.30 + direction_factor × 0.30
    tf_factor = timeframe_got.timeframe_confidence
    cross_factor = cross_index_consistency
    direction_factor = clip((unified_score + 1) / 2, 0.3, 1.0)
  regime_scale clip(0.2, 1.0)

最终: position_limit = vol_adjusted × regime_scale    clip(0.2, 1.5)
```

**⚠️ 与设计文档差异**：设计文档是 `base_limit = trend → {1.0/0.5/0.0}` 的离散映射，实际是连续函数

#### 2.1.7 输出 Schema

```python
class MarketContext(BaseModel):
    tech_growth: TechGrowthSignal          # 核心：含 trend/score/signals/timeframe_got/regime_metadata
    mid_small_cap: StyleSignal             # 中小盘
    large_cap: StyleSignal                 # 大盘蓝筹
    position_limit: float                  # 0.0 ~ 1.5
    confidence: float                      # 0.0 ~ 1.0
    key_levels: KeyLevels                  # short_term/mid_term/volume_profile
    reasoning: Optional[str]
    data_quality: DataQuality
    today_change: dict[str, float]         # {"sh": -1.08, "sz": -1.43, "cy": -1.38}
    market_breadth: Optional[dict]         # {up_count, down_count, limit_up_count, limit_down_count}
    realized_volatility: Optional[float]   # 年化波动率
    vol_adjusted_position: Optional[float] # Layer 1 输出
    regime_scale: Optional[float]          # Layer 2 输出 (0.2~1.0)
```

#### 2.1.8 LLM 调用

**1 次调用**：`_generate_llm_reasoning()`
- **用途**：生成 ≤100 字市场研判文本
- **输入**：tech_growth trend/score、时间框架信号、跨指数模式、今日实时、市场宽度
- **参数**：temperature=0.3, max_tokens=200
- **降级策略**：失败 → `_rule_based_reasoning()` 规则拼接

---

### 2.2 Node 2: 板块分析

**职责**：识别板块状态（主线/衰竭/埋伏/防守），判断启动和过热信号，为选股提供方向

**文件**：`src/got/nodes/sector_analysis.py`

#### 2.2.1 板块发现（两阶段动态发现）

**阶段 1**：`stock_all()` 全量获取 → 快筛（关键词匹配）→ Top N

**阶段 2**：注册表保底（`TDX_BOARD_REGISTRY`）

**CONCEPT_UNIVERSE**：14 大类、~50 子类、~200 个关键词

| 大类 | 子类 | 代表关键词 |
|------|------|-----------|
| `ai_chip` | semiconductor_equip, ai_chip, memory_chip, auto_chip, semiconductor_pkg, eda_ip, power_semiconductor, electronic_components | 半导体设备、AI芯片、HBM、EDA、IGBT… |
| `ai_infra` | compute_power, optical_module, liquid_cooling, pcb_connector | 算力、光模块、液冷、PCB… |
| `ai_model` | llm, ai_platform, digital_economy | 大模型、AIGC、数字经济… |
| `ai_terminal` | ai_phone_pc, consumer_elec, apple_chain, new_content | AI手机、消费电子、苹果、短剧… |
| `robot` | humanoid, robot_parts, robot_system | 人形机器人、减速器、伺服… |
| `auto_drive` | smart_drive, new_energy, battery_tech, auto_market | 自动驾驶、固态电池、快充… |
| `emerging` | low_altitude, data_element, huawei_chain, satellite, frontier | 低空经济、华为、卫星、核聚变… |
| `tech_defensive` | innovative_drug, power_grid, energy_storage | 创新药、电力、储能… |
| `finance` | securities, insurance, banking, diversified_finance | 券商、保险、银行… |
| `external` | nvidia | 英伟达 |
| `cyclical` | nonferrous, steel_chem, new_materials | 有色、钢铁化工、新材料… |
| `consumer` | beverage, healthcare_consumer, travel, silver_economy | 白酒、医美、旅游、养老… |
| `military` | aerospace | 军工航天 |

#### 2.2.2 4 维评分（截面百分位排名）

**两阶段流程**：
1. **Pass 1**：`_calculate_raw_metrics()` 计算原始值（未归一化）
2. **Pass 2**：`_apply_cross_sectional_ranking()` 将每个维度的原始值转为**截面百分位排名（0~1）**
3. **加权汇总**：`_calc_strength_score()` → `sum(percentile × weight) × 100` → 0-100 分

| 维度 | 权重 | 原始指标定义 |
|------|------|------------|
| `price_momentum` | **0.30** | 5 日涨幅 (`close[-1]/close[-5] - 1`) |
| `turnover_expansion` | **0.25** | 5d 均值 / 20d 均值（成交额比） |
| `relative_strength` | **0.25** | 20 日涨幅 (`close[-1]/close[-20] - 1`) |
| `breadth_health` | **0.20** | 上涨家数占比 (`up_count / total`) |

**⚠️ 与设计文档差异**：设计文档是 5 维（volume_price/price_momentum/breadth/relative_strength/capital_flow），clip 归一化。实际是 **4 维** percentile rank，无 capital_flow

#### 2.2.3 启动信号检测（降级为诊断指标）

> **Node 2 v3 变更**：启动信号不再驱动状态判定，降级为诊断指标。状态由 RRG 四象限统一驱动（见 §2.2.5）。启动信号的 `quality_score` 仍用于 `_classify_mainline_tier()` 的主线质量判定。

**3 硬门槛（全部通过才继续）**：
1. **突破**：`close[-1] > max(high[-21:-1]) × 1.005`（20日突破 + 0.5% buffer）
2. **放量**：`volume[-1] > volume_ma5 × 1.5`（5日均量 × 1.5）
3. **站上 MA20**：`close[-1] > ma20`

**6 维软打分**：

| 维度 | 权重 | 计算方式 |
|------|------|---------|
| `consolidation` | 0.20 | Bollinger 带宽百分位 → `1.0 - bb_pct/0.5` |
| `volume_expansion` | 0.25 | 量比分段：1.5x→0, 3.0x→1.0, 5.0x→1.0, >5x→衰减 |
| `price_strength` | 0.15 | 20日区间位置 `(close-low20)/(high20-low20)` |
| `trend_alignment` | 0.15 | MA20 斜率×0.6 + MA5/MA20 偏离×0.4 |
| `breadth` | 0.10 | `(up_ratio - 0.4) / 0.3` |
| `ad_confirmation` | 0.15 | A/D Line 确认（`ad_line_confirmation(df)`）|

**Regime-Dependent 阈值（R1）**：

| 市场方向 | strong_launch | weak_launch |
|---------|--------------|-------------|
| 普涨 (calm) | ≥ 0.50 | ≥ 0.35 |
| 分化 (transition) | ≥ 0.75 | ≥ 0.55 |
| 普跌 (turbulent) | ≥ 0.80 | ≥ 0.65 |

#### 2.2.4 过热信号检测（降级为诊断指标）

> **Node 2 v3 变更**：过热信号不再驱动状态判定，降级为诊断指标。过热 severity 仍用于 `_detect_family_crowding()` 的家族拥挤预警。

**前置条件**：`close[-1] >= high_60d × 0.90`（距 60日高点 ≤10%）

| 信号 | 权重 | 计算逻辑 |
|------|------|---------|
| MACD 背离 | **0.30** | DIF 下跌幅度占前高比例 ×0.7 + 死叉 ×0.3 |
| 量能背离 | **0.20** | 当前量/前高量 <0.7 → 渐进分 |
| 宽度极端 | **0.15** | 0.80→0.3, 0.85→0.5, 0.90→0.8, 0.95→1.0 |
| 换手极端 | **0.15** | 超过 P90 → 渐进分 |
| A/D Line 分配 | **0.10** | 布尔值 → 0 或 1 |
| 天量滞涨 | **0.10** | 20日成交额高点的 95% + 近5日均涨>0 + 今日涨幅<均值×30% |

**加权汇总 severity**：
- `> 0.5` → `"exhausted"`（踢出主线框）
- `0.3 ~ 0.5` → `"warning"`
- `< 0.3` → `"normal"`

#### 2.2.5 状态机判定（Node 2 v3.1，2026-08-17 文献校准）

> 替代 P0~P5 优先级链 + 旧版"分数动量埋伏"。基于 17 日运行数据暴露的问题 + 外网论文校准：
> - **状态必须持久**：市场 regime 自转移概率 0.89-0.99（Liu & Xu & Zhao 2010），切换需驻留确认
> - **转移路径固定**：RRG 实测 Leading 92% 转 Weakening（Optuma 2万次旋转）→ **主线只能经 高位降温/衰竭 退出，不存在 主线→埋伏 的边**
> - **埋伏=improving 象限起点**：RS-Momentum 刚上穿、RS-Ratio 低位（不是"高分数残留"）
> - **日频截面过敏感**：RS-Ratio/Momentum 经 3 日 EMA 平滑，吸收单日截面噪音（黄金 08-13 类"一夜蒸发"根除）

**计算方式（`_compute_rrg`，含 3 日 EMA 平滑）**：

| 指标 | 计算 | 含义 |
|------|------|------|
| `RS-Ratio` | `strength_score` 的截面百分位排名 → **3日EMA** | 板块在全市场的相对趋势强度 |
| `RS-Momentum` | RS-Ratio 的 5 日变化斜率（归一化 0-1）→ **3日EMA** | 趋势正在加速还是减速 |

**六状态转移矩阵**（`_apply_state_machine`，核心）：

```
active_mainline ──[转弱未破位: weakening/score回落>15]──► high_position_cooling(新增)
      │  ▲                                                    │              │
      │  │[修复确认: leading/improving+score>55, 驻留≥2天]    │[破位]        │[score<45 连续2天/驻留≥5]
      │  └────────────────────┐                               ▼              ▼
      │                       │                        exhausted_mainline   neutral(熄灭)
      │  ┌──────────────┐     │                              │  ▲
      │  │ [破位: 5日涨幅≤0/跌破MA20/过热+高位<0.6/单日崩≥20]─┴──┤[修复需连续2天确认] ─┐
      └──┴──────────────┴──────────────────────────────────────┘                          ▼
                                                                                     neutral
ambush ──[leading 突破+score>60, 驻留≥1天]──► active_mainline
  ▲      ▲
  └──[低位(<0.55)+动量刚转正+结构吸筹≥0.5（_check_ambush）]◄── neutral
```

**关键规则**：
- **禁止边**：`active→ambush`、`cooling→ambush`、`exhausted→ambush` 一律不存在（RRG 顺时针转移证据）
- **破位/未破位判据**（区分 `high_position_cooling` 与 `exhausted_mainline`）：
  - 破位 BROKEN = 5日涨幅≤0 或 跌破MA20 或 过热exhausted且20日高位<0.60 或 单日分数崩≥20
  - 主判据 `price_chg_5d` 是真实5日涨幅，**不随当日截面排名变**（黄金 08-13 被医药挤压到 50 分但5日涨仍正 → 走冷却而非消失）
- **驻留/置信度门槛**：切换需连续驻留（cooling→active 需2天、exhausted→active 需2天、ambush 持续值≥2天动量转负才出局）；**硬触发豁免**：单日崩≥20分立即降级、单日涨>8%+leading 立即进主线
- **埋伏判据**（`_is_ambush_candidate`，替代旧 `_check_momentum_ambush`）：`ratio<0.55 + 动量≥0.5 且刚转正 + 近3日不跌 + 结构吸筹≥0.5 + score≤72（P2: 45下限砍掉） + 非防守`。高位残留分(黄金 08-17 ratio 0.922/78分)被直接拒绝
- **状态记忆**：`Node2SectorTrendRecord` 新增 `state`/`dwell_days`/`rrg_ratio_ema`/`rrg_momentum_ema` 4 列，替代 unified_signal 三值回映射（修复"主线记忆易断开"）

**学术依据**：
- de Kempenaer 2016: Relative Rotation Graph 四象限系统；StockCharts RRG chart school（周频/日频敏感性）
- Liu & Xu & Zhao 2010: 市场 regime 马尔可夫自转移概率（对冲基金 Sector ETF 研究）
- Ntantamis 2010: Duration-HMM（状态时长随机变量，转移概率依赖驻留时间）
- Procida 2026: HMM 亚洲市场 regime 检测（切换需后验概率置信度门槛）
- Optuma "Buying outperformers is too late": RRG 2 万次旋转转移统计

#### 2.2.6 主线分级 S/A/B

| 标准 | 分值 |
|------|------|
| 国家级政策支持（`_POLICY_SUPPORTED_CHAINS = {"AI硬件", "AI软件", "新能源"}`） | **+3** |
| 家族成员名非"其他" | **+1** |
| 量能扩张（turnover_expansion > 0.6） | **+2** |
| 量能扩张（turnover_expansion > 0.4） | **+1** |
| 连续强势（pm>0.6 且 rs>0.6） | **+1** |

**分级**：S ≥ 5, A ≥ 3, B < 3

#### 2.2.7 生命周期（RRG 象限驱动，Node 2 v3）

> 替代旧的天数驱动系统（`LIFECYCLE_THRESHOLDS`）。旧版用纯天数（launch_end/acceleration_end/mature_end）判定阶段，经常与启动/过热信号矛盾。新版直接由 RRG 象限映射。

**象限 → 阶段映射**：

| RRG 象限 | 生命周期阶段 | 含义 |
|----------|------------|------|
| Leading | `acceleration` | 趋势强+动量强 → 加速上涨 |
| Improving | `launch` | 动量先转强 → 启动初期 |
| Weakening | `mature` | 趋势仍强但动量衰减 → 成熟期（**对应 high_position_cooling 降温态**） |
| Lagging | `late_stage` | 趋势弱+动量弱 → 衰退末期 |

**连续性**：`days_since_launch = prev_days + 1`（继承前日天数，保持阶段连续性）。

**2026-08-17 扩展**：high_position_cooling（高位降温）也计算生命周期（weakening→mature），使降温期在 reasoning 中可见，不再是"标签跳变"。

**已删除**：`LIFECYCLE_THRESHOLDS` 常量、`_inherit_or_calc_days()`、`_calc_days_since_launch()`。

#### 2.2.8 主线家族（层次聚类）

**方法**：`_build_theme_families()`

1. **构建收益矩阵**：90日 K 线收盘价 → 日收益率序列
2. **相关性矩阵**：两两 Pearson 相关系数
3. **层次聚类**：complete linkage（最弱环节），阈值 `r > 0.65`
4. **一致性评分**：板块与同簇成员的平均相关性
5. **家族命名**：用最强板块的真实名称
6. **混合驱动检测**：平均两两相关性 < `0.65 × 0.6 = 0.39` → 标记混合驱动

**回退**：数据不足时用 `FAMILY_KEYWORDS` 硬编码分组

#### 2.2.9 行业/概念双视角分离

**行业板块**：使用 mootdx `881xxx` K 线数据，与概念板块**完全独立的截面百分位排名**（行业板块内部独立排名），取 Top 10 输出到 `SectorContext.industry_top`

**行业板块不做状态分类**（`state="neutral"`），仅提供评分排名

#### 2.2.10 框系统（SQLite 持久化）

**主线框常量**：
- 出框阈值：`score < 50`
- S/A 级容忍天数：**3 天**
- B 级容忍天数：**1 天**
- T-0 硬止损：单日跌 >3% → consecutive_low_days +=2；跌 >1.5% → +=1
- 入框门槛：S/A 级 ≥50，B 级 ≥80

> 埋伏板块不使用框系统。每日 state=ambush 由 `_is_ambush_candidate()` 实时判定（2026-08-17 新判据：
> 截面低位 ratio<0.55 + 动量刚转正 + 近3日不跌 + 结构吸筹≥0.5 + 45≤score≤72 + 非防守），输出截断 Top 10。
> 旧 `_check_momentum_ambush`（"score>阈值即埋伏"，无方向门槛）已废弃——它是黄金 08-17 类"高位回调前主线被误标埋伏"的根因。

**框进出（2026-08-17 校准）**：
- **exhausted_mainline**（真破位）→ 立即踢出主线框，进入 exhausted 输出
- **high_position_cooling**（回调未破位）→ **软化移出主线框**（区别于衰竭），进入 cooling_down 输出，
  避免冷却板块继续占用主线框位置；修复确认（驻留≥2天）后再重新入框
- S/A 级容忍 3 天低分、B 级 1 天；T-0 硬止损不变

**持久化**：SQLite `SectorFrameRecord` 表（仅主线框）；`Node2SectorTrendRecord` 表新增
`state`/`dwell_days`/`rrg_ratio_ema`/`rrg_momentum_ema` 4 列（状态机记忆 + EMA 前值，PRAGMA 幂等迁移）

#### 2.2.11 输出 Schema

```python
class SectorContext(BaseModel):
    active_mainline: list[MainlineGroup]      # 活跃主线（按 frame_group 聚合）
    exhausted_mainline: list[SectorDetail]     # 衰竭主线
    ambush: list[SectorDetail]                 # 埋伏期
    defensive_rotation: list[SectorDetail]     # 防守轮动
    cooling_down: list[SectorDetail]           # 高位降温（state=high_position_cooling，2026-08-17 新增）
    top_by_score: list[SectorDetail]           # 评分最高 N 个
    industry_top: list[SectorDetail]           # 行业板块 Top 10（独立评分）
    theme_families: list[ThemeFamily]          # 主线家族（相关性聚类）
    market_direction: str                      # "分化" / "普涨" / "普跌"
    notes: list[str]                           # 附加说明
    reasoning: Optional[str]
    data_quality: DataQuality
    confidence_level: str                      # "high" / "medium" / "low"
```
> `SectorDetail.state` 六态：`active_mainline / high_position_cooling / exhausted_mainline / ambush / defensive_rotation / neutral`
> `SectorDetail.rrg` 新增 `rs_ratio_raw`/`rs_momentum_raw`（当日原始值，诊断用）

#### 2.2.12 LLM 调用

**1 次调用**：生成 reasoning 文本（纯文本总结，不影响信号逻辑）

---

### 2.3 Node 3: 资金面分析

**职责**：追踪盘中实时资金流向，为其他节点提供辅助验证信号

**文件**：`src/got/nodes/capital_analysis.py`

#### 2.3.1 3 维评分体系

| 维度 | 权重 | 数据源 |
|------|------|--------|
| `sector_flow` | **0.50** | akshare `stock_fund_flow_concept/industry("即时")` |
| `dde` | **0.35** | DdeFetcher (Playwright → push2.eastmoney.com) |
| `northbound_realtime` | **0.15** | akshare `stock_hsgt_fund_min_em` (2024-08后全0自动降级) |

**⚠️ 与设计文档差异巨大**：
- 设计文档 5 维：北向资金(0.25)/龙虎榜(0.25)/ETF申赎(0.25)/融资(0.15)/大宗交易(0.10)
- 实际 **3 维**：板块资金流(0.50)/DDE大单(0.35)/北向资金(0.15)
- 龙虎榜、ETF申赎、融资融券、大宗交易 **均未实现**

#### 2.3.2 维度 1：板块资金流评分

**方法**：`_score_sector_flow()`

- 概念板块 Top 20 净流入均值 → `clip(avg/200亿, -1, 1)` × 权重 0.65
- 行业板块 Top 10 净流入均值 → `clip(avg/100亿, -1, 1)` × 权重 0.35
- 加权合并 → 主线板块在概念 Top 10 中 → **+0.15 加分**

#### 2.3.3 维度 2：DDE 大单净流入评分

**方法**：`_score_dde()`

- 全市场大单净流入合计 → `clip(total_net/1000亿, -1, 1)`

#### 2.3.4 维度 3：北向资金实时评分

**方法**：`_score_northbound_realtime()`

- 累计净流入(万元) → `clip(cumulative/50亿, -1, 1)`
- 最后30分钟趋势 → `clip(recent_30m/10万, -0.2, 0.2)` 作为 bonus
- 最终 = 基础分 + 趋势 bonus

#### 2.3.5 北向资金自动降级

**检测逻辑**：
```python
df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
last_col = df.columns[-1]
non_zero = df[last_col].dropna()
non_zero = non_zero[non_zero != 0]
if non_zero.empty:
    # 全 0 → 返回 None → 自动标记为缺失维度
    return None
```

#### 2.3.6 降级后的权重归一化

```python
# 仅使用可用维度
available_scores = [s1, s2, ...]    # 实际有数据的维度分数
available_weights = [w1, w2, ...]   # 对应权重

total_weight = sum(available_weights)
institutional_score = sum(s * w / total_weight for s, w in zip(scores, weights))
# clip(-1, 1)

confidence_multiplier = len(available_scores) / len(DIMENSION_WEIGHTS)  # = 可用数/3
```

**态度判定**：
- `score > 0.3` → `"bullish"`
- `score < -0.3` → `"bearish"`
- else → `"neutral"`

#### 2.3.7 输出 Schema

```python
class CapitalContext(BaseModel):
    status: str                             # "normal" / "degraded" / "failed"
    institutional_attitude: str             # "bullish" / "bearish" / "neutral"
    institutional_score: float              # -1.0 ~ 1.0
    confidence_multiplier: float            # 0.0 ~ 1.0 (= 可用维度/3)
    evidence: CapitalEvidence               # sector_flow / northbound_realtime / dde
    missing_dimensions: list[str]           # 缺失维度中文名
    reasoning: Optional[str]
    data_quality: DataQuality
```

#### 2.3.8 LLM 调用

**1 次调用**：`_generate_reasoning()`
- **用途**：150 字资金面分析报告
- **输入**：evidence 摘要 + 大盘趋势 + 活跃主线名 + 交易时段感知
- **参数**：temperature=0.3, max_tokens=300
- **降级策略**：失败 → `_fallback_reasoning()` 规则拼接

---

### 2.4 Node 4: 消息面分析

**职责**：采集和分析影响市场的政策、行业、国际新闻

**文件**：`src/got/nodes/news_analysis.py` + `src/got/news_feeds.py`

#### 2.4.1 7 源推送型采集

| # | 源名 | 端点 | 采集方式 | 覆盖窗口 | 反爬 |
|---|------|------|----------|----------|------|
| S1 | 新浪文章流 | `feed.sina.com.cn/api/roll/get` | 4 栏目并发（财经/股票/国内财经/国际财经），每栏 30-50 条 | 21+ 小时 | UA 轮换 |
| S2 | 财联社电报 | `cls.cn/v1/roll/get_roll_list` | 签名认证 + 分页拉取（最多 10 页×20 条=120 条），`MD5(SHA1(sorted_qs))` 签名 | ~1-3 小时 | 专用限流 0.8s+抖动 + UA 轮换 + Session+Retry |
| S3 | 华尔街见闻 | `api-one.wallstcn.com/apiv1/content/lives` | GET `channel=global-channel&limit=30` | 实时窗口 | UA + Referer + Origin |
| S4 | 东财快讯 | `np-listapi.eastmoney.com/comm/web/getNewsByColumns` | `column=350,35,466,467`，`page_size=30` | 实时窗口 | UA |
| S5 | NewsNow 热榜 | `newsnow.busiyi.world/api/s` | 8 平台串行（微博/百度/知乎/澎湃/头条/财联社热/抖音/B站） | 热榜快照 | UA |
| S6 | NGA 论坛 | 复用 `ForumSentimentService.fetch_hot_stocks()` | 热门股关联帖子标题 | 论坛热帖 | Cookie |
| S7 | 同花顺热榜 | `src.services.ths_hot_list.fetch_hot_topics()` | `page_size=30` 话题 | 实时热榜 | 内部封装 |

**核心方法**：`fetch_all_news()` — 串行 7 源（避免并发触发反爬），`merge_news()` 合并

#### 2.4.2 权重排序公式

```
total = source_score × 0.30 + time_score × 0.25 + hot_score × 0.20 + count_score × 0.25
```

- **source_score** (0-28): `_SOURCE_WEIGHTS` 字典 — sina=22, cls=20, wallstreetcn=25, eastmoney=20, newsnow=26, nga_forum=16, ths_hot=28, cctv=28, caixin=24
- **time_score** (5-25): ≤1h→25, ≤4h→20, ≤8h→15, ≤16h→10, >16h→5
- **hot_score**: `_HOT_KEYWORDS` 取单标题最高匹配词分值（~50 词，涨停=20, 制裁=18, 政策=15 等）
- **count_score**: `min(source_count × 10, 30)`

排序后截断至 `MAX_MERGED_NEWS = 150` 条

#### 2.4.3 4 次 LLM 串行调用

| # | 调用点 | Prompt 模板 | Temperature | Max Tokens | 方法/行号 |
|---|--------|------------|-------------|------------|-----------|
| 1 | 事件提取 | `EVENT_EXTRACTION_PROMPT` — 注入 concept_list + historical_context + news_data | 0.2 | 4000 | `_analyze_with_llm` |
| 2 | 概念发现 | `CONCEPT_DISCOVERY_PROMPT` — 注入 current_concepts + historical_context + news_data | 0.5 | 800 | 同上 |
| 3 | 风险总结 | `RISK_SUMMARY_PROMPT` — 仅 news_data | 0.3 | 300 | 同上 |
| 4 | Reasoning | `REASONING_PROMPT` — 注入 overall_impact/score/events/concepts/risks | 0.3 | 400 | `_generate_reasoning` |

**底层调用**：`_call_llm()` — `LLMToolAdapter.call_text()`，含 3 次空响应重试。每次调用通过 `asyncio.to_thread()` 异步化

**降级策略**：
- 风险总结 LLM 失败 → `_parse_risks()` 从负面事件提取
- Reasoning LLM 失败 → `_fallback_reasoning()` 规则拼接
- JSON 解析失败 → `_repair_truncated_json()` 修复截断 JSON

#### 2.4.4 去重逻辑

| 场景 | 阈值 | 方法 |
|------|------|------|
| 原始新闻去重 | `TITLE_SIMILARITY_THRESHOLD = 0.45` | `_deduplicate()` — `SequenceMatcher.ratio()` |
| 跨源确认匹配 | 0.35 | `_apply_source_count()` — 原始新闻→LLM 事件 |
| 持久化去重 | 0.6 | `_save_events_to_db()` — 事件标题 vs 历史事件 |

#### 2.4.5 source_count 计算（两层机制）

1. **新闻级**：每条新闻标题 vs 7 源标题列表做 `SequenceMatcher`，≥0.45 则该源计为匹配，`source_count = len(matched_sources)`
2. **事件级**：LLM 事件提取已合并同事件多源报道，但输出 `source_count=1`。本方法反向匹配：每条原始新闻→最相似 LLM 事件（阈值 0.35），然后 `event.source_count = len(unique_source_types)`

#### 2.4.6 事件持久化（SQLite `node4_events` 生命周期状态机）

**表结构**（`Node4EventRecord`）：
- PK: `event_id` = `MD5(title + date)`
- 字段: title, source, first_seen, last_seen, mention_count, sources_seen(JSON), lifecycle_state, sentiment_trajectory(JSON), category, significance_score

**状态机**：
```
archived: last_seen < today - 7 days
peak:     mention_count ≥ 10 AND source_count ≥ 3 AND days_span ≥ 3
fermenting: mention_count ≥ 3 AND days_span ≥ 2
emerging: first_seen == today AND mention_count == 1
fading:   last_seen < today（其他条件不满足时）
```

**更新逻辑**：
- 匹配已有事件（标题相似度 ≥0.6）→ `mention_count += 1`, 更新 `last_seen`, 追加 `sources_seen`, 追加 `sentiment_trajectory`（保留最近 7 天）
- 新事件 → INSERT，初始 `lifecycle_state = "emerging"`

#### 2.4.7 评分公式

```python
# 过滤: 仅 high/medium 参与评分（low 被丢弃）
for event in scored_events:
    base_weight = IMPACT_WEIGHTS[event.impact_level]  # high=2.0, medium=1.0
    source_mult = 1.5 if event.source_count ≥ 3 else 1.0
    sentiment_val = {positive: 1.0, negative: -1.0, neutral: 0.0}[event.sentiment]
    effective_weight = base_weight × source_mult
    weighted_sum += sentiment_val × effective_weight
    weight_total += effective_weight

score = weighted_sum / weight_total  # clamp [-1, 1]
```

**阈值映射**：
- `score > 0.15` → "positive"
- `score < -0.15` → "negative"
- else → "neutral"

#### 2.4.8 输出 Schema

```python
class NewsContext:
    status: str                        # "normal" / "degraded" / "failed"
    overall_impact: str                # "positive" / "negative" / "neutral"
    impact_score: float                # [-1, 1]
    major_events: list[NewsEvent]      # 已过滤 low-impact
    concept_suggestions: list[dict]    # [{concept_name, evidence, mention_frequency}]
    key_risks: list[str]
    reasoning: Optional[str]
    data_quality: DataQuality
```

**NewsEvent**：
```python
class NewsEvent:
    event_type: str          # "policy" / "industry" / "global"
    title: str
    summary: str
    impact_level: str        # "high" / "medium" / "low"
    sentiment: str           # "positive" / "negative" / "neutral"
    affected_sectors: list[str]
    affected_stocks: list[str]
    timestamp: Optional[str]
    source_count: int        # ≥1
    source_type: str         # "professional" / "retail_forum"
    lifecycle_state: Optional[str]
    mention_count: Optional[int]
    first_seen: Optional[str]
```

---

### 2.5 Node 5: 情绪面分析

**职责**：采集散户情绪数据，量化散户情绪，输出反身性信号

**文件**：`src/got/nodes/sentiment_analysis.py`

#### 2.5.1 6 维置信度加权评分

| 维度 | key | weight | confidence | 数据源 | 归一化方式 |
|------|-----|--------|------------|--------|-----------|
| D1 东财千股千评 | eastmoney | 0.25 | 0.90 | `ak.stock_comment_em()` | `(avg(综合得分) - 50) / 50` |
| D2 市场宽度 | breadth | 0.15 | 0.85 | `DataFetcherManager.get_market_breadth()` | `0.6×breadth_score + 0.4×limit_score` |
| D3 NGA 论坛 | nga | 0.20 | 0.80 | `ForumSentimentService` | 直接 `market_sentiment ∈ [-1,1]` |
| D4 搜索舆情 | news | 0.15 | 0.70 | `SearchService.search_market_news()` | 关键词正负计数（含否定检测） |
| D5 评论详情 | detail | 0.10 | 0.60 | `stock_comment_detail_scrd_desire_em()` | `avg(参与意愿变化) / 30` |
| D6 时序动量 | momentum | 0.15 | 0.80 | 历史 `sentiment.json` 文件 | EMA(5) 偏差 |

**加权公式**：
```python
effective_weight_i = weight_i × confidence_i
score = Σ(score_i × eff_weight_i) / Σ(eff_weight_i)   # 缺失维度自动重分配
```

#### 2.5.2 东财千股千评采集

- **端点**：`akshare.stock_comment_em()` → `datacenter-web.eastmoney.com`
- 返回 ~5190 只股票的 DataFrame，含列: 代码/名称/综合得分/涨跌幅/关注指数/机构参与度
- 实测耗时 ~3s

#### 2.5.3 评论详情采样

- **采样数**：`COMMENT_DETAIL_SAMPLE_N = 15` — 按"关注指数" `nlargest(15)`
- **4 个 detail API**：
  - `ak.stock_comment_detail_scrd_desire_em` — 参与意愿
  - `ak.stock_comment_detail_scrd_focus_em` — 关注度
  - `ak.stock_comment_detail_zlkp_jgcyd_em` — 机构参与度
  - `ak.stock_comment_detail_zhpj_lspf_em` — 综合得分历史
- **超时**：`COMMENT_DETAIL_TIMEOUT = 60s`
- 15 只股并发调用

#### 2.5.4 市场宽度

- **数据源**：`DataFetcherManager.get_market_breadth()` — 支持多源降级
- **派生指标**：`breadth_score = (up_count - down_count) / total`
- **降级**：异常时返回空 dict，该维度权重自动不参与加权

#### 2.5.5 搜索舆情关键词匹配

- **3 条查询**：`"A股 今日 散户 情绪 恐贪"`, `"股市 市场情绪 多空 资金"`, `"股吧 热门讨论 散户"`
- **正面词库** (20 词)：看涨/看多/反弹/突破/利好/放量/强势/大涨/新高/牛市/加仓/爆发/拉升/金叉/多头/量价齐升/超预期/底部放量/主力吸筹
- **负面词库** (20 词)：看空/看跌/暴跌/利空/减持/套牢/割肉/大跌/新低/熊市/崩盘/出逃/恐慌/跳水/砸盘/死叉/量价背离/主力出货/爆雷
- **否定检测**：否定词（不/没/难以/不再/未）出现在情感词前 3 字符内 → 翻转方向
- **评分**：`(pos_count - neg_count) / (pos_count + neg_count)`

#### 2.5.6 反身性 5 阶段分类

```python
# 常量
EUPHORIA_SCORE_THRESHOLD = 0.5
EUPHORIA_ATTENTION_THRESHOLD = 0.75   # 东财关注指数均值 / 80
OPTIMISM_SCORE_THRESHOLD = 0.2
PESSIMISM_SCORE_THRESHOLD = -0.2
CAPITULATION_SCORE_THRESHOLD = -0.5

# 优先级判定
if score > 0.5 AND attention_ratio > 0.75:  → "euphoria", "contrarian_sell", confidence=0.7
if score > 0.2:                               → "optimism", "trend_following", confidence=0.5
if |score| ≤ 0.2:                             → "neutral",  "neutral",        confidence=0.3
if score < -0.5 AND attention_ratio > 0.5:    → "capitulation", "contrarian_buy", confidence=0.7
if score < -0.2:                              → "pessimism", "trend_following", confidence=0.5
```

`attention_ratio = min(avg(关注指数) / 80, 1.0)`

#### 2.5.7 情绪分歧度

```python
dispersion = std(综合得分) / abs(mean(综合得分))   # 变异系数
up_down_gap = mean(涨股综合得分) - mean(跌股综合得分)
contrarian_signal = (dispersion < 0.35) AND (mean_score > 70 OR mean_score < 30)
```

低分歧 + 高分 = 一致看多 → 见顶反向信号；低分歧 + 低分 = 一致看空 → 见底反向信号

#### 2.5.8 时序动量 EMA

```python
# 加载 data/got_daily/{date}/sentiment.json 最近 10 天（倒序）
alpha = 2.0 / (min(len(scores), 5) + 1)   # EMA(5) 的 α
ema = scores[0]   # 最近一天
for s in scores[1:]:
    ema = alpha × s + (1 - alpha) × ema

return current_score - ema   # 正=升温, 负=降温
```

#### 2.5.9 恐贪指数映射

```python
fear_greed_index = (sentiment_score + 1) / 2   # [-1, 1] → [0, 1]
# 0 = 极度恐惧, 1 = 极度贪婪
```

#### 2.5.10 hot_stocks 提取逻辑

**优先级**：
1. **东财优先**（`em_data`）：按"关注指数" `nlargest(50)`，情绪由"综合得分"判定（≥70→positive, ≤30→negative, else neutral），`mention_count = 关注指数 × 1000`
2. **雪球降级**（`xueqiu`）：合并 3 榜（follow/deal/tweet），按 `total_attention` 降序取 top 50，sentiment 固定 "neutral"（雪球无情绪数据）

**雪球采集**：
- `ak.stock_hot_follow_xq("最热门")` / `stock_hot_deal_xq` / `stock_hot_tweet_xq`
- 科技成长过滤：`TECH_GROWTH_KEYWORDS` (~80 关键词)
- 超时：`XUEQIU_TIMEOUT = 90s`

#### 2.5.11 输出 Schema

```python
class SentimentContext:
    status: str                        # "normal" / "degraded" / "failed"
    overall_sentiment: str             # "bullish" / "bearish" / "neutral"
    sentiment_score: float             # [-1, 1]
    sentiment_stage: str               # "euphoria" / "optimism" / "neutral" / "pessimism" / "capitulation"
    trading_signal: str                # "contrarian_sell" / "trend_following" / "neutral" / "contrarian_buy"
    signal_confidence: float           # 0.7 / 0.5 / 0.3
    forum_mood: str                    # "极度乐观" / "乐观" / "偏乐观" / "中性" / "偏悲观" / "悲观" / "极度悲观"
    hot_stocks: list[HotStock]
    hot_topics: list[str]
    retail_attention_count: int        # 东财关注指数总量
    market_temperature: float          # [0, 1] 冰点→沸点
    news_drift: str                    # "positive" / "negative" / "neutral"
    reasoning: Optional[str]
    data_quality: DataQuality
    fear_greed_index: float            # [0, 1]
    sentiment_dispersion: float        # 变异系数
    contrarian_signal: bool            # 一致看多/空反向信号
    sentiment_momentum: float          # EMA(5) 偏差
```

**HotStock**：
```python
class HotStock:
    stock_code: str
    stock_name: str
    mention_count: int
    sentiment: str             # "positive" / "negative" / "neutral"
    associated_sectors: list[str]
```

#### 2.5.12 LLM 调用

**1 次调用**：reasoning + hot_topics 生成（JSON 输出，失败时规则降级）

---

### 2.6 Node 6: 聚合节点（v3 分层 GOT 架构）

**职责**：聚合 5 个节点的输出，检测矛盾，生成统一的交易信号和仓位建议

**文件**：`src/got/nodes/aggregator.py`

**设计理念**：
- 全局环境感知（Level 0）：使用 5 个节点的全局聚合字段判断市场 regime
- 板块级情报整合（Level 1）：对每个候选板块做 4 维 mini GOT 交叉验证
- 组合决策（Level 2）：基于 regime + confirmed_sectors 计算仓位和信号

**参考论文**：
- ICLR 2025: Top-Down Sector Allocation（板块配置即决策）
- FINCON NeurIPS 2024: Manager-Analyst 层级（Analyst 输出 insight 而非 score）
- FinCoT ACL 2025: 推理结构匹配领域工作流

#### 2.6.1 Level 0: 全局环境感知

**方法**：`_compute_market_regime()`

```python
regime_score = 0.0

# Node 1: ADX + 趋势方向
if adx_value > 25:  # 有趋势
    regime_score += 0.3 if tech_growth.score > 0.3 else -0.3
else:  # 无趋势
    regime_score += tech_growth.score × 0.2

# Node 2: 板块方向
regime_score += {"普涨": 0.3, "普跌": -0.3, "分化": 0.0}[market_direction]

# Node 3: 资金面
regime_score += institutional_score × 0.2

# Node 4: 消息面
regime_score += news_impact_score × 0.1

# Node 5: 情绪面
regime_score += 0.1 if fear_greed > 0.7 else -0.1 if fear_greed < 0.3
# sentiment_stage 修正
regime_score += {"euphoria": -0.15, "capitulation": +0.15,
                 "optimism": +0.05, "pessimism": -0.05}[stage]
regime_score += sentiment_momentum × 0.1

# 分类
regime = "bull" if score > 0.3 else "bear" if score < -0.3 else "transition"

# 风险预算
if bull:    risk_budget = 0.6 + 0.2 × (score - 0.3) / 0.7
if bear:    risk_budget = 0.3 + 0.2 × (score + 1.0) / 0.7
if transition: risk_budget = 0.4 + 0.2 × (score + 0.3) / 0.6
risk_budget = clip(risk_budget, 0.2, 0.8)
```

#### 2.6.2 Level 1: 板块 mini GOT

**方法**：`_build_sector_intel()` → `_validate_single_sector()`

对每个候选板块做 4 维交叉验证：

```python
composite = tech_score  # Node 2 的 strength_score (0-100)
composite += 10 if capital_check == "aligned" else -15 if "divergent"
composite += 8 if news_check == "catalyzed" else -12 if "negative"
composite += 5 if sentiment_check == "high" else -5 if "low" and role != "ambush"
# P3: 埋伏板块不惩罚低热度（低关注度是埋伏的利润来源）
# Lifecycle 修正
composite += 8 if phase in ("launch", "acceleration")
composite -= 15 if phase in ("late_stage", "mature_exhausted")
composite = clip(composite, 0, 100)
```

**分类门槛**：

| 分类 | 主线条件 | 埋伏条件 |
|------|---------|---------|
| confirmed | score≥75 + capital∈{aligned,neutral} + news≠negative; 或 score≥60 + capital=aligned | score≥75 + capital=aligned + news≠negative; 或 score≥60 + capital=aligned |
| watch | score≥50 + 无负面信号 | 同 |
| divergent | score≥50 + capital=divergent 或 news=negative | 同 |
| speculative | score<40 + news=catalyzed | 同 |
| avoid | score<40 + 无消息催化; 或晚期+negative信号 | 同 |

#### 2.6.2b Level 1 平行分支: 埋伏通道（P3）

**方法**：`_build_ambush_channel()`

> 在 mini-GOT Level 1 内与主线 composite **平行的独立分支**。
> 4 维交叉验证照做但重新分工，不再合成 composite。

**输入**：`sector.ambush`（Node 2 技术闸门）+ `capital`（吸筹确认）+ `news.catalyst_calendar`（催化剂）+ `sentiment`（拥挤度）

**3 层流水线**：

| 层 | 方法 | 逻辑 |
|----|------|------|
| 闸门层 | `_check_negative_catalyst()` | ★★★ 利空 → 直接否决 |
| 闸门层 | `_check_accumulation_gate()` | 当日正向净流入 → 通过 |
| 闸门层 | `_check_anti_top_buy()` | 注意力闸（3+ 只个股上热榜 → 否决）+ 已发酵检查（催化剂 ≥5 天前出现 → 行情可能已走完） |
| 闸门层 | `_check_crowded_blacklist()` | 板块名精确匹配热榜 → 否决（来晚了） |
| 排序层 | `_compute_catalyst_score()` | B 公式：主催化分 + 冗余加成 |

**B 公式**：
```python
main_score = IMPACT_WEIGHT[level] × proximity × confidence
# proximity: 3~10 交易日 = 1.0; 1~2 交易日 = 0.6（anticipation 大半走完）
# IMPACT_WEIGHT: ★★★=1.0, ★★=0.6, ★=0.3

redundancy = min((n-1) × 0.2, 0.4) × main_score  # 第2+个催化剂，封顶+40%
total = main_score + redundancy
```

**无催化分支**：闸门全过但窗口内无合格催化剂 → 蓄水池（`AmbushReservoirEntry`）

**输出**：
- `ambush_pool: list[AmbushCandidate]`（正式池，≤10，宁缺毋滥）
- `ambush_reservoir: list[AmbushReservoirEntry]`（蓄水池，4 周自动除名）
- `ambush_veto_log: list[str]`（否决记录）

**文献依据**：
- Kargarzadeh et al. 2026：事件前和当天价格移动最大，"等确认再进 = 买在顶上"
- 每类事件有故事寿命（M&A 12.9 天、财报 2.3 天、产品发布 0.9 天）→ 退出按类型分叉

#### 2.6.2c Phase 2 埋伏仓位与退出（P4）

**方法**：`_ambush_exit_signal()` + `_write_ambush_buy_snapshots()` + `_close_ambush_snapshots()`

**类型化仓位系数**（在 `_assess_risk()` 的 `sector_limits` 公式中追加）：

```python
sector_limit = base × regime_m × role_m × conf_w × catalyst_coeff
# catalyst_coeff: drift=1.0 / fade=0.7 / binary=0.4
# 文献依据：Yee 2025（二元事件风险调整收益在事件后上升段达峰，限仓压缩）
```

| 催化剂类型 | 系数 | 理由 |
|-----------|------|------|
| drift（量化基本面） | ×1.0 | 持有吃事件后漂移，给得大 |
| fade（软叙事） | ×0.7 | 事件日即退，持有期短 |
| binary（高不确定） | ×0.4 | 二元事件不要赌，赌则限仓 |

**买入催化剂快照**（Phase 2 建仓时写入 `ambush_position_snapshots` 表）：
- 记录：symbol / sector / category_tag / catalyst_event_id / event_date / impact_level / entry_price
- 退出后：exit_signal / exit_date / exit_price / is_open=False
- 与催化归档表共表设计（买入快照 + 兑现结果 = 完整复盘记录）

**埋伏退出分支**（扩展 `_score_holdings()`，`_lifecycle_sell_signal` 之外平行检测）：

| 情形 | 触发条件 | exit_signal |
|------|---------|-------------|
| fade 型催化剂 | `today > event_date`（事件日次日开盘卖） | `fade_sell` |
| drift 漂移衰竭 | 事件后 ≥10 交易日 + 持有涨幅 <2% | `drift_exhausted` |
| drift 破 MA20 | `current_price < MA20` | `drift_break_ma20` |
| drift 硬超时 | 持有 >20 交易日 | `drift_timeout` |
| 事件落空（binary） | 事件日 >2 交易日仍未发生 | `event_cancelled` |
| 硬止损 | `P&L < -7%`（任意类型） | `stop_loss` |
| DDE 毕业 | 板块 DDE 爆发（TODO：待板块级检测） | `dde_graduation` |

**配置参数**（集中常量，归档积累后校准）：
```python
AMBUSH_DRIFT_STALL_DAYS = 10          # 漂移衰竭观察窗口
AMBUSH_DRIFT_TIMEOUT_DAYS = 20        # 硬超时（交易日）
AMBUSH_DRIFT_STALL_THRESHOLD = 0.02   # 漂移衰竭涨幅阈值
AMBUSH_BINARY_GRACE_DAYS = 2          # 二元事件 grace period
```

#### 2.6.2d Phase 2 催化剂关联度重排（P5）

**目的**：在选股漏斗中注入前瞻维度——催化剂日历告诉选股流水线"未来 2 周哪只股票有事件驱动"。

**集成点**：`_filter_got_finalists()` 轮次 4（风险排雷）之后、轮次 5（板块分散）之前。

**评分逻辑**（`_score_catalyst_relevance()`）：

| 事件级别 | 匹配方式 | 分数 |
|---------|---------|------|
| 个股级（earnings/lockup）| title 正则提取股票代码 → 当事股 | 100（直接置顶）|
| 板块级（narrative/global/macro）| sector 字段匹配 | confidence × impact × proximity × direction_coeff × 50 |
| 无匹配 | — | 0 |

**重排规则**：
- 仅对 ambush 板块候选生效（主线保持技术排序）
- 最大 confidence < 0.7 → 不重排（保持技术排序）
- 关联度只做重排不做准入（防"概念正宗但没人买"）

**临近度系数**：`proximity = max(0.3, 1.0 - days_diff / 30.0)`

**方向修正**：positive → ×1.0, negative → ×0.5（利空不置顶）

#### 2.6.2e 催化归档表（P6）

**目的**：每个催化剂事件兑现后记录预期 vs 实际，作为所有可配参数的 A 股本地化校准源。

**四大用途**：
1. 参数校准源（0.7 阈值/10日衰竭/4周除名等）
2. 反向查询通道存废依据
3. 矛盾否决准确率统计
4. 事件类型故事寿命本地重估

**`CatalystRealizationRecord` 表**：

| 字段组 | 字段 | 用途 |
|--------|------|------|
| 事件标识 | event_id(UK), event_type, source, source_type | forward/reverse_discovery/veto/reservoir |
| 事件信息 | sector, title, event_date, category_tag, impact_level | 与 CatalystEvent 对齐 |
| 预期 | consensus_expectation, direction, confidence | 买入前的判断 |
| 兑现结果 | is_realized, realization_date, actual_return, drift_direction_correct | 事后验证 |
| 故事寿命 | story_start/peak/end_date, story_lifetime_days | 持有窗口分型 |

**`CatalystRealizationTracker` 类**：
- `register_event(event, source_type)` — 日历构建时注册（`_save_events()` 末尾自动调用）
- `check_and_record_realization(target_date)` — 每日运行，检测过期事件并计算板块收益（orchestrator `run()` 末尾调用）
- `get_realization_stats(days)` — 按 event_type/source_type/category_tag 分组统计兑现率
- `get_story_lifetimes(days)` — 各类事件的故事寿命分布

#### 2.6.3 Level 2: 组合决策

**方法**：`_compute_position_from_regime()` + `_determine_unified_signal()`

**仓位计算**：
```python
confirmed_count = len([s for s in confirmed if classification == "confirmed"])
avg_confidence = mean([s.confidence for s in confirmed])

if bear:       position = 0.2 + 0.1 × confirmed_count
if transition: position = 0.3 + 0.1 × confirmed_count + 0.1 × avg_confidence
if bull:       position = 0.4 + 0.08 × confirmed_count + 0.1 × avg_confidence

position = min(position, risk_budget)
final = clip(position, 0.0, 0.8)
```

**⚠️ 日限幅已移除**：代码注释说明"三层仓位系统（波动率调整 + 状态缩放 + 回撤保护）已提供平滑过渡。旧版日限幅 bug：当 market.position_limit=0.0 时，clip 到 [-0.2, 0.2] 导致锁死 20%"

#### 2.6.4 矛盾检测（规则决策树）

**方法**：`_detect_contradictions()`

**不是硬编码 10 对**，而是 **2 种自动检测规则**：

**矛盾 1**：`regime_vs_sectors` — bear regime + ≥3 confirmed 板块
```python
if regime == "bear" and confirmed_count >= 3:
    → Contradiction(severity="medium", conflict_type="opinion")
```

**矛盾 2**：`_detect_family_capital_mismatch()` — 主线家族 vs 资金流向错位
- **高阈值模式**：同时满足 ① Node 2 top-1 家族在资金 top-5 无匹配 AND ② 资金 top-1 家族在 Node 2 排名 ≥ top-3
- 错位 → `severity="high"`, `conflict_type="factual"`
- 排名差 ≥3 → `severity="medium"`, `conflict_type="opinion"`

#### 2.6.5 LLM 精炼（板块情报）

**方法**：`_llm_refine_sectors()`

**触发条件**：总是调用（只要有板块数据），非条件触发

**Prompt**：注入 regime + 板块情报前 10 个，要求输出：
```json
{
  "narrative": "50字板块格局总结",
  "cross_sector_risks": ["风险1", "风险2"],
  "adjustments": [{"sector": "名", "new_classification": "...", "reason": "..."}]
}
```

**安全约束**：LLM 调整**仅允许上调**（avoid→watch→confirmed），**禁止下调**

**LLM 调用**：`_call_llm_async()`, temperature=0.3, max_tokens=500, 3 次重试

#### 2.6.6 消息面覆盖（条件 LLM）

**方法**：`_simplified_news_override()`

**筛选条件**：
```python
overridable = [e for e in news.major_events
    if e.event_type == "policy"
    and e.impact_level == "high"
    and e.source_count >= 3
    and any(kw in e.title or kw in e.summary for kw in SYSTEMIC_KEYWORDS)]
```

**SYSTEMIC_KEYWORDS**：`降息/降准/加息/IPO/注册制/退市/禁止/禁令/监管/制裁/关税/财政刺激/国债/印花税/暂停/熔断/救市/护盘`

**LLM 决策**：提出新仓位 → clamp 到 `[position_limit - 0.2, position_limit + 0.2]`

#### 2.6.7 时间一致性校验

**方法**：`_adaptive_temporal_check()`

```python
regime_score_map = {"bull": 0.5, "transition": 0.0, "bear": -0.5}
current_score = regime_score_map[regime]
z_score = |current_score - prev_ema_score| / max(prev_std, 0.05)

# 冷启动期（sample_count < 5）
if z_score > 5.0: → critical "sudden_trend_flip"
if z_score > 3.5: → high "trend_flip"

# 稳定期
if z_score > 3.0: → critical
if z_score > 2.0: → high
```

**动作**：critical → `status = "manual_review_required"`

#### 2.6.8 EMA 基线持久化

**存储**：`data/aggregator_contexts/ema_baseline.json`

**结构**：
```json
{
  "regime_score": 0.123,
  "score_std": 0.045,
  "last_updated": "2026-08-11",
  "sample_count": 12
}
```

**更新公式**：
```python
EMA_ALPHA = 0.3
new_score = 0.3 × current_score + 0.7 × prev_score
# 滚动方差: Welford 在线算法
delta = current_score - prev_score
new_ss = (n-1) × prev_std² + delta² × n/(n+1)
new_std = max(sqrt(new_ss / n), 0.01)
```

**冷启动**：`EMA_WARMUP_SAMPLES = 5`，样本不足时用宽松阈值（critical=5.0, high=3.5），充足后用严格阈值（critical=3.0, high=2.0）

#### 2.6.9 输出 Schema

```python
class UnifiedContext:
    status: str                           # "normal" / "manual_review_required"
    unified_signal: str                   # "bullish" / "bearish" / "neutral" / "cautious"
    overall_confidence: float             # [0, 1]
    final_position_limit: float           # [0, 1]
    contradictions: list[Contradiction]
    common_root_cause: Optional[str]
    news_override: Optional[NewsOverride]
    temporal_inconsistencies: list[TemporalInconsistency]
    key_risks: list[str]
    key_opportunities: list[str]
    concept_suggestions: list[dict]
    reasoning: Optional[str]
    data_quality: DataQuality
    market_regime: Optional[MarketRegime]     # Level 0 输出
    llm_refinement_used: bool
    ema_baseline: Optional[float]
    confirmed_sectors: list[SectorIntel]      # Level 1 板块情报
```

**MarketRegime**：
```python
class MarketRegime:
    regime: str                  # "bull" / "bear" / "transition"
    risk_budget: float           # [0.2, 0.8]
    adx_value: Optional[float]
    cross_index_consistency: float
    market_direction: str        # "普涨" / "分化" / "普跌"
    mainline_count: int
    institutional_score: float
    news_impact_score: float
    fear_greed: float
    sentiment_stage: str
    sentiment_momentum: float
    macro_overlay: str
```

**SectorIntel**：
```python
class SectorIntel:
    sector_name: str
    sector_code: str
    sector_type: str              # "concept" / "industry"
    sector_role: str              # "mainline" / "ambush" / "independent"
    mainline_tier: Optional[str]  # S / A / B
    mainline_quality: Optional[str]
    mainline_days: Optional[int]
    tech_score: float             # [0, 100]
    tech_detail: str
    capital_check: str            # "aligned" / "neutral" / "divergent"
    capital_detail: str
    news_check: str               # "catalyzed" / "neutral" / "negative"
    news_detail: str
    sentiment_check: str          # "high" / "medium" / "low"
    sentiment_detail: str
    lifecycle: Optional[LifecycleInfo]
    composite_score: float        # [0, 100]
    confidence: float             # [0, 1]
    classification: str           # "confirmed" / "divergent" / "watch" / "avoid" / "speculative"
    reasoning: str
```

#### 2.6.10 LLM 调用汇总

| 调用点 | 触发条件 | 用途 | 降级策略 |
|--------|---------|------|---------|
| `_llm_refine_sectors()` | 总是 | 板块情报精炼 | 保持原始分类 |
| `_simplified_news_override()` | 条件（policy+high+source_count≥3+SYSTEMIC_KEYWORDS） | 消息面覆盖 | 返回 None |

---

## 3. Phase 2: 统一决策节点

**职责**：基于 Phase 1 输出，生成交易建议（买/卖/持有 + 仓位 + 止损）

**文件**：`src/got/nodes/portfolio_decision.py`

**架构评审**（2026-08-03）：
- 原串行设计（持仓判断 → 选股 → 建议）存在循环依赖
- 改为统一决策节点（3 Step 封装在 1 个节点，1 次 LLM 调用）
- 学术依据：FINCON (NeurIPS 2024), RAPTOR (NeurIPS 2025)

### 3.1 5 步流水线

```
Step 1: 风险评估 (L146-228) → 约束设定
Step 2: 统一打分 (L315-520, L1206-1336) → 持仓+候选股一起评
Step 3: 组合优化 (L1340-1514) → 规则驱动仓位分配
Step 4: 轮动检测 + 拥挤检查 (L109-112) → 吃鱼身+高切低策略
Step 5: LLM 精炼 (L1518-1611) → 生成可追溯推理总结
```

### 3.2 Step 1: 风险评估

**方法**：`_assess_risk()`

**输出约束**：
- **position_limit**：从 `unified.final_position_limit` 提取，经回撤缩放调整
- **stop_loss_threshold**：基于 market_regime 动态调整
  - bear → -5%, transition → -7%, bull → -10%
- **sector_exposure_limits**：动态计算（Shen 2026 + Bayesian Kelly + Conviction-Tier）
  - 公式：`base × regime乘数 × role乘数 × confidence`
  - 效果：bull+主线+高确信 ≈ 36%，bear+埋伏 ≈ 5%
- **risk_budget**：从 `unified.market_regime.risk_budget` 映射 [0.03, 0.08]

**回撤缩放逻辑**：
```python
0-5% 回撤: multiplier = 1.0
5-10% 回撤: 线性减到 75%
10-15% 回撤: 75% → 50%
>15% 回撤: 固定 50%
最低保留 20% 仓位
```

### 3.3 Step 2: 统一打分

**方法**：`_score_holdings()` + `_score_candidates()`

**持仓评分**：
- 五维打分：资金面(30%) + 消息面(20%) + 板块强度(20%) + 技术面(15%) + 风险面(15%)
- 资金面/消息面：复用 `stock_screener._score_capital()` 和 `_score_news()`
- 板块强度：从 `sector_context` 获取，利用 Node 6 v3 的 4 维验证调整
- 技术面：优先使用 `TechnicalIndicatorService` 真实指标，降级为 P&L 代理
- 动作建议：生命周期驱动（launch/acceleration=鱼身持有，mature_exhausted=清仓）

**候选股评分**：
- 调用 `stock_screener._extract_got_universe()` 提取候选池
- 调用 `stock_screener._filter_got_finalists()` 执行 5 轮漏斗筛选
- 重新计算综合评分（用板块级别评分替代个股级别评分）
- 使用生命周期优先级排序（而非单纯按 composite_score）

**板块情报调整**：
```python
classification: confirmed +10(主线)/+5(埋伏), avoid -20
capital_check: aligned +5, divergent -10
news_check: catalyzed +5, negative -10
sentiment_check: high +3, low -5
```

### 3.4 Step 3: 组合优化

**方法**：`_allocate()`

**流程**：
1. **生成卖出建议**：根据 `action_recommendation` 决定 sell/reduce/hold
2. **计算买入预算**：
   - 可用资金 = 现有现金 + 卖出释放的资金
   - 高切低策略：即使仓位已达标，也用卖出释放资金的 80% 买入新股
3. **生成买入建议**：
   - 最多买 3 只
   - 差异化仓位：主线仓标准止损(-7%)，埋伏仓缩小 45% + 更紧止损(-5%)

### 3.5 Step 4: 轮动检测 + 拥挤检查

**方法**：`_detect_rotation_opportunity()` + `_check_family_crowding()`

**轮动时机**： sentiment_momentum 由负转正 + 板块处于 launch/acceleration 阶段

**拥挤检查**：同一主线家族内持仓过多 → 警告

### 3.6 Step 5: LLM 精炼

**方法**：`_llm_refinement()`

**次数**：1 次（仅用于精炼总结）

**Prompt**：包含风险评估、持仓盈亏、Top 5 评分、初步分配建议

**输出**：300 字以内的推理总结（整体决策理由 + 逐只持仓分析）

### 3.7 输出 Schema

```python
class PortfolioDecision:
    status: str                           # "normal"
    risk_assessment: PortfolioRiskAssessment
    unified_scoring: list[UnifiedScoringResult]  # 持仓+候选股统一评分
    allocation: list[TradeAllocation]     # 交易建议
    summary: str                          # 操作总结
    warnings: list[str]                   # 风险提示
    reasoning: str                        # LLM 推理总结
    rotation_signals: list[dict]          # 轮动建议
    crowding_warnings: list[dict]         # 家族拥挤警告
```

---

## 4. GOT 选股系统

**职责**：基于 Phase 1 GOT 输出，从主线/埋伏板块成分股中精选 Top 2-3 只，用于波段操作（持仓周期约 2 周）

**文件**：`src/services/stock_screener.py`

### 4.1 run_got_mode() 完整流程

```
1. 加载 GOT 上下文 (L909-949)
2. 提取主线/埋伏板块成分股 (L951-1047)
3. 执行 5 轮漏斗筛选 (L1166-1258)
4. 构建输出 + 保存结果 (L894-897)
```

### 4.2 _load_got_context()

**加载文件**：
- 优先：`data/got_daily/YYYYMMDD/` 目录下的 6 个节点 JSON
  - market.json, sector.json, capital.json, news.json, sentiment.json, unified.json
- 降级：`data/aggregator_contexts/previous_context_YYYYMMDD.json`（仅 unified）

### 4.3 _extract_got_universe()

**提取逻辑**：
1. 识别"兜底填充"板块并排除
2. 展开 MainlineGroup → 成员板块
3. 从达标的主线/埋伏板块获取成分股
4. 去重（同一股票可能在多个板块）

### 4.4 _get_sector_constituents_filtered() DDE 策略

**主线板块**（active_mainline）：
```sql
1个交易日前的dde大单净量>=0.8%，近2个交易日的dde大单净量>=0.8%
```

**埋伏板块**（ambush）：
```sql
近5个交易日的dde大单净量合计>0
```

**行业过滤**：
- 概念→行业关键词映射（防止蹭概念的票）
- 例："数据要素" → ["计算机", "软件", "数据处理", ...]

### 4.5 5 轮漏斗筛选

```
轮次 1: 信号过滤 → 排除衰竭板块
轮次 2: 资金面评分 Top 20 → DDE + 板块资金流
轮次 3: 波段技术确认 Top 15 → MA20/布林带/RSI/MACD
轮次 4: 风险排雷 Top 8 → RSI>80(-30), 5日涨幅>20%(-25), 波动率>0.05(-15)
轮次 5: 板块分散 Top N → 每个板块最多 1 只
```

### 4.6 _score_capital() 实际维度和权重

**维度 1**：DDE 大单净量百分位 (0-50分)

**维度 2**：板块资金流百分位 (0-50分)

**总计**：0-100分

### 4.7 _score_news() 实际逻辑

**流程**：
1. **LLM 语义匹配**：
   - 将 Node 4 事件 + CONCEPT_UNIVERSE 板块名列表交给 LLM
   - LLM 输出：每个受影响板块的 sentiment × relevance
   - 同一板块多事件取绝对值最大的
2. **降级方案**：子串匹配（用 CONCEPT_UNIVERSE 展开）
3. **评分**：基础分 40 + 板块情感分 × 60

**⚠️ 重要澄清**：不是"每只候选股 1 次 LLM"，而是"1 次 LLM 评 100 板块 + 字典查找"

### 4.8 _score_technical_wave() 波段指标

**8 个维度**：
1. **MA20 趋势** (20分)：偏离度 12 + 斜率 8
2. **布林带位置** (15分)：最优区间 0.5~0.85
3. **RSI12** (10分)：50-70 最优
4. **20日突破** (10分)：突破程度连续
5. **量比** (10分)：1.5-3.0 最优
6. **换手率** (10分)：3%-8% 最优
7. **MACD 状态** (10分)：金叉/死叉/方向 + DIF 力度
8. **均线排列** (15分)：MA 间距连续

### 4.9 _score_risk() 排雷逻辑

**扣分项**：
- RSI > 80（超买）：-30分
- 近 5 日涨幅 > 20%：-25分
- 波动率 > 0.05：-15分

### 4.10 _compute_got_composite_score() 权重分配

```python
composite = (
    capital * 0.30 +      # 资金面
    news * 0.20 +         # 消息面
    sector_score * 0.20 + # 板块强度
    tech_score * 0.15 +   # 技术面(波段)
    risk_score * 0.15     # 风险因子(反向)
)
```

### 4.11 输出格式和存储位置

**输出**：`ScreenerRunResult` 包含 `List[ScreenerStock]`

**存储**：`data/got_screener/YYYYMMDD.json`

---

## 5. 数据层架构

### 5.1 DataFetcherManager 故障切换机制

**文件**：`data_provider/base.py`

**优先级链**：
```python
_DAILY_MARKET_FETCHER_SUPPORT = {
    "MootdxFetcher": {"cn"},           # P0, TCP 7709
    "EfinanceFetcher": {"cn"},         # P1
    "AkshareFetcher": {"cn", "hk"},    # P1
    "TushareFetcher": {"cn", "hk"},    # P2
    "PytdxFetcher": {"cn"},            # P2
    "BaostockFetcher": {"cn"},         # P3
    "YfinanceFetcher": {"cn", "hk", "us"},  # P4
    "LongbridgeFetcher": {"hk", "us"},      # P5
    "FinnhubFetcher": {"us"},          # P2
    "AlphaVantageFetcher": {"us"},     # P3
}
```

**故障切换策略**：
1. 美股指数/美股股票直接路由到 YfinanceFetcher
2. A 股指数跳过 MootdxFetcher 和 EfinanceFetcher（间歇失败/反爬严格）
3. 从最高优先级数据源开始尝试
4. 捕获异常后自动切换到下一个
5. 记录每个数据源的失败原因（record_provider_run）
6. 所有数据源失败后抛出 DataFetchError

**美股专用路由**：
```python
# 配置长桥凭据后
prefer_lb: Longbridge → Finnhub → AlphaVantage → Yfinance
# 未配置长桥
default: Finnhub → AlphaVantage → Yfinance → Longbridge
# 美股指数
index: Yfinance → Finnhub
```

### 5.2 mootdx_fetcher.py TCP 数据获取

**核心特点**：
- 优先级：P0（最高）
- 协议：TCP 7709（通达信行情服务器）
- 优势：TCP 协议不受 HTTP 反爬影响，免费、无需 Token
- 支持市场：仅 A 股（沪深）

**关键策略**：
1. **BESTIP 自动探测**：`Quotes.factory(market='std', timeout=10)`
2. **连接冷却**：失败后 15 秒内跳过
3. **指数退避重试**：使用 tenacity 库
4. **不支持的市场**：直接抛异常触发降级

### 5.3 em_rate_limiter.py 限流参数

**限流参数**：
```python
EM_MIN_INTERVAL = 1.0        # 最小请求间隔（秒）
EM_JITTER_MIN = 0.1          # 抖动下限
EM_JITTER_MAX = 0.5          # 抖动上限
EM_TIMEOUT = 15              # 请求超时（秒）
```

**限流策略**：
1. **串行化**：`threading.Lock` 保证同一时刻只有一个请求在执行
2. **最小间隔**：1.0s + 随机 0.1-0.5s 抖动
3. **连接池**：`HTTPAdapter(maxsize=5)` 复用 TCP 连接
4. **自动重试**：`urllib3.Retry(total=3, 指数退避, 429/5xx 强制重试)`
5. **UA 随机化**：5 个浏览器 UA 轮换

**统一入口**：`em_get()` / `em_post()`

### 5.4 东财反爬补丁

**文件**：`src/patches/eastmoney_patch.py`

**核心实现**：
1. **全局替换**：`requests.Session.request = patched_request`
2. **目标域名**：fund.eastmoney.com, push2.eastmoney.com, push2his.eastmoney.com
3. **NID 令牌**：
   - 向 `https://anonflow2.eastmoney.com/backend/api/webreport` 请求
   - 缓存 20 秒，失败后缓存 5 分钟
   - 生成随机 UUID MD5 + SHA256 st_nvi
4. **随机 UA**：`fake_useragent.UserAgent().random`
5. **Referer**：push2his 需要 `https://quote.eastmoney.com/`
6. **随机休眠**：1-4 秒

---

## 6. 编排器与执行流程

**文件**：`src/got/orchestrator.py`

### 6.1 两路并行的实际调度方式

**执行流程**：
```
1. 交易日检查 (L65-66)
2. 并行启动两路 (L72-79):
   - 技术分析路: Node 1 → Node 2 → Node 3 (串行依赖)
   - 独立数据路: Node 4 ‖ Node 5 (并行)
3. 两路都完成后 → Node 6 聚合 (L92-100)
4. 自动存档 (L105-123)
5. Phase 2 统一决策 (L125-156)
```

**技术分析路**：
```python
market_ctx = await self.market_node.run()
sector_ctx = await self.sector_node.run(market_context=market_ctx)
capital_ctx = await self.capital_node.run(market_context=market_ctx, sector_context=sector_ctx)
```

**独立数据路**：
```python
news_ctx, sentiment_ctx = await asyncio.gather(
    self.news_node.run(),
    self.sentiment_node.run(),
)
```

### 6.2 Phase 1 → Phase 2 的衔接点

**触发时机**：Phase 1 聚合完成后，且 `use_mock=False`（真实数据）

**衔接方式**：
```python
decision_node = PortfolioDecisionNode(use_mock=False)
decision = await decision_node.run(
    unified_context=unified,
    market_context=market_ctx,
    sector_context=sector_ctx,
    capital_context=capital_ctx,
    news_context=news_ctx,
    sentiment_context=sentiment_ctx,
)
```

### 6.3 archive 的触发时机

**Phase 1 存档**：
- 触发：`use_mock=False` 且 Phase 1 完成
- 位置：`data/got_daily/YYYYMMDD/`
- 包含：6 个节点 JSON + unified.json

**Phase 2 存档**：
- 触发：Phase 2 完成
- 调用：`archive_phases2(decision, run_date=target_date)`

### 6.4 错误处理策略

**Phase 1 错误**：
- 两路并行失败：抛出异常，终止流程
- 存档失败：`logger.warning`，不阻断主流程

**Phase 2 错误**：
- Phase 2 失败：`logger.warning`，不阻断 Phase 1
- Phase 2 存档失败：`logger.warning`，不阻断主流程

---

## 7. 历史档系统

**文件**：`src/got/archive.py`

### 7.1 目录结构

```
data/got_daily/YYYYMMDD/
├── market.json          # Node 1 输出
├── sector.json          # Node 2 输出
├── capital.json         # Node 3 输出
├── news.json            # Node 4 输出
├── sentiment.json       # Node 5 输出
├── unified.json         # Node 6 输出
├── reasoning.md         # 所有节点的 LLM reasoning 汇总
└── meta.json            # 运行元数据（耗时/数据质量/LLM模型/git commit）
```

### 7.2 存档内容

每个 JSON = `ctx.model_dump(mode="json")` 全量存档（含 None 字段，方便分析数据缺失模式）

**reasoning.md**：汇总所有 6 个节点的 LLM 推理文本（人读友好）

**meta.json**：记录运行元数据（耗时 / 数据质量摘要 / 缺失维度 / LLM 模型 / git commit）

### 7.3 保留策略

永久保留（单档 ~50KB，一年 ~12MB）

`data/got_daily/` 已被 `.gitignore` 的 `/data/` 规则覆盖

---

## 附录 A: 跨节点 LLM 调用汇总

| 节点 | 调用数 | 调用点 | 降级策略 |
|------|--------|--------|---------|
| Node 1 | 1 次 | reasoning | 规则拼接 |
| Node 2 | 1 次 | reasoning | 规则拼接 |
| Node 3 | 1 次 | reasoning | 规则拼接 |
| Node 4 | 4 次 | 事件提取 + 概念发现 + 风险总结 + reasoning | 风险→规则从负面事件提取；reasoning→规则拼接 |
| Node 5 | 1 次 | reasoning + hot_topics | 规则拼接 |
| Node 6 | 2 次（1 次总是 + 1 次条件） | 板块精炼（总是）+ 消息面覆盖（条件） | 板块精炼失败→保持原始分类；覆盖失败→返回 None |
| Phase 2 | 1 次 | 精炼总结 | 无（失败则 decision.reasoning 为空） |
| **总计** | **11 次** | | |

---

## 附录 B: 关键常量速查

### Node 1
- **K 线长度**：250 日
- **score→trend 阈值**：±0.3（收窄，非旧版 ±0.5）
- **confidence 下限**：0.05

### Node 2
- **启动信号硬门槛**：20日突破+0.5%、量能 1.5x、站上 MA20
- **过热前置条件**：距 60日高点 ≤10%
- **主线框出框阈值**：score < 50
- **家族聚类阈值**：r > 0.65

### Node 3
- **板块资金流归一化**：概念 200亿→1.0，行业 100亿→1.0
- **DDE 归一化**：1000亿→1.0
- **态度阈值**：±0.3

### Node 4
- **去重阈值**：0.45（原始新闻）、0.35（跨源确认）、0.6（持久化）
- **评分权重**：high=2.0, medium=1.0, low=0.0
- **多源确认奖励**：source_count≥3 → ×1.5
- **影响阈值**：±0.15

### Node 5
- **反身性阈值**：euphoria>0.5, optimism>0.2, pessimism<-0.2, capitulation<-0.5
- **恐贪指数**：(score+1)/2
- **分歧度反向信号**：dispersion<0.35 AND (mean>70 OR mean<30)

### Node 6
- **regime 阈值**：bull>0.3, bear<-0.3, else transition
- **仓位上限**：clip(0.0, 0.8)
- **EMA_ALPHA**：0.3
- **冷启动样本数**：5

### Phase 2
- **止损阈值**：bear=-5%, transition=-7%, bull=-10%
- **回撤缩放**：0-5%=1.0, 5-10%=75%, 10-15%=50%, >15%=50%, 最低20%
- **最多买入**：3 只
- **埋伏仓缩小**：45%

### GOT 选股
- **DDE 阈值**：主线>=0.8%, 埋伏>0(5日累计)
- **风险阈值**：RSI>80(-30), 5日涨幅>20%(-25), 波动率>0.05(-15)
- **权重分配**：资金30% + 消息20% + 板块20% + 技术15% + 风险15%
- **每板块最多**：1 只

### 数据层
- **Mootdx 冷却**：15 秒
- **东财限流**：1.0s + 0.1-0.5s 抖动
- **东财重试**：3 次指数退避
- **NID 缓存**：20 秒，失败 5 分钟

---

## 附录 C: 设计文档 vs 实际代码差异总结

| 项目 | 设计文档（旧） | 实际代码（新） |
|------|--------------|--------------|
| **Node 1 评分维度** | 5 维 + ADX 动态调权 | 4 维（无 market_breadth），ADX 仅作元信息透传 |
| **Node 1 仓位上限** | 离散映射 (bullish→1.0/neutral→0.5/bearish→0.0) × tf_confidence × 极端风险折扣 | 连续函数：波动率调整(target_vol/realized_vol) × 状态缩放(tf×0.4+cross×0.3+direction×0.3) |
| **Node 1 score→trend 阈值** | ±0.5 | **±0.3**（收窄） |
| **Node 2 强度评分** | 5 维 clip 归一化 | **4 维截面百分位排名**（无 capital_flow） |
| **Node 2 启动信号** | 5 维软打分 | **6 维**（新增 ad_confirmation 15%权重），且阈值 regime-dependent |
| **Node 2 过热信号** | 4 条件二元触发 | **6 信号渐进式加权** severity（MACD背离+量能背离+宽度+换手+A/D分配+天量滞涨）|
| **Node 2 板块发现** | 硬编码 ~30 个 | **两阶段动态发现**：stock_all() 全量 → 快筛 → Top N + registry 保底 |
| **Node 2 状态判定** | 8 分支优先级链（P0~P6） | **RRG 四象限**（Leading/Improving/Weakening/Lagging），5 分支，启动+过热+持续强势+高位派发统一 |
| **Node 2 生命周期** | 天数驱动（LIFECYCLE_THRESHOLDS） | **RRG 象限映射**（Leading→acceleration, Improving→launch, Weakening→mature, Lagging→late_stage） |
| **Node 2 启动/过热信号** | 状态驱动器 | **降级为诊断指标**（启动 quality_score 供分级用，过热 severity 供拥挤预警用） |
| **Node 2 框系统** | 无 | **SQLite 持久化**的主线框（个体级进出管理）；埋伏板块不持久化，每日 state 实时判定 |
| **Node 2 家族聚类** | 硬编码关键词 | **90日收益相关性矩阵 + 层次聚类**(complete linkage, r>0.65) |
| **Node 3 维度数** | 5 维（北向/龙虎榜/ETF/融资/大宗） | **3 维**（板块资金流/DDE/北向） |
| **Node 3 权重分配** | 0.25/0.25/0.25/0.15/0.10 | **0.50/0.35/0.15** |
| **Node 4 数据源** | 搜索型（SearchService） | **7 源推送型**（新浪/CLS/华尔街见闻/东财/NewsNow/NGA/同花顺热榜） |
| **Node 4 LLM 调用** | 3 次 | **4 次**（事件提取+概念发现+风险总结+reasoning） |
| **Node 5 评分维度** | 3 维（雪球/宽度/搜索） | **6 维**（东财/宽度/NGA/搜索/详情/动量） |
| **Node 5 核心数据源** | 雪球热榜 | **东财千股千评**（雪球降级为备用） |
| **Node 6 架构** | 10 对硬编码矛盾 + 3 次 LLM | **3 层分级 GOT**（L0 全局/L1 板块 mini-GOT/L2 组合决策）+ 2 次 LLM |
| **Node 6 矛盾检测** | 10 对硬编码 | **2 种自动检测规则**（regime_vs_sectors + family_capital_mismatch） |
| **Node 6 仓位日限幅** | ±0.2 | **已移除**（三层仓位系统已提供平滑过渡） |
| **Phase 2 架构** | 串行（持仓判断→选股→建议） | **统一决策**（风险评估→统一打分→组合优化→轮动检测→LLM精炼） |
| **选股消息面** | 每只候选股 1 次 LLM | **1 次 LLM 评 100 板块 + 字典查找** |
| **选股评分权重** | 资金30% + 消息20% + 板块20% + 技术15% + 风险15% | **同左**（已优化） |

---

## 附录 D: 学术依据

| 设计决策 | 论文依据 |
|---------|---------|
| Node 2 启动信号 Regime-Dependent 阈值 | Mokhtarian et al. 2024; Alexiou & Tyagi 2020; Brock et al. 1992 (band filter); IEEE 2026 (<1.5x 量能视为假突破) |
| Node 2 A/D Line 确认 | A/D Line 确认突破真伪（65-72% 准确率） |
| Node 2 家族聚类 | 层次聚类 (complete linkage) |
| Node 2 RRG 四象限状态判定 | Minervini 2016 (Relative Rotation Graph); Janssen 2020 (RS-Momentum 领先 RS-Ratio) |
| Node 5 6 维置信度加权 | fffinstill Fear & Greed Index; 国金证券舆情选股; 清华投资者情绪 |
| Node 5 情绪分歧度 | 国金证券研究：低分歧+极端均值 → 反向信号 |
| Node 6 分层 GOT | ICLR 2025: Top-Down Sector Allocation; FINCON NeurIPS 2024: Manager-Analyst 层级; FinCoT ACL 2025: 推理结构匹配领域工作流 |
| Phase 2 统一决策 | FINCON NeurIPS 2024; RAPTOR NeurIPS 2025 (组合构建是优化问题，不是顺序决策流水线) |
| Phase 2 板块暴露限制 | Shen 2026 + Bayesian Kelly + Conviction-Tier |
| Phase 2 回撤缩放 | Moreira & Muir (2017), AlgoKing "Dynamic Position Sizing with Kelly + Regime" |

---

**文档结束**
