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
| **A-P0-5** | Node 2 三处派生计数器补 `tracking_date < today` 过滤 | ✅ **已落地（排期批 3.3，2026-09-28 复核）**——三个派生计数器（dwell/exit_pressure/wave）的 `prev_*` 均来自 `_load_all_sector_trends`（`sector_analysis.py:3934`）与 `_compute_wave_progress`（`:4340`），均已带 `tracking_date < today`；波函数 docstring（`:101`）亦明记 prev_record `< today`。首轮「部分」已补齐 |
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
| **OPEN-B4** | `关注指数 ×1000` 的来历 → **git 考古已落定（2026-09-28，拍板③授权即判）**：commit `8ad119b`（2026-09-12「GOT 四层审核修复 A/B/C/D」）把原先硬编码 `*1000` 提取为常量 `EM_ATTENTION_SCALE` 并首标 OPEN-B4 TODO「后续改为存原值」。来历=东财千股千评「关注指数」原值约 0~100，为塞进 int 型 `mention_count` 字段保精度而 ×1000；展示侧 `_format_mention_display` 已按 `mention_source` 分支 /1000 还原（雪球走 `total_attention` 原值不缩）。**判定：保留 ×1000**（翻案收益仅“整洁”，代价=历史归档 `mention_count` 口径全量重标 + 碰 R-B8 不变组；展示已正确还原、无实际读数错）。`archive/got_layer_b_plan_0911.md` §7.2 |
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
| **D-P1** | `divergent` 阈值与分位可观测（样本数 / 5% 分位 / `min()` 后最终阈值 / 每确认板块落盘） | ✅ **改判：随 A6 阶段 2 退役消解（排期批 3.2，2026-09-28 实测）**——原目标 `_divergent_threshold`/`_cross_check_capital` 已删（§4.2 动作 1），现 `divergent` 纯由 glue `capital_read=low` 经 `_GLUE_CAPITAL_READ_MAP`（`aggregator.py:1520-1523`）分类映射产出，**无阈值/分位可观测**；为其加分位落盘会制造新死代码（§9 缺陷谱系），故不改代码、只账本改判。历史基线（§8 #8 divergent_trigger_days=6）口径已换为 glue 时代 |
| **D-P2** | 埋伏闸门逐层漏斗 `ambush_gate_funnel`（含 `evaluated` / `skipped_reason`） | ✅ **已落地（2026-09-26）**——`aggregator.py:2386-2397` gate_funnel 各闸补 `skipped`/`skipped_reason` + `_gate_stat` 计数唯一入口；死闸（`hot_sectors` 空 ⇒ G2/G3）与资金流缺失（`accum_strength=="missing"` ⇒ G4）改记 `skipped` + reason，不再虚计 `evaluated`；**不改 `_run_ambush_gates` 判定逻辑**；单测 `tests/test_node6_ambush_funnel.py`（空 hot_sectors→skipped、决策路径快照不变、同日重跑逐字节幂等） |
| **D-P3** | 定义并落盘「正式池水位」目标 `AMBUSH_POOL_TARGET_RANGE` | ✅ **已收口（2026-09-28 复核实测）**——双源已消解：常量单源 `catalyst_config.py:159`，`archive.py:454` 已改 import 读取（不再硬编码 `(2,6)`）；原记「常量双源 + 过期 TODO」作废，TODO 注释已同日更正。取值本身仍归 OPEN-D7（暂定 `(2,6)`） |
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
| **P1-1** | 1 周内 | 持仓股 `name == symbol`（600250 / 605566） | ✅ **已补（2026-09-28，搭批 5 后小尾巴）** | OPEN-D8 结：`portfolio_decision.py` 新增 `_resolve_holding_name`（静态方法），在 `_score_holdings` 两处消费点（L567/L598）兜底——`name` 为空串或等于 `symbol` 时走离线股票索引 `src.data.stock_index_loader.get_index_stock_name`（进程内 lazy 缓存、无网络，实测 600250→南京商旅 / 605566→福莱蒽特）；不可用则回退原行为返回 symbol。**仅消费侧兜底、不动 `PortfolioService` 源头**（源头被 webui 共享）；`name` 无计算副作用（仅展示/日志/归档），R-C3 锤点逐位不变（verify 20260910：0 FAIL）、`test_holding_name_fallback.py` 5 例绿。❗ 计划原写“查 `a_share_codes.json` 代码→名称映射”不成立（该文件为纯代码清单无名称），改用现存离线索引 |
| **P1-2** | 1 周内 | reasoning 中同一板块出现 ×3 | ✅ **已生效** | `portfolio_decision.py:1526-1535` `seen_canons` set 去重（D-P7）；⚠️ 原「代码未去重」的记载已过期 |
| **P1-3** | 2 周内 | 50 只热股 `mention_count` 极差 <3%，无区分度 | ✅ **已落地（2026-09-28 核实）** | D-P8：`mention_source` 标注字段已写入生产端——`sentiment_analysis.py:805` eastmoney / `:836` xueqiu（常量 `MENTION_SOURCE_EASTMONEY/XUEQIU` L50-51），展示侧 `_format_mention_display`（L843-847）按源分支渲染；**数值不动**（仅口径标注） |
| **P1-4** | 2 周内 | `associated_sectors` 50/50 全空 | **重判：闲置口径，非待查**（2026-09-28） | OPEN-D2：原「是否真无可靠数据源」重判为「字段仍空但闸门已旁路」——G2 注意力闸已于 audit_20260922 Q2（09-23）改用 `SectorContext.ths_hot_sectors`（`aggregator.py:3136/3172`），生产端 `sentiment_analysis.py:804/835` 仍写空列表；口径标注已落（schemas `HotStock.associated_sectors`），字段保留仅为归档兼容 |
| **P1-5** | 2 周内 | `reasoning.md` 中 Node 7 段出现 ×2 | ✅ **已生效**（见下注） | D-P6 → 并入 A-P0-8；`archive.py:642` 覆盖写 |
| **P2-1** | 按需 | `affected_sectors` 不区分正负面 | ✅ **已拍板 #8a（2026-09-28）：不升级 `list[dict]`，明示「不区分正负面」为长期口径** | 口径声明已落三处：`schemas.CatalystEvent.affected_sectors` 描述 + `aggregator._group_events_by_sector` docstring + `catalyst_calendar._align_events_to_sectors` docstring（翻案需同步改这三处，见 §7 #8） |
| **P2-2** | 按需 | 催化剂日历膨胀至 92 个事件 | **症状消失，无归因** | — |
| **P2-3** | 按需 | `current_position_ratio` 被 `min(1.0, raw)` 截断 ⇒ 26 个归档日中 **13 天 `buy_budget = 0`**，一笔都买不了 | ✅ **已落盘 `raw_position_ratio`（排期批 3.1，2026-09-28）** | OPEN-D9：截断不可证伪已解——`portfolio_decision.py:392` 旁落 pre-clip 原值（`schemas.PortfolioRiskAssessment.raw_position_ratio`，Optional）。**仅留痕不参与判定**（单测 `TestRawPositionRatio` 3 例锁定：截断日 raw=1.3/current=1.0、未截断日 raw==current、raw 不影响 position_limit/stop_loss）；新字段按 #5a 先例应移出 R-B8 不变组。历史 13 天无法回填（旧归档无该字段），自下一次生产起可证伪 |
| **P2-4** | 按需 | "其他"家族成垃圾箱 | ✅ **已生效** | "其他"占比 40.0% → 34.4%（**⚠️ 与 §7 #9 /【架构计划】WI-P2-4（解禁事件）同号不同物，勿混**） |
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

**统计**（2026-09-28 小尾巴轮后）：已生效 8（P0-3 / P1-2 / P2-4 / P2-5 + 随退役消解 P0-1 / P0-2 + **P1-1 已补** / **P1-3 已落地**）+ 不修 1（P0-4）+ 症状消失 1（P2-2）+ 自愈待确认 1（P0-5）+ 已拍板结案 1（P2-1 #8a）= **12 项了结**；
挂起 2（P1-4 重判闲置口径 / P2-3）= **2 项各有归属**；**真·未排期 = 0**。（本轮回改：P1-1 搭批 5 后补上消费侧兜底、P1-3 核实 `mention_source` 生产者早已在位——两项转「已生效/已落地」。）

---

## 3. OPEN 挂起项汇总

### 3.1 主文档（`archive/agent_glue_plan_0903.md` §9）

| # | 项 | 要点 |
|---|---|---|
| OPEN-1 | 仓位链路 | 4 条已验证事实：W1 仓位超限无减仓路径（不减仓反而继续买入）；W2 两阶段结构已存在，只需给 Step 1 补卖出来源；W3 `total_equity` 与 `accounts[0]` 口径不一致（5 处）；W4 从 `reasoning` 字符串解析板块名的脆弱耦合。**〔排期批 4.1 已实施 W2/W1（2026-09-28）〕**：`portfolio_decision.py::_allocate` 新增 **Step 1.5 组合层超限强制减仓**——Step 1 卖出后仍超 `position_limit` 时按综合分升序削最弱持仓回落至上限，**复用 Fix 3 宽限**（消费 `UnifiedScoringResult.sector_drift_grace` 标记跳过宽限持仓，不解析 reasoning 串→不触 W4，防 688598 式误卖）；W1「超限反续买」闭环（`over_limit_forced` 时 Step 2 `buy_budget=0`）。**三态开关**（仿 `ENABLED`+`SHADOW` 双开关惯例，本项风险最高，先观察再转正）：**`GOT_SELL_SOURCE_ENABLED`（生效，默认关）=真减仓**；**`GOT_SELL_SOURCE_SHADOW`（影子，默认开）=每日跑减仓检测、结果落 `PortfolioDecision.sell_source_shadow` 观测字段（`{mode, excess_value, planned_trims, grace_skipped_symbols…}`）但不写 allocation/不改 `sell_proceeds`/`over_limit_forced`→ 决策逐字节零改动**（新增观测字段按 raw_position_ratio/#5a 先例移出 R-B8/幂等不变组、按设计 SKIP）；两者皆关时 Step 1.5 整体跳过。（2026-09-28 修正：初版误将「默认关」当作「影子观察」，实为整步不进=零观察零作用，属写了不跑的死分支，违反本仓对产而不用字段的清理口径→ 改为真影子默认开。）单测 `tests/test_sell_source_over_limit.py` **11 例**（生效 3 + 评审边界 4 + 影子 2 + 全关 1 + 默认影子 1）全绿。**〔评审后修复 2026-09-28，CodeReview 实证〕**：①Critical——Step 1.5 候选未排除 Step 1 已 sell/reduce 标的且取原始股数 → 重复卖单 + sell_proceeds 虚增（污染 enabled 决策与 shadow 读数双向）→ Step 1 现维护 `remaining_qty` 真相，Step 1.5 削减量一律按剩余敞口计；②零股（<100 股）被 `max(100,…)` 抬回一手致负 target_quantity → 去掉 max，按 `min(ceil(…), remaining)` 整笔清仓；③W1 回补抑制被整手向上取整尾差击穿（`position_gap>0` 抢先，elif 永不进）→ `over_limit_forced` 提为 Step 2 前置短路。**转正门槛**：上述修复仅为计算正确性；多账户/外币口径（W3）仍为 `GOT_SELL_SOURCE_ENABLED=1` 的硬前置（现仍用聚合 excess 削 `accounts[0]`）；修复前影子期读数的 planned_trims 含重复标的，不可作标定输入。**W3/W4 仍单独立项（不在本批）** |
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
- **OPEN-C2** M3 人工流程未定义 —— ✅ **流程已定义（2026-10-06）**：新建 `scripts/build_m3_review_package.py`（离线只读，**不代判**）把【C 层计划】§7.2 建议口径钉成代码：按日分层抽 10（回收 5 + 冲突 3 + 随机对照 2，seed 固定、逐日独立子流可复现、**池不足只记缺口绝不跨层补齐**）+ 判据三档（强/弱/无关系）+ 一档兜底（无法判定，不计分子也不计分母）+ 计分（误配率=无关系/已判，glue 弃权与双空不入分母，因 §8.1 定义 M3 只针对「给出了归属」的行）+ 红线与转正（单日≤15%、末尾连续 5 个抽查日）。**口径可比性硬约束**：分层口径对难点超采、读数偏高。**（原写「转正必须 `--sample-mode full`（§8.1 每日全量）复跑」——该前置已于 2026-10-06 负责人裁决作废，见本条末尾）**抽样与类别判定复用现有审核工具 `export_glue_family_audit.py` 的 `discover_days/load_day/classify`，不另写第二套口径。**评审包已产（as_of 2026-10-06）**：5 归档日（0923/0924/0928/0929/0930）候选 120 行 → 抽 36 行（回收 25/一致 10/推翻 1），**重要提醒：推翻（冲突）层 5 日合计仅 1 行**，OPEN-C2 设想的「冲突 3」当前无样本可抽，M3 主战场实为「其他回收」层；另有 55/120（46%）glue 弃权不入分母。**【首次终审已完成（2026-10-06）】**：负责人逐行判完 36 行，误配率 **2.8%**（无关系 1 / 弱关系 4 / 强关系 31）、Wilson95 [0.49%, 14.17%]、逐日连续 5 日达标 `status=met`；唯一误配（低空经济→智能驾驶，两个抽查日稳定复现）已对症收口（军工族新增关键词 "低空"，依据：`aggregator.py:1075` 任务2 只对落「其他」的板块有裁量权，规则侧命中后 glue 不再介入）。**【M3 结案转正（2026-10-06 负责人裁决）】—— 本项已结**：直接采用分层终审读数 2.8% 作验收，**放弃 §8.1 的 `--sample-mode full` 复跑前置**。裁决依据是实测不是嫌麻烦：分层样本难点（回收+推翻）占 26/36＝72.2%，而总体仅 36/65＝55.4% ⇒ 2.8% 是**偏高**读数（拿偏高读数作验收属保守方向，不会把差的说成好的）；按层外推全量口径 ≈2.2%，两读数同处 15% 红线内且与红线差一个数量级，复跑不改变结论（早前说「全量≈120 行判不动」是高估了：120 是含 glue 弃权/双空的总候选，真正分母 65 行/5 日＝日均约 13 条，本就在 §8.1 自己的 ≤10 分钟/日预算内，日后想补随时可补）。**结案后的两条尾巴**（均不阻本项）：① stratified 降为日常预警且天然偏高（单日 1 条无关系即 14.3% 贴线，全量口径仅 7.7%）⇒ 破线需人工复核、不自动回滚；② 真正的残留风险在统计功效不在口径：n＝36，Wilson95 上界 14.17% 仅勉强压线，弱关系堆积 4/36＝11.1%（prompt 收紧信号）本轮未处理。本项只结 M3：glue 已于 2026-09-17 A6 转正在线，不动任何生产开关；M1/M2/M4-a 仍是未达的水位监控项。读数、机制发现与结案论证见【verification】§6 与【架构计划】§11.8；`build_m3_review_package.py` 三处口径文案已同步，并有守卫测试 `test_process_note_records_promotion_decision` 防前置被写回去
- **OPEN-C3** ~~`persistence_regime == "low"` 恒真~~ → **更正（2026-09-28 实测）：是永不触发死分支，非恒真**——`aggregator.py:3452` 要求 `persistence_regime == "low"`，但生产端 `sentiment_analysis.py` 从不写该字段，`getattr(..., None)` 恒 None ⇒ 「高震荡+低持续性」惩罚分支自接线以来零触发（属 §9 死分支缺陷谱系）。处置未决：接线/删分支/保留三选一，**决策简报已由排期批 2.2 产出**（见 `.review_tmp` 取证结论），本项转待拍板
- **OPEN-C4** M1 的信息量到底多大 → 由 C-P12 曲线回答，**不预设**——✅ 曲线已产出（2026-09-26）：M1′ A 均值 43.6% vs B 均值 39.0%（B 反低，因任务1 强制 low 垫底拉低均摊）；具体信息量判读归负责人
- **OPEN-C5** `agent_glue.json` 的当日覆盖语义与回填产物是否同语义
- **OPEN-C6**（C-P13 阶段1 登记，2026-09-27）L1 的 K 值与 L5 的 τ 需在回放矩阵上联合网格标定（cp13 笔记 OPEN-1）——阶段1 初值 `GLUE_L1_TOP_K=12` 待矩阵标定；τ 标定属阶段2（L5 校准置信度），C-P13 红线 0.6 在阶段1 仍无真实概率可标
  - 2026-09-27 L1 冒烟（cp13_l1_smoke.py）：K=12 时 0903「房贷信贷」top-K 混入 0.251 噪声「短剧游戏」（真实相关项银行 0.439，分数分布自然截断带 ~0.27-0.30）；K=8 两病例全过且 0910 漏挂病例不误伤 → 默认值已降为 8，矩阵跑批后做终标定
  - **【阶段2 收口（2026-09-28）】**：realized 收益率口径标定 confidence 红线 τ = **证伪关闭**（19 天池 alpha≈0 / conf rho −0.06 / CoVe kept−dropped 仅 0.04pp，详 `docs/got/cp13_phase2_closeout_20260928.md`）；**红线不采纳，K 终标定与 τ 一并随阶段1 休眠推迟**。纠偏：confidence 非无用——阶段1 人工 M3 显示 conf≥0.70 全真、牵强全≤0.50，尺子应换成 M3 归属口径（非收益率）再标。解冻条件见收口纪要 §6
- **OPEN-C7**（C-P13 阶段1 登记，2026-09-27）接地管线调用成本（cp13 笔记 OPEN-3）——阶段1 实测口径：每日 ≈ 1 次 base glue + 事件数次 L2（got_cp13_l2_generate）+ ceil(relations/10) 次 L3（got_cp13_l3_verify），待 0901–0914 矩阵跑批后用 `get_llm_usage_summary(by_call_type)` 回填实际量；仅回放可接受，生产化前需重新评估
  - 2026-09-27 矩阵跑批实测回填（llm_usage DB 当日分列）：L2 **583 次**/547.9k tokens（事件量实测 20-120/日，高于 20-40 预估，0903 达 120）+ L3 **67 次**/89.4k + base glue 9 次/174.1k，合计 659 次/~811k，略超计划预算 <600（+10%）；墙钟 ~11 分钟（L2 并发 4 + embedding 按日缓存）；详见 `docs/got/cp13_stage1_matrix_20260927.md`

### 3.4 D 层（`archive/got_layer_d_plan_0911.md` §7.2）

OPEN-D1（P2-2 界点归因）、OPEN-D2（`associated_sectors` 是否真无可靠数据源）、OPEN-D3（`CATALYST_CONFIDENCE_GATE` 校准样本 → 🧾 **取证表已产出（2026-10-06）**：`scripts/calibrate_catalyst_confidence_gate.py` 只读取证（分箱质量/门槛模拟/归档漏斗 reason/分类质量），产物 `reports/catalyst_confidence_gate_forensics_YYYYMMDD.md|.json`（reports/ 不入库）——初读：减基准超额胜率随阈值 0.50→0.80 极差仅 3pp、方向正确率（净化）反而 44%→34% 倒挂 → **现 confidence 分值对兑现质量无实质区分度**，取值/改结构归负责人（联动 OPEN-D10）；**本次仅取证不改值**（glue_consumed 9<10 交易日）、OPEN-D4（`AMBUSH_SCORE_FLOOR=0.30` 来源与带宽）、OPEN-D5（蓄水池驱逐规则 FIFO vs 质量）、OPEN-D6（P2-3 `raw_position > 1.0` 语义）、OPEN-D7（正式池水位目标取值，暂定 `(2,6)`）、OPEN-D8（P1-1 新归属，排在下一批）→ ✅ **已结（2026-09-28 小尾巴轮：`_resolve_holding_name` 消费侧兜底落地）**、OPEN-D9（`current_position_ratio` 截断不可证伪，建议落盘 `raw_position`）

- **OPEN-D10**（P1-1 取证时发现，2026-10-06）`direction` 字段语义污染致方向准确率系统性伪低：
  `catalyst_config.GLOBAL_TO_A_SHARE_MAP` 的 `direction` 存的是**漂移类型词**（`drift`/`fade`/`binary`），
  而 `catalyst_realization._check_direction`（L742）只认 `positive`/`negative`/`binary`，其余恒 `return False`。
  实测污染 464 条已兑现样本中的 **162 条（35%，conf=0.8 整档 149 条几乎全在列）**：
  原始口径方向准确率 **29%** → 净化后 **44%**（有效样本 302）。
  ❗ **副作用**：`scripts/calibrate_catalyst_params.py`（T4）按 `direction_acc < 0.5 → 建议降门槛至 0.60` 分支，
  属该缺陷导致的**伪结论**，不得采用（新脚本已按净化口径重算并标注）。
  待拍板二选一：① 修源头（映射表 `direction` 改 positive/negative/binary，`category_tag` 已是类型位）；
  ② 修判定（`_check_direction` 补 drift→正 / fade→负 分支）。**两者均改变已有统计读数**，
  属新 WI（不并入 P1-1 门槛标定），修后需回填重算 P6 兑现报表。

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
| **Phase 3b** | remote §8 分歧摘要 + SkillAggregator + RiskOverridePlan | **A6 之后**（同改 `aggregator.py` 聚合核心）。〔**排期批 5 §3 已落（2026-09-28，影子态）**〕：新建 `src/got/disagreement.py`（本地基座复用 `AgentOpinion`/`normalize_decision_signal` + SkillAggregator 式加权共识；11 类冲突分类=§8.2 显式 7 类 + 同维度补全 4 类（已标 TODO 待远端原文核对）；`RiskOverridePlan`；`memory=None` 退化等权）；aggregator 矛盾检测段旁挂 `GOT_DISAGREEMENT_SHADOW`（默认开·落 `conflict_summary_shadow` 观测不改既有检测/仲裁）/`GOT_DISAGREEMENT_ENABLED`（把 `decision_path_hint` 注入 T3 仲裁 prompt）；PD `_allocate` Step 2 注入 RiskOverridePlan（enabled+veto_buy→`buy_budget=0`，与 `over_limit_forced` 并列；`risk_override_shadow` 留痕）。**enabled 转正归负责人 + 需影子期读数**（计划 §9.3）。`test_disagreement.py` 14 绿；R-C3 十锚点逐位一致 PASS。 |
| **Phase 4b** | remote §5 MarketThemeContext Step 2（mootdx 板块排名接入） | **A6 之后**；Step 1（模型定义）已完成。〔**排期批 5 §2 已落（2026-09-28，影子态）**〕：`market_theme.py` 增 `build_theme_context`（mootdx `get_board_quick_change` 批量自算，禁逐板 kline；三时框同向判 phase；失败回退 unknown 零行为）；builder 调用落 `orchestrator.py`（AggregatorNode 无 fetcher）；aggregator 组装段 `GOT_MARKET_THEME_SHADOW`/`_ENABLED` 三态消费 + `schemas.UnifiedContext` 两观测字段 + archive「🧭 市场结构」段。相关性验收脚本 `.review_tmp/cp4b_correlation_acceptance.py` 诚实 SKIP（enabled 转正前置）。`test_market_theme_step2.py` 11 绿；R-C3 十锚点逐位一致 PASS。排名源=mootdx（TickFlow 探测证不达标，见 §7/5b）。 |
| **Phase 4d** | remote §7 AlphaSift 选股引擎 | **A6 之后**（归零率基线重标 D-3）。〔**排期批 5 §4 部分落地（2026-09-28）**〕：✅ **4.1 归零率基线观测化**——`stock_screener.run_got_mode` 5 轮漏斗补 `funnel_stats`（actionable 板块数/0 只入选板块/逐轮存活数）落 GOT 归档，`ScreenerRunResult.funnel_stats` 观测字段不进判定（D-3 基线首次可落盘，需攒 ≥10 交易日）；✅ **4.2 快照源连通性取证**——`79.push2.eastmoney.com` 当前**可达未封**（见 verification.md §7.1），hotspot 因子无需降级、§1 申请清单联动不触发。⏳ **4.3 影子接线 + 2 周评估**未开工（前置=D-3 基线满 10 日）。`test_got_funnel_stats.py` 4 绿。 |
| **Phase 5b** | remote §10 TickFlow 数据源 | **需申请 API Key**〔key 已就位（2026-09-28），改能力探测批，见 verification.md §7〕：探测证 **TickFlow 不能作 4b 板块排名源**（概念 universe=0、`get_by_universes`/`klines.batch`=PermissionError，套餐权限所限）；`quotes.get_by_symbols` 个股实时可用（Phase 6/5c 候选）。mootdx 先行定案（拍板②）。 |
| **Phase 5c** | glue P1 调参 + OPEN-1 家族仓位 | **✅ 已拍板为本批主线（2026-09-28 排期）**，规格见 `archive/agent_glue_plan_0903.md` §P1（P1-1…P1-4）；⚠️ P1-1 分位重标需转正后 ≥10 交易日样本（预计 ~10-05 解锁），W3/W4 单独立项不在本批。**〔排期批 4 实施进度的2026-09-28〕**：✅ **4.1 W2/W1 卖出来源已落**（三态开关：`GOT_SELL_SOURCE_ENABLED` 生效默认关 + `GOT_SELL_SOURCE_SHADOW` 影子默认开·跑检测落 `sell_source_shadow` 观测字段不改决策；单测 7 绿，详 §3.1 OPEN-1）；✅ **4.5 zip 金丝雀已落**（`aggregator.py` 家族梯队 `zip(members, member_details)` 长度错配显式留痕，OPEN-4 X1 同型防御）；⏸️ **4.2 P1-1 分位重标 DEFER**（批 2.3 实测 `glue_consumed` 样本仅 **6<10** 交易日，按 gating 顺延至解锁日）；✅ **4.3 P1-2 护栏复核=验证性无代码**（OVERHEAT/top_n/_wave_coeff 本体不动）；⏳ **4.4 P1-3 `CATALYST_CONFIDENCE_GATE=0.7` 真校准=数值判定归负责人**（§0 性质约束，本批不擅自设值）**〔2026-10-06 取证推进：新脚本 `scripts/calibrate_catalyst_confidence_gate.py` 已入库（只读，不改值），分位读数显示 confidence 无实质区分度（见 §3.4 OPEN-D3）；同时发现 `direction` 语义污染新缺陷→ OPEN-D10，需先修数据再读方向类指标；glue_consumed 仍 9<10 交易日，判读顺延至 10-08 复市后重算〕** |
| **P0-B B3** | `crowded_families` 处置（Node 2 停产 vs 修正口径） | ✅ **已拍板（2026-09-28）：维持现状+加口径标注**，不停产不新增接线。注：旧「无消费者」记载过期——实有 3 处活消费（Node 6 续航 crowded 标记 / Phase 2 追加降权 ×0.90 / 埋伏 G3 输入，2026-09-28 grep 实测）；标注已落（`sector_analysis.py` 产出处 + schemas 两处字段描述） |

> ⚠️ **批次 5 影子读数有数期作废并重起（2026-09-29，批 5 审查修复收尾）**：Ultra Review （`ULTRA_REVIEW_20260928_batch5.md`）发现 Critical #1——`aggregator.run()` 内裸 `os.getenv` 因缺模块级 `import os` 抛 NameError，使 3b `conflict_summary_shadow` / 4b `market_theme_shadow` 两个影子字段自 0928 合入以来**从未真正产出**（run() 必崩；域回归 86 passed 未捕获因当时无 run() 级测试）。已补 `import os` + 新增 `tests/test_aggregator_run_smoke.py`（run() 级冒烟，修复前必失败）。**后果**：0929 之前任何归档的 3b/4b 影子字段均为空/不存在，不可作 4b/3b 转正的标定读数输入；影子读数自本修复部署之日起重新累。4.1 `sell_source_shadow` 在 `portfolio_decision`（非 aggregator.run）路径，**不受此崩溃影响**，但同样需 P0 提交后重新累读数验证。

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
| 2 | ~~`GlueStrength.basis` 无生产者~~ ✅ **已结**（`basis` 是常量口径声明，语义落在 prompt `aggregator.py:824`，见 §1.C C-P9）；**仅剩** `HotStock.mention_source` 的 `nga_mention` 值无生产者（实测只有 `sentiment_analysis.py:805` eastmoney / `:836` xueqiu 两个生产者）。**【2026-09-26 半结】**注释层已落：`schemas.py:544` 声明 `nga_mention` 为预留值、当前无生产者。**【已拍板 #2a（2026-09-28）：不接线、维持预留】**——A6 退役后 NGAS 源不在链路，接无源枚举=死代码（属 §9 缺陷谱系）；注释已同日更新标拍板，本项**结项** | ✅ **已拍板 #2a，结项（2026-09-28）** |
| 3 | `signal_dates` 的口径：「**按次**」还是「**按日**」 | ✅ **已拍板 #3a 并实施（2026-09-26）：按日去重**——`persistence.py` 每种信号类型每日最多记 1 条，`signal_count_30d` 语义由「30 日次数」变为「30 日天数」（逐板块明细仍在 unified.json cross_node_signals 可取证）；单测 2 例（同日多板块共振 / 同日重跑幂等）。存量历史条目不改写，随 30 日窗口自然衰减；R5 复测仍按原计划 10-11 后跑 `verify_same_day_idempotency.py` 确认归零（详见 §9 #5） |
| 4 | 存量的 **4 份测试污染归档产物**是否清理（`data/got_daily/20260911/agent_glue.json`、`20260912/agent_glue.json` 及各自 `_replay/` 一份） | ✅ **已拍板 #4b 并执行（2026-09-26）：备份后删除**——4 份已移入 `data/got_daily_backup_20260926_1700/`（留档清单含 md5 与 verification.md §2.2/§4.1 互证），原目录仅剩空 `_replay/` 子目录（盘点按文件存在性判定，无影响）；现场证据以备份 + verification.md 文字记载形式保留 |
| 5 | **R-B8 的 `market_regime.*` 字段去留**（2026-09-14 干跑发现） | ✅ **已拍板 #5a 并实施（2026-09-26）：移出「应不变」清单**——`verify_layer_b_consistency.py` 的 `_collect_state_fields` 不再提取 regime/dwell_days/previous_regime/transition_count/transition_pending 五字段；`check_r_b8_r_b9` 对该子集单独比对并在证据行列「按设计 SKIP，不计 FAIL」；零生产代码改动，verification.md §4/§5.1 同步 |
| 6 | **R-B8 / R-B9 / R-C7 在现工器下是否继续推进** | ✅ **已拍板 #6a（2026-09-26）：维持「仅归档」边界，不扩 `--got-replay` 链路**——§1.F.b 已自愈后主要痛点解除，扩链路成本/风险与收益不对等；§1.F.a 继续按设计文档记录限制 |
| 7 | **regime 状态机切换计数器停摆根因分析**（2026-09-26 新增并经归档实测定根因，自 A6 阶段 2 条件 3 移出时登记）：见下方「§7.1 停摆根因实测」 | ✅ **已实施**（2026-09-26）：迟滞豁免（`pinned_days≥3`）解确认死锁 + `transition_events` 事件列表修正 `transition_count` 语义 + 应急回退开关 `DISABLE_REGIME_OSCILLATION_FIX`，详见 §7.1 实施记录 |
| 8 | **P2-1 `affected_sectors` 是否升级 `list[dict]`（区分正负面）**（2026-09-26 收口时登记）：原预期随 T5 对齐层退役消失，接线后重判该层保留、字段活消费，「待退役」归属作废（见 §2 表） | ✅ **已拍板 #8a（2026-09-28）：不升级，明示「不区分正负面」为长期口径**。理由：需同步改 3 处且漂移历史回放基线，当前无「负面被当正面加分」的实证病例。口径声明已落三处（schemas 字段描述 / `_group_events_by_sector` / `_align_events_to_sectors`），翻案需新证据并同步改这三处 |
| 9 | **解禁事件的度量层次与用途**——是板块级负向退出证据，还是个股级中性提示？（2026-10-06 由【架构计划】WI-P2-4 取证引出，§11.6 → §11.9） | ✅ **已拍板（2026-10-06 负责人裁决）：个股级 · 中性 · 纯展示**。裁决原话「解禁是个股层级的事件，最多也就是个中性事件；分析解禁也只用分析持仓和每日的推荐票的解禁信息」。结果：① **不建 risk_events 通道、不引入 `GOT_RISK_EVENTS`**，改为持仓 code ∪ 当日推荐 code 的个股级解禁查询，只在报告作一行提示，**不进 `catalyst_events`、不参与评分/排序/退出**；② §11.6 的板块级反指读数（解禁板块 T+5 超额 +0.99%，p=0.00016）**改标为「不适用」而非「前提被证伪」**——板块级跑赢不能否定「解禁股自身走弱」，那个命题在板块口径下从未被检验；③ 范围收窄到只做解禁（减持/业绩预亏另议），前瞻窗口天数与占比提示阈值待实现时拍。**⚠️ 编号冲突**：本表 #9 说的是【架构计划】**WI-P2-4**，与 §2 处置表的「P2-4」（0902 审计的「其他」家族垃圾箱）**同号不同物** |
| 10 | **减持 / 业绩预亏的定性与数据源**（2026-10-06 由【架构计划】WI-P2-4 的「范围只做解禁」引出，负责人定性「这俩是负面性事件」；详见【架构计划】§11.10） | ✅ **已拍板（2026-10-06 三项裁决）**：① 预亏反向 bug＝**先取证再分流**——取证已跑完（`scripts/audit_earnings_forecast_direction.py`，产物 `data/earnings_forecast_evidence.json`）：**价格面检不出任何一个「预告类型」显著跑输基准**（负面合并组 T+5 超额 +0.66%、p=0.143、n=187；Wilson95 [0.375, 0.516] 跨 0.5），所以 `negative` 只能按**语义标签**打并明写「未经价格标定」，WI 落新增 **WI-P1-10**；② 减持源＝**暂用 LLM 新闻通路**——但要更正一条我给错的依据：akshare 1.18.91 **并非无减持接口**，`stock_ggcg_em(symbol="股东减持")`（实测 113,021 行 / 111 s，无日期参数）与 `stock_hold_management_detail_cninfo(symbol="减持")`（16,581 行 / 3.3 s，仅董监高）都在；真正的缺口是**没有前瞻的「减持计划/拟减持」公告源**（全库两词零命中）⇒ 裁决仍成立但理由换成这条，回溯性变动源可供新增 **WI-P2-13**（预亏/减持/解禁三行个股级提示进 `portfolio_decision.warnings`，零决策影响）使用；③ 决策参与度＝**先停在「停止污染」**，不新建个股级减分/否决通路。⚠️ **附带发现的活 bug 待拍**：`data_provider/fundamental_adapter.py:343-348` 用 `stock_yjyg_em(symbol=code)`，而实测该接口只收 `date` → TypeError 落第二候选 `{}` → 拿 **2020-03-31 报告期**的预告（陈旧 6 年）再按 code 过滤 ⇒ `earnings.forecast_summary` 要么空要么陈旧；`stock_yjbb_em`（默认 20200331）/`stock_yjkb_em`（默认 20211231）同病，消费者含 **RiskAgent 的 get_stock_info 工具**（`agent/tools/data_tools.py:460`）——单股分析体系的「业绩预亏」否决通路长期喂空/陈旧字段，**比催化剂日历那条更活**。⇒ **已拍板（2026-10-06 负责人：「立即另开 WI 修」）并已落地为【架构计划】WI-P1-11 / §11.11**：实测危害比本段估的更大——不止一个字段空，而是整个 `get_fundamental_bundle` 8.83 s / 6.47 s 超了 3.0 s 预算被丢弃（growth/earnings/institution 全块长期失效）；修后改为按报告期取全市场表 + 进程内缓存，后续股票 2.4~3.0 s，并新增 `earnings.forecast_type`（预增/首亏/扭亏…——WI-P1-10 与 WI-P2-13 真正需要的输入）。**仍待拍的两件**：① `fundamental_fetch_timeout_seconds=3.0` 与逐源延迟不匹配（**修后首股仍 9.8 s 超时**）——已另登本表 **#11**（已拍「先挂 backlog 观察」）；② 同族未修项（`stock_institute_hold` 默认 20051 陈旧表 / `stock_gdfx_top_10_em` 实测 KeyError / `stock_individual_fund_flow({"symbol"})` 死候选）均需先定口径 |
| 11 | **基本面 bundle 时间预算与逐源延迟不匹配**（2026-10-06 由【架构计划】WI-P1-11 修后遗留引出，§11.11 遗留 (a)）：`fundamental_fetch_timeout_seconds=3.0`（`src/config.py:985`）是给**整个 bundle** 的（`base.py:3079-3085`），而 akshare 业绩类接口是「按报告期返回全市场表」——**一个报告期只拉一次**，所以同日逐只股票里**只有首股付这份钱**（实测修后首股 9.84 s、后续 2.4~3.0 s）⇒ 首只股票的基本面上下文仍会被 `_run_with_timeout` 丢弃 | ⏸ **已拍板（2026-10-06 负责人：「先挂 backlog 观察」）**：不动任何配置、不另开 WI，先在真实逐日跑里统计 bundle 延迟分位与 `earnings`/`growth`/`institution` 各块的**实际超时率**，有读数再决定是提预算还是把基本面取数从 3 s 主链路挪出。**理由**：拿一只样本股（600187，首股付表拉取）的读数去调全局超时是错的；提预算的代价是基本面不可用时拖慢整轮并挤占共享 stage 预算，且属全局性能参数、动它需回放基线对账。**已知限制（须随本条一并读）**：单只股票的一次性分析路径（`services/alphasift_service.py:2162`、`agent/tools/data_tools.py:460` 的 RiskAgent get_stock_info）不存在「后续股票摊薄」——它就是首股，**可能长期拿不到基本面**；观察期统计需把逐日批量路径与单股一次性路径**分开计**，混在一起会把单股路径的超时率摊平掉。**〔审核订正 2026-10-07，【架构计划】§11.11 审核订正段〕**：①「只拉一次」严格说是「**非空表**只拉一次」——失败/空表不写缓存，未公布报告期（如 10 月初的 20260930）会被逐股重复拉取（O(股数) 次失败请求），观察期统计 bundle 延迟时需覆盖这份钱，负缓存（30~60 s 短 TTL）待实施随本条一并拍；② adapter 单例 + 锁外拉取，8 并发下首批股票惊群重复拉全市场表（瞬时 IO/延迟尖峰，非正确性 bug），本轮登记不修，随观察期一并看 |
| 12 | **WI-P1-7(b) 家族兜底阈值重标定与 enabled 转正**（2026-10-06 由 shadow 首批读数引出，【架构计划】§11.13）：`GOT_FAMILY_EMBED_MIN_SCORE=0.55` / `MIN_MARGIN=0.05` 实测在真实数据上**不可达**（10 归档日 93 个「其他」板块 93/93 全 `low_score` 被拒，score 最高仅 0.5143）——照现值转正 enabled 是彻底空转 | ✅ **已拍板（2026-10-06 负责人：「另开阈值重标定 WI」）**：本轮停在「读数落档、enabled 冻结、开关继续 off」；排期单开 WI，内容四项——① 扩样本复跑（本轮只用了 10 日，归档共 49 日；但「其他」存量随词表演进塌缩，早年档会高估负荷，取样需权衡代表性）；② 在 0.45~0.50 区间定 S/M（读数支持「排序对、绝对值错」：S=0.45/M=0.05 档 11 板块判属全部成立，但那是 AI 自检需负责人复核；margin 拦的是「靠名字相似的伪命中」，降 S 时不应一起砍）；③ **盲区候选集裁决**：`FALLBACK_FAMILIES`（14 候选含华为生态/前沿科技）是否与 `PROMPT_EXPOSED_FAMILIES`（12）对齐——embedding 判得出 glue 永远产不出的家族（首选家族分布里前沿科技 10 次）；④ 达标后才谈 enabled（届时才需回答 §11.7 三问③的下游 diff） |

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

- **同日多次 GOT 运行归档互相覆盖**（2026-10-08 登记，详 [`plans/加速末期识别缺陷分析_20261008.md`](plans/加速末期识别缺陷分析_20261008.md) §五）：`archive.py` 按日覆盖写 `data/got_daily/YYYYMMDD/`，当日重复运行仅存最后一份快照；9/23 案例中「当天多次跑、早先推荐被覆盖」导致买入决策追溯不可考。修复方向：`YYYYMMDD/run_HHMM/` 分目录或 `meta.json` 记录当日运行次数与各次 allocation 摘要（独立小项）。

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

*修订：2026-09-28（非冻结项排期轮 · 第二批拍板 + 账本修正）—— **四项拍板登记**：§7 #2 `nga_mention` 结项（#2a 不接线维持预留）；§7 #8 P2-1 结项（#8a 不升级 list[dict]，长期口径，声明落三处：schemas/`_group_events_by_sector`/`_align_events_to_sectors`）；§5 B3 定方向（维持现状+口径标注，并更正旧「无消费者」过期记载——实有 3 处活消费）；§5 Phase 5c 拍板为本批主线（P1-1 需转正后 ≥10 交易日，预计 ~10-05 解锁）。**三处账本过期修正**：§1.D D-P3 → ✅ 已收口（双源已消解，`catalyst_config.py:159` 单源 + `archive.py:454` import）；§2 P1-4（OPEN-D2）→ 重判「闲置口径非待查」（G2 已改 ths_hot_sectors）；§3.3 OPEN-C3 → 更正为**永不触发死分支非恒真**（生产端从不写 persistence_regime），处置转决策简报。§2 统计同步（了结 9→10，归属 5→4）。同轮代码注记：`catalyst_config.py` D-P3 TODO 更正、B3/P2-1/OPEN-D2/#2a 四处口径注释——**均零行为变更**（注释/docstring/字段描述，不进任何计算路径）。*

*修订：2026-09-28（非冻结项排期轮 · 批 2–4 执行）—— **批 2（取证）**：`.review_tmp/sched28_forensics.py` 扫 48 归档日只读取证（mention_count 分布 / OPEN-C3 触发面 / `glue_consumed` 样本计数 / P2-3 截断抽查）；OPEN-C3 死分支三选一决策简报 `docs/got/decision_brief_openc3_20260928.md`（建议删死分支，待负责人拍板）；5c 样本实测 **6<10** 交易日→锁定批 4.2 DEFER。**批 3（观测落盘）**：P2-3/OPEN-D9 `raw_position_ratio` pre-clip 原值已落（schemas 新字段 + `_assess_risk` 返回体，单测 `TestRawPositionRatio` 3 绿）；D-P1 divergent 分位落盘→改判随 A6 退役消解（零代码，避免造新死代码）；A-P0-5 核实已落地（`tracking_date < today`，幂等静态检查 A4 PASS 佐证）。**批 4（Phase 5c 行为变更）**：4.1 W2/W1 卖出来源已落（`_allocate` Step 1.5 + W1 回补抑制，`GOT_SELL_SOURCE_ENABLED` 默认关，宽限走 `sector_drift_grace`）+ 单测 `test_sell_source_over_limit.py` 5 绿；4.5 zip 金丝雀已落；4.2 DEFER（样本 6<10）/ 4.3 验证性无代码 / 4.4 数值判定归负责人。**回归**：got/portfolio/sell_source/winrate/glue 域 421 passed；5 例失败均判环境/既有（3×`exchange-calendars` 未装→交易日检查禁用、2×node6_glue 批量跑跨用例全局态污染但隔离跑均绿）；R-C3 十锚点逐位一致 PASS（glue 口径未触）；幂等静态形状 A1–A6 全 PASS（R1/R3/R4/R6 需实跑双快照 SKIP、R2/R5 为数据态预存 FAIL，均非本批代码所动）。验收红线判读归负责人。*

*修订：2026-09-28（批 4.1 CodeReview 后修复①②③）—— 三视角评审实证三项计算缺陷并修复：①Critical 重复卖单（Step 1.5 候选未排除 Step 1 已 sell/reduce 标的且取原始股数 → 同一 symbol 二次开卖 + proceeds 虚增，双向污染 enabled 与 shadow 读数）→ Step 1 维护 `remaining_qty` 真相；②零股负股数（`max(100,min(…))` 对 <100 股抬回一手）→ 去 max 改整笔清仓；③W1 抑制被整手尾差击穿（`position_gap>0` 抢先于 `elif`，实测同轮 sell+buy 并存）→ `over_limit_forced` 提为前置短路。单测 7→**11 例**（新增重复削减/reduce 余量/零股/取整超配 4 边界），关联回归 48+11 全绿。未修登记：Warning④ `.env.example` 开关区未录入、Warning⑤ W3 聚合口径削 `accounts[0]`（标为 enabled 转正硬前置）；建议项（`__init__` 初始化影子态/金丝雀补测/字段改名）及「R-B8 移出组」表述校准（校验脚本按显式字段清单取数，新字段本不在清单内）归负责人。**修复前影子读数不可作标定输入**。*

*修订：2026-09-28（批 4.1 设计修正·默认关→真影子）—— 负责人指出「影子开关关了不跑，写它干啥」：初版 `GOT_SELL_SOURCE_ENABLED` 默认 OFF 使 Step 1.5 整段 `if` 不进，等于**零观察零作用**，与仓内 `_SHADOW` 惯例（跑但不生效，如 `AGENT_SEMANTIC_GLUE_SHADOW`）不符，属写了不跑的死分支。→ 重构为**三态**：新增 `GOT_SELL_SOURCE_SHADOW`（默认开）每日跑减仓检测、将「本会削谁/多少/是否撞家族宽限」落新增观测字段 `PortfolioDecision.sell_source_shadow`，**但不写 allocation、不改 `sell_proceeds`/`over_limit_forced`→ 决策逐字节零改动**；`GOT_SELL_SOURCE_ENABLED`（默认关）作为后续「转正」才开的生效开关。新增字段按 raw_position_ratio/#5a 先例标注移出 R-B8/幂等不变组。单测扩至 **7 例**（生效 3 + 影子 2 + 全关 1 + 默认影子 1）全绿；`test_phase2_audit_fixes.py`+`test_portfolio_decision_lot_rejection.py` 27 绿（含构造完整 decision 的路径，验证新字段接线无碍）。*