# GOT 文档导航

> GOT（多节点决策系统）相关文档的唯一入口。目录三区：**现行清单**（本目录顶层）、**待实施计划**（`plans/`）、**历史归档**（`archive/`）。
> 架构现状的权威描述在 [`architecture.md`](architecture.md)（原仓库根 `agent_cot_design.md`，2026-09-17 移入）。

## 现行清单（持续更新）

| 文档 | 性质 | 更新规则 |
| --- | --- | --- |
| [`backlog.md`](backlog.md) | **待实施**清单：四层未落地项、0902 报告 15 项处置表、OPEN 汇总、A6 转正与退役、后续 Phase、需裁决事项、文档层缺陷、红线自身缺陷 | 只收未实施项；实施后移出并归档出处 |
| [`verification.md`](verification.md) | **待验证 / 影子期**清单：判据、实测值与出处，不落达标判定（达标与否归项目负责人） | 验证通过后条目移入 [`archive/verified_completion_log.md`](archive/verified_completion_log.md) |

## 待实施计划（按依赖排序）

| 序 | 计划 | 状态 | 前置 |
| --- | --- | --- | --- |
| 1 | **A6 阶段 2 · 退役**（backlog §4.2） | ✅ **已完工**（2026-09-26：规则评分模式三件套退役；负责人裁决放弃逐板块兜底（漏命中改 neutral 垫底）+ 提前跨过条件 2，单 commit 可回退） | —（已执行，详见 backlog §4.2「已执行动作」） |
| 2 | [`plans/win_rate_optimization_plan.md`](plans/win_rate_optimization_plan.md) — 选股胜率优化 4 步（9 月回测驱动） | **已实施**（2026-09-17：4 步入码 + `GOT_WINRATE_*` 开关 + `tests/test_winrate_optimization.py` 16 测试，回归 309 passed；待 9 月后回测复验胜率改善） | 无（每步独立开关、独立可验证） |
| 3 | [`plans/ambush_split_plan.md`](plans/ambush_split_plan.md) — 埋伏分支拆分 | 设计收口（18 项决策落定），待实施 | 注意其 P1/P2 子项已在 CHANGELOG 记为完成，实施前先对账 |
| 4 | Phase 3b / 4b / 4d / 5b / 5c（backlog §5 排期轮） | **影子态落地**（2026-09-28 批次 5：5b TickFlow 探测证伪其作板块排名源→mootdx 先行定案；4b `market_theme.py` Step 2 / 3b `disagreement.py` 分歧摘要+RiskOverridePlan / 4d 前置 funnel_stats，均默认影子零决策改动；4.1 超限减仓三态开关已落，批 2 取证判 5c 样本 6<10 → DEFER） | enabled 转正归负责人 + 需影子期读数；相关性验收脚本 `.review_tmp/cp4b_correlation_acceptance.py` 为 4b 转正前置 |
| 5 | Phase 6 盘中 0→1 确认闸门（[`archive/intraday_gate_plan_0907.md`](archive/intraday_gate_plan_0907.md)） | 草案 | 硬前置 Phase 6-0 数据地基（五档量/分时/竞价/盘中调度），地基未验收前不开影子期 |
| 6 | [`plans/architecture_optimization_implementation_20260929.md`](plans/architecture_optimization_implementation_20260929.md) — GOT 架构优化明细实施报告（2 GATE + 26 WI） | **已审核修订**（2026-09-29：CodeReview 逐条核验 10 组代码事实断言，9 组属实；更正 `sector_strength_map`"死代码"误判（1 Critical）+ P2-8 漏 Node6 / P0-3↔P0-4 confidence 隔离（2 Warning）。关联分析蓝图：`.qoder/plans/GOT架构漏洞分析_33e2c2de.md`） | **GATE-1** 卖出侧可成交性投影层（C1）/ **GATE-2** 最优停止 pooled 分层标定；两者为 P0 系退出引擎的地基，须先落地 |

**需裁决事项**入口：[`backlog.md` §7](backlog.md)（6 条开放裁决）。

## 验收工器（红线复算入口）

复现命令集中在 [`backlog.md` §10](backlog.md)：`scripts/verify_same_day_idempotency.py`、`verify_layer_b_consistency.py`、`verify_glue_metrics.py`、`check_audit_claims.py`、`export_glue_family_audit.py`。

## 历史归档（[`archive/`](archive/)，不再更新）

原始文档索引与「已实施并完成验收」清单见 [`archive/README.md`](archive/README.md)。包含：0902 全链路审核、0903 主方案（agent_glue，§6.1 退役清单 / §11 路线图的权威出处）、0911 四层计划（A/B/C/D）与实施复审、修订台账、E3 总结（仅作史料）、状态盘点、已验证完成日志，以及原 `docs/custom/` 的 3 篇已完结 GOT 文档（0827 诊断、时间线增强计划 v2.1、试点报告）。

> 代码与脚本中的 `docs/got/archive/xxx.md §X.Y` 节号引用在归档后仍然有效（文件内容与节号一字未改，仅路径前缀变化）。
