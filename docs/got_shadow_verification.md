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

> **机制澄清（2026-09-14 实测）**：三条线都是 `*_ENABLED`（默认 `false`）+ `*_SHADOW`（默认 `true`）的双开关形状，
> 但 **`*_SHADOW` 在 `*_ENABLED=false` 时是失效的**——它的语义是「跑了之后不生效」，不负责让功能跑起来。
> 因此三条线的差异只由一个变量决定：**`*_ENABLED` 的实际取值**。

| 影子线 | 门（代码位置） | 实跑 `ENABLED` | 归档产物 | 当前可数性 |
|---|---|---|---|---|
| **A5 glue 语义粘合层** | `src/got/nodes/aggregator.py:985`<br>`if not AGENT_SEMANTIC_GLUE_ENABLED: → S6_disabled; return` | **true**<br>（0910/0914 产物 `status=ok`，不可能是开关关闭的形状） | `data/got_daily/{date}/agent_glue.json` | ✅ **可数，但只有 5 天**（7 份文件中 2 份是测试污染，见 §2.2） |
| **T1 候选股终审** | `src/services/stock_screener.py:1622`<br>`if AGENT_STOCK_REVIEW_ENABLED and finalists:` | **false（`.env`）→ 本次实验性置 `true`**（运行时环境变量，未改 `.env`） | `data/got_screener/{date}.json`（`_save_got_result`，`src/services/stock_screener.py:2784`） | 🟡 **跑通但无归档**——见 §1.2 |
| **T1-H 持仓审查** | `src/got/nodes/portfolio_decision.py:2162`<br>`if not AGENT_HOLDING_REVIEW_ENABLED or not holdings: return holdings` | 同上 | 结论写回 `portfolio_decision.json` 的 `unified_scoring[]` | 🟡 **开关生效但审不到任何持仓**——见 §1.3 |

**这三条线的差异本身是事实**：A5 有逐日归档产物可复算；T1 与 T1-H 即便跑通，**T1 落到日志、T1-H 落到持久化字段**，两者的离线可复核性都远低于 A5。

### 1.2 T1 跑通了，但产物被丢弃（2026-09-14 实测）

以运行时环境变量开启 `AGENT_STOCK_REVIEW_ENABLED=true` 跑 `main.py --got`，T1 **确实执行且产出了判断**：

```
23:43:49  [T1终审] 候选 8 只，截断到 Top 2
23:44:09  [SHADOW] [T1终审] 000520 凤凰航运: REJECT — 基本面亏损、资金面极弱、高位放量滞涨，风险显著
23:44:09  [SHADOW][T1终审] 影子模式: 审查完成但不执行降级，reject 数量=1
```

（对照：同日 10:09 / 11:24 / 14:07 三次运行的 `reject 数量=0`，仅盘后这次为 1。）

**但 `data/got_screener/` 最新一份仍是 `20260825.json`** —— 归档没落。原因是 `_save_got_result`（`stock_screener.py:2784`）**只在 `run_got_mode()` 内被调用**（`:958`），而 `main.py --got` 的 Phase 2 经 `portfolio_decision.py:2634` 触发 T1，**该路径不落盘** ⇒ verdict 只活在日志里，**无法离线复核**。

> 要产出 T1 归档，需另跑 `scripts/run_got_screener_v2.py`，或让 Phase 2 路径也调用 `_save_got_result`。已登记 [`got_backlog.md`](got_backlog.md) §1.C。

### 1.3 T1-H：开关生效，但筛不到审查目标（2026-09-14 实测）

`AGENT_HOLDING_REVIEW_ENABLED=true` 下，日志**无任何 `[T1-H]` 行**，`portfolio_decision.json` 的 `unified_scoring[]` 9 条**全部 `agent_review_verdict = null`**。

**不是开关失效**——是 `_select_holding_review_targets`（`portfolio_decision.py:2285-2311`）只挑三类目标：
① 埋伏持仓（`ambush_position_type` 非空）② 有 P4 退出信号 ③ `composite_score < 40`。

当日唯一持仓 `600250` 的 `composite_score = 50.1`，三类**全不满足** ⇒ `review_targets` 为空 ⇒ 走 `logger.debug` 静默返回（`:2170`）。

⚠️ **这修正了 §1.1 末尾「只要 `ENABLED=true` 跑一天，结论即落进现有归档」的结论**：
开关只是**必要条件**，还须**持仓命中上述三类条件之一**。而优先级①长期不可用——`ambush_position_snapshots` 恒 0 行（见 [`got_backlog.md`](got_backlog.md) §1.A）。⇒ T1-H 的实际可数性取决于**持仓分数分布**，比原判断更受限。

### 1.1 T1-H 的可数性高于此前判断

原判断写「`portfolio_decision.json` 中无 `agent_decision_signal` / `holding_review` / `shadow` 标记」——
**这三个字段名在代码里都不存在**。真实字段是 `schemas.py:981-992` 的三个：

| 字段 | 类型 | 取值 |
|---|---|---|
| `agent_review_verdict` | `Optional[str]` | `hold` / `add` / `reduce` / `exit` |
| `agent_review_reasoning` | `Optional[str]` | T1-H 审查理由 |
| `agent_review_penalty` | `Optional[float]` | 降档惩罚系数 |

它们**已经落进归档**（位于 `unified_scoring[]` 下），实测 **29 个归档日全部为 `None`**（20260803 → 20260914；仅 20260819 无 `unified_scoring`）。

⇒ **T1-H 的产物路径确实比原判断省事**：不需要新产物、不需要改 schema，结论直接落进现有归档。
（归档值确为 JSON `null`，非字符串 `"None"`。）

> ⚠️ **但 2026-09-14 实测修正了后半句**：「只要 `ENABLED=true` 跑一天就有结论」**不成立**——
> 开关只是**必要条件**，还须持仓命中三类筛选条件之一。当日持仓 50.1 分即未命中，跑完仍 0 条。
> 详见 §1.3。

---

## 2. A5 glue 影子期：逐日产物盘点

`data/got_daily/*/agent_glue.json` 全量清点（2026-09-14 实测，字段为归档原文）：

| 归档日 | 星期 | 大小 | `status` | `relations` | `strengths` | `families` | `degraded` | 性质 |
|---|---|---|---|---|---|---|---|---|
| 20260904 | 五 | 8,299 B | （无该字段） | 6 | 19 | 19 | false | 真实 LLM 输出 |
| 20260907 | 一 | 9,501 B | （无该字段） | 6 | 22 | 22 | false | 真实 |
| **20260908** | **二** | **无文件** | — | — | — | — | — | **缺口**：交易日，6 节点归档齐全，但无 glue 产物 |
| 20260909 | 三 | 17,099 B | （无该字段） | 22 | 32 | 32 | false | 真实 |
| 20260910 | 四 | 18,976 B | （无该字段） | 25 | 32 | 32 | false | 真实 |
| 20260911 | 五 | 1,106 B | **`S6_disabled`** | 0 | **3** | 0 | true | **测试污染**（非影子期样本，见 §2.2） |
| 20260912 | **六** | 1,106 B | **`S6_disabled`** | 0 | **3** | 0 | true | **测试污染 + 非交易日**（周六，见 §2.2） |
| 20260914 | 一 | 14,828 B | `ok` | 21 | 22 | 22 | false | 真实——**当日跑 4 次**（10:09 / 11:24 / 14:07 / 23:39），此为**第 4 次（盘后，`bar_complete=true`）**的产物，前三跑已被覆盖。同日三次跑的 glue 规模不同（14:07 那次为 `26/23`，因盘中候选集与盘后不同） |

> `status` 键只在 **0914** 的真实产物里出现；**0904–0910 的产物没有这个键**。
> 0911/0912 虽带该键，但产物是测试污染（§2.2），**不能用来推断该键的引入时点**。

### 有效样本计数

**真实 LLM 样本 = 5 天**：`20260904` / `20260907` / `20260909` / `20260910` / `20260914`。

（0911、0912 的产物是测试污染，不是影子期样本，见 §2.2；0908 无产物。）

转正条件（`archive/got/agent_glue_plan_0903.md` §8.2）要求影子期 **≥10 个交易日**且双达标。
**是否达标由负责人判定**，本清单只登记上述计数。

### 2.1 0911 / 0912 的降级归因（原记载有误，此处更正）

原记载写 `evidence="F1 降级:纯技术分"`。**归档原文不是这个**：

```
0911 / 0912（两天产物字段完全相同）
  status           = "S6_disabled"
  evidence         = "F1 降级:开关关闭"
  warnings         = ["F1 降级: 开关关闭"]
  degraded_sectors = ["旅游", "半导体", "量子计算"]
  strengths        = 3        ← 原记载只记了 relations=[]，漏了这 3 条
  confidence       = 0.3
  capital_read = news_read = sentiment_read = "unknown"
```

`S6_disabled` 在 `aggregator.py:984-990` 的定义即 **`AGENT_SEMANTIC_GLUE_ENABLED=false` 走的 F1 全量降级**，
reason 为「开关关闭」。「纯技术分」是另一条 reason，两者含义相反：一个是**配置选择**，一个是**失败**。

**这个区别不是措辞问题**：`S6_disabled` 由开关取值直接决定，而 `F1 降级:纯技术分` 是 LLM 路径的降级。
前者是**配置选择**，后者是**失败**——「以产物为准：1106 B 是 F1 降级的形状」形状判断对，但归因错，
且方向正好相反：**这两天不是 glue 坏了，是 glue 没被允许跑。**

> ✅ **「为何关闭开关」已查明**：开关关着不是生产运行的选择，而是**测试的既定前提**——
> 见 §2.2。原「未查证」结论已撤销。

### 2.2 0911 / 0912 的产物不是影子期样本，是**测试污染**（2026-09-14 查明）

上表 0911 / 0912 两天共 4 份产物（`agent_glue.json` × 2、`_replay/agent_glue.json` × 2），
**没有一份来自生产运行**。证据是 `strengths` 条数与 mtime：

| 产物 | 大小 | `strengths` | mtime | 生成者 |
|---|---|---|---|---|
| `20260911/agent_glue.json` | 1,106 B | **3** | 09-11 23:35:19 | `test_s6_switch_off` |
| `20260911/_replay/agent_glue.json` | 7,331 B | **24** | 09-11 23:35:19 | `test_0903_replay_unchanged` |
| `20260912/agent_glue.json` | 1,106 B | **3** | 09-12 00:07:13 | 同上（隔夜的第二轮测试） |
| `20260912/_replay/agent_glue.json` | 7,331 B | **24** | 09-12 00:07:13 | 同上 |

两条判据，各自独立成立：

1. **`strengths=3` / `24` 是测试夹具的数，不是当日候选的数。** `tests/test_node6_glue.py`
   的 `_make_candidates()` 恰好返回 3 个（旅游 / 半导体 / 量子计算）；`test_0903_replay_unchanged`
   从 0903 归档收集候选并**断言 `len(candidates) == 24`**。两个数字与两个夹具一一对应，
   且两天的 3 条全部是 `["旅游", "半导体", "量子计算"]`。
2. **mtime 精确到秒相同。** 同一天的两份产物在同一秒写出 ⇒ 同一次测试调用的两个用例。
   0912 的 00:07:13 与 0911 的 23:35:19 相差 32 分钟，是同一套测试跨零点跑了第二轮。

**机制**：这 4 个用例真实调用 `_agent_semantic_glue`，而该函数第 5 行起就会调
`_save_glue_result`；落盘根目录取 `GLUE_ARCHIVE_ROOT / date.today()`，回放态再加 `_replay/`。
**测试没有重定向该根目录，于是写入生产归档。**

**这与 A5 影子期直接相关，不是无关的洁净度问题**：任一交易日跑一次测试套件，就会在
`data/got_daily/{当天}/` 落一份 3 候选的 `agent_glue.json`——它会被 §2 的盘点当成当日样品计入。
§2「有效样本 = 5 天」之所以仍成立，只是因为 0914 的真实运行（23 条 strengths）盖过了它。

**处置（2026-09-14 已实施）**：`tests/test_node6_glue.py` 新增 `_redirect_glue_archive()`，
把 `GLUE_ARCHIVE_ROOT` 指向临时目录并把运行日固定为 `TEST_RUN_DATE`；4 个会落盘的用例全部接入。
存量的 4 份污染产物**未删除**——它们是本节结论的现场证据；是否清理请负责人决定。

> ⚠️ **对 §4.1 的连带影响**：`_replay/` 那两份此前被当作「回放产物」，据此推出的路径约定
> 与「回放在开关处短路」都建立在**测试产物**上。§4.1 已按此更正。

### 已知的另一处干扰

**0912 是周六**——归档存在本身是 0914 commit `dc5bbde` 修掉的"非交易日照跑"bug 的现场证据（`_check_trading_day` 的 `import TradingCalendar` 恒抛 `ImportError` 且被 `except ImportError` 静默吞掉）。修复后非交易日不再运行。

> 📌 该解释与 §2.2 不冲突：`test_0903_replay_unchanged` 在 0912 00:07 写出产物，
> 与「0912 非交易日本不应有生产运行」两件事各自独立成立。

---

## 3. M1–M6 转正红线：定义、阈值、实测值

> 出处：`archive/got/agent_glue_plan_0903.md` §8.1 / §8.2；实测值与口径修正见 `archive/got/got_layer_c_plan_0911.md` §0 / §1。
> **本节只抄录，不判定。**
>
> **2026-09-14 复跑确认**：`python scripts/verify_glue_metrics.py --date 20260910` → R-C3 **PASS 10/10**，
> 下表全部数值与文档锚点逐位一致（M1 3.1% / M1′ 34.4% / M2 28.1% / M4-a 0.591P·0.419S / M4-bc 0.646P·0.500S / M6 65.6% / M6′ 96.9% / 回收率 90.9%）。
>
> **C-P6 / C-P7 落地后复跑**：主口径虽已切换为 glue 侧 / 回收率，**R-C3 的 10 个锚点仍逐位一致、
> 脚本退出码不变**（`1/2 PASS | 0 FAIL | 1 SKIP | exit 2`）——新口径只改主从命名，未触碰任何锚点数值。

| # | 指标定义（**主口径**，C-P6 / C-P7 于 2026-09-14 落地） | 转正红线 | 实测值（0910 + 四日） | 文档给出的状态描述 |
|---|---|---|---|---|
| **M1** | **glue 侧** `agent_glue.json` 的 `strengths[].news_read` 非 `low`/`unknown`（C-P6） | ≥ 60% | **11/32 = 34.4%** | 实测 < 红线 |
| **M2** | ≥1 条 `relations[]` 的候选占比 | ≥ 70% | **9/32 = 28.1%**（四日 21.1% / 13.6% / 21.9% / 28.1%） | 4/4 不达标 |
| **M3** | 人工抽查误配率 | ≤ 15% | 未测 | 依赖人工抽查 |
| **M4-a** | `ρ(glue.strength, tech_score)` | ≥ 0.6 | **0.591 P / 0.419 S** | 不达标且 n=32 |
| **M4-b** | `ρ(glue.strength, 旧 composite)`（反向指标） | < 1.0 | 0.646 P / 0.500 S | 达 |
| **M4-c** | `ρ(glue.strength, 旧 composite)`（下限） | ≥ 0.3 | 0.646 P / 0.500 S | 达 |
| **M5** | 触发 S1–S5 任一降级场景的交易日占比 | ≤ 10% | 不可度量 | 依赖 `call_type` 落库 |
| **M6** | **「其他」回收率** = 原「其他」板块中 glue 给出真实家族名的占比（C-P7；「无归属」**计为未回收**） | ≥ 80% | **10/11 = 90.9%** | — |

**对照列 / 观察项**（C-P6 要求规则侧并列保留、C-P7 要求覆盖率降级——**两者均不参与转正判定**）：

| # | 指标定义 | 实测值 | 说明 |
|---|---|---|---|
| M1 对照 | 规则侧 `unified.confirmed_sectors[].news_check != "neutral"` | **1/32 = 3.1%** | 原主口径。与 M1 主口径的差值（3.1% vs 34.4%）本身就是「glue 有多大信息量」的度量 |
| M6 观察 | 规则侧 `frame_group != "其他"`（修复前口径） | **21/32 = 65.6%** | 原主口径，已降为观察项 |
| M6 观察 | glue 侧 `families[].family` 非「无归属」（覆盖率） | **31/32 = 96.9%** | ⚠️ **与 M6 主口径不是同一个量**：分母是全部候选（32），而回收率的分母是原「其他」板块（11） |

> ⚠️ **本节只列口径与数值，不落达标判定**——M1 / M6 是否达标由项目负责人判定（C-P8 已把 M1 标为「待标定」，C-P13 明示本轮不标定）。
> `scripts/verify_glue_metrics.py` 同样只列数值与参考线，不产生 M1 / M6 的 PASS/FAIL。

### 关键结构问题：M1 / M6 的"门禁自锁"

文档自述（`got_layer_c_plan_0911.md` §0）：M1 / M6 的**基线取自规则侧字段、红线也写在同一个规则侧字段上**，而影子期 glue「接受但不使用」该字段（`src/got/nodes/aggregator.py:1119`），规则侧字段不因 glue 而变 ⇒ **M1 恒 3.1%、M6 恒 65.6%，两条红线结构性不可达成**。

C 层给出的修正方向是 **C-P6（M1 改 glue 侧）、C-P7（M6 改为「其他」回收率）**——
两项**已于 2026-09-14 落地**：`scripts/verify_glue_metrics.py` 的主口径已切换，规则侧降为对照列（见 §4 R-C3）。

⚠️ **落地只解除死锁，不解除「红线不可达」——这是两个独立问题**。C-P6 原文即明示
「修数据源解除死锁，但不解除『红线不可达』（M1 的 60% > 34.4%）」。
新主口径的数值见上表；**达标与否仍归项目负责人判定**，本节与脚本均不代判。

### M4 的口径陷阱（已知且已被脚本显式处理）

文档锚点 `M4-a = 0.419S` / `M4-b/c = 0.500S` **只在 `rank(method="first")` 下复现**；统计学标准平均秩给出 **0.478 / 0.564**，差 0.06–0.14。
`scripts/verify_glue_metrics.py` **同时打印两个值并声明依赖输入顺序**，未静默取舍（`archive/got/got_impl_review_0911.md` §R2.10）。

---

## 4. C 层验收红线 R-C1…R-C7 的当前状态

> 出处：`archive/got/got_layer_c_plan_0911.md` §6.1。

| # | 判据 | 达标线 | 当前状态（材料） |
|---|---|---|---|
| **R-C1** | 制造 S1 / S2 各一次后 `agent_glue.json` **仍存在**且 `status` 正确 | 2/2 存在 | ✅ **本次补测通过（4/4）**——`tests/test_node6_glue.py::TestGlueFailureSamplePersistence`，覆盖 `S1_call_failed` / `S1_empty` / `S2_parse_failed` / `S2_validate_failed`，逐个断言落盘文件存在 + `status` 相符 |
| **R-C2** | `--got-replay 20260901` 的 glue 产物落在 `20260901/_replay/` | 路径精确匹配 | **部分**——路径约定 `{date}/_replay/` 在 **1 份**真实产物上成立（`20260903/_replay/unified.json`）；其余 2 份为**测试污染**（§2.2 / §4.1），不计为证据。`20260901` 本身无产物 |
| **R-C3** | `verify_glue_metrics.py --date 20260910` 十锚点逐位一致 | 全部相等 | ✅ **本次实跑 PASS 10/10** |
| **R-C4** | `status=ok` 的交易日 ≥ 7 且连续，**须分列实盘 / 回填来源** | 7 天 | 实盘仅 **5 天**且缺 0908、不连续（见 §2） |
| **R-C5** | `_log_shadow_comparison` 在 `src/` 与 `tests/` 零引用 | 0 处 | ✅ **本次实跑复验通过**——`src/got/nodes/aggregator.py:1072` 仅剩墓志铭注释（`# C-P4: … 已删除`），`tests/` 零命中 |
| **R-C6** | M2 × M3 在 8 天回填样本上的 A/B 对照表 | 2×8 = 16 点 | 🔴 **SKIP**——回填产物文件 2 份但**去重后仅 1 份**，且为禁用态占位 ⇒ **有效样本 0 天**，见 §4.1。脚本已改为分列「文件数 / 去重数 / 有效数」 |
| **R-C7** | C 层零行为变更项落地后同期归档逐字段不变 | 100% 一致 | 待回放 |

> R-C3 的实跑命令见 §8。R-C6 是 `verify_glue_metrics.py` 内**唯一被判为 SKIP** 的一条（该脚本只含 R-C3 / R-C6 两条），前置为 C-P11（回填跑批）+ C-P12（prompt A/B），两项均未实施。

### 4.1 `_replay/` 产物全量盘点（2026-09-14 实测，含污染溯源）

仓库存量 `_replay/` 产物共 **3 份**，无一构成 R-B8/R-B9 或 R-C6 需要的「对」或「多日样本」：

| 路径 | 大小 | md5(前12) | `status` | 性质 |
|---|---|---|---|---|
| `20260903/_replay/unified.json` | 46,868 B | `ccd62f7d642f` | — | **单日**产物（09-03 21:40，mtime 与当日归档同步）。是 B 层 diff 的一半，**不是一对**；且早于 0911 修复，与 A/B/C 三层红线均无关。**此前未登记。本目录唯一非测试产物** |
| `20260911/_replay/agent_glue.json` | 7,331 B | `bea358d36270` | `S6_disabled` | **测试污染**（`test_0903_replay_unchanged`，见 §2.2） |
| `20260912/_replay/agent_glue.json` | 7,331 B | `bea358d36270` | `S6_disabled` | **同一测试跨零点第二次运行**，故与上份字节完全相同 |

**两份 glue 产物是同一份，且都不是回放运行。** 二者 `relations=0` / `strengths=24` / `families=0`，
`status=S6_disabled`。此前把它读作「`--got-replay` 在开关处短路、从未调用 LLM」是**误判**：
`strengths=24` 正是 `test_0903_replay_unchanged` 对 0903 归档收集到的候选数（该用例自己断言
`len(candidates) == 24`），mtime 与该测试的另一个用例精确到秒相同（§2.2）。
它们既非 `--got-replay` 的产物，也就**不能作为「回放路径约定成立」的证据**。

⇒ 对 R-C6 而言，不是「8 天里只有 2 天有数据」，而是**文件 2 份 / 去重后 1 份 / 有效样本 0 天**；
C-P11（回填跑批）的产出前提尚未成立。R-C2 的路径约定证据随之收敛到 0903 那 1 份。

> ⚠️ 这 3 份都不是 `--replay-before/--replay-after` 或 `--archive-before/--archive-after` 可用的输入。
> §8 的 ① 组红线（R-B3 / R-B8 / R-B9 / R-C7）仍为 3 SKIP。
>
> ✅ **`verify_glue_metrics.py` 的 R-C6 样本计数已修**（2026-09-14）：原 `_count_replay_samples()`
> 只数文件数（输出「当前回填样本天数 = 2」）；现改为 `_replay_sample_inventory()`，
> 分列**文件数 / md5 去重数 / 有效数**，并把 `S6_disabled`、`replay_mode` 判为占位态不计入。
> 实跑输出已变为「有效样本 = 0 天（文件 2 份，去重后 1 份）」并逐条列出重复组与占位态日期。

---

## 5. 已实施但缺「两跑 / 回放」证据的红线

0911 四层方案有一类红线**依赖「同一批交易日各跑一遍」产生的两组产物**（归档回放对，或同日两跑快照）。
**2026-09-14 首次产出了「同日两跑」快照**（`data/_run1` / `data/_run2`），R1/R3/R4 由此**首次真算**；
「归档回放对」仍缺（三障碍见 §5.1）。

| 红线 | 出处 | 判据 | 证据 | 当前状态（2026-09-14） |
|---|---|---|---|---|
| **R-B3** | `got_layer_b_plan_0911.md` §6 | 家族降权影响面有界（无单日增幅 > 100%） | 修复前后两组多日归档 | 脚本 SKIP（`--replay-before/--replay-after` 未提供） |
| **R-B8** | 同上 | B1/B3/B4（零行为变更项）前后 `final_position_limit` / `composite_score` / `confirmed_sectors` 逐字段一致 | 同期单日归档对 | SKIP——**且干跑证明其输入在当前工器下无法产出**，见 §5.1 |
| **R-B9** | 同上 | B2/B6（行为变更项）的 `classification` 与 `dimension_scores["sector"]` 变化逐条列出 | 同上 | 同上 |
| **R-C7** | `got_layer_c_plan_0911.md` §6 | C 层零行为变更项落地后归档逐字段不变 | 修复前后两组归档 | 待回放 |
| **R1** | `got_run_model_plan_0911.md` §6 | 同日两跑状态字段逐字节一致（100%） | `data/_run1` / `_run2` | ❌ **FAIL（177/569）**——归因为**数据差异，非幂等缺陷**，见 §5.2 |
| **R3** | 同上 | 事件计数不翻倍（`mention_count` / `appearance_count` 不增） | 同上 | ❌ **FAIL（4 项计数增长）**——同一归因，见 §5.2 |
| **R4** | 同上 | EMA 计数按日（`sample_count` / `daily_scores` 键数不变） | 同上 | ✅ **PASS**（`sample_count` 2→2、键数 2→2） |
| **R6** | 同上 | 15 个状态源（W1–W6 + D1–D9）逐项幂等复测 | 每源两跑前后各一次观测点 | **SKIP（脚本硬编码，与是否传 `--runs` 无关）**——见 §5.3 |

**2026-09-14 实跑复算**（两条脚本本次输出）：

     verify_layer_b_consistency.py    → 0/3 PASS | 0 FAIL | 3 SKIP | exit 2
     verify_same_day_idempotency.py   → 1/6 PASS | 4 FAIL | 1 SKIP | exit 1   （R4 PASS；R1/R2/R3/R5 FAIL；R6 SKIP）

### 5.1 R-B8 / R-B9 的输入为何产不出（2026-09-14 干跑验证）

以 `20260903` 归档配 `20260903/_replay/` 干跑（**非 R-B8 的正规输入**，仅用于暴露机制）：

    [FAIL] R-B8  7 个应不变字段发生变化
      · portfolio.risk_assessment.position_limit: 0.631 → <缺失>
      · portfolio.risk_assessment.sector_exposure_limits: {...} → <缺失>
      · unified.market_regime.{regime,dwell_days,previous_regime,transition_count,transition_pending}: 5 项全变

三类差异，成因各不相同：

| # | 现象 | 成因 | 可否解决 |
|---|---|---|---|
| 1 | `portfolio.*` → `<缺失>` | `--got-replay` **不跑 Phase 2**（`main.py:1318` 注释「只跑 Node 6…不写归档」），仅写 `_replay/unified.json` | 可修（扩 CLI 或先跑 Phase 2） |
| 2 | `sector.frame_group_lookup` 无法比对 | `--got-replay` **不跑 Node 2**，`_replay/` 内无 `sector.json` | **不可修**——见下 |
| 3 | `unified.market_regime.*` 必然变化 | 它是**跨日状态机**，输入为 `load_regime_state_history(days=20)`；回放跑在**今天**，读到 `0817…0914`，与原归档当日的窗口不同 ⇒ 输出必然不同 | **需裁决** |

⚠️ **障碍 2 牵连出根本矛盾**：B 层修的是 `family_keywords.py` + `sector_analysis.py`（**Node 2** 的家族词表），
而 `--got-replay` **设计上不跑 Node 2**。⇒ **即使补齐产物，该回放也验不到 B 层修的东西**。
`got_layer_b_plan_0911.md` §6.3 写的「跑 Node 6 + Phase 2」与 B 层实际改动位置对不上。

障碍 3 的性质：`verify_same_day_idempotency.py` 的 docstring 自己写过「回放不含状态推进」，
而 `check_r_b8_r_b9` 把 `market_regime.*` **归入「应不变字段」**（仅 `classification` /
`dimension_scores` / `frame_group_lookup` 三个分支被分流出去）
⇒ **R-B8 在回放语义下不可能 PASS**。属 [`got_backlog.md`](got_backlog.md) §9 那一类「红线本身设计有缺陷」。

### 5.2 R1 / R3 的 FAIL 归因：数据差异，非幂等缺陷（2026-09-14 实算）

**逐事件比对两个快照的 DB**：

    node4_events     : 共有 1346 条，★ 计数漂移 = 0
      run1=1346  run2=1359   仅 run2 新增 = 13   仅 run1 有 = 0
    node4_raw_news   : 共有 5605 条，★ 计数漂移 = 3
      run1=5605  run2=5787   仅 run2 新增 = 182  仅 run1 有 = 0

**结论**：`row_count` / `total` 的增长 **100% 来自新事件**（盘后新闻多于盘中）。老事件中
`node4_events` **零漂移**；`node4_raw_news` 仅 3 条漂移，且这 3 条**同时把 `last_seen_date` 从
`09-08 / 09-10 / 09-09` 推进到 `09-14`** —— 正是跨日守卫（`last_seen < today` ⇒ 允许 +1）**正确工作**的形状。
旁证：`last_seen_date = '2026-09-14'` 的行数 482 → 667，差额 185 = 182 新增 + 3 更新，自洽。

另一铁证：**`peak` 两表都不变**（`node4_events` 19→19、`node4_raw_news` 37→37）。
`peak` 是单事件计数最大值——若幂等性坏了，第二跑会给已有事件再 +1，`peak` 必涨。

剩余差异是 `sector[*].strength_trajectory` 末位微变与板块进出集合变化，同属行情差异。

> ⚠️ **本次比较的局限**：`_run1` 取自 **14:02（盘中，`bar_complete=false`）**、`_run2` 取自 **23:39（盘后）**，
> 两次跑的数据环境并不相同 ⇒ 严格说这不是「同条件两跑」。上述 DB 级逐事件比对是比 R1 更精确的证据；
> 要更纯净，需在盘后连跑两次。

### 5.3 R6 是脚本硬编码的 SKIP

`check_r6`（`scripts/verify_same_day_idempotency.py:470`）**无论是否传 `--runs` 都 SKIP**：

    if runs:
        evidence.append("已提供 --runs，但逐源复测仍需按源分别取数…")
    verifier.add(name, STATUS_SKIP, …)

它只列出 15 个状态源（W1–W6 写侧 / D1–D9 读侧）供**人工逐源取数**，脚本未实现自动判定。
⇒ 今天产出的两个快照**不会**让 R6 变成可算。

其中 R2 / R5 的 FAIL **不是代码缺陷**，实测值与性质：

| 红线 | 实测 | 性质 |
|---|---|---|
| **R2** | 36 个含 `unified.json` 的归档日中，**0 天** `previous_regime != regime`（8 天无 `market_regime` 字段、16 天 `previous_regime` 为 null、12 天相等） | 按状态机设计（`aggregator.py:527-531`），该条件**只在情形 2（确认切换）成立**，其余两情形按设计就相等。旁证：`transition_pending` 在 0904 / 0909 / 0910 均被正确写入 ⇒ 字段接线是通的，是市场未触发切换。<br>✅ **已收口（2026-09-14）**：`tests/test_node6_regime_state.py::test_confirmed_switch` 补断言 `result.previous_regime == "bull"`（情形 2 是唯一会让两者不等的路径）⇒ 该项**不再依赖等市场切换**，每次跑测试即验 |
| **R5** | 7 组 `(type, date)` 重复，**全部落在 08-28～09-10** | **预计 2026-10-11 起自动归零**——`persistence.py:704` 的去重只剔 `date == today_str`，历史条目靠 30 日窗口裁剪，最晚重复日为 09-10 |

① 组的 3 条（R-B3 / R-B8 / R-B9）仍为纯 SKIP。快照产出步骤见 `verify_same_day_idempotency.py`
SKIP 提示内的 ①②③ —— **本次已按该步骤产出 `data/_run1` / `data/_run2`**（两目录在 `.gitignore` 的 `/data/` 内，不入版本控制）。

> ⚠️ **历史记录仍不可复现，但本次已有独立读数**：`archive/got/got_impl_review_0911.md` §505 记
> 「`--runs` 冒烟 **PASS**（比对 474 字段、差异 0）」，该次所用快照**已不存在**，**不沿用其结论**。
> 本次用新产的 `_run1`/`_run2` 独立复算得 **1/6 PASS | 4 FAIL | 1 SKIP**（比对字段数 **569**，非 474），差异归因见 §5.2。
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
| **M3** 误配率人工抽查 | 人工 | 判据 ≤15%，样本为每日候选全量（≤10 分钟/日）。工具已备：`scripts/export_glue_family_audit.py`（**已纳入版本控制**，commit `0ac5252`），自述"只搬运事实，不做判定，判定列留空给审核者"。<br>📌 **材料已生成（2026-09-14 实跑）**：5 个有效归档日（0904 / 0907 / 0909 / 0910 / 0914，与 §2 的样本数独立互证）、**128 行候选**，类别分布 `same=78` / `recovered=42` / `overridden=6` / `both_none=2`；**待判**的那一类是 `overridden`（6 行）。输出 `data/glue_family_audit/family_audit.html`。判定归负责人 |
| **OPEN-C2** M3 人工流程未定义 | 人工 | 抽查怎么抽 / 判据 / 谁判，文档建议分层抽 10 个、三档判据 |
| **OPEN-C1** `transition_count` 时间炸弹 | 时间窗 | 🔴 **首次跌破阈值 = 2026-09-24**（已按窗口实算，替代原「约 09-25」估计）。窗口口径 = `load_regime_state_history(days=20)`，取**最近 20 个带 `market_regime` 字段的归档日**（无该字段的日不占窗格）。<br>📌 **实测 20260914 = 11**，窗口为 `0817…0914` —— 原文记的「0813–0826 十天」已过期：**0813 / 0814 早已滚出**，剩余 8 天（`0817–0826`）在 `0915–0924` 逐个滚出。<br>滚动预测（假设期间无新切换）：`0915:10 → 0918:8 → 0921:7 → 0923:5 → **0924:4**`。<br>两个消费者都在 `aggregator.py`：`:3192` 仓位震荡惩罚 ×0.8、`:3335` `range_reversal` 信号生成（需 `>=5` **且** `persistence_regime == "low"`）——**0924 起两者同时失去触发条件** |
| **A5 影子期** | 时间窗 | 需 ≥10 个交易日（当前有效样本 **5 天**，见 §2；缺口 0908，0911/0912 的产物是测试污染见 §2.2）。距达标差 **5 个交易日** |
| **Phase 5b 显著性校准** | 时间窗 | 需归档 ≥60 交易日（`custom/got_timeseries_calibration.md` 记为预计 2026-11 下旬） |

---

## 8. 复现命令

```bash
# A5 影子产物逐日盘点（§2）
for d in $(ls data/got_daily/ | grep -E '^2026' | grep -v pre_fix); do
  f="data/got_daily/$d/agent_glue.json"
  [ -f "$f" ] && echo "$d $(stat -c%s "$f")" || echo "$d ---"
done

# _replay 产物盘点 + 去重（§4.1）—— 两份 glue 回放产物 md5 相同即同一份
python -c "
import hashlib,glob
for p in sorted(glob.glob('data/got_daily/*/_replay/*.json')):
    b=open(p,'rb').read()
    print('%-52s %7d B  md5=%s' % (p, len(b), hashlib.md5(b).hexdigest()[:12]))
"

# M1–M6 与 R-C3 十锚点复算（§3 / §4）
python scripts/verify_glue_metrics.py --date 20260910

# B 层 R-B3 / R-B8 / R-B9（§4 / §5）—— 无参时 3 SKIP（缺回放对）
python scripts/verify_layer_b_consistency.py

# R1/R3/R4/R5/R6 同日两跑 diff（§5）—— --runs 需要【两个】快照目录
# 快照怎么产出见脚本 SKIP 提示里的 ①②③；只传字面量 --runs 会直接 argparse 报错
python scripts/verify_same_day_idempotency.py --runs data/_run1 data/_run2

# R1/R3 的差异归因（§5.2）—— 逐事件比对两快照 DB，区分「新事件」与「重复计数」
python -c "
import sqlite3
def load(p): return sqlite3.connect('file:%s?mode=ro'%p,uri=True)
c1,c2=load('data/_run1/stock_analysis.db'),load('data/_run2/stock_analysis.db')
for t,k,cnt in (('node4_events','event_id','mention_count'),('node4_raw_news','id','appearance_count')):
    a={r[0]:r[1] for r in c1.execute('select %s,%s from %s'%(k,cnt,t))}
    b={r[0]:r[1] for r in c2.execute('select %s,%s from %s'%(k,cnt,t))}
    common=set(a)&set(b)
    print('%s: 共有 %d 条，计数漂移 = %d；仅 run2 新增 = %d' % (t,len(common),sum(1 for x in common if a[x]!=b[x]),len(set(b)-set(a))))
"

# 无快照时跑全量（R2/R5 可离线真算，其余 SKIP）
python scripts/verify_same_day_idempotency.py

# R-C1 复测（§4）—— 4 个 S1/S2 失败样本落盘用例（无需 LLM / 网络）
python -m pytest tests/test_node6_glue.py::TestGlueFailureSamplePersistence -q

# R2 收口（§5）—— 情形 2 的 previous_regime 断言
python -m pytest tests/test_node6_regime_state.py::TestApplyRegimeStateMachine -q

# M3 人工抽查材料（§7）—— 输出 data/glue_family_audit/family_audit.html
python scripts/export_glue_family_audit.py

# R-C5 复验（§4）—— src/ + tests/ 零真实引用（仅 aggregator.py 墓志铭注释）
rg -n '_log_shadow_comparison' src/ tests/

# A-P0-4 沙箱幂等实测（§6.2）—— 复制 monitoring.json 到临时目录后连调 save_monitoring
# （不触碰 data/got_timeseries/monitoring.json；完整片段见 §6.2 说明）

# T1/T1-H 归档证据检查（§1）
ls -la data/got_screener/ | tail
```

---

*生成日期：2026-09-14*
*修订：2026-09-14 —— 明确收录范围（§0）；移出 R-D1 / R-D2 / R2 / R5；A-P0-4 由「待验证」改为「已实测有效」（§6）；补 R6 与归档输入禁区（§5）；修正 `--runs` 复现命令（§8）。*
*修订：2026-09-14（第二轮）—— R-C5 复验通过并改判（§4）；R-C6 样本描述更正为「2 份 = 同一份 `S6_disabled` 占位」，有效样本 0 天（§4 / §4.1）；新增 §4.1 `_replay/` 产物全量盘点，登记此前未记载的 0903 单日产物；§5 补两条脚本的实跑复算值与 R2 / R5 的性质说明；§7 补 `transition_count` 实测值（0914 = 11）；§8 增 B 层脚本与 `_replay` 盘点命令。*
*修订：2026-09-14（第三轮，影子期）—— §1 增「`*_SHADOW` 在 `*_ENABLED=false` 时失效」的机制澄清，三条线改按各自门与实跑取值列；新增 §1.1 T1-H 真实字段名与 29 天全 `None` 实测（原记载的三个字段名在代码中不存在）；§2 改为含 `status`/`relations`/`strengths`/`families`/`degraded` 的逐日表；新增 §2.1 更正 0911/0912 的降级归因（`S6_disabled`「开关关闭」≠「F1 降级:纯技术分」）；§3 补 R-C3 复跑 PASS 10/10；§4.1 补脚本 R-C6 样本数高估的提示；§7 更新 A5 样本数与 M3 工具入库状态。*
*修订：2026-09-14（第四轮，修正轮）—— 新增 §2.2：**查明 0911/0912 的 4 份产物全部是测试污染**（`strengths=3/24` 与测试夹具一一对应、mtime 精确到秒相同；测试未重定向 `GLUE_ARCHIVE_ROOT`），§1/§2/§7 随之改口径，§2.1 的「未查证」撤销；§4.1 据此更正——`_replay/` 那两份不是回放产物，R-C2 的有效证据收敛到 0903 一份；**§4 R-C1 由「待复测」改判 PASS（补 4 个失败样本落盘用例）**；§4 R-C6 与脚本改判为「文件 2 / 去重 1 / 有效 0」（`_replay_sample_inventory()`）；§5 R2 标注已由测试收口；§7 OPEN-C1 由「约 09-25」改为实算的 **09-24 破阈值**（并用窗口实算更正「0813–0826 十天」→「0817–0826 八天」）；§7 M3 补材料实跑结果（5 天 / 128 行）；§8 增 R-C1、R2、M3 三条复现命令。*
*修订：2026-09-14（第五轮，C-P6/C-P7 落地）—— §3 表改主从：**M1 主口径 = glue 侧 `strengths[].news_read`（34.4%）、M6 主口径 = 「其他」回收率（90.9%）**，规则侧（3.1% / 65.6%）与 M6′ 覆盖率（96.9%）降为对照列 / 观察项，并注明「回收率与 M6′ 分母不同（11 vs 32），不是同一个量」；§3「门禁自锁」小节由「尚未落地」改为**已落地**，并保留 C-P6 原文的「只解除死锁、不解除红线不可达」。**R-C3 复跑仍 10/10、脚本退出码不变**（`1/2 PASS | 0 FAIL | 1 SKIP | exit 2`）——新口径只改主从命名。M1 / M6 达标判定仍归项目负责人，脚本与本节均不代判。*
*修订：2026-09-14（第六轮，首跑实测）—— **首次产出同日两跑快照并开启三条影子线**：① §5 R1/R3/R4 **首次真算**（R4 PASS；R1/R3 FAIL 但经 DB 级逐事件比对**归因为数据差异而非幂等缺陷**——`node4_events` 零漂移、`peak` 两表不变、`node4_raw_news` 仅 3 条跨日重采且 `last_seen` 同步推进）；② 新增 §5.1（R-B8/R-B9 输入产不出的三障碍，含「`--got-replay` 不跑 Node 2 ⇒ 验不到 B 层改动」这一根本矛盾）、§5.2（R1/R3 归因证据）、§5.3（R6 为脚本硬编码 SKIP）；③ §1 三条影子线改为实测：**T1 跑通且 `reject=1` 但产物被 Phase 2 路径丢弃**（新 §1.2）、**T1-H 开关生效却因持仓 50.1 分未命中筛选条件而审不到任何目标**（新 §1.3），并据此**修正 §1.1 末尾「开关打开跑一天即有结论」的结论**；④ §2 的 0914 行更新为第 4 跑产物（当日共跑 4 次，前三跑被覆盖）；⑤ §8 增 DB 级差异归因命令。*
