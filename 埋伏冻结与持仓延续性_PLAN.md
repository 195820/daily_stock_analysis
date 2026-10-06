# 实施计划：埋伏通道两棒冻结 + 持仓延续性管理

日期：2026-09-30 ｜ 状态：待实施（已按当日 ultra 审核修订 v2：3 Critical / 4 Warning / 3 Suggestion 全部吸收） ｜ 前置结论出处：当日全流程实跑（data/got_daily/20260930/）

---

## 背景与裁决记录

### 埋伏通道现状（实测读数）

20260930 漏斗：`7 候选 → G1 全过 → G4 吸筹：6 skipped(flow_data_missing) + 1 evaluated 被拒 → final_pool=0`。
根因：Node 3 板块资金流对埋伏名单覆盖仅 1/7。relations 补挂（已上线）治好事件漏配
（7 漏 → 救回 2），但救回的电气设备/水务仍死于 G4 缺资金流数据。

**负责人裁决（2026-09-30）**：
1. 不做别名映射补覆盖——A5/A6 黏合已负责"名字→板块"层，G4 缺的是数值流水，映射救不了；
   埋伏名单日间高轮换（今日 7 码与昨日 10 码零交集），追码不可持续。
2. **资金闸门（G4）先冻结**，埋伏先有输出再谈别的。

### 冻结必须两棒一起改（否则白冻）

下游核实发现第二道同根卡点：埋伏候选的 SectorIntel 全部 `caution`（规则评分退役后
composite 只垫技术面中性分），而 Phase 2 选股入口只放行 `actionable` 板块
（portfolio_decision.py L3479-3482 `sector_ctx_dict["ambush"]` 按 actionable_names 过滤）。
caution→actionable 唯一出口"确定性提升"的条件 B 要求
`accum_strength ∈ {strong, medium, weak}`（aggregator.py L2160），missing 不满足。
**只冻 G4 不放宽提升条件 → 入池了也选不出股，输出依旧为空。**

---

## 第一部分：埋伏通道两棒冻结（优先级 P0，先做）

### 开关设计

- 新增环境变量 `GOT_AMBUSH_G4_FREEZE`，缺省 `"0"`（关闭=现行为完全不变）。
  审核修正：原缺省 `"1"` 等于合入即改变生产行为、无灰度窗口，而回放样本仅覆盖
  9 月单一 regime——上线动作改为在 `.env` 显式置 `1` 开启，回滚置 `0` 或删变量即恢复。
- 常量落 `src/got/catalyst_config.py`：`AMBUSH_G4_FREEZE = env 读取`。

### 改动点（均在 src/got/nodes/aggregator.py）

**① G4 missing 冻结放行（位置：`_run_ambush_gates` L2232-2239 + 乘数表；主循环仅计数适配）**

审核修正（Critical #3：原稿"改 L2745-2760 分支"在控制流上不可达）：主循环有
两层 `if not gate_passed:` 块，第二层 L2762 `continue` 直接跳出循环，在块内做任何
放行改造都到不了 L2767+ 的 B 公式打分入池段。正确改法：

- `_run_ambush_gates` 新增参数 `freeze_missing: bool = False`（调用方显式 opt-in，
  防止波及升格支路，见 ②）；L2237-2239 的 missing 分支在 `freeze_missing=True` 时返回
  `True, "", "G4冻结放行(无资金流证据)", accum_strength, evidence`——视同过闸；
- 主循环调用（L2679）传 `freeze_missing=AMBUSH_G4_FREEZE`；
- 放行后天然可达：L2767+ 催化剂排序（无合格催化仍落 no_catalyst 蓄水池）、B 公式
  打分入池、Top-K 截断均沿用现路径；G1 利空/G2 防买顶/G3 拥挤三硬否决在吸筹之前
  return，完全不受影响（原 L2748-2750 对 missing 的利空补检与 G1 是同一
  `_check_negative_catalyst`，冻结后利空优先语义不变）；
- 乘数防倒挂修正（Critical #1：原"missing 映射 medium ×0.8"自相矛盾，0.8 仍高于
  weak 的 0.6）：乘数表直接新增 `"missing": 0.5` 键（严格低于 weak），提为模块级
  常量 `AMBUSH_ACCUM_MULTIPLIER`，主循环 L2803 与升格支路 L2528-2530 两处查表共用，
  禁止各写各的；查表直接用真实 `accum_strength`，不做映射改写；
- 入池条目 `gate_notes="G4冻结放行(missing×0.5)"`，`accum_strength` 字段保留真实值
  "missing" 不改写（数据诚实，降权只发生在乘数与条件 B 判定处）。

**② 升格支路：明确不共用冻结（审核 Warning #7 对策，原稿"共用同一判定"废弃）**

升格 `_check_reservoir_promotion`（L2519 调 `_run_ambush_gates`）传
`freeze_missing=False`：冻结只对当日候选生效。若共用，蓄水池 9 个 data_missing
存量条目（大多已满足驻留天数+强度回升条件）会在冻结首日一次性升格涌入，
挤占 CATALYST_MAX_POOL_SIZE 干掉当日新鲜候选——升格通道的语义是"资金流真实恢复
或强度回升才算证据"，"依然无数据"不构成升格理由。存量条目继续随
days_in_reservoir 老化自然出清；若某日重新进入当日候选名单，走主循环冻结放行。

**③ 确定性提升条件 B 放宽（L2152-2167）**

冻结开关为真时，条件 B 接受 `accum_strength == "missing"`：
```
if accum_strength in ("strong", "medium", "weak") or nominated \
   or (AMBUSH_G4_FREEZE and accum_strength == "missing"):
    has_evidence = True
```
- 提升成功的 reasoning 追加 `[冻结期提升-无资金准据]`（可审计、可回放归因）；
- 条件 A（合格日历催化 ≥ CATALYST_CONFIDENCE_GATE）**不动**——无催化不该埋。

**④ 蓄水池分流收尾**

- 主循环不再新增 `reason="data_missing"` 蓄水池条目（路径被 ① 接管）；
- 存量 data_missing 条目不特殊处理，随 `days_in_reservoir` 老化自然出清；
- `_merge_reservoir` 中 data_missing 免于"不在近失名单→除名"的特殊豁免
  （L2349-2352）**保留不动**：仅服务存量出清期间，随存量清零自然闲置，防后续
  维护者误判为死代码删除；
- `_merge_reservoir` 容量驱逐逻辑不动。

**⑤ 熔断与漏斗口径适配（审核 Warning #4/#5）**

- `G4_吸筹` 漏斗 dict 初始化（L2629）预置 `"frozen_released": 0` 键——`_gate_stat`
  实现为 `entry[kind] += 1`（L2636），不预置直接 KeyError；
- G4 计数分支适配：gate_passed 且 `accum_strength == "missing"` 时计
  `frozen_released`，**不虚计 evaluated/accepted**（G4 确实未评估，漏斗保持诚实；
  L2710-2711 的 else 分支需加此判断）；开关关闭时 L2697-2699 的
  `skipped(flow_data_missing)` 留痕照旧；
- 轨道三比例熔断行号修正：实际在 **L2908-2914**（原稿"L2760 附近"错误）；冻结
  生效后 missing_count 递增点（L2761）被跳过，熔断条件恒 False 自然退役——
  数据源健康观测口径改为 `frozen_released / initial` 占比，占比过半时打一条
  INFO："冻结期轨道三静默，数据源健康度看 frozen_released={n}"，WARN 级别
  故障告警保留给开关关闭态。

### 预期效果（以 20260930 数据回放口径估）

- 电气设备、水务（relations 救回事件 + missing）：入池 → 提升 actionable → 选股 ✓
- 其余 5 个 missing：入池但条件 A 不过，维持 caution、不选股（符合"无催化不埋"）；
- 小金属（有数据、真净流出 9.8 亿）：照旧被 G4 拒——冻结不放行"坏信号"，只放行"无信号"。
- 埋伏输出从 0 → 约 2 个板块。产出天花板 = 当日有事件覆盖的候选数（relations/日历
  覆盖率决定，非本计划范畴）。

### 历史回放实测（2026-09-30，0724-0930 归档，.review_tmp/bt_event_aligned.py）

放行组质量验收结论：**过线，支持实施**。

| 口径 | T+5 | T+10 |
|---|---|---|
| 原始收益（含大盘 β，首轮误判来源） | 31% / -0.88% | 82% / +3.57% |
| **超额收益（减全A基准 881100）** | **70% / +2.03%** (n=23) | **86% / +7.62%** (n=14) |

- 首轮"T+3 胜率 25% 很差"是 β 幻觉：9 月上旬大盘调整稀释了板块表现；
  左侧前 3 天磨、第 5 天起超额转正、第 10 天超额显著——与埋伏"左侧吃漂移"定位自洽；
- 样本全部集中于 9 月板块轮动风格期，无跨 regime 稳健性，实施后需影子积累复核；
- 事件锚口径回放失败（不能作为本轮判据，但暴露了管线缺陷）：43 个信号 34 个"无锚"，
  根因是日历 `affected_sectors` 板块名↔880 板名匹配落空（生产层 relations 补挂解决的
  同一个问题在回测层重现）；仅有的 7 个锚中含窗=0 日假锚与 1 例实锤误锚
  （化工机械→农业板块事件）。→ 锚重建为 B4-1 新前置（见下文）。

### 测试

- 修改 `tests/test_node6_ambush_funnel.py`（审核修正：原稿文件名 glob 不匹配）：
  missing 候选 `freeze_missing=True` → `_run_ambush_gates` 返回 passed + 入池 +
  frozen_released 计数且不虚计 evaluated；`freeze_missing=False` → 旧蓄水池分流
  行为不变（双态各一条）；
- 乘数表断言：missing 候选入池后 adjusted_score 按 ×0.5 计，严格低于同催化分的
  weak 候选（防倒挂回归）；
- 升格支路 `freeze_missing=False` 不波及用例：蓄水池 data_missing 存量条目即使满足
  驻留+催化+回升三条件，冻结开启日仍不升格（Critical #7 守护）；
- 修改确定性提升用例：missing + 开关开 + 有催化 → actionable 且 reasoning 带冻结标注；
  missing + 开关关 → 不提升；
- 全量回归：ambush / glue / reservoir / node6 / gate1 套（上轮基线 183 passed）。

### 验收

conda 环境（D:\ProgramData\miniconda3\envs\daily_stock_analysis）跑 `main.py --got` 全流程
（timeout: 0，按 GOT 运行规范出四段式总结）。开关缺省关闭，验收前需在 `.env`
显式设 `GOT_AMBUSH_G4_FREEZE=1`。核对：
`ambush_gate_funnel.final_pool > 0`、frozen_released 计数（且 G4 evaluated 未被虚增）、
升格日志无 data_missing 存量条目涌入、reasoning.md 埋伏段落非空、
Phase 2 有埋伏买入分配及快照写入；再跑一轮开关关闭态确认行为与现网零 diff。

---

## 第二部分：持仓延续性管理（优先级 P1，埋伏出输出后做）

### 背景缺陷（当日核查结论）

1. 主线持仓退出是纯状态驱动，无"持有天数/盈利回吐"维度——板块强、个股阴跌的
   钝刀行情只靠硬止损兜底；无移动止盈，acceleration +15% 回撤可一路坐到 late_stage。
2. 天数阈值与实证错配：`_lifecycle_sell_signal` 的 mature 减仓门槛 `day > 30`
   （portfolio_decision.py L2164）——实证 93.5% 主线 day7 前结束，该阈值实际打不到，
   形同虚设；衰减折扣断点 day8-10 同样偏高。
3. 波次拼接前科：mainline_days 跨冷却期累加，长寿命样本是多波拼接值——
   **绝对天数断点不得写死为确定性规则**（既有 pitfall 记忆），只准做可配参数 + 观测。

### 改动点

**B1 主线移动止盈（P1 核心，先行）**

- 位置：`_lifecycle_sell_signal` 增加回撤离场分支 + `_score_holdings` 取回撤数据。
- 高点口径（审核修正 Critical #2：原稿"复用 `_batch_fetch_tech_scores` 已取的 K 线、
  零额外请求"事实错误——该函数返回 `Dict[str, float]` 分数，K 线在
  TechnicalIndicatorService 内部消费后不传出）：新增一次批量 IO——
  `_score_holdings` 内经 DataFetcherManager 批量拉持仓股日 K（60 根，走 fetcher
  缓存层，延迟量级与现有 tech 分数取数相当，在 PLAN 中明示不再伪装零成本）；
  取入场日以来 close 最大值（入场日取自 thesis/埋伏快照表；无快照的存量仓
  用 K 线窗口内最高点近似并在 reasoning 标注 `[近似HWM]`；K 线缺失 → 追踪
  不生效、动作退回现逻辑）。
- 规则（参数全部可配，落模块级常量）：
  - `TRAILING_ARM_PCT = 0.10`：盈利超过 10% 后激活追踪；
  - `TRAILING_DRAWDOWN_REDUCE = 0.08`：自高点回撤 8% → reduce（减半锁盈）；
  - `TRAILING_DRAWDOWN_SELL = 0.12`：自高点回撤 12% → sell；
  - 未激活（盈利 <10%）时不生效，维持现状。
- 硬止损优先级不变：先查 stop_loss_threshold，再查追踪。
- 埋伏仓不改（P4 催化剂退出已有 drift_break_ma20 类近似，避免双重触发）。

**B2 天数断点校准（保守幅度 + 上线前盘点，审核 Suggestion #10）**

- mature 减仓 `day > 30` → `day > 14`，常量提为 `LIFECYCLE_MATURE_REDUCE_DAY`
  可配。不做激进的 day 4-5（实证样本有混池/拼接污染前科，先对折一步，
  按 day 分箱落日志积累 2-3 个月后回归校准——沿用既有工程建议）。
- **上线前盘点步骤**：切换常量前先查现持仓中 `lifecycle_phase=="mature"` 且
  `lifecycle_day ∈ (14, 30]` 的板块清单；若存在，先保持常量=30 仅落
  "本应触发 reduce"观测日志一周，确认冲击可接受后再切 14，避免生效当日
  集中一次性 reduce。
- `_lifecycle_priority_score` 的 acceleration day8+ 档不动（买入侧排序，风险低）。

**B3 持有天数观测字段（零行为影响，随 B1 一起上）**

- `UnifiedScoringResult` 新增 `days_held: Optional[int]`、`drawdown_from_hwm: Optional[float]`；
- `_score_holdings` 填充，reasoning 追加 `[持有N日, 高点回撤M%]`；
- 归档落 portfolio_decision.json，供后续校准。纯观测，不参与动作判定（B1 除外）。

**B4 事件锚定时标：T+X 不写死（文献调研结论，2026-09-30）**

文献依据（联网调研）：
- PEAD（Bernard & Thomas 1989；arXiv 2512.00280 2025）：漂移窗长由信息消化难度与
  投资者期限构成内生决定（个股 60-90 日、高关注度板块事件 4-7 日），无通用日历窗；
- hard/soft catalyst 类型学（CFA L2 事件驱动框架）：hard（确定日期事件）退出锚=事件解决
  本身，soft（无时点预期）才需要时间/波动止损兜底——对应本项目 fade/binary（hard）与
  drift（soft）三分类，P4 现有事件锚定出口方向正确，问题只在 drift 参数写死；
- buy-the-rumor-sell-the-news（Leach & Francis 1986；Frazzini et al. 2012）：fade 型 alpha
  窗在事件日前，用事件后固定窗（T+3）度量 fade 信号属口径错误；
- 事件研究法 CAR 标定：按事件日对齐样本、分事件类型算 CAAR 曲线，取边际超额收益
  由正转零的拐点为该类型最优持有窗——X 是估计量，不是设计量。

落地改法：
1. **锚重建前置（回放实测新增，阻塞 B4-1）**：从历史归档还原事件锚时，日历
   `sector`/`affected_sectors` 与 880 板名的直接包含匹配实测覆盖仅 9/43（玻璃/陶瓷/MLCC
   等有事件的强催化板块全部漏匹配），且存在跨板块误锚。必须先复用生产侧同一套
   匹配件（relations 板码直连 + `board_aliases.normalize_sector_name` + 模糊匹配），
   回测/标定管线与生产共用匹配逻辑，禁止另写一套简化版；窗=0 日的已落地事件
   不得计作前瞻锚（binary/fade 同日锚无持仓含义，直接归无锚桶）；
2. 评价/回测口径改造（锚重建后启用）：埋伏信号质量评估弃用固定 T+3/T+5，改为
   事件对齐窗口——fade/binary 用 [入场日, 事件解决日+宽限]，drift 用分类型 CAAR 峰位窗；
   无论哪种窗口，**一律用减基准超额收益**（实测：原始口径与超额口径胜率差 40pp，
   不减基准会把大盘 β 当信号质量）；
3. drift 型退出参数自适应（替代 STALL=10/TIMEOUT=20 写死）：
   - 数据充足后：按 category_tag × impact_level 标定 CAAR 拐点日，写回 P4 参数表
     （L2237-2240 的 TODO「归档积累后校准」即此路径，样本阈值每类 ≥30 事件）；
   - 冷启动期（当前快照样本=0）：用注意力衰减代理变量——板块热度/换手率相对衰减
     半衰期（Da-Engelberg-Gao 2011 口径）或 ATR 归一化天数（高波动→扩散快→窗短），
     在 10-20 日区间内浮动，不再取常数；
4. B2 的天数断点（day>14 等）降级为观测分箱，不升级成动作规则——主线/板块级窗短是
   注意力高扩散的结果，动作判定仍以阶段转换与事件解决为主锚；
5. 依赖与顺序：锚重建（1）→ 影子快照积累（已有）→ 事件对齐收益落表
   （`ambush_position_snapshots` 扩展 exit_realized_return 与锚定窗字段）→ 标定脚本复用
   .review_tmp\bt_freeze_dedup.py 的 K 线缓存管线 → 每季度回归校准一次。

### 测试

- 移动止盈矩阵用例：激活/未激活 × 回撤 8%/12% × 硬止损优先级；
- 无快照存量仓近似 HWM 路径；K 线缺失 → 两字段 None、动作退回现逻辑（no-op 保证）；
- day>14 边界用例；归档字段序列化断言。

### 验收

全流程实跑，影子口径自查：持仓动作变化逐条能归因到 B1/B2 规则（无静默变化），
终端四段式总结中"风险"段列出所有被追踪止盈触发的 reduce/sell。

---

## 实施顺序与边界

1. **先做第一部分（埋伏冻结）**——"埋伏先有输出"是本轮唯一硬目标；
2. 实跑验收通过后启动第二部分（持仓延续性）；
3. 本计划不做的事：Node 3 资金流覆盖接管/别名扩表（裁决已排除）；
   生命周期波次重构（独立大项，另立计划）；埋伏名单轮换率优化（观测项）。
4. 冻结是**临时闸门形态**：frozen_released 计数长期留痕，待资金流数据源修复
   （独立课题）后在 `.env` 移除 `GOT_AMBUSH_G4_FREEZE=1` 即恢复原口径（缺省即关闭，
   回退成本为零）。
