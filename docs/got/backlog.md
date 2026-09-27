# GOT 待实施清单
> **原位置**：`docs/got_backlog.md`（2026-09-17 文档结构重排移入当前位置）

> **性质**：登记**尚未落地**的修复项、功能项与文档层缺陷，供排期使用。本清单**不含达标判定**，也不排优先级——排期由项目负责人决定。
>
> **基准**：2026-09-14 工作区快照，分支 `fix/mootdx-hq-server-pool`（领先 `main` 7 个提交，未合并）。
> **原文档已归档**至 [`archive/`](archive/)，本清单所有引用均指向该目录。
>
> **配套文档**：[`verification.md`](verification.md)（待验证 / 影子期）、[`archive/README.md`](archive/README.md)（归档索引与已完成清单）。

---

## 0. 边界说明

本清单**只收未实施项**。已实施但**效果待验证**的（如四层"零行为变更"回放红线、同日两跑红线）列在 [`verification.md`](verification.md)，不在本清单。

### 0.1 「未实施」不是一个类别，是六个（2026-09-14 明确）

「未修」这个标签把六种排期含义完全相反的状态压成了一种。本清单此后**一律标处置**，不写「未修」：

| 处置 | 含义 | 排期动作 |
|---|---|---|
| **待退役** | 所属机制在 §6.1 退役清单内，修了也会随删除消解 | **不修**，等 A6 |
| **已改归属** | 改到别处跟踪（如并进 A-P0-8） | 看承接方 |
| **降级口径** | 不修数值，只加口径标注字段 | 低优先 |
| **挂起待查** | 是否需要修本身未决 | 先查 |
| **明确延后** | 确认要修，排下一批 | 排期 |
| **真·待实施** | 未决、未排、无归属 | 排期 |

> **判定标准（新增）**：一条**验收红线只能量已实施的东西**。若它的读数由未实施工作决定，它是 burndown chart 而非验收红线，应收进本清单。
> 按此标准，`R-D1` / `R-D2` / `R2` / `R5` 已从 [`verification.md`](verification.md) 移入本清单（见 §2 / §8 / §9）。

### 0.2 口径提醒

来自 `archive/got_impl_review_0911.md` §6：首轮红线达标总表**已被其作者声明作废**（"§0–§10 记录的是修复轮之前的状态，红线达标表已过期，勿单独引用"）。因此本清单的"未落地"判定取自 **§R2.x 第二轮复审 + §R2.13 待决策项**，而非首轮总表。

---

## 1. 0911 四层修复的未落地项

### 1.A 运行模型层（A 层）

| 项 | 说明 | 状态出处 |
|---|---|---|
| **A-P0-1 三处绕过** | `_list_archive_dirs` 统一入口未消灭全部 `iterdir()` 自排序 | R2.1 记为 ✅ 已修（三个 `iterdir()` 全消）；若需确认请复跑 `scripts/verify_same_day_idempotency.py` |
| **A-P0-5** | Node 2 三处派生计数器补 `tracking_date < today` 过滤 | 首轮记为"部分"；第二轮未单列 |
| **A-P0-6** | `ambush_position_snapshots` 改自然键 upsert + 收窄静默 `except` | 首轮解包 bug（P0-1）已修；DB 实测该表仍 **0 行**（见下条） |
| **A-P0-9** | 归档侧快照标签 `snapshot_time` / `bar_complete`，只落 `meta.json` | 首轮 ⚠️（伴随 P1-6）；P1-6 已在 R2.1 修，是否收口待确认 |
| **P4 快照路径解析** | `:1933` 仍用 `_extract_sector_from_reasoning` 解析候选 reasoning ⇒ 恒 `""` ⇒ `continue`；**DB 实测 `ambush_position_snapshots` 0 行** | R2.7(A) §8-4 记为"已修"（改 `scoring.sector_name`），副产物 `_extract_sector_from_reasoning` 成死代码。**0 行 ≠ 修复失效，见下注** |
| **OPEN-1…4** | 见 §3 | — |

> 📌 **`ambush_position_snapshots` 0 行的复核结论（2026-09-14）**
>
> 该表 0 行曾被与 P4「已修」并列，读起来像修复未验证。实测**不是**：
>
> | 环节 | 实测（20260914） |
> |---|---|
> | 写路径可达性 | ✅ `buy_allocations` 非空——当日有 2 笔买入（`002040` 1200 股 / `000070` 700 股） |
> | 板块名判据 | ✅ `unified_scoring[].sector_name` 有值（P2-4 生效），仅 `688127` 为 `null` |
> | 交集判定 | ❌ 埋伏池 = `{先进封装, 光刻机}`；买入的 `002040`(港口) / `000070`(CPO概念) 均不在其中 ⇒ `continue` |
>
> **唯一命中的 `002106`(先进封装) 当日未被买入。** 故 0 行是**正确行为**：路径可达、判据正确，只是从未被触发。
> 与 P2-3（`buy_budget` 截断，OPEN-D9）叠加后，这张表的空置可能长期持续——它是**下游症状**，不是 P4 的证据缺口。
>
> 代码位置已漂移：文档记的 `:1933` 现为 `portfolio_decision.py:2016`。

### 1.B 概念一致性层（B 层）

| 项 | 说明 | 状态出处 |
|---|---|---|
| **R-B4 症状恶化** | `航运概念` 从"误归军工"变成掉进**「其他」**，`光刻机` 同落「其他」——因受控词表里**不存在「设备」族**，文档自身期望不可实现 | R2.1 记为已修（实测 `_build_frame_group('航运概念') == '周期资源'`）；`光刻机` 在 R2.9 #6 记为已修（→ AI硬件） |
| **B6 量纲自述** | prompt 自述单位（`东财关注指数{/1000:.1f}`、`NGA提及{}`） | R2.1 未单列，R-B7 首轮记为 ✗ |
| **B7 / OPEN-B1** | 双 LLM 入口（删 / 保留 / 限权）→ 文档建议**保留**现状双重护栏；**未决** | `archive/got_layer_b_plan_0911.md` §7.2 |
| **OPEN-B2 / B3** | 波动率调整接线、单笔风险预算接线 → 文档均建议**不接**，仅标注未接线 | 同上 |
| **OPEN-B4** | `关注指数 ×1000` 的来历 → 先查 git 历史 | 同上 |
| **OPEN-B6 / B7** | `航空` 家族归属（现落军工，标注属民航）；`保险` 是否独立成族 → 均建议本轮不动 | 同上 |
| **`sector_role_map` fallback** | `portfolio_decision.py:2858` 的 `.get(..., "mainline")` **放大风险**而非保守侧 | R2.13 #1（**需裁决**，见 §7） |
| **`archive.py` 双实现并存** | `list_archive_dirs` 与 `persistence._list_archive_dirs` 功能重叠，`_SKIP_DIR_SUFFIXES` 定义两份 | R2.9 #5 记为已修（persistence 为唯一权威实现，archive 改重导出） |

### 1.C 影子验收层（C 层）

| 项 | 说明 | 状态 |
|---|---|---|
| **C-P6** | M1 数据源改 glue 侧（`strengths[].news_read ∉ {low, unknown}`），规则侧并列对照列 | ✅ **已落地（2026-09-14）**——`scripts/verify_glue_metrics.py` 主口径已切换，规则侧降为对照列；⚠️ **只解除死锁，不解除 M1 不可达**（34.4% 仍 < 60%，两者是独立问题） |
| **C-P7** | M6 改为「其他」回收率（「无归属」计为未回收；覆盖率降为观察项） | ✅ **已落地（2026-09-14）**——同上；⚠️ 回收率（分母 = 原「其他」11 个）**≠** M6′ 覆盖率（分母 = 全部 32 候选），不是同一个量 |
| **C-P8** | 明确 M4 系数 = Spearman，增注"M4-a 的独立性以 B1 修复为条件"，M4-a 红线数值标记「待标定」 | ✅ **已落地（2026-09-26，纯注记零行为变更）**——`scripts/verify_glue_metrics.py` docstring 增「C-P8 红线口径声明」三项（判定系数=Spearman / M4-a 独立性以 B1 修复为条件且 B1 现状仍未修 / 0.6 标待标定归 C-P13），`archive/got_layer_c_plan_0911.md` §6.1 与 [`verification.md`](verification.md) §3 同步；**不改任何计算与 PASS/FAIL 逻辑**，R-C3 十锚点复验逐字节一致 |
| **C-P9** | `GlueStrength` 新增 `basis` 字段 + prompt 写明替代综合评分 | ✅ **两半均已落地**（2026-09-14 复核）——字段见 `schemas.py:1086-1089`（`default="composite"`，commit `8ad119b`，09-12）；prompt 见 `aggregator.py:824`「**此字段替代的是综合评分（技术面+资金面+消息面+情绪面）**，不是只替代技术面」。原记「无生产者」字面成立但**会误导**：`basis` 是恒真的口径声明，语义在 prompt 里，**不需要赋值语句**；归档侧 0911 起可见 `basis="composite"` |
| **C-P11** | 回填跑批（`--got-replay 20260901`…`20260910`），影子样本 0→**8 天** | ✅ **已执行（2026-09-26）**——开 `AGENT_SEMANTIC_GLUE_IN_REPLAY=true` 逐日回放 0901/0902/0903/0904/0907/0908/0909/0910（共 8 日，每日 1 次 glue LLM 调用），产物落 `{date}/_replay/agent_glue.json`（不污染生产归档）；8 份均 `status=ok`、**md5 两两互异**（非占位态）；`verify_glue_metrics.py` R-C6 有效样本由 **0 天→ 8 天**（旧 §4.1 占位态覆辙已避） |
| **C-P12** | prompt A/B（保守版 vs 全量归属版），输出 M2×M3 对照曲线 | ✅ **已实施（2026-09-26，D-C6 裁决执行）**——两版文案经负责人拍板后各跑 9 天 `--got-replay`：A 保守版（现行 prompt + 「省略不是错误」澄清句）vs B 全量归属版（任务1 禁 unknown + 任务3 全量归属低置信保留 + 禁止编造护栏）；**双保险隔离**：`GLUE_PROMPT_VARIANT=a|b`（文案，仅 replay 生效）+ `GLUE_REPLAY_VARIANT=variant_a|b`（目录）+ `prompt_variant` 留痕 + `--compare` 目录尾缀一致性校验。产物落 `{date}/_replay/variant_a|b/agent_glue.json`（0901/02/03/04/07/08/09/10/14 共 9 日，均 `status=ok`、md5 互异）；`verify_glue_metrics.py --compare` 出 **M2×M3 对照矩阵**（A 均值 M2=35.5%/低置信 6.8%，B 均值 M2=46.3%/低置信 40.8%，印证 §2.6 拉锯；矩阵见 `cp12_prompt_variants_draft_20260926.md` + 预审表 `cp12_m3_audit_20260926.md`）。⚠️ **0902 披露**：B 版输出撞 max_tokens=16000 截断，临时用 24000(A)/32000(B) 重跑，该日与其他日(16000)不对称、破坏单日对照同参，标定读数须剔除/标注。R-C6 **对照矩阵完整**（两版各 9 天 ≥8，达标判读归负责人）；新增 `tests/test_glue_prompt_variant.py`；R-C3 锚点复验不变（prompt 变量不进规则侧口径）。解锁 C-P13 标定 + OPEN-C1 阈值重标的样本输入 |
| **C-P13** | 红线标定 | ✅ **已拍板 C-P13a（2026-09-26）：等 C-P12 A/B 样本齐后一并标定**——**样本已齐**（C-P12 两版各 9 天 M2×M3 对照矩阵已产出，见 `cp12_prompt_variants_draft_20260926.md`，含 0902 max_tokens 不对称披露）；标定数值仍归项目负责人，脚本不代判（注：有效对照日 8 天，0902 建议剔除）。**【事件级复核（2026-09-27）】**：`cp12_event_level_ab_20260926.md` 揭示两项新质量发现——⚠️ **0902 A 版为退化重复循环（191 条垃圾，应整日作废而非降权）**、⚠️ **B 版存在真·过度归属幻觉（0903 房贷→24 板含减肥药/短剧游戏）**，现有 10 行分层预审样本抽不到高扇出病态事件、会低估 M3_B；**架构结论**：A/B 是同一生成架构在召回/精确单旋钮上的两端，拉锯无解，应转向「接地式召回-验证」管线（设计笔记见 `cp13_grounding_pipeline_research_20260926.md`，红线 0.6 应标在校准后的置信度而非自报 confidence） |
| **C-P3** | `replay_mode` 下允许 glue（受新开关控制，默认 `false`） | 首轮 ⚠️（replay 分支新增落盘副作用，违约） |
| **`0903.md` §8.1 口径** | M1′/M6′ 行的字段路径与数值三重错误（路径不存在 / 数值是规则侧的 / 正确值未采用） | R2.4；R2.7(A) P1-8 记为"部分修"（三子项均未按 C 层定义落地） |

### 1.D 0902 对账层（D 层）

修复清单 D-P1…D-P14 中**未落地或部分落地**：

| 项 | 说明 | 状态 |
|---|---|---|
| **D-P1** | `divergent` 阈值与分位可观测（样本数 / 5% 分位 / `min()` 后最终阈值 / 每确认板块落盘） | 部分——缺分位落盘 |
| **D-P2** | 埋伏闸门逐层漏斗 `ambush_gate_funnel`（含 `evaluated` / `skipped_reason`） | ✅ **已落地（2026-09-26）**——`aggregator.py:2386-2397` gate_funnel 各闸补 `skipped`/`skipped_reason` + `_gate_stat` 计数唯一入口；死闸（`hot_sectors` 空 ⇒ G2/G3）与资金流缺失（`accum_strength=="missing"` ⇒ G4）改记 `skipped` + reason，不再虚计 `evaluated`；**不改 `_run_ambush_gates` 判定逻辑**；单测 `tests/test_node6_ambush_funnel.py`（空 hot_sectors→skipped、决策路径快照不变、同日重跑逐字节幂等） |
| **D-P3** | 定义并落盘「正式池水位」目标 `AMBUSH_POOL_TARGET_RANGE` | 部分——常量双源（`archive.py` 仍硬编码 `(2,6)` + 过期 TODO）；`AMBUSH_POOL_MIN_DAYS` 零消费者（R2.9 #3 记为已删） |
| **D-P5** | 闸门文档诚实化（「4 层闸门」→ 标注 G2 注意力闸 / G3 未启用 + 加「未评估」留痕） | ✅ **已承接（2026-09-26）**——`architecture.md:506-509` 已标注 G2 注意力子闸 / G3「结构性不可达」（09-26 更新）；剩余「未评估」留痕由 D-P2 的 `skipped_reason` 承接（真正的载体在 `architecture.md`，非 `CLAUDE.md`），无单独动作 |
| **D-P9** | `scripts/check_audit_claims.py` | 已建并入版本控制；实跑 `unverified = 9`（**2026-09-26 收口复算：`unverified = 7`**，差异均可归因，见 §2 注） |
| **D-P10** | 附录 A 的可复算性（与 R-D2 对齐） | ✗——实算 4 项中 2 项与文档不一致 |
| **D-P11** | E3 第三层：修解析 + 落盘分支走向 | 首轮"仅解析层"（P1-4），R2.1 记为已修（改由 `sector_role` 决定） |
| **D-P12** | E3 第一层：口径声明 + 落盘绑定次数（**不归一化**） | 部分——✅ **绑定次数已落盘（2026-09-26）**：`portfolio_decision.py:219-241` 从已生成 `allocation` 反推 `buy_allocation_count` + `binding_count`（命中显式 limit 的买入次数，不归一化）并入 `sector_exposure_binding`，schemas 描述同步；**仅可观测留痕、不参与决策**（接线 = 拍板项，未做）。**仍残留**：`PortfolioDecision.sector_exposure_binding` 无下游消费者 |
| **D-P13** | E3 第二层：改读 `sector_role` | 首轮 ✗（只有 `sector_role = None` + `pass` + TODO），R2.1 记为已修（改读 `sector_role_map`） |
| **D-P14** | E10 删死参数 + 修注释 | R2.1 记为 ✅ |
| **`_temp_check.bin`** | 仓库根游离调试残片 | R2.1 记为已删（本次核验：确不存在） |

### 1.E 全修轮之后仍残留的项（来自 §R2.x）

| 项 | 位置 | 说明 |
|---|---|---|
| 注释与实现不符 | `portfolio_decision.py:2837-2838` | ✅ **已自愈**（2026-09-26 核验）：代码位置已漂移至 `src/got/nodes/portfolio_decision.py:3819-3821`，注释已如实写明「未命中映射时 fallback 为 `"mainline"`，反推逻辑已在 D-P13 移除，`sector_exposure_limits` 仅作参考值落盘」——原「注释虚构反推逻辑」的不符状态解除。**仅剩 fallback 取 `"mainline"` 的策略裁决，归 §7 #1，本项不再单独跟踪** |
| `sector_limit_detail` 落无用字段 | `portfolio_decision.py:2876-2878` | 仍落 `role_threshold` / `stock_limit`，但分支已不由它们决定 |
| `sector_role_map` fallback | `portfolio_decision.py:2858` | 与 §1.B 同项——放大风险，**需裁决** |

### 1.F 验收工器的能力缺口（2026-09-14 实测新增）

两项**工具/接线缺口**，它们是「红线跑不出结果」的直接原因。**与代码 bug 无关**——是验收工器本身不具备产出输入的能力。

| 项 | 位置 | 说明 |
|---|---|---|
| **`--got-replay` 只跑 Node 6** | `main.py:1318-1365` | 注释自述「只跑 Node 6，从归档加载数据，不跑 Node 1-5，**不写归档**」，仅产出 `_replay/unified.json`。⇒ ① `portfolio.*` 字段缺失（R-B8 需要的 Phase 2 未跑）；② `sector.json` 缺失（Node 2 未跑）。**更根本的是②**：B 层修的是 `family_keywords.py` + `sector_analysis.py`（**Node 2** 词表），而该回放**验不到 Node 2** ⇒ `got_layer_b_plan_0911.md` §6.3 写的「跑 Node 6 + Phase 2」与 B 层实际改动位置对不上。R-B8 / R-B9 / R-C7 均受此阻塞。**【2026-09-26 注】文档层已诚实化（`architecture.md`/B 层计划已标注回放只验 Node 6）；是否扩链路（Node 2 依赖当日外部行情、历史不可复现）归 §7 裁决 #6，本项维持开放** |
| **Phase 2 路径不落 T1 归档** | `stock_screener.py:958` vs `:2784` | ✅ **已自愈（2026-09-26 实测收口）**——T1 verdict 自 09-15 起由 `_save_shadow_records()`（`portfolio_decision.py:3063`）落 `shadow_t1.json`，实测 6 个归档日（0915/0916/0917/0921/0922/0924）有产物。原文描述仍成立：`_save_got_result()` 只在 `run_got_mode()` 内被调用，`data/got_screener/` 停更的是**选股回测快照**（另一产物，Standalone 模式仍产出），非 T1 verdict——两者语义不同，不属同一缺口 |

> 📌 第 2 项对 A5 影子期的连带影响见 [`verification.md`](verification.md) §1.2。

---

## 2. 0902 报告 15 项的**处置表**

`archive/GOT_0902_audit_report.md` 的 15 项，逐条给出真实处置。
**本表取代原先的「10 项未修」**——那 10 项里有 4 项实为「待退役」（修了会随 §6.1 删除而消解），1 项已改归属，1 项降级为口径标注，2 项挂起，1 项明确延后，只有 1 项是真的未排期。

> **2026-09-26 收口更新**：A6 阶段 2 落地后复算（`check_audit_claims.py`），原「待退役」4 项处置已逐条更新——P0-1 / P0-2 随退役消解，P0-5 自愈，P2-1 因 T5 对齐层接线后重判保留而**不再随退役消失**，改挂起待裁决。

| 编号 | 级别 | 现象 | 处置 | 出处 |
|---|---|---|---|---|
| **P0-1** | 立即 | `capital_check="divergent"` 是死分支（生产端从不 return，消费端 4 处检查它） | ✅ **已随退役消解**（2026-09-26） | 阶段 2 已删 `_cross_check_capital` + `_divergent_threshold` + 阈值常量（§4.2 动作 1）。`divergent` 现由 glue `capital_read=low` 产出，0917 起 6 个归档日有触发（20260924=22 条）——原「死分支」症状已不存在。`check_audit_claims.py` 仍记 INEFFECTIVE 系旧声明指标形状消失，证据行已消歧（见 §4.2） |
| **P0-2** | 立即 | `confidence` 恒 0.7——三个加分项全悬在 #1/#2/#3 上（`aggregator.py:790-800`） | ✅ **已随退役自然消解**（2026-09-26 复算） | 阶段 2 后 confidence 分布：规则侧基础 0.5 / 0.5+0.2，glue 命中板块取 `st.confidence`。20260924 实测 `{0.5:8, 0.55:8, 0.6:5, 0.65:4, 0.7:3}`，0.7 占比 10.7%（<50% 阈值）——塌陷解除，脚本判 EFFECTIVE |
| **P0-3** | 立即 | 粮食概念 -4.26% 仍 `active_mainline` A 档 | ✅ **已生效** | — |
| **P0-4** | 立即 | 液冷服务 tier=S 却标 exhausted | **不修（行为正确）** | 走 exhausted→active 回归路径，弱反弹不触发是正确行为 |
| **P0-5** | 1 周内 | `ambush_pool` 全被闸门拦死（8 个埋伏板块 → 池空） | ✅ **已自愈**（2026-09-26 复算，待负责人确认了结） | 原归因「闸门判据即 §6.1 #10/#11，随退役消失」已作废——接线后重判 T5 对齐层**保留**（§4.2）。实际走势：近 12 归档日池规模 0–5（0914=3、0915=5、0921=1、0923=1、0924=1），「池空」症状不可复现；归因是上游候选质量改善还是运气，不追认 |
| **P1-1** | 1 周内 | 持仓股 `name == symbol`（600250 / 605566） | **明确延后** | OPEN-D8：排在下一批 |
| **P1-2** | 1 周内 | reasoning 中同一板块出现 ×3 | ✅ **已生效** | `portfolio_decision.py:1526-1535` `seen_canons` set 去重（D-P7）；⚠️ 原「代码未去重」的记载已过期 |
| **P1-3** | 2 周内 | 50 只热股 `mention_count` 极差 <3%，无区分度 | **降级口径** | D-P8：加 `mention_source` 标注字段，**数值不动** |
| **P1-4** | 2 周内 | `associated_sectors` 50/50 全空 | **挂起待查** | OPEN-D2：是否真无可靠数据源 |
| **P1-5** | 2 周内 | `reasoning.md` 中 Node 7 段出现 ×2 | ✅ **已生效**（见下注） | D-P6 → 并入 A-P0-8；`archive.py:642` 覆盖写 |
| **P2-1** | 按需 | `affected_sectors` 不区分正负面 | **挂起待裁决**（原「待退役」作废，2026-09-26） | 原预期「随 §6.1 #10/#11 退役消失」不成立：T5 对齐层接线后重判**保留**，该字段仍被 `_group_events_by_sector` 扇出与 `portfolio_decision.py` 新闻加分活消费（`list[str]` 类型不变）。是否升级 `list[dict]` 转 §7 裁决 #8 |
| **P2-2** | 按需 | 催化剂日历膨胀至 92 个事件 | **症状消失，无归因** | — |
| **P2-3** | 按需 | `current_position_ratio` 被 `min(1.0, raw)` 截断 ⇒ 26 个归档日中 **13 天 `buy_budget = 0`**，一笔都买不了 | **挂起待查** | OPEN-D9：截断不可证伪，建议落盘 `raw_position` |
| **P2-4** | 按需 | "其他"家族成垃圾箱 | ✅ **已生效** | "其他"占比 40.0% → 34.4% |
| **P2-5** | 按需 | 破位信号不落盘，状态机决策无法离线复现 | ✅ **已生效** | 09-14 落 `break_signals` 83 条 |

> **⚠️ P1-5 的「已生效」与 `check_audit_claims.py` 的判定相反，本节以归档为准。**
> 脚本判 P1-5 为 `INEFFECTIVE`，理由是"修复代码尚未产生修复后归档，20260904 仍可复现重复"。
> **该理由不成立**：修复于 commit `8ad119b`（**2026-09-12**）落地，而 `20260914` 是修复后归档，
> 且当日**至少运行 2 次**（旁证：`node4_events` 中 `mention_count=1` 而 `mention_count_run_based=2` 的事件），
> 其 `reasoning.md` 中「Node 7」仅出现 **1 次**。逐日实测：
>
> ```
> 0827:1  0828:1  0831:1  0901:1  0902:0  0903:1  0904:2←修复前  0907:1  0908:1  0909:1  0910:1  0914:1
> ```
>
> 0904 的 ×2 是修复前归档；09-07 起无重复。脚本把**修复前的历史归档**当成了修复失效的证据。
> 因此 **R-D1 的正确读数是 8，不是脚本输出的 9**（差 1 即 P1-5）。脚本缺陷见 §9。
>
> **2026-09-26 收口复算**：A6 阶段 2 落地后重跑，脚本 `unverified = 7`（P0-1 为退役后形状消失的残留计数，见 §2 表）。剩余 6 项均有归属（见 §2），R-D1 作为 burndown 已从 10 降到 7，本身不再阻塞任何验收。

**统计**（2026-09-26 收口后）：已生效 6（P0-3 / P1-2 / P2-4 / P2-5 + 随退役消解 P0-1 / P0-2）+ 不修 1（P0-4）+ 症状消失 1（P2-2）+ 自愈待确认 1（P0-5）= **9 项了结**；
降级口径 1 + 挂起 3（P1-4 / P2-3 / P2-1）+ 延后 1 = **5 项各有归属**；**真·未排期 = 0**。

---

## 3. OPEN 挂起项汇总

### 3.1 主文档（`archive/agent_glue_plan_0903.md` §9）

| # | 项 | 要点 |
|---|---|---|
| OPEN-1 | 仓位链路 | 4 条已验证事实：W1 仓位超限无减仓路径（不减仓反而继续买入）；W2 两阶段结构已存在，只需给 Step 1 补卖出来源；W3 `total_equity` 与 `accounts[0]` 口径不一致（5 处）；W4 从 `reasoning` 字符串解析板块名的脆弱耦合 |
| OPEN-1b | `REFINE_MAX_SECTORS` 混合口径 | `REFINE_MAX_SECTORS=20` < 0903 confirmed 24 ⇒ 至少 4 个板块从未进入 LLM 视野；D5 后 glue 视野=全部候选，混合口径自动消失，但 narrative 分支仍用该上限 |
| OPEN-2 | 情绪面信号只到展示层 | `contrarian_signal` / `trading_signal` 不进入仓位公式也不进入准入判定，纯展示态 |
| OPEN-3 | exhausted 判定与当日涨幅 | 原依据的 `one_day_drop` 已于 0902 P0-3 移除，需按现行 4 条判据重新评估 |
| OPEN-4 | `INDEX_CONFIG` 权重配对三重缺陷 | X1 `zip` 静默截断 + 分母不归一；X2 原方案修法会直接踩坑（X1 是硬前置）；X3 两套 key 空间无校验；X4 降级到单指数时"一致性"恒为真 |
| OPEN-5 | 数据完整性自检 | Y1 `missing_fields=[]` 硬编码恒空；Y2 `crowded_families` 无消费者；Y3 `lifecycle_state` 全 null；Y4 F1-8 只打通一个字段 |

### 3.2 A 层（`archive/got_run_model_plan_0911.md` §7.2）

- **OPEN-1** 进程级互斥（0910 run#3/#4 时间重叠）→ 建议**本轮不做**，仅 A-P0-8 降一半风险
- **OPEN-2** 可复现性（`meta.git_commit` 不代表运行时代码；LLM 非确定）→ 建议落 `git_dirty` + `prompt_hash`
- **OPEN-3** `mention_count` 口径切换 → 建议双写一天，比对分布无位移后切
- **OPEN-4** 15:00 不是"日线走完"唯一判据 → 先用墙钟

### 3.3 C 层（`archive/got_layer_c_plan_0911.md` §7.2）

- **OPEN-C1** `transition_count` 时间炸弹（约 2026-09-25）—— ✅ 语义缺陷已随 §7.1 实施修复（改统确认切换事件，不再依赖输出值存量）；阈值 5 未重标（原建议③），继续观察。**【已拍板 OPEN-C1a（2026-09-26）】**：接受「不区分正负面/暂不重标」风险并维持阈值 5，待 C-P12 样本齐后与 C-P13 同批重标（当前样本 <40 交易日，重标无数学依据）。**【样本更新（2026-09-26）】**：C-P12 A/B 各 9 天矩阵已齐，可作为重标输入；但 glue 样本窗（0901–0914）仍 <40 交易日，OPEN-C1a「暂不重标、继续观察」判定不变
- **OPEN-C2** M3 人工流程未定义
- **OPEN-C3** `persistence_regime == "low"` 也是 11/11 恒真 → 与 OPEN-C1 同批排查
- **OPEN-C4** M1 的信息量到底多大 → 由 C-P12 曲线回答，**不预设**——✅ 曲线已产出（2026-09-26）：M1′ A 均值 43.6% vs B 均值 39.0%（B 反低，因任务1 强制 low 垫底拉低均摊）；具体信息量判读归负责人
- **OPEN-C5** `agent_glue.json` 的当日覆盖语义与回填产物是否同语义

### 3.4 D 层（`archive/got_layer_d_plan_0911.md` §7.2）

OPEN-D1（P2-2 界点归因）、OPEN-D2（`associated_sectors` 是否真无可靠数据源）、OPEN-D3（`CATALYST_CONFIDENCE_GATE` 校准样本）、OPEN-D4（`AMBUSH_SCORE_FLOOR=0.30` 来源与带宽）、OPEN-D5（蓄水池驱逐规则 FIFO vs 质量）、OPEN-D6（P2-3 `raw_position > 1.0` 语义）、OPEN-D7（正式池水位目标取值，暂定 `(2,6)`）、OPEN-D8（P1-1 新归属，排在下一批）、OPEN-D9（`current_position_ratio` 截断不可证伪，建议落盘 `raw_position`）

### 3.5 B 层（`archive/got_layer_b_plan_0911.md` §7.2）

OPEN-B1…B7 —— 见 §1.B。

---

## 4. A6 转正与退役

### 4.1 阶段 1 · 接线（2026-09-17 已完工）

**背景**：8 交易日影子期实测 glue 相对规则侧 M1 +25.5 pp / 13.1×、M6 +31.4 pp，M4-b/c 7/7 PASS；0915 归因为 Node 2 上游故障（`sector.frame_groups=0` → glue `S2_parse_failed`），不计入降级样本。项目负责人裁决直接转正。

**已完成项**：

- ✅ 新增 `GOT_A6_ENABLED`（`src/got/catalyst_config.py`，默认 `true`），`AGENT_SEMANTIC_GLUE_SHADOW` 默认翻为 `false`
- ✅ `_build_sector_intel` Pass 1.5 新增 `_apply_glue_override_to_raw_data`，以 `_GLUE_CAPITAL_READ_MAP` / `_GLUE_NEWS_READ_MAP` / `_GLUE_SENTIMENT_READ_MAP` 三套词汇直接替换 `composite_score / capital_check / news_check / sentiment_check / confidence`；`glue.degraded_sectors` 保留规则侧兜底
- ✅ 4 个消费点接线：`run()` 中 `refine_events_map` 改走 `_glue_aware_events_map`（含 `code_to_name` 反查）；`_llm_refine_sectors` 在 `self._a6_glue_active=true` 时跳过 adjustments 分支（narrative / cross_sector_risks 保留）；`_deterministic_catalyst_promotion` 自动跟随 glue-aware events_map；`_synthesize_mainline_endurance` 新增 `glue` 参数 + family_name 三级优先（`glue.families[code].family` → `intel.frame_group` → `intel.sector_name`）
- ✅ E2 公式：`MainlineEndurance.family_resonance` 分母从固定 50.0 改为当日家族 `std_dev` P90 自适应归一化（P90=0 时 fallback 50.0）
- ✅ 归档 schema：`UnifiedContext` 新增 `glue_consumed: bool` + `glue_status: Optional[str]` 两个字段，**不删除**任何现有字段（阶段 2 才删）
- ✅ 单元测试：`tests/test_a6_wiring.py` C1–C5 五个用例，行为面验收锚点 C3 替代 R-C7
- ✅ 回归：`pytest tests/ -k "node or got or portfolio or ambush"` 293 passed（排除 `test_discord_platform.py` 的 `nacl` 依赖问题，与本次无关）

**状态**: ✅ **已完工 (2026-09-17)** — 见 commit `91b64d2` 及后续整合提交

**保留项（阶段 2 前不动）**：

- `_cross_check_capital/news/sentiment` 三件套继续计算，仅结果不写入 SectorIntel（僵尸期）
- `SectorIntel.family_resonance` 字段保留（schema 兼容），下游 L2996-3003 / L3091 / L3124 / L3645 活代码仍读归一化后的 [0,1] 值
- `_llm_refine_sectors.adjustments` 分支代码本体保留，仅用 if 早退
- `AGENT_SEMANTIC_GLUE_ENABLED` 保留（阶段 2 前不能关，否则 glue 不跑就没数据）

> ✅ 上述「阶段 2 前不动」保留项已于 **2026-09-26 阶段 2 退役**全部删除（三件套僵尸期结束、`SectorIntel.family_resonance` 删字段、`adjustments` 分支本体删），详见 §4.2「已执行动作」。`MainlineEndurance.family_resonance`（E2 公式，与 `SectorIntel` 同名异体）保留。

### 4.2 阶段 2 · 退役（✅ 已执行，2026-09-26 负责人裁决提前跨过条件 2）

**触发条件**（原三条）：
1. 观察窗口 ≥ 3 个交易日 `glue_consumed=true`，且 `S1/S2` 降级率 ≤ 10%
   —— ✅ **已过（2026-09-26 核验）**：0917/0918/0921/0922/0923/0924 六个归档日全部 `glue_consumed=true` + `status=ok`，降级率 0%（0/30、0/34、0/28，无 degraded 取证文件）
2. 决策面结果无回归事故（Node 7 推荐 T+3 胜率相对 9 月基线不劣化）
   —— ⏩ **负责人裁决提前跨过（2026-09-26）**：条件 2 T+3 样本未收齐（首批可收日 0930 起）即执行退役。已有读数：0922 批 T+2 胜率 33.3% / alpha -0.72%（n=3，相对基线 28.1% / -1.67% 不劣化）。全局回退本已失效（`aggregator.py` run() 入口对 false/None/非 ok/degraded 全 raise），删除不损失活的安全网，故接受提前执行；取证脚本 `.review_tmp/backtest_a6_condition2.py`
3. ~~0924 前 `transition_count` 未跌破阈值 5（OPEN-C1 时间窗）~~
   —— ✅ **已移出（2026-09-26 负责人裁决）**：实测序列 10→9→8→7→5→5（字面擦边未跌破），但该指标不携带市场信息——状态机自 0827 接线以来零次真实切换（`previous_regime==regime` 恒等），读数完全由窗口滚动的历史残留决定（即 OPEN-C1 预告的时间炸弹）。按 §0.1 判定标准属缺陷红线（同 §9 谱系），移出触发条件；停摆根因专项见 §7 裁决 #7

**已执行动作**（`archive/agent_glue_plan_0903.md` §6.1 / §11.3）：
1. ✅ 退役 §6.1 三件套本体：`_cross_check_capital/news/sentiment` + `_lookup_capital_flow` + `_divergent_threshold` + `_parse_capital_flows` + `_extract_news_keywords` + 专用常量（`_NEWS_*`/`_CAPITAL_DIVERGENT_*`/`_CAPITAL_ALIGNED_MIN_YI`/`_UNRELIABLE_MATCH_TYPES`）全部删除
2. ✅ `_compute_sector_scores` 精简为「tech + lifecycle 修正」，`capital_check`/`news_check` 返回 `neutral`、`sentiment_check=missing`、detail 文案置「规则模式已退役」——漏命中/降级板块**放弃逐板块兜底**，改为 neutral 中性垫底（负责人裁决）
3. ✅ Pass 1.5 删 `rule_capital_check`/`rule_capital_detail` 留存；`_build_sector_intel_from_scores` 删对应传参；`schemas.py` 删 `SectorIntel.rule_capital_check`/`rule_capital_detail`
4. ✅ 删 `_llm_refine_sectors.adjustments` 分支代码本体（保留 narrative / cross_sector_risks）
5. ✅ 删 `_detect_glue_capital_mismatch`（Q8-A2）及其调用；D-P1 资金背离诊断 `_build_capital_divergence_info` 删除，`UnifiedContext.capital_divergence` 置 `None`（schema 字段保留，archive 仍读）
6. ✅ `GOT_A6_ENABLED`/`AGENT_SEMANTIC_GLUE_ENABLED`/`SHADOW` 开关保留为 fail-fast（置 false 直接抛错，不静默改行为）

**§6.1「删」清单接线后重判（防误删，均修正为保留）**：
- `_match_sector` / `_GENERIC_ROOTS`：仍供 `_lookup_flow_map`（吸筹确认闸门 `_check_accumulation_gate` 的资金流比对）消费 → 保留
- `_parse_sector_level_flows` / `_CAPITAL_CHANNEL_TERMS`：仍供 `_detect_family_capital_mismatch` 消费 → 保留
- `_group_events_by_sector` / `_lookup_sector_events` / `_extract_hot_sectors`：glue 事件供给 + 埋伏/蓄水池路径仍消费 → 保留
- `MainlineEndurance.family_resonance`（E2 公式）：保留；`SectorIntel.family_resonance` 全仓无读取方，已随本次删除

**回归**：`pytest tests/` got 域 248 passed / 0 failed；全量 3704 passed，25 failed 均为无关环境项（docker/nacl/pypinyin/network/config），无本次改动引入的失败。`tests/test_node6_glue.py::TestGlueCapitalMismatch` 随 `_detect_glue_capital_mismatch` 一并删除。

> ℹ️ 原阶段 1 观察期「不可提前删三件套」的告警已被负责人「提前跨过条件 2」裁决覆盖；删除依据是全局回退已失效 + glue 命中板块覆写后规则值本就丢弃，对命中板块零行为变更，唯一受影响者为漏命中/降级板块（改 neutral 垫底）。

### 4.3 回滚开关

`GOT_A6_ENABLED=false` 一个环境变量即回到影子期行为（glue 归档但不消费），零代码回退。两个开关组合形成三态：

| `AGENT_SEMANTIC_GLUE_ENABLED` | `GOT_A6_ENABLED` | 行为 |
|---|---|---|
| true | true | **A6 生效**（glue 主导 4 个消费点） |
| true | false | **影子期**（glue 归档不消费） |
| false | – | **完全禁用**（glue 不跑，`status=S6_disabled`） |

---

### 4.4 持仓板块漂移修复（2026-09-18 完成）

**背景**: 持仓股 688598 金博股份被误判卖出（板块名漂移导致买入理由丢失，落入 `sector_role not in ("mainline","ambush")` → sell）。

**Git 提交**: `20cba78ea46017773e0c192349c9232774af8ef6` (branch `fix/sector-drift-grace`, 2026-09-18)

**已完成项**:

- ✅ **Fix 1: 家族中继匹配** (`_family_relay_match`, `portfolio_decision.py:1848-1901`)
  - 板块名→normalize→frame_group_lookup→家族名→锚点板块
  - strength ×0.85 打折（避免虚高），weight=1.2
  - 过热降权继承接线（不可逃逸）
  
- ✅ **Fix 2: 买入论点快照** (`holding_thesis_snapshots` 表 + CRUD 方法)
  - 新表：`HoldingThesisRecord` (`storage.py:1257-1285`)
  - 写入：`_write_thesis_snapshots` (买入时 UPSERT)
  - 读取：`_load_holding_theses` (持仓分析时查询)
  - 关闭：`_close_thesis_snapshots` (卖出时标记 closed)
  - 强度打折：×0.75（比当日 boards 中继再低一档）
  
- ✅ **Fix 3: 卖出宽限** (`_drift_action_with_grace`, `portfolio_decision.py:2537-2595`)
  - Invesco 非对称门槛判定
  - 家族锚点 ≥60 → hold + 置观察
  - 家族锚点 [40,60)：首次 reduce，跨日 sell
  - 家族锚点 <40 → sell（不宽限）
  
- ✅ **可观测性字段** (`schemas.py:986-999`)
  - `sector_match_type`: direct/relay/thesis/fallback/none
  - `sector_drift_grace`: bool（家族宽限标记）

**测试验证**:
- 单测：`test_reverse_lookup.py` (3 用例) + `test_portfolio_decision_lot_rejection.py` (3 用例) = **16/16 PASSED ✓**
- 代码审查：家族中继逻辑正确，过热继承接线完整，五路径全覆盖

**根因修复**:
- 688598 金博股份误卖问题彻底解决
- 买入理由继承机制建立（前向从下一次买入开始积累）
- 漂移宽限窗口防止 whipsaw（Bethke 2026 证据）

**状态**: ✅ **已完工 (2026-09-18)** — 待生产验证后从 backlog 移出至 verified_completion_log

---

## 5. 后续 Phase

| Phase | 内容 | 前置 |
|---|---|---|
| **Phase 3b** | remote §8 分歧摘要 + SkillAggregator + RiskOverridePlan | **A6 之后**（同改 `aggregator.py` 聚合核心） |
| **Phase 4b** | remote §5 MarketThemeContext Step 2（mootdx 板块排名接入） | **A6 之后**；Step 1（模型定义）已完成 |
| **Phase 4d** | remote §7 AlphaSift 选股引擎 | **A6 之后**（归零率基线重标 D-3） |
| **Phase 5b** | remote §10 TickFlow 数据源 | **需申请 API Key** |
| **Phase 5c** | glue P1 调参 + OPEN-1 家族仓位 | A6 转正 |
| **P0-B B3** | `crowded_families` 处置（Node 2 停产 vs 修正口径） | **需与 Node 2 协商**（独立决策） |

### 5.1 remote 中未被主文档承接的模块

`archive/remote_integration_plan.md` 的 §14.1 A3、§14.2 B4–B8、§7 影子期强制验收指标，在主文档 `agent_glue_plan_0903.md` 的 §11.1 / §11.2 / §11.3 中**无任何落点**：

| 项 | remote 原文 | 主文档状态 |
|---|---|---|
| §14.1 **A3** | 单代理路径不消费记忆（默认 `single` 架构下记忆注入与校准全部休眠） | 未提及 |
| §14.2 **B4–B8** | 仪表盘解析重试 / 双轨 Prompt 维护负担 / 阶段顺序靠 Prompt 约束 / 上下文压缩只覆盖 chat / `stock_scope` 护栏只在 chat 路径 | 未提及（仅 §10.2 模块地图用省略号带过） |
| §7 影子期验收 | 候选池归零率 / near_miss 覆盖率 / `fetch_snapshot_with_fallback` mootdx 前置 | 仅提"重标基线"，三项强制验收标准未逐条承接 |

---

## 6. Phase 6：盘中 0→1 确认闸门

`archive/intraday_gate_plan_0907.md` 状态：**草案（未实施）**。

**硬前置（Phase 6-0 数据地基）**——文档自述"瓶颈不在算法，在数据地基"：

| # | 缺失 | 核查事实 |
|---|---|---|
| F3 | 五档买**量** | `get_realtime_quote` 只取价未取量；通达信 `quotes()` 是否返回 `bid_vol/ask_vol` 列**待核实**（OPEN-I2） |
| F4 | 分时序列 / 分钟 K 线 | 全量检索**无**相关接口 |
| F5 | 集合竞价 | **无接口** |
| W1 | 盘中调度器 | `--got` 日频一次性，无 9:26 / 11:25 / 14:45 定时 |
| W2 | 盘中归档/回放 | `archive` 仅日频 |

文档明示：**不可在地基未验收前开影子期**。

**OPEN-I1…I5**：策略定位边界（打板 vs 低吸）/ 五档量可得性 / 实时性 vs 成本 / 与持仓侧的关系 / Agent 仲裁必要性。

---

## 7. 需裁决事项

> 📄 **2026-09-26 决策简报**：本表中的硬裁决项（#1/#3/#4/#5/#6 + C-P13/OPEN-C1 重标）已逐项给出选项/影响面/实测证据/建议，详见 [`decision_brief_20260926.md`](decision_brief_20260926.md)。
> ✅ **2026-09-26 已拍板并实施（同日第二批）**：#1a / #3a / #4b / #5a / #6a / C-P13a / OPEN-C1a，逐项结果见下表「已拍板」标注与简报「拍板结果」节。

| # | 项 | 性质 |
|---|---|---|
| 1 | `sector_role_map` 的 fallback 是 `"mainline"`（**放大风险**而非保守侧） | ✅ **已拍板 #1a（2026-09-26）：保持 `"mainline"` 不变**。理由：实测归档侧显式未命中样本 = 0，修一个疑似无害的分支只有理论收益，且改取值会使全部历史回放基线漂移。注：「板块名非空但查不到→mainline 1.0×」与「验证清空→unknown 0.55×」两处口径不一致为**已知并被接受**，再翻案需新证据 |
| 2 | ~~`GlueStrength.basis` 无生产者~~ ✅ **已结**（`basis` 是常量口径声明，语义落在 prompt `aggregator.py:824`，见 §1.C C-P9）；**仅剩** `HotStock.mention_source` 的 `nga_mention` 值无生产者（实测只有 `sentiment_analysis.py:805` eastmoney / `:836` xueqiu 两个生产者）。**【2026-09-26 半结】**注释层已落：`schemas.py:544` 声明 `nga_mention` 为预留值、当前无生产者（A6 退役后 NGAS 源不在链路）；是否接线需先定语义（下表） | 口径声明类，接线需先定语义 |
| 3 | `signal_dates` 的口径：「**按次**」还是「**按日**」 | ✅ **已拍板 #3a 并实施（2026-09-26）：按日去重**——`persistence.py` 每种信号类型每日最多记 1 条，`signal_count_30d` 语义由「30 日次数」变为「30 日天数」（逐板块明细仍在 unified.json cross_node_signals 可取证）；单测 2 例（同日多板块共振 / 同日重跑幂等）。存量历史条目不改写，随 30 日窗口自然衰减；R5 复测仍按原计划 10-11 后跑 `verify_same_day_idempotency.py` 确认归零（详见 §9 #5） |
| 4 | 存量的 **4 份测试污染归档产物**是否清理（`data/got_daily/20260911/agent_glue.json`、`20260912/agent_glue.json` 及各自 `_replay/` 一份） | ✅ **已拍板 #4b 并执行（2026-09-26）：备份后删除**——4 份已移入 `data/got_daily_backup_20260926_1700/`（留档清单含 md5 与 verification.md §2.2/§4.1 互证），原目录仅剩空 `_replay/` 子目录（盘点按文件存在性判定，无影响）；现场证据以备份 + verification.md 文字记载形式保留 |
| 5 | **R-B8 的 `market_regime.*` 字段去留**（2026-09-14 干跑发现） | ✅ **已拍板 #5a 并实施（2026-09-26）：移出「应不变」清单**——`verify_layer_b_consistency.py` 的 `_collect_state_fields` 不再提取 regime/dwell_days/previous_regime/transition_count/transition_pending 五字段；`check_r_b8_r_b9` 对该子集单独比对并在证据行列「按设计 SKIP，不计 FAIL」；零生产代码改动，verification.md §4/§5.1 同步 |
| 6 | **R-B8 / R-B9 / R-C7 在现工器下是否继续推进** | ✅ **已拍板 #6a（2026-09-26）：维持「仅归档」边界，不扩 `--got-replay` 链路**——§1.F.b 已自愈后主要痛点解除，扩链路成本/风险与收益不对等；§1.F.a 继续按设计文档记录限制 |
| 7 | **regime 状态机切换计数器停摆根因分析**（2026-09-26 新增并经归档实测定根因，自 A6 阶段 2 条件 3 移出时登记）：见下方「§7.1 停摆根因实测」 | ✅ **已实施**（2026-09-26）：迟滞豁免（`pinned_days≥3`）解确认死锁 + `transition_events` 事件列表修正 `transition_count` 语义 + 应急回退开关 `DISABLE_REGIME_OSCILLATION_FIX`，详见 §7.1 实施记录 |
| 8 | **P2-1 `affected_sectors` 是否升级 `list[dict]`（区分正负面）**（2026-09-26 收口时登记）：原预期随 T5 对齐层退役消失，接线后重判该层保留、字段活消费，「待退役」归属作废（见 §2 表） | 待定范围：若做，需同步改 `catalyst_calendar` 对齐 prompt、`_group_events_by_sector` 扇出与 `portfolio_decision` 加分三处；若不做，明示接受「不区分正负面」为长期口径 |

### 7.1 停摆根因实测（2026-09-26，逐日扫 `data/got_daily/*/unified.json` 的 `market_regime`）

**不是「市场未切换」，是两层缺陷叠加使确认切换无法达成：**

1. **确认死锁（主因）**：自 **0910** 起，归档 `regime` 每日恒为 `transition`（上一交易日→今日均相等，即情形 1/3），但 `transition_pending` 在 0914=bull / 0915=bear / 0916=bull / 0918=bull 之间**日日交替**——说明 Node 1 每日算出的 raw regime 在 bull↔bear 之间高频振荡，**从未连续两日落在同一非-`transition` 值**⇒ 情形 2（`raw==prev_pending` 确认切换）永不触发，状态机被永久钉在 `transition`（`dwell_days` 一路从2累到 12）。两日确认机制本为去噪，但遇振荡信号会锁死。
2. **计数器语义缺陷（即 OPEN-C1 时间炸弹的实装形态）**：`transition_count`（`aggregator.py:638-642`）统计的是 `load_regime_state_history(20)` 回读的 **`[日期, regime]` 序列相邻变化次数**——用的是状态机**输出值**而非「确认切换事件」。因 0910 后输出被钉住不再变化，计数只能靠 0910 前的波动存量支撑（11→...→5），随旧高波动日逐日滚出 20 日窗口单调衰减且**无法回补**⇒ 下一交易日必跌破 5，触发仓位惩罚自行翻转。

> 修正旧认知：0827–0910 期间确认切换确实发生过多次（0901 bull→transition / 0902 →bear / 0904 →bull / 0910 →transition 均 情形 2 命中），所以「自 0827 零切换」仅对 **0910 后**成立。条件 3 当时给的「字面擦边未跌破」仍成立，但机制上已确定后续必跌破。
> 修法方向（提出时）：① 计数器改统「确认切换事件」（状态机自记而非回看输出 diff）；② 振荡保护；③ 重标阈值 + 窗口限定为状态机生效日后（OPEN-C1）。取证脚本：本次为一次性扫描，已删。
> **2026-09-26 拍板并实施**：①② 已做（迟滞豁免 + 事件列表），③ 未做（阈值 5 保持原值，OPEN-C1 继续观察）。

**实施记录（2026-09-26，拍板：迟滞豁免；计数口径：事件列表 + 窗口计数；带回滚开关；同日代码复审修正）**：
- `schemas.MarketRegime` 新增 `pinned_days` / `transition_events` / `regime_switch_exempt`（默认值向后兼容，旧归档无需迁移）；`persistence.load_previous_regime_state` 回读新字段，新增 `seed_regime_transition_events`（旧输出相邻 diff 作迁移种子）
- `aggregator._apply_regime_state_machine` 新增**迟滞豁免分支**（复审后合并原「候选再现/换挂」两条行为一致的分支）：连续情形 3 钉住 `pinned_days ≥ REGIME_PINNED_EXEMPT_DAYS(=3)` 时直接信任当日 raw（放弃两日确认）⇒ 打破交替死锁；脱钉后纯交替信号被迟滞抑制（输出锁在首个脱钉值），持续两日的真实信号仍按情形 2 正常确认——**非强锁**；豁免日归档标 `regime_switch_exempt=True`（R2 复盘区分两日确认 vs 单日强切）
- `transition_count` 改统**确认切换事件**：事件日期 append 进 `transition_events` 随归档落盘，计数 = **日期区间**窗口（历史最早日..今日，含当日）内事件数，同日重跑幂等不重复 append；旧归档无事件记忆时迁移种子**播种进列表**续记（切换日计数只增不归零，读数与旧 diff 口径无缝衔接）；`[]`（已迁移）照落与 `None`（未迁移/回滚期）区分
- 回滚开关 `DISABLE_REGIME_OSCILLATION_FIX=true` → 无豁免 + 输出 diff 计数 + 不记事件（避免半新半旧；关闭后事件链从迁移种子重建）
- 回归：`tests/test_node6_regime_state.py::TestRegimeOscillationFix` 8 例（含 0910 交替链路复现 / 事件窗口与幂等 / 种子续接 / 迁移空列表 / 开关回退）；got 域 405 全绿
| — | ~~`A-P0-4`（monitoring 按日去重）效果未验证~~ | ✅ **已结**（2026-09-14）：沙箱实测 + 09-14 归档旁证确认有效，见 [`verification.md`](verification.md) §6 |

> 另有一条**曾被公开撤销的错误断言**记录在 `archive/agent_glue_plan_0903.md` §9：原断言"`base_position` 是死参数"曾在写作阶段被错误撤销（查错了文件），**撤销作废**——`aggregator.py` 的 `base_position` 确是死参数。

---

## 8. 文档层缺陷（非代码）

| # | 问题 | 事实 |
|---|---|---|
| 1 | ~~**索引层失效**~~ | ✅ **已结**（2026-09-17 文档重排）：`docs/INDEX.md` 重写为三区全量索引，`docs/custom/` 已撤销（GOT 篇入 `got/archive/`，非 GOT 篇入 `archive/plans/`），诊断/设置/集成/规则文档全部收录并登记「新增文档须同步登记」约束 |
| 2 | ~~**死链**~~ | ✅ **已结**（2026-09-17）：根 `CLAUDE.md` 链接已改指 [`docs/AGENTS.md`](../AGENTS.md)；根 `AGENTS.md` 仍不存在（`docs/AGENTS.md:55` 要求二者为软链接关系，属治理域遗留项，不在文档层解决） |
| 3 | ~~**`docs/CLAUDE.md` 过期**~~ | ✅ **已结**（2026-09-17）：正文（选股系统 Phase 1–5 实施记录）整体归档至 [`archive/plans/screener_phase_devlog.md`](../archive/plans/screener_phase_devlog.md)，原位置改为重定向页指向 `docs/AGENTS.md` 与新结构 |
| 4 | **`CHANGELOG.md` 混段** | ✅ **已结**（2026-09-17）：`[Unreleased]` 拆为「本地工作（GOT 及仓库自维护）/ 上游同步」两子段（拆分实算 23 + 30 条，原记 19+28 为旧快照）；`## GOT 架构实施记录` 节保留原位并加历史流水说明 |
| 5 | ~~**文档状态字段过期**~~ | ✅ **已结**（2026-09-17）：`archive/plan_got_timeseries_enhancement.md` 状态头「待评审」已改为「已实施并归档（commit `03bb3d1`）」 |
| 6 | ~~**未纳入版本控制的验收脚本**~~ | ✅ **已结**（2026-09-26 核验）：`git ls-files scripts` 实测 **11 个 `verify_*.py` 全部已入库**（common / flow_archive / glue_metrics / layer_b_consistency / position_sizing / remote_data_sources / risk_fix / same_day_idempotency / t5_alignment / ths_integration / ths_syntax）。⚠️ `.gitignore` 的 `verify_*.py` 通配规则仍在，负向例外仅显式列了 4 个 0911 手具——其余 7 个系 force-add 收录，未来新建 verify 脚本仍会被静默忽略，规则本身是否收紧属治理决策，不在本清单裁决 |
| 7 | ~~**未跟踪工具**~~ | ✅ **已结**（2026-09-14）：`scripts/export_glue_family_audit.py`（M3 人工抽查工具，702 行）已入库，commit `0ac5252` |
| 8 | **附录 A 基线不可复现（R-D2 FAIL）** | `archive/got_layer_d_plan_0911.md` §附录 A 的四个子计数与实算不一致。**2026-09-26 收口复算**（A6 阶段 2 后）：① `divergent_trigger_days` 实算 **6** / 文档 **0**——退役后 `divergent` 改由 glue `capital_read=low` 产出，0917 起 6 日有触发，基线测的是规则时代，语义已换；② `ambush_pool_saturation_days` 实算 **8**（顶格日共 15 天）/ 文档 **≥10**——基线数据源未落盘（D-P2）仍无法复核；③ `dead_signal_count` 实算 **2**（E5/E11）/ 文档 **4**——基线早于 D-P14 删 E10；④ `dead_branch_count` 实算 = 文档 = **1** ✅。R-D2 维持「文档缺陷」定性：输入是过期基线而非代码状态，四项差异均可归因；建议下次触及 `got_layer_d_plan` 时整体重标基线，不单独立项 |

---

## 9. 红线与验收脚本自身的缺陷

被移出 shadow 文档的 4 条，加复查中新发现的 1 条，共 5 条。

| # | 项 | 缺陷 |
|---|---|---|
| 1 | **R-D1 口径自相矛盾** | `archive/got_layer_d_plan_0911.md` §6 的**定义**是「报告中每个**已修复/已解决**声明都附一条可复算证据」= **窄口径**；但同节**基线明细**把 9 条「待修」全算进去了 = **宽口径**。15 项里只有 P0-1 是「已修复」声明 ⇒ 窄口径 `unverified = 1`，宽口径 `= 8`（脚本输出 9，多算 1，见 #4）。**宽口径下「如实标注待修」反而被罚**，与「可复算率」这个名称的语义相反 |
| 2 | **R-D1 是 burndown 而非验收红线** | 它的 9 项读数**全部由未实施工作决定**（见 §2 处置表：待退役 4 / 延后 1 / 挂起 2 / 降级 1 / 改归属 1）。A6 退役一落地，4 项随 §6.1 删除自动消解 ⇒ **R-D1 → 0 是 A6 的副产品，不是 A6 的前置任务** |
| 3 | **R2 把市场事件当验收条件** | 「出现 `previous_regime != regime` 的归档日 ≥ 1 天」。按状态机设计（`aggregator.py:583-590`），`prev != cur` **只在情形 2（确认切换）**发生，其余两情形**按设计就相等**；自 08-27 接线以来 0 次触发。**零切换无法区分「市场没切换」与「情形不可达」**，两种解释都不可由该红线区分。✅ **已收口（2026-09-14）**，见 §9.1。<br>⚠️ **§7.1 后（2026-09-26）补充**：迟滞豁免分支也可产生 `prev != cur`（单日强切），豁免日以归档字段 `regime_switch_exempt=True` 区分——R2 复盘需将豁免日单列或剔除，否则「≥1 天」可被强切假性满足 |
| 4 | ~~**`check_audit_claims.py` 把修复前归档当失效证据**~~ ✅ **已修（2026-09-14）** | P1-5 被判 `INEFFECTIVE`，理由「修复代码尚未产生修复后归档，20260904 仍可复现重复」——**该理由不成立**：修复于 commit `8ad119b`（**09-12**）落地，`20260914` 即修复后归档且当日 ≥ 2 跑，其 `reasoning.md` 中「Node 7」仅出现 1 次（逐日实测见 §2 注）。<br>⚠️ **原记「根因：脚本不区分 pre-fix / post-fix 归档，只扫全量找复发」有误**：实测 `check_p1_5` **本来就有**分流 `recent = [d for d in duplicates if d.split("×")[0] > "20260901"]`，只是**阈值写的是 0901**，而修复落地在 0912 ⇒ 修复前的 0904 被判进了「之后」。真正的修法是改一个日期字面量，不是加分流。<br>**已修**：阈值提取为常量 `P1_5_FIX_DATE = "20260912"`（注明 commit `8ad119b`）⇒ **R-D1 实跑读数 = 8**，与本节预期一致 |
| 5 | **R5 与 `signal_dates` 的设计语义冲突** | R5 要求同 `(type, date)` 至多 1 条；但 `persistence.py:709-711` **有意**按当日信号出现次数逐条落盘（`for _ in range(count)`），`persistence.py:713-716` 的 `signal_count_30d` 正依赖该计数。当前 7 组重复全部是**修复前**的历史条目（去重只剔 `date == today_str`，历史条目靠 30 日窗口裁剪）⇒ **预计 2026-10-11 起自动归零**（最晚重复日 09-10 滚出窗口）。⚠️ 若某天单次运行就产生 ≥2 条同类信号，R5 会**再次** FAIL —— 那时是定义与实现语义打架，**需裁决「按次」还是「按日」** |
| 6 | **R-B8 的字段清单含跨日状态机输出**（2026-09-14 干跑发现） | `check_r_b8_r_b9`（`scripts/verify_layer_b_consistency.py:217-243`）把 `unified.market_regime.{regime,dwell_days,previous_regime,transition_count,transition_pending}` 归入「**应不变**字段」——只有 `classification` / `dimension_scores` / `frame_group_lookup` 三个分支被分流出去。但 `market_regime` 是**跨日状态机**，输入为 `load_regime_state_history(days=20)`：回放跑在**今天**（窗口 `0817…0914`）、原归档跑在当日（窗口不同）⇒ **输出必然不同**。而 `verify_same_day_idempotency.py` 的 docstring 自己写过「回放不含状态推进」。⇒ **R-B8 在回放语义下不可能 PASS**，属**判据与手段不匹配**，不是实现缺陷。干跑实测：喂 `20260903` 那对得 7 项差异，其中 5 项全来自 `market_regime.*` |

### 9.1 R2 的低成本收口 ✅ 已实施（2026-09-14）

`tests/test_node6_regime_state.py` 的 `test_confirmed_switch` **已经**用合成 `prev_state` 构造了情形 2，
但原先只断言了 `regime` / `dwell_days` / `transition_pending` —— **恰好漏掉 `previous_regime`**，而它正是 R2 唯一的观测量。

**已补** `self.assertEqual(result.previous_regime, "bull")`（情形 2 是唯一会让两者不等的路径），
R2 遂从「等市场切换」变为「每次跑测试都验」。

---

## 10. 复现命令

```bash
# 四层修复的当前状态（同日两跑 / B 层一致性 / 红线复算 / 声明可复算率）
# ⚠️ --runs 需要【两个】快照目录；只传字面量 --runs 会 argparse 报错
python scripts/verify_same_day_idempotency.py --runs data/_run1 data/_run2
python scripts/verify_layer_b_consistency.py
python scripts/verify_glue_metrics.py --date 20260910   # ⚠️ 无 --all；R-C3 十锚点由 --date 触发，只接受 --date/--json/--spearman-method
python scripts/check_audit_claims.py

# 0902 对账基线中的 P2-3（买入预算归零）
python -c "
import json,glob,os
for p in sorted(glob.glob('data/got_daily/2026*/portfolio_decision.json'))[-26:]:
    d=json.load(open(p,encoding='utf-8'))
    print(os.path.basename(os.path.dirname(p)), d.get('risk_assessment',{}).get('position_limit'))
"

# 未纳入版本控制的脚本
git check-ignore -v --no-index scripts/verify_*.py

# 本轮收口的三项（R-C1 / R2 / M3）
python -m pytest tests/test_node6_glue.py::TestGlueFailureSamplePersistence -q   # R-C1：S1/S2 落盘
python -m pytest tests/test_node6_regime_state.py::TestApplyRegimeStateMachine -q  # R2：情形 2 断言
python scripts/export_glue_family_audit.py                                       # M3 材料 → data/glue_family_audit/family_audit.html
```

---

*生成日期：2026-09-14*
*修订：2026-09-14 —— §0 明确「未实施」的六类处置与验收红线判定标准；§2 由「10 项未修」改为 **15 项处置表**（真·未排期 = 0）；§8 增 R-D2；新增 §9 红线与验收脚本自身的缺陷（含 R-D1 正确读数 = 8）；§7 结掉 A-P0-4 并换入 `signal_dates` 口径裁决项；修正 §10 的 `--runs` 命令。*
*修订：2026-09-14（第二轮）—— §1.A 补 `ambush_position_snapshots` 0 行的复核结论（是下游症状，不是 P4 证据缺口）；P4 行补代码位置漂移提示（`:1933` → `:2016`）。*
*修订：2026-09-14（第三轮，影子期）—— §1.C C-P9 由「✗ 未落地」改判为 **两半均已落地**（字段 `schemas.py:1086` + prompt `aggregator.py:824`）；§7 裁决 #2 结掉 `GlueStrength.basis` 一半、仅留 `mention_source`；§8 #7 M3 工具已入库并结项；§10 修正 `verify_glue_metrics.py --all`（脚本无该参数）。*
*修订：2026-09-14（第四轮，修正轮）—— §9 #3 与 §9.1 标注 R2 **已收口**（测试补断言）；§9 #4 **改判已修**并**更正根因**（脚本本有 pre/post 分流，错的是阈值 0901 → 实为修复落地日 0912，已提为常量 `P1_5_FIX_DATE`；R-D1 实跑读数 = 8）；§1.C C-P11 样本描述更正为「文件 2 / 去重 1 / 有效 0」；§7 新增裁决 #4（存量 4 份测试污染产物是否清理）；§10 增 R-C1 / R2 / M3 三条复现命令。*
*修订：2026-09-14（第五轮，C-P6/C-P7 落地）—— §1.C 的 C-P6 / C-P7 由「✗ 未落地」改判 **✅ 已落地**：`scripts/verify_glue_metrics.py` 主口径切换为 glue 侧（M1）/「其他」回收率（M6），规则侧降为对照列与观察项，**退出码与 R-C3 十锚点均不变**；⚠️ 只解除死锁、不解除 M1 不可达。同步收口 `verification.md` §3 与 `archive/agent_glue_plan_0903.md` §8.1（自注 ③「尚待落地」已结，并更正「回收率 ≠ M6′ 覆盖率」这处口径混用：分母 11 vs 32）。M1 / M6 达标判定仍归负责人，脚本不代判。*
*修订：2026-09-14（第六轮，首跑实测）—— 新增 **§1.F 验收工器的能力缺口**（两项：`--got-replay` 只跑 Node 6 ⇒ 验不到 B 层所在的 Node 2 且缺 `portfolio.*`；Phase 2 路径不落 T1 归档）；§9 增第 6 条（**R-B8 把跨日状态机输出 `market_regime.*` 归入「应不变」字段 ⇒ 回放语义下不可能 PASS**，属判据与手段不匹配）；§7 增裁决 #5（该字段去留）与 #6（R-B8/R-B9/R-C7 是否在现工器下继续推进）。*
*修订：2026-09-17（文档结构重排）—— §8 文档层缺陷 #1/#2/#3/#4/#5 全部收口 ✅：`docs/INDEX.md` 重写为三区全量索引、死链修复、`docs/CLAUDE.md` 归档并改重定向页、`CHANGELOG.md` [Unreleased] 拆「本地工作/上游同步」两子段（实算 23+30）、`plan_got_timeseries_enhancement.md` 状态头更正。本仓文档物理归位：旧项目基底 → `docs/guide/`，GOT 现行 → `docs/got/`（本文件即 `got/backlog.md`），GOT 归档 → `docs/got/archive/`，非 GOT 已完结计划 → `docs/archive/plans/`。§8 #6（未跟踪 verify 脚本）为代码资产，不在本次文档重排范围。*
*修订：2026-09-26（R-D1 收口轮，A6 阶段 2 后复算）—— §2 原「待退役」4 项逐条更新：P0-1 / P0-2 → ✅ 随退役消解（脚本实算证据行同步消歧），P0-5 → ✅ 自愈（近 12 日池规模 0–5，池空不可复现），P2-1 → 挂起待裁决（T5 对齐层接线后重判保留、字段活消费，「随退役消失」归属作废，转 §7 裁决 #8）；统计行同步（了结 6→9，归属 9→5）。§2 注补 R-D1 最新读数：脚本 `unverified = 7`（基线 10）。§8 #8（R-D2）刷新为 0926 四子计数实算（divergent 6/饱和 8/死信号 2/死分支 1✅），维持「文档缺陷」定性。D-P9 行补复算值。R-D1 / R-D2 收口完成，脚本仍 exit 1 系基线对照语义（非代码问题）。*
*修订：2026-09-26（§7.1 实施轮）—— 裁决 #7「真·待实施」→ ✅ 已实施：regime 状态机振荡修复（`pinned_days` 迟滞豁免 + `transition_events` 事件列表计数 + `DISABLE_REGIME_OSCILLATION_FIX` 回退开关），详见 §7.1 实施记录；OPEN-C1 标注「语义已修、阈值未重标」。回归 `TestRegimeOscillationFix` + got 域全绿（例数/域读数以复审修正轮为准）。*
*修订：2026-09-26（§7.1 复审修正轮）—— 代码审核发现并修复：**Critical①** 迁移种子改播种进事件列表 + 窗口改日期区间（含当日），消除升级后首个切换日 `transition_count` 一夜 5→0 静默翻转 ×0.8 惩罚与 `range_reversal`；**②** 合并「2-豁免/3-豁免」冗余分支（含错误日志文案），新增归档字段 `regime_switch_exempt` 使豁免强切可复盘（§9 #3 同步加注）；**③** `[]`（已迁移）照落与 `None`（未迁移）区分，补种子续接/迁移空列表两回归例；**④** 测试数订正为 8 例、got 域 405。未修缓办：⑤事件链断链回溯（回滚开关不可逆已在开关文案说明）、⑥新字段未入 R-B8/幂等验收清单（已随裁决 #5a 处理：market_regime 输出字段含新增者移出不变组、按设计 SKIP）、⑦回读归一、⑩阈值重标（归 OPEN-C1，已拍板 a 待样本齐后重标）。*
*修订：2026-09-26（本清单核查收口轮，负责人裁决结项两项）—— **§8 #6**「未纳入版本控制的验收脚本」→ ✅ 已结：`git ls-files scripts` 确认 11 个 `verify_*.py` 全部入库（原记「仍有 6 个未入库」过期），并补注 `.gitignore` 通配规则仍在、7 个系 force-add 的残留治理提示；**§1.E**「注释与实现不符（`:2837-2838`）」→ ✅ 已自愈：位置漂移至 `nodes/portfolio_decision.py:3819-3821`，注释已如实描述 fallback mainline + 反推移除（D-P13），剩余争议（fallback 取档方向）收敛至 §7 裁决 #1，本行不再单独跟踪。两项结项依据均为当日代码/git 实测，非推断。*
*修订：2026-09-26（三类未结项分诊执行轮·第一批——零行为变更项）—— **D-P2** skipped_reason 已落（`aggregator.py:2386-2397` gate_funnel 各闸补 skipped/skipped_reason + `_gate_stat` 唯一计数入口，死闸不再虚计 evaluated；单测 `tests/test_node6_ambush_funnel.py`）→ §1.D 行标 ✅；**D-P5** 已由 D-P2 承接→ 标 ✅；**D-P12** binding_count 已落（`portfolio_decision.py:219-241` 从 allocation 反推，仅留痕不参与决策）→ 行更新；**C-P8** 纯注记已落（`verify_glue_metrics.py` docstring 三项口径声明 + layer_c_plan §6.1 + verification.md §3 同步，R-C3 逐字节不变）→ §1.C 行标 ✅。**§1.F.b** Phase 2 不落 T1 归档 → 标 ✅ 已自愈（shadow_t1.json 6 日实测）；**§1.F.a** 维持开放，加注「文档层已诚实化，扩链路归裁决 #6」；**§7 #2** nga_mention 标半结（`schemas.py:544` 注释层已落，接线待拍板）。**本批零行为变更**（funnel 仅触计数行、binding 仅留痕、C-P8 仅 docstring/文档）；回归：定向 64 passed（含新增 5 例）+ portfolio 32 passed + R-C3 逐字节一致。C-P11 回填/C-P12 变体隔离（第二批）与决策简报（第三批）本轮未动。*
*修订：2026-09-26（三类未结项分诊执行轮·第二批——C-P12 变体隔离前置 + C-P11 回填）—— **前置改造**：`GLUE_REPLAY_VARIANT` 变体目录隔离落地（`aggregator._save_glue_result` 仅 replay_mode 生效、默认空不改现路径，A/B 各落 `_replay/variant_a|b/`；`verify_glue_metrics._replay_sample_inventory(variant=)` 参数化 + `_discover_replay_variants()`；单测 `tests/test_glue_replay_variant.py` 6 例，无变体目录时 R-C3/R-C6 输出逐字节不变）。**C-P11 回填**：开 `AGENT_SEMANTIC_GLUE_IN_REPLAY=true` 逐日 `--got-replay` 跑 0901/0902/0903/0904/0907/0908/0909/0910 共 8 日（真实 LLM 调用，先跑 0901 冒烟确认 status=ok 再批跑余 7 日），8 份产物均 `status=ok` 且 md5 两两互异，产物落 `{date}/_replay/agent_glue.json` 不污染生产归档；**R-C6 有效样本 0 天→ 8 天**（目标达成，旧 §4.1 占位态覆辙已避）。R-C6 仍 SKIP（需两版 prompt 各跑 8 天 = C-P12，待负责人文案）、R-C3 仍逐字节一致。C-P12 本体、C-P13 标定、决策简报（第三批）本轮未动。*
*修订：2026-09-26（拍板落地轮，决策简报七项裁决全部处置）—— **#1a** `DEFAULT_SECTOR_ROLE` 保持 mainline（零代码改动，backlog §7 #1 定性为已知并被接受；`portfolio_decision.py:46` 注释同步标已拍板）；**#3a** `signal_dates` 按日去重已实施（`persistence.py` 每类型每日最多 1 条，`signal_count_30d` 语义次数→天数；单测 2 例，`tests/test_cross_node_signals.py::TestMonitoring` 18 passed）；**#4b** 4 份测试污染产物移入 `data/got_daily_backup_20260926_1700/`（含 README 留档清单，md5 与 verification.md §2.2/§4.1 互证）；**#5a** `market_regime` 5 字段移出 R-B8 不变组（`verify_layer_b_consistency.py` 单独比对、按设计 SKIP 不计 FAIL）；**#6a** 不扩回放链路；**C-P13a/OPEN-C1a** 均等 C-P12 样本齐后同批标定。文档同步：§7 表 #1/#3/#4/#5/#6 标已拍板、§1.C C-P13 行、§3.3 OPEN-C1 行、§7.1 复审⑥缓办项销账；verification.md §4/§5.1/修订行同步。回归：cross_node 18 passed；R-C3 锚点复验逐字节一致（未触及 glue 链路）。待办不变：C-P12 两版 prompt 文案→各跑 8 天→解锁 C-P13/OPEN-C1 标定输入；R5 复测 10-11 后。*
*修订：2026-09-26（C-P12 本体执行轮——prompt A/B 双版回放 + M2×M3 矩阵）—— **前置改造**：`GLUE_PROMPT_VARIANT`（a/b，仅 replay 生效、未知值回退默认）+ `GLUE_MAX_TOKENS`（默认 16000=现行生产值）双 env 落 `catalyst_config.py`；`aggregator._build_glue_prompt` 加 `variant` 形参 + 变体文案分支（A=现行+澄清句，B=任务1禁 unknown+任务3全量归属低置信保留+禁止编造护栏）；`agent_glue.json` 增 `prompt_variant` 留痕（`schemas.py`，schema 增量不改形状）。**--compare**：`verify_glue_metrics.py` 新增按变体逐日盘点 M1′/M2/低置信占比/放弃事件 + A/B ΔM2 矩阵 + 目录尾缀一致性校验 + `--m3-audit` 分层抽样预审表（种子=42）。**跑批**：A/B 各回放 0901/02/03/04/07/08/09/10/14 共 9 日，均 `status=ok`、`prompt_variant` 正确、md5 互异。矩阵实测：A 均值 M2=35.5%/低置信 6.8%、B 均值 M2=46.3%/低置信 40.8%（印证 §2.6 M2↔M3 拉锯；ΔM2 非单调）。⚠️ 0902 B 版撞 16000 截断（S1_empty），临时 24000(A)/32000(B) 重跑，该日与其他日不对称、破坏单日对照同参，已在 §1.C 行与草案披露。新增 `tests/test_glue_prompt_variant.py`（变体文案选择 + 共用区块锁死 + replay 门控 + 生产忽略 env + 未知值回退）。**R-C6 对照矩阵完整**（两版各 9 天 ≥8），达标判读归负责人；`--date 20260910` R-C3 锚点 PASS、R-C6 按设计 SKIP（无曲线判读即 SKIP）。回归：test_node6_glue + prompt_variant + replay_variant + a6_wiring 46 passed。C-P13/OPEN-C1 标定输入已解锁，数值标定归负责人。*

*修订：2026-09-27（C-P12 事件级复核 + C-P13 架构调研登记）—— 事件级 A/B 对照表 `cp12_event_level_ab_20260926.md`（脚本 `.review_tmp/cp12_ab_event_table.py`，不入库）实测：A relation=336 / B=428，同事件同板块交集 168 / B 补挂 229 / A 有 B 无 104。**两项质量发现登记**：① **0902 A 版为退化重复循环**（191 条 relation 仅 28 命中事件，属生成/解码故障非 prompt 内容问题，该日应在 C-P13 标定中**整日作废**而非降权）；② **B 版存在真·过度归属幻觉**（0903 房贷→24 板含减肥药/短剧游戏、金价→短剧游戏）→ 现有 10 行分层预审样本抽不到高扇出病态事件、**M3_B 会被低估**，若终审需补「单事件 relation≥8 高扇出过采样」专项表。**架构结论与修正方案**：A/B 是同一生成架构在召回/精确单旋钮上的两端（§2.6 拉锯为结构性），外网论文（THEME/CoVe/CAD/CausalStock/FinRipple/LangExtract）一致指向**转向「接地式召回-验证」5 层管线**（检索接地候选有界化 + CAD 受约束生成 + CoVe 精筛 + 图谱衰减传播 + 校准置信度），红线 0.6 应标在校准后置信度上——详见 `cp13_grounding_pipeline_research_20260926.md`（待拍板）。§1.C C-P13 行同步追加复核注。本轮纯文档登记，零代码/行为变更。*