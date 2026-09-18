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
| **C-P8** | 明确 M4 系数 = Spearman，增注"M4-a 的独立性以 B1 修复为条件"，M4-a 红线数值标记「待标定」 | ✗ 未落地 |
| **C-P9** | `GlueStrength` 新增 `basis` 字段 + prompt 写明替代综合评分 | ✅ **两半均已落地**（2026-09-14 复核）——字段见 `schemas.py:1086-1089`（`default="composite"`，commit `8ad119b`，09-12）；prompt 见 `aggregator.py:824`「**此字段替代的是综合评分（技术面+资金面+消息面+情绪面）**，不是只替代技术面」。原记「无生产者」字面成立但**会误导**：`basis` 是恒真的口径声明，语义在 prompt 里，**不需要赋值语句**；归档侧 0911 起可见 `basis="composite"` |
| **C-P11** | 回填跑批（`--got-replay 20260901`…`20260908`），影子样本 4→8 天 | ✗ 未实施（`_replay` 产物文件 **2 份**，去重后 **1 份**，且均为占位态 ⇒ **有效样本 0 天**，见 [`verification.md`](verification.md) §4.1） |
| **C-P12** | prompt A/B（保守版 vs 全量归属版），输出 M2×M3 对照曲线 | ✗ 未实施 —— **R-C6 因此 SKIP** |
| **C-P13** | 红线标定 | 依赖 C-P11/C-P12，文档明示**本轮不执行** |
| **C-P3** | `replay_mode` 下允许 glue（受新开关控制，默认 `false`） | 首轮 ⚠️（replay 分支新增落盘副作用，违约） |
| **`0903.md` §8.1 口径** | M1′/M6′ 行的字段路径与数值三重错误（路径不存在 / 数值是规则侧的 / 正确值未采用） | R2.4；R2.7(A) P1-8 记为"部分修"（三子项均未按 C 层定义落地） |

### 1.D 0902 对账层（D 层）

修复清单 D-P1…D-P14 中**未落地或部分落地**：

| 项 | 说明 | 状态 |
|---|---|---|
| **D-P1** | `divergent` 阈值与分位可观测（样本数 / 5% 分位 / `min()` 后最终阈值 / 每确认板块落盘） | 部分——缺分位落盘 |
| **D-P2** | 埋伏闸门逐层漏斗 `ambush_gate_funnel`（含 `evaluated` / `skipped_reason`） | 部分——**`skipped_reason` 全仓 0 命中，该字段在当前代码里根本不存在**；真实残留是"死闸仍被计 `evaluated`" |
| **D-P3** | 定义并落盘「正式池水位」目标 `AMBUSH_POOL_TARGET_RANGE` | 部分——常量双源（`archive.py` 仍硬编码 `(2,6)` + 过期 TODO）；`AMBUSH_POOL_MIN_DAYS` 零消费者（R2.9 #3 记为已删） |
| **D-P5** | 闸门文档诚实化（「4 层闸门」→ 标注 G2 注意力闸 / G3 未启用 + 加「未评估」留痕） | 部分——留痕未落地；且**文档位置指错**（真正的载体在 `architecture.md:505`，不在 `CLAUDE.md`） |
| **D-P9** | `scripts/check_audit_claims.py` | 已建并入版本控制；实跑 `unverified = 9` |
| **D-P10** | 附录 A 的可复算性（与 R-D2 对齐） | ✗——实算 4 项中 2 项与文档不一致 |
| **D-P11** | E3 第三层：修解析 + 落盘分支走向 | 首轮"仅解析层"（P1-4），R2.1 记为已修（改由 `sector_role` 决定） |
| **D-P12** | E3 第一层：口径声明 + 落盘绑定次数（**不归一化**） | 部分——绑定次数未落盘；`PortfolioDecision.sector_exposure_binding` 无消费者 |
| **D-P13** | E3 第二层：改读 `sector_role` | 首轮 ✗（只有 `sector_role = None` + `pass` + TODO），R2.1 记为已修（改读 `sector_role_map`） |
| **D-P14** | E10 删死参数 + 修注释 | R2.1 记为 ✅ |
| **`_temp_check.bin`** | 仓库根游离调试残片 | R2.1 记为已删（本次核验：确不存在） |

### 1.E 全修轮之后仍残留的项（来自 §R2.x）

| 项 | 位置 | 说明 |
|---|---|---|
| 注释与实现不符 | `portfolio_decision.py:2837-2838` | 注释写"回退到 `sector_exposure_limits` 反推"，实际 fallback 是 `"mainline"`，**无反推逻辑** |
| `sector_limit_detail` 落无用字段 | `portfolio_decision.py:2876-2878` | 仍落 `role_threshold` / `stock_limit`，但分支已不由它们决定 |
| `sector_role_map` fallback | `portfolio_decision.py:2858` | 与 §1.B 同项——放大风险，**需裁决** |

### 1.F 验收工器的能力缺口（2026-09-14 实测新增）

两项**工具/接线缺口**，它们是「红线跑不出结果」的直接原因。**与代码 bug 无关**——是验收工器本身不具备产出输入的能力。

| 项 | 位置 | 说明 |
|---|---|---|
| **`--got-replay` 只跑 Node 6** | `main.py:1318-1365` | 注释自述「只跑 Node 6，从归档加载数据，不跑 Node 1-5，**不写归档**」，仅产出 `_replay/unified.json`。⇒ ① `portfolio.*` 字段缺失（R-B8 需要的 Phase 2 未跑）；② `sector.json` 缺失（Node 2 未跑）。**更根本的是②**：B 层修的是 `family_keywords.py` + `sector_analysis.py`（**Node 2** 词表），而该回放**验不到 Node 2** ⇒ `got_layer_b_plan_0911.md` §6.3 写的「跑 Node 6 + Phase 2」与 B 层实际改动位置对不上。R-B8 / R-B9 / R-C7 均受此阻塞 |
| **Phase 2 路径不落 T1 归档** | `stock_screener.py:958` vs `:2784` | `_save_got_result()` **只在 `run_got_mode()` 内被调用**；而 `main.py --got` 的 Phase 2 经 `portfolio_decision.py:2634` 触发 T1，**该路径不落盘** ⇒ T1 verdict 只写日志。实测 2026-09-14 开开关跑通（`reject=1`），`data/got_screener/` 仍停在 `20260825.json`。要归档需另跑 `scripts/run_got_screener_v2.py`，或让 Phase 2 也调 `_save_got_result` |

> 📌 第 2 项对 A5 影子期的连带影响见 [`verification.md`](verification.md) §1.2。

---

## 2. 0902 报告 15 项的**处置表**

`archive/GOT_0902_audit_report.md` 的 15 项，逐条给出真实处置。
**本表取代原先的「10 项未修」**——那 10 项里有 4 项实为「待退役」（修了会随 §6.1 删除而消解），1 项已改归属，1 项降级为口径标注，2 项挂起，1 项明确延后，只有 1 项是真的未排期。

| 编号 | 级别 | 现象 | 处置 | 出处 |
|---|---|---|---|---|
| **P0-1** | 立即 | `capital_check="divergent"` 是死分支（生产端从不 return，消费端 4 处检查它） | **待退役** | §6.1 #1 `_cross_check_capital`【删】+ #9 四个阈值常量【删】：*"2026-09-02 P0-1 刚校准完，校准完即退役"* |
| **P0-2** | 立即 | `confidence` 恒 0.7——三个加分项全悬在 #1/#2/#3 上（`aggregator.py:790-800`） | **待退役** | 同上，同一批机制；修它等于给待拆的机器换零件 |
| **P0-3** | 立即 | 粮食概念 -4.26% 仍 `active_mainline` A 档 | ✅ **已生效** | — |
| **P0-4** | 立即 | 液冷服务 tier=S 却标 exhausted | **不修（行为正确）** | 走 exhausted→active 回归路径，弱反弹不触发是正确行为 |
| **P0-5** | 1 周内 | `ambush_pool` 全被闸门拦死（8 个埋伏板块 → 池空） | **待退役** | 闸门判据即 §6.1 #10/#11（T5 对齐层），随退役消失 |
| **P1-1** | 1 周内 | 持仓股 `name == symbol`（600250 / 605566） | **明确延后** | OPEN-D8：排在下一批 |
| **P1-2** | 1 周内 | reasoning 中同一板块出现 ×3 | ✅ **已生效** | `portfolio_decision.py:1526-1535` `seen_canons` set 去重（D-P7）；⚠️ 原「代码未去重」的记载已过期 |
| **P1-3** | 2 周内 | 50 只热股 `mention_count` 极差 <3%，无区分度 | **降级口径** | D-P8：加 `mention_source` 标注字段，**数值不动** |
| **P1-4** | 2 周内 | `associated_sectors` 50/50 全空 | **挂起待查** | OPEN-D2：是否真无可靠数据源 |
| **P1-5** | 2 周内 | `reasoning.md` 中 Node 7 段出现 ×2 | ✅ **已生效**（见下注） | D-P6 → 并入 A-P0-8；`archive.py:642` 覆盖写 |
| **P2-1** | 按需 | `affected_sectors` 不区分正负面 | **待退役** | 该字段属 T5 事件→板块对齐层 §6.1 #10/#11 |
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

**统计**：已生效 4（P0-3 / P1-2 / P2-4 / P2-5）+ 不修 1（P0-4）+ 症状消失 1（P2-2）= **6 项了结**；
待退役 4 + 已改归属 1 + 降级口径 1 + 挂起 2 + 延后 1 = **9 项各有归属**；**真·未排期 = 0**。

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

- **OPEN-C1** `transition_count` 时间炸弹（约 2026-09-25）
- **OPEN-C2** M3 人工流程未定义
- **OPEN-C3** `persistence_regime == "low"` 也是 11/11 恒真 → 与 OPEN-C1 同批排查
- **OPEN-C4** M1 的信息量到底多大 → 由 C-P12 曲线回答，**不预设**
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

### 4.2 阶段 2 · 退役（3–5 交易日后单独 commit，本次不做）

**触发条件**（三条全满足）：
1. 观察窗口 ≥ 3 个交易日 `glue_consumed=true`，且 `S1/S2` 降级率 ≤ 10%
2. 决策面结果无回归事故（Node 7 推荐 T+3 胜率相对 9 月基线不劣化）
3. 0924 前 `transition_count` 未跌破阈值 5（OPEN-C1 时间窗）

**动作**（`archive/agent_glue_plan_0903.md` §6.1 / §11.3）：
1. 退役 §6.1 的 **17 项**（跨维校验三件套 / T5 事件→板块对齐层 / 手写映射表消费点 / LLM 权限与准入阈值）
2. 删 `SectorIntel.family_resonance` 字段
3. 删 `_llm_refine_sectors.adjustments` 分支代码本体
4. 修订 `archive/remote_integration_plan.md`

> ⚠️ 不可在阶段 1 观察期未满时删除三件套——0915 类上游故障日 `glue.degraded_sectors` 非空或 `status != "ok"` 时，A6 分支自动跳过，等价于影子期行为。三件套代码本体删除 = 失去最后一道兜底。

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

| # | 项 | 性质 |
|---|---|---|
| 1 | `sector_role_map` 的 fallback 是 `"mainline"`（**放大风险**而非保守侧） | 策略决策：改它会改变"角色未知"时的仓位方向 |
| 2 | ~~`GlueStrength.basis` 无生产者~~ ✅ **已结**（`basis` 是常量口径声明，语义落在 prompt `aggregator.py:824`，见 §1.C C-P9）；**仅剩** `HotStock.mention_source` 的 `nga_mention` 值无生产者（实测只有 `sentiment_analysis.py:805` eastmoney / `:836` xueqiu 两个生产者） | 口径声明类，接线需先定语义 |
| 3 | `signal_dates` 的口径：「**按次**」还是「**按日**」 | R5 要求同 `(type, date)` 至多 1 条，而实现**有意**按当日信号出现次数逐条落盘，`signal_count_30d` 依赖该计数。二者必居其一（详见 §9 #5） |
| 4 | 存量的 **4 份测试污染归档产物**是否清理（`data/got_daily/20260911/agent_glue.json`、`20260912/agent_glue.json` 及各自 `_replay/` 一份） | 污染源已修（测试已隔离 `GLUE_ARCHIVE_ROOT`），存量文件**未删**——它们是该结论的现场证据。清理会失去现场，不清理则 `data/got_daily/` 里始终有 4 份非生产产物。取舍见 [`verification.md`](verification.md) §2.2 |
| 5 | **R-B8 的 `market_regime.*` 字段去留**（2026-09-14 干跑发现） | 验收判据决策：① 移出「应不变」清单（承认跨日状态机在回放下必然变，见 §9 #6）；② 或改判据为「回放前后**窗口对齐**」；③ 或让 R-B8 只验 `unified.json` 的非状态字段 |
| 6 | **R-B8 / R-B9 / R-C7 在现工器下是否继续推进** | 工器决策：`--got-replay` 不跑 Node 2 ⇒ **验不到 B 层改动本身**；不跑 Phase 2 ⇒ `portfolio.*` 必然缺失。要么扩 `--got-replay`（成本较高），要么收缩红线范围。见 §1.F |
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
| 6 | **未纳入版本控制的验收脚本** | `.gitignore:76` 的 `verify_*.py` 规则下仍有 6 个：`verify_flow_archive.py`、`verify_position_sizing.py`、`verify_remote_data_sources.py`、`verify_risk_fix.py`、`verify_t5_alignment.py`、`verify_ths_*.py`（4 个 0911 手具已有负向例外） |
| 7 | ~~**未跟踪工具**~~ | ✅ **已结**（2026-09-14）：`scripts/export_glue_family_audit.py`（M3 人工抽查工具，702 行）已入库，commit `0ac5252` |
| 8 | **附录 A 基线不可复现（R-D2 FAIL）** | `archive/got_layer_d_plan_0911.md` §附录 A 的四个子计数中 **2 项与实算不一致**：① `ambush_pool_saturation_days` 文档记 **≥10**，实算 **5**（顶格日共 8 天、最长连续 5 天）——文档自注"缺口见 D-P2 落盘"，即**该基线的数据源未落盘、无法复核**；② `dead_signal_count` 文档记 **4**，实算 **3**——文档基线早于 D-P14 删 E10 落地。R-D2 由「验收红线」降级为**文档缺陷**：它的输入是一份过期基线，不是代码状态。 |

---

## 9. 红线与验收脚本自身的缺陷

被移出 shadow 文档的 4 条，加复查中新发现的 1 条，共 5 条。

| # | 项 | 缺陷 |
|---|---|---|
| 1 | **R-D1 口径自相矛盾** | `archive/got_layer_d_plan_0911.md` §6 的**定义**是「报告中每个**已修复/已解决**声明都附一条可复算证据」= **窄口径**；但同节**基线明细**把 9 条「待修」全算进去了 = **宽口径**。15 项里只有 P0-1 是「已修复」声明 ⇒ 窄口径 `unverified = 1`，宽口径 `= 8`（脚本输出 9，多算 1，见 #4）。**宽口径下「如实标注待修」反而被罚**，与「可复算率」这个名称的语义相反 |
| 2 | **R-D1 是 burndown 而非验收红线** | 它的 9 项读数**全部由未实施工作决定**（见 §2 处置表：待退役 4 / 延后 1 / 挂起 2 / 降级 1 / 改归属 1）。A6 退役一落地，4 项随 §6.1 删除自动消解 ⇒ **R-D1 → 0 是 A6 的副产品，不是 A6 的前置任务** |
| 3 | **R2 把市场事件当验收条件** | 「出现 `previous_regime != regime` 的归档日 ≥ 1 天」。按状态机设计（`aggregator.py:583-590`），`prev != cur` **只在情形 2（确认切换）**发生，其余两情形**按设计就相等**；自 08-27 接线以来 0 次触发。**零切换无法区分「市场没切换」与「情形不可达」**，两种解释都不可由该红线区分。✅ **已收口（2026-09-14）**，见 §9.1 |
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
