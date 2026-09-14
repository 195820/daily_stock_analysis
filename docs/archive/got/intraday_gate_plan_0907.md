# GOT 盘中 0→1 确认闸门实施方案（Intraday Confirmation Gate）

> **定位**：本文是「NGA 短线心法 × GOT 总流程」比对分析产出的**改进 1**的独立落地方案，
> 是 GOT 演进的一个**新增 Phase**，衔接在 [`agent_glue_plan_0903.md`](./agent_glue_plan_0903.md)
> §11.3 路线图的 **A6 转正+退役之后**（即排在 A5 glue + T1/T1-H 双影子运营的后面）。
>
> **遵循既有工程约定**（与 `agent_glue_plan_0903.md` 同构）：
> 事实核查先行（§2）、策略定位澄清（§3）、**影子先行**（§6）、**可计数的强制验收红线 + 反向指标**（§6.1）、
> 验证基础设施缺口显式列出（§7）、挂起项 OPEN（§8）、治理原则「**规则守决策、Agent 做证据收集与语义仲裁**」（§4.3）。
>
> **状态：草案（未实施）。等待 A6 转正后启动。**
> 创建日期：2026-09-07。

---

## 0. 结论摘要

GOT 是**日频**系统：[`orchestrator.run()`](../../../src/got/orchestrator.py) 每日跑一次，盘前产出
`regime` + `confirmed_sectors` + 选股候选（[`_filter_got_finalists`](../../../src/services/stock_screener.py) 5 轮漏斗）。
它回答了「**今天该关注哪些板块、哪些票**」，但**没有回答「这只票今天走完 0→1 了吗、现在是不是真共识、该不该 now 介入」**——
这一步目前**完全交给人在盘中判断**。

这正是比对心法后识别的**最大结构性空白**：心法第 ①/⑦/⑩ 条（"抓 0-1 确定性"、"早盘看承接、午盘看共识、尾盘看底牌"、
"竞价委比/封单、等主力砸盘洗盘看清大单承接回封再上"）全部是**盘中分时择时**，GOT 无一覆盖。

**解法**：新增一个**旁挂**的盘中确认节点 `IntradayConfirmNode`，在竞价后 / 午前 / 尾盘三个时点，
对当日 GOT 候选股用分时 + 五档数据做「0→1 确认」判定（confirm / wait / reject），
**规则守硬闸门、Agent 做边界票的语义仲裁**，影子先行、可计数红线验收。

**但有一条决定性的前置事实（§2）**：

> 盘中确认的瓶颈**不在算法，在数据地基**。`UnifiedRealtimeQuote`（[`realtime_types.py` L109-189](../../../data_provider/realtime_types.py)）
> **不承载五档 / 封单 / 委比 / 分时**；底层 `mootdx` / `pytdx` 的 `get_realtime_quote` 虽返回五档买卖**价**
> （[`mootdx_fetcher.py` L337-338](../../../data_provider/mootdx_fetcher.py)），但**未上浮**到统一结构，且五档买卖**量**（封单/委比的原料）
> 当前**取都未取**；分时序列、集合竞价**无任何接口**。
>
> → 故本方案的 **Phase 6-0（盘中数据地基）是硬前置**，与 `agent_glue_plan` 把「回放 harness」列为 P0-A 第一项同理。

---

## 1. 触发场景：日频候选 ≠ 盘中可买

| 心法条目 | 盘中现实 | GOT 现状 |
|---|---|---|
| ⑦"周末爆吹商业航天，周一龙头只高开 2 点（不及预期），早盘冲高被砸，午后才放量拉升" | 同一只票，**早盘追 = 被砸，午后拉 = 真共识** | 盘前只给"这票在候选池"，**不区分早盘冲高与午后共识** |
| ①"等龙头竞价及午后资金共识确认（走完 0 到 1）才果断跟随" | 0→1 是**盘中过程**，不是盘前状态 | 无盘中过程追踪 |
| ⑩"看集合竞价委比、看封单，等主力砸盘洗盘时看清大单承接，回封再上" | 择时靠**竞价委比 / 封单 / 分时承接** | 无竞价、无委比、无封单、无分时 |
| ⑨/㉒"涨停板悄悄撤单，封单从 10 万手撤到 2 万手，游资借狂热撤退" | 出货是**盘中封单衰减**信号 | 情绪顶 `sentiment_top` 是**日频**信号，看不到盘中撤单 |

**核心缺口一句话**：GOT 把"选股"做到了纪律化，但"当日择时（何时买 / 要不要买 / 是不是诱多）"是空白，
导致盘前候选无法防止"早盘追高被埋伏盘砸"这一心法反复强调的亏损场景。

---

## 2. 事实核查：盘中数据能力边界

> 沿用 §2「逐条对账 + 代码位置」范式。**不臆断未核实的字段**，不确定项标 🔴 待核实并转入 §8 OPEN。

| # | 能力项 | 代码 / 数据事实 | 判定 |
|---|---|---|---|
| F1 | 统一实时结构承载字段 | `UnifiedRealtimeQuote`（[`realtime_types.py` L109-189](../../../data_provider/realtime_types.py)）含 price/change_pct/volume/amount/**volume_ratio(量比)**/**turnover_rate(换手)**/amplitude/open/high/low/pre_close。**无 bid/ask 五档、无封单量、无委比、无分时序列** | ✅ 现状明确 |
| F2 | 五档买卖**价** | `mootdx_fetcher.get_realtime_quote`（[L306-339](../../../data_provider/mootdx_fetcher.py)）返回 `bid_prices[5]`/`ask_prices[5]`；`pytdx_fetcher`（[L455-488](../../../data_provider/pytdx_fetcher.py)）同。**但这两字段无处安放于 F1 的统一结构 → 归一时被丢弃** | ⚠️ 底层有、统一层丢 |
| F3 | 五档买卖**量**（封单/委比原料） | 当前 `get_realtime_quote` **只取了价（L337-338 `bid{i}`/`ask{i}`），未取量**。通达信 quotes() 协议标准含五档量列，但**列名/可得性未核实** | 🔴 待核实（OPEN-I2） |
| F4 | 分时序列 / 分钟 K 线 | `data_provider/*.py` 全量检索**无** `get_minute`/分时/fenshi 接口（仅 `base.py` 一句"分时量比"口径注释、`tushare_fetcher` 的限流计时器 `_minute_start`） | 🔴 缺失，需新增 |
| F5 | 集合竞价（auction） | **无接口**。竞价量价需从分时首段或通达信竞价数据推导 | 🔴 缺失，需新增 |
| F6 | 盘中轮询可行性 | mootdx 走**通达信 TCP 7709，不封 IP**（[`mootdx_fetcher.py` L348 注释](../../../data_provider/mootdx_fetcher.py)）；已有 `CircuitBreaker` 熔断（[`realtime_types.py` L280-425](../../../data_provider/realtime_types.py)）+ `em_rate_limiter` 限流 | ✅ 适合盘中高频 |
| F7 | 盘中调度入口 | `main.py --got` 为**日频一次性**运行（`orchestrator.run` 每日一次）；`--got-replay`（[`main.py` L1254+](../../../main.py)）为**日频归档回放**。**无盘中定时（9:26/11:25/14:45）调度** | 🔴 缺失，需新增 |
| F8 | 盘中数据归档 / 回放 | `archive.py` 为**日频**归档（`got_daily/YYYYMMDD/`）。Phase 0-A1 已完成**日频**回放 harness（`load_daily_contexts` + `--got-replay`）。**盘中分时/五档快照无归档结构** | 🔴 缺失，需新增 |
| F9 | 可复用资产：审查 Agent | Phase 3a 已实现 `build_review_executor`（轻量 Agent，3 工具 + max_steps=4 + 审查专用提示词，见 `agent_glue_plan` §11.1） | ✅ 可复用于 §4.3 语义仲裁 |
| F10 | 可复用资产：T+1 结果回填 | Phase 3c 已实现 `get_stock_history` **惰性回填** `outcome_5d/20d/was_correct`（读取时用 DataFetcherManager 拉 K 线算，见 `agent_glue_plan` §11.1） | ✅ 可复用于 §6 的 I4/I5 有效性验证 |

**F1+F3+F4+F5 合起来 = GOT 当前无法对任何票做盘中分时判定**。这是比闸门算法更基础的缺口，故列为 Phase 6-0。

---

## 3. 策略定位澄清：低吸/半路择时，**不是打板**

> 这一节防止方案被误读为"追涨停确认"，与 GOT 既有定位冲突。

- GOT 选股**轮次 0 主动剔除涨停股**（[`_filter_got_finalists` L1448-1460](../../../src/services/stock_screener.py)："当日无法买入"），
  轮次 0b 软过滤涨 ≥8% 过热票。这是**波段低吸 + 埋伏**路线，与"打板/追龙头一字板/连板梯队"方向**相反**。
- 故本闸门的目标**不是**"确认能不能封住板"，而是：对 GOT 已选出的候选股，判断**当日是不是合适的低吸/半路介入时点**——
  即心法第 ① 条的"走完 0→1"、第 ⑦ 条的"午后真共识 vs 早盘诱多"。
- **涨停封单衰减**（心法 ⑨/㉒）在本方案中**只用于持仓侧的出货预警**（若 OPEN-I4 采纳），**不用于买入侧打板**。

**边界结论**：本方案 = 「日频选出的票，盘中判介入时点」。打板模式属另一产品线，不在射程（转 OPEN-I1）。

---

## 4. 架构设计

### 4.1 节点位置与数据流

新增 `IntradayConfirmNode`（暂命名 Node 8），**旁挂**在日频主链之外，**不改 Node 1-7**：

```
盘前（既有，不动）：
  GOT Node1-7 → confirmed_sectors + screener 候选 Top-N → 落盘 got_daily/YYYYMMDD/
                                                    │
                                                    ▼ 候选清单 = 盘中闸门的输入 universe
盘中（新增）：IntradayConfirmNode 在 3 时点触发（竞价后 9:26 / 午前 11:25 / 尾盘 14:45）
  ├─ 拉盘中数据（Phase 6-0 地基）：五档量价 / 分时 / 竞价 / 量比 / 换手 / 大单
  ├─ 规则闸门（守决策，§5）：0→1 确认判定 → confirm / wait / reject + 证据 + 触发时点
  ├─（可选，Phase 6-3）Agent 语义仲裁（§4.3）：对规则判 wait 的边界票，看分时形态给建议，只有降档权
  └─ 输出 IntradayVerdict（每股：verdict/confidence/trigger_time/evidence）
        │
        ▼ 影子模式（默认）：只归档 got_daily/YYYYMMDD/intraday/HHMM/ + [SHADOW][盘中闸门] 日志
                            不影响日频 GOT 输出、不自动下单
```

### 4.2 输入 / 输出契约

- **输入**：当日 `unified.json` 的 actionable 板块候选 + `_save_got_result` 落盘的 screener Top-N（含 `sector_type`/`composite_score`/催化剂字段）。
- **输出（新增 Pydantic 模型，落 `schemas.py`）**：
  - `IntradayVerdict`：`code` / `verdict(confirm|wait|reject)` / `confidence(0-1)` / `trigger_time` / `evidence{高开幅, 量比, 委比, 分时均线位置, 大单净额, 封单量(若涨停)}` / `reason` / `shadow(bool)`。
  - 归档：`got_daily/YYYYMMDD/intraday/{0926,1125,1445}/verdicts.json`。

### 4.3 治理原则：规则守决策、Agent 做证据收集与语义仲裁

| 层 | 职责 | 依据 |
|---|---|---|
| **规则闸门** | 基于**可量化盘中数据**给硬判定（高开幅 < 阈值、量比、委比正负、是否站上分时均线、大单净额、冲高回落幅度）| 与 `_compute_market_regime` 同哲学：规则守准入/决策 |
| **Agent 仲裁（可选，影子）** | 仅对规则判 `wait` 的**边界票**，看分时形态 + 盘口语言做语义判断（"这是洗盘还是出货？"），**只有降档权 / 建议权，不得推翻规则的 reject** | 复用 Phase 3a `build_review_executor`（F9）；与 T1/T1-H "Agent 只有降档权"一致 |

**为何 Agent 是可选、后置的**：盘中数据噪声大、时延敏感，先用**纯规则**跑通影子期（I1-I6 达标），
再评估是否需要 Agent 语义层。避免 `agent_glue_plan` §1.1 教训二「该用语义的地方用了查表」的**反面**——
也要防止「该用硬规则的地方滥用语义」。

---

## 5. 0→1 确认规则闸门（三时点 × 判据）

> 把心法 ①/⑦/⑩ 工程化。**所有阈值先用 0903-0907 候选样本回标，影子期校准**（不硬编码拍脑袋值）。

| 时点 | 心法依据 | 判据（规则） | 输出 |
|---|---|---|---|
| **竞价后 9:26** | ⑦"高开 2 点=不及预期"；⑩"看竞价委比" | 板块被重点看好但龙头**竞价高开 < θ_gap**（如 <3%）→ 标"不及预期"；**竞价委比**（五档量算）判承接强弱 | `wait`（不早盘追）/ 承接强则留观 |
| **早盘 9:30-10:00** | ⑦"早上拉易遭埋伏盘砸" | 快速冲高但**量能跟不上 / 五档卖压重 / 冲高回落 > θ_pull** → 诱多 | `reject`（避免追高被砸）|
| **午前 11:25** | ⑦"午后拉才是真共识"；①"0→1 确认" | **午后放量拉升 + 大单净流入转正 + 站上分时均线 + 委比转正** → 走完 0→1 | `confirm` |
| **尾盘 14:45** | ⑦"尾盘看底牌"；⑪"快刀斩乱麻" | 尾盘维持强势/回封 → `confirm` 加强；尾盘跳水/炸板 → `reject`（持仓侧转 exit 预警，若 OPEN-I4 采纳）| `confirm`/`reject` |
| **封单监控（涨停票）** | ⑨/㉒"封单从 10 万撤到 2 万=出货" | 封单量**衰减 > θ_seal**（仅用于**持仓出货预警**，非买入）| 持仓 `exit` 预警 |

**判定优先级**：`reject`（任一否决项命中）> `confirm`（须午前或尾盘共识达成）> `wait`（默认，等下一时点）。
**几何平均置信度**：多判据合成 confidence 时沿用 `_detect_cross_node_temporal_signals` 的**几何平均**（惩罚单维极端），
避免"单一维度高分独裁"（见既有教训）。

---

## 6. 影子期与可计数验收红线

> 沿用 §8「影子→转正」范式，并**继承 T5 的两条加严**：① 指标必须**可计数**；② 必须有**反向指标**（防"接了但什么也没改"）。

### 6.1 六条可计数指标

| # | 指标 | 基线 | 转正红线 | 数据源 | 当前可度量 |
|---|---|---|---|---|---|
| **I1** | **盘中数据覆盖率**：候选股中能拿到完整盘中数据（五档量 + 分时）的占比 | **0%**（无接口，F1/F3/F4）| **≥ 90%** | `intraday/*/verdicts.json` `evidence` 非空率 | 🔴 依赖 Phase 6-0 |
| **I2** | **区分度**：`confirm` 占候选比例 | — | **20% ~ 60%**（太高=没过滤，太低=没用）| `verdicts.json` verdict 分布 | 🔴 依赖 Phase 6-1 |
| **I3** | **反向指标**：`ρ(盘中 verdict, 日频 composite_score)` | — | **< 1.0**（若 =1.0，盘中闸门只是重述日频排序 → **接线零价值**）| 影子期双跑 | ✅（影子期）|
| **I4** | **有效性（核心）**：`confirm` 组 T+1 平均收益 **−** `reject` 组 T+1 平均收益 | — | **> 0**（confirm 组显著跑赢 reject 组）| 复用 F10 `outcome_5d` 惰性回填 | ✅（复用 Phase 3c）|
| **I5** | **诱多识别率**：被 `reject`（判诱多/埋伏）的票，**当日冲高回落或收阴**占比 | — | **≥ 60%** | 当日日线（`archive`）| ✅ |
| **I6** | **数据新鲜度**：盘中轮询 `provider_timestamp` 延迟 P95 | — | **≤ N 秒**（N 影子期定，避免用陈旧数据判盘中）| `UnifiedRealtimeQuote.provider_timestamp`（F1 已有）| ✅ |

**I3 + I4 是"必须改变、且改得对"的双向约束**（对应 `agent_glue_plan` M4-b/M4-c 的核心加严）：
I3 防"零价值重述"，I4 防"改了但改错方向"。**I4 是本方案区别于既有影子的关键——它有次日真金白银的收益验证**。

### 6.2 转正条件与开关命名

| 项 | 约定 |
|---|---|
| **影子期长度** | **≥ 10 个交易日**（与 A5/T1/T1-H 一致）|
| **双达标** | I1/I2/I6（数据/工程）**全部达标** 且 I3<1.0 且 **I4 连续 5 日 > 0** 且 I5 ≥ 60% |
| **一票否决** | 任一日盘中闸门把候选**全量 confirm 或全量 reject**（无区分）→ 暂停影子期排查 |
| **开关命名** | `INTRADAY_GATE_ENABLED`（默认 `false`）+ `INTRADAY_GATE_SHADOW`（默认 `true`），与 `AGENT_SEMANTIC_GLUE_*` / `SECTOR_ALIGN_SHADOW` 同构 |
| **回滚路径** | `INTRADAY_GATE_SHADOW=true` → 闸门照跑、结果只归档不生效；`INTRADAY_GATE_ENABLED=false` → 完全不跑盘中节点 |
| **影子期运营成本** | ⚠️ **与日频影子不同**：盘中闸门须**盘中实时跑**（3 时点定时），**不能收盘后补跑**（补跑则 I5 诱多识别、I6 延迟失真）。需 W1 盘中调度器 + 人工盯盘或自动化 cron。这是本方案最大的运营负担，须在启动前确认值守方式 |

### 6.3 时序约束（实施时必须注意）

> 对应 `agent_glue_plan` §8.2「影子期与退役的时序矛盾」。

- **I4/I5 需 T+1 数据**：影子期每日的 verdict 要等次日才能算有效性 → **验收窗口天然滞后 1 日**，10 交易日影子期实际需 11 日才出全量 I4。
- **Phase 6-0 数据地基是 I1 的硬前置**：地基未通（五档量/分时/竞价拿不到）则 I1=0%，影子期无意义。
  **不可在地基未验收前开影子期**（重蹈 T5 "只看无异常就转正" 覆辙）。

---

## 7. 验证基础设施缺口（3 项，全部是 Phase 6-0/6-1 前置）

| # | 缺失项 | 核查事实 | 影响 |
|---|---|---|---|
| **W1** | **无盘中调度器** | F7：`--got` 日频一次性，无 9:26/11:25/14:45 定时触发 | 闸门无法在盘中时点运行；I5/I6 无法度量 |
| **W2** | **无盘中数据归档/回放** | F8：`archive` 仅日频，盘中分时/五档快照无归档结构 | verdict 无法回放复算，I4/I5 验证无据；出错当日数据永久缺失 |
| **W3** | **五档量/分时/竞价数据地基** | F3/F4/F5：量未取、分时无接口、竞价无接口 | 闸门的判据（委比/封单/分时均线/竞价承接）全部无原料 |

**W3 是最底层前置**（无数据则无闸门），W1/W2 是工程前置（无调度/归档则无法运营与验收）。
**故 §9 把 Phase 6-0（W3）列为第一项，且是 Phase 6-2（开影子期）的硬阻塞**——与 `agent_glue_plan` 把回放 harness 列为 A5 硬阻塞同理。

---

## 8. 挂起项（OPEN）

| # | 挂起项 | 待决问题 | 建议默认 |
|---|---|---|---|
| **OPEN-I1** | 策略定位边界 | 盘中闸门是否只做"低吸/半路择时"，还是也覆盖"打板"？ | **只做低吸/半路**（§3），打板另立方案 |
| **OPEN-I2** | 五档量/竞价可得性 | mootdx `quotes()` 是否返回 `bid_vol/ask_vol` 列（F3 🔴）？竞价数据 mootdx 能否拿到，还是需 TickFlow（`agent_glue_plan` Phase 5b，需 API Key）？ | **先核实 mootdx**；不足再评估 TickFlow |
| **OPEN-I3** | 实时性 vs 成本 | 盘中三时点轮询 Top-N（N=?）候选，mootdx 单连接串行延迟是否够？是否需并发/连接池？ | N 先取 screener Top-8；单时点串行，超时降级 |
| **OPEN-I4** | 与持仓侧的关系 | 闸门是否也给**持仓**盘中 exit 建议（心法 ⑪ 快刀斩乱麻、⑨ 涨停撤单逃顶）？还是只管候选买入择时？ | **先只管买入择时**；持仓 exit 待买入侧达标后扩 |
| **OPEN-I5** | Agent 仲裁必要性 | 规则闸门是否足够？先纯规则影子，还是规则 + Agent 双影子？ | **先纯规则**（§4.3），达标后再评估 Agent 层 |

---

## 9. 路线图衔接

**位置**：`agent_glue_plan_0903.md` §11.3 后续路线图，**A6 转正+退役之后**新增一行：

| Phase | 内容 | 前置 | 文件范围 |
|---|---|---|---|
| **Phase 6（本方案）** | 盘中 0→1 确认闸门 | **A6 转正后**（复用 F9 审查 Agent + F10 outcome 回填，逻辑上排 A6 后）| 见下 |

**为何排在 A6 之后**（用户约束"排在现有影子模式实施的后面"）：
1. 本节点**旁挂、不改 Node 1-7 日频主链**，与 A6（改 `aggregator.py` 聚合核心）**无代码冲突**；
2. 但复用了 Phase 3a `build_review_executor`（F9）与 Phase 3c `outcome_5d` 回填（F10），
   且需 A6 转正后 `aggregator` 稳定，**逻辑依赖**上排在 A6 后最清晰；
3. 避免与 A5/T1/T1-H 影子期**争抢人工盯盘资源**（本方案影子期需盘中值守，§6.2）。

**内部子 Phase**：

| 子 Phase | 内容 | 前置 | 文件范围 |
|---|---|---|---|
| **6-0 数据地基** | ① 五档量上浮（`get_realtime_quote` 取 `bid_vol/ask_vol` + 扩 `UnifiedRealtimeQuote`）② 分时接口 ③ 竞价接口 | OPEN-I2 核实 | `realtime_types.py` / `mootdx_fetcher.py` / `pytdx_fetcher.py` |
| **6-1 闸门 + 调度 + 归档** | ① `IntradayConfirmNode` + 规则闸门（§5）② 盘中调度器（W1）③ 盘中归档结构（W2）④ `IntradayVerdict` 模型 | 6-0 | `src/got/nodes/intraday_confirm.py`（新）/ `schemas.py` / `archive.py` / `main.py`（`--got-intraday`）|
| **6-2 影子运营** | `INTRADAY_GATE_ENABLED=true` + `INTRADAY_GATE_SHADOW=true`，≥10 交易日，每日盘中 3 时点跑 + 查 `[SHADOW][盘中闸门]` 日志，I4/I5 次日回填验收 | 6-1 | `.env` |
| **6-3 Agent 语义仲裁（可选）** | 规则达标后，对 `wait` 边界票接入 `build_review_executor`，双影子对照 | 6-2 达标 + OPEN-I5 | `intraday_confirm.py` |
| **6-4 转正** | `INTRADAY_GATE_SHADOW=false`，verdict 生效（推送/看板，**不自动下单**）| 6-2 双达标 | `intraday_confirm.py` |

---

## 10. 关联项（改进 2-5，本方案不含，一句带过）

- **改进 2（龙头辨识度接入选股排序）**：与本方案**协同**——盘中闸门的"龙头竞价/领涨地位"判据可复用
  [`market_theme.py`](../../../src/got/market_theme.py) 已算出的龙头角色（warming/accelerating/cooling）。留独立方案。
- **改进 3（空仓/无行情信号档）**、**改进 4（换手率 regime 自适应）**、**改进 5（情绪顶出货证据链）**：
  见比对分析，均为日频侧增量，**不在本盘中方案射程**。其中改进 5 的"封单衰减出货证据"与本方案 §5 封单监控**同源**，可共享 Phase 6-0 数据地基。

---

## 附录 A：影子期环境变量清单

```bash
# 盘中 0→1 确认闸门影子（Phase 6-2）
INTRADAY_GATE_ENABLED=true
INTRADAY_GATE_SHADOW=true
```

## 附录 B：本文事实核查约定

沿用 `agent_glue_plan_0903.md` 附录 B：所有代码断言带文件路径 + 行号；未核实项标 🔴 并转 §8 OPEN，
不当作已知事实。本文对 F3（五档量列名）、F4/F5（分时/竞价可得性）**明确标注为待核实**，
Phase 6-0 启动前须先做数据源探针验证（建议写 `scripts/probe_intraday_data.py` 实测 mootdx 返回字段）。
