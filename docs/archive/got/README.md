# GOT 文档归档（2026-08 ~ 2026-09）

> 本目录存放 GOT 修复与规划线的**原始文档**，于 2026-09-14 从 `docs/` 根目录整体移入。
> **文件内容一字未改**——包括节号（如 `agent_glue_plan_0903.md` §5.3），因此代码与脚本中的节号引用**仍然可解析**，只是路径前缀变为 `docs/archive/got/`。
>
> **当前有效文档**（根目录）：
> - [`got_shadow_verification.md`](../../got_shadow_verification.md) —— 待验证 / 影子期
> - [`got_backlog.md`](../../got_backlog.md) —— 待实施

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
| `intraday_gate_plan_0907.md` | 2026-09-14 | Phase 6 盘中 0→1 确认闸门（草案未实施） | 待实施项已摘入 `got_backlog.md` §6 |
| `got_status_0914.md` | 2026-09-14 | 本批文档的状态盘点报告 | 其内容已重组进根目录两份新文档 |

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
| `custom/0827_got_diagnostic.md` | P0-1 `attack_power` 分档连续化（`sector_analysis.py:3227-3236`）、P0-2 Phase 2 sector 分用实际强度、P0-3 选股当日涨幅软过滤（`stock_screener.py:1462-1480`） |
| `custom/plan_got_timeseries_enhancement.md` v2.1 | 跨日记忆系统 + 跨节点时间线共振（commit `03bb3d1`，2026-08-28） |

### 2.5 2026-09-14 新增：C-P6/C-P7 落地 + 首跑实测

| 项 | 状态 |
|---|---|
| **C-P6** M1 主口径改 glue 侧（`strengths[].news_read`） | ✅ 落地——`scripts/verify_glue_metrics.py` 主口径切换，规则侧降为对照列 |
| **C-P7** M6 主口径改「其他」回收率 | ✅ 落地——M6′ 覆盖率降为观察项 |
| **R-C3 回归** | ✅ 十锚点仍逐位一致，退出码不变（`1/2 PASS \| 0 FAIL \| 1 SKIP \| exit 2`） |
| **同日两跑首验**（R1/R3/R4） | ✅ 快照已产出（`data/_run1` / `_run2`，在 `.gitignore` 的 `/data/` 内）；**R4 PASS**，R1/R3 的 FAIL 经 **DB 级逐事件比对**归因为**数据差异、非幂等缺陷** |
| **三条影子线首跑** | A5 ✅ `status=ok`；T1 ✅ 跑通（`reject=1`）但产物被 Phase 2 路径丢弃；T1-H 开关生效但持仓未命中筛选条件 |

> ⚠️ 判定归负责人：M1 / M6 是否达标、A5 影子期样本是否计入，均不由本次实施代判。
> 实测细节与三障碍见 [`got_shadow_verification.md`](../../got_shadow_verification.md) §1、§5；工器缺口见 [`got_backlog.md`](../../got_backlog.md) §1.F。

---

## 三、归档时的注意事项

1. **代码与脚本仍按路径引用本目录**——引用已同步更新，见 `scripts/` 与 `src/got/` 中的注释与常量。
2. **节号保持原样**——`agent_glue_plan_0903.md §5.3` 这类引用在归档后**仍然有效**（指向本目录同名文件）。
3. **两条已被作者声明的"作废"**，引用前务必注意：
   - `got_impl_review_0911.md` §0–§10 的**红线达标总表已过期**（作者原话："记录的是修复轮之前的状态……勿单独引用"）；
   - `E3_fixes_summary.md` 的声明**与真实改动不符**（改 2 个文件 vs 实际 13 个 tracked + 1 个新模块）。
4. **本目录不再更新**。当前有效的待验证项与待实施项分别见根目录两份新文档。

---

*归档日期：2026-09-14*
