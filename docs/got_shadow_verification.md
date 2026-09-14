# GOT 待验证 / 影子期清单

> **性质**：本清单只登记**待验证项、其判据、实测值与出处**，**不落达标判定**。
> 影子期 M1–M6 等转正红线的**合格与否由项目负责人判定**；本清单的作用是让判定所需的事实可查、可复算。
>
> **基准**：2026-09-14 工作区快照，分支 `fix/mootdx-hq-server-pool`。
> **原文档已归档**至 [`archive/got/`](archive/got/)，本清单所有引用均指向该目录。
> **环境偏差**：实测 Python 3.10.20，项目要求 3.12.13 —— 本清单中所有实跑结论受此影响。
>
> **配套文档**：[`got_backlog.md`](got_backlog.md)（待实施）、[`archive/got/README.md`](archive/got/README.md)（归档索引与已完成清单）。

---

## 0. 收录范围（2026-09-14 明确）

**本清单只收「改动已落地、缺的是证据」的项** —— 具体是这五类证据：

| 缺的证据 | 对应项 |
|---|---|
| 影子期样本天数（≥10 交易日） | A5 glue |
| 修复前后两组归档的回放对 | R-B3 / R-B8 / R-B9 / R-C7 |
| 同日两跑的两个快照 | R1 / R3 / R4 / R6 |
| 人工抽查 | M3（误配率） |
| 时间窗 | OPEN-C1（`transition_count` 时间炸弹）、Phase 5b 显著性校准 |

**不收的四类**，一律见 [`got_backlog.md`](got_backlog.md)：

| 不收的类别 | 去哪 |
|---|---|
| 代码还没写 | backlog §1 / §2 |
| 已决定不修 / 待退役 / 改归属 / 挂起 | backlog §2（处置表） |
| 红线本身设计有缺陷 | backlog §9 |
| 文档基线过期 / 数字对不上 | backlog §8 |

> ⚠️ **本节是修订产物**：`R-D1` / `R-D2` / `R2` / `R5` 四条**曾被误列在本清单**，现已移出。
> 误列原因：它们看起来也是「红线验收状态」，但读数**完全由未实施工作或文档缺陷决定**，不是验收红线。
> 判定标准（新增）：**一条验收红线只能量已实施的东西**；若它的读数由未实施工作决定，它是 burndown chart 而非验收红线。
> 移出后的处置见 [`got_backlog.md`](got_backlog.md) §2 / §8 / §9。

---

## 1. 影子期总览：三条影子线的可数性差异

| 影子线 | 开关 | 归档产物 | 当前可数性 |
|---|---|---|---|
| **A5 glue 语义粘合层** | `AGENT_SEMANTIC_GLUE_ENABLED`（默认 `false`）+ `..._SHADOW`（默认 `true`） | `data/got_daily/{date}/agent_glue.json` | ✅ **可数**（逐日产物，见 §2） |
| **T1 候选股终审** | `AGENT_STOCK_REVIEW_ENABLED`（默认 `false`）+ `..._SHADOW`（默认 `true`） | `data/got_screener/{date}.json`（`_save_got_result`，`src/services/stock_screener.py:2784`） | 🔴 **无归档证据**——`data/got_screener/` 最新一份是 **2026-08-25**，早于 T1 落地（0904） |
| **T1-H 持仓审查** | `AGENT_HOLDING_REVIEW_ENABLED`（默认 `false`）+ `..._SHADOW`（默认 `true`） | 无独立产物；结论写回 `portfolio_decision` 流程 | 🔴 **无归档证据**——`portfolio_decision.json` 中无 `agent_decision_signal` / `holding_review` / `shadow` 标记 |

**这三条线的差异本身是事实**：A5 有逐日归档产物可复算，T1/T1-H 目前只有运行期日志行（`[SHADOW][T1终审]` / `[SHADOW][T1-H]`），**没有可离线复核的产物**。

---

## 2. A5 glue 影子期：逐日产物盘点

`data/got_daily/*/agent_glue.json` 全量清点（2026-09-14 实测）：

| 归档日 | 星期 | 大小 | 性质 |
|---|---|---|---|
| 20260904 | 五 | 8,299 B | 真实 LLM 输出（`relations` 6 / `strengths` 19） |
| 20260907 | 一 | 9,501 B | 真实 |
| **20260908** | **二** | **无文件** | **缺口**：交易日，6 节点归档齐全，但无 glue 产物 |
| 20260909 | 三 | 17,099 B | 真实（`relations` 22 / `strengths` 32） |
| 20260910 | 四 | 18,976 B | 真实 |
| 20260911 | 五 | 1,106 B | **F1 降级占位**：`evidence="F1 降级:纯技术分"`、`news_read/capital_read/sentiment_read = unknown`、`confidence=0.3`、`relations=[]` |
| 20260912 | **六** | 1,106 B | **F1 降级 + 非交易日**（周六） |
| 20260914 | 一 | 16,702 B | 真实（`relations` 26 / `strengths` 23），新增 `status` 字段 = `ok` |

### 有效样本计数

**真实 LLM 样本 = 5 天**：`20260904` / `20260907` / `20260909` / `20260910` / `20260914`。

（0911、0912 为 F1 降级占位，不计入；0908 无产物。）

转正条件（`archive/got/agent_glue_plan_0903.md` §8.2）要求影子期 **≥10 个交易日**且双达标。
**是否达标由负责人判定**，本清单只登记上述计数。

### 已知的两处干扰

1. **0912 是周六**——归档存在本身是 0914 commit `dc5bbde` 修掉的"非交易日照跑"bug 的现场证据（`_check_trading_day` 的 `import TradingCalendar` 恒抛 `ImportError` 且被 `except ImportError` 静默吞掉）。修复后非交易日不再运行。
2. **0911 / 0912 的 glue 是降级态**——`[SHADOW]` 日志行与归档产物不一致时，**以产物为准**：1106 B 是 F1 降级的形状。

---

## 3. M1–M6 转正红线：定义、阈值、实测值

> 出处：`archive/got/agent_glue_plan_0903.md` §8.1 / §8.2；实测值与口径修正见 `archive/got/got_layer_c_plan_0911.md` §0 / §1。
> **本节只抄录，不判定。**

| # | 指标定义 | 转正红线 | 实测值（0910 + 四日） | 文档给出的状态描述 |
|---|---|---|---|---|
| **M1** | 规则侧 `unified.confirmed_sectors[].news_check != "neutral"` | ≥ 60% | **1/32 = 3.1%** | "门禁自锁" |
| **M1′** | glue 侧 `strengths[].news_read` 非 `low`/`unknown` | — | **11/32 = 34.4%** | 实测 < 红线 |
| **M2** | ≥1 条 `relations[]` 的候选占比 | ≥ 70% | **9/32 = 28.1%**（四日 21.1% / 13.6% / 21.9% / 28.1%） | 4/4 不达标 |
| **M3** | 人工抽查误配率 | ≤ 15% | 未测 | 依赖人工抽查 |
| **M4-a** | `ρ(glue.strength, tech_score)` | ≥ 0.6 | **0.591 P / 0.419 S** | 不达标且 n=32 |
| **M4-b** | `ρ(glue.strength, 旧 composite)`（反向指标） | < 1.0 | 0.646 P / 0.500 S | 达 |
| **M4-c** | `ρ(glue.strength, 旧 composite)`（下限） | ≥ 0.3 | 0.646 P / 0.500 S | 达 |
| **M5** | 触发 S1–S5 任一降级场景的交易日占比 | ≤ 10% | 不可度量 | 依赖 `call_type` 落库 |
| **M6** | 规则侧 `frame_group != "其他"` | ≥ 80% | **21/32 = 65.6%** | "门禁自锁" |
| **M6′** | glue 侧 `families[].family` 非「无归属」 | — | **31/32 = 96.9%** | — |
| （C-P7 改口径） | 「其他」回收率 | — | **10/11 = 90.9%** | — |

### 关键结构问题：M1 / M6 的"门禁自锁"

文档自述（`got_layer_c_plan_0911.md` §0）：M1 / M6 的**基线取自规则侧字段、红线也写在同一个规则侧字段上**，而影子期 glue「接受但不使用」该字段（`src/got/nodes/aggregator.py:1119`），规则侧字段不因 glue 而变 ⇒ **M1 恒 3.1%、M6 恒 65.6%，两条红线结构性不可达成**。

C 层给出的修正方向是 **C-P6（M1 改 glue 侧）、C-P7（M6 改为「其他」回收率）**——两项**尚未落地**，见 [`got_backlog.md`](got_backlog.md) §1.C。

### M4 的口径陷阱（已知且已被脚本显式处理）

文档锚点 `M4-a = 0.419S` / `M4-b/c = 0.500S` **只在 `rank(method="first")` 下复现**；统计学标准平均秩给出 **0.478 / 0.564**，差 0.06–0.14。
`scripts/verify_glue_metrics.py` **同时打印两个值并声明依赖输入顺序**，未静默取舍（`archive/got/got_impl_review_0911.md` §R2.10）。

---

## 4. C 层验收红线 R-C1…R-C7 的当前状态

> 出处：`archive/got/got_layer_c_plan_0911.md` §6.1。

| # | 判据 | 达标线 | 当前状态（材料） |
|---|---|---|---|
| **R-C1** | 制造 S1 / S2 各一次后 `agent_glue.json` **仍存在**且 `status` 正确 | 2/2 存在 | 待复测 |
| **R-C2** | `--got-replay 20260901` 的 glue 产物落在 `20260901/_replay/` | 路径精确匹配 | 已有 `_replay/` 产物（0911 / 0912），未逐日核对 |
| **R-C3** | `verify_glue_metrics.py --date 20260910` 十锚点逐位一致 | 全部相等 | ✅ **本次实跑 PASS 10/10** |
| **R-C4** | `status=ok` 的交易日 ≥ 7 且连续，**须分列实盘 / 回填来源** | 7 天 | 实盘仅 **5 天**且缺 0908、不连续（见 §2） |
| **R-C5** | `_log_shadow_comparison` 在 `src/` 与 `tests/` 零引用 | 0 处 | 已按 C-P4 删除，待复验 |
| **R-C6** | M2 × M3 在 8 天回填样本上的 A/B 对照表 | 2×8 = 16 点 | 🔴 **SKIP**——回填样本仅 **2 天**（`data/got_daily/*/_replay/agent_glue.json`），两版 prompt 未跑 |
| **R-C7** | C 层零行为变更项落地后同期归档逐字段不变 | 100% 一致 | 待回放 |

> R-C3 的实跑命令见 §8。R-C6 是 `verify_glue_metrics.py` 内**唯一被判为 SKIP** 的一条（该脚本只含 R-C3 / R-C6 两条），前置为 C-P11（回填跑批）+ C-P12（prompt A/B），两项均未实施。

---

## 5. 已实施但缺「两跑 / 回放」证据的红线

0911 四层方案有一类红线**依赖「同一批交易日各跑一遍」产生的两组产物**（归档回放对，或同日两跑快照），
本次**未产出**，故其达标状态无法由静态检查得出：

| 红线 | 出处 | 判据 | 缺什么 | 当前状态 |
|---|---|---|---|---|
| **R-B3** | `got_layer_b_plan_0911.md` §6 | 家族降权影响面有界（无单日增幅 > 100%） | 修复前后两组多日归档 | 脚本 SKIP（`--replay-before/--replay-after` 未提供） |
| **R-B8** | 同上 | B1/B3/B4（零行为变更项）前后 `final_position_limit` / `composite_score` / `confirmed_sectors` 逐字段一致 | 同期单日归档对 | 脚本 SKIP（`--archive-before/--archive-after` 未提供） |
| **R-B9** | 同上 | B2/B6（行为变更项）的 `classification` 与 `dimension_scores["sector"]` 变化逐条列出 | 同上 | 同上 |
| **R-C7** | `got_layer_c_plan_0911.md` §6 | C 层零行为变更项落地后归档逐字段不变 | 修复前后两组归档 | 待回放 |
| **R1 / R3 / R4** | `got_run_model_plan_0911.md` §6 | 同日两跑状态一致性 / 事件计数不翻倍 / EMA 计数按日 | 两个同日快照目录 | 无参时 **SKIP** |
| **R6** | 同上 | 15 个状态源（W1–W6 + D1–D9）逐项幂等复测 | 每源两跑前后各一次观测点 | **SKIP** |

> ⚠️ **R1/R3/R4 的历史记录不可复现**：`archive/got/got_impl_review_0911.md` §505 记「`--runs` 冒烟 **PASS**（比对 474 字段、差异 0）」，
> 但该次比对所用的 `data/_run1` / `data/_run2` 快照**已不存在**，本次无法复算。按「不能算就不落判定」原则，此处状态记为 **SKIP**，不沿用该历史结论。
>
> ⚠️ **归档输入禁区**：`data/got_daily/` 下有 `20260805_backup/` 与 `20260817_pre_fix/` 两个 **8 月调试快照**，各自配一个同日期正式目录。
> 它们与 B 层 / C 层修复**无关**。脚本的 `--archive-before/--archive-after` 会照单全收并给出裁决 —— 实测喂 0817 那对得
> `[FAIL] R-B8 112 个应不变字段发生变化`，**这 112 项差异全部来自 8 月那次调试**。切勿作为 R-B8/R-B9 的输入。

---

## 6. A-P0-4（monitoring 按日去重）—— 已实测有效

**原状态**：`archive/got/got_impl_review_0911.md` §R2.13 #3 记为「缺 09-11 之后的 `range_reversal` 触发样本，需等下一次触发」。

**现状态：样本已出现，判定有效。** 两条独立证据：

### 6.1 归档侧：09-14 至少跑过 2 次，而当日 `signal_dates` 只有 1 条

`node4_events` 中存在这样一行（`data/stock_analysis.db`）：

| 字段 | 值 | 含义 |
|---|---|---|
| `first_seen` | `2026-09-14` | 该事件只在 09-14 出现过 |
| `mention_count` | `1` | **按日去重**口径 ⇒ 只增了 1 次 ⇒ 只跨了 1 个自然日 |
| `mention_count_run_based` | `2` | **旧口径**（每次运行 +1，`storage.py:1022`）⇒ 被 2 次运行各计入 1 次 |

两者唯一自洽的解释：**09-14 至少运行了 2 次**。而 `monitoring.json` 的 `cross_node.signal_dates` 中
`range_reversal @ 2026-09-14` 恰好 **1 条**。

（旁证：`node4_raw_news` 同日有 5 条事件 `appearance_count_run_based = 3`。）

### 6.2 沙箱侧：`save_monitoring` 连调 3 次，今日条目恒为 1

把 `monitoring.json` 复制到临时目录，猴补丁 `_MONITORING_FILE` 后连续调用三次（**不触碰生产数据**）：

```
初始             : 总条数=36  今日(2026-09-14) range_reversal=1
第1次 save_monitoring: 总条数=36  今日 range_reversal=1
第2次 save_monitoring: 总条数=36  今日 range_reversal=1
第3次 save_monitoring: 总条数=36  今日 range_reversal=1
```

机制：`persistence.py:704-707` 在追加前剔除 `date == today_str` 的既有条目，再按当日计数追加 ⇒ 跨次运行幂等。

### 6.3 残留限制

本项只保证**跨次运行**不重复；**单次运行内**若某类信号出现 ≥2 次，仍会落 ≥2 条同 `(type, date)` 条目
（`persistence.py:709-711` 的 `for _ in range(count)`，`signal_count_30d` 依赖该计数语义）。
该口径冲突已登记在 [`got_backlog.md`](got_backlog.md) §9。

> **本节原含 6.2 / 6.3 / 6.4 三小节（R5 / R-D1 / R2），已移出至** [`got_backlog.md`](got_backlog.md) §2 / §8 / §9。

---

## 7. 需人工或等时间窗的项目

| 项 | 类型 | 说明 |
|---|---|---|
| **M3** 误配率人工抽查 | 人工 | 判据 ≤15%，样本为每日候选全量（≤10 分钟/日）。工具已备：`scripts/export_glue_family_audit.py`（**未纳入版本控制**），自述"只搬运事实，不做判定，判定列留空给审核者" |
| **OPEN-C2** M3 人工流程未定义 | 人工 | 抽查怎么抽 / 判据 / 谁判，文档建议分层抽 10 个、三档判据 |
| **OPEN-C1** `transition_count` 时间炸弹 | 时间窗 | 约 **2026-09-25** 前后，0813–0826 的 10 个原始日将滚出 20 日窗口，`transition_count` 落到 ≈4–6，横躺阈值 5。**A 层落地后须重算倒计时** |
| **A5 影子期** | 时间窗 | 需 ≥10 个交易日（当前有效样本 5 天，见 §2） |
| **Phase 5b 显著性校准** | 时间窗 | 需归档 ≥60 交易日（`custom/got_timeseries_calibration.md` 记为预计 2026-11 下旬） |

---

## 8. 复现命令

```bash
# A5 影子产物逐日盘点（§2）
for d in $(ls data/got_daily/ | grep -E '^2026' | grep -v pre_fix); do
  f="data/got_daily/$d/agent_glue.json"
  [ -f "$f" ] && echo "$d $(stat -c%s "$f")" || echo "$d ---"
done

# M1–M6 与 R-C3 十锚点复算（§3 / §4）
python scripts/verify_glue_metrics.py --date 20260910

# R1/R3/R4/R5/R6 同日两跑 diff（§5）—— --runs 需要【两个】快照目录
# 快照怎么产出见脚本 SKIP 提示里的 ①②③；只传字面量 --runs 会直接 argparse 报错
python scripts/verify_same_day_idempotency.py --runs data/_run1 data/_run2

# 无快照时跑全量（R2/R5 可离线真算，其余 SKIP）
python scripts/verify_same_day_idempotency.py

# A-P0-4 沙箱幂等实测（§6.2）—— 复制 monitoring.json 到临时目录后连调 save_monitoring
# （不触碰 data/got_timeseries/monitoring.json；完整片段见 §6.2 说明）

# T1/T1-H 归档证据检查（§1）
ls -la data/got_screener/ | tail
```

---

*生成日期：2026-09-14*
*修订：2026-09-14 —— 明确收录范围（§0）；移出 R-D1 / R-D2 / R2 / R5；A-P0-4 由「待验证」改为「已实测有效」（§6）；补 R6 与归档输入禁区（§5）；修正 `--runs` 复现命令（§8）。*
