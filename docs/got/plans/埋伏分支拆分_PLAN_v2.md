**状态**：已实施 2026-10-13 | Step 0 门 PASS | 影子期 ACTIVE=0 | 转正判据见 §5.2 | supersedes 已实施的原 v3 ambush_split_plan.md 之上的本拆分设计

# 埋伏分支 Phase 2 整体拆分计划（修订版 v2）


- **slug**: `ambush-lane-split-revision`
- **日期**: 2026-10-12
- **状态**: 已批准并实施（2026-10-13，见文首状态头；「待批准」为归档时原文）
- **取代**: 仓库根 `埋伏分支拆分_PLAN.md` 草稿（169 行）；批准后由实现者把本文件内容落为正式计划并归档草稿
- **修订动因**（用户指出的四块缺口）：
  1. 并入 2026-10-12 修复预演结论（`docs/reports/20261012_黄金票出局根因与窗口修复预演.md`）
  2. 新增「实施前方案的胜率测试」——拆分方案落码前先离线验证 G-A1~G-A4 参数与埋伏道选股胜率
  3. 探测并处置 Phase 2 中非 Node 6 的接线（Node 7 / 漏斗外消费方），避免埋伏道票拆分后被静默杀死（黄金票出局同类现象）
  4. 对拆分计划整体修订

## 0. 修订摘要（相对草稿的增量）

| # | 增量 | 来源 | 落点 |
|---|---|---|---|
| 1 | Step 0「实施前胜率测试」整节新增：双层设计（Layer 1 板块级离线重放·17 可用信号日 / Layer 2 影子期逐股对照·预注册判据），run card 纪律，预注册验收阈值 J1-J4，**J1 不通过禁止落码** | 用户要求 #2 + 本次数据盘点 | §4 Step 0 / §5 |
| 2 | Node 7 与漏斗外接线**逐拦点处置表**（14 个拦点，每个给：位置 / 触发条件 / 处置三选一：分道豁免・共用+trace・保持） | 用户要求 #3 + 两路侦察（Node7Gates / ScreenerWiring）+ 本人复核 | §3.1 + §4 Step 6 |
| 3 | Schema 接线修复：`PortfolioDecision` 无 `model_config` → Pydantic v2 默认 `extra='ignore'`，新 shadow 字段不声明即**静默丢弃**；`UnifiedScoringResult` 缺 `sector_type` → 埋伏身份在 Phase 2 输出断链 | 侦察 + 本人复核字段清单 | §4 Step 5 + §3.1 #5/#6 |
| 4 | G-A2 失败原因三分（`no_catalyst` / `low_confidence` / `position_unsafe`）：20261009 实测 16 个埋伏板块仅 5 个有日历事件，"日历无事件"与"置信度不达标"必须分开统计，否则转正判据会被供给面污染 | `埋伏池归零下游拦点分析_20261010.md` | §4 Step 2 |
| 5 | Verification 断言①加 pre-T1 caveat：预演无 LLM → T1 未跑，归档重放断言只对 pre-T1 名义终选成立 | 20261012 预演报告 | §1.2 / §6 断言① |
| 6 | 预演结论并入证据链：bug 4（`_score_capital` 半场读当日键）根因 + 黄金票死于 R3 的端到端实证 → D2 从假设升级为实证 | 20261012 预演报告 | §1.2 / §1.3 修订 1 |
| 7 | bear 日收手闸门豁免口径修正（D7）：现实现 `_is_ambush` 仅豁免蓄水池旁路板块（`_bypass_ambush_sectors`），Node 2 埋伏名单板块在 bear 日 buy→watch——与拆分设计意图不符 | 侦察 M3 + 本人复核 `portfolio_decision.py:4187-4192, :3951-3963` | §2 D7 + §3.1 #2 |
| 8 | G-A1 个股腿在问财路径上近乎同义反复的说明（问财初筛已含 `近5日DDE合计>0`），G-A1 真正新增的证据是板块 3d 流腿 + 降级票拦截 | 本人复核 `stock_screener.py:1150-1153, :1343-1352` | §3.2 + §4 Step 2 |
| 9 | 跨道撞票规则显式化（D1'）：universe 去重主线优先（`:1064, :1102-1108`），一票两道时归主线道，埋伏道不重复入选，撞票计数进 trace | 侦察 + 本人复核 | §2 D1' |

## 1. Context

### 1.1 问题陈述

选股漏斗的 5 轮结构（R0→R0b→R1→R1.5→R2→R3→R4→R5 + P5 重排 + T1 终审）为右侧动量票设计。埋伏票（左侧吸筹、横盘整理、催化在前方）在该漏斗中被结构性错杀：R3 波段技术 Top15 硬截断按 `tech_wave_score` 排序，其 8 维全是右侧动量指标（MA20 斜率、布林位置、RSI、20 日突破、MACD 金叉、均线多头……），横盘吸筹期全部低迷。

2026-10-12 黄金票事件是完整实证：黄金概念 3d 板块净流 +9.28 亿（真实吸筹），当日板块 −2.67%（回调日），600988 赤峰黄金 / 000975 山金国际两只黄金票——即使修复 bug 4 让资金面正确读到 3d 窗口（赤峰黄金 capital≈87.2 分、黄金 10 只进 R2 Top20），端到端接线后仍然**死于 R3 Top15**。结论：bug 4 修复单独落地救不了黄金票；埋伏票需要独立的入场分道，不与主线票共用右侧技术闸门——这是本拆分计划的核心依据。

### 1.2 已核实现状（含预演结论并入）

**预演根因（bug 4，独立登记、独立拍板）**：
- `_score_capital` 板块流半场读当日 `sector_flow_all`（90 键），应为 3d 窗口 `sector_flow_all_3d`（477 键，`capital_analysis.py:435-440`）；三级匹配 miss 静默 0 分，应复用 `AggregatorNode._lookup_flow_map`（含 `capital_flow_aliases.json` 61 条别名）。
- 反事实（仅修 bug 4）：3d 窗口下赤峰黄金 capital≈87.2 分（#1），黄金概念 10 只进 R2 Top20。
- 端到端（修复接线后）：黄金 2 只进 R2 短名单 → **死于 R3 Top15**（`stock_screener.py:1593-1595`，`candidates.sort(key=lambda x: x["tech_wave_score"], reverse=True)[:15]`，本人已复核）。
- 修复覆盖 12/19→17/19 板块（有色/福建无代理板，如实 0 分 + WARNING）。
- 已知代价：信号反转——传媒娱乐 当日 +13.18 亿 → 3d −11.46 亿（固有代价，非缺陷）。
- 回归锚点：20261012 归档重放断言赤峰黄金 capital≈87.2；落码前须做「纯三级匹配 vs +`_lookup_flow_map`」未命中名单对比，miss 补进 `capital_flow_aliases.json`（聚合器同受益，双消费方）。
- bug 4 与本计划边界（A3）：G-A1 的板块流腿用 3d→当日 fallback 链**独立工作**，不依赖 bug 4 落码；bug 4 落码后自动同源。

**生产 vs 预演终选差异（Verification 断言 caveat）**：生产 15:11 终选 = *ST中地（000736，被 T1 reject 降级移末位）/怡亚通/捷成股份；预演无 LLM → T1 未跑，终选 = 000736 / 恩捷股份 / 安泰集团。归档重放的「与生产一致」断言只能对 **pre-T1 名义终选**成立（或两侧一致禁用 T1）。

**事件供给结构性风险（G-A2 现实约束）**：20261009 实测 16 个埋伏板块仅 5 个有日历事件（2 直查 + 3 glue 补挂），11 个整链零事件；静态映射表 13 条偏科技/半导体，与资源/周期类名单交集≈2（有色/黄金）；macro_rule 10 条 affected_sectors 全空。⇒ G-A2 的 `no_catalyst` 会大量触发，属供给面现实而非闸门缺陷；trace 三分失败原因正是为了把这一现实量化出来。

### 1.3 理论与实证依据（承接草稿 §一，两处修订）

- 草稿 §一 的 Academic/Industry/组合构建三条依据**全文保留**（PEAD/pre-event run-up 文献支持「事件前异常资金流存在、右侧动量不必然出现」；AQR 分道-汇总共识支持双道结构）。
- **修订 1（R3 实证升级）**：草稿写「14 只黄金票全部死于 R2 之前或 R3 技术闸门」时 R3 截断尚属推断；20261012 预演已给出端到端实证——修复资金面接线后黄金票仍死于 R3 Top15（见 §1.2），D2（埋伏道不设右侧技术分）由设计假设升级为**实证结论**。
- **修订 2（供给面约束）**：理论假设「催化剂日历可提供临近度信号」在当前供给面下只对约 1/3 埋伏板块成立（20261009 实测 5/16；17 个可用信号日上 218 个埋伏板块日中 26.6% 有 14 日内名义匹配事件，本人已量测）。这不是否决 G-A2，而是把「日历无事件」与「置信度不达标」分开计数（§3 Step 2），并给 Layer 1 报告强制供给面读数（§3 Step 0），转正判据以「有事件子集」为准。

## 2. 关键决策（D1–D7）

承接草稿 D1–D6 全部保留（原文引用，不重述）：D1 只拆分道不拆风险排除；D2 埋伏道不设右侧技术分；D3 排序键=催化临近度；D4 汇总层主线优先；D5 影子先行（ENABLED=true / ACTIVE=false）；D6 板块名单仍取 Node 2 `sector_ctx["ambush"]`。

**修订与新增**：

- **D1'（新增，跨道撞票）**：universe 去重现按 `qualified_mainlines + qualified_ambush` 顺序遍历（`stock_screener.py:1064`、`:1102-1108`），一票双板块（主线+埋伏）时归主线道——**保留该语义**，撞票不重复入选，埋伏道 trace 记 `cross_lane_collision: n` 供统计。理由：主线是当日确认的更强信号（与 D4 同哲学），且去重顺序改动会污染主线道存量行为，违背影子零污染原则。
- **D7（bear 闸门豁免口径修正）**：现实现 `_is_ambush`（`portfolio_decision.py:4187-4192`）仅豁免 `_bypass_ambush_sectors`（`:3951-3963`，只收「ambush_pool 中不在 Node 2 ambush 名单」的蓄水池升格板块）——**Node 2 埋伏名单板块在 bear 日 buy→watch，与拆分设计意图相悖**（拆分后埋伏道过闸票应视为已审票）。处置：`GOT_AMBUSH_SPLIT_ACTIVE=true` 时 `_is_ambush` 扩为「蓄水池升格 ∪ 当日埋伏道 G-A1~G-A4 全过闸板块名单」（名单由埋伏道产物传入，见 Step 6 拦点表 #2）；ACTIVE=false（影子期）不改，bear 日 watch 行为照旧并进 trace（`bear_gate_watch: [codes]`），作为影子期观测数据点。理由：影子期零行为污染优先；豁免口径变化属 ACTIVE 模式的配套行为改动，必须与拆分转正同拍。
- **D-M1/M2（仓位乘数，维持共用）**：`ROLE_MULTIPLIER["ambush"]=0.55`（`portfolio_decision.py:44`）与埋伏类型化系数（drift×1.0 / fade×0.7 / binary×0.4）保持不变。0.55 是**确信度乘数**（进 `sector_exposure_limits` 公式），非敞口上限；无执行消费者单独引用。影子期 trace 观测埋伏道票实际仓位乘数链路（role→multiplier→exposure_limit），出现「拆分后埋伏票仓位被压到不可执行」再议，不做预防性改动。

## 3. Node 7 / 漏斗外接线拦点处置表（修订核心增量）

### 3.1 拦点全景（全部本人复核过行号；两路侦察报告为素材源）

Phase 2 消费链：`PortfolioDecisionNode._score_candidates`（`portfolio_decision.py:3856-4013`）直调 screener 的 `_extract_got_universe` + `_filter_got_finalists`（:3987/:3992-3993）→ 候选逐只过 `_verify_candidate_sectors`（:4022 调用，:1477-1637 实现）→ `_score_candidates` 循环内 action 判定（:4114-4125 组装 / :4158-4178 判定）→ bear gate（:4185-4222）→ `_allocate`（:4860-4869，只收 `action.startswith("buy")` 且 `sector_available`）。**拆分只改漏斗内部，以下拦点若不处置，埋伏道票出漏斗后仍会被静默杀死**——黄金票出局的同源现象在 Node 7 侧。

| # | 拦点 | 位置 | 触发条件 | 处置 | trace 字段 |
|---|---|---|---|---|---|
| 1 | **action 级联 watch**（最关键）：`_lifecycle_priority_score` 对 `lifecycle=None` 给 50 分（:2543-2594 else 分支），action 判定 buy_dip 需 `priority>50`（:4164-4178）→ 50>50 为假 → `action="watch"` → `_allocate` 只收 buy* → **漏斗埋伏票永远进不了买入分配**（除非 sentiment_momentum>0.15 抬到 55） | pd.py:4114-4125 / :4164-4178 / :4860-4869 / :2543-2594 | 归档核实：20261012 的 16 个 ambush 板块 lifecycle 全 None（`_build_sector_intel_map` :1132-1196 从 `sector_intel.lifecycle.phase` 取，Node 2 ambush 板块无该字段） | **分道豁免（ACTIVE 才生效）**：action 判定前，若 `sector_type=="ambush"` 且该票在埋伏道 G-A1~G-A4 全过闸名单中（由 Step 5 的 `lane_qualified_codes` 传入）→ priority 取 `max(计算值, 55)`（抬到 buy_dip 线），capital gate 仍共用（G-A1 已含资金证据）；ACTIVE=false 时不动，记 trace | `action_gate_watch: [codes]` |
| 2 | bear gate 豁免不含 Node 2 埋伏名单：`_is_ambush` 仅查 `_bypass_ambush_sectors` | pd.py:4185-4222 + :3951-3963 | `market_regime=="bear"` 且埋伏道过闸票 | **分道豁免扩展**（D7）：ACTIVE=true 时 `_bypass_ambush_sectors` ∪ 埋伏道过闸板块名单；影子期不改 + trace | `bear_gate_watch: [codes]` |
| 3 | capital gate：`capital_score<35` → watch（`GOT_WINRATE_CAPITAL_GATE=35.0`） | pd.py:4158-4163 | capital_score<35（None 不触发） | **保持共用**：G-A1 已含板块流+DDE 双证据，35 线再滤一道是安全冗余非浪费；bug 4 落码后 capital 分数同源抬升，无需分道 | `capital_gate_watch: [codes]` |
| 4 | `_verify_candidate_sectors` miss → `sector_available=False` → buy 强制 watch（:4182-4183） | pd.py:1477-1637，调用 :4022，强制 :4182 | 板块名在 capital/sector 上下文 miss（黄金概念类 TDX 名常见） | **保持共用 + miss 留痕**：lane 票仍走验证，miss 不静默——trace 记 `sector_verify_miss: [{code, sector}]`；执行前置任务：跑一次埋伏板块名 efinance/capital 口径命中抽查（黄金概念等），miss 名补 `capital_flow_aliases.json`（与 bug 4 修复共享同一别名补齐动作） | `sector_verify_miss: [...]` |
| 5 | `PortfolioDecision` schema 无 `model_config` → Pydantic v2 默认 `extra="ignore"`：新增 shadow 字段不声明即**静默丢弃**（现有 6 个 shadow 字段已声明：sell_source/risk_override/tradability/exposure_enforce/signal_size/mature_reduce） | schemas.py:1246-1340 | 任何新字段未进 Schema | **必须声明**：`ambush_lane_shadow: dict = Field(default_factory=dict)` + `ambush_lane_trace: dict = Field(default_factory=dict)` 两字段进 Schema；不声明则 Node 7 落盘丢数据，影子期统计无从谈起 | （字段即产物） |
| 6 | `UnifiedScoringResult` 无 `sector_type` → 埋伏身份在 Phase 2 输出断链（H5）：Node 7 unified_scoring 数组消费方无法区分 lane | schemas.py:1103-1210 | 拆分后输出 | **加字段**：`sector_type: str = "mainline"`（lane 票标 "ambush"）；拦点 #1 的分道豁免依赖此字段判断身份 | （字段即产物） |
| 7 | R3 Top15 右侧技术截断 | screener.py:1593-1596 | `tech_wave_score` 排序截断 | **原计划 D2/D4 已覆盖**（埋伏道绕过 R1–R4） | `funnel_trace["ambush_lane"]` |
| 8 | tech 25% 权重（`_score_unified` composite 中波段技术占 25%） | pd.py:4079-4095 | composite 计算 | **原计划 Step 3 折算已覆盖**（ACTIVE 时埋伏票以 `ambush_rank_score×100` 折算 composite 进 R5 同尺竞争） | `ambush_lane_shadow` |
| 9 | MEG 资金巨量 / FTS 跟风 / GATE-1 / 过热降权 | pd.py:4224-4275 / :4410-4460 / :4082-4110 | 各自阈值触发 | **保持共用 + trace**：ambush 票触发概率低（吸筹期定义即非巨量非过热），影子期统计触发率，不为埋伏道开洞 | `meg_fts_overheat_triggers: [codes]` |
| 10 | Step 1.5 超限减仓 / T1-H 持仓审查 / P4 埋伏退出 / 轮动 / 拥挤检测 | pd.py 各 L1-L5 | 持仓层 | **不动**：纯持仓/advisory 层，与入场拆分正交 | — |
| 11 | 问财初筛：ambush 无 1.5 轮放松（仅主线 :1322-1339）；降级轮不带 DDE 证据（:1343-1352，max 5 只）；负缓存/自愈闭环（:1141-1143/:1363-1381） | stock_screener.py 多处 | ambush 板块问财返回 ≤2 只或空 | **写明现状 + trace**：埋伏道在 R0 前注入（Step 1 抽道），天然绕开 R1-R4 对 universe 的二次截断；但问财初筛本身的「近5日合计>0」是 G-A1 条件 1 的**近乎同义反复**（见 §3.2 现实说明），G-A1 真正新增的是板块 3d 流腿 + 降级票拦截（`dde_degraded=True` 票在 G-A1 直接 fail，记 `g_a1_dde_degraded`——降级轮问财查询不带 5 日列，`dde_5d_sum` 缺失，不允许降级票过吸筹闸） | `g_a1_dde_degraded: n` |
| 12 | T1 prompt 喂 `tech_wave_score`（:1705-1731），右侧分对埋伏票有结构性偏见 | screener.py T1 | T1 开启时 | **T1 默认 OFF 保持**；计划记条件：若开 T1，须为 ambush 票补喂 `catalyst_relevance_score`/`dde_5d_sum`/`ambush_rank_score` 上下文，且 reject 只降级不移除（现状语义），禁喂裸 tech_wave_score 单维裁决 | — |
| 13 | `ambush_lane_trace` 等新字段须落 `funnel_stats`（dict 字段，screener 侧无 Schema 约束） | screener.py 落盘点 | 每次运行 | funnel_stats 是 dict，直接加键即可；**但 Node 7 侧的 shadow 产物必须走拦点 #5 的 Schema 声明** | （字段即产物） |
| 14 | `data/got_screener/YYYYMMDD.json` 顶层无 ambush 产物位 | got_screener 落盘 | 每次运行 | 顶层加 `ambush_lane_shadow`（与 funnel_stats 同级）；该文件无 Pydantic 约束（dict 直接落盘），无需 Schema 改动 | （字段即产物） |

### 3.2 G-A1 同义反复现实说明（修订增量 #8）

问财 ambush 初筛条件就是「近5个交易日的dde大单净量合计>0」（`stock_screener.py:1153`），G-A1 条件 1（`dde_5d_sum>0`）在问财路径上**几乎恒真**（CLAUDE.md/问财 docstring 写「连续3天为正」是陈旧口径，勿据其设计）。因此 G-A1 的设计定位修订为：

- **条件 1（个股 DDE）**：拦截降级票——`dde_degraded=True`（问财 DDE 轮全空降级，不带 5 日证据）→ fail（`g_a1_dde_degraded`）；正常路径票 pass。未来若初筛口径改为不含 DDE 条件（如降级轮扩容），此条件自动恢复区分度。
- **条件 2（板块 3d 流）**：G-A1 的**主区分度所在**——`sector_flow_all_3d` 经 `_lookup_flow_map` 匹配 >0 才 pass；miss → soft_pass；负 → fail。
- Layer 1 胜率测试（§4）须对「G-A1 板块流腿单独」与「G-A1 全条件」分别出读数，验证板块流腿的增量区分度。

## 4. 分步实施（修订版：Step 0 胜率测试插入为实施前置门）

> 执行顺序总则：**Step 0（胜率测试）→ Step 1-5（落码接线）→ Step 6（单测）→ Step 7（归档重放验证）→ Step 8（文档）**。Step 0 不通过则停止实施，参数回改重测（§5.3）。Step 1-5 即草稿 Step 1-5（抽道/四闸门/汇总层/开关/产物），本文以「修订增量」标注，未标注处按草稿原文执行。

### Step 0：实施前胜率测试（新增，硬前置门）

见 §5 全规格。落码前执行，产出 `run_card.json` + `winrate_report.md` 至 `.review_tmp/ambush_lane_winrate/`；**验收判据（§5.3）任一不通过 → 修改 G-A1~G-A4 参数/条件 → 重跑 → 通过后才允许进入 Step 1**。

### Step 1：抽道（草稿 Step 1 原文执行）

`_filter_got_finalists` 顶部（:1496 R0 前）抽道，`funnel_trace["lane_in"]` 计数。修订增量：抽道键用 `c.get("sector_type")`，与拦点 #6 的 `UnifiedScoringResult.sector_type` 字段同名同值，保持链路一致。

### Step 2：埋伏道四闸门（草稿 Step 2 执行，四处修订）

- G-A1：按 §3.2 修订——条件 1 拦 `dde_degraded`，条件 2 为区分度主腿；`funnel_trace["ambush_lane"]` 计数键全表：`g_a1_pass / g_a1_soft_pass / g_a1_flow_negative / g_a1_dde_degraded`。
- G-A2 失败原因**三分**（替换草稿的两分）：`g_a2_no_catalyst`（日历无该板块事件）/ `g_a2_low_confidence`（有事件但 confidence×impact 加权 < 0.7 线，即 P5 现行 0.7 阈）/ `g_a2_position_unsafe`（位置安全带 fail）。草稿的 `g_a2_no_catalyst` 语义收窄为第一分。
- G-A2 临近度：草稿 A2 的 `days_to_event` 字段核实结论——`catalyst_details` 无 `days_to_event`（P5 明细是 `event_date`+`confidence`），**直接采用 A2 的 fallback**：`days_to_event = (event_date - today).days`，负值（已过期事件，D3 守卫漏网）按无事件处理。
- G-A4 同板块去重提前到道内（草稿原文），TopK 默认 3。

### Step 3：汇总层（草稿 Step 3 原文执行）

### Step 4：开关与配置（草稿 Step 4 原文执行，6 个开关进 `catalyst_config.py`）

### Step 5：Schema 接线修复（新增，修订增量 #3）

- `src/got/schemas.py` `UnifiedScoringResult`（:1103-1210）加 `sector_type: str = "mainline"`；`_score_candidates` 产出循环赋值（lane 票 "ambush"）。
- `PortfolioDecision`（:1246-1340）加 `ambush_lane_shadow: dict = Field(default_factory=dict)` + `ambush_lane_trace: dict = Field(default_factory=dict)`——**不声明即静默丢弃**（extra="ignore" 已核实），此步漏做则影子期零数据。
- Node 7 `_score_candidates` 内埋伏道产物传递：`_filter_got_finalists` 返回值携带 `ambush_lane_shadow`（dict），Node 7 原样写入 `PortfolioDecision` 对应字段 + `funnel_stats`（已有 dict 字段）。埋伏道过闸名单（板块名 + 股票码）以 `lane_qualified = {"sectors": [...], "codes": [...]}` 结构随同传递——拦点 #1/#2 的 ACTIVE 豁免消费此结构。

### Step 6：Node 7 拦点接线（新增，按 §3.1 处置表逐条落码）

拦点 #1（action 级联）、#2（bear gate）、#5（Schema）为**必改**；#3/#4/#9 保持共用 + trace 键（`capital_gate_watch` / `sector_verify_miss` / `meg_fts_overheat_triggers`）；#6 Schema 字段（Step 5 已含）；#11 问财现状写进 trace；#12 T1 条件记文档不动码。全部 ACTIVE 门控：`GOT_AMBUSH_SPLIT_ACTIVE=false` 时 Node 7 行为与拆分前逐字节一致（Step 7 断言）。

### Step 7：测试与验证（草稿 Step 6 的 9 用例 + 新增 4 用例，合并为 `tests/test_ambush_lane_split.py`）

草稿 9 用例原文保留，新增：
10. `test_action_gate_shadow_trace`：ACTIVE=false，lifecycle=None 的 ambush 票 → action=watch 且 `action_gate_watch` trace 留痕（拦点 #1 影子面）
11. `test_action_gate_active_lift`：ACTIVE=true，lane 过闸 ambush 票 priority 抬到 55 → buy_dip 可达（拦点 #1 豁免面）
12. `test_bear_gate_extension`：ACTIVE=true，bear 日 lane 过闸板块票不被强制 watch（拦点 #2）；ACTIVE=false 仍 watch 且 trace
13. `test_shadow_fields_persist`：`PortfolioDecision` dump→load 后 `ambush_lane_shadow`/`ambush_lane_trace`/`sector_type` 字段在（拦点 #5/#6——Schema 静默丢弃回归）

### Step 8：文档（草稿 Step 7 原文执行，追加）

- CHANGELOG 登记追加：拦点处置表落码清单 + Step 0 胜率测试结果引用 + pre-T1 caveat 说明。
- `CLAUDE.md`/`docs/AGENTS.md`：漏斗表 R1.5/R3 行注记之外，Node 7 段补「埋伏道 ACTIVE 豁免点（action/bear gate）」一句。

## 5. Step 0 胜率测试全规格（双层设计）

### 5.0 数据地基（本人已盘点核实，硬事实）

| 数据 | 覆盖 | 用途 | 限制 |
|---|---|---|---|
| `data/got_daily/` 归档 | 53 天（20260724→20261012）；unified ambush 板块日 460（去重板块码 151） | Layer 1 输入 | — |
| 880 板块 K 线缓存 `.review_tmp/bt880_cache/` | 280 文件，20260520→20261008；**unified ambush 板块日 460 中 426 命中（92.6%）** | Layer 1 前瞻收益计算 | 结束 20261008 → 右删失 |
| `sector_flow_all_3d` | 26 天（20260827 起，capital.json evidence.sector_flow 下） | G-A1 重放 | 20260827 前无 |
| 催化剂日历（news.json catalyst_calendar） | 31/53 天有事件（合计 1958 条） | G-A2 重放 | 供给面现实见 §1.2 |
| 基准 K 线 `.review_tmp/bt_cache/index_sh000852.csv` | 2014-10-17→**2026-09-29**（注意早于 880 缓存截止） | Layer 1 基准 | 20260929 后无基准 → 右删失提前 |
| **Layer 1 可用信号日（交集−右删失）** | **17 天：20260827→20260921 全列**：0827/0828/0831/0901/0902/0903/0904/0907/0908/0909/0910/0914/0915/0916/0917/0918/0921；ambush 板块日 218 个；名义事件匹配率 26.6%（58/218，§1.2 口径） | — | 0922 起右删失 6 天（T+5 越 0929 基准界）；0930/1008/1009/1012 非基准交易日 |
| 逐股历史回放 | **不可行（硬结论）**：问财 DDE 查询当日现算（"近5日合计"以查询日为锚）无法 point-in-time 重拉；candidate_pool.json 仅 3 天且无 dde_5d_sum/sector_type；SQLite stock_daily 仅 71 只票 | — | ⇒ Layer 2 影子期才有逐股对照 |

### 5.1 Layer 1：板块级离线回放（实施前，本次必跑）

**设计**：在 17 个可用信号日的归档数据上，重放 G-A1（板块腿）+ G-A2（临近度+安全带）+ G-A4（TopK）→ 逐日选出 lane Top3 板块 → 用 880 缓存算板块 T+1/T+3/T+5 前瞻收益 → 与双基准对照。**G-A3 个股腿无逐股历史数据，Layer 1 不含 G-A3（声明为覆盖缺口，Layer 2 补）**。

- **输入装载**：面板按 `(day, sector_code)` 键，`sector_code` 直接取 `unified.json confirmed_sectors` 中 ambush 板块的 880 码（**勿走 board_name_resolver 名字解析——已实测归档 ambush 名大量 resolve 返回 None，92.6% 命中靠 sector_code 直取**）；装载范式抄 `scripts/calibrate_optimal_stop.py` `load_panel()`，归档日过滤 = 目录名 8 位数字 + 有 unified.json（或直接用 `scripts/verify_common.py` `iter_archive_days`）。
- **G-A1 重放口径（板块腿）**：`capital.json evidence.sector_flow.sector_flow_all_3d` 经 `_lookup_flow_map` 三级+别名匹配（直接 import `AggregatorNode` 调用）>0 → pass；miss → soft_pass；<0 → fail。产出两臂读数：①全条件（含 soft_pass）②仅硬 pass（排除 soft）——区分度对比即 §3.2 要求的增量读数。
- **G-A2 重放口径**：事件源 = 当日 news.json catalyst_calendar，`days_to_event = (event_date − 信号日).days`（>14 或 ≤0 → 无事件）；板块-事件匹配复用 P5 语义（affected_sectors/sector 名包含匹配）；`proximity = max(0, 1 − days_to_event/14)`；置信线 = confidence ≥ 0.7（P5 现行）；安全带：880 收盘 ≤ 20 日高×1.03 且 ≥ MA20×0.92（880 缓存自带 OHLC，MA20 用收盘算）；fail 三分 `no_catalyst`/`low_confidence`/`position_unsafe` 落 trace。**报告必须给 `no_catalyst` 占比读数**（供给面量化，预置预期 70%±）。
- **G-A4 重放口径**：`ambush_rank_score = confidence × proximity × (1 + 0.2×吸筹强度)`，吸筹强度 = 板块 3d 流在当日 ambush 板块内的百分位；同板块只留 1（板块级无此问题，个股级规则原样进 Layer 2）；TopK=3。
- **前瞻收益**：880 板块 K 线 T+1/T+3/T+5 收盘收益（信号日收盘为基）；基准：①等权全部当日 ambush 板块（同池对照，量 lane 的选择增量）②中证1000（sh000852，量埋伏池整体的绝对量级）。**全部超额收益 = 板块收益 − 基准同窗收益**。
- **纪律件**：走 `scripts/backtest_standard.py` 强制 run card（--window `20260827~20260921` / --point-in-time `archive_close` / --benchmark `ambush_equal_weight+sh000852`），缺卡 exit 2 纪律沿用；`moving_block_bootstrap_ci` block_len=5 出均值 95% CI；walk_forward 不适用（样本 17 天 <60 train，**声明豁免并说明**——run card 里记 `walk_forward_skipped: sample_lt_train`）。
- **右删失**：信号日 20260922-20261012 共 9 天剔除（6 天 T+5 越基准界 + 3 天非基准交易日），报告记 `censored_days: 9`；不填补不伪造。

### 5.2 Layer 2：影子期逐股对照（落码后，预注册判据写死）

逐股 lane-vs-主线对照只能在 ACTIVE=false 影子期积累（`ambush_lane_trace` 落盘逐票记录 + T+5 时 mootdx 拉收盘结算）。**预注册判据（写死进本计划，防止事后挑窗）**：

- 最短样本：≥20 个交易日影子运行，且 `shadow_finalists`（G-A4 后幸存票）累计 ≥30 只票样本；未达线只出中间读数不出结论。
- 通过线（全部满足才允许讨论 ACTIVE 翻转）：①lane T+3 与 T+5 超额（vs 同期主线终选等权 + vs 中证1000 双口径）均值 ≥0 且 bootstrap CI 方向为正；②`g_a2_no_catalyst` 占比 ≤80%（事件供给改善或板块名单收敛任一驱动均可）；③无单日 lane 全灭（TopK=3 全 fail）连续 ≥5 交易日（若发生，先修供给面再继续影子）。
- 结算脚本随 Step 5 落码一并交付（读 `data/got_screener/` 影子产物 + mootdx T+5 收盘），T+5 结算复用 `ambush_position_snapshots` 同款 mootdx 拉取路径。
- 对照先例口径：9 月回测先例（win_rate_optimization_plan Context：11 交易日 40 只 T+3 28.1%/T+5 14.8% **负 alpha**）——Layer 2 报告必须对照该口径出读数，防止重蹈右侧漏斗式负 alpha。

### 5.3 Step 0 验收判据（Layer 1，预注册，写死）

| 判据 | 阈值 | 不通过动作 |
|---|---|---|
| J1 lane Top3 板块日 T+5 超额均值（vs 全 ambush 等权基准） | ≥ 0 且 bootstrap CI 上界 >0（方向为正） | G-A2 权重/安全带参数回改（1.03/0.92/14d 窗口逐一单变量重测），仍负 → 拆分方案降级为「仅 G-A1 吸筹过滤上线、G-A2~A4 退出」，重出报告 |
| J2 T+1 超额 | 不要求为正（埋伏语义允许 lag），但须 > T+5 超额 −2pp（防「高开即卖」结构性缺陷暴露） | 同上参数回改 |
| J3 样本充足 | ≥17 信号日 × ≥3 板块/日（TopK 满员率 ≥60%）；`no_catalyst` 占比读数必出（无阈值，供 Layer 2 判据②校准） | 样本不足 → 报告如实声明 + Layer 2 判据线维持，不放宽 |
| J4 基准对照 | 中证1000 同窗读数必出（量绝对量级） | — |

**J1 不通过时禁止进入 Step 1（落码）**——这是「实施前胜率测试」的硬语义。参数回改循环上限 2 轮，仍不通过则本计划整体打回（向用户呈报数据，由用户决定是否降级方案或终止）。

## 6. Verification（修订版）

1. **单测**：`python.exe -m pytest tests/test_ambush_lane_split.py tests/test_phase2_audit_fixes.py tests/test_winrate_optimization.py -q` 全绿（后两个防存量回归）。
2. **Step 0 产物**：`.review_tmp/ambush_lane_winrate/run_card.json` + `winrate_report.md`，J1-J4 判据逐条落结论，censored_days=9 声明在案。
3. **归档重放（端到端）**：throwaway 脚本读 `data/got_daily/20261012/` 6 节点 JSON 重放（预演 monkeypatch 手法已验证：`_parse_sector_flow`→3d 表+别名键、`_extract_got_universe`→`_lookup_flow_map` 补齐、`_save_got_result`→no-op）：
   - 断言①（pre-T1 caveat）：ACTIVE=false 终选与生产 pre-T1 名义终选一致（000736/怡亚通/捷成股份 中 T1 reject 前序；生产 15:11 终选含 T1 reject 降级，重放无 LLM 不跑 T1——断言只对 pre-T1 名义终选成立，报告注明差异）；
   - 断言②：黄金票 600988/000975 逐闸去向留痕（预期 G-A1 pass：3d +9.28 亿 / G-A2：黄金概念当日有 14 日内事件与否按归档日历实查 / G-A3：-2.67% 回调 + 量比核 / G-A4：排位）；
   - 断言③（ACTIVE=true 重放）：黄金票进 R5 竞争、action 级联豁免后 buy_dip 可达（拦点 #1 豁免面实证）。
4. **生产链路验证**：批准后首个交易日跑 `python.exe main.py --got`（bash `timeout: 0`），检查 `data/got_screener/` 当日 JSON：`ambush_lane_shadow` 顶层键 + `funnel_stats.ambush_lane_trace` 计数齐全 + Node 7 `portfolio_decision.json` 中 `ambush_lane_shadow/trace` 在（拦点 #5 的实弹验证）。
5. **黄金用例回归锚**：修复后赤峰黄金 capital≈87.2 分（bug 4 独立拍板项，若先于本计划落码则复用该锚）。

## 7. Assumptions & contingencies（修订版）

- **A1 影子哲学**（草稿原文保留）：ENABLED=true/ACTIVE=false；转正判据由草稿的模糊表述（「影子期 ≥10 交易日」）**升级为 §5.2 预注册判据**（≥20 交易日 + ≥30 票 + J1' 双口径），判据写死进本计划，防事后挑窗。
- **A2 阈值来源**（草稿原文保留 + 收敛）：G-A2 1.03/0.92、G-A3 量比 3.0、临近度 14 日已配置化；`days_to_event` 字段核实结论已定（无此字段，用 event_date 差值，Step 2 修订增量），执行者不再需要 grep 核实。
- **A3 与 bug 4 边界**（草稿原文保留）：G-A1 板块流腿 3d→当日 fallback 链独立工作；bug 4 落码后同源。拦点 #4 的别名补齐动作与 bug 4 共享（`capital_flow_aliases.json` 双消费方：`_score_capital` + `_lookup_flow_map`）。
- **A4 失败回退**（草稿原文保留）：闸门异常 → 埋伏道全量 pass + `lane_error` trace；ENABLED=false 走原单道逻辑（唯一允许的兼容分支，影子可一键回滚）。
- **A5 成本**（草稿原文保留）：G-A2/G-A3 K 线拉取同 R3 量级，增量 <30s。
- **A6 R1.5 语义**（草稿原文保留）：横盘是埋伏特征不进埋伏道排除；校准手段是 G-A1 吸筹窗口扩展，不是搬回 R1.5。
- **A7（新增）Layer 1 覆盖缺口**：G-A3（个股防接刀）与 G-A1 条件 1（个股 DDE 降级拦截）在 Layer 1 无逐股历史数据，不进离线胜率——声明为已知覆盖缺口，由 Layer 2 影子期读数补足；Layer 2 判据② 的 `no_catalyst` 占比线（80%）以 Layer 1 实测读数（预置 70%±）为校准基。
- **A8（新增）基准数据边界**：sh000852 缓存止于 2026-09-29，早于 880 板块缓存（20261008）——Layer 1 右删失以更早者为准（0921 为最后可用信号日）；若执行时基准可补拉（mootdx 指数 K 线），可放宽至 880 界（1001 前后 3 个信号日），执行者可选但须在 run card 记 `benchmark_source` 与放宽说明。
- **A9（新增）walk_forward 豁免**：样本 17 天 < train 60，walk-forward 检查声明豁免（run card 记 `walk_forward_skipped`），不作为判据；此豁免是样本现实非纪律松动，Layer 2 影子期样本增长后自然补足。

## 8. 执行者对账清单（零决策遗留）

1. Step 0 先行：按 §5.1 规格写 throwaway 脚本（可放 `.review_tmp/ambush_lane_winrate/`），跑 Layer 1，出 J1-J4 结论 → 决定继续/回改/打回。
2. 落码顺序：Step 1（抽道）→ Step 5（Schema 三处）→ Step 2（四闸门）→ Step 3（汇总层）→ Step 4（开关）→ Step 6（Node 7 拦点，#1/#2/#5 必改）→ Step 7（13 用例）→ Step 8（文档）。
3. 唯一回滚分支：`GOT_AMBUSH_SPLIT_ENABLED=false` 时 `_filter_got_finalists` 原单道逻辑（A4）。
4. 修改前对账锚点（行号以 20261012 工作树为准，若漂移以符号名优先）：`_filter_got_finalists`（screener.py:1474）/ R3（:1593-1596）/ P5（:1614-1636）/ `_score_catalyst_relevance`（:2662）/ 问财 ambush 条件（:1150-1153）/ `_lookup_flow_map`（aggregator.py:3276）/ `_run_ambush_gates`（:2117）/ `_score_candidates`（pd.py:3856-4013）/ action 判定（:4158-4178）/ bear gate（:4185-4222）/ `_lifecycle_priority_score`（:2543-2594）/ `_is_ambush`（:4187-4192）/ `_bypass_ambush_sectors`（:3951-3963）/ ROLE_MULTIPLIER（pd.py:44）/ `UnifiedScoringResult`（schemas.py:1103-1210）/ `PortfolioDecision`（schemas.py:1246-1340）。
5. 逐条完成 §3.1 拦点表 14 项处置后，在 CHANGELOG 登记表抄录（拦点编号 + 处置 + trace 字段名）。
6. 本文件为草稿的**修订真源**：批准后实现者以本文件为准落码，草稿 `埋伏分支拆分_PLAN.md` 归档至 `docs/got/plans/`（文首加状态头「已被 v2 修订版取代」），CLAUDE.md 引用同步更新。
