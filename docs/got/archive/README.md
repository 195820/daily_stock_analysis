# GOT 文档归档（2026-08 ~ 2026-10）
> **原位置**：`docs/archive/got/README.md`（2026-09-17 文档结构重排移入当前位置）

> 本目录存放 GOT 修复与规划线的**原始文档**：2026-09-14 从 `docs/` 根目录移入 `docs/archive/got/`，2026-09-17 文档重排平移至当前位置 `docs/got/archive/`，并收入原 `docs/custom/` 的 3 篇已完结 GOT 文档（见下表末三行）；**2026-10-10 再收 14 份已完结过程稿与根目录计划稿**（见下表「2026-10-10 新收」段）。
> **正文与节号一字未改**（包括节号，如 `agent_glue_plan_0903.md` §5.3），因此代码与脚本中的**节号引用仍然可解析**。
> ⚠️ 但**不是“文件一个字节都没动”**：2026-10-10 搬迁时对三份过程稿做了最小改动（`decision_brief_20260926.md` 加首屏归档注 + 修 3 处指回 backlog 的相对链接前缀；`cp12_prompt_variants_draft_20260926.md` / `cp13_phase2_closeout_20260928.md` 各改文档路径引用），对 6 份根目录稿只新增首屏状态头。三类改动均**不移动 `§X.Y` 节号**，故节号引用不受影响；带路径的引用（`docs/got/<file>.md`）已统一改指 `docs/got/archive/<file>.md`。
>
> **当前有效文档**（`docs/got/` 顶层，仅四份清单类）：
> - [`backlog.md`](../backlog.md) —— 待实施
> - [`verification.md`](../verification.md) —— 待验证 / 影子期
> - [`architecture.md`](../architecture.md) —— 架构现状权威描述（含时效注记）
> - [`README.md`](../README.md) —— GOT 文档导航
> - [`../plans/`](../plans/) —— 待实施 / 待批准计划（2026-10-14 治理轮后，见 [`docs/INDEX.md`](../../INDEX.md) 二区）
>
> **2026-11-02 新收**：`decision_brief_openc3_20260928.md` 由顶层移入本目录——文首已有 2026-10-10 更正头（「死分支」事实链被推翻），且 `backlog.md` §3.3 ② 已于 2026-10-15 拍板「维持观测面」，原「待拍板」状态结束。

---

## 一、本目录各文档是什么

| 文档 | 首次落库 | 性质 | 被什么取代 / 收口 |
|---|---|---|---|
| `GOT_0902_audit_report.md` | 2026-09-03 | 0902 全链路审核，15 项问题 | 已由 `got_layer_d_plan_0911.md` 全面对账 |
| `grilling_0903_solutions.md` | 2026-09-04 | 从 0903 输出反推的 8 个问题修复方案 | 大部分被 `agent_glue_plan_0903.md` §7.4 判定**作废或挂起** |
| `remote_integration_plan.md` | 2026-09-04 | 远端模块整合明细（§3–§10 模块规格） | 路线图已并入 `agent_glue_plan_0903.md` §10；模块明细**仍是权威来源** |
| `agent_glue_plan_0903.md` | 2026-09-04 | **主实施方案**：语义粘合层重构 + 远端整合 | 本身即主文档，9-11 / 9-12 各修订一次 |
| `got_run_model_plan_0911.md` | 2026-09-12 | A 层：运行模型 / 同日重跑幂等 | 实施记录见 `got_impl_review_0911.md` |
| `got_layer_b_plan_0911.md` | 2026-09-12 | B 层：同一概念多处计算与同名两义 | 同上 |
| `got_layer_c_plan_0911.md` | 2026-09-12 | C 层：影子验收设计的可度量性 | 同上；M1–M6 判据的权威来源 |
| `got_layer_d_plan_0911.md` | 2026-09-12 | D 层：与 0902 报告对账 | 同上 |
| `got_impl_review_0911.md` | 2026-09-12 | 实施审核 + 第二轮复审 | **本轮修复的权威记录**；其 §0–§10 红线表已被作者声明作废 |
| `got_revision_ledger_0911.md` | 2026-09-12 | 修订台账（4 轮复核 + 自曝 3 处新错误） | 记录四份文档的修订史 |
| `E3_fixes_summary.md` | 2026-09-11 | E3（D-P11/12/13）实施总结 | ⚠️ 已由 `got_impl_review_0911.md` §7 判定**声明与真实改动严重不符**，仅作史料 |
| `intraday_gate_plan_0907.md` | 2026-09-14 | Phase 6 盘中 0→1 确认闸门（草案未实施） | 待实施项已摘入 `backlog.md` §6 |
| `got_status_0914.md` | 2026-09-14 | 本批文档的状态盘点报告 | 其内容已重组进根目录两份新文档 |
| `verified_completion_log.md` | 2026-09-15 | 已验证完成项归档（7 项） | 从 `verification.md` 归档的已通过验证内容 |
| `0827_got_diagnostic.md` | 2026-08-27（原 `docs/custom/`） | 0827 全流程诊断报告（5 问题 + 4 系统级归纳） | 其 P0 三项已实施（见 §2.4），待实施项已摘入 `backlog.md` |
| `plan_got_timeseries_enhancement.md` | 2026-08-27（原 `docs/custom/`） | 时间线能力增强计划 v2.1 | ✅ 已实施并归档（commit `03bb3d1`，状态头已于 2026-09-17 收口） |
| `got_timeseries_calibration.md` | 2026-08-27（原 `docs/custom/`） | 上述计划的试点报告（≤27 天样本） | 由 `scripts/backtest_got_timeseries.py` 生成，输出路径已改指本目录 |

### 一之补 · 2026-10-10 文档整理新收 14 份

| 文档 | 原位置 | 性质 | 被什么取代 / 收口 |
|---|---|---|---|
| `audit_20260922.md` | `docs/got/` | 09-22 当日运行审计（P0-P3 + Q1-Q9） | Q1-Q9 已由 `4ad12b6` 与后续轮次全部落地；**代码注释仍按名引用其 Q 编号（如 `audit_20260922 Q2-A2`），编号仍可在本目录同名文件查到** |
| `decision_brief_20260926.md` | `docs/got/` | backlog §7 硬裁决项决策简报 | 七项裁决已于同日第二批全部拍板并实施（简报内已改登「拍板结果」） |
| `cp12_prompt_variants_draft_20260926.md` | `docs/got/` | C-P12 prompt A/B 文案草案 + M2×M3 矩阵结论 | C-P12 已实施；矩阵供 C-P13 标定使用 |
| `cp12_m3_audit_20260926.md` | `docs/got/` | C-P12 M3 分层抽样预审表 | ⚠️ **已被 2026-10-06 M3 终审取代**（误配率 2.8%、已结案转正），本表「待划勾」状态作废 |
| `cp12_event_level_ab_20260926.md` | `docs/got/` | C-P12 事件级 A/B 覆盖对照 | 其两项质量发现已转入 C-P13 立项依据 |
| `cp13_grounding_pipeline_research_20260926.md` | `docs/got/` | C-P13 接地式召回-验证 5 层管线设计笔记 | 阶段1 已落地并**休眠**（`glue_pipeline.py` replay-only）；**代码/测试中的 `docs/got/...` 路径已改指本目录** |
| `cp13_stage1_matrix_20260927.md` | `docs/got/` | 阶段1 L1 冒烟 + 9 天矩阵读数 | 阶段六条全过 → 阶段1 结项 |
| `cp13_phase2_closeout_20260928.md` | `docs/got/` | **阶段2 收口决策**（realized 口径证伪） | **解冻条件见本文 §6**（C-P13 挂起项的活引用，backlog §3.3 OPEN-C6 行指向此处） |
| `埋伏冻结与持仓延续性_PLAN_20260930.md` | 仓库根目录 | 工作流 A/C 原始计划 | 已并入选计划 [`../plans/入场分道与持仓延续性_总计划_20260930.md`](../plans/%E5%85%A5%E5%9C%BA%E5%88%86%E9%81%93%E4%B8%8E%E6%8C%81%E4%BB%93%E5%BB%B6%E7%BB%AD%E6%80%A7_%E6%80%BB%E8%AE%A1%E5%88%92_20260930.md)，**勿按本文重复实施** |
| `回测审核_入场分道与持仓延续性_20261001.md` | 仓库根目录（未入库） | 总计划独立重算审核（F1–F6） | F1–F6 已全部并入总计划正文；本件仅作审核留痕 |
| `持仓板块漂移_PLAN_20260918.md` | 仓库根目录（曾被 `.gitignore` 逐条忽略） | 板块漂移误卖修复设计 | 已实施（commit `20cba78`），现行叙述以 [`backlog.md`](../backlog.md) §4.4 为准 |
| `FIX_SUMMARY_sector_drift_20260918.md` | 仓库根目录（原 `FIX_SUMMARY.md`） | 同一修复的提交统计 | 行数/文件数已漂移，仅史料 |
| `文档更新执行报告_20260918.md` | 仓库根目录（曾被 `.gitignore` 逐条忽略） | 09-18 一轮文档同步动作报告 | 当前文档结构真源以 [`../../INDEX.md`](../../INDEX.md) 为准 |
| `ULTRA_REVIEW_20260928_batch5.md` | 仓库根目录（未入库） | 批次5 三视角审查报告 | **全部发现已修复落地**（见 [`backlog.md`](../backlog.md) §5 尾注）；原「归档去向待裁决」项就此收口 |

> ❗ **与「作废」区分**：上表前 8 行是**完结的过程稿**（结论已被采纳或推翻）；后 6 行是**被取代的重复稿**（同一件事已有唯一现行叙述）。两者均不得再作为开工依据。

### 一之补② · 2026-10-14 文档治理轮新收 1 份

| 文档 | 原位置 | 性质 | 被什么取代 / 收口 |
|---|---|---|---|
| `架构审查计划_20261011.md` | 仓库根目录（untracked，原 `GOT_项目架构审查_PLAN.md`） | 10-11 架构审查轮的只读审查计划（五项拍板 + R-A/R-B 硬要求） | 审查已执行：报告 `reports/got_audit_20261011.md`（不入库）+ backlog 新挂号（§1.A F-1 / §1.J 四条 / §7 #13–#14 / §8 #12–#14）；四项挂号已于 10-14 全部收口，后续进度以 [`../backlog.md`](../backlog.md) / [`../verification.md`](../verification.md) 为准 |

> 同轮另有两份**现行**文档按「计划单一落点」条款自仓库根移入 [`../plans/`](../plans/)（非归档）：`外网对标实施_PLAN.md` → `plans/外网对标实施计划_20261011.md`；`GOT_问题分析_PLAN.md` → `plans/regime与数据流断路_文献对照分析_20261011.md`——带路径引用均已同步改指。

---

## 二、已实施并完成验收的内容（清单）

下列内容**已落地且经审核报告/本次独立核验确认**。原始记录分散在上表各文档中，此处汇总以防失联。

### 2.1 主实施方案 Phase 0–4（`agent_glue_plan_0903.md` §11.1）

| 步 | 内容 | 完成 | 验收结果（文档自称） |
|---|---|---|---|
| Phase 0-A2 | `call_type` 落库（4 调用点）+ 超时传参 + temperature 传参 | 09-03 | ✅ `get_llm_usage_summary(by_call_type)` 可查 4 类调用 |
| Phase 0-A2 附加 | F1-7 `llm_refinement_used` 去硬编码 + F1-8 `missing_fields` 字段级自检 | 09-03 | ✅ 自动检出 `lifecycle_unavailable` / `catalyst_events_empty` |
| Phase 0-A1 | GOT 回放 harness（`load_daily_contexts` + `--got-replay` + `replay_mode`） | 09-03 | ✅ 0903 回放逐字段一致 |
| Phase 1-A3 | 抽出 `_collect_sector_candidates` | 09-03 | ✅ 13 测试通过 |
| Phase 1-A4 | 实现 `_agent_semantic_glue` | 09-03 | ✅ 17 测试通过 |
| P0-B B1 | `_build_data_quality` 字段级检查 | 09-03 | ✅ |
| P0-B B2 | 护栏空转 WARNING 日志 | 09-03 | ✅ |
| Phase 2 §3 | `TencentFetcher`（腾讯指数 K 线 fallback） | 09-03 | ✅ 20 测试 |
| Phase 2 §4 | 指数注册表 + `parse_analysis_target()` 消歧 | 09-03 | ✅ 26 测试 |
| Phase 3a §15 | T1/T1-H 审查层 | 09-04 | ✅ 22 测试 |
| Phase 3c §14.1 | A1/A2 AgentMemory 修复 | 09-04 | ✅ |
| Phase 4a §6 | Intelligence 情报系统 | 09-04 | ✅ 17 测试 |
| Phase 4b §9 | `canonical_id` 双写 | 09-04 | ✅ 只写不读 |
| Phase 4c §5 Step 1 | MarketThemeContext 模型定义 | 09-04 | ✅ 接口预留 |

### 2.2 0911 四层的已落地项

| 层 | 项 |
|---|---|
| A | A-P0-2 / A-P0-3 / A-P0-4 / A-P0-8（**用了正解**：搬 glue + `_replay/` 进 tmp）；A-P0-1 三处绕过、A-P0-6 解包 bug、A-P0-7 DB 迁移 于第二轮修 |
| B | R-B2（集合相等）/ R-B5（形参已删，白名单活代码未误删）/ R-B6；R-B4 `航运概念`、`光刻机` 于第二轮修 |
| C | C-P1 / C-P2 / C-P4 / C-P10 |
| D | D-P6 / D-P7 / D-P8 / D-P14 |

### 2.3 本次（2026-09-14）独立核验确认的项

| 项 | 核验方式 |
|---|---|
| node4 新增列有 DB 迁移 | 实库 `PRAGMA table_info`：两列 PRESENT（`node4_events` 15 列 / `node4_raw_news` 12 列） |
| 4 个验收脚本纳入版本控制 | `.gitignore:76-84` 有 4 条负向例外；`git ls-files` 确认已跟踪 |
| `verify_glue_metrics.py` 能真算 | 实跑 `--date 20260910`：**R-C3 十锚点逐位一致 PASS** |
| 交易日检查恢复生效 | `data/got_daily/20260912` 归档存在且 **09-12 是周六**——该 bug 的现场证据 |
| GOT 相关测试 | 5 个测试文件实跑 **52 passed**（2.96s，Python 3.10.20） |
| 0914 两项修复 | commit `dc5bbde`：交易日检查 + Phase 2 整手归零可见化 |

### 2.4 更早的已落地项

| 来源 | 项 |
|---|---|
| `0827_got_diagnostic.md`（本目录） | P0-1 `attack_power` 分档连续化（`sector_analysis.py:3227-3236`）、P0-2 Phase 2 sector 分用实际强度、P0-3 选股当日涨幅软过滤（`stock_screener.py:1462-1480`） |
| `plan_got_timeseries_enhancement.md`（本目录） v2.1 | 跨日记忆系统 + 跨节点时间线共振（commit `03bb3d1`，2026-08-28） |

### 2.5 2026-09-14 新增：C-P6/C-P7 落地 + 首跑实测

| 项 | 状态 |
|---|---|
| **C-P6** M1 主口径改 glue 侧（`strengths[].news_read`） | ✅ 落地——`scripts/verify_glue_metrics.py` 主口径切换，规则侧降为对照列 |
| **C-P7** M6 主口径改「其他」回收率 | ✅ 落地——M6′ 覆盖率降为观察项 |
| **R-C3 回归** | ✅ 十锚点仍逐位一致，退出码不变（`1/2 PASS \| 0 FAIL \| 1 SKIP \| exit 2`） |
| **同日两跑首验**（R1/R3/R4） | ✅ 快照已产出（`data/_run1` / `_run2`，在 `.gitignore` 的 `/data/` 内）；**R4 PASS**，R1/R3 的 FAIL 经 **DB 级逐事件比对**归因为**数据差异、非幂等缺陷** |
| **三条影子线首跑** | A5 ✅ `status=ok`；T1 ✅ 跑通（`reject=1`）但产物被 Phase 2 路径丢弃；T1-H 开关生效但持仓未命中筛选条件 |

> ⚠️ 判定归负责人：M1 / M6 是否达标、A5 影子期样本是否计入，均不由本次实施代判。
> 实测细节与三障碍见 [`verification.md`](../verification.md) §1、§5；工器缺口见 [`backlog.md`](../backlog.md) §1.F。

### 2.6 2026-09-15 归档：7 项已验证完成

详见 [`verified_completion_log.md`](./verified_completion_log.md)。

| 项 | 验证结论 |
|---|---|
| **A-P0-4** monitoring 按日去重 | ✅ 已实测有效（归档侧 + 沙箱侧双证据） |
| **R-C1** S1/S2 失败样本落盘 | ✅ 补测通过（4/4） |
| **R-C3** 十锚点逐位一致 | ✅ PASS 10/10 |
| **R-C5** `_log_shadow_comparison` 零引用 | ✅ 复验通过 |
| **R2** 状态机 `previous_regime` | ✅ 已收口（测试补断言） |
| **R4** EMA 计数按日 | ✅ PASS |
| **M6** 「其他」回收率 | ✅ 90.9% ≥ 80% 达标 |

> 上述项已从 [`verification.md`](../verification.md) 移除。

---

## 三、归档时的注意事项

1. **代码与脚本仍按路径引用本目录**——引用已同步更新，见 `scripts/` 与 `src/got/` 中的注释与常量。
2. **节号保持原样**——`agent_glue_plan_0903.md §5.3` 这类引用在归档后**仍然有效**（指向本目录同名文件）。
3. **两条已被作者声明的"作废"**，引用前务必注意：
   - `got_impl_review_0911.md` §0–§10 的**红线达标总表已过期**（作者原话："记录的是修复轮之前的状态……勿单独引用"）；
   - `E3_fixes_summary.md` 的声明**与真实改动不符**（改 2 个文件 vs 实际 13 个 tracked + 1 个新模块）。
4. **本目录不再更新**。当前有效的待实施项见 [`backlog.md`](../backlog.md)，待验证/影子期项见 [`verification.md`](../verification.md)，待实施计划见 [`../plans/`](../plans/)（导航入口 [`README.md`](../README.md)）。
5. **2026-09-15 新增**：T1/T1-H 影子产物文件（`shadow_t1.json` / `shadow_t1h.json`），与 A5 的 `agent_glue.json` 对称，由 `portfolio_decision._save_shadow_records()` 落盘到 `data/got_daily/{date}/`，供 `archive._render_shadow_section()` 渲染到 `reasoning.md` 末尾。详见 [`verification.md`](../verification.md) §2 末尾。

---

*归档日期：2026-09-14*
