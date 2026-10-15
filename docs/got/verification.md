# GOT 待验证 / 影子期清单
> **原位置**：`docs/got_shadow_verification.md`（2026-09-17 文档结构重排移入当前位置）

---

## ‼️ 2026-10-14 补登（G-A2 位置安全带接线 — backlog §1 row 52 修法③）

| 项 | 内容 |
|---|---|
| 根因 | `_build_sector_kline_lookup`（`stock_screener.py:1902`）只从 `sector_context.ambush[].klines` 建缓存；`SectorDetail`（`schemas.py:267`）无 `klines` 字段 → Node 2 `_analyze_single_sector` 组装时 raw dict 里的 klines 被 Pydantic 静默丢弃 → 归档 ambush 条目永不携带（1009/1012/1013 各 16 条、1014 10 条实测 `any klines = False`）→ 生产 lookup 恒 `None` → `g_a2_position_unsafe` 永不可能非零，安全带因子 soft-pass（闸门退化双因子） |
| 修法裁决 | 三选一：①选股侧按 `code` 实时拉（选股路径加 I/O）②`SectorDetail` 加 `klines` 字段（归档体积大增、与另一会话 schemas.py 未提交改动冲突）**③Node 2 预计算三标量（拍板采纳）**——close/high20/ma20 注入 `indicators`（自由 dict，schema 零改动），判据留在闸门（trace/转正判据口径不破），数据归源头（不重复拉、不整包搬运）。口径：high20 取 **high 列**（对齐 PLAN_v2 §166 重放校准「收盘 ≤ 20 日高×1.03」与仓库惯例 `_detect_launch_signal` 突破判据，J1-J4 胜率结论直接迁移；high 缺失/非正回退 close 列），MA20 用收盘算（§166 同文） |
| 改动 | Node 2：新增静态助手 `_compute_position_scalars(df)`（`sector_analysis.py:1399`，≥20 根才产出 `{pos_close, pos_high20, pos_ma20}`，round 4 位；短史/ma20 NaN/high20 非正 → 空 dict 保 soft-pass），`_analyze_single_sector` 在 `SectorDetail` 组装前 `indicators.update(...)`（:1370）。选股侧：`_build_sector_kline_lookup` 改读 `indicators` 三键建缓存（:1919-1924），klines 旧路径废弃删除 |
| 生效边界 | 修复落码后**首个生产实跑归档**起 ambush 条目才携带三标量——新归档前生产仍是 soft-pass；旧归档条目（无三键）lookup 返回 None 走 soft-pass，回放兼容。生效判据：新归档 `sector.json` ambush 条目 `indicators` 含 `pos_close/pos_high20/pos_ma20` 三键，且实跑日志 `pos_unsafe=` 出现非零值 |
| 测试 | `tests/test_ambush_lane_split.py` 新增 `TestGA2PositionScalars` 6 例（助手满史 high 列口径/无 high 列回退 close/短史空 dict、lookup 读 indicators、端到端 position_unsafe=1 真拦截〔failing-before 场景：生产归档形态恒 soft-pass〕、缺三标量 soft-pass 保留）；`test_sector_state_machine`/`test_wave_days`/`test_meg_momentum_exhaustion_gate`/`test_family_topping_suppression` 回归。.venv 与 conda 双 152 passed（五套：ambush_lane_split 20 / sector_state_machine / wave_days / meg_momentum_exhaustion_gate / family_topping_suppression） |
| 归因说明 | 抓取层无故障——`_fetch_sector_data` 90d K 线拉取/重试/`data_quality.sector_klines` 记账全正常，K 线在 Node 2 内部已充分消费（indicators/RRG/生命周期全源于它）；断点仅是 `SectorDetail` 映射出口缺失 |

---

## ‼️ 2026-10-13 补登（埋伏道静默失效 Bug 修复 — 重放读数与可观测性接线）

**背景**：当日全流程实跑确认 5 个埋伏板块（影视音像 83 / 黄金概念 55 / 黄金 55 / 有色 45 / 小金属 40）却 0 只埋伏票进终选。定性为**决策 Bug 而非闸门过滤**：P5 块变量遮蔽（`ambush_candidates` 被主线池重过滤覆盖为 `[]`）致埋伏道四闸门整段未执行——修复前埋伏道从未被调用，故**本条不设影子判据**（修复即转正对象），登记修复后可观测性与重放读数供复核。

| 项 | 内容 |
|---|---|
| 根因与修复 | P5 块遮蔽修复（局部 `p5_ambush`）+ 误导日志双分支 + 闸门存活链 INFO 行 + `funnel_trace` 扁平合并；详见 [`CHANGELOG.md`](../CHANGELOG.md) [Unreleased] 2026-10-13 fix 条目 |
| 回归锚点 | `tests/test_ambush_lane_split.py` 14 绿（含 2 新例：全链路断言 `_run_ambush_lane` 恰调 1 次；失败注入验证 failing-before/passing-after 闭环） |
| **重放读数（归档上下文+真实闸门函数）** | 50 票 → A1 50 过（`g_a1_pass=40 soft_pass=10 flow_negative=0`）→ A2 30 过（`no_catalyst=20`）→ A3 30 过 → A4 Top3=黄金/有色/小金属。逐板块：影视音像 **no_catalyst**（唯一事件 event_date=2026-10-13=运行日，`0 < days_to_event` 排除同日）；黄金概念 **no_catalyst**（事件只挂「黄金」「有色金属」，无别名变体）；黄金/有色/小金属 pass（conf 0.8/0.8/0.7）→ 三板块本应各出 TopK 候选 |
| 重放局限 | ① 逐票 change_pct/volume_ratio 归档无 → G-A3 用中性值（knife 判定不敏感）；② **G-A2 位置安全带是生产级 schema 缺口，非重放产物**：`SectorDetail`（`schemas.py:267+`）无 `klines` 字段且 `archive.py` 全量 model_dump ⇒ 归档 `sector.json` ambush 条目永不带 K 线，而 `_load_got_context` 消费的正是这些归档 JSON ⇒ `_build_sector_kline_lookup` 生产恒空缓存 → `position_unsafe` 分支**生产也恒 soft-pass**（~~已登 backlog 待接线~~ → **2026-10-14 修法③接线完成，见「2026-10-14 补登」节**） |
| 新可观测性 | 下一实跑起：日志 `埋伏道闸门: in=… G-A1→… G-A2→… G-A3→… G-A4→… (soft= flow_neg= dde_deg= no_cat= low_conf= pos_unsafe= knife= err=)`（合并块后单时点扁平汇入 `funnel_trace`，含合并块写入的 `cross_lane_collision`/`lane_overflow_n`）；`meta.json.funnel_stats.round_survival_counts` 直接含 `lane_in/g_a1_pass/…/cross_lane_collision/lane_overflow_n` 扁平键（嵌套 `ambush_lane_trace` 键不再出现；trace 全量仍独立落 `portfolio_decision.json.ambush_lane_trace`） |
| 待裁决（登记不修） | ① 同日事件窗口语义：`0 < d <= 14` 使运行日当天事件（如 OpenAI 发布会→影视音像）不可作催化剂——是否改 `0 <= d`。〔② candidate_pool.json 写入器键错位已不属于本节：2026-10-14 修复拍板（dict 直修·窄契约），迁 backlog §7 #13〕 |

---

> **性质**：本清单只登记**待验证项、其判据、实测值与出处**，**不落达标判定**。
> 影子期 M1–M6 等转正红线的**合格与否由项目负责人判定**；本清单的作用是让判定所需的事实可查、可复算。
>
> **基准**：2026-09-14 工作区快照，分支 `fix/mootdx-hq-server-pool`。
> **原文档已归档**至 [`archive/`](archive/)，本清单所有引用均指向该目录。
> **环境偏差**：实测 Python 3.10.20，项目要求 3.12.13 —— 本清单中所有实跑结论受此影响。
>
> **配套文档**：[`backlog.md`](backlog.md)（待实施）、[`archive/README.md`](archive/README.md)（归档索引与已完成清单）。

---

## ‼️ 2026-10-12 全流程实跑：多条影子线今日才真正起算 + 两条零产物定性

**背景**：2026-10-12 盘中全流程（09:49→10:06，510.1s，`bar_complete=false`，`git_commit=5a4f29f`）是 10-09~10-11 合入的那批闸门/影子线**第一次有完整归档的一天**（09:37 那一跑在 Node 4 崩溃、零产物，见本批 commit `e691fac`）。本节只登记读数，达标判定归负责人（遵 §0 收录范围）。取证脚本 `.review_tmp/read_20261012.py`（只读、不入库）。

### 今日真正起算的线

| 线 | 今日读数 | 可数天数变化 | 说明 |
|---|---|---|---|
| **MEG** | `shadow_meg.json`：evaluated **3**、层2 命中 0、层3 缩仓 0 | 1 → **2 天**（1009、1012） | 仍无正例，命中率/假阳性不可判 |
| **FTS** | `shadow_fts.json` **首份**：evaluated 3、`suppressed_families=[]`、层B 命中 0；`unified.family_suppression=[]` | 0 → **1 天** | 10-10 记的「0 天」自此失效；今天 `topping_risk` 档未进压制台账（家族未跨日 latch 到 aggravated） |
| **FXG** | veto `evaluated 25`、剔池 0、豁免 0；**`fxg_baseline_scoring.json` 首份**（`mode=baseline_off` 对照 3 行） | 0 → **1 天** | 自建对照机制首次真实跑通；「≥1 周」时钟自 10-12 起算 |
| **GATE-1 tradability** | `tradability_shadow.mode="shadow"`、`pending_exits=[]` | 0 → **1 天** | WI-P-4 的「下一交易日确认 mode==shadow」今日闭环；**账户空仓无卖单 ⇒ 今日无 `quote_unavailable` 样本** |
| **项三 3a / D-3 基线** | `meta.json.funnel_stats` **首次非空**：actionable 7 / 有入选 3 / **零入选率 0.571**（创新药、有色、生物疫苗、银行），逐轮存活 61→55→55→36→20→15→6→3 | 0 → **1 天** | **D-3 归零率基线起算日 = 2026-10-12**（取代原「≥1013 首验后才起算」的表述） |
| **个股级事件退日历** | 42 事件按源 `macro_rule 10 / global_map 21 / llm_narrative 11`，**`akshare_*` 计数 = 0** | 验收首次可读 | 10-08 裁决的判据今日首次通过；再现即旁路复活。**后续：10-13 = 0/62、10-14 = 0/13 连续通过（登记见 10-08 节）** |
| **批次 G / C1 / E1 / F** | `review.json` 在产；`consistency.json`（抽样 3 / 告警 0 / 缺源 0）；`net_preflight` 4/4 ok；`scoring_formula_version=2026-10-11.1` | 首日 | 一期批次首次有生产读数 |
| **影子五线** | `risk_override`/`sell_source`（缺口 ¥3,541、削 0 只）/`exposure_enforce`（`mode=n/a`）present；**`signal_size_shadow=null`**；**`mature_reduce_shadow=[]`**（旧形状）| 逐日累计 | 当日把五线判据里待确认的 **`[]`（跑了没命中）vs `null`（未产样本）** 语义实例化了一遍；**其中 `mature_reduce_shadow` 已于同日定稿为三态 dict（A1 #3 / WI-P-6），其余四线门槛仍待填（A1 #4）** |

### 两条零产物：定性完全不同，别混为一谈

1. **`shadow_bear_gate.json` 缺失＝留痕被抹，不是「当日无拦单」**。日志铁证：10:02:11「Node1=bear 收手闸门**命中 1 单**（dip_mode=veto，埋伏来源已豁免）」、10:06:10 第二轮「命中 0 单」，而 `shadow_bear_gate.json` 未落盘、`reasoning.md` 渲染成「本次无产物…多为当日无拦单」（该解释在此案下**是错的**）。机制：`portfolio_decision.py:4027` 每轮 `_score_candidates` 重置 `self._bear_gate_shadow = []`，`:4306` 只在命中时组装 dict，`:3492` 落盘处 `or None` ⇒ **最后一轮的 `[]` 覆盖前一轮命中**。Phase 2 一天跑两轮是常态（今日实测），所以 backlog §1.H 记的「拦单留痕已实施闭环」在生产上不成立，**B 的误伤率照样算不出来**。修法（跨轮按 `date+symbol` 累积去重 / 落盘时合并当日各轮）与判据对齐已回写 backlog §1.H 改判为真·待实施。
   **〔2026-10-14 复核订正 + 修复落地（backlog §1.H 收口）〕**：当日 09:49 run 为**单次** `_score_candidates` 调用——10:02 命中 1 单出自 **FXG veto 双跑的基线实例**（独立 `PortfolioDecisionNode`，不走 `run()/_save_shadow_records`，永不落盘），10:06 真实轮 0 命中 ⇒ 无产物；「每实例内多轮重置互相覆盖」在生产拓扑（每实例单次调用）下不存在，上文机制链与「修法①跨轮累积」**作废**。真实盲区两条：①基线实例只进日志零归档（已修：拦单观测随 `fxg_baseline_scoring.json` 以 `bear_gate` 子块落盘）；②0 命中日无 tombstone、缺文件三义混写（已修：`_score_candidates` 末尾无条件组装 `{hits:0, executed:false}`，dict 必落、0 命中日覆盖 stale 旧文件）。**现行判据**：`shadow_bear_gate.json` **缺文件＝当日未跑 Phase 2**；文件在＝「跑过就有产物」，0 命中日读 `hits:0`（tombstone），命中日读 `flagged_stocks`。详见下文「bear gate 拦单留痕产物」节 2026-10-14 段。
2. **`shadow_antibias.json` 缺失＝结构性无输入，不是缺陷**。批次 B 缺省开，但 `antibias._collect_actionable_sectors` 只取 `is_holding` 或 action ∈ (`buy_trend`, `buy_dip`, `hold`)；今日 `unified_scoring` 3 行**全 `watch` 且空仓** ⇒ 返回空、函数 `return None` 不落盘（无任何日志/告警）。⇒ B 的转正判据「影子 ≥10 交易日 + `counter_thesis` 非空率 >80%」在空仓 / 全 watch 的行情日**一天样本都攒不到**，判据必须补「仅在有买入/持有标的的交易日计样本」，否则又是一个不动的计时器（同 FXG「暂无读数 ≠ 证伪」谱系）。已登 backlog §9 #7。

> ⚠️ **本节不改动下面各节的历史记载**，只作起算日订正：下文「三闸门读数起算」中「MEG 1 天 / FTS 0 天 / FXG 0 天」「至今一份都没有」与「≥1013 首验后才起算」均自 2026-10-12 起过期。

---

## ‼️ 2026-10-11 补登（排期批 4/5 影子五线判据——「影子线必登判据」条款回溯）

**背景**：`sell_source_shadow` / `risk_override_shadow` / `exposure_enforce_shadow` / `signal_size_shadow` / `mature_reduce_shadow` 五条影子线自 0928 随排期批 4.1/5 上线，早于 10-10「影子线必登判据」条款，属条款未回溯的漏网（backlog §8 #13 挂号）。本节按条款补登**产物位置与读数口径**；转正门槛数值归负责人。审查取证：[`reports/got_audit_20261011.md`](../../reports/got_audit_20261011.md) §2.3（报告不入库）。

| 影子线 | 开关 | 产物/读数位置 | null vs `[]` 语义（实测推断，待负责人确认） | 六归档日实测（0928–1009） |
|---|---|---|---|---|
| **4.1 超限减仓卖出来源** | `GOT_SELL_SOURCE_ENABLED=0` + `GOT_SELL_SOURCE_SHADOW=1`（缺省影子态） | `portfolio_decision.json` 顶层 `sell_source_shadow`：本会削谁/多少/是否撞家族宽限 | `null`=当日 Step 1 后未超 `position_limit`（未进 Step 1.5 检测）；非 null 列表=检测跑了，行内有被削候选 | present 仅 1008/1009（7 条）；0928–0930 null。**〔2026-10-12 裁决：冻结，不再积算天数；去留归持仓大改 WI-P-7〕** |
| **3b 风险覆写计划** | `GOT_DISAGREEMENT_SHADOW=1`（缺省开；`GOT_DISAGREEMENT_ENABLED=0` 时 plan 仅观测不进 T3） | `portfolio_decision.json` 顶层 `risk_override_shadow` | `null`=3b 开关未跑或当日无矛盾；非 null=有 RiskOverridePlan 观测。**本线不冻结，门槛已定（见下）** | 6/6 日 present（各 5 条）。**〔10-12 复核〕** present 白名单=0928/0929/0930/1008/1009/1012 共 **6 个有效样本日**（glue 均 `status=ok`）；`conflict_type` **仅 2 种取值**（`partial_bullish_with_degraded_inputs`×4 / `mixed_with_degraded_inputs`×2），11 类冲突分类只覆盖到 2 类 |
| **敞口强制（Step 1.5 变体）** | 归批 5 敞口执行观测 | `portfolio_decision.json` 顶层 `exposure_enforce_shadow` | 同上惯例 | present 0930/1008/1009（各 5 条）；0928/0929 null。**〔10-12 裁决：冻结；去留归 WI-P-7〕** |
| **信号仓位缩放观测** | 归批 5 | `portfolio_decision.json` 顶层 `signal_size_shadow` | 同上惯例 | present 仅 0930（6 条），其余日 `null`（空仓/无买入分配 ⇒ 无差值可算）。**〔10-12 裁决：冻结；去留归 WI-P-7〕** |
| **成熟期减仓观测（T-2）** | 归批 5 | `portfolio_decision.json` 顶层 `mature_reduce_shadow` | **三态已定稿（2026-10-12 A1 #3 / WI-P-6）**：字段由裸 list 改为 dict `{hits, holdings_seen, skipped_reason}`——`null`=本次未调 `_score_holdings`；`skipped_reason=no_accounts/no_holdings`=跑了但无可观测对象（**hits 必空，不得读作“没有到期持仓”**）；`holdings_seen>0 且 hits 空`=真读数。⚠　10-12 前为裸 list，旧归档（1008/1009 的 `[]`）与新 dict **不得直接对拍**。返工钩子见 [`plans/持仓模块重构_待分析与待实施清单_20261010.md`](plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) §三 WI-P-6 与 §六 | 1008/1009 旧形状 `[]`（当时无法区分无持仓与无命中）；0928–0930 null；10-12 起为新 dict。**〔10-12 裁决：冻结；三态定稿不等于将转正，去留归 WI-P-7〕** |

> ⚠️ **读数起算更正（2026-10-11 复核修正，推翻原注记）**：批 5 曾有 Critical #1（`aggregator.run()` 裸 `os.getenv` 缺模块级 `import os`，`912cf68` 09-29 修复）。原注记「0928/0929 归档的 3b/4b 影子字段不可作标定输入（run() 必崩，产物为空/不存在）」**与盘上证据不符**：0928 归档（21:47:59，meta `git_commit=ae432ab`、`bar_complete=true`、时长 1202.6s）已含非空 `conflict_summary_shadow` / `market_theme_shadow`，`reasoning.md` 亦渲染 🧭 段——若按提交序（ae432ab 无 `import os`）执行，run() 在 3b 影子分支入口（缺省开）必崩、产不出该档；唯一自洽解释是修复当晚已落**工作树**（先跑后提交，与批 4.1 残片 `candidate_pool.json` 23:59:58 同款），0929 归档 meta 已记 `git_commit=912cf68`。**断窗（合入 `3d71945` 09-28 18:23 → 修复落树）内无归档落入 ⇒ 现存 0928/0929 的 3b/4b 影子读数均为真跑产物、可作标定输入**；backlog 轮批 5 注记同日更正。

**转正门槛（2026-10-12 负责人裁决，取代本行原写的“未定——五线均无登记”）——拆成两侧**：

**① 3b `risk_override_shadow`：已定门槛，影子期到期即可测**（本线不属持仓侧，继续爬坡）：
- **有效样本日**：`risk_override_shadow` 非 null 且当日 `agent_glue.status=ok`（降级/回放日不计）；**需累计 ≥10 个**（现 6 个，差 4）。
- **测法（先写后跑）**：按 **11 类冲突分类分层**抽查，每类 ≥2 条（全量归档只出现过 2 类 ⇒ 不足类用合成上下文补测，并如实标“未见于生产样本”，不得只测已出现的两类就算过）；负责人逐条判“分类 + `decision_path_hint` 是否成立”，**一致率 ≥80%**。
- **硬红线**：零条“实际应买入/应减仓被判成 `veto_buy`”的严重误判（enabled 后该判定会把 `buy_budget` 归零，错一次就是直接资金影响）。
- ❌ **不得拿“与现行 `contradictions[]` 差异 0”当判据**：那是两套口径互比，一致只说明没打架，不说明分类对（参 M3 结案：分层人工终审才是尺子）。

**② 其余四条（`sell_source` / `exposure_enforce` / `signal_size` / `mature_reduce`）：全部冻结，不再积算转正天数**——它们都是持仓/卖出侧观测，去留归持仓模块大改裁决（**[`plans/持仓模块重构_待分析与待实施清单_20261010.md`](plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) WI-P-7**）。原因为什么不是“再等等”而是“自等”：四条的样本前提全在持仓大改手里（W3 多账户口径、conf_w/ECE 未校准、空仓日无买入分配、T-2 生命期标签断供）——继续天数只会得出一堆 `null`，并制造“已在观察”的假安心。冻结前已产生的读数保留在案，但不作转正依据。

> 本节不再参考 GATE-1 的“≥10 交易日”惯例套在持仓四条上——GATE-1 每日都有卖单候选所以天数有意义，持仓四条没标的就零样本（同 §9 #7 antibias 与 §1.H bear gate 的“计时器不动”病）。

---

## ‼️ 2026-10-15 补登（4b 市场结构上下文影子判据——「影子线必登判据」条款补漏）

**背景**：4b（批次 5 §2.2，2026-09-28 上线）此前只有读数更正（§7 TickFlow 探测节「4b 排名源确定 mootdx 自算」）与 10-11 复核注，**判据四要素从未入本清单**——同 §8 #10 谱系「影子线必登判据」漏登，本节补齐。只登记事实与判据面，达标与否归负责人（遵 §0）。

| 要素 | 登记 |
|---|---|
| **开关与缺省** | `GOT_MARKET_THEME_SHADOW=1`（缺省开，每日真跑零决策改动）/ `GOT_MARKET_THEME_ENABLED=0`（缺省关，开了才对 `overall_confidence` 施一致性因子并填 `market_theme` 真值）。⚠️ 两者为**消费点内联 `os.getenv`**（`aggregator.py` 4b 消费块 + `orchestrator.py` Step 2.5 构造块），**不在 `catalyst_config.py`**（Phase A 勘误表已勘） |
| **产物** | `unified.json` → `market_theme_shadow`（影子留痕 dict：`{status, source, top5_concepts[], breadth, note}`，`schemas.py:1062`）；`market_theme`（真值 `MarketThemeContext`，仅 ENABLED=1 时填，影子期恒 `None`，`schemas.py:1069`）。Step 2.5 构造块（orchestrator）与 Node 6 消费块（aggregator）读同一对开关，无独立构造开关 |
| **判据面** | ① 影子日 `market_theme_shadow` present 且 `status` 非 None（取数失败回退 `unknown` 属正常读数，不得记缺陷）；② `market_theme` 恒 `null`（ENABLED=0 下非 null 即违约——把未生效数据伪装成已接入）；③ `source` 字段可追溯排名源（探测结论：mootdx 自算，见 §7） |
| **转正门槛** | **未定，归负责人**（本线零决策改动，转正语义 = 开 `GOT_MARKET_THEME_ENABLED` 使 `overall_confidence` 吃一致性因子，属决策面变更，须先有影子期一致性观测材料；**非空可数日 8 天（0928–1014），不足 10**，语义一致性判定亦未做） |

**既有读数锚点**（2026-10-15 只读抽验 11 归档日实测）：**非空可数日 = 8 天（0928→1014：0928/0929/0930/1008/1009/1012/1013/1014），全部 `status="ok"`**，`market_theme` 真值全 `null`（判据② 无违约）；0922–0924 上线前无产物（`None`）属预期，判据①「影子日 present」以 0928 起算（10-11 复核注口径）。另有 10-11 复核注已订正「0928 产物不可用」旧记载；TickFlow 探测节（§7）确认排名源 = mootdx 自算而非 TickFlow API。

---

## ‼️ 2026-10-08 状态变更（个股级结构化事件退出催化剂日历）

**背景**：1008 全流程实测中财报披露通道「死而复生」（0825–0930 因 `stock_yjyg_em` 取数失真恒为零，六周下游无一处报缺）；当日 7 条 `akshare_earnings`（direction=drift）挤占 22 格日历，飞龙股份一只扇出 4 板块——与 WI-P2-4 移除解禁的三条理由（层次错配/稀释密度/无规模信息）逐字同案。

**裁决（负责人 2026-10-08）**：财报披露与解禁同样不入催化剂日历；个股级财报/减持/解禁信息走基本面 bundle → RiskAgent（仅持仓与推荐票）。

**实施**：`catalyst_calendar._full_build` 不再取数/映射/聚合个股事件，退役函数 9 个（`_fetch_earnings_calendar` / `_fetch_lockup_calendar` / `_get_relevant_quarters` / `_map_structured_event(_fast)` / `_build_structured_events` / `_map_stock_to_sectors` / `_batch_map_stocks` / `_fetch_stock_boards` / `_aggregate_structured_events`，净 −332 行）；全量构建日志改为 `macro/global/narrative（个股级不入历）`，消除 `lockup=88` 只取数被误读为入历的问题。

**待验证判据**（本条只登记事实，达标与否由负责人判定）：
- 存量 7 条 earnings（`expires_at=2026-10-13`）已经负责人指令**当日直接删除**（1008，连带 `catalyst_realizations` 7 条注册行，脚本 `.review_tmp/del_earnings_rows_1008.py`）；删后 DB 仅剩 macro_rule/global_map/llm_narrative 三源。**次一交易日（1009）即增量日，DB 已无个股存量，明日起日历不应再出现任何 akshare_\* 条目**（无需等到 ~10-14 下次全量重建）。
- 验收：每日 `news.json → catalyst_calendar.events` 中 `source.startswith('akshare_')` 计数 = 0；若再现即为旁路复活。
- **连续通过登记（2026-10-15 补记）**：1009 起算的验收读数实测 **10-13 = 0/62、10-14 = 0/13**（62/13 为当日日历事件总数，`akshare_*` 均为 0）——个股级退日历判据连续两交易日通过，无旁路复活迹象；判据继续按日生效。
- 单测锚点：`tests/test_catalyst_calendar_stock_events.py`（5 例，含「增量日仍带存量」的反向登记——该例测的是桩内 DB 存量语义，与本次直接删库不矛盾）；消费侧回归 `test_node6_glue / test_node6_ambush_funnel / test_ambush_relations_wiring / test_lockup_risk_events` 共 67 例绿。

---

## ‼️ 2026-10-10 补登（买入侧三道新闸门：MEG / FTS / FXG 影子判据入本清单）

**背景**：三者此前只活在 [`docs/CHANGELOG.md`](../CHANGELOG.md) `[Unreleased]` 与 [`plans/加速末期识别缺陷分析_20261008.md`](plans/%E5%8A%A0%E9%80%9F%E6%9C%AB%E6%9C%9F%E8%AF%86%E5%88%AB%E7%BC%BA%E9%99%B7%E5%88%86%E6%9E%90_20261008.md) 里，未进本清单——影子期无人登记判据等于无人管。本节只登记**判据与产物位置**，达标与否归负责人（遵 §0 收录范围）。

| 闸门 | 开关与缺省 | 影子产物 | 判据（读数工具） | 转正门槛 |
|---|---|---|---|---|
| **MEG 动能衰竭门**（acceleration 内动量断裂压制 `buy_trend`） | **仅 4 个真开关**：`GOT_MOM_EXHAUST_GATE_ENABLED=0`（缺省关）/ `GOT_MOM_EXHAUST_MODE=shadow` / `GOT_MOM_EXHAUST_DECAY=-0.30` / `GOT_MOM_EXHAUST_SIZING=1`。⚠️ 设计稿 §7.6 列的 `_PEAK_DD` / `_CONFIRM` **代码未实装**（`sector_analysis._compute_momentum_state` 只算 `decay_2d ≤ 阈值 → snap`，无 peak_dd OR 分支、无连续确认日数），不可当可调参数登记 | `data/got_daily/{date}/shadow_meg.json`（`evaluated` / `flagged_stocks` / 层3 sizing） | `scripts/meg_shadow_report.py`：① 命中率（逐日+累计 `action_hits/sizing_hits/hit_rate` + 被拦标的/板块频次）② `--forward N` 假阳性（拦截股 N 日前向收益 ≤0 为正确拦截，>0 为误杀） | 观测 ≥1 周后由负责人判读命中率与假阳性率再切 `MODE=veto`；回测证真基线（§7.8.1）：snap−thrust T+5 超额 −2.54%（Welch t=−3.28、p=0.001）、假阳性 12%。⚠️ **影子所验的不是设计稿完整版**：`decay_2d≤阈值` 单一判据之外，§7.3 的 `peak_dd` OR 分支与 `_CONFIRM` 连续确认均未实装也未回测 ⇒ 转正时授权范围应与影子实际验过的那一版对齐，不得把设计稿当成已验证对象 |
| **FTS 家族见顶持续压制门**（跨日 latch，`buy→watch` + 仓位 sizing） | `GOT_FAMILY_TOPPING_GATE_ENABLED=0` / `GOT_FAMILY_TOPPING_MODE=shadow` / `GOT_FAMILY_TOPPING_SIZING=1` / `GOT_FAMILY_TOPPING_COOLDOWN=5` / `GOT_FAMILY_TOPPING_EXIT_HEALTHY=2` / `GOT_FAMILY_TOPPING_AGGRAVATED_MIN_CONFIRMS=1`（子参变量名**不含 `_GATE` 中段**，按上列全名为准） | `data/got_daily/{date}/shadow_fts.json`（含 `suppressed_families` 台账与 `fxg` 子块） | 逐日读 `aggravated` / `days_suppressed` / `days_healthy` 与 `flagged_stocks`；**层A `sector_exposure_limits` 历史上无执行消费者**（FXG 根因，见下行），读数时不得把它当已生效防线 | 同归负责人判读；`89062d3`/`d5bf3ca` 为正确性前置修复（aggravated 语义确认 + 分级塌缩修复），**修复前读数不可作标定输入** |
| **FXG 家族级见顶上游排除闸门**（在 `actionable_names` 构造阶段真剔池） | `GOT_FAMILY_TOPPING_EXCLUDE_ENABLED=0`（需父 `GOT_FAMILY_TOPPING_GATE_ENABLED=1`）/ `GOT_FAMILY_TOPPING_EXCLUDE_MODE=shadow` / `GOT_FAMILY_TOPPING_EXCLUDE_MIN_DAYS=2` / `GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW=0` | `shadow_fts.json` 的 `fxg` 子块 + `candidate_pool.json`；`EXCLUDE_BASELINE_SHADOW=1` 且 `EXCLUDE_MODE=veto` 时另落 `fxg_baseline_scoring.json`（剔前原模式基线）。⚠️ **对照是自建的、不缺**：基线由 `EXCLUDE_BASELINE_SHADOW=1` 与 veto **同一次跑内双跑**产生（veto 真剔池 + 独立实例再跑一遗 FXG-off 的原模式评分），所以下表「基线 0 份」只意味着**部署后尚未有过全流程实跑**，不意味着「开了却没有对照」 | `scripts/fxg_pair_report.py`：主口径=家族/板块池成员差集（①见顶家族被删 ②补位者），③前向 T+5/T+10 best-effort（取不到即 N/A） | **已拍 veto（2026-10-09~10 负责人裁定）**：切 veto 后连续 ≥1 周用 `fxg_pair_report.py` 证真**补位净正/中性**方可长期保留，否则回退 shadow。**取向已定：踏空优于接盘**（负责人原话「丢收益总比接盘亏要好」）⇒ 「回退 shadow」只在看中证伪后触发，**不因「暂无读数」提前回退**（无读数不等于被证伪） |

**读数起算与局限（必读）**：
1. **影子读数的实际起算日（2026-10-10 实测更正；本条原写「三闸门读数均自 2026-10-09 部署日起算」不成立）**：扫 `data/got_daily/` 实测可数产物 = **MEG 1 天 / FTS 0 天 / FXG 0 天**（`shadow_meg.json` 仅 20261009；`shadow_fts.json` 与 `fxg_baseline_scoring.json` **至今一份都没有**）。原因**不是「写了不跑」**：20261009 那次运行的 `meta.json` 记 `git_commit=d391345` / `run_timestamp=13:55:03` / `bar_complete=false`（盘中跑），而 FTS 合入在其后（`3947500` 10-09 15:47、前置修复 `d5bf3ca` 22:14），FXG 基线更晚（`9074d7c` 10-10 09:03）；10-10 目录**只有 `candidate_pool.json`**（无完整全流程归档）。⇒ 「观测 ≥1 周」的计时实际从**下一个全流程交易日**才开始，当前无任何可用转正读数。**〔2026-10-12 订正：该「下一个全流程交易日」已到达，`shadow_fts.json` 与 `fxg_baseline_scoring.json` 首份均落盘 ⇒ 三闸门时钟自 2026-10-12 起算，本行原读数作废，见顶部节〕**
   ⚠️ **但「盘中跑」不是缺陷，转正判读不得要求改成收盘跑**（上一版本节把 `bar_complete=false` 当成口径问题，已订正）：项目约定为**盘中（约 13:30）执行**，收盘跑是补跑/测试例外——`archive._is_bar_complete` docstring 自述「标记**收盘后补跑版**（如 0910 17:51）…与 **13:30 节奏配合**」，audit_20260922 Q4 亦裁定「降档标记 + `bar_complete` 接 P6」（产物侧已有 `TradeAllocation.intraday_snapshot` 与「⚠️ 盘中快照警告」）。⇒ **判读口径：按 `meta.json.bar_complete` 分组，盘中日作生产主口径，收盘补跑日单列不混池**。
   真正残留的口径风险**不在时点而在量纲**：MEG 的 `decay_2d = (ema[t] - ema[t-2]) / ema[t-2]` 中当日项 `rrg.rs_momentum`（`sector_analysis.py:1590` 起）在盘中是**未定版值**，与 §7.8.1 回测所用的收盘日线不同构 ⇒ 影子期读数与回测读数不能直接对拍；修法属【架构计划】WI-P2-8（per-source availability，把「当日 bar 未走完」下沉为逐源 gate），不是调运行时点。
2. `--got-replay` **不经过 Phase 2**，三闸门读数只能从实跑交易日归档累；非交易日 `main.py --got` 直接抛错。
3. FXG 基线口径局限（脚本 docstring 已自述）：基线=**评分阶段** vs veto=**分配阶段**口径有别，且家族差集混入当日榜单自然漂移。
4. MEG 回测仅覆盖 2026-05~10 单一下跌/轮动 regime（无牛市样本）；设计稿 §7.3 的 `peak_dd ≤ −0.40` OR 分支与 `_CONFIRM` 连续确认**均未实装也未回测**，影子期无法观测这两条——若转正评审需要它们，得先实装再重跑影子。`stall`(−2.80%) 与 `snap`(−3.07%) 均值超额接近，“stall 降仓 + snap 硬降”两级门属后续校准方向。
5. 当前 `.env` 实态（**2026-10-10 grep 复核与下表一致**；仅登记口径，不作达标判定）：MEG `ENABLED=1 + MODE=shadow + SIZING=1`、FTS `GATE=1 + MODE=shadow`、FXG `EXCLUDE_ENABLED=1 + MODE=veto + BASELINE_SHADOW=1`。

### 三闸门实测读数（as of 2026-10-10，只读复算；工具已跑过，缺的是输入不是机制）

| 闸门 | 唯一可读的一天 | 读数 | 出处与工具状态 |
|---|---|---|---|
| **MEG** | 20261009 | 评估 **5 只**候选，层2 命中 **0**、层3 缩仓 **0** 板块 ⇒ **无正例**，命中率与假阳性均不可判（≠“命中为 0 所以门无效”；回测基线的 snap 占比本就低，且当日为盘中跑、当日 EMA 项未定版） | `data/got_daily/20261009/shadow_meg.json`；`scripts/meg_shadow_report.py` 跑通，输出同结论（累计 5 / 0 / 0.0%） |
| **FTS** | 无 | `shadow_fts.json` 0 份；`unified.family_suppression` 1008/1009 均 `None`（当时跑在 FTS 合入前）⇒ 台账零读数。但**靶子常在**：`mainline_endurance` 的 `topping_risk` 档 1008=「无归属」(wave 3)、1009=「医药」(wave 4) ⇒ 部署生效后的首日就有触发候选，不是“可能永远不打中” | `data/got_daily/{20261008,20261009}/unified.json` |
| **FXG** | 无 | 基线文件 0 份⇒对照尚未产生（机制上由 `EXCLUDE_BASELINE_SHADOW=1` 与 veto 同跑自动产出）；`scripts/fxg_pair_report.py` 跑通并如实报「未找到含 `fxg_baseline_scoring.json` 的交易日」 | 脚本输出（工具健康，缺的是输入） |

> 三行共同结论：**零读数 ≠ 零机制，也 ≠ 证伪**。唯一有意义的时钟是「自三闸门代码均部署完毕后的第一个全流程交易日起算 ≥1 周」（生产节奏为盘中约 13:30 跑，不需也不建议改跑收盘）；读数时按 `meta.json.bar_complete` 分组，盘中日作主口径、收盘补跑日单列。

---

## ‼️ 2026-10-11 补登（外网对标实施计划：项一/项二/项三 影子判据与本清单登记）

**背景**：[`外网对标实施计划_20261011.md`](plans/外网对标实施计划_20261011.md)（原根目录 `外网对标实施_PLAN.md`，2026-10-14 治理轮移入 plans/）六项拍板均为推荐方案（§7）。本节登记其中需长期影子观察的两项判据与一项数据迁移状态；达标与否归负责人（遵 §0 收录范围）。

| 项 | 开关与缺省 | 产物/观测位置 | 转正门槛（采纳 §7 建议值） | 当前状态 |
|---|---|---|---|---|
| **项一：埋伏候选事件→板块映射盲区**（修法 ②+②b） | ②（口径归一 + 扇出）**受 `GOT_MACRO_FANOUT_ENABLED=false` 门控**（审查 W1 修正：原无门控会即时改宏观排序=决策面变更，现缺省关仅旧白名单 券商/银行/房地产 扇出，行为同项一前）；②b（macro 走 T5）开关 `GOT_MACRO_SECTOR_ALIGN_ENABLED=false`（缺省关） | 观测字段 `ambush_gate_funnel.relations_supplement.legacy_miss_and_no_relations_hit`（基线现值 10/14）；macro 事件 `affected_sectors` 不再 NULL | **题材类候选零事件率 <30%**（基线 ~100%）且 `still_missed` 较基线**降 ≥50%**，连续 ≥10 交易日 | ⚙ 两开关均缺省关（严格影子先行）；翻 GOT_MACRO_FANOUT_ENABLED=true 后待全流程实跑产读数。⚠️ **门槛标定必须等项二 direction 干净化后**（已满：direction 已净化） |
| **项二：OPEN-D10 `direction` 语义净化**（修源头①） | 非影子特性，为**直接 bugfix**（无开关）：`direction` 统一价格语义，事件类型只用 `category_tag` | `scripts/migrate_direction_semantics.py`（默认 dry-run；**`--apply` 翻转即重算、幂等可二次跑修复**，审查 W3）；单测 `tests/test_node4_realization.py::TestCheckDirection` 4 例 | 两表 `direction` 取值 ∈ {{positive, negative, binary}}；direction_accuracy 口径复算 | ✅ 代码 5 处已改；迁移 **`--apply` 已执行**（catalyst_events 19 行 + catalyst_realizations 280 行→binary，重算后 direction_accuracy 27.9%→39.0%）。❗ GLOBAL_TO_A_SHARE_MAP 13 条为建议重标，已写入代码（待负责人事后复核可选调） |

### 项三：Phase 4d §4.3 AlphaSift 影子接线（3a/3b 拆分）

**3a（已实施）——funnel_stats 接进生产链路**：
- 此前 `funnel_stats` 只在 `run_got_mode` 计算/落 `data/got_screener/{date}.json`，生产 Phase 2 直调 `_extract_got_universe`+`_filter_got_finalists` 不经该路径 ⇒ `meta.json.funnel_stats` 恒缺、**D-3 基线 0 天**。
- 现于 `portfolio_decision._score_candidates` 补算 `screener._compute_funnel_stats(universe, finalists)` → 落 `PortfolioDecision.funnel_stats`（纯观测，try/except 全包）→ `archive_phase2` 回读-改-写合并进 `data/got_daily/{date}/meta.json` 的 `funnel_stats` 键（归档层零新文件）。
- **D-3 基线起算日 = 3a 部署后的下一个全流程交易日**（此前 0 天）；`meta.json.funnel_stats` 非空即视为接线成功。`test_got_funnel_stats.py` 4 绿。
- **〔2026-10-11 审查注记〕** 六归档日（0928–1010）`meta.json` 实测全无 `funnel_stats` 键 + 写入点 grep（`_score_candidates`/`archive_phase2` 全仓仅定义处命中）双证：3a 接线代码已提交（`5a4f29f`，10-11 13:54）但**生产基线实测仍 0 天**——「D-3 基线起算日 = 3a 部署后下一个全流程交易日」的计时尚未开始，≥1013 生产日首验 `meta.json.funnel_stats` 非空后才起算。取证：[`reports/got_audit_20261011.md`](../../reports/got_audit_20261011.md) §2.1（报告不入库）。**〔2026-10-12 首验通过：`meta.json.funnel_stats` 已非空（actionable 7 / 有入选 3 / 零入选率 0.571），D-3 基线起算日 = 2026-10-12，本行「≥1013 才起算」作废；读数见顶部 10-12 节〕**

**3b（未开工——受阻，需负责人拍板后另起 WI）**：
- **前置未满足（§3.2 计划自述）**：3b 前置 = 3a 完成 **+ D-3 基线攒满 ≥10 交易日**。3a 当日才接线，基线 0 天，影子转正窗口尚未起算。
- **接口缺口（本轮代码核验发现，与计划描述不符）**：`AlphaSiftService.screen(*, strategy, market, max_results)`（`src/services/alphasift_service.py:384`）——① **无 `context`/`sector_filter` 入参**（计划 §3.2 设想的 `screen(strategy=auto, context={sector_filter: actionable_sectors})` 不存在）；② 硬闸 `_ensure_alphasift_enabled`——`ALPHASIFT_ENABLED=false`（生产缺省）时直接抛 403，与「ALPHASIFT_SHADOW=true 跑但不生效」的真影子语义冲突；③ 失败抛 `HTTPException` 而非返回 `{error}`，旁挂需全包裹兜底；④ **P6 ground-truth join** 需给 `catalyst_realizations` 加 `source: primary|alphasift_shadow` 维度且从**板块级**兑现追踪扩到**个股级** PEAD 多窗口收益——属新子系统，非小改。
- **结论**：3b 非「直接实施」可得，需先就上述 ①②④ 三项做设计拍板（影子如何绕过 enabled 闸、sector_filter 从何注入、个股级兑现追踪是否复用现基建），并待 D-3 基线满 10 日。当前保持未开工，登记备查。

### 项四：竞品经验补强计划批次 A–G

- 本体为独立计划文档 [`docs/got/plans/竞品经验补强计划_20261006.md`](plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md)，已过 UltraReview、批准序 D0→D→G→A→B→C2→C1→E1→E3→E2→F。**逐批实施逐批验**（每批独立 WI/commit），不并入本外网对标实施计划的单次改动。本清单不代判，进度归该计划自身登记面。

---

## ‼️ 2026-10-11 补登（竞品经验补强计划：批次 D0/D/G/A/B 影子判据入本清单）

**背景**：[`竞品经验补强计划_20261006.md`](plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md) 已批准全量按序实施（外网对标计划 §7 拍板 5）。本轮已落 **D0→D→G→A→B**（均为影子/观测先行、零决策面变化）；判据登记如下，达标归负责人。

| 批次 | 开关与缺省 | 影子产物 | 转正门槛 | 当前状态 |
|---|---|---|---|---|
| **D0** 个股当日涨幅批量取数 | 无开关（`TencentFetcher.get_stock_quick_change` 为取数原语） | —（供 D 消费） | — | 已落，5 单测 | 
| **D** news_pulse 持仓异动归因 | `GOT_NEWS_PULSE_ENABLED=0`（缺省**关**）/ `GOT_NEWS_PULSE_THRESHOLD=7.0` / `GOT_NEWS_PULSE_MAX_STOCKS=3` | `data/got_daily/{date}/news_pulse.json` | 先手动跑一周验输出质量（归因表含排除项/性质四选一/盘中收盘两口径）；推送接线另立子项 | 代码+测试就位（6 例），默认关未跑 |
| **G** output_review 产出自查 | `GOT_OUTPUT_REVIEW=1`（缺省**开**，warn 态零行为） | `data/got_daily/{date}/review.json` | 一期只 warn；critical 项升为**阻断**需两周零误报人工确认（且阻断只拦推送/终端，归档照常） | 已落（一期 warn），10 例 |
| **A** 证据链与出处 | 无独立总开关；一期 `EVIDENCE_TIER_MULTIPLIER` confirmed==reported==1.0（**零行为变化**）；Node4 抽取 prompt 的 url/时间接地受 `GOT_NEWS_EVIDENCE_GATING=false` 门控（审查 W2：开启会改 LLM 输入，缺省关=prompt 逐字同改造前） | `catalyst_events.evidence_tier/source_url` + `node4_raw_news.url/published_at` 列（已实 DB 迁移） | 二期启用 rumor<1.0 折扣前，先历史归档重放 pool 成员对比 + 人工确认（防换池）；开接地前先同输入 golden 抽取对照 | 已落（催化剂侧 tier+乘数零变化 + 新闻侧回连机制，默认关），13 例；rumor 未标→零变化 |
| **B** antibias 反偏见 | `GOT_ANTIBIAS_SHADOW=true`（缺省**开**，只观测）/ `GOT_ANTIBIAS_MAX_SECTORS=8` | `data/got_daily/{date}/shadow_antibias.json` | 一期不进排序/warnings；B2 转正前置：glue status=ok 非降级/回放日 + 影子≥10 交易日 + counter_thesis 非空率>80%。**〔10-12 实测补条件：`_collect_actionable_sectors` 只取 `is_holding` 或 action∈(buy_trend,buy_dip,hold)；10-12 三个候选全 `watch` 且空仓 ⇒ 函数 `return None`、零产物零告警 ⇒ 「≥10 交易日」必须只计「有买入/持有标的」的日，否则计时器不动（backlog §9 #7）〕**〔10-15 采样口径注记：产物侧 `{skipped_reason: no_actionable_target}` 落行仍未实施（全仓零命中），在补码前，空仓/全 watch 日「文件缺失 = 无样本日不计」的读法成立；样本侧不足类冲突按 3b 门槛先例用合成上下文补测，不得拿单一行情态样本当全量〕 | 已落，6 例；与 G 的红线不一致校准 warning 待二期接（需跨产物读数） |
| **C1** 双源交叉验证 | `GOT_CONSISTENCY_SAMPLE=10`（缺省 10，**0=关**）/ `GOT_CONSISTENCY_TOL=1.0` | `data/got_daily/{date}/consistency.json` | 不改任何决策输入；二期可接推送/告警 | 已落（源A=manager 实时 / 源B=腾讯批量 D0，比当日 change_pct；资金无二源→诚实标注），10 例 |
| **C2** 全局 is_stale | 无开关（`src/got/data_freshness.py`） | —（工具） | — | 已抽为唯一权威实现，capital_analysis 改为引用（禁二口径）+ 扩识别英文 `date` 列；**Node 1/2/5 行情消费点接入为待办子项** |
| **E1** 网络预检 | `GOT_NET_PREFLIGHT=true`（缺省开，纯观测） | `meta.json.net_preflight`（回读-改-写） | 不据此降级；lite 自动降级延后到 lite/full 历史差异读数后 | 已落（GET 探活4域），4 例；**lite 手动/自动降级为独立子项（未实现）** |
| **E3** 逐股无缓存点提速 | 无开关（`manager.get_belong_boards` 当日缓存） | — | — | 已落（get_belong_boards 当日滚动缓存，仅缓存非空，跨日失效）+ `NewsEvent.source_type` 死枚举对齐；6 例 |
| **F** 回测纪律 | 无运行期开关（`GOT_SCORING_FORMULA_VERSION` 仅调参时 bump） | `run_card.json`（离线）/ meta.json `scoring_formula_version` | — | 已落（`src/got/backtest_discipline.py` 纯函数 + `scripts/backtest_standard.py` 强制卡片 + F3 版本戳入归档）；5 例 |

**关键实现决策（与计划原文的偏差，诚实登记）**：
- **B 落点**：计划写“SectorIntel 新字段”，但 SectorIntel 属 Node 6、其 `unified.json` 在 Phase 1 已归档（早于 Phase 2），Phase 2 回填不会持久化 ⇒ 改用与 `shadow_t1/meg/fts` 同构的**独立影子产物**（零 schema 改动、避免死字段）。
- **A 一期零变化**：计划 §A2 字面“llm_narrative/global 缺省 rumor”会即时折扣叙事事件；但 news 侧逐事件 tier 接地未完（依赖 LLM 真实产出），故未接地时统一归 reported（==1.0），**保证一期零决策变化**（与 §A4“乘数一期零变化”断言一致）；rumor 标注待二期接地后生效。
- 本批新增环境变量（D/G/B + 外网对标项一 GOT_MACRO_SECTOR_ALIGN_ENABLED）已集中定义于 `catalyst_config.py` 并同步 `.env.example`（G4）。

---

## ‼️ 2026-09-17 状态变更（A5 影子期转正 → A6 阶段 1 接线）

**项目负责人裁决**：8 交易日影子期实测相对规则侧 M1 +25.5 pp / 13.1×、M6 +31.4 pp，M4-b/c 7/7 PASS；**直接转正**。

**技术定义**（与 T5 前车之鉴不同，本次不是翻开关）：
- 影子期 glue 产出零消费（`grep 'glue\.(relations|strengths|families)' src/` = 0 命中），**转正 = 实施 A6 接线代码**。
- 0915 当日 `sector.frame_groups=0` → glue `S2_parse_failed`，属 Node 2 上游服务器故障，**不计入降级样本**。

**两阶段实施**（本次仅完成阶段 1）：
- **阶段 1（本次）**：接线。新增 `GOT_A6_ENABLED` 开关（默认 true），将 glue 输出接入 4 个消费点：`_build_sector_intel` Pass 1.5 直接替换三件套 / `refine_events_map` 走 `_glue_aware_events_map` / `_llm_refine_sectors` 跳过 adjustments / `_synthesize_mainline_endurance` 三级优先取 family + E2 公式改 P90 自适应。归档 `unified.json` 新增 `glue_consumed` + `glue_status` 字段。
- **阶段 2（3–5 交易日观察后单独 commit）**：退役 `agent_glue_plan_0903.md` §6.1 的 17 项 + `SectorIntel.family_resonance` 字段 + `_llm_refine_sectors.adjustments` 分支代码本体。

**回滚成本**：`GOT_A6_ENABLED=false` 一个环境变量即回到影子期行为（glue 归档不消费），零代码回退。

**行为面验收锚点**：`tests/test_a6_wiring.py::TestC3ShadowPeriodEquivalence`（C3）—— A6=false 与影子期基线逐字段一致，替代原 R-C7。

下文 §1–§6 保留 09-14 快照内容作为历史基线，便于阶段 2 退役前的行为对比。

---

## 0. 收录范围（2026-09-14 明确）

**本清单只收「改动已落地、缺的是证据」的项** —— 具体是这五类证据：

| 缺的证据 | 对应项 |
|---|---|
| 影子期样本天数（≥10 交易日） | A5 glue |
| 修复前后两组归档的回放对 | R-B3 / R-B8 / R-B9 / R-C7 |
| 同日两跑的两个快照 | R1 / R3 / R6 |
| 人工抽查 | M3（误配率）——**2026-10-06 已结案转正**，此后转日常预警 |
| 时间窗 | OPEN-C1（`transition_count` 时间炸弹）、Phase 5b 显著性校准 |

**不收的四类**，一律见 [`backlog.md`](backlog.md)：

| 不收的类别 | 去哪 |
|---|---|
| 代码还没写 | backlog §1 / §2 |
| 已决定不修 / 待退役 / 改归属 / 挂起 | backlog §2（处置表） |
| 红线本身设计有缺陷 | backlog §9 |
| 文档基线过期 / 数字对不上 | backlog §8 |

> ⚠️ **本节是修订产物**：`R-D1` / `R-D2` / `R2` / `R5` 四条**曾被误列在本清单**，现已移出。
> 误列原因：它们看起来也是「红线验收状态」，但读数**完全由未实施工作或文档缺陷决定**，不是验收红线。
> 判定标准（新增）：**一条验收红线只能量已实施的东西**；若它的读数由未实施工作决定，它是 burndown chart 而非验收红线。
> 移出后的处置见 [`backlog.md`](backlog.md) §2 / §8 / §9。

---

## 1. 影子期总览：三条影子线的可数性差异

> **机制澄清（2026-09-14 实测）**：三条线都是 `*_ENABLED`（默认 `false`）+ `*_SHADOW`（默认 `true`）的双开关形状，
> 但 **`*_SHADOW` 在 `*_ENABLED=false` 时是失效的**——它的语义是「跑了之后不生效」，不负责让功能跑起来。
> 因此三条线的差异只由一个变量决定：**`*_ENABLED` 的实际取值**。

| 影子线 | 门（代码位置） | 实跑 `ENABLED` | 归档产物 | 当前可数性 |
|---|---|---|---|---|
| **A5 glue 语义粘合层** | `src/got/nodes/aggregator.py:985`<br>`if not AGENT_SEMANTIC_GLUE_ENABLED: → S6_disabled; return` | **true**<br>（0910/0914 产物 `status=ok`，不可能是开关关闭的形状） | `data/got_daily/{date}/agent_glue.json` | ✅ **可数，但只有 5 天**（7 份文件中 2 份是测试污染，见 §2.2） |
| **T1 候选股终审** | `src/services/stock_screener.py:1622`<br>`if AGENT_STOCK_REVIEW_ENABLED and finalists:` | **true** | **`shadow_t1.json`**（2026-09-15 起）；之前产物被 Phase 2 路径丢弃，仅日志可查 | 🟡 **4 天日志（0908/0909/0910/0914）+ 1 天归档（0915 起）** |
| **T1-H 持仓审查** | `src/got/nodes/portfolio_decision.py:2162`<br>`if not AGENT_HOLDING_REVIEW_ENABLED or not holdings: return holdings` | **true** | **`shadow_t1h.json`**（2026-09-15 起） | 🟡 **1 天归档（0915，未执行：持仓不满足三类筛选条件）** |

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

> 要产出 T1 归档，需另跑 `scripts/run_got_screener_v2.py`，或让 Phase 2 路径也调用 `_save_got_result`。已登记 [`backlog.md`](backlog.md) §1.C。

### 1.3 T1-H：开关生效，但筛不到审查目标（2026-09-14 实测）

`AGENT_HOLDING_REVIEW_ENABLED=true` 下，日志**无任何 `[T1-H]` 行**，`portfolio_decision.json` 的 `unified_scoring[]` 9 条**全部 `agent_review_verdict = null`**。

**不是开关失效**——是 `_select_holding_review_targets`（`portfolio_decision.py:2285-2311`）只挑三类目标：
① 埋伏持仓（`ambush_position_type` 非空）② 有 P4 退出信号 ③ `composite_score < 40`。

当日唯一持仓 `600250` 的 `composite_score = 50.1`，三类**全不满足** ⇒ `review_targets` 为空 ⇒ 走 `logger.debug` 静默返回（`:2170`）。

⚠️ **这修正了 §1.1 末尾「只要 `ENABLED=true` 跑一天，结论即落进现有归档」的结论**：
开关只是**必要条件**，还须**持仓命中上述三类条件之一**。而优先级①长期不可用——`ambush_position_snapshots` 恒 0 行（见 [`backlog.md`](backlog.md) §1.A）。⇒ T1-H 的实际可数性取决于**持仓分数分布**，比原判断更受限。

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

转正条件（`archive/agent_glue_plan_0903.md` §8.2）要求影子期 **≥10 个交易日**且双达标。
**是否达标由负责人判定**，本清单只登记上述计数。

### T1/T1-H 影子产物（2026-09-15 起）

T1 与 T1-H 在 2026-09-15 开始各自产出独立的影子产物文件，与 A5 的 `agent_glue.json` 对称：

- **`shadow_t1.json`**：T1 候选股终审的影子观测记录（审查了几只、否决了谁、理由是什么）
- **`shadow_t1h.json`**：T1-H 持仓审查的影子观测记录（审了几只持仓、每条 verdict 是什么）

两份产物由 `src/got/nodes/portfolio_decision.py` 的 `_save_shadow_records()` 落盘到 `data/got_daily/{date}/`，供 `src/got/archive.py` 的 `_render_shadow_section()` 读取并渲染到 `reasoning.md` 末尾的「🧪 影子模式观测」段落。

**设计要点**：
- 零 schema 改动（不改 `schemas.py`，不影响 R-B8/R-C7 的逐字段 diff）
- 重跑不堆积（`_append_phase2_reasoning()` 的覆盖写机制天然处理，影子段随 Node 7 段一并重建）
- "未执行"必带原因（开关关闭 / 无可审目标 / 具体哪只持仓不满足哪条筛选条件）

### bear gate 拦单留痕产物（2026-10-10 起）

工作流 B（bear 期入场闸门，`GOT_BEAR_ENTRY_GATE`，缺省 on）在 `_score_candidates` 拦单时新增逐单留痕：

- **`shadow_bear_gate.json`**：命中闸门的候选逐单 dict `{symbol, name, sector, pool_source, composite, action_before, action_after, mode}`，汇总 `{line: "bear_gate", executed, regime, dip_mode, hits, flagged_stocks}`。**〔2026-10-12 更正：原写「无命中日落 `{"executed": false}`」与实现不符——`_save_shadow_records` 走 `self._bear_gate_shadow or None`，「仅命中时落盘」，无命中日不产文件且同日多轮 `_score_candidates` 只保留最后一轮，命中会被后一轮的重置抹掉（10-12 实测：10:02 命中 1 单 → 归档无产物）。判据随 backlog §1.H 的修复一并重定〕**
- 由 `_save_shadow_records()` 第 5 项落盘到 `data/got_daily/{date}/`，同走 `_render_shadow_section()` 渲染链。
- **性质与 T1/T1-H 影子不同**：闸门本身是生效的（硬拦/veto/soft 三档真实改写候选动作），留痕只是**观测旁挂**（闸门缺省 on、留痕非影子开关），不存在"攒读数后转正"的开关翻转。
- 归档渲染：`_render_shadow_section` 新增 `_render_shadow_bear_gate` 线（同批接线，`shadow_bear_gate.json` 缺失时降级为"当日无拦单"）。**⚠️ 该降级文案会误导向**：10-12 实测「有拦单但留痕被后一轮抹去」也会渲染成同一句「多为当日无拦单」⇒ 不得拿这句当作「当日无拦单」的证据，需回查日志 `Node1=bear 收手闸门命中 N 单`。**〔2026-10-14 渲染同步更新〕**：缺文件文案改为「缺失 = 当日未跑 Phase 2（或旧归档）」；tombstone 两态分句渲染——`executed:false` →「非 bear 日，闸门未激活」，`executed:true`+`hits:0` →「bear 日闸门开启、当日 0 拦单」，命中行并列 Node1/Node6 双 regime 口径。

**〔2026-10-14 修复（backlog §1.H 收口），口径重定如下〕**

- **tombstone**：`_score_candidates` 末尾**无条件**组装汇总 dict——0 命中轮写 `{"executed": false, "hits": 0, "flagged_stocks": []}`，`_save_shadow_records` 对 bear_gate 行改判「dict 必落」⇒ **0 命中日有 tombstone、不再盲日**；旧命中文件在 0 命中日被覆盖，stale 归因歧义消除。`executed` 语义改为「闸门条件是否激活」（Node1 raw=bear 日 true / 其余 false），0 命中日 `executed:false` ≠ 闸门关闭，勿混淆。
- **regime 双字段**：新增 `node1_regime`（触发口径，闸门只认 Node1 raw regime）；原 `regime` 保留为 Node6 unified 展示口径（10-14 实测 Node1=bear / Node6=transition 并存属正常，非误标）。
- **基线侧盲区收口**：FXG veto 双跑的基线实例（`orchestrator._maybe_capture_fxg_baseline` 独立实例）不走 `run()/_save_shadow_records`，其 bear gate 拦单观测**随 `fxg_baseline_scoring.json` 以 `bear_gate` 子块落盘**（tombstone 契约下真跑必为 dict；桩测未组装为 null）。10-14 之前该实例拦单只进日志、归档零痕迹（1014 `fxg_baseline_scoring.json` 4 行全 watch 是其唯一间接痕迹）。
- **历史归因更正（2026-10-14 复核）**：10-12「上午命中被下午轮抹掉」系误诊——当日 09:49 run 为单次调用，10:02 命中 1 单出自**基线实例**（独立实例、永不落盘），10:06 真实轮 0 命中 ⇒ 无产物；「每实例内多轮覆盖」在生产拓扑（每实例单次调用）下不存在，同日重跑覆盖仅在「后轮也有命中」时发生。
- 单测：`tests/test_bear_entry_gate.py` 新增 5 例（tombstone 组装/命中携带/双 regime 口径/stale 覆盖落盘 + 基线子块 2 例）；`tests/test_fxg_veto_baseline_pair.py` 集成冒烟补 `bear_gate` 子块端到端断言。conda 与 .venv 双 101 passed（四套：bear_entry_gate/fxg_veto_baseline_pair/family_topping_suppression/shadow_reasoning）。

**〔2026-10-14 归档目录日期口径贯通（backlog §7 #12 修法①·实例态变体）〕**：`_save_shadow_records` / `_save_candidate_pool_snapshot` 两写入点的 `day_dir` 改读 `run(as_of=...)` 贯通的 `self._as_of`（兜底 `date.today()`），`candidate_pool.json` 的 `record["date"]` 同步——归档目录跟业务日走，带外跑历史日/重放 harness 不再错落墙钟日目录（20261010 孤儿归档根因闭环）；生产当日 `as_of==today()` 零行为变化，缺省兜底保未跑 `run()` 的直调（基线实例）逐字节不变。测试：`test_fxg_veto_baseline_pair.py` 新增 `TestSaveSnapshotAsOf` 三例；双环境 104 passed。

详见 `tests/test_shadow_reasoning.py` 的 11 个测试用例。

### GATE-1 tradability 影子留痕（2026-10-11 起）

GATE-1（卖出可成交性投影层，`GOT_TRADABILITY_GATE`，缺省 off）在 `_allocate` 对每张卖单/买入候选做可成交性检查：

- **`portfolio_decision.json` 的 `tradability_shadow` 字段**：`{mode, pending_exits, note}`；shadow 下 `pending_exits` 为被拦截项清单（symbol/name/reason/intent/quantity），无拦截时字段为 null（mode 记录在 note 语义外，读数时 mode 仅为确认开关态）。
- **allocation 级 `pending_exit`/`blocked_reason`**：被拦截卖单打标（enabled 顺延清单同记）。
- **读数方式**：读当日 `data/got_daily/{date}/portfolio_decision.json` 的 `tradability_shadow` 字段与各 allocation 的 `pending_exit`/`blocked_reason`。
- **转正门槛（建议，数值归负责人）**：影子期 ≥10 个交易日；期间 `quote_unavailable` 占比 <10%；无误拦截实证（被拦卖单次日实际可成交）。满足后经负责人批准切 enabled。
- **性质**：本层是三态影子开关（off→shadow→enabled，存在"攒读数后转正"翻转），与 bear gate 的"闸门生效+留痕旁挂"性质不同。
- 拦截口径（tradability.py）：跌停封板→`limit_down`（现价≤跌停价且 high 贴住→全天未开板；盘中开板视为可成交）；当日建仓→`t_plus_1`；涨停一字→`limit_up`（buy 方向）。
- 降级红线：行情缺失/超时/异常一律放行不拦截，仅留 `quote_unavailable` 告警标签。
- 单测：`tests/test_gate1_tradability_wiring.py`（**26** 个用例：三态行为 + 降级红线；本行原记 17 个，2026-10-12 `--collect-only` 实测为 26）。
- **首日读数（2026-10-12）**：`portfolio_decision.json` 的 `tradability_shadow` = `{mode: "shadow", pending_exits: []}` ⇒ 接线成功；**但账户空仓、当日无卖单 ⇒ `quote_unavailable` 占比与「误拦截」两条门槛无样本**，需等有卖单的交易日才能累。详见顶部节。

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

> 出处：`archive/agent_glue_plan_0903.md` §8.1 / §8.2；实测值与口径修正见 `archive/got_layer_c_plan_0911.md` §0 / §1。
> **本节只抄录，不判定。**
>
> **【裁决（2026-10-14，负责人）】M1–M6 不再作为一个整体**——glue 已于 2026-09-17 A6 转正在线，六条红线当初捆绑的前提（「影子期能否转正」）已消失，按各自性质分流：
> - **M3** ✅ 已结案（2026-10-06）→ 转日常预警；
> - **M1 / M5 / M6** → 作为**已上线层的水位观测**继续（`scripts/verify_glue_metrics.py` 每日零成本读数、不产 PASS/FAIL、不门禁）；红线是否重标、何时收口归负责人；
> - **M2 + M4-a** → 🔒 **随 C-P13 一并休眠**：两者的修正/重标定结构解都挂在 C-P13 接地管线——M2 的 variant_b 只是把「覆盖不足」转嫁成 M3「低置信编造」，不算独立杠杆；M4-a 红线 0.6 的 τ 标定已被 realized 收益率口径证伪关闭。解冻绑定 [`archive/cp13_phase2_closeout_20260928.md`](archive/cp13_phase2_closeout_20260928.md) §6；休眠期读数继续留存，但**不推进达标动作、不作转正依据**。

| # | 指标定义（**主口径**，C-P6 / C-P7 于 2026-09-14 落地） | 转正红线 | 实测值（0910 + 四日） | 文档给出的状态描述 |
|---|---|---|---|---|
| **M1** | **glue 侧** `agent_glue.json` 的 `strengths[].news_read` 非 `low`/`unknown`（C-P6） | ≥ 60% | **11/32 = 34.4%** | 实测 < 红线；🟢 水位观测继续（非门禁，见上 10-14 裁决） |
| **M2** | ≥1 条 `relations[]` 的候选占比 | ≥ 70% | **9/32 = 28.1%**（四日 21.1% / 13.6% / 21.9% / 28.1%） | 🔒 随 C-P13 休眠（见上裁决）；读数继续留存，不推进达标 |
| **M3** | 人工抽查误配率 | ≤ 15% | **2.8%**（分层口径 36 样本：无关系 1 / 弱关系 4 / 强关系 31，Wilson95 [0.49%, 14.17%]，连续 5 日达标）——**✅ 已结案转正（2026-10-06 负责人裁决）**：放弃 §8.1 的 `--sample-mode full` 复跑前置（分层对难点超采 72.2% vs 总体 55.4% ⇒ 2.8% 是**偏高**读数，属保守方向；按层外推全量口径 ≈2.2%，同处红线内）。残留：n=36 统计功效弱、弱关系堆积 11.1% 未处理；口径读数与结案论证见 [`plans/architecture_optimization_implementation_20260929.md`](plans/architecture_optimization_implementation_20260929.md) §11.8 | 结案（本项只结 M3；M1/M2/M4-a 仍未达，不构成任何新闸门动作——glue 已于 09-17 A6 转正在线） |
| **M4-a** | `ρ(glue.strength, tech_score)` | ≥ 0.6 | **0.591 P / 0.419 S** | 🔒 随 C-P13 休眠（红线待标定，见上裁决） |
| **M4-b** | `ρ(glue.strength, 旧 composite)`（反向指标） | < 1.0 | 0.646 P / 0.500 S | 达 |
| **M4-c** | `ρ(glue.strength, 旧 composite)`（下限） | ≥ 0.3 | 0.646 P / 0.500 S | 达 |
| **M5** | 触发 S1–S5 任一降级场景的交易日占比 | ≤ 10% | 不可度量 | 依赖 `call_type` 落库 |

**对照列 / 观察项**（C-P6 要求规则侧并列保留——**不参与转正判定**）：

| # | 指标定义 | 实测值 | 说明 |
|---|---|---|---|
| M1 对照 | 规则侧 `unified.confirmed_sectors[].news_check != "neutral"` | **1/32 = 3.1%** | 原主口径。与 M1 主口径的差值（3.1% vs 34.4%）本身就是「glue 有多大信息量」的度量 |

> ⚠️ **本节只列口径与数值，不落达标判定**——M1 是否达标由项目负责人判定（C-P8 已把 M1 标为「待标定」，C-P13 明示本轮不标定）。
> `scripts/verify_glue_metrics.py` 同样只列数值与参考线，不产生 M1 的 PASS/FAIL。

### 关键结构问题：M1 的"门禁自锁"

文档自述（`got_layer_c_plan_0911.md` §0）：M1 的**基线取自规则侧字段、红线也写在同一个规则侧字段上**，而影子期 glue「接受但不使用」该字段（`src/got/nodes/aggregator.py:1119`），规则侧字段不因 glue 而变 ⇒ **M1 恒 3.1%，结构性不可达成**。

C 层给出的修正方向是 **C-P6（M1 改 glue 侧）**——
**已于 2026-09-14 落地**：`scripts/verify_glue_metrics.py` 的主口径已切换，规则侧降为对照列。

⚠️ **落地只解除死锁，不解除「红线不可达」——这是两个独立问题**。C-P6 原文即明示
「修数据源解除死锁，但不解除『红线不可达』（M1 的 60% > 34.4%）」。
新主口径的数值见上表；**达标与否仍归项目负责人判定**，本节与脚本均不代判。

### M4 的口径陷阱（已知且已被脚本显式处理）

文档锚点 `M4-a = 0.419S` / `M4-b/c = 0.500S` **只在 `rank(method="first")` 下复现**；统计学标准平均秩给出 **0.478 / 0.564**，差 0.06–0.14。
`scripts/verify_glue_metrics.py` **同时打印两个值并声明依赖输入顺序**，未静默取舍（`archive/got_impl_review_0911.md` §R2.10）。

> ✅ **C-P8 已落地（2026-09-26，纯注记零行为变更）**：脚本顶部 docstring 新增「C-P8 红线口径声明」——
> ① **M4 判定系数固定为 Spearman**（旧 0903 §8.1 表未写明系数，Pearson 0.591 vs Spearman 0.419 会翻转达标结论）；
> ② **M4-a 的独立性以 B1 修复为条件**——B1 现状为 D-B1「只标注口径、不改数值」（**仍未修**），故实测 `ρ(tech, composite)≈0.985` 使 M4-a ≡ M4-c（共线）；
> ③ **M4-a 红线 0.6 标「待标定」**（n=32 下 Spearman 标准误 ≈0.18，0.591 的 95% 区间约 ±0.35，「0.591 不过 0.6」是一次抽样非结论）——归 C-P13。
> 本表上行 M4-a 的 `0.591 P / 0.419 S` 口径随之明确为「判定取 Spearman（first）0.419」；**达标与否仍归项目负责人**。

---

## 4. C 层验收红线 R-C1…R-C7 的当前状态

> 出处：`archive/got_layer_c_plan_0911.md` §6.1。

| # | 判据 | 达标线 | 当前状态（材料） |
|---|---|---|---|
| **R-C2** | `--got-replay 20260901` 的 glue 产物落在 `20260901/_replay/` | 路径精确匹配 | **部分**——路径约定 `{date}/_replay/` 在 **1 份**真实产物上成立（`20260903/_replay/unified.json`）；其余 2 份为**测试污染**（§2.2 / §4.1），不计为证据。`20260901` 本身无产物 |
| **R-C4** | `status=ok` 的交易日 ≥ 7 且连续，**须分列实盘 / 回填来源** | 7 天 | 实盘仅 **5 天**且缺 0908、不连续（见 §2） |
| **R-C6** | M2 × M3 在 8 天回填样本上的 A/B 对照表 | 2×8 = 16 点 | ✅ **矩阵产出 + 负责人判读完成（2026-10-14）**——两版 prompt 各回放 9 日（0901/02/03/04/07/08/09/10/14）均 `status=ok`、md5 互异，`verify_glue_metrics.py --compare variant_a variant_b` 出 **M2×M3 对照矩阵**（A 均值 M2=35.5%/低置信 6.8%，B 均值 M2=46.3%/低置信 40.8%）+ `--m3-audit` 预审表（A 无关系率 10% / B 20%，`cp12_m3_audit_20260926.md`）。**判读结论：保 A 版为默认 prompt**（现行不变更）——三条同方向证据：① **曲线非单调**属真信号（0903 B 大升 +62.5pp、但 0901 A 反赢 5.1pp、0914 A6 转正后 B 优势已削平），不能仅看均值；② **B 版有真·过度归属幻觉**（事件级复核 2026-09-27 发现 0903 房贷→ 24 板块含减肥药/短剧游戏，现有 10 行分层抽不到高扇出病态事件→**实际 M3_B 会高于 20%**）；③ **0902 A 版属生成故障**（191 条退化重复循环、max_tokens=24000 临时提至 32000(B) 重跑破坏同参前提）**整日作废**，有效对照取 8 天。**R-C6 判读结论 = prompt 单旋钮结构性无解**（M2↔M3 拉锯非数据问题而是架构问题）→ **转【C-P13】接地式召回-验证 5 层管线**（设计已录 `cp13_grounding_pipeline_research_20260926.md`，阶段 2 已于 2026-09-28 收口休眠，解冻需新证据）。本行与【§3】M2/M4-a 行同步（已隶 2026-10-14 裁决随 C-P13 休眠）。脚本仍判 SKIP：R-C6 属曲线判读非 PASS/FAIL，SKIP 为设计（D-C6），判读完成不推翻 SKIP 语义。详见 CHANGELOG 【Unreleased】本地工作 10-14 「M1–M6 解散”条与本文件末尾修订 |
| **R-C7** | C 层零行为变更项落地后同期归档逐字段不变 | 100% 一致 | ✅ **已由 `tests/test_a6_wiring.py::TestC3ShadowPeriodEquivalence`（C3）行为面单测替代结案（2026-10-14）**——C-P1…C-P10 全为零行为变更、其「生产分类分支未被触动」的语义由 C3 锁定；原始「归档回放对」在现工器下产不出（详见 §5.1 障碍 2 + backlog §7 #6a 已裁不扩链路），且 C-P 系列已合入历史、前一次归档不可重生成。CHANGELOG 205 行附近与本文件 §3.5 已记 C3 替代关系 |

> R-C6 是 `verify_glue_metrics.py` 内**唯一被判为 SKIP** 的一条（该脚本只含 R-C3 / R-C6 两条，R-C3 已归档）。前置 C-P11（回填跑批）+ C-P12（prompt A/B）**均已实施**（2026-09-26），两版各 9 日对照矩阵已产出；SKIP 不是缺数据，而是脚本不代判曲线（D-C6），达标判读归项目负责人。**【2026-10-14 判读已完成】：保 A 版为默认 prompt，结论转 C-P13 接地式架构修正（详见 §4 表 R-C6 行）**。

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
**2026-09-14 首次产出了「同日两跑」快照**（`data/_run1` / `data/_run2`），R1/R3/R4 由此**首次真算**，但两跑分别取自盘中 14:02 与盘后 23:39，属**不同条件**（见 §5.2 局限注）。**2026-10-14 于 A 股午休窗口 11:30-13:00 内 (11:32-12:02) 完成一次真正的同条件双跑**（两跑 `bar_complete` 均为 False、数据源同为午盘收盘快照、LLM prompt 版本一致），R1/R3 由此**正式结案**、R4/R5 首次给出 PASS（见 §5.2.1）。「归档回放对」经 [`backlog.md`](backlog.md) §7 裁决 #6a（2026-09-26）定案**不扩链路**，R-B3/B8/B9 依此结案、R-C7 由 C3 行为面单测替代（详见表内与 §5.1）。

| 红线 | 出处 | 判据 | 证据 | 当前状态（2026-09-14） |
|---|---|---|---|---|
| **R-B3** | `got_layer_b_plan_0911.md` §6 | 家族降权影响面有界（无单日增幅 > 100%） | 修复前后两组多日归档 | ✅ **依 backlog §7 裁决 #6a（2026-09-26）结案（2026-10-14 落笔）**——B 层修复位置在 Node 2（`family_keywords.py` + `sector_analysis.py`）、`--got-replay` 设计上不跑 Node 2 ⇒ 回放对产不出；扩链路成本/风险与收益不对等。B 层降权副作用由**生产归档自然累积 + 逐条人工比对**承接，本条**不排机械验收** |
| **R-B8** | 同上 | B1/B3/B4（零行为变更项）前后 `final_position_limit` / `composite_score` / `confirmed_sectors` 逐字段一致。**market_regime 跨日状态机 5 字段（regime/dwell_days/previous_regime/transition_count/transition_pending）已按决策 #5a（2026-09-26）移出不变组，脚本另列「按设计 SKIP」留痕不计 FAIL** | 同期单日归档对 | ✅ **依 backlog §7 裁决 #6a 结案（2026-10-14）**——同 R-B3，工器不覆盖 Node 2。零行为侧的语义保护由 `portfolio_decision.py` 回撤保护层的 §5.1 防误修清单 + 单测承接 |
| **R-B9** | 同上 | B2/B6（行为变更项）的 `classification` 与 `dimension_scores["sector"]` 变化逐条列出 | 同上 | ✅ **依 backlog §7 裁决 #6a 结案（2026-10-14）**——同上。B2/B6 已合入生产，其分类变化在 `unified.json` 逐日归档自然留痕（见 §2 归档样本行） |
| **R-C7** | `got_layer_c_plan_0911.md` §6 | C 层零行为变更项落地后归档逐字段不变 | 修复前后两组归档 | ✅ **已由 `TestC3ShadowPeriodEquivalence`（C3）行为面单测替代结案（2026-10-14）**——详见 §4 R-C7 行与本节引言段 |
| **R1** | `got_run_model_plan_0911.md` §6 | 同日两跑状态字段逐字节一致（100%） | `data/_run1` / `_run2` | ✅ **2026-10-14 同条件双跑结案**（377 字段差 5 项，全部归因数据环境差异：4 项 DB row_count/total 增长 + 1 项 sentiment_momentum 微变；`peak` 两表不变、`node4_events` 老事件零漂移、R4/R5 双 PASS——见 §5.2.1） |
| **R3** | 同上 | 事件计数不翻倍（`mention_count` / `appearance_count` 不增） | 同上 | ✅ **2026-10-14 同条件双跑结案**——4 项计数增长但 `node4_events.peak 19→19` / `node4_raw_news.peak 47→47` 都不变（幂等未坏铁证）；DB 逐事件比对 `node4_events` 共有 1774 条漂移 0、`node4_raw_news` 11381 条漂移仅 2 且跨日守卫正确工作（见 §5.2.1） |
| **R6** | 同上 | 15 个状态源（W1–W6 + D1–D9）逐项幂等复测 | 每源两跑前后各一次观测点 | **SKIP（脚本硬编码，与是否传 `--runs` 无关）**——见 §5.3 |

**2026-09-14 首次实跑复算**（两条脚本本次输出，两跑分别取 14:02 盘中 / 23:39 盘后，属**不同条件**）：

     verify_layer_b_consistency.py    → 0/3 PASS | 0 FAIL | 3 SKIP | exit 2
     verify_same_day_idempotency.py   → 0/5 PASS | 4 FAIL | 1 SKIP | exit 1   （R1/R3/R2/R5 FAIL；R6 SKIP）

**2026-10-14 同条件双跑复算**（`_run1` 11:32-11:45 / `_run2` 11:48-12:02，**全在午休窗口 11:30-13:00 内、`bar_complete=False` 一致**）：

     verify_same_day_idempotency.py   → 2/6 PASS | 3 FAIL | 1 SKIP | exit 1
       R4 PASS（EMA sample_count 18→18、daily_scores 键数 18→18）——「按日推进」代码修复生效
       R5 PASS（7 条 signal_dates 无重复）——决策 #3a 按日去重 + 30 日窗口已生效
       R1 FAIL 读数 5/377、R3 FAIL 读数 4 项计数增长——由 §5.2.1 三条铁证结案
       R2 FAIL（等下一次真实 regime 切换日，非缺陷）
       R6 SKIP（脚本硬编码，见 §5.3）
     verify_layer_b_consistency.py   → 未跑（R-B3/B8/B9 已依 §7 #6a 结案，不再依赖回放对）

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
| 3 | `unified.market_regime.*` 必然变化 | 它是**跨日状态机**，输入为 `load_regime_state_history(days=20)`；回放跑在**今天**，读到 `0817…0914`，与原归档当日的窗口不同 ⇒ 输出必然不同 | ✅ **已裁决（决策 #5a，2026-09-26）**：移出不变组，按设计 SKIP |

⚠️ **障碍 2 牵连出根本矛盾**：B 层修的是 `family_keywords.py` + `sector_analysis.py`（**Node 2** 的家族词表），
而 `--got-replay` **设计上不跑 Node 2**。⇒ **即使补齐产物，该回放也验不到 B 层修的东西**。
`got_layer_b_plan_0911.md` §6.3 写的「跑 Node 6 + Phase 2」与 B 层实际改动位置对不上。

障碍 3 的性质：`verify_same_day_idempotency.py` 的 docstring 自己写过「回放不含状态推进」，
而 `check_r_b8_r_b9` 把 `market_regime.*` **归入「应不变字段」**（仅 `classification` /
`dimension_scores` / `frame_group_lookup` 三个分支被分流出去）
⇒ **R-B8 在回放语义下不可能 PASS**。属 [`backlog.md`](backlog.md) §9 那一类「红线本身设计有缺陷」。

> ✅ **2026-09-26 已裁决并实施（决策 #5a）**：`market_regime` 5 字段移出不变组，`_collect_state_fields`
> 不再提取；`check_r_b8_r_b9` 对该子集单独比对并在 R-B8 证据行列「按设计 SKIP，不计 FAIL」。
> 零生产代码改动，仅验器脚本口径变更。

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
> 要更纯净，需在盘后连跑两次。**（注意：这是幂等测试对「同条件两跑」的要求，不是生产节奏——生产常态为盘中约 13:30 跑，不得据此建议生产改跑收盘）**
> ✅ **本局限已于 2026-10-14 由同条件双跑消除**，见 §5.2.1。

### 5.2.1 2026-10-14 同条件双跑正式结案

**取证条件**（比 §5.2 那次更纯净）：

- **Run #1**：11:32:52 启动 → 11:45 完成（`orchestrator.py:134` 打印 `bar_complete=False`）
- **Run #2**：11:48:02 启动 → 12:02 完成（`bar_complete=False`）
- 两跑均在 A 股午休窗口 11:30-13:00 内、数据源同为午盘收盘快照、LLM prompt 版本一致 ⇒ **同条件双跑**
- 快照产物 `data/_run1` / `data/_run2` 各 23 文件 / 17 MB（在 `.gitignore` 的 `/data/` 内、不入版本控制）
- 日志归档：`.review_tmp/_r13_run1.log` / `_r13_run2.log` / `_r13_verify.txt` / `_r13_dbdiff.txt`（一次性取证文件，非提交产物的长期归档位）

**脚本机械读数**（`verify_same_day_idempotency.py --runs data/_run1 data/_run2`）：

    汇总：2/6 PASS | 3 FAIL | 1 SKIP
    R1 [FAIL] 377 字段比对，差异 5：
      · db.node4_events.row_count:     1774 → 1779
      · db.node4_events.total:         2592 → 2597
      · db.node4_raw_news.row_count:   11381 → 11409
      · db.node4_raw_news.total:       15246 → 15276
      · sentiment.sentiment_momentum:  0.036 → 0.029
    R3 [FAIL] 4 项计数增长（同 R1 前 4 项）
    R4 [PASS] EMA sample_count 18→18、daily_scores 键数 18→18
    R5 [PASS] 7 条 signal_dates、7 唯一、 0 重复
    R2 [FAIL] 52 归档日 0 天 previous_regime != regime（等下一次真实切换日）
    辅助 A3-A6 [PASS]（幂等意图代码形状齐）

**DB 级逐事件比对**（`.review_tmp/_r13_dbdiff.py`，只读）：

    node4_events:     共有 1774 条, 计数漂移 = 0, 仅 B 新增 = 5, 仅 A 有 = 0
    node4_raw_news:   共有 11381 条, 计数漂移 = 2, 仅 B 新增 = 28, 仅 A 有 = 0
      漂移 2 条, 例:
        id=4438  A=19 → B=20  last_seen_B=2026-10-14
        id=9664  A=1  → B=2   last_seen_B=2026-10-14
    node4_events   peak: A=19  B=19   ← 完全不变
    node4_raw_news peak: A=47  B=47   ← 完全不变
    node4_raw_news last_seen_date='2026-10-14' 行数 A=231 B=261 差=30 = 28 新增 + 2 更新

**三条铁证**（本次给出，比 §5.2 更严格）：

1. **`peak` 两表都不变**（19→19 / 47→47）——若幂等真坏，第二跑会给已有事件再 +1，`peak` 必涨；**没涨 = 没坏**。
2. **`node4_events` 老事件 1774 条零漂移**——所有 row_count 增长都来自**新事件**（Run #1 到 Run #2 之间 5 分钟 akshare 新闻源又滚了几条）。
3. **`node4_raw_news` 仅 2 条漂移**，且这 2 条 `last_seen_date` 都被推到 `2026-10-14`——正是跨日守卫（`last_seen < today` ⇒ 允许 +1）**在正确工作**的形状；旁证：`last_seen_date='2026-10-14'` 行数差 30 精确等于 28 新增 + 2 更新，自洽。

**R4/R5 首次给出 PASS**：

- R4：`ema_baseline.sample_count` 在两跑间 18→18（若按次推进应 18→19）——【got_run_model_plan_0911】 A-P0-1 “按日推进”代码修复**真生效**。
- R5：7 条 signal_dates 零重复，验证 §7 裁决 #3a（按日去重）+ 30 日窗口已自然裁置——原 §5.3 R5 行“预计 2026-10-11 起自动归零”已达成。

**结案**：R1/R3 由本次同条件双跑**正式结案**，脚本机械 FAIL 读数属数据环境差异（两跑之间新闻源动态推进、LLM 微秒级波动），**不属幂等缺陷**。R2 的 FAIL 与 R1/R3 属不同性质（需等真实 regime 切换日自然发生），不属回归。

### 5.3 R6 是脚本硬编码的 SKIP

`check_r6`（`scripts/verify_same_day_idempotency.py:470`）**无论是否传 `--runs` 都 SKIP**：

    if runs:
        evidence.append("已提供 --runs，但逐源复测仍需按源分别取数…")
    verifier.add(name, STATUS_SKIP, …)

它只列出 15 个状态源（W1–W6 写侧 / D1–D9 读侧）供**人工逐源取数**，脚本未实现自动判定。
⇒ 今天产出的两个快照**不会**让 R6 变成可算。

其中 R5 的 FAIL **不是代码缺陷**，实测值与性质：

| 红线 | 实测 | 性质 |
|---|---|---|
| **R5** | 7 组 `(type, date)` 重复，**全部落在 08-28～09-10**（09-14 读数） | ✅ **2026-10-14 同条件双跑复测已归零**（7 条 signal_dates 零重复）——`persistence.py:704` 按日去重（决策 #3a）+ 30 日窗口自然裁剪，最晚重复日 09-10 已滚出 |

① 组的 3 条（R-B3 / R-B8 / R-B9）**依 backlog §7 裁决 #6a 结案（工器不覆盖 Node 2、扩链路成本不对等）**，不再依赖机械回放。快照产出步骤见 `verify_same_day_idempotency.py` SKIP 提示内的 ①②③ ——**2026-09-14 / 2026-10-14 已各产一对 `data/_run1` / `data/_run2`**（两目录在 `.gitignore` 的 `/data/` 内，不入版本控制；后一对手为同条件双跑、正式结案）。

> ⚠️ **历史记录仍不可复现，但本次已有独立读数**：`archive/got_impl_review_0911.md` §505 记
> 「`--runs` 冒烟 **PASS**（比对 474 字段、差异 0）」，该次所用快照**已不存在**，**不沿用其结论**。
> 本次用新产的 `_run1`/`_run2` 独立复算得 **0/5 PASS | 4 FAIL | 1 SKIP**（比对字段数 **569**，非 474），差异归因见 §5.2。
>
> ⚠️ **归档输入禁区**：`data/got_daily/` 下有 `20260805_backup/` 与 `20260817_pre_fix/` 两个 **8 月调试快照**，各自配一个同日期正式目录。
> 它们与 B 层 / C 层修复**无关**。脚本的 `--archive-before/--archive-after` 会照单全收并给出裁决 —— 实测喂 0817 那对得
> `[FAIL] R-B8 112 个应不变字段发生变化`，**这 112 项差异全部来自 8 月那次调试**。切勿作为 R-B8/R-B9 的输入。

---

## 6. 需人工或等时间窗的项目

| 项 | 类型 | 说明 |
|---|---|---|
| **M3** 误配率人工抽查 | 人工 | 判据 ≤15%，样本为每日候选全量（≤10 分钟/日）。工具已备：`scripts/export_glue_family_audit.py`（**已纳入版本控制**，commit `0ac5252`），自述"只搬运事实，不做判定，判定列留空给审核者"。<br>📌 **材料已生成（2026-09-14 实跑）**：5 个有效归档日（0904 / 0907 / 0909 / 0910 / 0914，与 §2 的样本数独立互证）、**128 行候选**，类别分布 `same=78` / `recovered=42` / `overridden=6` / `both_none=2`；**待判**的那一类是 `overridden`（6 行）。输出 `data/glue_family_audit/family_audit.html`。判定归负责人。<br>✅ **2026-10-06 评审包已产**（`scripts/build_m3_review_package.py`，流程口径见下行）：最近 5 归档日（0923/0924/0928/0929/0930）候选 120 行 → 分层抽 36 行（回收 25 + 一致 10 + 推翻 1），产物 `data/glue_family_audit/m3_review_20261006.md/.csv/.json`（判定列全空，等负责人填 `grade` 后 `--score` 计分）。**新读数提醒**：`overridden`（推翻）池 5 日合计仅 1 行（0914 那批曾有 6 行）——家族词表同源修复后规则侧/glue 侧已趋一致，**OPEN-C2 设想的「冲突 3」层当前无样本可抽**，M3 的主战场实为「其他回收」层（35 行/5 日）；另有 55/120（46%）是 glue 弃权/双空，按 §8.1 不入 M3 分母，但属 M1/M4 侧要看的信号。<br>✅ **终审完成并结案（2026-10-06）**：36 行判满→误配率 **2.8%**、连续 5 日达标 `status=met`；负责人裁决**直接转正**，放弃 `--sample-mode full` 复跑前置——全量分母实为 **65 行/5 日＝日均约 13 条**（不是此前估计的 120 行判不动，120 含弃权/双空），且分层对难点超采 72.2% vs 总体 55.4% 使 2.8% 属**偏高**读数，按层外推全量 **≈2.2%**，两读数同处红线内。残留：n＝36 功效弱（Wilson95 上界 14.17% 勉强压线）、弱关系堆积 11.1% 未处理；stratified 转**日常预警**，破线需人工复核、不自动回滚。读数表见 **§3** 本指标行，结案论证见【架构计划】§11.8 |
| **OPEN-C2** M3 人工流程未定义 | 人工 | ✅ **流程已定义（2026-10-06，落进 `scripts/build_m3_review_package.py` + 评审包 §〇 表）**：① **谁判**＝负责人（脚本不代判，M3 无可查询真值表）② **抽样**＝按日分层抽 10（『其他』回收 5 + 冲突 3 + 随机对照 2），seed 固定逐日独立子流可复现，**池不足如实记缺口、绝不跨层补齐**（拿一致样本填冲突空缺会把误配率稀释成假达标）③ **判据三档 + 一档兜底（不参与计分）**＝强关系（存在真实需求/基本面传导）/ 弱关系（链存在但间接牵强）/ 无关系（无实质传导）/ 无法判定（证据不足，不入分子也不入分母，单独披露）④ **计分**＝误配率 = 无关系 /（强+弱+无），只有「无关系」计分子；glue 弃权/双空不入分母（§8.1 定义 M3 针对「给出了归属」的行）⑤ **红线与转正**＝单日 ≤15%（10 样本 → 最多 1 条无关系，2 条即 20% 破线），末尾**连续 5 个抽查日**逐日达标；⑥ **可比性约束**＝分层口径对难点超采、读数偏高。原写「转正必须 `--sample-mode full`（§8.1 每日全量）复跑」，**该前置已于 2026-10-06 负责人裁决作废**：M3 直接以分层终审读数结案（偏高读数作验收属保守方向；按层外推全量 ≈2.2%），stratified 此后只作日常预警。**本项已结**（读数见 §3 M3 行，结案论证见【架构计划】§11.8） |
| **OPEN-C1** `transition_count` 时间炸弹 | 时间窗 | 🔴 **首次跌破阈值 = 2026-09-24**（已按窗口实算，替代原「约 09-25」估计）。窗口口径 = `load_regime_state_history(days=20)`，取**最近 20 个带 `market_regime` 字段的归档日**（无该字段的日不占窗格）。<br>📌 **实测 20260914 = 11**，窗口为 `0817…0914` —— 原文记的「0813–0826 十天」已过期：**0813 / 0814 早已滚出**，剩余 8 天（`0817–0826`）在 `0915–0924` 逐个滚出。<br>滚动预测（假设期间无新切换）：`0915:10 → 0918:8 → 0921:7 → 0923:5 → **0924:4**`。<br>两个消费者都在 `aggregator.py`：`:3192` 仓位震荡惩罚 ×0.8、`:3335` `range_reversal` 信号生成（需 `>=5` **且** `persistence_regime == "low"`）——**0924 起两者同时失去触发条件** |
| **A5 影子期** | 时间窗 | 需 ≥10 个交易日（当前有效样本 **5 天**，见 §2；缺口 0908，0911/0912 的产物是测试污染见 §2.2）。距达标差 **5 个交易日** |
| **Phase 5b 显著性校准** | 时间窗 | 需归档 ≥60 交易日（`archive/got_timeseries_calibration.md` 记为预计 2026-11 下旬） |

---

## 7. TickFlow 能力探测结论（2026-09-28，批次 5 §1 / Phase 5b）

key 已就位（`TICKFLOW_API_KEY`，len=35），SDK 实测版本 **0.1.25**（文档旧记 0.1.18 已过期）。一次性只读探测脚本 `.review_tmp/tf_capability_probe{,2,3}.py`（不入库），输出 `tf_probe*.txt`。**结论：TickFlow 不能作 4b 板块排名源，维持 mootdx 先行（与拍板②一致）。**

| 探测项 | 结果 | 对批次 5 的影响 |
|---|---|---|
| 概念/主题 universe | **0 个** | universe 全是申万行业 SW1/SW2/SW3（各 335）+ 零星 ETF/index/bond，**无任何概念板块**（880xxx 通达信概念 / 同花顺 / 东财概念一律没有）→ 4b 需要的「概念排名」TickFlow 结构性缺失 |
| `quotes.get_by_universes` | **PermissionError** | 当前套餐不放行 universe 级批量行情 → 无法用成分股聚合出板块涨跌幅，排名源的候选通路封死 |
| `klines.batch` | **PermissionError** | 板块日线批量自算同样被封；单标的 `klines.get` 可用（dict OHLCV+amount）但 600+ 板块逐个拉不可接受 |
| `universes.get(id)` | OK | 返回成分股 symbol 列表（如 SW1电子 22 只）——仅成员表，无排名 |
| `quotes.get_by_symbols` | OK | 单/少标的实时（含 `change_pct`），≤5 标的/请求——4b 排名用不上，但 **5c/Phase 6 个股实时可用** |
| `realtime` | QuoteStream（WebSocket subscribe） | 非 REST，窄面 P0 之外无现成板块流 |

**后续动作登记**：① 4b 排名源**确定 mootdx 自算**（`get_board_quick_change` 批量，见 §2），TickFlow 切换议题在本套餐权限下**关闭**，除非升级套餐重新探测放行 `get_by_universes`；② 4d 快照 fallback 若依赖 TickFlow universe 批量预取——同样被 `get_by_universes` 权限卡住，§4.2 取证需据此标注降级；③ Phase 6 盘中闸门 OPEN-I2 重估：TickFlow 个股实时 `get_by_symbols` 可用，但五档/竞价权限未探（本批未测，留 Phase 6 触发时）。

### 7.1 4d 快照源连通性取证（2026-09-28，批次 5 §4.2 / Phase 4d）

一次性只读脚本 `.review_tmp/cp4d_snapshot_connectivity.py`（不入库），复刻 `alphasift_service.DsaEastMoneyHotspotProvider._fetch_board_names`（L1487）的生产请求打 `79.push2.eastmoney.com/api/qt/clist/get`。

**结论：该直取通路当前可达、未被封 → 4d hotspot 因子无需标注降级，§7 注②/§1 申请清单联动不触发。**

| fs 用例 | HTTP | 耗时 | json | `data.diff` 行数 | 采样（f14/f3/f12） |
|---|---|---|---|---|---|
| `m:90 t:3 f:!50`（概念） | 200 | 0.37s | OK | 100 | 光刻胶 −4.61 BK1753 / 2026中报预减 −1.30 BK1752 |
| `m:90 t:2 f:!50`（行业） | 200 | 0.31s | OK | 100 | 综合Ⅲ −1.16 BK1627 / 稀土 −5.15 BK1626 / 钨 −6.15 BK1625 |

**判定口径与边界**：
- 79.push2 是 hotspot 因子的**最后一层 fallback**（链路：akshare `stock_board_change_em` → DSA fetcher rankings → 本直取）；本探测证实最坏情况下兜底通路仍活，影子期 snapshot 源可用。
- ⚠️ 单次探测=瞬时读数，非 SLA 保证；4d 影子正式接线（§4.3）时仍须在 `screening_shadow.json` 里**按日记 snapshot 源实际命中层级**（沿用 missing_fields/告警惯例），若后续交易日转 `BLOCKED_*` 再触发降级标注 + §1 申请清单联动。计划 §9.4「快照源再次被封致影子空转」风险的缓解手段即此按日可见性。

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

# M1–M5 复算（§3）
python scripts/verify_glue_metrics.py --date 20260910

# B 层 R-B3 / R-B8 / R-B9（§4 / §5）—— 无参时 3 SKIP（缺回放对）
python scripts/verify_layer_b_consistency.py

# R1/R3/R5/R6 同日两跑 diff（§5）—— --runs 需要【两个】快照目录
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

# 无快照时跑全量（R5 可离线真算，其余 SKIP）
python scripts/verify_same_day_idempotency.py

# M3 人工抽查材料（§6）—— 输出 data/glue_family_audit/family_audit.html
python scripts/export_glue_family_audit.py

# M3 评审包生成 + 回填后计分（§11.8 的 2.8% 就是这条命令读出来的，离线只读、不触网）
python scripts/build_m3_review_package.py                 # 缺省分层口径（最近 5 个归档日）
python scripts/build_m3_review_package.py --sample-mode full   # §8.1 原全量口径（可选复核，非前置）
python scripts/build_m3_review_package.py --score data/glue_family_audit/m3_review_20261006.csv

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
*修订：2026-09-15（第七轮，T1/T1-H 影子产物落盘）—— **T1/T1-H 新增独立影子产物文件**：`shadow_t1.json` / `shadow_t1h.json`，与 A5 的 `agent_glue.json` 对称，由 `portfolio_decision._save_shadow_records()` 落盘，供 `archive._render_shadow_section()` 渲染到 `reasoning.md` 末尾的「🧪 影子模式观测」段落。**零 schema 改动**（不碰 `schemas.py`，不影响 R-B8/R-C7 逐字段 diff）。§1 表更新 T1/T1-H 的「实跑 ENABLED」与「归档产物」列：T1 现有 4 天日志 + 1 天归档，T1-H 现有 1 天归档（未执行：持仓不满足三类筛选条件）。新增 §2 末尾「T1/T1-H 影子产物」小节说明设计要点。11 个测试用例覆盖（含重跑不堆积、「未执行+原因」分支）。端到端验证通过（0915 归档，T1 否决广立微 301095，T1-H 未执行）。*
*修订：2026-09-15（第八轮，归档已验证项）—— **7 项已通过验证的内容归档至 [`archive/verified_completion_log.md`](archive/verified_completion_log.md)**：A-P0-4（monitoring 按日去重）、R-C1（S1/S2 失败样本落盘）、R-C3（十锚点逐位一致）、R-C5（零引用复验）、R2（状态机收口）、R4（EMA 计数按日）、M6（回收率 90.9% 达标）。源文档删除对应段落（§3 M6 行、§4 R-C1/C3/C5 行、§5 R2/R4 行、§6 A-P0-4 整节、§8 对应复现命令），§7→§6 重编号，脚本输出描述同步更新。*
*修订：2026-09-26（第九轮，拍板落地）—— 决策简报（`decision_brief_20260926.md`）七项拍板实施：**#3a** `signal_dates` 改按日去重（`persistence.py`，`signal_count_30d` 语义由次数变天数，单测 2 例）；**#4b** 4 份测试污染产物备份后删除（移入 `data/got_daily_backup_20260926_1700/`）；**#5a** `market_regime` 跨日状态机 5 字段移出 R-B8 不变组（`verify_layer_b_consistency.py` 单独比对、按设计 SKIP 不计 FAIL，§4/§5.1 同步）；**#1a/#6a/C-P13a/OPEN-C1a** 维持现状，已在 backlog §7 标已拍板。*
*修订：2026-09-26（第十轮，C-P12 prompt A/B 双版回放）—— **R-C6 由「有效样本 0 天 SKIP」→「两版各 9 天 M2×M3 对照矩阵已产出、待负责人判读」**（§4 R-C6 行）：新增 `GLUE_PROMPT_VARIANT`（a/b）/`GLUE_MAX_TOKENS` env（均仅 replay 生效、生产零影响）+ `agent_glue.json` `prompt_variant` 留痕 + `verify_glue_metrics.py --compare`（按变体逐日 M1′/M2/低置信占比/放弃事件 + ΔM2 矩阵 + 目录尾缀一致性校验 + `--m3-audit` 分层预审表）。A/B 各回放 0901/02/03/04/07/08/09/10/14 共 9 日（`{date}/_replay/variant_a|b/agent_glue.json`、均 status=ok/md5 互异）；矩阵：A 均值 M2=35.5%/低置信 6.8%、B 均值 M2=46.3%/低置信 40.8%（印证 §2.6 M2↔M3 拉锯）。⚠️ 0902 B 版撞 16000 截断、临时 24000(A)/32000(B) 重跑，与其他日不对称，有效对照建议取 8 天（剔除 0902）。矩阵/预审表见 `cp12_prompt_variants_draft_20260926.md` / `cp12_m3_audit_20260926.md`；`--date 20260910` R-C3 锚点逐字节一致，R-C6 按设计仍 SKIP（无曲线判读即 SKIP，非缺数据）。达标判读 / C-P13 标定 / OPEN-C1 阈值重标归项目负责人。*

*修订：2026-09-27（第十一轮，C-P12 事件级复核 + C-P13 架构调研登记）—— 事件级 A/B 对照表 `cp12_event_level_ab_20260926.md` 实测（A relation=336 / B=428，交集 168 / B 补挂 229 / A 有 B 无 104），暴露两项此前披露不足的质量问题：**① 0902 A 版为退化重复循环**（191 条 relation 仅 28 命中事件，属生成/解码故障非 prompt 内容，§4 R-C6 行已标「标定应整日作废」）；**② B 版存在真·过度归属幻觉**（0903 房贷→24 板含减肥药/短剧游戏、金价→短剧游戏），导致现有 10 行分层预审样本抽不到高扇出病态事件→**M3_B 会被低估**（终审需补高扇出过采样专项表）。**架构结论**：A/B 为同一生成架构在召回/精确单旋钮上的两端、§2.6 拉锯结构性无解，基于外网论文（THEME/CoVe/CAD/CausalStock/FinRipple/LangExtract）给出「接地式召回-验证」5 层管线修正方案，设计笔记见 `cp13_grounding_pipeline_research_20260926.md`（待拍板）。本轮纯文档登记，零代码/行为变更；R-C3 锚点未触及、R-C6 仍按设计 SKIP。*

*修订：2026-10-06（第十二轮，M3 首次读齐）—— §6 **M3 由「未测」改为首次实测**：负责人逐行判完分层口径 36 样本 → 误配率 **2.8%**（无关系 1 / 弱关系 4 / 强关系 31）、Wilson95 [0.49%, 14.17%]、逐日连续 5 日达标 `status=met`。⚠️ **分层初筛 ≠ 转正**（§8.1 要求 `--sample-mode full` 复跑，本轮未跑；**该前置已于第十三轮作废**）。唯一「无关系」误配（低空经济→智能驾驶，20260921/0929 两个抽查日稳定复现的系统性偏置）已做对症收口：`family_keywords.py` 军工族新增关键词 "低空"——依据是 `aggregator.py:1075` 任务2「家族归属（仅对落「其他」的板块）」，规则侧命中后 glue 不再有裁量权。**本轮含在线决策变更**（非纯观测）：`低空经济` 的 `frame_group` 由「其他」变「军工」，拥挤计数 / 主线聚合 / Node 6 家族中继会跟着动。读数表、机制发现、prompt 12 族 vs `CONTROLLED_FAMILIES` 14 族漂移、电气设备弱关系为何不改的论证均见架构实施计划 **§11.8**。守卫测试 `test_dikong_resolves_to_military_family`；同跑 92 + 180 passed、`_audit_keyword_overlaps()` 仍 `[]`。*

*修订：2026-10-06（第十三轮，M3 结案转正）—— 负责人裁决 **M3 直接转正，放弃 §8.1 的 `--sample-mode full` 复跑前置**，§6 M3 行改为「✅ 已结案转正」。裁决依据是实测而不是嫌麻烦：分层样本难点占 26/36＝72.2%，而总体仅 36/65＝55.4% ⇒ 2.8% 是**偏高**读数（拿偏高读数作验收属保守方向）；按层外推全量口径 ≈2.2%，两读数同处 15% 红线内且与红线差一个数量级，复跑不改变结论。同时更正第十二轮的代价估计：全量分母是 **65 行/5 日（日均约 13 条）**，本就在 §8.1 自己写的「≤10 分钟/日」预算内（早前说的 120 行是含 glue 弃权/双空的总候选，不入分母）。结案后 stratified 降为**日常预警**（单日 1 条无关系即 14.3% 贴线，全量口径仅 7.7% ⇒ 破线需人工复核、不自动回滚）。`scripts/build_m3_review_package.py` 三处口径文案同步（抽样/判据/计分公式不动，`--score` 复跑仍 2.8%），新增守卫测试 `test_process_note_records_promotion_decision`（防 full 复跑前置被写回去）——test_m3_review_package **18 passed**。本项只结 M3：不动任何生产开关，M1/M2/M4-a 仍是未达的水位监控项。详见【架构计划】§11.8。*

*修订：2026-10-11（GOT 项目架构审查轮注记两处，只读零代码）—— ① 新增顶部「排期批 4/5 影子五线判据」补登节（backlog §8 #13 挂号回填）：五线（sell_source / risk_override / exposure_enforce / signal_size / mature_reduce_shadow）产物键、null vs `[]` 语义（mature_reduce 的 `[]`=跑了没命中实测推断、待负责人确认固化）、六归档日读数、转正门槛留空归负责人；**Critical #1 警告经盘上复核更正**：0928/0929 归档实测含非空 3b/4b 影子字段（0928 21:47 真跑、修复当晚先落工作树、0929 meta 已记 `912cf68`），断窗内无归档落入，故早期样本不作废、读数可作标定输入（详见节内更正注记）。② 项三 3a 节补审查注记：funnel_stats 3a 接线已提交（`5a4f29f`）但六归档日 meta 实测无键、D-3 基线 0 天未起算，≥1013 首验后才计。取证报告 [`reports/got_audit_20261011.md`](../../reports/got_audit_20261011.md)（不入库）。

*修订（2026-10-12，全流程实跑读数订正轮，零代码改动）*——新增顶部「2026-10-12 全流程实跑」节，并把四处被实测推翻的原文就地更正：① 三闸门读数起算由「MEG 1 / FTS 0 / FXG 0、至今一份都没有」→ **10-12 起 FTS/FXG 各有首份产物**（`shadow_fts.json` + `fxg_baseline_scoring.json`），时钟正式起算；② 项三 3a 的「≥1013 首验后才起算」→ **10-12 已首验通过**，D-3 基线起算日=10-12；③ GATE-1 首日 `mode=shadow` 已确认（WI-P-4 尾项闭环），但空仓无卖单 ⇒ `quote_unavailable`/误拦截两门槛无样本；单测数 17→**26**（`--collect-only` 实测）；④ **bear gate 留痕判据与实现不符就地更正**——原记「无命中日落 `{"executed": false}`」，实现是仅命中时落盘且多轮只保留最后一轮（10-12 实测 10:02 命中 1 单、归档无产物），且降级文案会把这种情况说成「当日无拦单」；已回写 backlog §1.H 改判真·待实施。另登记批次 B 的零产物定性（`_collect_actionable_sectors` 在无买入/持有标的时返空 ⇒ 转正判据需补计样本前提，backlog §9 #7）。本跑同时发现并修复 Node 4 prompt 模板大括号转义缺陷（commit `e691fac`），09:37 那一跑零归档即因此。取证脚本 `.review_tmp/read_20261012.py` + `scan_prompt_braces.py`（不入库）。*

*修订（2026-10-14，“缺回放对 / 两跑快照”四项集中结案轮，零代码改动）*——**R1/R3/R-C7/R-B3/R-B8/R-B9 六项一次落笔**：

- **R1/R3 → ✅ 同条件双跑结案**：当日 A 股午休窗口 11:30-13:00 内 (11:32→→12:02) 完成一次真正的同条件双跑（两跑均在 `bar_complete=False`、数据源同为午盘收盘快照、LLM prompt 同版本），新增 §5.2.1 给出三条铁证：`node4_events.peak 19→19` / `node4_raw_news.peak 47→47` 完全不变、`node4_events` 共有 1774 条老事件零漂移、`node4_raw_news` 仅 2 条漂移且都正确触发跨日守卫（旁证 `last_seen='2026-10-14'` 行数差 30 = 28 新增 + 2 更新）；**R4/R5 首次给出 PASS**——分别直接印证【got_run_model_plan_0911】A-P0-1 与 backlog 裁决 #3a 已真生效。脚本机械 FAIL 读数（R1 5/377、R3 4 项）属两跑之间新闻源动态推进、LLM 微秒波动，**不属幂等缺陷**。§5 引言/表/实跑复算 block 同步。
- **R-C7 → ✅ 已由 C3 行为面单测替代结案**：§4 与 §5 两处 R-C7 行由「待回放」→「`TestC3ShadowPeriodEquivalence` 锁定 A6=false 与影子期基线逐字段一致」。C-P 系列已合入历史、前一次归档不可重生成；且 `--got-replay` 不写归档、Node 6 之外链路不覆 – 原始取证材料产不出属工器设计边界非回归。
- **R-B3 / R-B8 / R-B9 → ✅ 依 backlog §7 裁决 #6a 结案**：§5 表三行由「SKIP」→「已依 #6a 结案」。B 层修复位置在 Node 2（`family_keywords.py` + `sector_analysis.py`）而 `--got-replay` 设计上不跑 Node 2 ⇒ 回放对产不出；扩链路需可复现当日行情、历史不可复现，成本/收益不对等。零行为侧语义保护由 §5.1 防误修清单 + 单测承接，行为侧自然留痕在生产归档。
- **§5.3 R5 行同步**：由「预计 2026-10-11 起自动归零」→「**2026-10-14 同条件双跑复测已归零**」（7 条 signal_dates 零重复）。
- **§5 尾注同步**：① 组三条红线行由「仍为纯 SKIP」→「依 #6a 结案」。
- **R-C6 仍开放**：本不动 §4 R-C6 行，矩阵已产出、判读归负责人，待下一轮处理。

取证日志：`.review_tmp/_r13_run1.log` / `_r13_run2.log` / `_r13_verify.txt` / `_r13_dbdiff.txt` + 一次性只读脚本 `.review_tmp/_r13_dbdiff.py`（属取证产物、不入库）。本轮零生产代码变更、零 schema 变动；`verify_layer_b_consistency.py` 不再重跑（B 层三项已转人工归档留痕口径）。

**同轮补登：R-C6 判读完成**（同日同批 commit、与上面三项一并处理）——§4 表 R-C6 行由「矩阵已产出、待负责人判读」→ **✅ 判读完成（保 A 版为默认 + 结论转 C-P13 接地式架构修正）**。判读依据：曲线非单调属真信号（不只看均值）、B 版有真·过度归属幻觉（分层预审抽不到高扇出病态事件、实际 M3_B > 20%）、0902 A 版属生成故障已整日作废（有效对照取 8 天）；三条同方向指向“prompt 单旋钮结构性无解”。零 prompt 默认变更、零代码变更，现行 A 版本就是默认。本行与【§3】M2/M4-a 行（已于 2026-10-14 同日裁决随 C-P13 休眠）同步。本轮四项结案（R1/R3 + R-C7 + R-B3/B8/B9 + R-C6）**全部完成**，§8 复现命令保持可重跑。*
