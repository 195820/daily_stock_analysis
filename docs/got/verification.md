# GOT 待验证 / 影子期清单
> **原位置**：`docs/got_shadow_verification.md`（2026-09-17 文档结构重排移入当前位置）

---

## 2026-11-02 补登（10-30 运行时收敛开关登记 + 两处产物/解析修复）

背景：`bcede32`（2026-10-30）落语义运行时 / 两个窄 Agent / 只读复盘时未同步本清单；11-02 补登判据，并收口两处实锤缺陷。达标判定归项目负责人。

### 10-30 新增开关与已生效改动

| 项 | 开关（缺省） | 产物/行为 | 判据 | 回滚 |
|---|---|---|---|---|
| 窄 Agent（身份 + 归因） | `GOT_NARRATIVE_AGENTS_ENABLED`（true）/ `GOT_NARRATIVE_MAX_SYMBOLS`(20) / `GOT_NARRATIVE_TIMEOUT_S`(60) / `GOT_NARRATIVE_CONSTITUENTS_ENABLED`(true) / `..._TIMEOUT_S`(10) | `narrative_context.json`（status/identities/attributions）+ `semantic_memory` 表；A6 只当证据 | ① 有持仓日 `status ∈ {ok, empty}`（无持仓 `empty` 属预期）；② 每条 identity/attribution 的 `evidence_ids` 都能在 `agent_run.json` 闭包中解析（零伪造）；③ 决策面零改动：`sector_role`/闸门/仓位与 `=false` 日逐字节一致（抽 3 日） | 置 false（`status=disabled`，零 LLM 调用） |
| 只读复盘 | `GOT_AUDIT_ENABLED`（shadow）/ `GOT_AUDIT_MAX_HYPOTHESES`(3) / `_MAX_GRILL_ROUNDS`(2) / `_MAX_SYMBOLIC_TOOLS`(8) / `_MAX_LLM_CALLS`(8) / `_TIMEOUT_S`(180) | `data/got_audit/YYYYMMDD/{report.md,findings.jsonl,audit_runs.jsonl,evidence_pack.json}` + trace | ① 只读：产物目录外零写入（`src/`/docs/thesis/trade_plans/config 无改动）；② 失败/超时/超预算 → `status=incomplete`，不得静默 pass；③ 现有读数可复算：10-29 `incomplete`、10-30 `ok`（0 findings / 2 calls） | `GOT_AUDIT_ENABLED=false`（CLI 直接 no-op） |
| LLM trace | `GOT_LLM_TRACE_ENABLED`（true） | prompt/raw_response 落 run 目录 | 敏感字段不落盘（`SECRET_PATTERNS` 过滤）；置 false 只少原始报文，调用/usage/证据闭包不变 | false |
| 已生效非影子改动（10-30） | 无运行期开关 | ① 删除 Node 1/2/3/4 reasoning、Node 6 refine、Node 7 LLM 精炼；② A6 共享语义回填 Node 4 concepts/risks + Node 5 hot_topics；③ 事件稳定 ID（`normalize_title+source+as_of`）+ T5 退役 + `_align_macro_rule_events` 规则扇出；④ 选股消息面直读 A6 relations（缺失标 `relation_missing`） | 回归测试 + 归档字段验收（`agent_run.json`/`agent_steps.jsonl`/`agent_glue.json`/`news.json`）；决策面回归以 10-30 归档为基线 | `git revert bcede32`（无运行期开关） |

### 2026-11-02 两处修复

| 缺陷 | 根因（实证） | 修复 | 判据 |
|---|---|---|---|
| `shadow_attention.json` 归档后消失 | `archive_daily_run` 原子替换白名单漏登；Node 4 已 4 次落盘（10-30 日志 14:57/15:01/15:09/15:30），归档后被整目录替换删除 | 新增 `PRE_ARCHIVE_ARTIFACTS` 单一登记点，补 `shadow_attention.json` / `shadow_shortlist_quota.json`；守卫测试 `tests/test_archive_pre_archive_artifacts.py` | 同日二次归档后两产物仍在；attention 判据（2026-10-28 节）**计时自修复后首个归档日重算**，此前归档不计样本 |
| Q1 low 救援整批丢弃 | 单条送审时模型返回裸对象（10-30 `node4.low_rescue:004` status=ok，输出 `{index:0,...}`），解析器只收数组 → `ValueError: 输出非数组: dict` | 单对象归一 `[obj]`，标量仍 raise；T2b 端到端用例 + T7/T7b | 单对象响应 `upgraded=1`、`shadow_low_events.failure` 不出现；`tests/test_low_sector_rescue.py` 15 passed |

### 2026-11-02 P0 持仓修复（I-1 / I-3 / I-4）

| 项 | 根因 | 修复 | 判据（下次生产跑机械可核对） |
|---|---|---|---|
| P4 快照退出 | `assess_holding` 的 P4 在 `thesis_drift` 之后且要求 `sector_role=="ambush"`；002009 被判 mainline + grace 时 `fade_sell` 永不登记 | 快照 `signal` 非空提到 ⓪ 最高优先级，去掉 role 门槛 | 002009 若仍有 open snapshot + `fade_sell`：`unified_scoring` 动作 `sell`、`action_reason_code=p4_exit`、`ambush_exit_signals` 含 002009，快照 `is_open=false`；无 snapshot 持仓不受影响 |
| H1 资金流桥接 | `holdings_flow_index` 以 thesis `动力电池` 为 wish，但 `sector_flow_all` 键为 `动力电池回收`；Phase 2 又只按当日 boards 查 | `HOLDING_SECTOR_FLOW_BRIDGES` + 消费侧并入 thesis 板块名 | `capital.json.evidence.holdings_flow_index.index["动力电池"]`：`matched=true`、`bridge_from=动力电池`、`matched_key` **逐字节**存在于 `sector_flow_all` 键集合；`_score_capital` 的资金维不再 `capital_data_missing` |
| cancelled buy 锚 | `reconcile` 只改 trade_plans 状态，不关 open thesis/snapshot（002741/002093 无持仓仍 open） | `_close_buy_plan_anchors`：buy cancelled 且票消失时关闭 | 下一跑若有 cancelled buy plan：对应 `holding_thesis_snapshots.is_open=false`、`lifecycle_state=closed`；`ambush_position_snapshots.is_open=false`、`exit_signal=plan_cancelled`；partial/executed 不关 |

T1-H 裁决（2026-11-02）：继续影子，不转正；有效样本 3 日 / 1-2 只，待 ≥10 个交易日且 ≥3 只持仓复评。`penalty=null` 属影子设计。

---

## 2026-10-29 补登（Node 4 分层粗筛 — 生产直接启用 + 事后验证）

计划：`local://node4-news-retrieval-plan.md`；改动明细见 `docs/CHANGELOG.md` 同日条目。
**非影子线**：ECS `/opt/dsa/code/.env` 已设 `GOT_NEWS_READING_ARCH=cluster`（备份 `.env.bak.20261029`）；代码缺省仍 `flat`，回滚一行改回。达标判定归项目负责人。

| 判据 | 口径/产物 | 当前读数 | 不达标动作 |
|---|---|---|---|
| 索引模式 | `news_retrieval_trace.json.index_mode` | 10-28 回放 / 10-29 生产均为 `embedding`；ECS litellm 1.82 需 `encoding_format=float`（已修） | 持续 `jaccard_fallback` 先查 embedding key/model |
| 归档事件回连 | 10-28 `news.json` 11 条 `major_events` → 原始标题/`node4_events` 历史 | **11/11**（flat 亦 11/11） | 回滚 flat 并审计 carry 窗口 |
| 短名单唯一性 | 12 条 `news_cluster_id` 去重 | 12/12 唯一；`review_stats.invalid=0, duplicate=0` | 查 `review_candidates` 校验 |
| 主题覆盖 | 存储/AI 政策/固态电池/上海智能制造/OpenAI 谷歌芯片 | 前 3 项直系；上海智能制造由「物理AI」覆盖；OpenAI 谷歌芯片在终审池未入 12（flat 回放同样未入，原稿来自归档历史事件） | 人工复核后决定是否调 prompt/终审池 |
| 资源预算 | Node 4 调用数/字符/耗时 | 同池 1650：4→38 次、276.6k→324.4k 字符、18.6→90.6s；生产 1089：33 次、分片 88.6k、82–103s、0 失败片 | 超 5 min 或失败片 ≥3 查分片并发/超时 |
| 单片 prompt | 最大 shard `input_chars` | 10-29 生产最大 6,035（flat 单次 76,780） | >15k 降 `GOT_NEWS_READ_SHARD_SIZE` |
| 重复事件 | `_save_events_to_db` 新增/更新 | 10-29 生产 10–12 条事件，无激增 | 查同题归并与 `source_count` 口径 |

**已知取舍（记录，不作缺陷）**：终审 12 条是 LLM 判断，未入选主题仍保留在终审池/原始池/历史上下文；flat 同日 12 条含 3 个重复位（SK海力士×3、工业利润×2），cluster 0 冗余但会换掉 flat 的广电/卫星/绿电/电力市场等主题。

---

## 2026-10-28 补登（Node 4 消息面补强 — 三条影子线）

计划：`local://node4-news-hardening-plan.md`；改动明细见 `docs/CHANGELOG.md` 同日条目。

| 影子线 | 开关（缺省） | 产物 | 决策影响 | 转正判据（归负责人） |
|---|---|---|---|---|
| attention 观测（P0-1） | `GOT_ATTENTION_SHADOW`（true） | `shadow_attention.json` | 零：不写 `source_count`、不进 prompt、不进 `_detect_fermenting_events` | ≥10 交易日 `platform_count` 分布稳定 + 多平台同题人工抽检无系统性误聚。**〔11-02 注记：该产物此前被归档原子替换删除，计时自修复后首个归档日重算，见 2026-11-02 节〕** |
| 正文精读（P0-2） | `GOT_NEWS_FULLTEXT_ENABLED`（**false**） | 无独立产物（prompt 素材行） | true 后细读输入变长（≤`GOT_NEWS_FULLTEXT_MAX_CHARS`=1200 字/条 + 正文补抓） | 对照跑：`affected_stocks` 非空事件数 ≥ 基线、LLM 输出 token 用量无异常 |
| 短名单类目配额（P1-3） | `GOT_NEWS_SHORTLIST_QUOTA`（false） | `shadow_shortlist_quota.json` | true 时同类目超额部分整组裁掉（按 LLM 顺序，空位不回填） | `category_dist` 实测单一类目常态 ≤6 → 可直接放弃转正并删开关 |

**已直接生效（无决策面改动）**：P1-1 URL 指纹去重（`url_canonical` 列）、P0-1 热榜元数据落库（`platform/platforms/rank/heat/snapshot_count` 列）、P1-2 权威 RSS 源注册、`has_partial_failure` 判定修复。

**两条实现边界（勿回退）**：① `MAX_MERGED_NEWS=150` 的 weight 截断会把**无发布时间的热榜条目整源丢弃**（实测 2026-10-28：newsnow 203 + ths_hot 15 → merged 0 条）→ `_persist_raw_news` 改吃 `fetch_all_news_pool` 的未截断池，LLM 输入仍是 150 条口径；② 热榜源 `published_at`=采集时刻，按 desc 会占满 `list_items(limit=100)` → 权威 RSS 走独立取片（`source_id_prefix="rss-"` + `RSS_LOOKBACK_DAYS=7`）。

---

## ‼️ 2026-10-27 补登（持仓模块三原则接线 H1/H2/H3 — 供数 + 裁决归位 + 历史回查；**当日用户裁决跳过影子直接转正**）

**计划**：`local://holdings-module-rewiring-plan.md`（用户三条架构原则落地：①持仓分析归决策编排层 ②前序 Node 给持仓供数 ③买入时历史决策喂当日决策）。

| 阶段 | 内容 | 开关（缺省） | 决策影响 |
|---|---|---|---|
| **H1** 供数 | `HoldingsContext` 构建（编排器头部，零 LLM/零爬虫）→ Node 2 `holdings_sectors`+`holdings_coverage`（持仓板块兜底补拉）/ Node 3 `evidence.holdings_flow_index`（三级别名匹配）/ Node 6 `holdings_intel`（role=holding，独立字段） | 无开关（常开） | 零：`confirmed_sectors` 组成、TOP-N 名额、埋伏池、position_limit 均不变；补拉板块不进截面分母、不写 node2_sector_trends |
| **H2** 裁决归位 | `_score_holdings` 8 分支链搬至 `decision_plan.assess_holding`（纯函数）；新增 `sector_confirmed` 守卫（选股通道 actionable + mainline/ambush，或持仓补入通道 `board_state∈{active_mainline,ambush,high_position_cooling}`；lifecycle 缺失 → hold，不再落 40/60 降温档） | `GOT_HOLDINGS_VERDICT_V2`（**enabled**，2026-10-27 转正） | 守卫生效；对拍产物照旧（`mode=enabled`）；`shadow`=只算不生效（取证态）；`off`=kill-switch |
| **H3** 历史回查 | `decision_plan.lookup_decision_history`（thesis × catalyst_events × trade_plans × ambush snapshots，纯 SQLite 读）→ 证据进 reasoning；P4 分支扩为 snapshot ∪ thesis 催化剂锚双锚 | `GOT_HOLDINGS_HISTORY_FEED`（**enabled**，2026-10-27 转正） | 催化剂到期/失效的埋伏持仓可在 snapshot 表 0 行时退出；`shadow`=只作 evidence 不改 action；`off`=kill-switch |

**唯一已知误判修正（H2）**：confirmed ambush 板块跌出候选集 → `lifecycle=None` → 40/60 降温档 `reduce`。旧链对新链对拍行 `legacy_action=reduce / v2_action=hold / reason_code_v2=sector_confirmed`。

**转正裁决（2026-10-27，用户）**：纯新增能力、非修正 → 跳过影子纪律直接生效（同 D3 分道预算先例）；下列判据**降级为事后验证（转正后 ≥10 个有效交易日审阅）**，不达标用 `off` 回滚。

**事后验证判据（机械可核对）**：

1. `portfolio_decision.json.holdings_verdict_shadow` 逐日存在（`mode=enabled`），`holdings_seen` 与当日持仓只数一致；`applied_action == v2_action`（转正后生效路径）。
2. 不一致行（`changed=true`）逐条可归因：reason_code_v2 ∈ {`sector_confirmed`（H2 守卫）, `p4_exit`（H3 双锚/P4 提前判定）}；出现其他原因码即视为搬家走样，须回滚。P4 相关差异在 `ambush_position_snapshots` 仍为 0 行且 `HISTORY_FEED=shadow` 时应恒为 0 条，出现即说明提前判定抢了不该抢的仓。
3. 不一致率不以「越低越好」判定——修正目标恰是让这类行改变；判读标准是「改为 hold 后该板块次日起 N 日未跑输」。
4. `holdings_history_shadow.rows[].catalyst_status` 与 `catalyst_events` 当日状态一致（抽样人工核对 ≥3 行）；enabled 前该字段不得影响任何 action。
5. H1 红线：`sector.json.holdings_coverage` 与 `unified.json.holdings_intel` 同日均存在（无持仓诉求时缺省），且 `confirmed_sectors` 条数与关闭状态下逐字节一致（H1 零决策改动）。
6. 回滚：`GOT_HOLDINGS_VERDICT_V2=off` + `GOT_HOLDINGS_HISTORY_FEED=off` → trade_plans/allocation 与接线前一致（H2/H3 全程不改分数，只改 action 判定入口）。
7. **回归对比口径**：影子期 `reasoning` 文本会新增 H1/H3 标注（资金维 1d/3d/5d、`入场composite→当日`、`近窗计划兑现率`、`催化剂锚状态`），属预期留痕；逐字节回归只对 **action_recommendation / allocation / trade_plans** 生效，不对 reasoning 文本生效。

**首批预期差异（转正即刻生效，人工抽检重点）**：① confirmed 板块（actionable 或 `board_state` 命中）但 `lifecycle=None` 的持仓 → `reduce` 变 `hold`（`reason_code=sector_confirmed`）；② thesis 催化剂锚 `elapsed/expired` 的埋伏持仓 → 可在 `ambush_position_snapshots` 0 行时 `sell`（`reason_code=p4_exit`，`p4_anchor=thesis`）；③ 带快照且真有退出信号的埋伏持仓 → `sell`（③′ 提前判定，原为死分支）。

**冻结红线未动**：`GOT_SELL_SOURCE_*` / `GOT_TRAILING_STOP_*`（WI-P-7）不解冻；`.env GOT_TRADABILITY_GATE=shadow` 不动；B4-C 退出分层仍只登记判据。

**影子产物**：`portfolio_decision.json.{holdings_verdict_shadow,holdings_history_shadow}`；`capital.json.evidence.holdings_flow_index`；`sector.json.{holdings_sectors,holdings_coverage}`；`unified.json.holdings_intel`；`reasoning.md`「🧪 影子模式观测」→「持仓数据供给（H1）」段。

**单测**：`tests/test_holdings_rewiring.py`（34 用例：H2 分支表 18 / H1 供数 9 / H3 回查 6 + 开关缺省 1）。

**P4 死分支修复（H3 附带，2026-10-27）**：原链把 P4 埋伏退出放在 `lifecycle is None` 分支之后，而埋伏板块的 `detail.lifecycle` 恒为 None（Node 2 只给 mainline/exhausted/cooling 赋 lifecycle）⇒ 退出信号永远被 40/60 降温档抢先，分支结构性不可达。改为「只在真的触发退出时抢前」（③′）：未触发则逐字节维持原顺序。`ambush_position_snapshots` 实测 0 行 ⇒ 当日零行为变化；表有数据或 thesis 催化剂到期（`GOT_HOLDINGS_HISTORY_FEED=enabled`）后才生效。

**实测读数（2026-10-28 08:35–08:56 全流程 `main.py --got`，EXIT=0）**：当日组合为空（0 只持仓），故各持仓供数位按设计缺省——

| 产物 | 读数 | 判读 |
|---|---|---|
| `orchestrator` 日志 | `[GOT] 持仓上下文: status=empty, 0 只持仓, 0 个板块诉求` | H1 构建点已接线且空组合不阻断 |
| `portfolio_decision.json.holdings_verdict_shadow` | `{mode: shadow, holdings_seen: 0, changed_count: 0, rows: []}` | H2 对拍线在产（tombstone 证明跑过），空持仓下差异必为 0 |
| `portfolio_decision.json.holdings_history_shadow` | `null` | H3 无持仓 → 零额外 SQLite 读 |
| `capital.json.evidence.holdings_flow_index` | `null` | 无板块诉求 → 不产索引（缺省不等同「全未命中」） |
| `sector.json.{holdings_coverage,holdings_sectors}` | `null / []` | Node 2 补拉零成本跳过 |
| `unified.json.holdings_intel` | `[]` | 选股通道零污染 |
| `reasoning.md`「持仓数据供给（H1）」 | 未渲染 | 无产物即不产段（无持仓时 reasoning.md 结构不变） |
| `trade_plan_log` | 2 行（对账 + 装配） | 决策编排层日志照常 |

> 有持仓时的端到端路径由进程内集成冒烟验证（真实 `PortfolioDecisionNode._score_holdings` + 桩 fetcher/LLM）：`shadow` → 应用 legacy `reduce` 且对拍行 `legacy=reduce / v2=hold / changed=True / reason=sector_confirmed`；`enabled` → 应用 `hold`；`off` → 应用 `reduce` 且零对拍行。

---

## ‼️ 2026-10-22 架构修正（Node 4 事件进埋伏通道 + 置信度改排序权重 + 产业级事件召回 — 用户批准方案直接生效）

**性质**：修复即生效（用户批准，方案 `local://node4-ambush-events-plan.md`）。四问驱动（日历与 Node 4 区别 / 谁先出现 / 是否专注预期新闻 / 阈值是否该降），三路并行研究（外网文献 + 归档回测 + 代码审阅）后定为**方案 B**：Node 4 事件直接进埋伏通道 + 置信度由硬闸改排序权重 + 短名单之外的产业级召回。三条各自独立开关，可单独回退。

### 已核实现状问题（归档回测）

| # | 问题 | 数据（n=已兑现 `llm_narrative`+`global_map` conf≥0.6 共 401 条） |
|---|---|---|
| 1 | 埋伏通道只读日历、不读 Node 4 事件 | `_build_ambush_channel` 只消费 `news.catalyst_calendar.events`；10-21 实测 11 个产业级事件（稀土出口管制/厄尔尼诺/MLCC 交期 52 周/液氩涨价）对埋伏**零贡献**；`major_events` × 当日 ambush 板块重叠率 **7%**（720 条中 53 条）——进了也少命中，但「完全不进」连这 7% 都拿不到 |
| 2 | 0.7 硬闸把 LLM 保守输出全杀 | 10-21 实测 6 条 narrative `confidence` 0.50–0.65 全被拦，正式池 **0**、蓄水池 15 条大量 `no_catalyst`；59 个归档日中正式池 >0 仅 **16 天（27%）**，均值 0.47 个/日 |
| 3 | 阈值本身无区分度 | 见下表——**降阈值不损失质量**（低档 Wilson 区间覆盖高档点估计） |
| 4 | 提前量与正确率无关 | lead≥2d 组 0.41 / lead 0–1d 组 0.38（区间重叠）；但「未来事件池」有 P6 样本连续性优势（`catalyst_realizations` 已按 `event_date` 兑现） |
| 5 | P5 选股重排同样只认日历 | `_score_catalyst_relevance`（`stock_screener.py:3364`）与 G-A2 闸门（`:1703`）都只读日历 + 同一个 0.7 闸 → **本次不动**（它们是位置/下单层，严一档合理） |

| 置信档 | 已兑现条数 | 方向正确率 | Wilson 95% | 平均收益 |
|---|---|---|---|---|
| conf ≥ 0.6 | (低档集合) | 0.40 | 与另两档全部重叠 | +0.10% |
| conf ≥ 0.65 | (中档集合) | 0.42 | 全部重叠 | +0.15% |
| conf ≥ 0.7 | (高档集合) | 0.40 | 全部重叠 | +0.24% |

**文献依据**：Lucca & Moench (2015) pre-FOMC drift 30bp、Campbell et al. (2006) 机构公告前建仓 → 「预期发酵可交易」成立；ConfidenceBench (2025) + “Ordinal Gates, Cardinal Bets” (2026) → **LLM 自报置信度只适合 ordinal ranking，不适合 cardinal 硬闸**；FinMem (AAAI 2024) 分层记忆 Sharpe +30% → 「事件池」结构有据。A 股开源生态无同类完整实现。

### 用户核心命题「LLM 判断的带发酵潜在事件」的数据裁决

口径：`llm_narrative` 且 `confidence≥0.6`；提前量 = `event_date − created_at`；埋伏迹象 = 该 sector 在 `[run_date, event_date]` 内进入过 `unified.json` 的 `ambush_pool/reservoir`。

| 项 | 结果 |
|---|---|
| 产出结构（语义分类归档 narrative 标题） | fermentation 类 **395** / node 类 83 / 其他 377 —— 发酵类**已是主要产出**，不是缺失能力 |
| 三类正确率（已兑现） | fermentation 0.32（n=44，Wilson [0.20,0.47]）/ node 0.38（n=39，[0.25,0.54]）/ other 0.51（n=121，[0.42,0.60]）——区间大面积重叠，**无证据支持专挑发酵类**；ferm 平均收益 −0.55% 为三类最差 |
| 「发酵→后续埋伏迹象」组合 | 313 条发酵事件中仅 80 条（**26%**）板块在 event_date 前进入过池/蓄水池；这 80 条兑现正确率 **0.30**（n=23）**低于**未出现迹象的 0.50（n=66）——组合优势不成立（两组区间重叠、样本均 <30，不能定论，但足以否掉「显然成立」） |
| 被浪费的能力 | `news_analysis._detect_fermenting_events()`（`appearance_count≥3` 且跨日≥2 天）**全仓零调用**（grep 确认，tests 亦无引用）→ 第 6 步以「接入 narrative prompt」的方式首次利用，不新建第二套检测 |

**结论**：本期不按「发酵/节点」二分重构日历、不单独建池；阈值问题与事件类型无关，由第 3 步统一解决；`catalyst_onset_days` 字段与第 6 步发酵信号为此留好扩展点（若日后 fermentation 档 dir_correct 显著高于 node 档且 Wilson 不重叠，再单独立项）。

### 用户两个反例的根因（都成立，但根因不是「事件类型选错」，而是事件没进候选池）

| 反例 | 数据事实 | 根因 |
|---|---|---|
| 厄尔尼诺 × 农业 | 原始新闻 8-24 起连续 37 条（「地表最强厄尔尼诺来袭！种植链再掀涨停潮」8-31、「超强厄尔尼诺持续发酵」9-01 出现 3 次、气象局 9-02、发改委 9-22）；`node4_events` 只抽出 3 条（9-02/9-22/10-21），8-24~9-29 期间日历**零农业板块催化**；同期农业板块状态是 `active_mainline`/`exhausted_mainline`、`ambush_days` 恒 0 | Node 4 只抽到「气象局通报/发改委预案」这类**二手转述**，没抽到「种植链涨停潮/印尼甘蔗减产 25%」这类**价格与供需事实**；且板块 8-20 前后已进主线，埋伏窗口早已过去 → 修法在第 6 步（跨日发酵原始新闻直入 narrative prompt） |
| DeepInfer × 算力 | raw id=12870（10-20，sina 转载华尔街见闻）`DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜` + id=9278（9-30 昇腾基础设施组件开源）；两条 `appearance_count` 均 =1、`lifecycle=active`，**都没进 `node4_events`**（10-20 当日 22 条事件无一条关于 DeepInfer） | 两段式阶段 1 把 788 条压到 12 条时，这条没有「涨停/暴涨/政策」热度词、也不是多源重复的产业级开源发布排在 12 名外，LLM 从未看到 → 修法在第 7 步（独立召回通道，不依赖当日标题热度） |

### 开关与回滚

| 开关 | 缺省 | 关时行为 |
|---|---|---|
| `GOT_NODE4_AMBUSH_EVENTS_ENABLED` | true | `_build_node4_event_index` 返回 `{}` → 埋伏通道只读日历（逐字节回现状） |
| `GOT_CATALYST_CONFIDENCE_GATE_LOW` | 0.6 | 置 0.7 回现状（准入与提升同档） |
| `GOT_NARRATIVE_FERMENTING_INPUT` | true | 不拼「跨日发酵信号」段（narrative prompt 逐字节回现状） |
| `GOT_FERMENTING_RECENCY_DAYS` | 3 | 发酵原始新闻的 `last_seen_date ∈ [today−N, today]` 时效窗（真库 170 行→6 行） |
| `GOT_INDUSTRY_RECALL_ENABLED` / `GOT_INDUSTRY_RECALL_MAX` | true / 8 | 不召回（细读池大小回 12 条） |
| — | — | 三者互不依赖：任一步回退不影响另外两步 |

### 判据（机械可核对）

1. **次日 ambush 池 >0 的天数占比**是否从 27%（16/59）上升；读数口径：归档 `portfolio_decision.json` / `unified.json` 的 `ambush_pool`。
2. **Node 4 事件入池板块的 3 日超额**（`gate_notes` 含 `Node4事件(` 的候选）；`ambush_position_snapshots` 为空表，暂用 `catalyst_realizations` 的 `day_3` 近似。
3. **P6 新增 `node4_event` 样本的 dir_correct** 与既有 `llm_narrative` 档是否同区间（若显著更差 → 说明 Node 4 事件准入过松，先收紧 `_NODE4_IMPACT_WEIGHT` 再考虑关开关）。
4. **接入发酵原始新闻后** narrative 事件日均产出条数与 `confidence` 分布是否变化（若 conf 分布整体下移而无正确率提升 → 回滚该步）。
5. **产业召回后** `node4_events` 中「供给侧动作 + 产业实体」类事件占比是否上升，且 DeepInfer 类单源产业发布**次日**能入事件。

### 实测读数（落地当日）

| 项 | 结果 |
|---|---|
| 新增单测 | `tests/test_node4_ambush_events.py` **15 passed**（Node 4 入池+P4 fade 出口可达/别名 L2/负面排除/日历优先/开关回退/0.6 准入/0.58 拦截/提升仍 0.7/P6 登记/发酵 prompt/发酵开关/发酵时效与裸实体过滤/周末守卫/DeepInfer 召回+source_index 回连/召回上限与关键词组） |
| 回归矩阵 | 计划 10 文件 **146 passed**（本方案 15 + `test_node4_title_shortlist` 15 + `test_news_weight_scrub` 4 + `test_catalyst_calendar_dedup` 38 + `test_catalyst_calendar_stock_events` 5 + `test_catalyst_identity_anchor` 10 + `test_evidence_chain` 7 + `test_node6_ambush_funnel` 11 + `test_ambush_lane_split` 32 + `test_a6_wiring` 9）；并入 `test_news_grounding` 后 11 文件 **154 passed** |
| GOT 范围全量回归 | **1041 passed / 0 failed**（got/catalyst/node/ambush/news/aggregator/screener/phase/decision/archive/glue/portfolio/low_sector/market_theme/disagreement/evidence 16 类 106 文件）。唯一红项 `test_news_grounding::test_event_prompt_off_state_formats_and_matches_pre_batch` 是**逐字 prompt 快照**（钉 `source_type` 后紧跟 `}`），`onset_days` 属无条件字段契约、不随 `GOT_NEWS_EVIDENCE_GATING` → 该用例改为结构断言（不泄露 `source_index` + 示例可解析 + 覆盖 `onset_days`），不重钉快照 |
| 真库冒烟（临时 SQLite） | `CATALYST_SOURCE_TIER["node4_event"]=="reported"`；`_make_event_id("node4_event","narrative","有色",today)` 幂等；`register_event` 同 `event_id` 二次调用**不产生重复行**（1 行，`source=node4_event`、`source_type=forward`） |
| 真库只读复核（发酵输入） | 生产库 `node4_raw_news` **13,392 行恒为 `lifecycle_state='active'`**（无任何代码推进该列）→ `_detect_fermenting_events` 命中 **170 行**、最老 `first_seen=2026-08-24`；按 `appearance_count` 降序前 20 全是陈旧论坛长文/裸个股名（巨化股份 49× 至 10-16、汉钟精机 31× 至 10-08、中际旭创 29×）。**故在调用侧加两道边界**：`last_seen_date ∈ [today−3, today]`（兼作回放前视守卫）+ 标题长度 ≥8（裸实体无事件信息）→ 170 → **6 条**（`长文拆解…宝丰能源` 45×、`牧原股份…黄金猪` 20×、`吹票纯血机器人零部件` 7×、`印度央行抛售美元` 3× 等），剩余为当日仍在出现的论坛帖，prompt 明示「没有则忽略」，开关可整体关 |
| P4 出口复核 | `top_event_date` 由 `None` 改为 **`today`**（Node 4 事件即当日事件，与 P6 `event_date=today` 同口径）：`_ambush_exit_signal` 的 fade 分支判据是 `event_date and today > event_date`，留 `None` 会让 fade 型持仓**永远走不到「事件日次日卖」**（只剩 −7% 止损）。已加断言：事件日当天不卖、次日 `fade_sell` |

### 已知缺陷 / 后续

- **Node 4 事件无 `event_date`**：B 公式 proximity 固定 0.5（等价「中等临近」）。若回测显示固定值扭曲排序，再把 `catalyst_onset_days`（字段已落）接进 `_compute_catalyst_score`——本次不接。候选侧 `top_event_date` 已取 `today`（P4 出口可达），不参与该公式。
- **发酵输入仍可能带论坛帖**：时效 3 日 + 标题长度 ≥8 两道边界后，真库 6 条里仍有「晚间复盘：今天买了什么？」这类重复栏目帖——它们是当日仍在出现的长文本（机械意义上的「跨日发酵」），价值判断交 LLM（prompt 明示「没有未来节点则忽略」）；若实测拖累产出，先把 `GOT_FERMENTING_RECENCY_DAYS` 收到 2 或整体关 `GOT_NARRATIVE_FERMENTING_INPUT`。
- **G-A2 / P5 的 0.7 闸本次不动**：全链路统一放宽属独立决策（下单层风险不同）。
- **「发酵事件专项池」本期不做**：数据未证明其优于节点类（见上表），扩展点已留。

---

## ‼️ 2026-10-22 补登（Node 4 两段式全量标题初筛 + global_map 事件目录治理 — 用户批准直接生效）

**性质**：修复即生效（用户批准的实施方案，非影子观测）。两项改动：
① Node 4 由「weight 排序取前 50 条喂细读」改为两段式——阶段 1 把当日**全部**候选标题（无条数上限、无规则打分、无 weight 排序）交 LLM 初筛 ≤12 条，阶段 2 只对选中条目细读抽事件；weight 删除「热度」项（只留来源 40%/时效 35%/多源 25%，仅用于展示顺序与 NGA 保底），LLM 失效时**不降级到规则排序**（宁可当日无事件，不可产出错事件）。
② `GLOBAL_TO_A_SHARE_MAP` 收紧为固定事件目录：13 条全补 `event_type_fixed`/`review_note`；日期确定型（earnings/policy/data/macro）缺 `anchor_keywords` → fail-closed；一次性大事件单列登记到 `src/got/global_events_manual.py`（缺省空表）。

**补读口径修正（真库实测当天，取代计划原案）**：计划原定「`appearance_count` 降序取前 20 条」。真库只读复跑显示该排序被多轮热榜刷高计数的娱乐/社会条目占满（云南锗业 / 肖战演唱会 / 唐禹哲缓刑…），`appearance_count=1` 的 DeepInfer 被挤掉——与 weight 热度项同一类误判（用「热度」代理「价值」）。改为：**不做热度排序/预截断**，近 3 日窗口（覆盖周末；`[today-3, today)`）内未吸收标题**全量**回灌，仅按 `_normalize_title` 去重；候选价值只由 LLM 粗筛判定。吸收判定直查 `Node4EventRecord.title`（`last_seen` 覆盖 N+1 天），不用按 lifecycle 过滤的 `_load_recent_news_events`（fading 事件会让已吸收原稿被重复回灌）。

### 开关与回滚

| 开关 | 缺省 | 关时行为 |
|---|---|---|
| `GOT_NEWS_TITLE_SHORTLIST_ENABLED` | true | 回旧路径：`_format_news_for_prompt(raw_news, max_items=50)`（weight 已是三项版） |
| `GOT_NEWS_TITLE_SHORTLIST_SIZE` | 12 | 调整细读条数（漏检多→20，成本超支→8） |
| `GOT_NEWS_CARRY_PREV_TITLES` | true | 不回灌昨日漏读标题 |
| map 治理字段 | 无开关 | 缺 `anchor_keywords` 的日期确定型映射不产事件；补锚定词即恢复 |

### 判据（机械可核对）

1. **粗筛覆盖率** = 进入粗筛 prompt 的当日标题数 / 本轮 `_fetch_news()` 返回条数，应 = 1.0（旧口径 = 50/N）。⚠️ 分母口径：`_fetch_news()` 的返回 = `merge_news` 排序后 `MAX_MERGED_NEWS=150` 截断 **+ `_merge_with_intelligence` 追加**（Intelligence 在截断之后并入），实测 2026-10-21 为 **246**；`node4_raw_news` 的 364/660 行是跨轮累计，**不得**当分母。昨日补读条目另计（不占当日分母）。核对：日志 `[Node 4] 标题粗筛: N → M 条`（N 减补读数即当日分母）；单测 `test_shortlist_sees_all_titles` 钉 200/200。
2. **昨日发酵条目次日入事件比例**：`node4_raw_news` 中近 3 日未吸收标题被 `[昨日]` 回灌后进入 `node4_events` 的比例；DeepInfer 类条目次日应可见（真库只读复跑已确认 carried 含 DeepInfer；单测 `test_deepinfer_scenario_end_to_end` 构造性证明全链路）。
3. **weight 热度项删除后的分差**：同来源/同源数/同时效下，含「涨停」与不含的标题得分必须**相等**（差 0，旧 20 分）；跨来源榜单口径（同花顺热榜 3 源 vs 新浪单源）实测 22.2 / 14.8 = **1.50×**（旧 1.93×），剩余差异全部来自信息源质量项。⚠️ 原计划写的「≤1.3×」与计划自身的预期数字（19.4/13.4≈1.45×）不自洽，且与「保留来源/多源质量」的设计冲突——本页按**实测口径**登记：同条件差 0、跨来源 ≤1.6×。
4. **误纳入行情播报**：次日 `major_events` 中「XX涨超X%」「多股涨停」类条目应为 0（粗筛 prompt 明列排除；若出现按 `GOT_NEWS_TITLE_SHORTLIST_SIZE`/prompt 迭代，不回退规则）。

### 实测读数（2026-10-22）

| 项 | 结果 |
|---|---|
| 单测矩阵（6 文件） | **79 passed**：`test_node4_title_shortlist` 15 / `test_news_weight_scrub` 4 / `test_global_events_manual` 7 / `test_catalyst_calendar_dedup` 38（+9）/ `test_catalyst_calendar_stock_events` 5 / `test_catalyst_identity_anchor` 10 |
| 全量受影响回归 | 与上表 6 文件合并跑 14 文件 **170 passed**（+ `test_catalyst_gate_forensics` 5 / `test_node6_ambush_funnel` 11 / `test_low_sector_rescue` 13 / `test_ambush_lane_split` 32 / `test_a6_wiring` 9 / `test_evidence_chain` 7 / `test_news_grounding` 8 / `test_news_pulse` 6） |
| weight 回放 | 涨停潮(ths_hot,3源) **22.2** / 英伟达财报(wallstreetcn,3源) 21.0 / 宁德·DeepInfer·厄尔尼诺(sina,1源) 各 **14.8**；同条件含/不含「涨停」均 14.8 |
| 真库冒烟（临时 SQLite） | 4 条 raw（今日 1 / 昨日 2 / 前日 1）+ 1 条 **fading** 已吸收事件 → 只回灌两条未吸收标题（昨日 DeepInfer + 前日未吸收），今日行与已吸收行均被排除；`sources_seen` 为空的历史行不再让日历加载失败（`max(1, len(...))` 护栏） |
| 真库只读复跑（2026-10-21 晚） | `_load_unabsorbed_prev_titles()` 返回 **545 条**未吸收标题（近 3 日去重后）且**含 DeepInfer**；对照原计划 `appearance_count` 降序前 20 条 → **不含** DeepInfer（全为热榜娱乐/社会条目）→ 已按上方「补读口径修正」改掉热度排序与预截断 |

### 真实运行观察（2026-10-21 晚 21:39–21:52，用户授权整跑 `--got`，日历强制 full 重建）

| 项 | 读数 |
|---|---|
| 标题粗筛 | **788 → 12 条**（今日 `_fetch_news` 246 条 + 近 3 日补读 542 条）；一轮小调用覆盖全池，旧口径只喂 50 条 |
| 事件抽取 | `major_events` **11** 条（旧归档 8 条）。新增产业/政策级：厄尔尼诺全球农业能源影响、液氩 7 天涨 25%、三星电机 MLCC 交期 52 周、日本稀土储备+出口管制、长川科技净利 +98~110%；**旧版的「创新药概念再度活跃，多股涨停」类行情播报不再入选** |
| DeepInfer | 在补读池内但**未被 LLM 选中**（当日 11 条事件竞争）——机制覆盖到位，选择权在 LLM（符合「不按规则预筛」） |
| 催化剂日历 | `refresh_mode=full`，**11 事件 = macro_rule 5 + llm_narrative 6**，global_map **0**；#2/#7/#8 fail-closed 警告按预期出现。**global=0 归因（逐条核库）**：① OpenAI 拟融资 / 苹果 iPhone Duo 供应链 / 三星 8nm 三条 `last_seen=10-20`、`lifecycle=fading`，被 `_load_recent_news_events` 的 emerging/fermenting/peak 过滤；② 今日「英伟达 GTC 柏林」「英伟达商谈 Firmus」「三星电机 MLCC」均无财报语义 → 被 #3/#12 锚定词正确拦截。**不是映射失效**：旧 22 条是增量累积，full 重建只从活跃事件重算 + 锚定词生效。6 条 narrative `confidence` 全 <0.7 → `reasoning.md` 只渲染 PMI（`archive.py` 的 `high_conf` 0.7 展示门，既有口径；6 条仍在 news.json） |
| 清理修正 | full 重建后库内仍留 **9 条旧 global_map 行**（5 条「OpenAI 拟融资 300 亿美元」顶了发布会、3 条「三星 8nm 工艺」顶了 SK 海力士财报、1 条苹果供应链）——`produced_sources` 只清「本次有产出」的来源，global=0 时跳过。**修**：`global_map`/`global_manual` 是规则/静态目录输出，产出 0=无命中而非降级，无条件参与残留清理；用本次归档 11 事件实跑 `_cleanup_stale_events` → 9 行清零，DB 仅剩 5 条 macro。否则同一交易日再跑（增量 `_load_active_events`）会把旧行原样载回 |
| 副产品 | `node4_raw_news` 今日 391 行；`node4_events` 今日 21 行；`catalyst_refresh` 重建为 `(2026-10-21, 0)`，下次走增量 |

### 宏观日历退出（2026-10-22 用户裁决）

- **决定**：PMI/CPI/社融/LPR 退出催化剂日历（此前 2026-10-21 只退出评分入口）。开关 `GOT_MACRO_CALENDAR_ENABLED`（缺省 **false**）。
- **关闭时行为**：`_generate_macro_calendar` 返回空（不再生成、不注册 P6）；`_load_active_events` 过滤存量 `macro_rule`（增量路径不复活）；`_cleanup_stale_events` 把 `macro_rule` 纳入清理（全量重建删存量）。
- **依据**：macro 已兑现 12 条仅 1 条 |收益|>2%；PMI 两条 +0.57% / −0.50%；calibration 报告 §5.1 判定 macro n=17 样本不足。⚠️ 口径：`drift_direction_correct` 对 `binary` 的判据是 `abs(actual_return) > 2.0`，不是涨跌方向。
- **落地**：存量 9 条 `macro_rule` 行已删；`catalyst_events` 当前 0 行（6 条 narrative 被 T2 影子 DROP 未落库）；P6 `catalyst_realizations` 历史 17 行保留。
- **影响面与回滚粒度**：PMI / CPI-PPI / 社融-M2 / LPR **四类一并退出**（无 per-rule 开关，回滚只能整类）。LPR 虽为 ★★★，P6 实测同样无大波动（10-20 银行 −0.50%/券商 −0.27%；09-20 银行 −0.42%/券商 +0.36%）；若日后只恢复某一类，需另加 per-rule 开关。
- **回滚**：`GOT_MACRO_CALENDAR_ENABLED=true` 恢复规则日历（评分入口仍受 `GOT_CATALYST_MACRO_AS_REGIME_REF` 控制）。


### 已知未闭合

- **被 fail-closed 的映射（3 条，有意过渡态）**：#2 美国 CPI/PCE（data）、#7 台积电/ASML 财报（earnings）、#8 礼来/诺和诺德财报及临床（earnings）——缺锚定词即不产事件；恢复方式=给对应 mapping 补 `anchor_keywords`（财报/数据语义），无需回滚开关。国内 PMI/CPI/社融/LPR 仍由 `macro_rule` 日历覆盖（#2 被砍属去重）。**#1 FOMC 例外保留**：`macro_rule` 四条不含 FOMC，砍掉即净损失，已补会议语义锚定词 `["FOMC","议息","利率决议","会议","点阵图"]` 并标 `policy`（「鲍威尔/美联储/Fed」裸提法仍被排除）。
- **单列通道缺省空表**：`global_events_manual.MANUAL_GLOBAL_EVENTS = []`，DeepInfer 仅作注释示例；登记后才会产事件（避免把未核实新闻直接注入生产日历）。

---

## ‼️ 2026-10-21 补登（板块校验误伤修复：军贸行业过滤 + 实际板块名透传 + 白名单扩容）

**性质**：修复即生效（用户 2026-10-21 裁决修法：① 单检索源按现状工作；②③ 按推荐方案实施）。三项改动全部落在「验证上游」与「检索词口径」，**不新增裁判权限、不放松身份护栏**（T1/T2/T3 三条件逐字未改）。

| 项 | 改动 | 一处回滚方式 |
|---|---|---|
| backlog §7 #18 军贸漂移 | `stock_screener.py` `concept_industry_map` 补 `"军贸概念": ["军工","国防","航空","航天","兵器","船舶","雷达","导弹"]` → `_try_query` 内既有行业过滤循环由 no-op 转为生效，绿电/消费漂移票在入池前被剔 | 删该行（恢复「不过滤」旧行为） |
| backlog §7 #19 标签错位 | 问财实际查询名 `sector_query_board`（`resolve_with_kind`/别名改写/自愈产物）随成分股 dict → universe 行 → `ScreenerStock` → Node 7 `_verify_candidate_sectors` 的检索模板与 `boards` 首位（`verify_board = sector_query_board or tagged_sector`）。**Node 6 标签保留**：`sector_intel_map` 键、FXG 剔除名单、family 归因、`sector_verify_evidence[].sector` 全按标签工作 | 代码 revert；字段缺省 `""` 时逐字节回旧行为 |
| 检索源白名单 | `EVIDENCE_SOURCE_ALLOWLIST` 12 → 15 域（`sohu.com`/`qianzhan.com`/`southmoney.com`），`is_allowed_source` 精确/后缀判定逻辑不变 | 删三行 |

### 判据（机械可核对）

1. **行业过滤生效**：归档日 `candidate_pool.json` 的「军贸概念」行内不应再出现 TDX 归属光伏/电力/环保的票（601778 晶科科技 / 浙江新能 / 江苏新能 / 芯能科技）。若过滤后为空，由既有第 1.5 轮（DDE 0.5%→0.3%）与第 2 轮（不带 DDE）降级接管；仍为空则当日该板块零票，属 fail-closed 可接受结果，不加回「不过滤」分支。
2. **检索词口径**：`portfolio_decision.json.sector_verify_evidence[].query` 应含问财实际板块名（如「液冷服务器」）而非 Node 6 标签（「液冷服务」）；`candidate_pool.json` 每行新增 `sector_query_board`，标签 vs 实名差异可离线核对（D1 事后验证样本源）。
3. **单源假阴性**：白名单扩容**不改变**单源约束——Bocha 零结果仍判 `insufficient_evidence`。⚠️ 本页 10-20 节「白名单召回损失摊开」实测：22 条 off-list 里 21 条本就过不了身份护栏，对**当日样本**扩容零召回收益；预期收益在跨日的 F10/概念成分页，不是 10-20 那批行情页。故本条不承诺召回提升，只承诺「多进入护栏判定的样本」。

### 验证读数（2026-10-21）

| 项 | 结果 |
|---|---|
| 单测 | `test_verify_candidate_sectors.py` **16 passed**（14 + 2 新增：实名检索保标签 / 空串回落标签）；`test_sector_evidence.py` **12 passed**（11 + 1 新增：扩容正例 + `evil-sohu.com`/`sohu.com.evil.cn` 后缀伪造反例）；`test_junmao_industry_filter.py` **2 passed**（新建，打桩问财返回 5 只混合行业：绿电两票被剔、船舶/航空三票保留；`液冷服务→液冷服务器` 写回 `sector_query_board`）；`test_family_topping_suppression.py`+`test_ambush_lane_split.py` **89 passed**（候选池快照新键断言） |
| 端到端（真实 Bocha + LLM，001400 江顺科技 × 液冷服务） | `sector_query_board="液冷服务器"` → query `江顺科技 001400 液冷服务器 概念股`、`sector_available=True`、evidence 引证券之星「1月7日江顺科技涨停:液冷服务器…」；对照组 `""` → query 回落「液冷服务」亦为 True（10-20 的 False 当日已不可复现，检索源覆盖随日变化；修复判据以 `query` 口径为准——实名出现在检索词中即成立） |
| 预期外未闭合 | 001400 在 10-20 判 False 的根因中「Bocha 未召回」部分无法在 10-21 复位验证；若后续再出现 insufficient，按运营项（配第二检索源/自建 SearXNG）处理，与本修复判据无关 |

---
## ‼️ 2026-10-21 补登（催化剂价值分级 #1：identity 重锚 + macro 退出评分池 — 用户裁决跳过影子）

**性质**：用户 2026-10-21 裁决「全套重构、不加影子」——两件直接生效改动：① `catalyst_identities` 身份表以 `global_event` 为单位锁定首次 catalyst_date：重锚判据是**陈旧度**（`news_date - first_anchor_date >= CATALYST_WINDOW_DAYS`，即 14 日历日）而非「catalyst_date 是否仍在窗口」——增量路径 `start=today` 且只载 1 天新闻，用窗口判据会在锚定到期次日必然重锚、新 event_id 造新 P6 行（等于没修）；同一事件实例内只递增 `anchor_count`、日期永不滚动，到期实例由 `catalyst_date < start` 谓词自然落历（根治「同一 SK 海力士事件 19 行」的日期进 ID 永生问题，Tetlock 2011 陈旧新闻证据）；② `CATALYST_MACRO_AS_REGIME_REF=true` 把 PMI/CPI/社融/LPR 从聚合/选股/Phase2 三个评分入口排除（macro 实测方向正确率 1/10 + Savor-Wilson 2013 公告溢价=事件日属性，非板块方向催化剂），事件仍入 `catalyst_events` 与归档。**回滚键**：`GOT_CATALYST_MACRO_AS_REGIME_REF=false` 一键恢复旧行为（macro 重新参与评分）；identity 锚定无开关，回退 = 回退代码。

### 三条判据（机械可核对）

1. **identity 表增速**：每日 `catalyst_identities` 新增行数 ≤ 当日 global 模板命中组数；同一 identity 的重命中必须走 `anchor_count` 递增而非新行；`catalyst_realizations` 中同一 (sector,title) 的重复行日增速应趋 0（首跑基线见 calibration 报告 §1）。核对 SQL：`SELECT identity_key, anchor_count, catalyst_date FROM catalyst_identities ORDER BY updated_at DESC LIMIT 20;`
2. **macro 评分入口 zero-count**：归档日 `unified.json`/`catalyst_calendar` 中 `source=macro_rule` 事件仍在，但 `events_by_sector` 派生物与 P5/`_ga2_gate` 消费侧不出现 macro_rule；抽查方式 = 在归档 `portfolio_decision.json` 的 `lane_budget_split_shadow`/`t3_routing_shadow` 与 `unified.json` 的催化相关字段中 grep `macro_rule` 应为 0（日历本体除外）。
3. **calibration 报告作为调参依据**：`reports/catalyst_value_tier_20261021.md` 的 §2/§5 kept/excess/正确率表是 conf 闸与 identity 冷却窗口的唯一调参凭据；样本 <30 的行不得出结论（`calibration script --min-samples 30` 纪律）。

### 已知未闭合（等 calibration 报告拍板，不在本次范围）

- **重锚窗口取值**：当前用 `CATALYST_WINDOW_DAYS`（14 日历日）作为「同一实例」的判定边界；7/30 日的替代取值待 calibration 报告 §5 的 kept/excess 表拍板（代码内常量引用，非新增开关）。
- **conf 闸下调**：0.65-0.7 段质量与 ≥0.7 相当（已兑现样本正确率 52.9% vs 51.6%），0.7 闸砍掉的是可用样本；是否下调到 0.65 待 calibration 报告 §2 分档数据拍板。
- **T-1 前瞻窗检验**：文献 Hu et al. (2019) / Gao-Hu-Zhang (2022) 指出事件价值分布在公告前而非公告后，但当前 schema 无 T-1 收益列，需另立数据 WI 才能检验。calibration 报告 §4 诚实 SKIP 并登记数据缺口。


---
## ‼️ 2026-10-20 转正补登（D3 分道预算 + ACTIVE 转正 + D4 union — 用户裁决跳过影子纪律）

**性质**：**转正即生效**，非影子观测。用户 2026-10-20 明示裁决：原 C4 四判据（D1 前向超额分箱显著区分度 + Layer 2 积样 ≥20 交易日/≥30 票 + `SCORING_FORMULA_VERSION` bump + 基线 diff 归零）中的「影子 ≥10 交易日」纪律在本次不执行，直接修正生效；D1 标定**降级为事后验证**。本注记即该裁决的出处登记，防后续审计误判为纪律违规。

### 三项生效改动

| 项 | 开关（缺省） | 生效语义 | 一处回滚方式 |
|---|---|---|---|
| **D3 分道预算** | `GOT_LANE_BUDGET_ENABLED`（**1**，call-time 直读） | `_allocate` 的 `per_stock_budget(c) = buy_budget × w(lane(c)) / Σ w(lane(top3))`，恒等式 `Σ = buy_budget`。选股仍取 `buy_candidates` 复合分降序 top3（不引入跨道单尺重排）；lane 由 `sector_type` 经模块级 `_stype_to_role` 映射（`active_mainline`→`mainline`，`ambush`→`ambush`；裸 `mainline`（schema 缺省值＝道身份缺失）与其余值、以及 `sector_name` 为空的未验证票→`unknown`）；数值沿用 D2 登记的 `LANE_BUDGET_PARAMS`（1.0/0.55/0.3/0.55），无新参数 | `GOT_LANE_BUDGET_ENABLED=0` → 逐字节回「等分×`ROLE_MULTIPLIER`」旧路径 |
| **ACTIVE 转正** | `GOT_AMBUSH_SPLIT_ACTIVE`（**1**，catalyst_config 登记 + `portfolio_decision.py` 运行时消费点两处同步） | 拦点#1 action lift（lane 过闸埋伏票 `priority = max(priority, 55)` → `buy_dip` 可达 `buy_candidates`）+ 拦点#8 tech 维折算（`ambush_rank_score×100`）；bear 闸豁免 2026-10-13 已无条件，不随本开关 | `GOT_AMBUSH_SPLIT_ACTIVE=0` → 回影子态（仅 `action_gate_watch` trace，零决策改动） |
| **D4 合并 union** | 无开关（语义修正，非观测） | Phase 2 生产路径 `_filter_got_finalists(..., lane_merge_mode="union")`：主线仍 Round 5 top_n 截断，埋伏道 `lane_finalists` 全量追加不丢（道内 `AMBUSH_LANE_TOPK=3` 兜底），`lane_overflow_n` 恒 0，撞票仍归主线；`--screener` 展示入口保持缺省 `topn_fill`。生产 `unified_scoring` 条数上限 10 → 13 | 代码 revert |

**诊断链（为何三者必须同时生效）**：D4 缺失时主线先占满 10 名额 → 埋伏候选整道不可见；ACTIVE=0 时 lane 过闸但 `lifecycle_phase=None` 的埋伏票 `priority=50` → `action=watch`（仅 trace），永远进不了 `buy_candidates`；D3 缺失时「等分×角色打折」让混合道日预算蒸发（2 主线+1 埋伏 = 0.85×B、纯埋伏 3 只 = 0.55×B）。三者任一缺失，「决策层能买到埋伏票」即不通。

**路径级可观测性（新增/变更）**：`sector_limit_detail` 增 `lane`/`lane_weight`/`lane_budget_applied`（分道预算比可由 allocation 实际金额复算）；`lane_budget_split_shadow` 的 `lane_budget_applied` 改读 call-time 开关（不再恒 false），道归属改按实际生效道（`lane` 键，缺省回落 `sector_role`）；`lane_trace` 增 `lane_merge_mode`；`candidate_pool.json` universe 行增 `sector_type` 道标签（供 D1 回测直读）。`SCORING_FORMULA_VERSION` = `2026-10-20.1`——**跨此版本的前向统计不得与前序归档混池**（预算口径 + 池规模双变）。

**事后验证（替代原事前判据）**：`scripts/backtest_lane_budget.py` 的 lane 判定改为「优先读 `candidate_pool.json` 的 `sector_type` 标签，无标签回退 `sector.json` 名单匹配」；新交易日自然积累带标签样本，攒到 ≥20 交易日 / ≥30 票后重跑，若出现「无 lane 主胜出」或「道间差距 <2pp」→ 用上述两个 env 分别回滚（不动代码）。该脚本的窗口/分箱/双基准/`MIN_SAMPLE_SIZE=3` 口径全部未变。

**验证读数**：新增 `tests/test_lane_budget_d3.py`（7 例：混合道全额投放 + 埋伏票入 allocation / 纯埋伏 B÷3 / 纯主线等分 / `=0` 旧路径 0.85×B 回归 / 未验证板块落 unknown / 两开关缺省=1 / `_stype_to_role` 映射）+ `tests/test_trade_plan_log.py`（10 例）+ `test_ambush_lane_split.py` 改写与新增（union 全量追加 / `topn_fill` 溢出丢弃 / 撞票计数 / ACTIVE 缺省 lift→`buy_dip` 与 `=0` 对照 / Phase 2 调传 `"union"` 断言）；`test_portfolio_decision_lot_rejection.py::test_affordable_candidate_buys_whole_lots` 由 100 股改 200 股（旧断言钉在 0.55 折后预算，D3 下道权重自相抵消）。

### 决策编排层留痕（第四项，零决策改动）

`PortfolioDecision.trade_plan_log: list[str]` + `decision_plan.reconcile_log_lines/assemble_log_lines` 两个纯函数 + `run()` 开头 reconcile / 末尾 assemble 两处接线回填 + `archive._append_phase2_reasoning` 在 Node 7 段渲染「### 决策编排层（trade_plans 装配/对账）」。此前两处返回值在接线处被丢弃（只进 SQLite），人工复盘看不到装配/对账。空列表不渲染 → off 态 `reasoning.md` 逐字节不变；同日重跑随 Node 7 段一并丢弃重建（段落数恒 1）。

### 首个观测日读数（2026-10-20 实跑 `main.py --got`，D1 事后验证第 1 个带标签日）

| 检查项 | 读数 | 判定 |
|---|---|---|
| `candidate_pool.json` universe 道标签 | 44 / 44 行带 `sector_type`（`active_mainline`/`ambush`） | ✅ 供数就绪（D1 脚本可直接读） |
| 埋伏道供给 | `lane_in=29 → G-A1 soft_pass=29 → G-A2 pool_pass=29 → G-A3 29 → G-A4 selected=2` | ✅ 供给非零 |
| union 合并 | `lane_merge_mode=union`、`cross_lane_collision=0`、`lane_overflow_n=0`；`unified_scoring` 含 2 只 `sector_type=ambush`（001400 / 002076）+ 1 只主线（601778） | ✅ **埋伏道首次进入生产 Phase 2 打分池**（旧语义下被主线 10 名额挤空） |
| ACTIVE lift | `action_gate_lifted=[]`（两只埋伏票 `lifecycle_priority` 均 >50，未落入 lift 触发带） | ⚪ 未触发（band 未命中，非开关失效） |
| 买入侧 | `allocation` 0 条；两只埋伏票 `sector_verify_miss`（问财标签「液冷服务」/「数据中心」与 efinance 板块不匹配 → `sector_name` 清空 → Q9-B 强制 watch） | ⚠️ 供给被板块身份验证闸门吃掉，非 D3/D4/ACTIVE 缺陷 |
| `lane_budget_split_shadow` | `[]`（当日 0 条 buy allocation，无道可归集） | ⚪ 数值语义由 `test_lane_budget_d3.py` 承保，待首个有买入的交易日复验 |
| `reasoning.md` 决策编排层段 | 「### 决策编排层（trade_plans 装配/对账）」1 段 2 行（`昨日对账(2026-10-19): 无 pending 计划待回填` / `当日计划装配: allocation 为空，无计划写入`） | ✅ 渲染 + 幂等（重跑段落数仍为 1） |
| `portfolio_decision.json.trade_plan_log` | 同上 2 行随 model_dump 落盘 | ✅ |
| `meta.json.scoring_formula_version` | `2026-10-20.1` | ✅ 版本戳生效 |
| `meta.json.funnel_stats.round_survival_counts` | 扁平含 `lane_merge_mode='union'` / `cross_lane_collision=0` / `lane_overflow_n=0` / `g_a4_selected=2` | ✅ |

**已知无关告警（非本轮引入）**：`data/got_daily/20260917/portfolio_decision.json` 为历史坏档（`json.loads` 报 `Expecting value: line 298 column 18`），`_build_shadow_day_index` 扫历史日时打一条 `[Archive] 影子产物读取失败` WARNING 后降级（今日文件本身有效）。该坏档未修复（历史数据处置不在本轮范围）。

**次日复验项**：① 有买入的交易日核对 `lane_budget_split_shadow[*].lane_budget_applied=true` 且分道金额与 `allocation.target_value` 复算一致；② 埋伏票穿越板块验证闸门的交易日，确认 `action=buy_dip` 且出现在 `allocation`；③ 带标签样本按 `scripts/backtest_lane_budget.py` 自然积累至 ≥20 交易日/≥30 票后重跑 D1。

**🔴 阻塞项（未关闭，本轮不得判为「冒烟通过」）**：计划验收口径「lane 供给>0 的交易日 → 至少 1 只埋伏票 `action=buy_dip` 且进 `allocation`」**今日未达成**。证据链：`unified_scoring` 三只候选（001400 江顺科技 / 002076 *ST星光 均 `sector_type=ambush`、601778 主线）全部 `sector_available=false`、`sector_name=null`、`action=watch`；`ambush_lane_trace.sector_verify_miss` 同时列出 001400/002076；`allocation=[]`、`lane_budget_split_shadow=[]` → **D3 分道预算数值路径今日执行 0 次**。**RCA（2026-10-20 复核，拆两类，勿再写成单一「3/3 蹭概念」）**：`_verify_candidate_sectors`（portfolio_decision.py:1573）在 union 合并后的 3 只 finalists 上逐只校验，L1 未命中 → LLM 仲裁（`sector_verify_degraded=None` 证实非降级分支）判不匹配 → 清空 `sector_name` → Q9-B 强制 `watch`。但三条失配的成因完全不同：

- **① 军贸概念（601778 主线票）= 问财成分股污染，该拦**：15 只成员里只有 4 只与军工/军贸相关（300589 江龙船艇 / 600435 北方导航 / 300065 海兰信 / 000576 甘化科工），其余 11 只（002626 金达威、600032 浙江新能、603693 江苏新能、601778 晶科科技、芯能科技、同有科技、宜安科技、亿田智能、正强股份、日盈电子、松原安全）与军贸无关 → THS 按板块名语义检索跑偏，候选集本身不可信。
- **② 液冷服务 / 数据中心（001400 / 002076）= 词表未桥接 + 仲裁过严，不该拦**：29 只成员大体是 AI 服务器供应链（飞荣达 / 同飞股份 / 苏州天脉 / 立讯精密 / 蓝思科技 / 润建股份…）。机制：`data/wencai_board_aliases.json` 已记 `液冷服务 → 液冷服务器（source=auto, verified=2026-10-20，当日自愈写入）`，即问财查询实际跑的是 THS 板块 `液冷服务器`，而 `universe[].sector` 仍保留 Node 6 名 `液冷服务`；校验比的是**原始标签**，走另一张表 `src/got/board_aliases.normalize_sector_name`（无该桥接：`液冷服务→液冷服务`、`数据中心→数据中心`，而 THS 实名是 `液冷服务器` / `数据中心(AIDC)`，见 `data/ths_concept_boards.json`）→ **L1 结构性必失配**，只剩 LLM 仲裁兜底；且东财侧常无对应概念（001400 的 19 个东财板块无「液冷」，002076 的 23 个无「数据中心」，601778 有「算力概念/阿里概念」）→ 两套 taxonomy 不能直接等价判定。
- **③ 设计缺口（登记待办）**：校验比对的是 Node 6 标签而非**问财实际查询板块**，且 `wencai_board_aliases` ↔ `board_aliases` ↔ `ths_concept_boards` 三张表互不相通。候选修法：`resolve_with_kind` 产物随 universe 透传作为校验标签 / 三表桥接 / GOT 信任模式（等同撤回 09-22 审核 3.3，需拍板）。

与本轮改动无关（主线票同样被拦）；`action_gate_lifted=[]` 另因两只埋伏票 `lifecycle_priority`>50 未落入 lift 触发带。**关闭条件**：出现「埋伏票通过板块验证 + `action=buy_dip` + 进 `allocation`」的交易日，或在校验链路修复（标签改用 resolved board / 别名表桥接 / 降级口径）后复验；在此之前 D3 金额正确性只有单测（`test_lane_budget_d3.py` 8 例）承保，无生产读数。**同日口径校准**：`sector_available_false_count` 在 20261019 为 **3/4**（4 只候选清 3 只）、20261020 为 **3/3**、20261013 为 **3/3**——「3/3 全灭」只属 1013/1020 两天。

### 板块身份校验改 fail-closed（LLM 不可用即报错，2026-10-20 用户裁决）

> **已被取代（2026-10-20 晚）**：裁判已从 efinance 换成「联网检索证据 + LLM 判定」，本节的 fail-closed 语义（raise → status=failed → 退出码 1）继续保持，但 efinance 比对、`efinance_empty/efinance_error` 两个 reason 已随实现删除。现行实现见下方「板块身份校验换裁判」小节。

**裁决原文**：「llm不可用报错，这是决策系统，又不是应用，断了总比拿错误数据出决策好」。据此废止审核 3.3 的「LLM 不可用 → 保守清空标签继续出决策」路径——它把「数据缺失」伪装成「判定为不匹配」，当日仍在无人察觉的降级态产出 watch/空仓建议（降级痕迹只埋在 `data_quality` 里）。

| 项 | 旧（审核 3.3，已废止） | 新（fail-closed） |
|---|---|---|
| LLM 调用失败/超时 | 清空标签 → 维度剔除 → **继续出决策** | `raise SectorVerifyUnavailable` → Phase 2 失败 |
| LLM 返回空 / 无 JSON 数组 / JSON 解析失败 / 非数组 | 返回 None → 同上 | 同上（raise） |
| LLM 覆盖不全（缺案例索引） | 缺的索引默认 None → 当作"不匹配"清空 | raise（缺数据 ≠ 判定） |
| LLM 给出列表外板名（防幻觉 guard 命中） | 丢弃该命中 → 当作"不匹配"清空 | raise（坏数据不参与裁决） |
| LLM 判「不匹配」（判定本身可信） | 清空标签 | **不变**：清空标签 + `sector_available=false` |

**落点**：`portfolio_decision.py` 模块级 `SectorVerifyUnavailable(RuntimeError)`；`_llm_arbitrate_sector_match` 六类坏输入全部 raise（含防幻觉 guard 由"丢弃"改"报错"），并用不同错误消息区分「LLM 不可用」与「LLM 输出不可信」（后者含"覆盖不全，缺案例 [..]"与"给出列表外板名"），归档里可直接定位失败类别；`_verify_candidate_sectors` 删掉保守清空分支，改为「置 `_sector_verify_degraded/_pending_count` 后原样上抛」，输入 stock 对象不被修改。

**出口（必须失败，否则等于静默无决策）**：`orchestrator.run()` 的 Phase 2 except 分支 → `logger.exception` + 落 `status="failed"` 占位 `portfolio_decision.json`（`allocation=[]`、`warnings` 载原因）；`main.py --got` 分支读回决策后经 `_phase2_exit_code(decision)`：**决策缺失或 `status=="failed"` → `return 1`**（normal/partial → 0）。这样调度器/终端拿到非零退出码，占位产物只承担"区分没跑 vs 跑崩"的归档职责，不再冒充成功。

**未加额外重试**：`LLMToolAdapter` 路由已配 `num_retries=2`（`llm_adapter.py:404/441`），单次网络抖动已在适配器内消化；本层再加一层会叠成 6 次调用，反而拉长整日决策延迟。

**未改动（同类问题待裁决）**：`efinance.get_belong_boards` 取不到板块列表时仍「保留问财标签」继续（`portfolio_decision.py:1630-1640`）——同样是静默信任，只是失败源从 LLM 换成 efinance。按同一裁决原则应否一并 raise，待负责人拍板（该路径触发频率显著高于 LLM 仲裁失败：网络抖动即命中）。

**观测补丁（同一轮，2026-10-20 review）**：既然 efinance 回退暂不改语义，**至少让它可数**——新增 `PortfolioDecision.sector_verify_unverified: [{symbol, sector, reason}]`（reason ∈ `efinance_empty` / `efinance_error`），逐票留痕并在日志 + `[板块验证] 完成` 行加计数。**动机（实测）**：20261016 的 `confirmed_sectors` 确实含 石油/石油开采/路桥（排除 `tag∉intel_map` 的跳过分支），而 `data_quality` 是 `sector_verify_degraded=null / sector_available_false_count=0`；重拉东财后这三只 L1 命中均为 False → 当日是「没验过即放行」，却与真命中在归档里**完全同形**。任何 precision/recall 的分母必须先扣掉这些行，否则回测数字不可信。

**样本源解耦（同一轮）**：`candidate_pool.json` 快照不再挂在 `GOT_FAMILY_TOPPING_GATE_ENABLED` 下（原条件 `portfolio_decision.py:4258`）。它是 **D1 分道预算事后验证 + 板块匹配度评估的唯一带标签样本源**（universe 行保留验证前的原始 `sector_name`/`sector_type`），挂在无关闸门上会让「关 FTS」静默断掉样本积累，且事后无法从归档判断样本断在哪天。解耦后每日无条件落盘（try/except 降级、~15KB/日，同 `meta.json` 的「跑过就有文件」契约）。

**现状口径别写错（2026-10-20 订正）**：板块标签被清空（`sector_available=false`）目前经 Q9-B（`portfolio_decision.py:4492-4496`）**强制 `action=watch` ⇒ 当日买不进**；「只剔板块维度权重、不强制 watch」是 **B 方案**（尚未拍板），不得当成既成行为描述。

#### 行为证据判别实验（否定结论，2026-10-20；零代码改动、无消费点）

**动机**：同源自证不可行（mobile 问财不支持「按股票反查概念清单」，四种问法均 0 行），跨 taxonomy（东财）结构性误杀，于是考虑"用市场行为判归属"：剔除市场因子后的**残差相关 + 超额同号率**。一次性探针（mootdx 日线，基准 881100 中证全指，窗口 20/40 日）

| 票 | 板块 | 20 日 ρ | 40 日 ρ | 同号率 20/40 | 先验期望 |
|---|---|---|---|---|---|
| 601778 晶科科技 | 军贸→**代理 880507 国防军工** | 0.415 | 0.348 | 0.45 / 0.52 | 不联动 |
| 000799 酒鬼酒 | 880381 白酒 | 0.489 | 0.518 | 0.70 / 0.65 | 联动 |
| 001400 江顺科技 | 880685 液冷服务 | −0.080 | 0.073 | 0.70 / 0.68 | 未知（只记录） |

**结论：不可作为闸门，也不得作为排序特征消费，只能观测**。理由四条：① 正负例 ρ 只差 0.07–0.17，在 N=20/40 的噪声带内，任何阈值（如 0.45）都是 2 样本过拟合；② 负例同号率 20 日 0.45 / 40 日 0.52，不构成"不联动"；③ 001400 出现 ρ≈0（−0.08/0.07）与同号率 0.70 的**指标分裂**，说明口径本身不稳；④ 只减单一市场基准的残差仍含风格/市值暴露（601778×国防军工 的 0.42 很可能来自共同的小盘成长暴露，与板块归属无关）；且军贸概念在 TDX 无对应板块（只有 国防军工/军工信息/军民融合），只能用代理。

**检索证据校验组件（2026-10-20 落码并在当晚接线）**：`src/got/sector_evidence.py::evidence_supports_membership` —— 针对「检索 + LLM 判板块归属」的证据护栏。**只做 span 存在性校验不够**：601778 的检索结果里唯一出现「军贸」的片段讲的是**另一只票**（晶品特装 688084 军贸+内装），纯 span 校验会把它当成本票证据。规则三条：① 板块名与股票标识（名称或 6 位代码）须在**同一 segment**；② segment 内出现其他 6 位代码一律判否（同句混谈多票 ⇒ 归属不可归因）；③ 仅板块名无标的时，须同时满足「含指代词（该股/该公司/该标的）+ 结果标题含本票标识 + 段内无其他代码」，或 T3 指名式概念列表（见下节）。同文件提供来源白名单 `is_allowed_source`。

**若将来仍要做行为闸门，前置条件（预登记，缺一不可）**：风格中性化（市场 + 市值 + 成长代理，或行业中位数调整）；带标注 (stock, board) 观测 ≥100 对；阈值与窗口预登记并跑 ≥2 个 walk-forward 窗口；单列观测产物（不接 `unified_scoring`/`allocation`）。在此之前，行为指标**不得**参与任何买卖决策。

**测试**：`tests/test_verify_candidate_sectors.py` **11 passed**——新增/改写 4 例 fail-closed 与留痕（LLM 异常 raise 且不修改输入、列表外板名 raise、覆盖不全 raise、efinance 空/异常保留标签并记 `sector_verify_unverified`），原「命中保留 / 判不匹配清空 / 别名命中免 LLM」三例不变；`tests/test_family_topping_suppression.py` **57 passed**（新增 1 例：FTS 闸门关闭时 `candidate_pool` 快照仍落盘）。

### 板块身份校验换裁判：联网检索证据 + LLM 判定（2026-10-20 晚实施，efinance 退役）

**为什么换**：efinance 板块列表与问财/同花顺是两套 taxonomy，跨概念名结构性误杀——2026-10-20 三只 finalists 全被清空，其中 001400 江顺科技经检索证实确属「液冷服务器」。同日实测：军贸概念 15 只成员里 11 只与军贸无关（011/15 与同花顺该概念的真实成员不符），而液冷/数据中心 29 只大体是 AI 服务器链——**两种失败形态不同**，无法用"跨口径比对"统一裁决。

**现行流程**（`_verify_candidate_sectors`，每只带有效板块标签的 finalist）：

| 步骤 | 内容 |
|---|---|
| 1. 检索 | 2 条模板 × 最多 2 个 provider 顺位（`provider_offset` 0/1），**以护栏通过为停止条件**；单票 ≤4 次逻辑查询，`days=365`（概念归属是事实型，默认 7 天会漏证）。检索词用候选的 `sector_query_board`＝问财**实际查询**的板块名（`resolve_with_kind`/别名改写/自愈产物；2026-10-21 起，空则回落 Node 6 标签），`boards` 候选＝`[verify_board, tagged_sector, *变体, resolved]`。**死顺位收敛**：offset≥1 的引擎整轮无结果 → 类级记死顺位，本进程后续票/FXG 双跑不再等它（主顺位 0 永不停用）；`available_provider_count` 只数 `is_available` 的引擎，不保证网络可达 |
| 2. 白名单 | `is_allowed_source(url)`：`urlparse().hostname` 精确/后缀匹配 **15** 个财经域名（stockstar/eastmoney/10jqka/cfi/cninfo/hx168/dabanke/sina/qq/163/hexun/jrj + 2026-10-21 扩容 sohu/qianzhan/southmoney），防营销稿与 `evil.com/?x=stockstar.com` 伪造 |
| 3. 身份护栏 | `evidence_supports_membership`：T1 同段含票标识 / T2 指代词+标题绑定 / T3 指名式概念列表（`核心概念 白酒概念`，段长 ≤120、段内无他票代码） |
| 4. LLM 判定 | 通过护栏的原文批量一次调用，`verdict ∈ {member, not_member, uncertain}`，prompt 只给「票名/代码/板块/原文」并禁止引用未给内容 |
| 5. 落地 | `member` 保留标签；`not_member`/`uncertain`/无证据 一律清空标签（Q9-B 强制 watch），reason 分别 `llm_rejected`/`llm_uncertain`/`insufficient_evidence` |
| 6. 双跑复用 | FXG 基线影子（`_maybe_capture_fxg_baseline`）会用独立实例再跑一次 `_score_candidates`：类级 `(symbol, sector) → 决策` 缓存命中 → **零检索零 LLM**，检索/LLM 次数不翻倍（实测双跑 0 查询/0.0s vs 首跑 13 查询/24s） |

**失败语义（决策系统口径）**：响应级 LLM 失败（异常/空/无 JSON 数组/解析失败/非数组）或**全部查询零结果**（有新票待查时） → `SectorVerifyUnavailable` → Phase 2 `status="failed"` 占位 + 进程退出码 1；**单案例脏输出**（缺 index / 非法 verdict）只把该票降为 `uncertain`，不废整日；**零候选**（无任何带标签 finalist）直接返回、零查询不抛错；**有结果但全被白名单滤掉** → `insufficient_evidence`，不判为检索故障；**全缓存命中**（双跑第二轮）不触发检索故障判据。

**留痕**：`portfolio_decision.json.sector_verify_evidence`（member 票的 `quote/provider/query`）+ `sector_verify_unverified`（三 reason）+ `reasoning.md` Node 7 段「### 板块身份校验（web 证据）」；日志 `[板块验证] 检索 N 票/M 次查询（可用引擎 K）`、`非白名单来源略过 N 条（host 分布 top5）`、`第 N 顺位引擎整轮无结果，本进程内停用`、`复用本进程已校验决策 N 票`。

**实测读数（2026-10-20 探针，Bocha 单引擎实跑）**：

| 票 | 判定 | 证据 |
|---|---|---|
| 001400 江顺科技 × 液冷服务器 | **member** | 证券之星「1月7日江顺科技涨停:液冷服务器,有色金属,航天概念热股」 |
| 601778 晶科科技 × 军贸概念 | insufficient_evidence | 唯一含「军贸」片段讲晶品特装(688084)，被护栏规则 2 拒；白名单内无证据 |
| 000799 酒鬼酒 × 白酒概念 | insufficient_evidence | 见下「白名单召回损失摊开」 |
| 002076 *ST星光 × 数据中心 | insufficient_evidence | 白名单内仅证券之星「信息安全子公司」一条，与板块无关 |

**白名单召回损失摊开（31 条原始结果逐条判定，2026-10-20）**：白名单放行 9 条（stockstar×4 / news.qq×2 / 163×1 / cfi×1 / finance.stockstar×1），滤掉 22 条。滤掉的 22 条里：`q.stock.sohu.com`×11 **全是行情页**（「酒鬼酒:54.99 -0.02% 000799」，无板块词，扩了也过不了身份护栏）；`stock.qianzhan.com`×4、`s.askci.com`×2 均为财报/主营页（snippet 无板块词）；`m.southmoney.com`×2 题材页（snippet 不含板块词）；`caijing.chinadaily.com.cn`×2；`xueqiu.com`×1 为 UGC 帖——**唯一一条过了身份护栏的 off-list 结果**（snippet 中票标识与白酒板块词绑定成立），被白名单挡下属 UGC 信任取舍（雪球股主帖是散户观点，本就是白名单要挡的来源），是当天唯一真实召回损失。**其余 21 条 off-list 全部过不了身份护栏，扩白名单（sohu/qianzhan/askci/southmoney）零召回收益**；000799 判 insufficient = Bocha 对该票只返回行情/财报页 + F10 概念列表页（hx168/dabanke）未被召回（检索源覆盖问题）+ 雪球 UGC 有意排除。当天买不进，安全侧。

**测试**：`tests/test_sector_evidence.py` **11 passed**（原 6 + T3 3 + 白名单 2 组）、`tests/test_search_generic.py` **4 passed**（单 provider 切片语义）、`tests/test_verify_candidate_sectors.py` **14 passed**（证据命中/无证据/白名单滤空/LLM 三态/非法枚举/响应级失败/全空/零候选/部分空/601778 反例 + 新增：死顺位整轮停用 + FXG 双跑决策缓存复用）。

**e2e 生产链路验证（2026-10-20 21:31–21:54 `--got` 重跑，exit=0，status=normal；16:04 归档为旧 efinance 裁判末轮不计）**：

| 核对项 | 结果 |
|---|---|
| 新裁判在生产生效 | ✓ 4 只主线终选票走完 检索→白名单→护栏→LLM 仲裁（**该 4 票漏斗属 FXG 双跑的基线实例**，veto 正式跑 universe 构造前已剔 3 族、仅 1 票走缓存复用，见下两行）：601778/603619/002946 `insufficient_evidence`、300164 `llm_uncertain`，全清标签 → Q9-B watch（13 次查询 = Bocha 7 + SearXNG 空转 6，`sector_verify_unverified` reason 枚举为新值，efinance 旧路径无此产出） |
| `sector_verify_evidence`/`unverified` 落盘 | ✓ 键均在（evidence=None：本日 0 例 member，正常） |
| 三条评审加固实战生效 | ✓ 死顺位引擎整轮停用（第 2 顺位 SearXNG 无结果即类级记死）；FXG 双跑决策缓存复用（正式跑 0 查询 vs 基线 13——**缓存为节点实例共享态**：基线独立实例产出 verdict 后由类级缓存供正式跑复用）；白名单略过 19 条落 host 分布 top5（sohu×12/qianzhan×4/southmoney×2/baogaobox×1） |
| D4/D3 配套 | ✓ meta `scoring_formula_version=2026-10-20.1`；candidate_pool universe 37 行全带 `sector_type`（ambush 22/mainline 15）；union 接线确认（pd.py:4281 `"union"`）；trade_plan_log 2 条（对账+空 allocation 留痕） |
| FXG 族差归因口径（当日退化） | ⚠ 20261020 的 `fxg_pair_report` 族差对照不可用：验证清标签先于基线捕获执行（13 次检索即基线实例所跑），`fxg_baseline_scoring.json` 4 行 sector/family 全 None → 族差归因须读 universe 集合差（候选池）而非 baseline-vs-veto 评分行；验证清标签与基线捕获的先后顺序为结构性耦合，改任一处都会污染 FXG 对照。〔2026-10-21 复核〕候选池磁盘证据齐全，族差归因实际可做：`candidate_pool.json` 37 行含 `sector_name/family/sector_type`（军贸 15 行 family=军工、`is_finalist=true` 唯一 601778）+ `shadow_fts.json` fxg 子块 `excluded_sectors` 记录被剔三板块（石油开采/石油/酿酒，family=其他）——被剔族全在「其他」、主线族（军工）完整在池，此退化不改变族级结论：EXCLUDE 仅触发「其他」族 |
| 分道预算明细 | 当日无产出（allocation=[] → `lane_budget_split_shadow=[]`）：22 只埋伏全部 G-A1 flow_negative 零 finalists、主线终选 1 票 watch——结构代码就位（pd.py:5587 lane_trace 含 `lane/lane_weight/formula_version`），首个买单日可见。~~注：`funnel_stats.lane_merge_mode` 仅 `--screener` 路径落盘~~ **已修（同日）**：合并块被 `if lane_finalists:` 守卫致零 finalist 日缺键，`stock_screener.py:2356` 补 else 分支无条件落 `lane_merge_mode`，`test_zero_lane_finalists_still_records_merge_mode` 回归覆盖 |
| 当日语义 | 安全侧拒买：检索源覆盖不足（Bocha 单引擎，第二顺位未配）→ 真实成员也可能 insufficient → 全 watch/空仓。运营项不变：配 Tavily/Brave 或自建 SearXNG 后自动恢复双顺位 |

---
## ‼️ 2026-10-16 补登（G-A2 催化闸门双死点修复 — pool fast-pass + day-0 窗口口径对齐）

**性质**：修复即转正对象（同 2026-10-13 埋伏道遮蔽修复先例——两死点均为「生产路径构造性失效」，无影子判据，登记重放/回归读数供复核）。

| 项 | 内容 |
|---|---|
| 死因（当日两跑实测） | 埋伏道 `G-A1→15 G-A2→0` 连续 0 输出。①首跑东数西算：G-A2 日历名字级匹配缺口——GTC 事件 affected_sectors 无「东数西算」且 board_aliases 无桥接别名；②重跑军民融合：三要素两过（affected_sectors 直含 + conf 0.8≥0.7）但事件 dated today，旧谓词 `0 < days_to_event` 剔除当日事件，而 Node 6 `_compute_catalyst_score` day-0 分支照常入池——两套窗口口径分裂。两死点互相独立，只修一个漏另一个 |
| 修法（用户拍板：窗口 + pool 放行） | ①G-A2 增 `ambush_pool_names` 参数，候选板块 ∈ Node 6 `ambush_pool` 直接通过催化判定进位置安全带（`g_a2_pool_pass` 独立计数）；Node 6 入池已完成语义匹配（T5/relations）与 D4/G4 折信，道内不复算——D4 折信作用域 = pool 准入/排序，不下沉道内闸门（与 P5 同口径），不拿折后值对原置信 gate；②窗口谓词 `0 <` → `0 <=`；位置安全带抽 `_ga2_position_belt` 共用，pool 通道不豁免 close/high20/ma20 |
| 接线 | pool 白名单来源 `got_context.unified_context.ambush_pool`：归档路径 `_load_got_context` 本就装载；Node 7 内存装配（portfolio_decision.py got_context dict）补 `unified_context` 键对齐。生产 `--got` lane 候选恒 ⊆ pool 成员（aggregator pool-members-only + Node 7 actionable 裁剪）⇒ pool fast-pass 构造性生效、G-A2 催化谓词退化为纯位置安全带（设计接受）；`--screener` 回放路径名字匹配兜底仍是活的，勿当死代码删除 |
| 回归锚点 | `tests/test_ambush_lane_split.py` **23 passed**（新增 3 例：day-0 事件入窗 / pool fast-pass + 位置安全带不豁免 + `g_a2_pool_pass` 计数 / 无日历+空事件双分支 pool 放行）；catalyst/ambush/screener 选择集 **147 passed** |
| 端到端回放（真实归档） | 20261016 归档 context（`_load_got_context`，pool=[军民融合]）+ 15 只军民融合合成 lane 输入 → `G-A1→15 G-A2→15(pool_pass=15, no_catalyst=0) G-A3→15 G-A4→1`；对照修复前当日实测 `G-A2→0`。G-A4 后排序/TopK 边界未实测（依赖完整问财 universe，属生产实跑职责） |
| 新可观测性 | 日志埋伏道闸门行增 `pool_pass=` 字段；`funnel_stats.round_survival_counts` / `portfolio_decision.json.ambush_lane_trace` 随扁平合并自动携带 `g_a2_pool_pass`；schemas.py `ambush_lane_trace` description 同步 |

---

## ‼️ 2026-10-16 补登 #6（Q1 low 事件板块级救援直连 + 埋伏 E14 出场催化宽限影子）

**RCA 背景**：Q1 排查发现 Node 4 三级过滤把 `impact_level=low` 事件无痕蒸发——其中混有「全局 low 但板块级 high」事件（如行业性利好只影响单板块时全局显著度低）。该类事件本应进 `major_events` → NewsContext → impact_score → 日历候选池，现被一级过滤整体丢弃，埋伏/主线候选池系统性缺粮。同一 RCA 下第二发现：埋伏道 E14（催化临近转出）把「转出后 10 交易日内有合格催化」的板块直接清出场，宽限判据存在但从未生效，属潜在误杀线。两条线同日落码，**Fix 1 用户裁决直连（覆盖影子先行缺省）**，Fix 2 照影子惯例零决策改动。

**开关**（`src/got/catalyst_config.py`）：

| 开关 | 缺省 | 说明 |
|---|---|---|
| `GOT_LOW_SECTOR_RESCUE` | `1`（直连） | Q1 low 板块救援总开关；`0` 一键回滚原过滤行为（回滚语义=不升级） |
| `LOW_RESCUE_TEMPERATURE` | `0.2` | 救援复审 LLM 温度（判定任务低温） |
| `LOW_RESCUE_MAX_TOKENS` | `2000` | 救援复审输出 token 上限 |
| `GOT_AMBUSH_EXIT_GRACE_SHADOW` | `1` | 埋伏 E14 出场催化宽限影子开关（只算只落盘） |
| `AMBUSH_EXIT_GRACE_WINDOW_DAYS` | `10` | 宽限窗口（交易日） |

**两线落码**：

| 线 | 位置 | 行为 |
|---|---|---|
| Fix 1 救援 | `news_analysis.py` `_rescue_low_events`（run() Step 3.1 接线） | 复审候选=affected_sectors 非空且 sentiment≠negative 的 low 事件；LLM 三维判定（冲击共性/板块占比/增量信息，few-shot 文献接地）→ `sector_impact=high` 事件**原地升级 medium**（NewsEvent 可变，全字段保留）直连进 DB/NewsContext/impact_score（medium=1.0 权重）/日历候选池；LLM/解析失败→方法内消化（降级原过滤，**仍落 failure 影子**）；开关关→disabled 占位影子 |
| Fix 1 留痕 | `data/got_daily/{date}/shadow_low_events.json` | 全量 low 事件逐条 sector_impact（含未送审行）+ 三态分布 + failure 说明；`archive.py` 搬运/渲染/进度四件套齐 |
| Fix 2 宽限 | `aggregator.py` `_compute_ambush_exit_grace_shadow`（run() 挂载） | E14 识别=前日 ambush→今日 neutral（排 E12b+复验 E14）；合格催化=confidence≥CATALYST_CONFIDENCE_GATE 且 direction≠negative；窗口 1≤交易日至 event_date≤10；`grace_would_hold` 判定**只落盘不改判**；replay_mode 跳过；DB 失败→warning+空样本落盘（漏记不误记） |
| Fix 2 留痕 | `data/got_daily/{date}/shadow_ambush_exit_grace.json` | records[]（sector/判据/grace_would_hold/nearest_event）+ grace_candidates_count；reasoning.md 🧪 段渲染体 + 转正进度行 |

**转正/质量判据**：
- **Fix 1（直连，观测即验收）**：升级率（upgraded/review_candidates）与升级事件次日板块表现抽查并行走 10 交易日；`sector_impact_dist` 高位事件应能复现「板块级利好被全局低显著度掩盖」形态。
- **Fix 2（影子，≥10 交易日）**：`grace_candidates_count>0` 天数占比；`grace_would_hold=true` 的板块后续 5 日 `strength_trajectory` 对照转出组（若宽限组显著更优→立案转正评估；无差异/更差→影子作废，E14 判据不动）。

**回滚判据**：
- Fix 1：升级率 >50% 且抽查多数无板块级影响（LLM 过度乐观）→ `GOT_LOW_SECTOR_RESCUE=0` 回滚并复盘 prompt；
- Fix 2：影子期内 grace 组转出后板块走弱（strength_trajectory 负向占优）→ 维持 E14 现判据，影子线关闭。

**登记日 / clock_start**：均 2026-10-16（进度行 `required=10`）。

---

## ‼️ 2026-10-15 补登 #5（决策编排层 B 门五项拍板落地第一版 — thesis 四态状态机 + 全账户聚合）

**资料包**：[`plans/决策编排层_step0_裁决资料包_20261015.md`](plans/%E5%86%B3%E7%AD%96%E7%BC%96%E6%8E%92%E5%B1%82_step0_%E8%A3%81%E5%86%B3%E8%B5%84%E6%96%99%E5%8C%85_20261015.md)；**实施计划**：[`plans/GOT三问架构裁决_实施计划_20261015.md`](plans/GOT%E4%B8%89%E9%97%AE%E6%9E%B6%E6%9E%84%E8%A3%81%E5%86%B3_%E5%AE%9E%E6%96%BD%E8%AE%A1%E5%88%92_20261015.md)。五项裁决按资料包推荐倾向落第一版。

**五项裁决与落码**：

| 裁决 | 选择 | 落码 |
|---|---|---|
| **B1** positions 口径 | **b**：GOT 侧自建聚合 `_all_positions`，不动 PortfolioService（webui 共享真相源） | `portfolio_decision.py` 新增 static helper（合并 accounts positions、symbol 重复取先现者、单账户等价）；四处 `accounts[0]` 错配改写（`_estimate_current_drawdown` / `_score_holdings` no_accounts 三态保留 / `_close_thesis_snapshots` / `_close_ambush_snapshots` / `_allocate`）；`total_*` 全账户读法本就正确不动；空账户 10 万虚拟兜底保留 |
| **B2** 埋伏入场锚 | **c**：双表并存，thesis 唯一入场锚源，埋伏行补 catalyst_event_id 关联 | 实测 `_write_thesis_snapshots` buy_allocations 不过滤 is_ambush_entry ⇒ 埋伏买入已被覆盖，收窄为 `catalyst_event_id` 关联回写（ambush_pool_map 按 sector_name 命中，UPDATE/INSERT 两分支；非埋伏票置空防同日改道残留）；`written` dict 同步补 `catalyst_event_id` 字段（归档排查用，非埋伏票为 None） |
| **B3** 论点生命周期 | **四态状态机** intact/drift_watch/thesis_broken/closed，`is_open` 降级派生（closed↔False）保留兼容 | 列 `lifecycle_state` + `catalyst_event_id`（storage.py）；迁移 `_migrate_thesis_lifecycle_state`（模式同 catalyst_realizations，接线 init 链）；转换点：买入/重写=intact、`_apply_drift_watch`=drift_watch、`_mark_thesis_broken`（新 helper，`_score_holdings` 三处漂移 sell 且无宽限分支调用，判定函数 `_drift_action_with_grace` 保持纯净零写库）、清仓=closed。**评审轮补丁**：同日重写分支重置 intact 时同步清 `drift_watch_since/drift_family`（不变式 intact⟹since NULL——回填谓词随每次 init 重跑，若保留 since 下次重启会把行翻回 drift_watch）；**宽限期内回归→intact 转换延后**：drift_watch 行宽限期内不回写 intact、保持观察态，实施挂 B4/持仓大改轮 |
| **B4** 退出分层 | **C**：按角色分道，本版只登记判据不接线 | 见下方登记；实施挂 WI-P-7 解冻 |
| **B5** 家族中继 | **b**：`_family_relay_match` 现状核对结案（Fix 1 已实现：一级半层中继、0.85 打折、weight 1.2、同族单名额） | 零改动 |

**迁移踩坑登记（回填谓词）**：SQLite `ALTER TABLE ADD COLUMN ... DEFAULT 'intact'` 给存量行**直接回填 DEFAULT 值非 NULL** ⇒ `WHERE lifecycle_state IS NULL` 永不命中。修正为**证据冲突驱动**谓词：`WHERE lifecycle_state='intact' AND (is_open=0 OR drift_watch_since IS NOT NULL)` → CASE 置 closed/drift_watch；回填后不再冲突 ⇒ 天然幂等（重跑零写入）。生产库 `data/stock_analysis.db` 已迁移 + 存量行 688598（open + drift_watch_since=2026-10-15）已修正回填 drift_watch。

**验证**：新增 `tests/test_thesis_lifecycle_state.py` **15 用例**（迁移回填三行证据 + 幂等重跑 + 新库建列 + 四转换路径含 `_apply_drift_watch`/`_close_thesis_snapshots` 真实方法直调（部分减仓保持/清仓关闭）+ intact 重写清 since 不变式 + thesis_broken 不触 closed 行 + `_all_positions` 三态）；回归 `test_phase2_audit_fixes.py`(20) + `test_portfolio_decision_lot_rejection.py`(8) + 全矩阵 5 文件(113) 全绿，合计 **156 passed**。

**B4-C 退出分层判据登记（本版不接线，实施挂 WI-P-7 解冻）**：

| 持仓角色 | 主锚（第一优先） | 兜底 |
|---|---|---|
| 主线仓 | 硬止损 + 生命期（生命期耗尽/板块 exhausted） | FTS 全角色兜底 |
| 埋伏仓 | P4 催化退出 + HWM 移动止盈 | FTS 全角色兜底 |
| 全角色 | FTS（恐慌清仓）不受角色分道限制 | — |

> T-1 近似 HWM 退役依赖 B2 已定稿（埋伏行已有 catalyst_event_id 关联 + thesis 唯一锚源）；真 HWM 引入归 B4 实施轮。**约束**：WI-P-7 冻结仍约束持仓侧四条 shadow（sell_source/exposure_enforce/signal_size/mature_reduce）开关与判据不得动；B3 状态机仅标注状态不改任何卖出决策路径。

---
## ‼️ 2026-10-15 补登 #4（Phase 6-1 盘中 0→1 确认闸门落码 — 三开关 + I1-I6 判据 + 一票否决线）

**实施稿**：[`plans/Phase6-1_盘中闸门影子期实施稿_20261015.md`](plans/Phase6-1_%E7%9B%98%E4%B8%AD%E9%97%B8%E9%97%A8%E5%BD%B1%E5%AD%90%E6%9C%9F%E5%AE%9E%E6%96%BD%E7%A8%BF_20261015.md)（§4 十项已全落码，落码清单见 backlog 2026-10-15 Phase 6-1 落码轮修订行）。

**开关**（`src/got/catalyst_config.py`，`.env.example` 同步登记）：

| 开关 | 缺省 | 说明 |
|---|---|---|
| `INTRADAY_GATE_ENABLED` | `false` | 总开关；`true` 才跑 `--got-intraday` |
| `INTRADAY_GATE_SHADOW` | `true` | **影子期一票否决线：Phase 6-4 转正前禁止置 false**（配置注记为第一道闸，`run_session` 写侧防御性拦载为第二道） |
| `INTRADAY_GATE_TOPN` | `8` | 每时点轮询候选数上限（`portfolio_decision.unified_scoring` 按 composite_score 降序截断） |
| `INTRADAY_GAP_THRESHOLD_PCT` | `3.0` | θ_gap 竞价高开幅（影子期校准） |
| `INTRADAY_PULLBACK_THRESHOLD_PCT` | `2.0` | θ_pull 冲高回落幅度 |
| `INTRADAY_IMBALANCE_THRESHOLD` | `0.15` | 五档委比 |
| `INTRADAY_SEAL_DECAY_THRESHOLD` | `0.5` | 封单衰减（观测用） |

**产物**：`data/got_daily/YYYYMMDD/intraday/{0926,1125,1445}/verdicts.json` + 当日 `intraday/summary.json`；`archive_daily_run` 已加 `intraday/` 搬运分支（原子替换不抹当日盘中产物）；读取 `archive.load_intraday_shadow_record`。

**影子期判据（转正 = Phase 6-4，双达标 + 负责人批准）**：

- **I1 证据覆盖率**：`verdict.evidence` 非空占比。目标 ≥80%；竞价明细缺失日（F5 段补测前）分层标注 `missing_auction_detail=true` 单独统计，不混入总池。
- **I2 confirm 占比**：confirm/(confirm+reject+wait)。目标区间 10%–50%（过低=判据过严无信号，过高=闸门失效）。
- **I3 判据分辨率**：confirm 股 T+1 相对收益 vs wait/reject 股应显著为正（影子期逐日积累，样本 ≥10 交易日后回看）。
- **I4 reject 命中率**：reject 股 T+1 收益中位数 ≤ 0（尾盘转弱/诱多判定有杀伤力）。
- **I5 数据新鲜度**：三时点必须盘中实时跑（0926/09:26、1125/11:25、1445/14:45 ±5min）；收盘补跑样本作废（schtasks 注册后由 summary.sessions_run 核对时点齐全）。
- **I6 样本纪律**：影子期样本**仅计跑满三时点的交易日**（summary.sessions_run 长度=3）；单时点缺勤日不计入分母。
- **零决策纪律**：影子期产物不进 Node 1-7、不进 screener 漏斗、不进通知；任何消费路径出现即违规（grep 无消费点为静态证据）。

**排期步骤（待负责人启动影子期）**：① schtasks 三时点注册（0926/1125/1445 各 `python main.py --got-intraday --intraday-time <t>`，`INTRADAY_GATE_ENABLED=true` 入 .env）；② F5 竞价段 9:15-9:25 `transactions()` 交易日补测（补齐 I1 竞价分层）。

---
## ‼️ 2026-10-15 补登 #3（§8 #1 meta 运行记次实施 + §7 #12 家族兜底 WI 代码侧 + Phase 6-0 探测）

三件并行实施，两件零决策、一件含缺省关的 V2 开关：

1. **§8 #1 meta 记次（已实施，观测性元数据无开关）**：`src/got/archive.py` 新增 `_read_meta_safe`（meta.json 损坏/缺失/NUL 字节消毒 try/except 当空）+ `_build_allocation_summary`（allocation ≤10 条 `{symbol,name,action,target_value,weight}`）+ `_build_meta(archive_dir)` 读旧 meta merge 后 append `runs` 数组（`run_seq` 当日 1-based 递增 / `run_at` ISO / `phase` phase1→full 双阶段 enrichment / `position_ratio`），tmp+rename 原子写；`archive_phase2` 复用同 entry 补 allocation/position_ratio（phase1→full）。现有字段（funnel_stats/net_preflight/git_commit 等）全保留、下游零感知。同日多次运行归档互相覆盖问题就此有观测解（9/23 案例追溯不可考→此后 meta.json 自带当日全史）。单测 `tests/test_archive_run_counter.py` 10 例。
2. **§7 #12 WI 代码侧（三小项，V2 缺省关）**：`src/got/family_embed.py` 两-term 打分 `cos×0.6+keyword_overlap×0.4`（`GOT_FAMILY_SCORING_V2` 缺省 0=旧逻辑逐字节不变）+ 语义兜底 bypass（覆盖率≥0.8 直接归家族，trace `fallback_reason='keyword_exact'`）+ 网格脚本 `scripts/calibrate_family_threshold.py`（761 个真实「其他」板块 S×M 网格跑通）。**S=0.48/M=0.03 为伪 cosine（hash 模拟）占位数非标定结论**——归档无历史 cosine 分数，真实标定须 shadow（V2=1+fallback=shadow）≥5 交易日后复跑。盲区候选集对齐报告 [`plans/家族兜底盲区候选集对齐_20261015.md`](plans/%E5%AE%B6%E6%97%8F%E5%85%9C%E5%BA%95%E7%9B%B2%E5%8C%BA%E5%80%99%E9%80%89%E9%9B%86%E5%AF%B9%E9%BD%90_20261015.md)（差集={华为生态,前沿科技}，推荐 FALLBACK 收缩到 PROMPT 12 个，待拍板）。3 个新 env 键属超范围新增待负责人确认。
3. **Phase 6-0 四接口只读探测（已实施，零生产改动）**：`scripts/probe_intraday_capabilities.py` 真实跑通——F3 五档买量**可用**（`quotes()` 46 列含 bid_vol1~5/ask_vol1~5，OPEN-I2 解决）；F4 分时/分钟K **可用**（`minute()` 240 行 + `bars(frequency=8)` 1 分钟 K，OPEN-I3 解决）；F5 集合竞价**部分可用**（`transactions()` 历史逐笔可得，9:15-9:25 段待交易日补测）；W1 调度**可行**（schtasks 零依赖或 schedule 库）。结论 [`plans/Phase6-0_盘中数据地基探测_20261015.md`](plans/Phase6-0_%E7%9B%98%E4%B8%AD%E6%95%B0%E6%8D%AE%E5%9C%B0%E5%9F%BA%E6%8E%A2%E6%B5%8B_20261015.md)；开 Phase 6-1 影子期归负责人。

---
## ‼️ 2026-10-15 补登 #2（#14 ④ regime jump penalty 模型 — 两级开关 + λ 标定 + 影子判据）

**拍板背景**（backlog §7 #14，2026-10-15）：现行 regime 确认状态机在 0910 起的 bull↔bear 震荡中死锁于 `transition`（两日确认 + dwell 要求），直到 1015 才确认 bear——滞后 35 个交易日。裁决采纳 jump penalty 模型 **s_t = argmin_s[|raw−θ_s| + λ·1{s≠s_{t-1}}]**：切换成本 λ 一次性收取，结构性删除两日确认层；θ 固定 ±0.3（与 `_compute_market_regime` 三段阈值一致）；tie 严格小于才切（迟滞语义）。

**两级开关**（`src/got/catalyst_config.py`，缺省与 `.env.example` 同步）：

| 开关 | 缺省 | 语义 |
|---|---|---|
| `GOT_REGIME_JUMP_SHADOW` | 1 | 影子层：jump 模型并行重放 raw 序列，落 `data/got_daily/YYYYMMDD/shadow_regime_jump.json`，零决策改动；replay_mode 跳过 |
| `GOT_REGIME_JUMP_PENALTY` | 0 | 生产切换：=1 时 jump 模型整体替换现行确认状态机（`_apply_regime_jump_model`）；**影子判据达标 + 负责人拍板前禁置 1** |
| `REGIME_JUMP_LAMBDA` | 0.25 | 切换成本 λ（env 可覆盖）；θ 不设开关（固定 ±0.3，EMA 标定留待影子读数后复核） |

**λ 标定回放**（`scripts/calibrate_regime_jump_penalty.py`，45 日全窗 20260805–20261015，标定窗 0910–1009 共 15 日，σ daily-diff=0.4929）：

| λ | 窗内 switch | 窗内方向一致（vs 无记忆阈值基线） | 全窗 switch | 备注 |
|---|---|---|---|---|
| 无记忆（λ=0 极限） | 10 | 15/15 | 31 | 逐日阈值基线，agree 判据对照态 |
| 0.15 | 7 | 8/15 | 24 | |
| **0.25（缺省）** | 7 | 8/15 | 23 | 0.5σ；窗内比无记忆少 3 次切换，保留更多信号 |
| 0.3 | 6 | 7/15 | 17 | |
| 0.5 | 4 | 7/15 | 9 | 更收敛但抹掉更多真实转向 |
| 1.0 / 2.0 | 0 | 4/15 | 0 | ⚠ λ 过大钉死锚点态 |

锚点（非真标定）：现行状态机 1015 才切/1 次；λ=0.5 → 0928 切/2 次；λ=2.0 → 0 次永不切换。λ 终选已拍（2026-10-15 负责人）：**维持 0.25 纪律缺省**（0.5σ；λ≥1.0 钉死锚点态作废；影子四判据 clock_start=2026-10-16 触发再复核）。

**影子判据（5 交易日，clock_start=2026-10-16）**——全部满足才有资格提请转正：

1. **方向一致率 ≥80%**：`agree`（jump_state == confirmed_state）天数占比 ≥4/5；
2. **方向一致不低于无记忆基线**：逐日 jump_state 与当日阈值态（raw>0.3→bull / raw<−0.3→bear / 其余 transition）一致天数 ≥ 无记忆基线同窗读数；
3. **切换次数低于现行状态机**：`switches_20d_jump` ≤ `switches_20d_confirmed`（同窗对照，滚动窗口逐日记录）；
4. **零决策红线**：任何日 `unified.json` 的 confirmed regime/dwell_days/transition_count 与 `GOT_REGIME_JUMP_SHADOW=0` 重跑完全一致（影子层只落盘不改决策）。

**回滚**：`GOT_REGIME_JUMP_SHADOW=0`（关影子）；生产态回滚 `GOT_REGIME_JUMP_PENALTY=0`（回到确认状态机）。影子期不动历史归档；raw 序列自 2026-10-15 起新归档携带 `raw_regime_score`（4 位小数），旧归档 raw 缺失日以 regime 输出 diff 参与事件计数（迁移种子语义，随 20 日窗口老化衰减）。

**实现落点**：`aggregator.py` `_jump_argmin`/`_apply_regime_jump_model`/`_compute_regime_jump_shadow`（:858 起）+ call site :238-254 四分支（replay 跳过 / DISABLE_REGIME_STATE_MACHINE 跳过 / PENALTY=1 生产切换 / else 原状态机 + 影子旁路）；`persistence.py` `load_regime_jump_history`（:541）；`MarketRegime.raw_regime_score`（schemas.py:719，不参与决策、不进 R-B8 幂等组）；archive.py `SHADOW_REGIME_JUMP_ARTIFACT` + `_render_shadow_regime_jump` + progress rule（needs=["regime_jump"], required=5, clock_start=2026-10-16）+ archive_daily_run ② 搬运分支（防原子替换删除当日影子产物，同 agent_glue.json 语义）。测试：`tests/test_node6_regime_state.py::TestRegimeJumpModel` 7 例（argmin/tie 严格小于/λ 钉死/立即切换/事件重放/legacy diff/冷启动/退役字段），conda + .venv 双 31 passed。

**首版冒烟事故注记**：首版冒烟直接写真实 `data/got_daily/20261015/shadow_regime_jump.json`（bg_44，跑完 4.3s 自删，`ls` 复核归档干净、unified.json 完好 bear/raw=-0.74）——冒烟纪律已明确：**影子层冒烟一律 tmp chdir 或纯函数级，禁止指向真实日期**（aggregator.py:109 点名事故类）。

---

## ‼️ 2026-10-15 晚间补登（D3 门控骨架 + D4 trace 增强 + candidate_pool 测试污染事故）

### D3 分道预算门控骨架（零决策，fail-fast）

> **⚠️ 已被取代（2026-10-20）**：门控块与 `raise NotImplementedError` 已删除，D3 分道预算转正生效（`GOT_LANE_BUDGET_ENABLED` 缺省 `1`），见本文件「2026-10-20 转正补登」节。下文保留为历史判据记录。

`GOT_LANE_BUDGET_ENABLED`（catalyst_config.py:387，缺省 `0`）→ `_allocate` 预算行前插入门控块（portfolio_decision.py:5205-5209）：`=1` 时 `raise NotImplementedError`——**接线被 D1 标定判据阻塞，按 O3 纪律不允许静默等权旁路**。转正判据：`scripts/backtest_lane_budget.py` 前向超额分箱出显著区分度（lane 主胜出且差距 ≥2pp）+ Layer 2 积样 ≥20 交易日/≥30 票 + `SCORING_FORMULA_VERSION` bump + 基线 diff 归零，四条同时满足才允许把门控块替换为真实预算接线。

### D4 trace 增强（`GOT_LANE_BUDGET_SHADOW=1` 时 lane_budget_split_shadow 道条目内新增两键）

| 字段 | 语义 | 供数 |
|---|---|---|
| `lane_budget_applied` | 该道当日 D3 是否生效（bool；2026-10-20 转正后缺省 true，`GOT_LANE_BUDGET_ENABLED=0` 时 false） | `_allocate` call-time 直读 `GOT_LANE_BUDGET_ENABLED` |
| `would_be_split` | 若 D3 生效，该道假想预算比 = `LANE_BUDGET_PARAMS[lane]×n / Σ`（`buy_candidates[:3]`，screener `sector_type` 词表经 `_stype_to_role` 映射到 sector_role 词表，miss→unknown；纯计算，不重排不触碰 allocation） | `:5310-5335` |
| `catalyst_supply_by_lane` | 该道板块当日 confidence≥0.7 催化剂事件数（与 P5 同门槛 `CATALYST_CONFIDENCE_GATE`） | `_score_candidates` 末尾 `_last_catalyst_supply`（catalyst_calendar.events → normalize_sector_name 归一化键，`:4090-4118`）；查询侧同归一化（`:5339-5349`） |

**词表锚点（D4-a 映射依据）**：`buy_candidates.sector_type` = screener 词表 `active_mainline/ambush`（stock_screener.py:89/1070，埋伏道 finalist 硬编码 `ambush` :1564）；`_lane_trace` 键 = `SectorIntel.sector_role` 词表 `mainline/ambush/cooling/unknown`（schemas.py:783 + ROLE_MULTIPLIER :45）。映射 `{"active_mainline": "mainline", "ambush": "ambush"}`，miss→`unknown`。**冒烟实证**（2026-10-15）：2 主线+1 埋伏 → `would_be_split={mainline: 0.7843, ambush: 0.2157}`（和=1），`lane_budget_applied=false`，`catalyst_supply_by_lane={半导体: 2}` 归一化查表命中，无幽灵 `active_mainline` 条目。

### 🧨 测试污染事故与修复（F-1 candidate_pool.json）

| 项 | 内容 |
|---|---|
| 根因 | `test_bear_entry_gate.py` / `test_meg_momentum_exhaustion_gate.py` / `test_winrate_optimization.py` / `test_family_topping_suppression.py`（FtsGate harness）的 `_run()` 不 chdir，stub universe 为 `[{"code": stock.code}]` 残缺形态；`GOT_FAMILY_TOPPING_GATE_ENABLED=1`（.env:909）时 `_score_candidates` F-1 分支（:4124-4129）把 stub 写进 **repo cwd** `data/got_daily/<今日>/candidate_pool.json`，覆盖生产归档 |
| 实锤 | 1015 mtime 18:58→22:38（pytest 17 passed 触发）；1013/1014 同机制；1012 全空 symbol 行是 `0175423`（10-14 14:21 F-1 修复）之前的旧 getattr bug，另一成因。污染行形如 `{"symbol": "300001"/"600513", "name": "", ...}`（测试 fixture 默认 code） |
| 修复 | 四个 harness 实例级打桩 `node._save_candidate_pool_snapshot = lambda *a, **k: None`（与 test_family_topping_suppression FxgPhase2 harness :594 / orchestrator.py:79 先例同款；不 chdir——chdir 会让 `_save_shadow_records` 等其它写盘失真）。test_ambush_pool_consumption 无需打桩（fake_extract 返回 `[]` → universe 空提前 return，快照分支不可达）；test_fxg_veto_baseline_pair 原有 stub+chdir 双保险 |
| 处置 | `20261013/14/15` 三个污染 candidate_pool.json 已删除（md5 留痕 `.review_tmp/*.bak`，内容本身即污染 stub 非生产数据）。**生产快照已不可恢复**（pytest 覆写发生在备份前的多个时点） |
| 防回归 | 全矩阵 6 文件 134 passed × 2 轮，mtime 恒定不刷新（23:00:50 桩前写入后冻结）；语义断言由 test_family_topping_suppression `test_candidate_pool_snapshot`（chdir 隔离 + 生产契约形态输入）继续承保 |
| **D1 数据地基结论** | candidate_pool 6 日快照**全部无效**（1012 旧 bug 全空行；1013-1015 测试 stub 单行）→ D1 回测样本源归零。修好后新样本只能等新交易日自然积累；D1 报告（`.review_tmp/lane_budget_d1_report_20261015.md`，n=2）作废，待样本重新积累后按 C4 判据重跑 |

---

四条新影子线 + 三组支撑改动（C1/C2/C3 基础设施 + D2 参数登记），全部缺省开、零决策改动。判据如下：

| 线 | 开关 | 产物字段 | 转正门槛（≥10 交易日观测） | 红线 |
|---|---|---|---|---|
| **C4** 分道预算 trace | `GOT_LANE_BUDGET_SHADOW` | `PortfolioDecision.lane_budget_split_shadow`（lane/allocated_value/allocated_weight/role_budget_m/n_positions/formula_version；**D4 增强 2026-10-15 晚**：+`lane_budget_applied`/`would_be_split`/`catalyst_supply_by_lane`，见上「晚间补登」节） | 积累 10 日后对照 `scripts/backtest_lane_budget.py` 的 regime×lane 前向超额分箱：**分箱区分度不显著（无 lane 主胜出或差距 <2pp）→ f() 维持现状等权 ROLE_MULTIPLIER，LANE_BUDGET_PARAMS 不引入新数值**（O3 纪律）；**D1 样本源 2026-10-15 晚归零**（测试污染事故，见上节），样本自修复后重新积累 | trace 数值与实际 allocation 分道统计逐日可复算一致；≠0 日 `_allocate` 决策输出与 =0 日完全一致（只读性质） |
| **E1** T3 verdict 路由 | `GOT_T3_ROUTING_SHADOW` | `PortfolioDecision.t3_routing_shadow`（contradiction_id/rule_verdict/t3_verdict/routed_signal=None/action_taken=shadow_none） | 10 日内 `rule_verdict` 与 `t3_verdict` 不一致条目的占比稳定 ≤30%（T3 仲裁是修正不是翻案）；若 >30% 说明规则层与 LLM 层系统性分歧，先查 prompt/词表再谈转正 | 零数值调制：任何日 `unified_scoring`/`allocation` 输出与 =0 日一致 |
| **E2** cross_node 路由 | `GOT_XNODE_ROUTING_SHADOW` | `PortfolioDecision.cross_node_signals_shadow`（signal_type/routing_mode/blocked_by/action_taken） | `bull_confirmation` 条目在 **#14 regime 钉死拍板前恒 blocked_by=regime_pin_#14_pending，不得解除**；其余三模式积累 10 日后人工抽 10 条判 routing_mode 语义是否成立（≥80% 一致） | 同上零决策改动 |
| **F1** 埋伏道校验 | `GOT_AMBUSH_VALIDATION_SHADOW` | `UnifiedContext.ambush_lane_validation`（rule=stagnation_ambush/catalyst_expiry_watch/family_divergence + sector/detail/proposal/severity） | 10 日内 `catalyst_expiry_watch`（阈值 days_in_reservoir≥7，0.7×RESERVOIR_EXPIRY_DAYS=10）触发条目与实际除名/升格事件的命中率 ≥60% 才有资格进入提案转正讨论；`stagnation_ambush`/`family_divergence` 先积累分布 | **埋伏池本体 set 相等**：任一日 `ambush_pool`/`ambush_reservoir` 内容与开关关闭日完全一致（校验只产提案，不删不加不移） |

支撑改动（无判据，仅记录）：C1 `trade_plans` 表 + `TradePlanRecord`（storage.py，UniqueConstraint(plan_date,symbol,action) UPSERT）；C2 `src/got/decision_plan.py` assemble/reconcile（W3-pending：reconcile 单账户口径；卖出计划对账语义=票消失才 executed，票仍在=cancelled，缺昨日基线不判 partial）；C3 `--got` 通知 hook（`GOT_NOTIFY_ENABLED` 缺省 1，正文=终端块精简版 ≤25 行，try/except 不阻断）；D2 `LANE_BUDGET_PARAMS` 登记 + `SCORING_FORMULA_VERSION` bump '2026-10-11.1'→'2026-10-15.1'；E3 3b 先验权重接线（见上节 3b 判据注记）。

**读数起点 = 2026-10-15**（14:03 修复 F1 后的重跑为首个有效观测日归档；13:36 首跑崩于 F1 bug 未归档）。F1 规则3 已修 `ThemeFamily.family_name` 字段名 bug（agent 产物 `f.name` 会 AttributeError 静默失效，已验证三规则正例路径全通）；首跑另抓规则1 `SectorDetail.name` 字段名 bug（同日修复，见下读数表）。

### 2026-10-15 首个观测日读数（14:03 实跑，六项清单 + 四线 presence）

| 项 | 读数 |
|---|---|
| **首跑即抓 bug** | 13:36 首跑崩于 Node 6 末尾：F1 规则1 对 `cooling_down`/`industry_top`（`SectorDetail`，字段 `name`）误用 `sector_name` → AttributeError，**归档全未落**。已修（aggregator.py:2629-2630）+ 合成 smoke，14:03 重跑成功。首日 lessons：影子代码崩在生产主链路上（虽然函数本身在门控内，但接线点 :461 无 try/except），与 C2b/C3 的双层防不同——**F1 后续若再改，接线处同样不加防（读数保真优先），靠门禁测试拦截** |
| 退日历 | catalyst events=29，`akshare_*`=0 ✅ |
| 4b | `market_theme_shadow` present，status=ok（mootdx_quick_change 覆盖~1.0，top1 出版业）✅ |
| 3b | `conflict_summary_shadow` present（bullish_agents: family_rank/catalyst_gate）✅ |
| **C4** | `lane_budget_split_shadow` = **[]（空数组，非 None）**——今日 0 交易（空仓观望，allocation=0），buy 道无分道可记。presence ✅，有效样本待首个非空仓日 |
| **E1** | `t3_routing_shadow` = 4 条 ✅ 首个有料样本；`t3_verdict` 全 None（dwell_days=1<2 新切换，T3 仲裁 skip——与 aggregator.py:4799 日志一致）；rule_verdict 有长文本裁决。**注意**：`contradiction_id` 格式 `pair:timeframe`、`rule_verdict` 是中文裁决文本而非枚举——转正统计口径（不一致率）需先定义 verdict 归一化规则，否则无法对齐比对 |
| **E2** | `cross_node_signals_shadow` = []（今日 `cross_node_signals` 本身空，路由无输入）presence ✅ |
| **F1** | `ambush_lane_validation` = []（今日正式池=0、蓄水池 15 条全部 days<5，三规则无触发）presence ✅ |
| funnel_stats | r0_in=49 → r4=7，zero_pick_rate=0.0 ✅（非 D-3 归零形态） |
| MEG/FTS/FXG | shadow_meg evaluated=5 flagged=0；shadow_fts 医药 aggravated days=2；fxg_baseline mode=baseline_off ✅ |
| GATE-1 / C1/C2 | `trade_plans` 表已建、今日 0 行（空仓无买卖计划，正常）；通知 hook：`GOT_NOTIFY_ENABLED=1` 走到 send但因未配置渠道跳过（log :249 证实，hook 本身工作正常） |
| regime | transition → **bear**（dwell=1, transition_events=5），risk_budget=0.39 |

**首日结论**：四线全部 present（两条空数组属输入为零的正常形态，非字段缺失）；E1 产出首个非空样本但暴露 verdict 归一化口径缺口（登记待办：定义 rule_verdict/t3_verdict 对齐规则后再统计不一致率）。读数起点自此日计。

**E1 归一化口径已拍板（2026-10-15 负责人选 A 案：关键词映射 + 抽样复核，零生产改动）**：起草 rule_verdict（中文裁决文本）→ `{avoid/reduce/hold/buy}` 四档关键词映射表（处理 `contradiction_id` 格式 `pair:timeframe`、映射只对齐统计口径、不改任何字段）；映射表交负责人抽 10 条人工复核后启用不一致率统计。首日 4 样本 `t3_verdict` 全 None（dwell<2 T3 skip）不构成归一化样本，映射表启用后待首个 T3 有值日积累。**✅ 复核通过（2026-10-15 当日）**：映射表草案 v1（[`E1_rule_verdict映射表草案_20261015.md`](plans/E1_rule_verdict%E6%98%A0%E5%B0%84%E8%A1%A8%E8%8D%89%E6%A1%88_20261015.md)）负责人确认映射口径认可，统计启用；读数起点=首个 `t3_verdict` 有值日。**两个口径同日拍板**：① 四档语义 = avoid 拦新买/reduce 既有仓位去风险/hold 口径声明/buy 正向倾向；② avoid→keep 11 条按「非不一致」单列、不进不一致率分母（T3 keep 维持矛盾严重度而非否定勿追涨约束；该模板若被 T3 downgrade 仍属真不一致）。另注：早期归档 20260805–20260814 `unified.json` 内嵌 `\u0000` 转义（TDX 板块名脏字符），凡抽取归档文本二次加工须 `replace('\x00','')` 消毒。

**门禁口径备注**：定向回归最终计数 **83 passed**（storage 19 + aggregator smoke/ambush 49 + PD/GATE-1 34，一次 pytest 调用；bg_42 与 F1 修复后复跑两次一致）。早前「102」为三次独立调用的子集口径相加误计，以 83 为准。归档渲染标签当日为「⚖️ T3 路由影子」，代码同日统一为「T3 verdict 路由影子」（archive.py :1146-1153，cosmetic，JSON 读数不受影响；新旧标签检索口径以此注记为准）。

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
| 新可观测性 | 下一实跑起：日志 `埋伏道闸门: in=… G-A1→… G-A2→… G-A3→… G-A4→… (soft= flow_neg= dde_deg= no_cat= low_conf= pos_unsafe= pool_pass= knife= err=)`（合并块后单时点扁平汇入 `funnel_trace`，含合并块写入的 `cross_lane_collision`/`lane_overflow_n`）；`meta.json.funnel_stats.round_survival_counts` 直接含 `lane_in/g_a1_pass/…/cross_lane_collision/lane_overflow_n` 扁平键（嵌套 `ambush_lane_trace` 键不再出现；trace 全量仍独立落 `portfolio_decision.json.ambush_lane_trace`）。2026-10-16 增 `g_a2_pool_pass` 键（见「2026-10-16 补登」节） |
| 待裁决（登记不修） | ① 同日事件窗口语义：`0 < d <= 14` 使运行日当天事件（如 OpenAI 发布会→影视音像）不可作催化剂——是否改 `0 <= d`。〔**已裁决并实施（2026-10-16，见「2026-10-16 补登」节）**：改 `0 <= d`〕〔② candidate_pool.json 写入器键错位已不属于本节：2026-10-14 修复拍板（dict 直修·窄契约），迁 backlog §7 #13〕 |

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
| **3b 风险覆写计划** | `GOT_DISAGREEMENT_SHADOW=1`（缺省开；`GOT_DISAGREEMENT_ENABLED=0` 时 plan 仅观测不进 T3） | `portfolio_decision.json` 顶层 `risk_override_shadow` | `null`=3b 开关未跑或当日无矛盾；非 null=有 RiskOverridePlan 观测。**本线不冻结，门槛已定（见下）** | 6/6 日 present（各 5 条）。**〔10-12 复核〕** present 白名单=0928/0929/0930/1008/1009/1012 共 **6 个有效样本日**（glue 均 `status=ok`）；`conflict_type` **仅 2 种取值**（`partial_bullish_with_degraded_inputs`×4 / `mixed_with_degraded_inputs`×2），11 类冲突分类只覆盖到 2 类。**〔10-16 回测全档复核更正：6→9〕** 3.1 回测 `scripts/backtest_risk_override_replay.py` 全档扫得 present 白名单实为 9 日（0928/0929/0930/1008/1009/1012/1013/1014/1015，glue 均 `status=ok`），10-12 复核时点之后新增 1013/1014/1015 三日未回填；conflict_type 仍 2 类，分布更新为 Node7 `partial_bullish_with_degraded_inputs`×6 / `mixed_with_degraded_inputs`×3（Node6 conflict_summary_shadow 侧 ×8/×1） |
| **敞口强制（Step 1.5 变体）** | 归批 5 敞口执行观测 | `portfolio_decision.json` 顶层 `exposure_enforce_shadow` | 同上惯例 | present 0930/1008/1009（各 5 条）；0928/0929 null。**〔10-12 裁决：冻结；去留归 WI-P-7〕** |
| **信号仓位缩放观测** | 归批 5 | `portfolio_decision.json` 顶层 `signal_size_shadow` | 同上惯例 | present 仅 0930（6 条），其余日 `null`（空仓/无买入分配 ⇒ 无差值可算）。**〔10-12 裁决：冻结；去留归 WI-P-7〕** |
| **成熟期减仓观测（T-2）** | 归批 5 | `portfolio_decision.json` 顶层 `mature_reduce_shadow` | **三态已定稿（2026-10-12 A1 #3 / WI-P-6）**：字段由裸 list 改为 dict `{hits, holdings_seen, skipped_reason}`——`null`=本次未调 `_score_holdings`；`skipped_reason=no_accounts/no_holdings`=跑了但无可观测对象（**hits 必空，不得读作“没有到期持仓”**）；`holdings_seen>0 且 hits 空`=真读数。⚠　10-12 前为裸 list，旧归档（1008/1009 的 `[]`）与新 dict **不得直接对拍**。返工钩子见 [`plans/持仓模块重构_待分析与待实施清单_20261010.md`](plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md) §三 WI-P-6 与 §六 | 1008/1009 旧形状 `[]`（当时无法区分无持仓与无命中）；0928–0930 null；10-12 起为新 dict。**〔10-12 裁决：冻结；三态定稿不等于将转正，去留归 WI-P-7〕** |

> ⚠️ **读数起算更正（2026-10-11 复核修正，推翻原注记）**：批 5 曾有 Critical #1（`aggregator.run()` 裸 `os.getenv` 缺模块级 `import os`，`912cf68` 09-29 修复）。原注记「0928/0929 归档的 3b/4b 影子字段不可作标定输入（run() 必崩，产物为空/不存在）」**与盘上证据不符**：0928 归档（21:47:59，meta `git_commit=ae432ab`、`bar_complete=true`、时长 1202.6s）已含非空 `conflict_summary_shadow` / `market_theme_shadow`，`reasoning.md` 亦渲染 🧭 段——若按提交序（ae432ab 无 `import os`）执行，run() 在 3b 影子分支入口（缺省开）必崩、产不出该档；唯一自洽解释是修复当晚已落**工作树**（先跑后提交，与批 4.1 残片 `candidate_pool.json` 23:59:58 同款），0929 归档 meta 已记 `git_commit=912cf68`。**断窗（合入 `3d71945` 09-28 18:23 → 修复落树）内无归档落入 ⇒ 现存 0928/0929 的 3b/4b 影子读数均为真跑产物、可作标定输入**；backlog 轮批 5 注记同日更正。

**转正门槛（2026-10-12 负责人裁决，取代本行原写的“未定——五线均无登记”）——拆成两侧**：

**① 3b `risk_override_shadow`：已定门槛，影子期到期即可测**（本线不属持仓侧，继续爬坡）：
- **有效样本日**：`risk_override_shadow` 非 null 且当日 `agent_glue.status=ok`（降级/回放日不计）；**需累计 ≥10 个**（现 6 个，差 4）。**〔2026-10-16 回测复核更正：现 9 个，差 1〕**（0928–1015 白名单 9 日全 glue `status=ok`，扫档证据见上表〔10-16〕注）。**〔2026-10-15 E3 接线注记〕** `_build_conflict_summary_shadow` 的 `memory=None` 已改 `AgentMemory.from_config()`（aggregator.py E3），3b 权重从等权切换为 `_SOURCE_PRIOR_WEIGHTS` 先验加权（regime 1.5 / capital 1.3 / family_rank 1.2 / catalyst_gate 1.0 / sell_source 1.0 / sentiment 0.8）——此前 6 个有效样本日读数**作废重计，读数起点 2026-10-15 起**；分层抽查一致率须用新口径。
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
| **转正门槛** | ✅ **已拍（2026-10-15 负责人选 A 案·简单门槛）**：**非空可数日 ≥10（present）且其中 status=ok 占比 ≥90% 且判据② 真值违约 0 → 达标**，达标后开 `GOT_MARKET_THEME_ENABLED` 试运行（转正语义 = `overall_confidence` 吃一致性因子，属决策面变更）；达标与否归负责人判定。**非空可数日 8 天（0928–1014），不足 10，语义一致性判定亦未做** |

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
- **结论**：3b 非「直接实施」可得，需先就上述 ①②④ 三项做设计拍板（影子如何绕过 enabled 闸、sector_filter 从何注入、个股级兑现追踪是否复用现基建），并待 D-3 基线满 10 日。当前保持未开工，登记备查。**✅ 已拍板（2026-10-15 负责人选 D-3 满后连设计一起拍）**：D-3 基线起算 2026-10-12，约 **10-27 满 10 交易日**；届时凭基线读数对 ①②④ 三个设计点（影子绕 `_ensure_alphasift_enabled` 硬闸 / sector_filter 注入点 / 个股级 PEAD 复用与否）一次性拍板再起 WI，此前不先行拍设计。

### 项四：竞品经验补强计划批次 A–G

- 本体为独立计划文档 [`docs/got/plans/竞品经验补强计划_20261006.md`](plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md)，已过 UltraReview、批准序 D0→D→G→A→B→C2→C1→E1→E3→E2→F。**逐批实施逐批验**（每批独立 WI/commit），不并入本外网对标实施计划的单次改动。本清单不代判，进度归该计划自身登记面。

---

## ‼️ 2026-10-11 补登（竞品经验补强计划：批次 D0/D/G/A/B 影子判据入本清单）

**背景**：[`竞品经验补强计划_20261006.md`](plans/%E7%AB%9E%E5%93%81%E7%BB%8F%E9%AA%8C%E8%A1%A5%E5%BC%BA%E8%AE%A1%E5%88%92_20261006.md) 已批准全量按序实施（外网对标计划 §7 拍板 5）。本轮已落 **D0→D→G→A→B**（均为影子/观测先行、零决策面变化）；判据登记如下，达标归负责人。

| 批次 | 开关与缺省 | 影子产物 | 转正门槛 | 当前状态 |
|---|---|---|---|---|
| **D0** 个股当日涨幅批量取数 | 无开关（`TencentFetcher.get_stock_quick_change` 为取数原语） | —（供 D 消费） | — | 已落，5 单测 | 
| ~~**D** news_pulse 持仓异动归因~~ | —（`GOT_NEWS_PULSE_*` 已不存在） | — | — | ✅ **已销（2026-10-30 运行时收敛）**：`news_pulse.py` + `test_news_pulse.py` 删除，原「手动跑一周」判据作废 |
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
- 拦截口径（tradability.py）：跌停封板→`limit_down`（现价≤跌停价且 high 贴住→全天未开板；盘中开板视为可成交）；当日建仓→`t_plus_1`；涨停封板→`limit_up`（现价≥涨停价即拦，含一字与盘中封板；buy 方向）。
- 降级红线：行情缺失/超时/异常一律放行不拦截，仅留 `quote_unavailable` 告警标签。
- 单测：`tests/test_gate1_tradability_wiring.py`（**28** 个用例：三态行为 + 降级红线；本行原记 17 个，2026-10-12 `--collect-only` 实测为 26，2026-10-27 增 2 例“盘中开板后封板仍拦/未触板放行”）。
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
| **A5 影子期** | 时间窗 | ✅ **已结（条目失效）**：A5 glue 影子期已于 2026-09-17 按负责人裁决转正为 A6 阶段 1（见下方同名状态变更节），«≥10 交易日» 判据不再适用；后续 glue 水位观测改由 M1–M6 承担 |
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
