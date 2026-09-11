# A股智能分析系统 GOT 架构设计文档 v2.0

> **版本**: v2.0（2026-08-31）  
> **基于代码实际实现**（反映 2026-08-31 代码状态）  
> **架构**: 半 GOTT 前半段图结构并行 + 后半段线性流水线决策 + 催化剂前瞻系统

---

## 目录

1. [架构总览](#1-架构总览)
2. [Node 1: 大盘分析](#2-node-1-大盘分析)
3. [Node 2: 板块分析](#3-node-2-板块分析)
4. [Node 3: 资金面分析](#4-node-3-资金面分析)
5. [Node 4: 消息面分析](#5-node-4-消息面分析)
6. [Node 5: 情绪面分析](#6-node-5-情绪面分析)
7. [Node 6: 聚合节点](#7-node-6-聚合节点)
8. [催化剂系统](#8-催化剂系统)
9. [Phase 2: 统一决策节点](#9-phase-2-统一决策节点)
10. [GOT 选股流水线](#10-got-选股流水线)
11. [持久化与存储层](#11-持久化与存储层)
12. [配置与开关](#12-配置与开关)
13. [附录 A: LLM 调用总结](#附录-a-llm-调用总结)
14. [附录 B: 关键常量速查](#附录-b-关键常量速查)
15. [附录 C: 学术参考](#附录-c-学术参考)

---

## 1. 架构总览

### 1.1 系统架构图

```
                          ┌─────────────────────────────────────────────┐
                          │              数据源层                        │
                          │  5指数K线 · 板块K线 · 资金流 · 7源新闻      │
                          │  雪球/东财评论 · 热榜 · 问财                 │
                          └────────────────────┬────────────────────────┘
                                               │
                  ┌────────────────────────────┼──────────────────────────┐
                  │                            │                          │
     ┌────────────┴────────────┐  ┌────────────┴────────────┐            │
     │  技术分析路（串行）      │  │  独立数据路（并行）      │            │
     │  Node 1 → Node 2 → N3  │  │  Node 4 ‖ Node 5        │            │
     └─────────────────────────┘  └─────────────────────────┘            │
                  │                            │                          │
                  └──────────┬─────────────────┘                          │
                             ↓                                            │
              ┌──────────────────────────────┐                           │
              │  Node 6: 聚合                 │◄── 催化剂日历(嵌入NewsCtx)│
              │  L0: 全局环境(regime)         │                           │
              │  L1: 板块mini-GOT             │                           │
              │  L1': 埋伏通道(4层闸门/G2注意力闸+G3未启用+蓄水池)  │                           │
              │  L2: 组合级决策               │                           │
              │  + 跨节点时序共振信号         │                           │
              └──────────────┬───────────────┘                           │
                             ↓                                            │
              ┌──────────────────────────────┐                           │
              │  Phase 2: 统一决策             │                           │
              │  风险评估→统一打分→组合优化    │                           │
              │  →轮动检测→LLM精炼            │                           │
              │  + 埋伏退出信号(P4)           │                           │
              └──────────────┬───────────────┘                           │
                             ↓                                            │
                    PortfolioDecision                                     │
                                                                         │
              ┌──────────────────────────────┐                           │
              │  P6: 催化兑现追踪              │                           │
              │  事后验证 + PEAD多窗口        │                           │
              │  + 故事寿命统计               │                           │
              └──────────────────────────────┘                           │
```

### 1.2 编排流程

**入口**: `GOTOrchestrator.run()`

**两路并行** (`asyncio.gather`):
- **tech_path**: Node 1 → Node 2(消费 market_ctx) → Node 3(消费 market+sector_ctx)
- **data_path**: Node 4 ‖ Node 5 (并行独立采集)

**汇合后串行**:
1. Node 6 聚合（消费全部 5 个 context）
2. 归档（`archive_daily_run()`，仅生产模式）
3. Phase 2 统一决策（消费 6 个 context）
4. 归档（`archive_phase2()`）
5. P6 催化兑现追踪（`check_and_record_realization()`，失败不阻塞）

### 1.3 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **数据源优先级** | mootdx TCP P0 → efinance HTTP P0 → akshare P1 | TCP 不受 HTTP 反爬影响 |
| **评分归一化** | 截面百分位排名(Node 2) | 自适应牛熊市，避免硬阈值 |
| **矛盾检测** | 规则决策树 + LLM 仲裁(T3, medium/high) | 规则稳定 + LLM 语义补充 |
| **仓位计算** | 两层: 波动率目标 × 状态缩放 | Moreira & Muir 2017 |
| **状态机** | RRG 四象限 + 6 态 Markov | 比硬编码阈值更灵活 |
| **埋伏检测** | 6 维加权评分(非 AND 闸门) | 消除硬闸门归零陷阱 |
| **催化剂排序** | B 公式(影响×临近×置信+冗余封顶 40%) | 前瞻维度注入选股 |
| **板块名解析** | 四层: 清洗→别名→模糊→统一入口 | TDX/THS 不一致是常见故障源 |

### 1.4 代码规模总览

| 模块 | 文件 | 行数 | LLM 调用 |
|------|------|------|---------|
| Node 1 大盘 | market_analysis.py | 1042 | 1 |
| Node 2 板块 | sector_analysis.py | 4373 | 1 |
| Node 3 资金 | capital_analysis.py | 859 | 1 |
| Node 4 消息 | news_analysis.py | 1072 | 4 |
| Node 5 情绪 | sentiment_analysis.py | 1351 | 1 |
| Node 6 聚合 | aggregator.py | 3438 | 3-6 |
| Node 7 决策 | portfolio_decision.py | 2527 | 1 |
| 催化剂日历 | catalyst_calendar.py | 1642 | 3(T5+T2+叙事) |
| 催化剂兑现 | catalyst_realization.py | 772 | 0 |
| 新闻采集 | news_feeds.py | 793 | 0 |
| 选股流水线 | stock_screener.py | 2912 | 0-2(T1) |
| **总计** | | **~21,000** | **12-15** |

---

## 2. Node 1: 大盘分析

**文件**: `src/got/nodes/market_analysis.py` (1042 行)

**职责**: 5 指数 250 日 K 线分析 + 多时间框架 mini-GOT + 仓位上限计算

**输入**: 无（根节点）  
**输出**: `MarketContext`

### 2.1 核心 Schema 字段

**MarketContext**:
- `tech_growth`: TechGrowthSignal（创业板 0.6 + 科创 50 0.4 加权）
- `mid_small_cap`: StyleSignal（中证 500）
- `large_cap`: StyleSignal（沪深 300）
- `market_state`: MarketStateSignal — **核心条件变量**，下游消费
  - `attack_power` (0~1): 主线数量限制的依据
  - `regime`: bull/bear/transition
  - `mini_got_*`: 时间框架 mini-GOT 结果
  - `crash_override`: 单日暴跌>-3%→强制 attack_power≤0.15
- `position_limit` (0.2~1.5): 两层系统
  - Layer 1: 波动率目标 `target_vol/realized_vol`, range [0.5, 1.5]
  - Layer 2: 状态缩放 `tf_confidence×0.4 + cross_consistency×0.3 + direction×0.3`, range [0.2, 1.0]
  - Final = L1 × L2, clip [0.2, 1.5]
- `confidence`: 3 因子加权（跨指数 35% + 跨时间框架 35% + ADX 强度 30%）
- `market_breadth`: 涨跌家数/涨停跌停
- `realized_volatility`, `vol_adjusted_position`, `regime_scale`

### 2.2 核心算法

**1. ADX 自适应权重** (`indicators.py`):

| 状态 | ADX 范围 | MA 排列 | MACD | 量价 | 布林 |
|------|---------|--------|------|------|------|
| 强趋势 | >30 | 0.24 | **0.35** | 0.29 | 0.12 |
| 温和 | 20-30 | 0.29 | 0.29 | 0.24 | 0.18 |
| 震荡 | ≤20 | **0.12** | 0.24 | 0.24 | **0.40** |

极端波动: 布林+0.10（封顶 0.50）

**2. 时间框架 mini-GOT**: short(5d)×0.20 + medium(20d)×0.35 + long(60d)×0.45
- 矛盾检测: |short-long| > 0.3 → 分数级（非类别级）
- 模式分类: pullback_in_uptrend / rally_in_downtrend / broad_decline / broad_advance / mixed_signals

**3. attack_power 合成**:
- momentum_power = (tech_score + 1) / 2
- mini_got_power = (unified_score + 1) / 2
- base = 0.5 × momentum + 0.5 × mini
- risk_power = (position_limit - 0.2) / 1.3
- attack_power = 0.5 × base + 0.5 × risk
- crash_override: 最弱指数<-3% → 强制≤0.15

### 2.3 关键常量

- 趋势阈值: ±0.3
- 仓位范围: [0.2, 1.5]
- 波动率目标: 0.15（年化）
- 波动率回看: 20 日
- 暴跌阈值: -3%
- 置信度下限: 0.05

### 2.4 LLM 使用

1 次调用，temp=0.3，max_tokens=200，≤100 字市场摘要

### 2.5 DataQuality

追踪 5 指数 K 线、市场宽度的可用性，缺失维度权重重分配

---

## 3. Node 2: 板块分析

**文件**: `src/got/nodes/sector_analysis.py` (4373 行，最大文件)

**职责**: 板块 4 维百分位评分 + RRG 状态机 + 主线分级 + 埋伏检测

**输入**: `MarketContext`（attack_power→主线数量限制，today_change→弱市检测）  
**输出**: `SectorContext`

### 3.1 核心 Schema 字段

**SectorContext**:
- `active_mainline`: list[MainlineGroup]
- `exhausted_mainline`: 已消耗主线
- `ambush`: 埋伏板块
- `near_miss_ambush`: 差一点够到埋伏门槛的板块（含 near_miss_conditions）
- `defensive_rotation`: 防守轮动
- `cooling_down`: 高位降温
- `industry_top`: 行业板块 Top
- `theme_families`: 题材家族
- `strength_lookup`: dict[板块名→强度分]（供 Node 6 消费）
- `confidence_level`: "high"/"medium"/"low"

### 3.2 评分体系（4 维截面百分位）

| 维度 | 权重 | 原始指标 |
|------|------|---------|
| 价格动量 | 0.30 | 5 日涨幅 |
| 换手扩张 | 0.25 | 5d 均量/20d 均量 |
| 相对强度 | 0.25 | 20 日涨幅 |
| 宽度健康 | 0.20 | 上涨家数/(上涨+下跌) |

原始值 → 截面百分位排名(0-1) → 加权和×100 = strength_score(0-100)  
THS 热榜加分: Top10 +5, Top20 +3, Top30 +1

### 3.3 RRG 相对旋转图

- RS-Ratio: strength_score 的截面百分位（3 日 EMA, α=0.5）
- RS-Momentum: 3-5 日强度轨迹斜率，归一化 [0,1]
- 四象限: Leading(≥0.5,≥0.5) / Improving / Weakening / Lagging
- 阈值: 0.5/0.5

### 3.4 6 态状态机（Markov）

```
neutral → active_mainline → high_position_cooling → exhausted_mainline → neutral
neutral → ambush → active_mainline
neutral → defensive_rotation → neutral
```

- 无主线→埋伏直边
- 硬触发: HI1（分数暴跌≥20→即时降级），HI2（单日>8%+leading+score>70→破例入主线），HI4（反弹: 单日>3%+score>80→快速回主线）
- E12b 飞刀驱逐: momentum<0.2 + score<25 → 强制退出 ambush→neutral
- 退出压力: score<50→+1/天; T-0 跌<-3%→+2; <-1.5%→+1; S/A 容忍 3 天，B=1 天
- 驻留确认: 多数转移需连续日确认（HMM 持久化）

### 3.5 9 维过热检测

- D1-D6（背离型，需 price_near_peak 在 60 日高点的 10% 内）:
  D1: MACD 背离，D2: 量能背离，D3: 宽度极端，D4: 换手极端，D5: A/D 分配，D6: 量能停滞
- D7-D9（高潮型，无前提条件）:
  D7: 龙头极端，D8: 主线疲劳，D9: 成分股追涨
- severity >0.5=exhausted，>0.3=warning

### 3.6 6 维埋伏评分

| 维度 | 权重 | 逻辑 |
|------|------|------|
| RS-Ratio(低) | 0.20 | 越低→分越高 |
| RS-Momentum | 0.15 | >0.3 给分，>0.65 半分，1.0 满分 |
| 转向检测 | 0.15 | momentum 从下方穿越 0.5（1 天=1.0，2 天=0.7，3 天=0.4） |
| 吸筹确认 | 0.25 | 布林收窄+A/D 线+VCV+试探（4 子维加权） |
| 仓位位置 | 0.15 | 低价+高量=吸筹 |
| 强度上限 | 0.10 | 强度越低→上涨空间越大 |

- 冷启动惩罚: 轨迹<5 天→线性渐入 `len/5.0`
- 方向惩罚: 近 3 日累计跌幅每 1%→总分×0.9（下限 0.3）
- 斜率约束: 3+ 天单调递减→额外惩罚（0.8/0.6/0.4）
- 门槛: Track2 填充需≥0.30; Track1 上限 16

### 3.7 噪声过滤（222 条精确集合 + 语义模式）

- `_NOISE_BOARD_EXACT`: 222 条（7 族: 统计指数/行情事件/财务标签/机构持仓/风格市值/公司动作/ST 杂项）
- `_is_noise_pattern()`: 统计后缀（主板/创业/科创+总值/流通等），事件前缀（昨日/近期/最近/历史/即将/连续/预计），TDX 前缀
- 不设白名单——新产业概念自动发现

### 3.8 主线家族（FAMILY_KEYWORDS, 12 族）

AI 硬件/AI 软件/机器人/智能驾驶/新能源/医药/金融/周期资源/军工/消费/华为生态/前沿科技

### 3.9 板块发现（4 通道）

1. 日涨幅 Top30+ 跌幅 Top30
2. B-lite 累积动量（3d>0 ∧ 5d>0 ∧ 非暴跌 → Top20）
3. 注册表兜底
4. 时间线维护（前日可操作板块强制入，max 50）

### 3.10 主线分级（S/A/B）

政策支持+3，家族成员≥3+2，换手扩张>0.6+2，连续强势+1  
S≥5, A≥3, B<3

### 3.11 LLM 使用

1 次调用，temp=0.3，max_tokens=400，纯总结不影响信号

### 3.12 DataQuality

追踪板块获取成功率，>30% 失败→confidence_level="low"

---

## 4. Node 3: 资金面分析

**文件**: `src/got/nodes/capital_analysis.py` (859 行)

**职责**: 3 维资金面评分 + 降级机制 + 多日时序扩展

**输入**: MarketContext + SectorContext  
**输出**: `CapitalContext`

### 4.1 3 维度评分

| 维度 | 权重 | 数据源 | 评分 |
|------|------|--------|------|
| 板块资金流 | 0.50 | akshare 概念+行业资金流（实时） | 概念 Top20 均/200 亿×0.65 + 行业 Top10 均/100 亿×0.35 |
| DDE | 0.35 | DdeFetcher（Playwright→东财） | total_net/1e11, clip(-1,1) |
| 北向实时 | 0.15 | akshare stock_hsgt_fund_min_em | cumulative/500000 + trend_bonus |

**豁免维度**: `northbound_realtime` 永久豁免（2024-08 起官方停披）
- 缺失北向→不降低 confidence_multiplier，不标记 fetch_failed
- 有效维度=2（板块流+DDE），confidence=available/2

**降级**: 缺失维度权重归一化；全 3 维失败→status="failed"，score=0

### 4.2 v2.1 多日扩展

- 板块资金流 3d/5d 日均（从归档读）
- DDE 累计 3d/5d + 连续正天数
- 主线一致性: 每条主线板块连续正流入天数
- 市场加速度: (5d 均/1d - 1)
- 主线加分: 百分位×一致性×0.02，上限 0.15
- 回退开关: `DISABLE_NODE3_MULTIDAY=true`

### 4.3 LLM 使用

1 次调用，temp=0.3，max_tokens=300

### 4.4 DataQuality

追踪 3 维度可用性，缺失→权重归一化+confidence 下调

---

## 5. Node 4: 消息面分析

**文件**: `src/got/nodes/news_analysis.py` (1072 行)

**职责**: 7 源推送型采集 + 4 次 LLM 调用 + 事件生命周期追踪

**输入**: 无（并行采集）  
**输出**: `NewsContext`

### 5.1 7 源推送型采集（news_feeds.py, 793 行）

| 源 | 函数 | 覆盖 |
|----|------|------|
| S1 新浪 | fetch_sina_news() | 4 栏目，21+ 小时归档 |
| S2 财联社 | fetch_cls_telegraph() | A 股核心快讯，签名 API |
| S3 华尔街见闻 | fetch_wallstreetcn_news() | 全球宏观 |
| S4 东财快讯 | fetch_eastmoney_kuaixun() | 国内媒体聚合 |
| S5 NewsNow | fetch_newsnow_hot() | 8 平台热榜（微博/百度/知乎/澎湃/头条/CLS/抖音/B 站） |
| S6 NGA | fetch_nga_forum_news() | 散户讨论（早期信号） |
| S7 同花顺 | fetch_ths_hot_topics() | 投资者关注 |

**权重**: 来源(30%) + 时效(25%) + 热度关键词(20%) + 跨源确认(25%)  
**去重**: SequenceMatcher ≥ 0.45  
**上限**: MAX_MERGED_NEWS = 150  
**NGA 保底**: ≥5 条 NGA 进入 Top150

### 5.2 LLM 调用（4 次，串行）

| 调用 | temp | max_tokens | 用途 |
|------|------|------------|------|
| 事件提取 | 0.2 | 4000 | 结构化事件（type/impact/sentiment/affected_sectors/stocks） |
| 概念发现 | 0.5 | 800 | 发现新投资概念 |
| 风险总结 | 0.3 | 300 | 3-5 个不确定性因素 |
| 推理 | 0.3 | 400 | 最终分析报告 |

### 5.3 事件生命周期状态机

```
emerging → fermenting → peak → fading → archived
```

- emerging: first_seen=今天，mention_count=1
- fermenting: mention_count≥3，span≥2 天
- peak: mention_count≥10，≥3 独立源，span≥3 天
- fading: last_seen<今天
- archived: last_seen<今天-7 天

### 5.4 原始新闻持久化（P1-1）

- → SQLite `node4_raw_news` 表
- 去重: 精确（normalized_title+source）→ 相似（bigram Jaccard≥0.45，同源，3 日窗口）
- 跨日发酵检测: appearance_count≥3 AND span≥2 天

### 5.5 历史上下文注入

加载 7 天事件历史→LLM prompt，支持跨日关联

### 5.6 DataQuality

追踪 7 源采集成功率，缺失源权重归一化

---

## 6. Node 5: 情绪面分析

**文件**: `src/got/nodes/sentiment_analysis.py` (1351 行)

**职责**: 6 维置信度加权评分 + 反身性 5 阶段 + 情绪分歧度

**输入**: 无（并行采集）  
**输出**: `SentimentContext`

### 6.1 6 维置信度加权评分

| 维度 | 权重 | 置信度 | 数据源 |
|------|------|--------|--------|
| 东财评论 | 0.25 | 0.90 | 千股千评均分→[-1,1] |
| 市场宽度 | 0.15 | 0.85 | 涨跌比 60%+涨停比 40% |
| NGA 论坛 | 0.20 | 0.80 | 市场情绪已[-1,1] |
| 搜索舆情 | 0.15 | 0.70 | 关键词情感+否定检测 |
| 评论详情 | 0.10 | 0.60 | 参与意愿变化/30 |
| 时序动量 | 0.15 | 0.80 | EMA(5)偏差 |

缺失→权重重分配（归一化）

### 6.2 恐贪指数

`(sentiment_score + 1) / 2` → [0, 1]

### 6.3 反身性 5 阶段

| 阶段 | 条件 | 信号 | 置信度 |
|------|------|------|--------|
| euphoria | score>0.5 AND attention>0.75 | contrarian_sell | 0.7 |
| optimism | score>0.2 | trend_following | 0.5 |
| neutral | |score|≤0.2 | neutral | 0.3 |
| pessimism | score<-0.2 | trend_following | 0.5 |
| capitulation | score<-0.5 AND attention>0.5 | contrarian_buy | 0.7 |

### 6.4 阶段状态机（v2.1 跨日记忆）

- `stage_state`: current_stage, dwell_days, momentum_direction, stage_transitions(10d), contrarian_alert
- `persistence_regime`: AR(1) 自回归系数，>0.7=high_persistence（趋势态），<0.3=low_persistence（均值回归）
- 反向信号规则:
  1. 连续 3 天升温→contrarian_sell
  2. 连续 3 天降温→contrarian_buy
  3. euphoria>5 天→contrarian_sell
  4. capitulation>3 天→contrarian_buy
  5. 高频切换（10d≥5 次）→不触发反向

### 6.5 情绪分歧度

- 变异系数: std/|mean| of 综合得分
- 涨跌差: 上涨股均分-下跌股均分
- 反向信号: 分歧度<0.35 AND (mean>70 OR mean<30) → 一致看=反转信号

### 6.6 LLM 使用

1 次调用，temp=0.3，max_tokens=600，JSON 输出（reasoning+hot_topics）

### 6.7 DataQuality

追踪 6 维度可用性，缺失→权重重分配

---

## 7. Node 6: 聚合节点

**文件**: `src/got/nodes/aggregator.py` (3438 行)

**职责**: 三层 GOT 架构（L0 全局环境 + L1 板块 mini-GOT + L1' 埋伏通道）+ 矛盾检测 + 跨节点时序共振

**输入**: 全部 5 个 context  
**输出**: `UnifiedContext`

### 7.1 L0: 全局环境研判（MarketRegime）

**规则决策树**:
- Node 1: ADX + 趋势分（±0.3 权重）
- Node 2: market_direction（普涨+0.3，普跌-0.3，分化 0）
- Node 3: institutional_score × 0.2
- Node 4: news_impact_score × 0.2
- Node 5: fear_greed（>0.7→+0.1，<0.3→-0.1）+ 阶段修正 + momentum×0.1

**regime 判定**: score>0.3=bull，<-0.3=bear，else=transition  
**risk_budget**: bull [0.6-0.8]，bear [0.3-0.5]，transition [0.4-0.6]  
**状态追踪**: dwell_days, previous_regime, transition_count, transition_pending

### 7.2 L1: 板块 mini-GOT（4 维交叉验证）

- tech_score（来自 Node 2 strength_score）
- capital_check: aligned/divergent/neutral → ±10/-15
- news_check: 分级（high/medium/low × source_count × global × fading × generic）→ ±20/-24
- sentiment_check: high +5，low -5（埋伏除外）
- 生命周期: launch/acceleration +8，late_stage/exhausted -15
- 分类: Top-N(12)=actionable，其余=caution，硬性排除: composite<40 或 late+divergent

### 7.3 L1': 埋伏通道（与 L1 平行）

**4 层闸门（其中 G2 注意力闸 / G3 因无数据源未启用）**:
1. ★★★利空催化剂否决（不可逆）
2. 防买顶: 热榜≥3 只 OR 催化剂出现≥5 天（G2 已发酵子闸活 / 注意力子闸依赖 hot_sectors 恒空 ⇒ 结构性不可达）
3. 拥挤黑名单: 精确名称匹配（G3，依赖 hot_sectors 恒空 ⇒ 结构性不可达）
4. 吸筹确认（3 档）:
   - 强: ≥3 连续正流入+今天正
   - 中: 3d 均正+今天正
   - 弱: 仅今天正
   - 缺失: 数据不可用→蓄水池观察

**B 公式催化剂排序**:  
主催化分 = 影响权重 × 临近系数 × 置信度 + 冗余加成（封顶+40%）

### 7.4 蓄水池系统

- 跨日合并（前日结转+新入）
- 过期: RESERVOIR_EXPIRY_DAYS=10
- 容量上限: RESERVOIR_MAX_SIZE=15（最旧优先驱逐）
- 升格: 驻留≥RESERVOIR_PROMOTION_MIN_DAYS=2 + 合格催化剂 + 强度回升→重走闸门

### 7.5 Agent 提名（Decision 21）

- 仅被吸筹闸拦截的候选
- 单次批量 LLM 调用
- AMBUSH_NOMINATION_MAX=2
- AMBUSH_NOMINATION_MIN_CONFIDENCE=0.6
- 催化剂分数折扣: AMBUSH_NOMINATION_MULTIPLIER=0.6

### 7.6 5 类矛盾检测

1. regime_vs_sectors: bear + ≥3 actionable → medium
2. sector_capital_mainline_mismatch: 资金 Top 板块家族排名≥2 → high
3. tech_composite_rank_divergence: tech vs composite 排名差≥3 → medium
4. capital_vs_sentiment: institutional≥0.6 但 fear_greed∈[0.3,0.6] → medium
5. regime_fresh_switch: dwell<2 → low

### 7.7 T3 矛盾仲裁

- 仅 medium/high，最多 3 个
- 串行 LLM（非并行），各 3 次重试
- 输出: upgrade/downgrade/keep + confidence
- 置信度门槛: <0.6→忽略
- regime dwell<2→跳过仲裁

### 7.8 EMA 基线

- regime_score 随时间追踪（bull=0.5，transition=0，bear=-0.5）
- EMA_ALPHA=0.3，冷启动预热 5 日
- Z-score 检测 sudden_trend_flip: critical>3.0（预热 5.0），high>2.0（预热 3.5）

### 7.9 跨节点时序共振信号（4 模式）

1. bull_confirmation: regime=bull + dwell≤2 + 资金加速度>0.15
2. sentiment_top: contrarian_alert + 降温 + 资金撤退
3. catalyst_confirmation: 3 日回报>5% + 一致性天数≥3
4. range_reversal: transition_count≥5 + 低持久性

### 7.10 LLM 使用

3-6 次调用:
- _llm_refine_sectors: 1 次，temp=0.3，max=500（仅升级不降级）
- _simplified_news_override: 1 次（条件性），temp=0.2，max=300
- _arbitrate_contradictions: ≤3 次串行，temp=0.3，max=300
- _llm_nominate_ambush: 1 次（条件性），temp=0.2，max=400

### 7.11 DataQuality

追踪 5 节点输入完整度，缺失维度不参与矛盾检测

---

## 8. 催化剂系统

### 8.1 日历构建（catalyst_calendar.py, 1642 行）

**事件源**:
- 结构化: 财报（akshare stock_yjyg_em）+ 宏观规则（PMI/CPI/社融/LPR）+ 全球映射（13 组）
- LLM 叙事: 从 Node 4 新闻提取前瞻性催化剂

**刷新节奏**:
- 全量重建: 每 5 交易日（CATALYST_FULL_REFRESH_INTERVAL）
- 增量: 其余日（加载 DB 活跃事件+1 日新闻窗口）

**D1-D4 守卫**:

| 守卫 | 名称 | 逻辑 |
|------|------|------|
| D1 | 回声检测 | title 与 source_news Jaccard≥0.7 或 子串包含 → 丢弃 |
| D2 | 周末守卫 | event_date.weekday()≥5 → 丢弃 |
| D3 | 时态守卫 | 来源含过去标记但标题无未来标记 → 丢弃 |
| D4 | 实体一致性 | 标题中"头衔+人名"在 source_news 找不到 → 幻觉 → 丢弃 |

**过去标记**: 已完成/已落地/涨超/暴增/暴涨/重挫/大跌/早盘/收盘/完成/落地  
**未来标记**: 将/即将/预计/定于/计划/下周/待/有望/拟

**T5 事件→板块语义对齐**:
- LLM 批量映射叙事/全球事件→板块宇宙（≤150 个）
- 一次调用产出多板块扇出（affected_sectors 字段）
- 影子模式: SECTOR_ALIGN_SHADOW=true（默认）

**T2 叙事校验者**:
- 反 Sycophancy 批判角色
- 4 项检查: 时态/方向/板块映射/证据支撑
- 批处理一次调用
- 影子模式: NARRATIVE_CRITIC_SHADOW=true（默认，2 周）

**全局→A 股映射（13 组，v1.2）**:  
FOMC/CPI/PCE/NVIDIA/OpenAI/Google/Meta/Apple/Tesla/TSMC+ASML/Eli Lilly+Novo/Oil(OPEC)/Copper(LME)/Gold/SK Hynix+Samsung/SpaceX  
每组: keywords + sector 映射（正+负）+ impact_level + direction + category_tag

**宏观日历规则（4 月度事件）**:  
PMI（月末），CPI/PPI（月中 10 日），社融/M2（月中 13 日），LPR（20 日）

### 8.2 配置（catalyst_config.py, 230 行）

**关键常量**:

| 常量 | 值 | 用途 |
|------|------|------|
| CATALYST_WINDOW_DAYS | 14 | 前瞻窗口（日历日） |
| CATALYST_CONFIDENCE_GATE | 0.7 | 低于此→不参与评分 |
| CATALYST_FULL_REFRESH_INTERVAL | 5 | 全量重建间隔（交易日） |
| CATALYST_MAX_POOL_SIZE | 10 | 埋伏正式池上限 |
| RESERVOIR_EXPIRY_DAYS | 10 | 蓄水池过期（交易日） |
| RESERVOIR_PROMOTION_MIN_DAYS | 2 | 升格最低驻留 |
| RESERVOIR_MAX_SIZE | 15 | 蓄水池容量 |
| IMPACT_WEIGHT | {1:0.3, 2:0.6, 3:1.0} | 影响级别→权重 |
| ANTICIPATION_DECAY_NEAR | 0.6 | ≤2 交易日→衰减 |
| ANTICIPATION_DECAY_NORMAL | 1.0 | 3-10 交易日 |
| REFINE_TOP_N | 10 | LLM 精炼基础 Top-N |
| REFINE_MAX_SECTORS | 20 | 总板块上限 |
| AMBUSH_NOMINATION_MAX | 2 | Agent 提名上限 |
| AMBUSH_NOMINATION_MIN_CONFIDENCE | 0.6 | 提名置信度门槛 |
| AMBUSH_NOMINATION_MULTIPLIER | 0.6 | 催化分数折扣 |

**影子模式开关**:

| 开关 | 环境变量 | 默认 | 用途 |
|------|---------|------|------|
| SECTOR_ALIGN_SHADOW | SECTOR_ALIGN_SHADOW | true | T5 影子（1 周） |
| NARRATIVE_CRITIC_SHADOW | NARRATIVE_CRITIC_SHADOW | true | T2 影子（2 周） |
| AGENT_STOCK_REVIEW_ENABLED | AGENT_STOCK_REVIEW_ENABLED | false | T1 Agent 终审 |
| AMBUSH_NOMINATION_DISABLED | AMBUSH_NOMINATION_DISABLED | "" | 应急回退开关 |

### 8.3 兑现追踪（catalyst_realization.py, 772 行）

**核心**: `CatalystRealizationTracker`
- register_event: 日历构建时注册到归档表
- check_and_record_realization: 每日运行，检测过期事件，计算 T+1/3/5 回报
- get_realization_stats: 按 event_type/source_type/category_tag 分组兑现率
- get_story_lifetimes: 故事寿命统计
- get_pead_analysis_report: PEAD 多窗口分析（均 T+1/3/5，最大窗口，一致率）

**板块名解析（3 层）**:
- L1: 精确反向映射（name→code from TDX_BOARD_REGISTRY）
- L2: normalize_sector_name + 别名变体 → 反向映射
- L3: difflib 模糊匹配（cutoff 0.68）

**三态 resolution**:
- NULL → 未检查
- "unresolvable" → 不可解析（3 层失败）
- 其他 → 已解决

**PEAD 方向逻辑**:
- positive: 回报>0→一致
- negative: 回报<0→一致
- binary: |回报|>1%→一致
- 3 窗口多数→"aligned"; 零匹配→"counter"; 否则→"neutral"

---

## 9. Phase 2: 统一决策节点

**文件**: `src/got/nodes/portfolio_decision.py` (2527 行)

**职责**: 风险评估 + 统一打分 + 组合优化 + 轮动检测 + 埋伏退出信号

**输入**: UnifiedContext + 全部 5 个 node context  
**输出**: `PortfolioDecision`

### 9.1 5 步流水线

1. **风险评估**: 仓位上限/止损/板块限制
2. **统一打分**: 持仓+候选一起评（消除循环依赖）
3. **组合优化**: 规则分配（卖→预算→买 Top3）
4. **轮动检测+拥挤检查**
5. **LLM 精炼**: 1 次调用

### 9.2 Step 1 风险评估

**仓位上限**: 来自 Node 6 final_position_limit  
**回撤缩放**: 0-5% 无变，5-10%→75-100%，10-15%→50-75%，>15%→50%（下限 20%）  
**止损（按 regime）**: bear -5%，transition -7%，bull -10%  
**板块暴露限制**: base × regime_mult × role_mult × confidence × catalyst_coeff
- Regime: bull 1.0，transition 0.65，bear 0.35
- Role: mainline 1.0，ambush 0.55，cooling 0.3
- Catalyst 类型: drift 1.0，fade 0.7，binary 0.4
- Base: actionable 主线 0.40，caution 主线 0.12，actionable 埋伏 0.20，caution 埋伏 0.06

### 9.3 Step 2 统一打分（5 维）

资金面 15% + 消息面 15% + 板块强度 25% + 技术面（波段）25% + 风险因子 20%  
缺失维度→权重重分配

### 9.4 埋伏退出信号（P4, 6 种触发）

| 类型 | 退出触发 | exit_signal |
|------|---------|-------------|
| fade | 事件日次日 | fade_sell |
| drift | 事件后≥10 日+涨幅<2% | drift_exhausted |
| drift | 破 MA20 | drift_break_ma20 |
| drift | 持有>20 交易日 | drift_timeout |
| binary | 事件日>2 日未发生 | event_cancelled |
| 通用 | P&L<-7% | stop_loss |

### 9.5 仓位快照（P4）

- 买入时写入 ambush_position_snapshots 表
- 退出时更新 is_open=False + exit 字段
- 加载: is_open=True 的活跃快照

### 9.6 生命周期卖出信号

- mature_exhausted → 强制卖出
- overheat_severity > 0.7 → 卖出
- late_stage → 减仓 50%
- mature+day>30 → 减仓 50%
- launch/acceleration → 持有（"吃鱼身"）
- mature+PnL>10% → 减仓（锁利）

### 9.7 生命周期买入优先级

launch(≤10d):95，launch(>10d):85，acceleration:70，mature(<20d):40，mature:20，late_stage:5，exhausted:0  
+ 情绪动量: >0.15→+5，<-0.15→-10  
Final = 0.6×lifecycle_priority + 0.4×composite_score

### 9.8 轮动检测

- 老化持仓（late_stage/exhausted/mature>30d）→ 寻找 ambush/launch 替代
- 情绪动量择时: >0.15→加 launch，<-0.15→减 mature

### 9.9 家族拥挤

- ≥3 只同家族→高严重度警告
- 2 只→中警告
- 建议: 减到≤2

### 9.10 LLM 使用

1 次调用，temp=0.3，max_tokens=500，≤300 字推理

### 9.11 DataQuality

追踪 5 节点输入完整度，缺失→权重重分配

---

## 10. GOT 选股流水线

**文件**: `src/services/stock_screener.py` (2912 行)

**入口**: `run_got_mode(got_date, top_n)`  
**消费**: `data/got_daily/YYYYMMDD/` 下 6 个节点 JSON

### 10.1 问财初筛 DDE 策略

- 主线板块: `dde 大单净量>=0.5%`（≤2 只时放松至 0.3%）
- 埋伏板块: `近 5 日 DDE 累计>0`
- 概念→行业关键词映射（100+ 条）过滤"概念蹭热点"股

### 10.2 5 轮漏斗

| 轮次 | 逻辑 | 保留数 |
|------|------|--------|
| R0 | 去涨停（无法买入） | ~30-40 |
| R0b | ≥8% 涨幅软过滤（保底 5 只） | ~25-35 |
| R1 | 排除 exhausted 板块 + overheat_severity>0.5 排除（保底 5 只） | ~20-30 |
| R1.5 | 停滞过滤（4 死区: 3d 0-2%/5d -2~2%/12d 0-10%/15d 0-10%，≥2 窗口→排除） | ~15-25 |
| R2 | 资金面 Top 20（DDE 百分位+板块资金流百分位） | 20 |
| R3 | 波段技术 Top 15（8 维连续评分） | 15 |
| R4 | 风险 Top 6 主线 + ≤2 只保底(>35) | 8 |
| **P5** | **催化剂重排**（仅 ambush，confidence≥0.7） | 不变 |
| R5 | 板块分散（每板块最多 1 只）→Top N | 2-3 |
| **T1** | **Agent 终审**（默认关，只有否决权） | 不变 |

### 10.3 P5 催化剂关联度

- 个股级（earnings）→title 含股票代码→当事股置顶（score=100，direction=negative 跳过）
- 板块级（narrative/global/macro）→sector 匹配→score=confidence×impact×proximity×direction_coeff×50
- 仅 ambush 板块参与，主线保持原序

### 10.4 T1 Agent 终审

- 默认关闭: AGENT_STOCK_REVIEW_ENABLED=false
- 只有否决权: reject→降级移末位（不删除）
- 预算: AGENT_STOCK_REVIEW_MAX=2 次调用
- 串行+3 次重试

### 10.5 5 维评分权重

资金面 15% + 消息面 15% + 板块强度 25% + 技术面（波段）25% + 风险因子 20%

### 10.6 停滞校准标记

- _STAGNATION_CALIBRATION_DATE: "2026-06-17"
- _STAGNATION_SAMPLE_COUNT: 44
- TODO: 每 3 个月重新校准

---

## 11. 持久化与存储层

### 11.1 SQLite 表（8 张 GOT 相关）

| 表名 | 写入方 | 用途 | 状态 |
|------|--------|------|------|
| node2_sector_trends | Node 2 | 板块轨迹（6 态+RRG EMA+生命周期+强度轨迹 JSON） | 活跃 |
| sector_frames | Node 2 | 主线框 | **已废除（2026-08-31）**，保留审计 |
| node4_events | Node 4 | 事件生命周期（emerging→archived，7 日自动归档） | 活跃 |
| catalyst_events | CatalystCalendar | 催化剂事件（event_id UPSERT，14 日窗口） | 活跃 |
| catalyst_refresh | CatalystCalendar | 刷新节奏（单行表，全量 5 日/增量其余） | 活跃 |
| ambush_position_snapshots | Phase 2（P4） | 埋伏仓位快照（买入+退出+复盘） | 活跃 |
| catalyst_realizations | P6 | 催化兑现归档（预期 vs 实际+PEAD+故事寿命） | 活跃 |
| node4_raw_news | Node 4 | 原始新闻（title 归一化+Jaccard 去重+跨日发酵） | 活跃 |

### 11.2 文件持久化

| 路径 | 写入方 | 内容 |
|------|--------|------|
| data/got_daily/YYYYMMDD/ | archive.py | 6 个节点 JSON + reasoning.md + meta.json + portfolio_decision.json + trading_recommendations.md |
| data/aggregator_contexts/previous_context_*.json | persistence.py | Node 6 跨日 UnifiedContext |
| data/aggregator_contexts/ema_baseline.json | persistence.py | EMA 基线（α=0.3，冷启动 5 日） |
| data/got_timeseries/monitoring.json | persistence.py | 生产监控（告警阈值） |

### 11.3 跨日记忆（v2.1）

- 资金流时序: load_capital_flow_series(days=5) → 板块+DDE 日序列
- Regime 状态机: load_previous_regime_state() → dwell_days/transition_count
- 情绪阶段: load_previous_stage_state() → current_stage/dwell_days
- 情绪分数历史: load_sentiment_score_history(days=20) → AR(1) 计算
- 最大连续缺失: _MAX_CONSECUTIVE_MISSING=5（超过→序列降级为 None）

### 11.4 生产监控告警

| 指标 | 条件 | 严重度 |
|------|------|--------|
| flow_5d_non_null_rate | <0.8（冷启动保护:total_boards≥10） | 🔴 critical |
| regime_switch_count_20d | >8 | 🟡 warning |
| contrarian_alert_count_30d | >10 | 🟡 warning |
| cross_node_signals 30d | 全零 | 🟡 warning |
| last_successful_run | >3 交易日 | 🔴 critical |

---

## 12. 配置与开关

### 12.1 催化剂系统常量

见 §8.2 配置表

### 12.2 影子模式开关

见 §8.2 影子模式开关表

### 12.3 其他散落配置

**Node 2 常量**:
- AMBUSH_TRACK1_MAX: 16
- AMBUSH_TRACK2_TOP_K: 8
- AMBUSH_GATE_THRESHOLD: 0.30
- RESERVOIR_EXPIRY_DAYS: 10
- RESERVOIR_MAX_SIZE: 15

**Node 3 常量**:
- DISABLE_NODE3_MULTIDAY: false（回退开关）

**Node 6 常量**:
- EMA_ALPHA: 0.3
- EMA_COLD_START_DAYS: 5
- REFINE_TOP_N: 10
- REFINE_MAX_SECTORS: 20

**选股流水线常量**:
- _STAGNATION_CALIBRATION_DATE: "2026-06-17"
- _STAGNATION_SAMPLE_COUNT: 44

---

## 附录 A: LLM 调用总结

**总计每轮**: 12-15 次 LLM 调用

| 节点 | 次数 | temp | max_tokens | 用途 |
|------|------|------|------------|------|
| Node 1 | 1 | 0.3 | 200 | 市场摘要 |
| Node 2 | 1 | 0.3 | 400 | 板块总结 |
| Node 3 | 1 | 0.3 | 300 | 资金分析 |
| Node 4 | 4 | 0.2-0.5 | 300-4000 | 事件提取+概念发现+风险+推理 |
| Node 5 | 1 | 0.3 | 600 | 情绪推理+热门话题 |
| Node 6 | 3-6 | 0.2-0.3 | 300-500 | 精炼+新闻覆盖+矛盾仲裁+提名 |
| Node 7 | 1 | 0.3 | 500 | 决策推理 |
| 催化剂 | 3 | 0.2-0.3 | 400-2000 | 叙事提取+T5 对齐+T2 校验 |

---

## 附录 B: 关键常量速查

**Node 1**:
- 趋势阈值: ±0.3
- 仓位范围: [0.2, 1.5]
- 波动率目标: 0.15（年化）
- 波动率回看: 20 日
- 暴跌阈值: -3%
- 置信度下限: 0.05

**Node 2**:
- RRG 阈值: 0.5/0.5
- 埋伏门槛: 0.30
- Track1 上限: 16
- 冷启动惩罚: len/5.0
- 方向惩罚: 0.9^跌幅（下限 0.3）

**Node 3**:
- 豁免维度: northbound_realtime
- 主线加分上限: 0.15

**Node 4**:
- MAX_MERGED_NEWS: 150
- 去重阈值: 0.45
- 事件归档: 7 天

**Node 5**:
- 恐贪指数: (score+1)/2
- 分歧度反向: <0.35 AND (mean>70 OR mean<30)
- AR(1) 高持久: >0.7

**Node 6**:
- EMA_ALPHA: 0.3
- 冷启动预热: 5 日
- Z-score critical: >3.0（预热 5.0）
- Z-score high: >2.0（预热 3.5）

**催化剂**:
- CATALYST_WINDOW_DAYS: 14
- CATALYST_CONFIDENCE_GATE: 0.7
- RESERVOIR_EXPIRY_DAYS: 10
- RESERVOIR_MAX_SIZE: 15

**选股**:
- DDE 主线: ≥0.5%（放松 0.3%）
- 停滞校准: 2026-06-17

---

## 附录 C: 学术参考

- **Moreira & Muir 2017**: 波动率目标仓位
- **Freyberger et al. 2022 (NBER)**: 缺失维度权重重分配
- **Han et al. 2022**: AR(1) 情绪持久性
- **国金证券**: 情绪分歧度选股
- **清华**: 投资者情绪指数
- **fffinstill**: Fear & Greed Index 方法论
- **FINCON (NeurIPS 2024)**: Manager-Analyst 层级决策
- **RAPTOR (NeurIPS 2025)**: 组合构建是优化问题
- **Debate-or-Vote (NeurIPS 2025)**: 投票优于辩论
- **ThinkSum (ACL 2023)**: 概率聚合在 LLM 外部
- **ConsensAgent (ACL 2025)**: 反 Sycophancy
- **VOTING/ISP (2024)**: Surprising Popularity
- **Minervini 2016**: Relative Rotation Graph 四象限
- **Janssen 2020**: RS-Momentum 领先 RS-Ratio
- **Kang 2023**: MACD 背离渐进式
- **Hsu 2016**: 天量滞涨=分配信号
- **O'Neil**: Stall Day（高位放量滞涨=机构派发）
- **Liu & Wang 2017**: 跨节点时间线共振
- **PEAD 文献**: 催化剂兑现多窗口分析

---

**文档结束**
