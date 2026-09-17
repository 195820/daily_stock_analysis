# GOT 项目状态报告（截至 2026-09-14）
> **原位置**：`docs/archive/got/got_status_0914.md`（2026-09-17 文档结构重排移入当前位置）

> **报告性质**：只做**材料整理与事实核验**，不做达标判定。
> 尤其：影子期 **M1–M6 六条转正红线**的达标与否由项目负责人判定，本报告只搬运各文档的实测值与该值的出处。
>
> **核验基准**：本仓库工作区快照 + 分支 `fix/mootdx-hq-server-pool`（领先 `main` 7 个提交，未合并）。
> **代码侧结论**均由本次实跑/读码得出，标注为「已核验」；文档自称的结论标注为「文档自称」。
> **环境偏差**：实际 Python **3.10.20**，项目要求 3.12.13（`CLAUDE.md`）。此偏差为 `docs/got_impl_review_0911.md` §1 自行标注，本报告沿用它。

---

## 0. 一句话摘要

讨论对象是 2026-08-14 → 2026-09-14 期间新增的 **18 份 GOT 相关文档**。
核心事实：这条线上的一批**修复方案（0911 四层）已于 09-12 落库**，其中一部分经本次独立核验确认生效（DB 迁移、验收脚本入版本控制、红线脚本可复算）；
同时**上一轮审核自身留下的、以及更早的 0902/0903 问题仍大量挂起**；
而 `docs/` 的**索引层与三份活文档均已失效**。

---

## 1. 文档全景（按首次落库日）

| 日期 | 文档 | 大小 | 角色 |
|---|---|---|---|
| 8-26 | `docs/custom/cloud_deployment_plan.md` | 9K | 阿里云 2核2G 部署计划（与本轮 GOT 修复无关） |
| 8-27 | `docs/custom/0827_got_diagnostic.md` | 15K | 0827 全流程诊断（5 个问题 + 4 条系统级归纳） |
| 8-27 | `docs/custom/plan_got_timeseries_enhancement.md` | 49K | 时序增强计划 v2.1 |
| 8-27 | `docs/custom/got_timeseries_calibration.md` | 4K | 上述计划的试点报告（≤27 天样本） |
| 9-02 | `docs/GOT_0902_audit_report.md` | 30K | 全链路审核 15 项 |
| 9-03 | `docs/grilling_0903_solutions.md` | 49K | 从 0903 输出反推的 8 个问题 |
| 9-03 | `docs/remote_integration_plan.md` | 41K | 远端模块整合明细 |
| 9-03 | `docs/agent_glue_plan_0903.md` | 135K | **当前主文档**（9-11、9-12 又各修订一次） |
| 9-04 | `docs/AGENTS.md` | 17K | AI 协作规则 |
| 9-07 | `docs/intraday_gate_plan_0907.md` | 22K | Phase 6 盘中闸门（草案，9-14 又修改过） |
| 9-11 | `docs/got_run_model_plan_0911.md`（A 层） | 36K | 运行模型 / 同日重跑幂等 |
| 9-11 | `docs/got_layer_b_plan_0911.md`（B 层） | 48K | 同一概念多处计算与同名两义 |
| 9-11 | `docs/got_layer_c_plan_0911.md`（C 层） | 56K | 影子验收设计的可度量性 |
| 9-11 | `docs/got_layer_d_plan_0911.md`（D 层） | 58K | 与 0902 报告对账 |
| 9-11 | `docs/E3_fixes_summary.md` | 8K | 实施总结 |
| 9-12 | `docs/got_impl_review_0911.md` | 47K | 实施审核 + 第二轮复审 |
| 9-12 | `docs/got_revision_ledger_0911.md` | 29K | 修订台账（4 轮复核 + 自曝） |
| 9-14 | `docs/CHANGELOG.md` | 248K | 活文档，持续追加 |

**结构观察**：A/B/C/D 四份是同构模板——共享完全相同的骨架（§0 结论摘要 / §1 触发 / §2 事实核查 / §3 根因 / §4 已确认决策 / §5 修复清单 / §6 验收红线 / §7 范围外与挂起项 / 附录 A 实测基线 / 附录 B 事实核查约定），合计约 198KB。

---

## 2. 四层方案规模速览（文档自称）

| 层 | 触发实测 | 根因 | 修复项 | 验收红线 | 挂起项 |
|---|---|---|---|---|---|
| **A** 运行模型 | 0910 一天 **4 次启动 / 2 次归档**；`regime` 被"修正"为 `transition`，11 个板块敞口上限 0.28→0.182 | 无"一日一跑"定义 + 归档列表不排除当天 | 9 项（A-P0-1…A-P0-9） | 6 条（R1–R6） | OPEN-1…4 |
| **B** 概念一致性 | 同日同一份输出 **5 组「一名两值」** | 结论/证据不分界 + 派生无命名隔离 + 量纲丢失 | 6 小节（B1/B2/B3/B4/B6/B7） | 9 条（R-B1…R-B9） | OPEN-B1…B7 |
| **C** 影子验收 | 六条转正红线**首次实测 0/6** | 验收指标未经"能否被算出"检验 | 13 项（C-P1…C-P13） | 7 条（R-C1…R-C7） | OPEN-C1…C5 |
| **D** 0902 对账 | 0902 报告 15 项首次对账：**未修 10 / 生效 3 / 症状消失 1 / 不修 1** | 「自述 ≠ 验收」在 4 个载体上重演 | 14 项（D-P1…D-P14） | 2 条 + 4 子计数 | OPEN-D1…D9 |

**四份内部自标的计数不一致**（材料，非判定）：

- B 层 §0 称"另有**两条**属于…"，括号内只列出 1 项（B3）。
- B 层自称"重排为 B1–B7 连续编号"，实际 6 个小节，**全文无 B5**。
- C 层头部称 M1/M6 为"结构上不可动"，而 §2.9 第 1 条把同一表述判为"表述不准且低估"——两处措辞方向相反。
- D 层 §6 称子计数"**两个**"，实际列出 **4 行**。

---

## 3. 各主线「主张 vs 现状」三态

### 3.1 已落地 —— 本次独立核验确认

| 主张 | 出处 | 核验方式与结果 |
|---|---|---|
| GOT 回放 harness（`--got-replay`） | 主文档 §11.1 | 已核验：`data/got_daily/*/_replay/` 存在真实产物 |
| glue 语义粘合层 + F1 八行降级契约 | 主文档 §11.1 | 已核验：实跑产出 `agent_glue.json`，`degraded=false`、`status=ok` |
| node4 新增列有 DB 迁移 | 审核报告 P0-2（第一轮判 FAIL） | 已核验：实库 `PRAGMA table_info` 两列 **PRESENT**；`node4_events` 15 列、`node4_raw_news` 12 列 |
| 4 个验收脚本纳入版本控制 | 台账 §R2.12 | 已核验：`.gitignore:76-84` 有 `verify_*.py` 规则 + 4 条负向例外与说明注释；`git ls-files` 确认 4 个已跟踪 |
| `verify_glue_metrics.py` 能真算 | 审核报告 §R2.10 | 已核验：实跑 `--date 20260910`，**R-C3 十个锚点逐位一致 PASS**；R-C6 SKIP（回填样本仅 2 天） |
| 交易日检查恢复生效 | 0914 commit `dc5bbde` | 已核验：`data/got_daily/20260912` 归档存在且 **2026-09-12 是周六**——即该 bug 的现场证据 |
| GOT 相关测试 | 台账 §R2.13 #4 | 已核验：5 个 GOT 测试文件实跑 **52 passed**（2.96s） |

> 关于验收脚本的口径诚实性：`verify_glue_metrics.py` 在 Spearman 相关系数上**同时打印** `method="first"` 与 `method="average"` 两个值，并声明"文档锚点只在 first 口径下复现……两值均列出，未静默取舍"。文档 §R2.10 记录了这个口径陷阱。

### 3.2 未落地 / 仍挂起 —— 文档自承

- **D 层对账结论**：0902 报告 15 项中 **10 项未修**；标为"立即 / 1 周内"的 6 项只完成 1 项（P0-3）。
- **C 层**：六条转正红线实测 **0/6**——2 条门禁自锁（M1/M6）+ 2 条不达标（M2、M4-a）+ 1 条不可度量（M5）+ 1 条未测（M3）。其中 `transition_count` 带一个**约 2026-09-25 的时间炸弹**（OPEN-C1）。
- **B 层**：`R-B4` 症状**恶化**——`航运概念` 从"误归军工"变成掉进「其他」，`光刻机` 同落「其他」；受控词表里不存在「设备」族，故文档自身的期望不可实现。
- **主文档 §11.2** 三项进行中/待办：A5 影子期运营、T1/T1-H 影子期运营、P0-B B3（`crowded_families` 处置，需与 Node 2 协商）。
- **审核报告 §R2.13** 仍待决策 3 项：`sector_role_map` fallback 方向（放大风险）、`GlueStrength.basis` 无生产者、`A-P0-4` 效果缺触发样本。
- **`remote_integration_plan.md`** 未被主文档完整吸收：主文档 §10 只并入了**路线图**，remote §3–§10 的模块明细（Tencent fallback / 指数注册表 / MarketThemeContext / Intelligence / AlphaSift / 分歧摘要 / canonical_id / TickFlow）仍只存在于 remote。其中 §14.1 A3、§14.2 B4–B8 在 0903 主文档的进度表里**无任何落点**。
- **Phase 6 盘中闸门**：`intraday_gate_plan_0907.md` 自述"草案（未实施）"，其 Phase 6-0（盘中数据地基：五档量 / 分时 / 竞价）被自身列为硬前置。

### 3.3 已被后续文档推翻 / 升格 / 降级

| 原主张 | 后续判定 | 出处 |
|---|---|---|
| 「M1/M6 结构上不可改善」 | 实为**转正门禁自锁**（红线写在"转正后才会变的字段"上） | C 层 §2.9 撤销 #1 |
| 「六条转正红线 2/6 达标」 | 实为 **0/6**；该错误断言**同源传播到 A 层、B 层各一份** | 台账 §2 |
| 「埋伏闸门只有 2 层活着」 | 过度断言，实为 **2 个半**（G1 活 / G2 半 / G3 结构性死 / G4 活）；该断言传播 **9 处、跨 3 份文档**，其中一处落在决策行 D-D5 | 台账 §2、§4.1 |
| 「埋伏分支数学上不可达」 | 根因是解析 bug 而非结构性质；**修好解析后可达** ⇒ 升格为行为变更项 | 台账 §1 #13 |
| `E3_fixes_summary.md`：「修改文件：`schemas.py` + `portfolio_decision.py`」 | 实际 **13 个 tracked 文件 + 1 个新模块**，+730/−330 | 审核报告 §7 #1 |
| `E3_fixes_summary.md`：「结论：逻辑正确，ambush 分支可达」 | 该节推导混用理论值 0.40 与实测值 0.182（两者不同源），**结论碰巧对、推导错** | 审核报告 §7 #3 |
| R2 红线「出现 `previous_regime != regime` 的归档日 ≥1 天」 | **该红线本身无效**——把市场事件当代码验收条件；37 个归档日零切换无法区分"市场没切"与"情形不可达" | 审核报告 §R2.11 |
| 「4 个验收脚本已交付」 | 首轮 4/4 **掉包了红线定义**（打印 `[PASS]` 但验证的不是自己名字所指的那条） | 审核报告 §R2.3 |
| R-B2 达标线、R-B5 作用域 | 原写法分别是空红线 / 会诱导误删活代码，**均已修订** | B 层 §6、台账 §1 #1 #2 |
| R-C3 锚点「M1=34.4% / M1′=3.1%」 | 与 §1 表**恰好互换**，照它校验只会得到假失败，**已重写** | C 层 §6.1 |
| 「R-C4 连续 7 天」 | 把 `_replay/` 回填样本与实盘样本混为一条计数，**已改为强制分列来源** | C 层 §6.1 |
| `custom/plan_got_timeseries_enhancement.md` 头部状态「待评审」 | 该文档与其实现**同批落库**（commit `03bb3d1`，2026-08-28，提交信息即 "feat: v2.1 跨日记忆系统 + 跨节点时间线共振"），但文档头部状态字段此后未更新 | 状态字段过期 |

---

## 4. 影子期实际进度（可数事实）

`data/got_daily/*/agent_glue.json` 逐日盘点：

| 归档日 | 星期 | 产物 | 性质 |
|---|---|---|---|
| 20260904 | 五 | 8.3 KB | 真实 glue 输出 |
| 20260907 | 一 | 9.5 KB | 真实 |
| **20260908** | **二** | **无文件** | **缺口**：交易日，6 节点归档齐全，但无 glue 产物 |
| 20260909 | 三 | 17.1 KB | 真实 |
| 20260910 | 四 | 19.0 KB | 真实 |
| 20260911 | 五 | 1.1 KB | **F1 降级占位**（`news_read=unknown`、`evidence="F1 降级:纯技术分"`、`confidence=0.3`） |
| 20260912 | **六** | 1.1 KB | **F1 降级 + 非交易日**（周六） |
| 20260914 | 一 | 16.7 KB | 真实；较前几日新增 `status` 字段（C-P1 落地后） |

**真实 LLM 样本 = 5 天**（0904 / 0907 / 0909 / 0910 / 0914）。

转正条件（主文档 §8.2）要求影子期 **≥10 个交易日** 且双达标。
此外 §8.2 另记「一票否决：任一日 glue 成功但 `classification` 全量翻转」与"回滚路径"两条。
**本报告不判定是否达标**，仅提供上述计数与其来源。

> ⚠️ §8.1 / §8.2 的转正条件本身带一处已知结构问题：M1/M6 的**基线取自规则侧字段、红线也写在同一个规则侧字段上**，而影子期 glue「接受但不使用」，该字段不因 glue 而变 ⇒ 两条红线**结构性不可达成**（C 层 §0 称之为"门禁自锁"）。这一点决定了"≥10 日 + 双达标"这条路径当前不可走通，除非先落 C 层修正。

---

## 5. 文档层结构问题

以下均为事实陈述，未对任何文件做改动。

1. **索引层失效**：`docs/INDEX.md`（mtime 2026-06-13）收录 **22 个 `.md` 条目**（另有 3 个非 md 链接：`bot/` 目录、`architecture/api_spec.json`、`examples/litellm_config.example.yaml`）。未被收录的有：
   - `docs/` 根目录下 **12 份 GOT 文档，一份都没有**；
   - `docs/custom/` **整个目录**未收录；
   - 另有 6 份早于 INDEX.md 的文件（`run-diagnostics-p0~p3.md`、`settings-help.md`、`alphasift-integration.md`）属**创建时即缺失**；
   - `docs/AGENTS.md`、`docs/CLAUDE.md` 两个治理文档亦未收录。
2. **死链**：仓库根目录 `AGENTS.md` **不存在**，但 `CLAUDE.md:7` 仍写「→ 参见 `AGENTS.md`（CLAUDE.md 为其镜像）」。实际规则文件是 `docs/AGENTS.md`（298 行，含 `### GOT 运行规范` 一节）。
3. **`docs/CLAUDE.md`（2026-06-25）**：**零处提及 GOT**（`grep -c GOT` = 0），内容停在选股系统 Phase 5 改造，与 8-9 月的 GOT 架构演进完全脱节。
4. **`CHANGELOG.md` 的 `[Unreleased]` 段**（第 10–61 行，47 条）：
   - **19 条本地 GOT 工作** + **28 条上游开源项目条目**混在同一段，无分类标题。
   - 另有 `## GOT 架构实施记录（从 CLAUDE.md 归档）` 一节位于 1.0.0 版本之后（后段）。
5. **`E3_fixes_summary.md` 仍与四层方案并列**在 `docs/` 根目录，尽管审核报告已判定其"声明的对照物本身不存在"。
6. **`custom/plan_got_timeseries_enhancement.md` 的状态字段过期**（自称"待评审"，实际已实施）。

---

## 6. 仓库其他事实

| 项 | 事实 |
|---|---|
| 分支 | `fix/mootdx-hq-server-pool`，领先 `main` **7 个提交**，未合并 |
| 工作区 | 仅 1 个未跟踪文件：`scripts/export_glue_family_audit.py` |
| 该脚本性质 | M3 家族误配率**人工抽查**工具；自述"只搬运事实，不做判定，判定列留空给审核者"——与「M 指标判定归负责人」的口径一致 |
| GOT 测试 | 5 个测试文件实跑 **52 passed**（2.96s） |
| 环境 | 实际 Python **3.10.20**，项目要求 3.12.13 |
| 仍被 gitignore 的验收脚本 | `verify_flow_archive.py`、`verify_position_sizing.py`、`verify_remote_data_sources.py`、`verify_risk_fix.py`、`verify_t5_alignment.py`、`verify_ths_*.py` —— 均在 `.gitignore:76` 的 `verify_*.py` 规则下，未被 4 条负向例外覆盖 |

---

## 7. 挂起项汇总（供排期，非建议）

| 来源 | 挂起项 | 文档给出的阻塞 |
|---|---|---|
| 主文档 §8.2 | A5 影子期（≥10 交易日 + 双达标） | 无（代码就绪）；但 M1/M6 门禁自锁使其不可走通 |
| 主文档 §9 | OPEN-1 仓位链路 / OPEN-2 情绪面只到展示层 / OPEN-3 exhausted 判定 / OPEN-4 `INDEX_CONFIG` 权重配对 / OPEN-5 数据完整性 | 部分与 A6 转正互锁 |
| C 层 §7.2 | OPEN-C1 `transition_count` 时间炸弹（约 2026-09-25） | 需 A 层落地后重算倒计时 |
| C 层 §7.2 | OPEN-C2 M3 人工流程未定义；OPEN-C3 `persistence_regime` 恒真 | 与 OPEN-C1 同批排查 |
| D 层 §7.2 | OPEN-D1…D9（含 `AMBUSH_SCORE_FLOOR` 来源、正式池水位目标、P1-1 新归属） | — |
| 审核报告 §R2.13 | 3 项待决策（`sector_role_map` fallback / `GlueStrength.basis` 无生产者 / `A-P0-4` 缺样本） | 需负责人裁决 |
| 审核报告 §R2.11 | R5 有 7 组 `signal_dates` 重复，但**全在 09-10 之前**，属历史遗留；A-P0-4 的实际效果本次未验证 | 需等下一次 `range_reversal` 触发 |
| 盘中层 | `intraday_gate_plan_0907.md` Phase 6-0 数据地基 | 五档量 / 分时 / 竞价接口 |
| 部署层 | `custom/cloud_deployment_plan.md` 里程碑全部"待执行" | — |

---

## 附录：核验方法与复现命令

本报告中所有「已核验」结论的复现方式（均只读）：

```bash
# 1. node4 迁移是否真在库里
python -c "import sqlite3;c=sqlite3.connect('data/stock_analysis.db');\
print([r[1] for r in c.execute('PRAGMA table_info(node4_events)')])"

# 2. 验收脚本是否入版本控制
git ls-files scripts/ | grep verify
git check-ignore -v --no-index scripts/verify_glue_metrics.py

# 3. 红线脚本能否真算（R-C3 十锚点）
python scripts/verify_glue_metrics.py --date 20260910

# 4. glue 影子产物逐日盘点
for d in $(ls data/got_daily/ | grep -E '^2026' | grep -v pre_fix); do
  f="data/got_daily/$d/agent_glue.json"
  [ -f "$f" ] && echo "$d $(stat -c%s "$f")" || echo "$d ---"
done

# 5. GOT 相关测试
python -m pytest tests/test_node6_glue.py tests/test_node6_candidates.py \
  tests/test_node6_regime_state.py tests/test_got_orchestrator_trading_day.py \
  tests/test_portfolio_decision_lot_rejection.py -q

# 6. INDEX.md 覆盖率
for f in docs/*.md; do b=$(basename "$f"); grep -q "($b)" docs/INDEX.md || echo "未收录: $b"; done
```

**未做**：归档回放（A/B/C/D 的"零行为变更"红线本应由回放证明，但该回放需完整跑批）；`verify_same_day_idempotency.py` / `verify_layer_b_consistency.py` / `check_audit_claims.py` 的实跑（它们的结论见 §R2.10 / §R2.11，本报告只核验了存在性与其中一条 R-C3 的复算）。

---

*生成日期：2026-09-14*
