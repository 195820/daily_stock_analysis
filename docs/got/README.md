# GOT 文档导航

> GOT（多节点决策系统）相关文档的唯一入口。目录三区：**现行清单**（本目录顶层）、**待实施计划**（`plans/`）、**历史归档**（`archive/`）。
> 架构现状的权威描述在 [`architecture.md`](architecture.md)（原仓库根 `agent_cot_design.md`，2026-09-17 移入）。
>
> **2026-10-10 文档整理后的顶层构成**：四份清单类文档 + 唯一一份**待拍板简报**（`decision_brief_openc3_20260928.md`）。已完结的过程稿（0922 审计、0926 决策简报、cp12 系列×3、cp13 系列×3）**已全部移入** [`archive/`](archive/)，带路径的引用已同步改指。

## 现行清单（持续更新）

| 文档 | 性质 | 更新规则 |
| --- | --- | --- |
| [`backlog.md`](backlog.md) | **待实施**清单：四层未落地项、0902 报告 15 项处置表、OPEN 汇总、A6 转正与退役、后续 Phase、需裁决事项、文档层缺陷、红线自身缺陷 | 只收未实施项；实施后移出并归档出处 |
| [`verification.md`](verification.md) | **待验证 / 影子期**清单：判据、实测值与出处，不落达标判定（达标与否归项目负责人）；**含 MEG/FTS/FXG 三道买入侧新闸门判据（顶部 2026-10-10 补登节；其「读数起算」已按实测更正为 MEG 1 天 / FTS 0 / FXG 0）** | 验证通过后条目移入 [`archive/verified_completion_log.md`](archive/verified_completion_log.md) |
| [`architecture.md`](architecture.md) | 架构现状权威描述 | ⚠️ 正文基线为 0831 快照；**文首时效注记已于 2026-10-10 刷新**（除 A6 转正/规则退役外另补入场分道 A/B/C、MEG/FTS/FXG、个股级事件退日历、WI-P1-11、M3 结案等五档，并列出四个已知脱节区） |
| [`decision_brief_openc3_20260928.md`](decision_brief_openc3_20260928.md) | **待拍板简报**（OPEN-C3 死分支三选一） | 拍板后移入 `archive/`，结论回写 `backlog.md` §3.3 |

> ❗ **新增影子线必须登记判据**：只写 CHANGELOG 不进 `verification.md`，等于无人验收（MEG/FTS/FXG 就是例）。

## 待实施计划（按依赖排序）

| 序 | 计划 | 状态 | 前置 |
| --- | --- | --- | --- |
| 1 | **A6 阶段 2 · 退役**（backlog §4.2） | ✅ **已完工**（2026-09-26：规则评分模式三件套退役；负责人裁决放弃逐板块兜底（漏命中改 neutral 垫底）+ 提前跨过条件 2，单 commit 可回退） | —（已执行，详见 backlog §4.2「已执行动作」） |
| 2 | [`plans/win_rate_optimization_plan.md`](plans/win_rate_optimization_plan.md) — 选股胜率优化 4 步（9 月回测驱动） | **已实施**（2026-09-17：4 步入码 + `GOT_WINRATE_*` 开关 + `tests/test_winrate_optimization.py` 16 测试，回归 309 passed；待 9 月后回测复验胜率改善） | 无（每步独立开关、独立可验证） |
| 3 | [`plans/ambush_split_plan.md`](plans/ambush_split_plan.md) — 埋伏分支拆分（**合稿**：v3 埋伏分支 + v3.5 波次化生命周期） | **两块主体均已实施**（v3：2026-09-28 核实｜v3.5 S2–S5：2026-10-10 核实，`decide_wave_days` / `_wave_coeff` / `MainlineEndurance` 全链路 / `calibrate_wave_coeff.py` 均在产；S5 首跑待埋点满 40 交易日） | 勿按「待实施」开工；先读该文件两份状态头。原残留两项**均已收口**：NUL 幽灵行清理 ✅ 2026-10-10 实施（`70e4748`）；`wave_days` 回填 ✅ **2026-10-12 负责人裁决作废（不实施，无使用方）** ⇒ 本节无待实施尾巴 |
| 4 | [`plans/加速末期识别缺陷分析_20261008.md`](plans/加速末期识别缺陷分析_20261008.md) — RRG 象限相位滞后于动量衰减（acceleration 内 mom_raw 两日 -34% 仍 actionable；`_lifecycle_priority_score` 仅按 day 降档、overheat §3.5 失明、B 闸门 bear-only 三线空档） | **MEG 影子接线完成**（2026-10-08 K 线回测证真 §7.8.1：snap−thrust T+5 超额 -2.54%、Welch p=0.001，案例 880952 命中 -9.41%；2026-10-09 三层落地，缺省 `GOT_MOM_EXHAUST_GATE_ENABLED=0` 逐字节不变）；**同一问题域已派生两道闸门：FTS 家族见顶持续压制门（shadow）与 FXG 家族级见顶上游排除闸门（已切 veto）**——三者均无独立计划文档，判据在 [`verification.md`](verification.md) 顶部 | `scripts/meg_shadow_report.py` 聚合命中率/假阳性；观测 ≥1 周证真充分再切 `MODE=veto`；FXG 切 veto 后连续 ≥1 周用 `scripts/fxg_pair_report.py` 证真补位净正/中性，否则回退 shadow；与 overheat §3.5 修复互补不合并。**⚠️ 10-10 实测曾为 MEG 1 天 / FTS 0 / FXG 0、“≥1 周”计时未开始；〔10-12 实跑订正：`shadow_fts.json` 与 `fxg_baseline_scoring.json` 首份均已落盘 ⇒ 三闸门时钟自 2026-10-12 起算（MEG 2 / FTS 1 / FXG 1），读数与两条零产物定性见 [`verification.md`](verification.md) 顶部 10-12 节〕**（盘中跑为生产常态（13:30 节奏），判读按 `meta.bar_complete` 分组而非改跑收盘） |
| 4b | **入场分道三工作流**：[`plans/入场分道与持仓延续性_总计划_20260930.md`](plans/入场分道与持仓延续性_总计划_20260930.md) | 工作流 A/B/C 代码**已合入**（commit `4be371b`）｜ **A 已判完成**（2026-10-10 负责人裁决：冻结机制实测生效——日志「冻结放行 6/7」「9/16」+ 归档「蓄水池含数据缺失 0」；`final_pool>0` **不属 A 的责任边界，已移出验收**）｜ **B：行为面正常（bear 日实测命中 1–3 单/日），验收只剩一个真缺口＝拦单留痕在生产多轮场景下被后一轮重置抹掉（10-12 实跑证伪：10:02 命中 1 单、10:06 命中 0 单 → `shadow_bear_gate.json` 零产物 ⇒ 误伤率依旧算不出；代码 10-10 已合入，10-12 改判真·待实施，修法见 backlog §1.H）**；非 bear 日对照＝条件不全不是缺陷（等样本），「埋伏被放行」条款已移出 B 归 A-2 线｜ C **已终结**（2026-10-10 裁：持仓模块要大改，未决事项全部移交下行持仓模块文档，**不得再单独推 C-B1/B2/B3**）| 不再需“先归因池归零才能结 A”；待实施项与 B 的三块处置已登 [`backlog.md`](backlog.md) §1.H |
| 4c | [`plans/埋伏池归零下游拦点分析_20261010.md`](plans/%E5%9F%8B%E4%BC%8F%E6%B1%A0%E5%BD%92%E9%9B%B6%E4%B8%8B%E6%B8%B8%E6%8B%A6%E7%82%B9%E5%88%86%E6%9E%90_20261010.md) | **分析稿，未批准实施**（与旧计划不混排）。结论：不是 0.7 门槛拦的（全库 43/50 事件 ≥0.7、规则类硬编码恒过、全天仅 1 例被拦），而是**事件→板块映射覆盖盲区**：16 候选中 **11 个整链零事件**，映射表 33 板块/narrative 34 板块与当日名单精确交集仅 2/1 个，`macro_rule` 10 条 `affected_sectors` 全空 | 负责人拍三条候选修法之一（名单与事件源前置对齐 / `affected_sectors` 单一词表 / 扩映射表）后另立 WI |
| 4d | [`plans/持仓模块重构_待分析与待实施清单_20261010.md`](plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) — 持仓侧唯一归集点（以已终结的 C 为开头） | **待分析 + 待实施清单，未批准实施**。开头三条：T-1 近似 HWM 误触发（实测 P&L −17.6% 却反推高点收益 +176%，「曾盈利≥10%」形同恒真）/ T-2 板块出名单 ⇒ 生命期标签断供 / T-3 `hwm_gain`·`approx` 不入档；地基实测：`holding_thesis_snapshots` 1 行、`ambush_position_snapshots` 0 行，卖出/仓位侧 6 个 `*_shadow` 在产但**零转正** | 先做 **WI-P-1**（approx 不动作），**修完前不得开 `GOT_TRAILING_STOP_ENABLED`**；WI-P-2/3 为大改备度量；WI-P-5（W3 多账户口径）需先答“持仓真相源是谁”。**〔10-12 A1 #3/#4 更新：WI-P-6 已定稿三态；四条持仓侧 shadow 冻结，新增 WI-P-7——大改时必须逐条判其去留，裁决前不得开对应 enabled 开关；另新增 §六「大改返工钩子」表〕**。挂号于 [`backlog.md`](backlog.md) §1.I |
| 5 | Phase 3b / 4b / 4d / 5b / 5c（backlog §5 排期轮） | **影子态落地**（2026-09-28 批次 5：5b TickFlow 探测证伪其作板块排名源→mootdx 先行定案；4b `market_theme.py` Step 2 / 3b `disagreement.py` 分歧摘要+RiskOverridePlan / 4d 前置 funnel_stats，均默认影子零决策改动；4.1 超限减仓三态开关已落，批 2 取证判 5c 样本 6<10 → DEFER） | enabled 转正归负责人 + 需影子期读数；**〔10-12 A1 #4：3b 已定转正门槛（≥10 有效样本日 + 11 类冲突分层抽查一致率 ≥80% + 零条误判 veto_buy；现 6 个有效日，到期即可测）；持仓侧四条 shadow 已冻结，去留归 WI-P-7〕**；相关性验收脚本 `.review_tmp/cp4b_correlation_acceptance.py` 为 4b 转正前置。**〔10-10 复核：`glue_consumed=true` 已 11 个归档日 ≥ 10 ⇒ 5c 的 4.2 P1-1 分位重标已解锁可开工（与 OPEN-C1a「满 40 交易日」是两个窗，后者仍未满足）〕** |
| 6 | Phase 6 盘中 0→1 确认闸门（[`archive/intraday_gate_plan_0907.md`](archive/intraday_gate_plan_0907.md)） | 草案 | 硬前置 Phase 6-0 数据地基（五档量/分时/竞价/盘中调度），地基未验收前不开影子期 |
| 7 | [`plans/architecture_optimization_implementation_20260929.md`](plans/architecture_optimization_implementation_20260929.md) — GOT 架构优化明细实施报告（2 GATE + 26 WI） | **逐条推进中**（§11.1 已落地：GATE-1/GATE-2/P0-3/P0-4/P0-5/P1-7(a)(b)/P1-9/P1-11/P2-9(a)，均为 off/shadow 态；§11.2 延后：P0-1/P0-2/P1-1…P1-6/P1-8/P1-10/P2 余项——其中 P1-10 原催化剂日历改动面已随 2026-10-08 裁决退役，残余 scope＝bundle→RiskAgent 侧消费 `earnings.forecast_type`，开工前须先改写 WI（见该计划 §200 状态头）） | 各 WI shadow 读数达标后逐个 enabled；候选池归零红线用例必备 |
| 8 | [`plans/竞品经验补强计划_20261006.md`](plans/竞品经验补强计划_20261006.md) — 9 个竞品比对后的 7 批次补强 A–G（证据链/反偏见/双源校验/异动归因/lite档/回测纪律/运行时自查） | ✅ **一期已实施（2026-10-11，commit `5a4f29f`）**——经外网对标计划 §7 拍板批准全量按序，已落 D0→D→G→A→B→C1/C2→E1→E3→F（均影子/观测先行、零决策面变化，`news_pulse.py`/`output_review.py`/`data_consistency.py`/`backtest_standard.py`/`antibias.py` 均在产）；**E2 回放与 lite 自动降级按前置阻塞未开工**。判据已登 [`verification.md`](verification.md) 2026-10-11 补登节（本行原记「规划稿、批次 A–G 零落地、四文件均不存在」已过期）。⚠️ 批次 B 于 10-12 实跑发现**结构性无样本**（无买入/持有标的时零产物），判据需补前提，见 backlog §9 #7 | 逐批读数达标后转正；A 二期 rumor 折扣需历史重放 + 人工确认 |

**需裁决事项**入口：[`backlog.md` §7](backlog.md)——§7 现共 12 条，**其中 #11（bundle 预算与逐源延迟）已拍「挂起观察」、#12（家族兜底阈值重标定）已拍「另开 WI」待排期，其余均已拍板并实施**；另有 backlog §3.3 **OPEN-C3** 待拍板（简报在顶层）。

## 验收工器（红线复算入口）

复现命令集中在 [`backlog.md` §10](backlog.md)：`scripts/verify_same_day_idempotency.py`、`verify_layer_b_consistency.py`、`verify_glue_metrics.py`、`check_audit_claims.py`、`export_glue_family_audit.py`。

## 历史归档（[`archive/`](archive/)，不再更新）

原始文档索引与「已实施并完成验收」清单见 [`archive/README.md`](archive/README.md)。包含：0902 全链路审核、0903 主方案（agent_glue，§6.1 退役清单 / §11 路线图的权威出处）、0911 四层计划（A/B/C/D）与实施复审、修订台账、E3 总结（仅作史料）、状态盘点、已验证完成日志，原 `docs/custom/` 的 3 篇已完结 GOT 文档，以及 **2026-10-10 新收的 14 份**：cp12 系列×3、cp13 系列×3、审计与决策简报×2，以及从**仓库根目录**收回的 6 份（两份已取代 PLAN/审核稿、板块漂移 PLAN、FIX_SUMMARY 提交统计、文档更新执行报告、ULTRA_REVIEW 审查留痕）。

> 代码与脚本中的 `docs/got/archive/xxx.md §X.Y` **节号引用**在归档后仍然有效（正文与节号未改）。注意不等于“文件零改动”：本批搬迁对三份过程稿改了文档路径引用/相对链接前缀、对六份根目录稿新增了首屏状态头，逐份清单见 [`archive/README.md`](archive/README.md) 头部注记。
