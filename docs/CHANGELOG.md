# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

## [Unreleased]

### 本地工作（GOT 及仓库自维护，非上游发布内容）

- [feat(got/node6)] **家族簇：K 线聚类 → 家族 identity（2026-11-03，规则层回测结案）**：FTS/FXG 家族级闸门原依赖 `frame_group` 关键词首命中，2026-09-28–10-30 生产归档 47/93 个 actionable 主线板块落「其他」（50.5%）——`其他` 把石油开采/石油/酿酒等异质板块压成同一 endurance 家族（E2 P90 分母与 crowded 判定失真）、FTS 台账 `其他` 键跨日混杂（10-16→10-21 latch 0→1→3）、Phase 2 层 A join（`_assess_risk` 的 `frame_group ∈ family_suppression`）空转。改用 Node 2 `theme_families`（90 日收益相关矩阵 + complete linkage）作 identity 第一性来源：新增 `src/got/family_cluster.py`（`cluster_id`=成员集 sha1、跨日按 Jaccard≥0.34 继承、词表多数票「其他」成员不计票、A6 复核裁决标签、glue 分歧升审、失败一律降级不 raise）；新增 `UnifiedContext.family_cluster_shadow` 随 unified.json 落盘（免动 archive 白名单）；endurance/FTS 键统一走 `resolve_endurance_key`：**逐板块**——已具名板块（glue/`frame_group≠其他`）维持现状键，只有落「其他」/缺失的板块换簇 ID（只堵漏报，不把簇内具名板块改判成别的家族）；live 期簇 `family_label`（A6/vote）也只补给落「其他」的成员，已具名板块永不改判。顺带修正 `_load_previous_family_suppression` 锚定 `_run_date`（原 `date.today()` 回放读相对真实今天的归档，与 W1 同型前视）。开关 `GOT_FAMILY_CLUSTER_ENABLED/REVIEW/JACCARD_MIN`（`.env` 1/**off**/0.34，代码缺省 0/off/0.34）：**不设每日影子**，规则层直接结案，A6 仅离线抽样核验后才允许 live。
  **结案回测**：新增 `scripts/backtest_family_cluster.py`（链式重放+临时归档树+逐日对照，产物 `data/got_daily/backtest_family_cluster_<start>_<end>.md`）。0928–1030 共 19 个交易日 / 88 个 actionable 主线板块日：`其他` 46→0（52.3%→0.0%）、endurance 家族 48→63、46 个板块-日改键（其中原键=`其他` 33 个；另 13 个是 replay 无 glue 的伪差异，生产 glue 正常时不发生）；石油簇/酿酒簇/路桥簇多日同 ID（Jaccard 继承）。FTS 台账 `其他` 键消失（10-16 `其他(d0:石油开采/石油/路桥)` → `4ea31266c8e7(d0:路桥)`；10-20 `其他(d2:…/酿酒)` → 石油簇+军工+酿酒簇）。回测还证明「整簇下发」会把 `AI眼镜 AI软件→军工`、`化工机械 周期资源→AI硬件` 等具名板块改判成错家族，故定为逐板块口径。
  **验证**：`tests/test_family_cluster.py` 38 passed（shadow/off 不回写、多数票与并列无票、逐板块改键、vote/A6 改写只覆盖「其他」成员、Jaccard 双分支、A6 失败降级+熔断、glue 状态纪律、归档锚定、endurance 回滚路径、live 回写、Phase 2 层 A + FXG join）；相邻 12 文件 277 passed。真机 LLM 单簇冒烟：石油簇 → `周期资源`。判据与结案读数登记 `docs/got/verification.md` 2026-11-03 节。

- [fix(got/node2)] **家族聚类基改残差相关：90 日原始相关被大盘 beta 同化（2026-11-03，用户实证质疑）**：`theme_families` 原用板块原始收益相关 + complete linkage@0.65。实证（20261023 归档 + 板块 K 线截当日）：38 成员簇簇内 pairwise 相关 raw 90/40/20 日最小 +0.70/+0.70/+0.74、中位 +0.92/+0.91/+0.93，去截面均值/β 调整后中位 ≈ -0.05，重聚 1 簇（28 只）→ 13-18 子簇（最大 5）——问题是**未去市场因子**，不是窗口长短（20/40/90 均 beta 饱和）。修：默认改残差相关（`GOT_FAMILY_CLUSTER_BASIS=residual`，`raw` 回滚），窗口 `GOT_FAMILY_CLUSTER_WINDOW=60`，并修正取窗方向（原 `[:min_len]` 在序列不等长时取最老段 → `[-window:]`）。真同族残差相关不受影响（石油开采/石油 +0.98、白酒/酿酒 +1.00、芯片/半导体 +0.95、创新药/医药 +0.94、军贸/航空 +0.88），跨族伪相关被削（芯片/军贸 0.51→0.14）。新增 `TestClusterBasis`（raw 合并四板块 / residual 拆成真子簇两例）。**纯度对照**（新增 `scripts/backtest_family_cluster_purity.py`，按日重建 0928-1030 共 19 日簇，K 线截当日无前视）：簇内受控家族多数派占比 **0.654→0.797**、最大簇 **39.1→23.6**、成员落 >10 簇比例 **0.625→0.258**、actionable 主线「其他」落 >10 簇 **11/46→4/46**（日均参与簇 9.4→20.2、具名成员 91.2→84.0）。**大簇二级切分**（2026-11-03，回答「AI硬件/AI软件还能细分吗」）：`GOT_FAMILY_CLUSTER_SPLIT=1` 缺省，≥10 成员的一级簇在残差相关 0.88 下再聚一次。0928-1030 回测：纯度 **0.797→0.831**、最大簇 **23.6→8.0**、成员落 >10 簇 **0.258→0.013**、actionable「其他」落 blob **4/46→0/46**；簇数 46→62（10-23）。10-23 的 23 成员 AI 混编簇拆成 `光刻机/芯片/存储/先进封装/OLED`(6)、`玻璃基板/PCB/MiniLED/折叠屏/消费电子/苹果`(6)、`东数西算/数据中心`(5)、`CPO/铜缆/光通信/5G/6G`(5)、`多模态/人工智能/AI智能体`(4)、`AIGC/国资云/短剧`(4)、`AI眼镜/AI手机`(4)；阈值 0.90 更细（0.842 / 最大 7.5）但碎片更多。明细：`scripts/backtest_family_cluster_purity.py --detail`。注意：0928-1030 规则层回测跑的是归档 raw 簇，只验证解键逻辑；簇成员变化后 coverage/纯度以重建簇读数为准。
- [fix(got/node6)] **A6 家族标签离线抽样审计 + 两处 live 规则修正（2026-11-03）**：新增 `scripts/audit_family_cluster_labels.py`（按当前残差+切分基重建归档簇 → `REVIEW=shadow` 只出标签不写回 → 落 `audit_family_cluster_labels_<dates>.md`）。抽 10-21/10-23/10-30 共 **60 簇**：复核异常 **0**、`a6=其他` 18（多为单成员或真混编：汽车整车/啤酒/中成药、煤炭/石油）、vote 簇 19 个 a6 同意 15（**79%**）、live 会改写「其他」板块 9 个。审计抓出两处 live 缺陷并修：① **A6 拒绝被 vote 覆盖**（`石油加工→新能源`、`DRG-DIP→AI软件`）→ live 优先级改为 `A6 approved > A6 rejected(None) > vote > cluster_id`；② **live 曾整簇下发标签**（混合簇被整簇标成 AI软件）→ live 也与 off/shadow 一致逐板块，簇标签只补给落「其他」成员，已具名板块永不改判。`tests/test_family_cluster.py` 新增两例（拒绝压过 vote / live 只改「其他」成员）共 42 passed。`REVIEW` 仍为 off。
- [fix(got/archive)] **归档旁挂产物白名单收口（2026-11-02）**：`archive_daily_run` 的原子替换只搬运白名单文件，`shadow_attention.json` / `shadow_shortlist_quota.json` 未登记 → Node 4 归档前虽已落盘（10-30 日志 4 次），同日再次归档即被整目录替换删除。新增模块级 `PRE_ARCHIVE_ARTIFACTS` 单一登记点并补齐两项，新增 `tests/test_archive_pre_archive_artifacts.py`（同日二次归档后产物仍在）；attention 影子判据计时自修复后首个归档日重算。

- [fix(got/node4)] **Q1 low 救援单对象输出被整批丢弃（2026-11-02）**：单条送审时模型返回裸对象 `{"index":0,...}`（10-30 `node4.low_rescue:004` status=ok），解析器只收数组 → `ValueError: 输出非数组: dict`，归档留痕为「LLM/解析失败」。`_parse_low_rescue_verdicts` 增加对象归一 `[obj]`，标量仍 raise；新增 T2b 端到端用例、T7/T7b 解析用例（`test_low_sector_rescue.py` 15 passed）。同步修正 10-30 新增 `task_id` 参数导致的测试替身签名不匹配（6 例原本已红）。

- [test] **`test_decision_plan_session` 日期炸弹修复（2026-11-02）**：`reconcile` 的过期判据读 `date.today()`，用例把 `valid_until` 固定在 2026-10-29 → 11-02 起恒判 `expired`（`→ executed` 断言失败）。改为 `date.today()+3d`，保持原对账语义；该文件 2 passed。

- [docs] **文档债清账（2026-11-02）**：① `CLAUDE.md` / `architecture.md` 删除 Node 7「LLM 精炼」口径（改「规则推理，0 次 LLM」）并移除已无引用的 `REFINE_TOP_N`/`REFINE_MAX_SECTORS`（常量+import 一并删除）；② `verification.md` 销 `news_pulse`（模块已删）与 A5 影子期（已被 A6 转正覆盖）两条，并新增 2026-11-02 节登记 10-30 窄 Agent / 只读复盘 / LLM trace 开关判据与两处修复判据；③ `.env.example` 补登 10-30 运行时键；④ `got/README.md` 更新 win_rate 回测待执行、Phase 6-0/6-1 状态、Phase C/D/E/F 已实施、批次 D 销账、项一开关已翻 true、3b 有效日复核，`OPEN-C3` 简报移入 `archive/` 并同步 `INDEX.md`/`archive/README.md`；⑤ 复盘稿 §11/§12 状态头改为「已落码（10-30）」；⑥ backlog §7 补回只写在修订行的 #22（CLS 分页）并为 #17 注记 F1 已影子落码；⑦ `flowchart.md` 三处「板块精炼 / LLM 精炼注入」口径同步（html 直看版未重生成）。

- [修复] **P0 持仓退出与资金流口径（2026-11-02）**: `decision_plan.assess_holding` 将 P4 快照退出提到最高优先级，不再要求当日 `sector_role=="ambush"`，修复 002009 被 boards 判为 `mainline` + `thesis_drift_grace` 时 `fade_sell` 永不登记；`board_aliases` 新增持仓 thesis→资金流口径桥接（`动力电池→动力电池回收`），Node 3 `holdings_flow_index` 与 Phase 2 消费侧把 thesis 板块并入查找名，修复 H1 对 002009 的结构性漏命中；`decision_plan.reconcile` 在 buy plan `cancelled` 且票消失时关闭对应 open thesis/ambush 快照（I-4，防无持仓锚污染历史回查/P4）；T1-H 维持影子（有效样本仅 3 日/1-2 只，待 ≥10 日且 ≥3 只复评）。测试：`tests/test_holdings_rewiring.py` + `tests/test_decision_plan_session.py` 40 passed，含三项 failing-before 读数。

- [修复] **持仓身份锚 + 父→子别名守卫（2026-11-02）**: `UnifiedScoringResult` 新增 `identity_sector_name`/`identity_family_name`，`_find_stock_sector_with_thesis` 把 thesis 买入身份固定为身份锚，当日 `sector_name` 只作当前匹配标签；`reasoning` 在两者不一致时追加 `[身份:...]`。`board_name_resolver` 增加窄化别名守卫：`source=auto/auto_lowconf` 的父级→子概念映射（如 `化工→氟化工概念`）在加载与 `confirm()` 回写时拒绝，`source=manual` 保留人工裁决；服务器 `data/wencai_board_aliases.json` 已删除该坏条目并留备份。测试：resolver 34 + 持仓身份/别名 80 passed。

- [docs] **新增 GOT 待办核验清单（2026-11-02）**：`docs/got/plans/待办核验清单_20261102.md` 对文档×代码×ECS 生产归档做交叉核验，关闭 D3 10-20 阻塞项（10-27/10-28 `lane_budget_applied=true` + `buy_dip` 进 allocation），更正 3b/4b 为各 11 个有效日、jump penalty 10 日 `agree=6/10` / `switches 7>3` 不达标、10-30 生产为旧运行时（基线自 11-02 起）；同步登记 `got/README.md` 与 `INDEX.md`。不改代码/决策。

- [feat(got/runtime)] **GOT 语义运行时收敛 + 两个窄 Agent + 只读决策复盘（2026-10-30）**：新增 `src/got/agent_runtime.py`（run 级 `EvidenceRef`/`SemanticStep`/`GOTAgentRuntime`，统一模型路由、重试、usage、证据闭包与 `agent_run.json`/`agent_steps.jsonl` trace；`GOT_LLM_TRACE_ENABLED` 只控制原始报文落盘）。删除纯展示 LLM 调用（Node 1/2/3/4 reasoning、Node 6 refine、Node 7 LLM 精炼），Node 4 concepts/risks、Node 5 hot_topics 改由 A6 `SemanticGlueResult` 超集一次合成回填；Node 6 A6 增加 `evidence_ids` 闭包（伪造 ID → S2_validate_failed），narrative 进 reasoning、cross_sector_risks 并入 key_risks。事件身份改用 `normalize_title+source+as_of` 稳定 ID（历史相似命中复用旧 ID/first_seen），T5 LLM 对齐删除 → `_align_macro_rule_events` 仅规则扇出；新增 `apply_glue_relations` 将 A6 relations 回填 `llm_narrative/global_map` 并 UPSERT 日历，选股消息面 `_score_news` 直读 relations（缺失标 `relation_missing`）。新增 `semantic_memory.py` + 3 张窄表（identity/attribution/narrative realization，as_of 上界、replay 零写），`narrative_agents.py` 身份/涨跌归因（≤10 只/批、novelty 需双源、无证据 abstain/unexplained），`audit_agent.py` 只读复盘（Collector→Planner→Scout→Griller→Writer→Verifier，写 `data/got_audit/`），`scripts/replay_got_agent.py` 对照 harness（archive_approximation，不宣称收益提升）。`news_pulse` 及其配置/测试删除；`SCORING_FORMULA_VERSION=2026-10-30.1-agent-runtime-v1`。归档渲染同步增强：`archive._render_a6_glue_formal` 新增 A6 relation 证据表（事件标题/方向/强度/置信度/证据数/传导路径）、证据闭包统计与共享语义产物块（narrative/concepts/key_risks/cross_sector_risks/hot_topics），`_event_title_map` 负责 event_id→标题回连，后续每次 `--got` 的 reasoning.md 自动可审。验证：新增 runtime/narrative/audit/consolidation 测试 + 既有 GOT 相关 16 文件 **236 passed**；真实 `main.py --got`（2026-10-30）Phase 1+2 完成（trace 14 步/81445 tokens/status=ok，A6 9 relations + narrative/concepts/risks/hot_topics 回填，选股消息面 relation 映射 4 板块），`--got-audit 20261030` status=ok 且报告写明 archive_approximation。

- [文档] **新增埋伏道误判复盘（002009 天奇，1028 建仓 / 1029 未止损，2026-10-29）**：归档 [`docs/got/plans/埋伏道误判复盘_002009天奇_20261029.md`](got/plans/%E5%9F%8B%E4%BC%8F%E9%81%93%E8%AF%AF%E5%88%A4%E5%A4%8D%E7%9B%98_002009%E5%A4%A9%E5%A5%87_20261029.md)（6 P0 / 8 P1 / 4 P2 + **N-1～N-6**）。核心结论：机器人/具身智能票经「动力电池回收」概念进入 ambush 道；该板候选流入由 PCB/AIDC 资金主导（候选子集占比 1日 71.6% / 3日 84.8%），机器人系仅 15.4%/8.4%，天奇自身 3 日净流出 0.50 亿；G-A4 同板块同分、同分取问财序第一；ambush 决赛票不跑技术/风险评分（`tech=ambush_rank×100`、`risk` 默认 100，composite 57.8 中 20 分权重来自默认值）；1029 硬止损被 `thesis_drift_grace` 先返回短路。**§8 补 Node 2 盲区**：10-28 人形机器实为 active_mainline（被组名「减速器」隐藏）、10-29 因 `score_drop≥20` + 减速器 5 日 -0.08% 刀锋判破位而整组转 exhausted；商业航天 10-27 `ambush_score=0.27` 距 0.30 仅 0.03，10-28/29 分数未落盘不可审计；10-29 板块指数 -2.25% 而中国卫星/上海沪工涨停，D7/D9 映射仅按当日 f3 取前 300（`n=300`）且缺失无标记——10-29 快照前 500/516 未见商业航天，10-28 排序未回溯，龙头维度实际不可确认。**§9 追加 B+C 设计（拍板方向，未实施）**：T1 评分+催化 / T2 单评分 / T3 单催化三层准入与排序（T3 限 2 只/日、系数 0.5）；催化源扩到原始新闻检索、前瞻/潜在事件池、跨日发酵，并保留闸门否决与 shadow 回滚。**§10 追加催化剂重复身份分析（未实施）**：10-29 归档 5 条 OpenAI 为同一全球事件的板块扇出，HBM 11-04/05 为同一 `source_news` 的叙事重复；根因是 `event_id=MD5(source|type|sector|date)` 与 `_deduplicate(sector,date)` 把逻辑事件/行身份压在一起，叙事无身份锚；修法分展示聚合、同源合并、Prompt 约束、`logical_event_key+occurrence` 四层，D1/D3 涉及 P6 与 DB 边界。**§11 追加叙事层 Agent 化设计（2026-10-30 裁决，未实施）**：规则在 A6/Node 4/T5/催化剂叙事/T3 已多次证明无法处理开放语义；改为两个窄 agent——概念身份时间序列 + 个股涨跌归因，规则保留硬闸门/仓位/止损/证据校验，agent 只读、结构化输出、影子先行。**§12 追加自迭代审计 Agent 化设计（2026-10-30，未实施）**：把本轮手工复盘固化为只读流水线——Collector 证据包 → Planner 异常分诊 → Scout 并行取证 → Griller 反证 → Writer 结构化报告 → Verifier 行号/数值/链接校验；报告只写草稿、人工裁决，不自动改代码/交易，下一轮做复发检查。零生产代码改动，修法待负责人裁决。

- [feat(got/node4)] **Node 4 分层粗筛：事件簇索引 + 分片泛读 + MMR 终审（2026-10-29，缺省 `flat`）**：小时快照让单日新增达数千条，旧 `_shortlist_titles` 把「实时池 + 近 3 日补读」一次性塞进单次 prompt 只选 12 条（10-28 实测 3150 条 / 27.7 万字符），补读行无 snippet，「扩大上下文」不是可靠召回。新增 `src/got/news_retrieval.py`：`normalize_title`/`title_similarity` 成为全仓唯一实现（Node 4 委托）→ 完全同题归并（主行 URL>snippet>新>原序，附 `member_titles/sources/member_count`）→ 标题 embedding cosine ≥0.78 连通分量（复用 `glue_pipeline` 的 `embed_texts/_cache_key/_load_embed_cache`，按日缓存 `data/got_daily/_cache/news_title_embed_YYYYMMDD.json`；候选桶用 normalized title 的 **character bigram 倒排**——旧「实体 token×日龄」桶实测漏掉 127/165 个真实高相似对）→ 日龄（今日/昨日/更早）×产业形态桶连续切片（默认 60，不跨日龄）→ 每片一次 LLM 泛读（≤20 候选，importance/freshness/evidence_index）→ 全局去重 → MMR（`0.55×重要性 + 0.25×时效 + 0.20×来源质量 − 0.60×主题相似度`，同 topic ≤3、同簇 ≤1，终审池 36）→ 终审一次 LLM（最多 12，`merge_with` 仅接受同类目）→ 真实代表条目 + 簇溯源字段。降级契约：embedding 不可用→bigram Jaccard 兜底；单片失败→跳过该片；终审空响应/JSON 失败/全非法 ID→MMR 前 12；终审合法空数组→`[]`（不伪造结论）；连续 3 片失败→停止后续调用。`news_retrieval_trace.json` 落簇/片/候选/终审/失败/降级指标；`archive_daily_run` 的原子替换只搬运白名单旁路产物，trace 已加入搬运清单（同 glue/low-events），否则归档后文件被删（实测首跑 10:47 写、10:49 消失；单测 `test_archive_preserves_news_retrieval_trace`）。
  **接入**：`GOT_NEWS_READING_ARCH=flat|cluster`（缺省 flat）；cluster 只替换 `_analyze_with_llm` 的粗筛入口，`extraction_pool = 终审 12 + 产业召回 ≤8`，概念/风险/`all_news_text`/`_apply_source_count`/`_save_events_to_db` 仍吃原始 `raw_news`；`GOT_NEWS_TITLE_SHORTLIST_ENABLED=false` 仍是最高优先级回滚（weight 前 50）。`news_feeds.filter_carried_pool()` 只在粗筛入口/补读剔除 6 个娱乐热榜与对应 `intelligence_newsnow-*`（保留同花顺/财联社热门/澎湃/RSS），实时 `merged_news` 与 DB 行不动；`_persist_raw_news` 先按 `_normalize_title` 同题归并本次入参（跨源转载只落一行，`source_count` 不扩散）。配置集中在 `catalyst_config.py`（shard_size 60 / 并发 3 / finalist 36 / 缓存与 trace 根，越界钳制 + warning）。
  **embedding 坑**：ECS venv litellm 1.82.6 对 DashScope compatible-mode 缺省 `encoding_format` 直接 400（`'encoding_format' only support with [float, base64]`），1.98 无此问题；`glue_pipeline.embed_texts()` 显式传 `encoding_format="float"` 两版通吃。ECS `.env` 的 `GLUE_EMBEDDING_API_KEY` 原为空，回放用临时 `.env.replay`（审计后删除）注入，生产启用时已补入 `.env`（备份 `.env.bak.20261029`）。
  **回放/验证**：新增 `scripts/replay_node4_reading.py`（`--date/--arch/--out`，直接调用生产 `_analyze_with_llm`，只拦网络层 `LLMToolAdapter.call_text` 与历史/持久化入口；不重抓、不写 DB/got_daily）。10-28 ECS 真库（当日 2348 行）：flat = 池 1650 / 短名单 12（含 3 条 SK海力士同题）；cluster = 池 1650 → 簇 1199 / 片 34 / 候选 392 / 终审池 36 / 入选 12 + 产业召回 4（`index_mode=embedding`、`review_status=ok`、LLM 38 次、90.6s、无失败片）；归档 11 条 `major_events` **11/11 可回连原始标题/历史事件**；终审 12 条无同簇重复、无伪造 cluster_id（`review_stats.returned=12, invalid=0`）；娱乐热榜剔除 608 条。相邻 pytest（`test_news_retrieval` 13 例 + `test_node4_title_shortlist`/`test_node4_ambush_events`/`test_news_collector`/`test_glue_pipeline`/`test_news_url`/`test_news_partial_failure`/`test_attention_shadow`/`test_intraday_confirm`）**151 passed**。生产冒烟（ECS 11:00–11:03，`main.py --got`，`.env` 已设 `GOT_NEWS_READING_ARCH=cluster` + `GLUE_EMBEDDING_MODEL=text-embedding-v3` + 补入 key，备份 `.env.bak.20261029`）：池 1089 → 簇 861 / 片 31 / 候选 308 / 终审池 36 / 入选 12（`index_mode=embedding`、`llm_failures=0`、82.1s）；`news.json status=normal`、12 条事件、催化剂日历 14 条；归档后 `news_retrieval_trace.json` 存活（24,361 B）且与日志一致；ntfy 通知发送成功。回滚：`.env` 设 `GOT_NEWS_READING_ARCH=flat`（或 `GOT_NEWS_TITLE_SHORTLIST_ENABLED=false` 回 weight 前 50）。

- [fix(got/display)] **候选兜底理由缺板块名 + 候选表标题写死 Top 5（2026-10-28）**：埋伏道票在 screener 侧不生成 `ai_reasoning`（`_run_ambush_lane` 不做 5 维打分），Node 7 兜底只剩「综合评分 X」→ 归档/终端无法识别板块（天奇股份 002009 实测）。修：兜底拼 `, 板块[<name>]`，ambush 型加「（埋伏道）」尾标（`portfolio_decision.py:4779`）；`trading_recommendations.md` 候选表标题改按实际非持仓条数渲染（`archive.py:2026`，持仓行本就不占候选名额，`unified_scoring` 上限 10 在 `_filter_got_finalists`）。验证：`test_bear_entry_gate.py` 17 passed、`test_phase2_audit_fixes.py` 20 passed、`test_shadow_reasoning.py`+`test_archive_run_counter.py` 32 passed；冒烟断言「板块[动力电池]（埋伏道）」与 `Top 4`。

- [feat(deploy)] **提交后自动同步 ECS（2026-10-28 用户裁决）**：新增 `scripts/sync_server.py`（`git diff --name-status` 清单 → `workbench upload` → 远端 md5 逐文件校验 → `chown dsa:dsa`/清 pycache/`py_compile`）+ 本机 `.git/hooks/post-commit`（**不入库**，每台克隆机重装；内容与边界见 `deploy/README.md`「自动同步」）；基准 `.git/dsa_sync_state.json`、日志 `.git/dsa_sync.log`，失败不推进基准、重复上传幂等。实测：752046f 的 `archive.py`/`portfolio_decision.py` 已同步，运行时全树 **229/229 md5 一致**（`src/`+`data_provider/`+`main.py`）；hook 演练把基准回退到 HEAD~1 后执行，日志记录「上传 2」，基准推进回 HEAD。

- [fix(portfolio/ocr)] **两行版面「下行缺数量列」致整行丢弃（2026-10-28 实测：天奇股份 T+1 买入 2000 股未入预览）**：中信两行版面要求数量在上下行重复出现，T+1 当日「可用」为空 → 下行只剩「市值 / 涨幅 / 现价」3 列，`_parse_two_row_layout` 直接跳过该票（日志 `两行版面校验失败`）。修：下行无重复数量时改用「市值 ≈ 数量×现价（±2%）」兜底校验，`qty×(现价-成本)≈盈亏` 残差守门不变；新增 `test_two_row_layout_without_duplicated_quantity`，`test_portfolio_ocr_two_row.py` **5 passed**。服务器真图复跑：天奇 2000@17.816、润建 700@54.016 两条均出，`reconcile` 预览 buy 2000 / sell 600；`dsa-bot` 14:18 重启加载。

- [fix(got/holdings)] **持仓板块归属被次要 confirmed 概念顶替（2026-10-28 润建股份 002929 被判「光伏」）**：`_find_stock_sector` 只做「个股所属板块 ∩ 当日 `confirmed_sectors`」并按强度取最高；002929 的算力成员板块（东数西算 46.4 / 人工智能 68.2 / 多模态AI 77.2）都在 Node 2 `strength_lookup` 但未进 confirmed，而光伏（composite 35）在列 → 板块分 21、动作 sell。修：新增 `src/got/holding_sector_overrides.py`（人工确认主板块表，默认 `002929→东数西算`；支持 `GOT_HOLDING_SECTOR_OVERRIDES=代码=板块` 追加）；`_find_stock_sector(allow_override=True)` **仅持仓评分生效**，强度取 `strength_lookup`，命中即跳过 confirmed 交集与 thesis 漂移判定，`sector_match_type=override`。**验证**：新增 `test_holding_sector_override.py` 5 例，连同 holdings/gate1 **67 passed**；服务器冒烟 002929 → 东数西算 46.4、`is_override=True`。**边界**：候选股不受影响；覆盖表只收人工确认项，不做通用回退。

- [feat(got/node4)] **Node 4 消息面补强六项（2026-10-28）**：URL 指纹去重 / 热榜元数据落库 / 正文级精读（影子）/ 权威 RSS 注册 / 分发失败判定修复 / 短名单类目配额（影子）。
  **P1-1 去重**：新增 `src/got/news_url.py:canonicalize_url`（去 utm_/spm/weibo_/bd_ 等追踪参数、统一 scheme+host、去 www./尾斜杠/fragment）；`news_feeds._deduplicate` 先判 URL 指纹再回退标题相似度 0.45；`node4_raw_news` 新增 `url_canonical`（迁移+索引），`_persist_raw_news` 加 Step 0 URL 精确匹配（旧行 NULL 跳过，行为同现状）。
  **P0-1 热榜元数据**：`fetch_newsnow_hot` 用**响应数组序号**补 rank（API 实测无 rank 字段）、`fetch_ths_hot_topics` 补 `platform="ths"` + `order` 名次；`_deduplicate` 合并被吞条目的 `platform/rank/heat`（`platforms` 集合、名次取更靠前、热度取更大），**`_count_cross_source` 的 `source_count` 零改动**（平台扩散≠独立媒体确认）；`node4_raw_news` 新增 `platform/platforms/rank/heat/snapshot_count` 五列，命中行 `snapshot_count+1`、`appearance_count`（按日）不变；新增 `src/got/attention_shadow.py`（`GOT_ATTENTION_SHADOW` 缺省 true）落 `shadow_attention.json`（多平台扩散/最好名次/快照次数，纯观测，不进 prompt/不进发酵检测）。
  **坑（必须记）**：`MAX_MERGED_NEWS=150` 的 weight 截断把**无发布时间的热榜条目整源丢弃**——2026-10-28 实测 newsnow 203 条 + ths_hot 15 条，merged 150 条中 0 条，platform 列永远落不了库。修：`merge_news` 拆出 `_merge_all` → 新增 `fetch_all_news_pool` 多返回**截断前去重全量池**，`_persist_raw_news` 改吃全量池（`_fetch_news` 返回三元组），**LLM 输入仍是 150 条口径不变**。
  **P0-2 正文精读（影子，缺省关）**：CLS/华尔街见闻采集层停截断（`snippet` 保留全文）；`GOT_NEWS_FULLTEXT_ENABLED`（缺省 false）+ `GOT_NEWS_FULLTEXT_MAX_CHARS`（缺省 1200）；`_format_news_for_prompt(fulltext=...)` 超长走 `_sample_long_text` 头 50%/中 25%/尾 25% 采样（政策结论常在末尾，纯截断会切掉）；新增 `src/got/news_fulltext.py:fetch_article_text`（trafilatura 可选依赖，缺失降级 requests+剥标签，8000 字上限），`_enrich_fulltext` 只对 `url` 非空且 `snippet<200` 的短名单条目抓正文（并发 5，原摘要留 `snippet_raw`）。概念/风险的全量文本在补正文**之前**格式化，开关只作用于细读。**注意**：`--got-replay` 只跑 Node 6，无法回放 Node 4，交叉对照须用真跑或归档 diff。
  **P1-2 权威 RSS**：`RSS_BUILTIN_SOURCES` 逐个 curl 实测后只留存活源——国家统计局「数据发布」（500 items）、中国政府网「原创推荐」（60 items）；剔除 gov.cn 公报（404）、发改委政策发布（200 但空频道 0 items）、工信部（404）。`initialize_builtin_sources` 幂等追加注册（`rss-`+md5(url)[:12]）。**坑**：热榜源 `published_at` 是采集时刻，按 desc 排序会占满全局 `limit=100`，政策条目（feed pubDate 常 1–3 天前）被饿死 → `list_items` 加 `source_id_prefix` 过滤，RSS 走独立取片（`RSS_LOOKBACK_DAYS=7`）。
  **分发失败判定**：`run()` 的 `has_partial_failure` 原读恒空的 `data_quality.fetch_failed_fields` 且比对已删除源名 → `status` 永不 degraded；改判 `missing_fields` 的 4 条高价值线（cls_telegraph/wallstreetcn/eastmoney_kuaixun/newsnow_hot），sina_feed/intelligence 缺失不降级。
  **P1-3 类目配额（影子，缺省关）**：`GOT_NEWS_SHORTLIST_QUOTA` → 粗筛 prompt 追加「同类目 ≤6 条」+ `_apply_category_quota` 把超配额部分整组裁掉（按 LLM 顺序，空位不回填，词表只做形态归类），落 `shadow_shortlist_quota.json`（picked/dropped_by_quota/category_dist）。
  **验证**：新增 `test_news_url.py`(13) / `test_attention_shadow.py`(8) / `test_news_fulltext.py`(12) / `test_news_partial_failure.py`(3)，扩展 `test_news_collector.py`(+2)/`test_node4_title_shortlist.py`(+2)/`test_intelligence_service.py`(+1)；Node 4 相关 13 文件 **158 passed**，相邻 7 文件 **153 passed**。真跑：两次 `--news-collect` → 8 平台 `platform/rank` 落库（rank 1–30）、`snapshot_count` 1→2、`shadow_attention.json`（177 行/14 条多平台）；RSS 注册幂等（2 → 0）、`fetch_intelligence_items` 输出含 6 条 `intelligence_rss-*` 政策条目。
  **评审补修两处**：① `fetch_article_text` 编码兜底——`requests` 对缺 charset 的 `text/*` 响应缺省按 ISO-8859-1 解码，实测新浪页 8000 字全 mojibake，改按 `apparent_encoding` 重解；② `_deduplicate` 标题判重命中时把被吞条目的 `url_canonical` 登记为保留条目的 URL owner，否则「同 URL 换标题」在标题先命中时仍会重复入库。

- [change(deploy)] **服务器代码整体对齐 + 2026-10-28 重跑（2026-10-28 14:00）**：按「本地工作树 vs 服务器」归一化 md5 逐文件比对（252 个运行时 `.py`），同步 21 文件（17 改 + 4 新增：`holdings_context`/`news_url`/`news_fulltext`/`attention_shadow`），全树 **252/252 哈希一致**；备份 `/opt/dsa/backup/align-20261028/code-before.tar.gz`，清受影响 `__pycache__`、`chown -R dsa:dsa`，服务器 import 冒烟通过。重跑 `main.py --got`（13:52–14:00，归档 24 文件，`status=normal`）：**G-A0 剔除 002741 光华科技（+9.99%）**——`funnel_stats.g_a0_limit_up=1`、`ambush_lane_shadow.limit_up_filtered` 留痕；新 BUY **002009 天奇股份**（当时 +1.49%/17.71，非涨停）2400 股 / ¥42,504；`trade_plan_log` 不再报 `DetachedInstanceError`（对账无 pending、装配 2 条成功）；ntfy 单条附件推送成功。**注意**：13:24 旧跑已推过一条错误建议（BUY 002741），今日共两条推送；002741 计划已人工置 `cancelled`，新计划 002009 为 `pending`。服务器现状 = 本地工作树；原有 30+ 项未提交运行时差异（含 `tdx_protocol`/Node 4/持仓接线等），2026-10-28 已分组提交（094b3f6..a039bb4），服务器运行时 `.py` 与 HEAD **252/252 一致**。**同日 14:22 持仓落库**：ntfy `/portfolio confirm` 写入 002009 天奇股份 2000 股 @17.816、002929 润建股份卖 600 股 @56.03（`portfolio_positions` 为懒重建缓存，`get_portfolio_snapshot()` 触发后为 002009 2000 / 002929 700）；14:33 将 002009 入口锚（`holding_thesis_snapshots` id4 / `ambush_position_snapshots` id3）由计划触发价 17.3558 校正为实际成交 17.816（P4 浮盈读数由 +1.69% 修正为 -0.93%），`trade_plans` 未动（buy 仍 pending，明日按持仓判 executed）；审计 `/opt/dsa/backup/align-20261028/anchor-correction-002009.txt`。

- [feat(got/holdings)] **持仓模块三原则接线（H1 供数 / H2 裁决归位 / H3 历史回查，2026-10-27，当日转正）**：
  **原则**：①持仓分析归决策编排层 ②前序 Node 给持仓供数 ③买入时历史决策喂当日决策。计划 `local://holdings-module-rewiring-plan.md`；判据登记 `docs/got/verification.md`。
  **H1（供数，常开零决策）**：新增 `src/got/holdings_context.py`——`build_holdings_context()` 在 `GOTOrchestrator.run()` 头部构建一次 `HoldingsContext`（全账户去重 positions + thesis 入口锚 + 催化剂锚，**只读 SQLite/快照，零 LLM 零爬虫**，失败 fail-open）。Node 2 新增 `_track_holdings_sectors`：`sector_wishes` 中未被当日候选集覆盖的板块**单独补拉 K 线**评分（合并截面只取补拉行排名 → 原 analyzed 排名分母不变），结果落 `SectorContext.holdings_sectors` + `holdings_coverage`，不写 `node2_sector_trends`、不进 `strength_lookup`。Node 3 新增 `CapitalEvidence.holdings_flow_index`（精确→别名→归一化三级匹配 + 1d/3d/5d 流向，未命中显式留痕）。Node 6 新增 `_build_holdings_intel` → `UnifiedContext.holdings_intel`（`sector_role="holding"`、`classification="watch"`，只覆盖 confirmed 未收录板块；**不进 TOP-N、不进选股池、不改 position_limit/埋伏池**）。Node 7 `_build_sector_intel_map` 合并两源（confirmed 覆盖保证分类口径），`_adjust_score_by_sector_intel` 对 `holding` 角色不加不减（未验证维度不得凭空加减分），持仓资金维改用 Node 3 命中键回喂 screener，缺失时 `capital_data_missing=True` + `[资金维缺失→权重已归一化]`（治「缺失吞 0 分」）。
  **H2（裁决归位）**：`_score_holdings` 的 8 分支 action 链（原 :961-1081）搬至 `decision_plan.assess_holding(facts)`——**搬家不重写**，Node 7 只采集事实（宽限用记忆化回调，保留懒写 `drift_watch`）并填 schema；新增 `HoldingAssessmentFacts/HoldingVerdict` + 原因码枚举（16 码）。**修正已坐实误判**：confirmed 板块（`classification=actionable` + role∈{mainline,ambush}）但 `lifecycle=None` 时不再落 40/60 降温档 → `hold`（`sector_confirmed` 守卫）。`GOT_HOLDINGS_VERDICT_V2` 三态，**缺省 `enabled`（2026-10-27 用户裁决：纯新增能力非修正 → 跳过影子纪律直接生效，同 D3 先例）**；`shadow`=只算不生效（取证态）、`off`=kill-switch；对拍产物照旧在产（`mode=enabled`）。
  **H3（历史回查）**：`decision_plan.lookup_decision_history()` 纯 SQLite 读四表（thesis × catalyst_events × trade_plans 近 10 交易日 × ambush snapshots），n<3 不出兑现率；证据进 reasoning（`入场composite → 当日`/`近窗计划兑现率`/`催化剂锚状态`）+ `holdings_history_shadow`。`GOT_HOLDINGS_HISTORY_FEED` 三态**缺省 `enabled`（同日转正）**；P4 分支为 **snapshot ∪ thesis 催化剂锚双锚**（`ambush_position_snapshots` 实测 0 行时也判到期退出）。
  **归档**：`reasoning.md` 🧪 段新增「持仓数据供给（H1）」（诉求覆盖/补拉/缺失 + 资金命中率 + 持仓专属情报逐条 lifecycle）；`holdings_verdict_shadow`/`holdings_history_shadow` 随 `portfolio_decision.json` 落盘。`SCORING_FORMULA_VERSION` bump `2026-10-27.2`（转正当日；跨版本前向统计须按两开关分组，不可与前序归档混池）。
  **验证**：`tests/test_holdings_rewiring.py` **34 passed**（H2 分支表 18 / H1 供数 9 / H3 回查 6 + 开关缺省）；相邻回归 `test_decision_plan_session`/`test_ambush_pool_consumption`/`test_ambush_lane_split`/`test_gate1_tradability_wiring`/`test_family_topping_suppression` **131 passed**。

- [fix(got/limit-up)] **埋伏道绕过 R0 涨停剔除 + GATE-1 仅拦“一字”（2026-10-27 实跑暴露：002093 国脉科技 +9.99%/7.93 仍被 BUY）**：`_filter_got_finalists` 在 R0 之前先把 ambush 票分流到 G-A1~A4，四闸门无涨停过滤，`GOT_TRADABILITY_GATE=shadow` 又只留痕不拦，导致盘中封板的涨停票进入买入建议。修：① `StockScreenerPipeline._is_limit_up` 从 R0 内嵌函数抽为共用静态方法，`_run_ambush_lane` 新增 **G-A0** 在 G-A1 前剔除（`g_a0_limit_up` 计数 + `ambush_lane_shadow.limit_up_filtered` 留痕，fail-open 不把已剔票放回）；② `check_tradability` 买入判定由“现价≥涨停价且 low 亦贴涨停（一字）”改为“现价≥涨停价即拦”，盘中开板后封板（002093 low=7.57、price=7.93）不再放行。**验证**：`tests/test_gate1_tradability_wiring.py` 新增 2 例（开板后封板仍拦/未触板放行）、`tests/test_ambush_lane_split.py` 新增 2 例 G-A0 回归（正常剔除 + fail-open 不回流）；两文件 **62 passed**；真值冒烟：002093 7.93→`limit_up` 拦截、7.80→放行，G-A0 从 2 只候选中剔 1 只并留痕。**边界**：`.env GOT_TRADABILITY_GATE` 仍为 shadow（未越权启用卖出侧 T+1/跌停顺延）；埋伏道 G-A0 已实际生效，GATE-1 新口径待开关切 enabled 后全量拦截。
  **服务器部署（2026-10-28，补做）**：10-27 两处修复当时**只存在于工作区，既未提交也未同步**——服务器 `/opt/dsa/code`（非 git 仓库，文件级部署）仍是 HEAD 版本（`stock_screener.py` mtime 2026-10-21、`tradability.py` 2026-09-30，`strip-trailing-cr` 对比与 HEAD 全文一致）。后果：10-28 13:20 定时跑 `002741 光华科技`（+9.99%、20.25，恰为涨停价）走埋伏道绕过 R0 被 BUY ¥40,500，且 `funnel_stats` 无 `g_a0_limit_up` 键、`blocked_reason=null`（旧“一字”口径见 low<涨停即放行）。10-28 13:40 整文件同步 `stock_screener.py`/`tradability.py`（服务器 md5 = 工作区 `378feb0a…`/`1c92f67d…`），原文件备份 `/opt/dsa/backup/limit-up-20261028/`；服务器冒烟 `_is_limit_up(002741,9.99)=True`、`check_tradability(20.25, pre_close 18.41, low 19.20)→limit_up`；次日 13:20 起由 G-A0 硬拦，不再依赖 shadow 态 GATE-1。

- [change(notify/ntfy)] **附件消息合并为单条「结论 + 附件」（2026-10-27 用户裁决：不要拆着发）**：旧实现先 `PUT` 上传附件（ntfy 会立刻发布一条空标题附件消息）、再 `POST` 摘要并 `attach` 引用 → 用户侧两条气泡；头部编码失败时更退化成 9 段。改为同一次 `PUT` 带 `?title=&message=`（URL 编码，UTF-8 安全；`Filename` 头保持 ASCII），`message` 取 `build_got_notify_summary()` 结论块（10-27 实测 526 B，超 2000 B 按 UTF-8 边界截断），上传失败才退回分段。`NotificationService.send` / `send_with_results` / `_send_to_static_channel` 增加可选 `summary` 透传，仅 ntfy 消费（其他渠道仍收发全文）。**验证**：`test_ntfy_attachment_headers` 4 例、`test_ntfy_bidirectional` 原「PUT + POST 两条」契约改写为「单条 PUT、零 POST」；notify 相关 7 文件 **198 passed**；服务器实发 topic 只多 **1 条**消息：`message=571 B` 结论、`attachment=2026-10-27.md 30876 B`。

- [fix(notify/ntfy)] **长消息附件因中文标题头编码失败而退化成 9 段纯文本（2026-10-27 真机重发暴露）**：`_upload_attachment` 把带 emoji/中文的标题直接塞进 `Filename`/`Title` HTTP 头，requests 发送时按 latin-1 编码 → `UnicodeEncodeError`（真机日志 `ntfy 附件上传失败: UnicodeEncodeError`），30,877 B 正文退回 `split_message` 分段路径（实测 9 条 `(1/9)…(9/9)`），附件从未上传。修：新增 `attachment_filename()` 把标题压成 ASCII（`📊 GOT 每日决策 — 2026-10-27` → `GOT-2026-10-27.md`，全非 ASCII 回退 `got-daily-report.md`），附件侧不再设 `Title` 头——标题/正文改走 query 参数（URL 编码，见下一条单条消息）。**验证**：`tests/test_ntfy_attachment_headers.py` 3 例（ASCII 化 / 全非 ASCII 回退 / 30 KB 一步到附件且头部可 latin-1 编码，旧实现必挂在第 3 例）；与 `test_got_notify_payload`/`test_ntfy_bidirectional` 同跑 **25 passed**；服务器重发后 topic 轮询实测：附件 `name=2026-10-27.md, size=30877`，无分段。

- [feat(got/notify)] **GOT 推送由「9 行摘要」改为「摘要 + reasoning.md 全文」（2026-10-27 用户裁决：全量走附件）**：
  **病灶**：`main.py` 通知 hook 手拼的摘要块只含 `summary`/风险三数/Top3 区间/轮动3/拥挤3/warnings5，10-27 实跑仅 **507 B**；`decision.reasoning`（Phase 2 LLM 精炼 1.1 KB）终端打印了却不进 push，`reasoning.md`（30 KB，Node 1–7 + 影子段）只在归档里。
  **改动**：新增 `src/got/notify_payload.py`——`build_got_notify_summary()` 产出结论块，`build_got_notify_content()` 在其后追加当日 `reasoning.md` 全文（路径由 `archive._default_base_dir()` 定位）；`main.py` hook 简化为「取决策 → content + summary → send」。归档缺失/空/不可读 → 退化纯摘要并 warning，不阻断推送。通道侧见下一条（长消息合并为单条「结论 + 附件」，不再「PUT 附件 + POST 摘要」）。
  **验证**：`tests/test_got_notify_payload.py` 3 例（摘要逐字 + 全文接通、缺档回退、空文件不追加）；相邻 `test_ntfy_bidirectional`/`test_decision_plan_session`/`test_trade_plan_log` 同跑 **34 passed**。真档冒烟（服务器 `20261027`）：总 **30,877 B**，sender 走附件分支——上传 30,877 B、正文取结论块 + `attach` URL。**注意**：公共 ntfy.sh 附件 3 h 过期，长期回看仍以服务器归档为准。

- [fix(got/decision_plan)] **装配/对账成功后仍被日志层报「失败」（2026-10-27 13:20 定时跑归档发现）**：`assemble()` / `reconcile()` 自建 session，`commit()` 后 `close()`，而接线层（`portfolio_decision.py:419-437`）在函数返回**之后**才调 `assemble_log_lines()`；`sessionmaker` 缺省 `expire_on_commit=True` 会在 commit 时过期全部属性，close 后读取即 `DetachedInstanceError`，于是「写入成功」被写成「当日计划装配失败（不阻断）: Instance <TradePlanRecord …> is not bound to a Session」。10-27 实跑复现：`trade_plans` 已落 2 行（002929 SELL / 002093 BUY，`pending`、`valid_until=10-30`），`trade_plan_log` 却是失败行；`reconcile` 同款缺陷，待首个有 pending 计划的交易日才会暴露。修：两处自建 session 显式 `expire_on_commit=False`（外部传入 session 生命周期归调用方，不改）；异常路径文案保留，真失败仍如实留痕。**验证**：新增 `tests/test_decision_plan_session.py`（装配/对账各 1 例，走真实 SQLite）修前 2 failed（`DetachedInstanceError`）、修后与 `test_trade_plan_log.py` 同跑 **12 passed**。
  **服务器部署（2026-10-28，补做）**：同款漏推——修复已提交（e6199a3）但服务器 `/opt/dsa/code/src/got/decision_plan.py` 仍是 `e6199a3^` 版本（mtime 2026-10-20、md5 `71db1eb4…`），10-28 13:20 跑批 `trade_plan_log` 仍在报两条 `DetachedInstanceError`。10-28 13:41 只同步 e6199a3 版本（md5 `d2d95cce…`；**不含** HEAD 的 H2 `assess_holding` 大改，避免与服务器 10-23 的 `portfolio_decision.py` 错配），备份 `decision_plan.py.pre1027`，服务器 import 冒烟通过。**遗留数据**：002741 buy 2000 股/¥40,500 已于 13:30 落 `trade_plans`（`pending`、`valid_until=2026-11-02`），属本次误推；不打算买应人工置 `cancelled`（次日 reconcile 见持仓 0 会自动 cancelled，但当日历史回查仍读到 pending）。

- [新功能] **新闻快照定时采集 + Node 4 补读扩围（2026-10-27）**：
  **病灶**：GOT 与 Node 4 采集绑在同一时刻（交易日 13:20），7 个源全是滚动窗口（CLS 1–3h、华尔街见闻/东财/NewsNow 数小时、新浪 21h），周末/假日新闻对周一 GOT 完全不可见，当日 11:30 前条目到 13:20 也已滚出并永久丢失；既有两条补读通道（`_load_unabsorbed_prev_titles`、`_detect_fermenting_events`）只读已落库行，窗口外新闻从未落库。
  **新增 `src/got/news_collector.py` + `main.py --news-collect`**：复用 `NewsAnalysisNode._fetch_news`/`_persist_raw_news` 采集落 `node4_raw_news`，**零 LLM**；`_persist_raw_news` 改为返回 `(persisted, updated)` 供 CLI 计数。空采集返回零计数不抛错，采集整体异常退出码 1。快照只采 **5 个滚动新闻线**（新浪/CLS/华尔街见闻/东财/NewsNow，`_fetch_news(skip_sources=…, include_intelligence=False)`），跳过 NGA 论坛/同花顺热榜/Intelligence 热榜——非时效源、按小时抓只会招反爬，仍由每日 GOT 全量采；单批实测 ~25 s。
  **补读扩围**：`_load_unabsorbed_prev_titles` → `_load_unabsorbed_titles`，查询由 `fetched_date < today` 放宽到 `<= today`（纳入当日更早时点的快照行），候选元组携带 `fetched_date`，返回字段由布尔 `carried_from_raw_news` 改为 `carried_age_days`（0=今日早前 / 1=昨日 / n=n 自然日前）；粗筛与细读 prompt 加 `[今日早前]`/`[昨日]`/`[N日前]` 前缀，**不做信息强度数值衰减**（市场时效不按自然日龄折减、判定权在 LLM；持续性由发酵检测与 D1 回声守卫负责）。
  **护栏**：新增 `GOT_NEWS_CARRY_DAYS`（缺省 3 自然日，4 天以上连休调 5）与 `GOT_NEWS_CARRY_MAX`（缺省 **1500**，超限按最新优先截断并 warning）。上限从初版 800 上调：真库实测唯一标题/日 360–680 条，周五→周一 4 日窗口合计可到 ~1500，800 会把周末批最旧的一半截掉；1500 条 ≈ 57k 字符粗筛输入，仍远低于渠道上下文上限。补读开关仍复用 `GOT_NEWS_CARRY_PREV_TITLES`（false 逐字节回现状）。
  **部署**：新增 `deploy/systemd/dsa-news-collect.{service,timer}`——`OnCalendar=*-*-* *:10:00`（**每小时 24/7**），`Persistent=true`（纯落库无决策，补采无害），不做交易日 gate（假日新闻对下一交易日有效）。间隔由实测源窗口决定（13:35 盘中）：CLS 单次仅约 20 条 ≈ 35–40 min（请求 200 条只得 21 条唯一，分页在返回重复，深度不可加深）、东财 30 条 ≈ 80 min、见闻 30 条 ≈ 2.5h、新浪 159 条 ≈ 4.2h；≤1h 保住后三条线，2h 会让 CLS/东财盘中成段丢。每批约 17 个 HTTP、~30 s。
  **验证**：`tests/test_news_collector.py` 4 例（计数、空采集、采集异常上抛，及「只采 5 线」——驱动真实 `_fetch_news`，断言 `skip_sources=("nga_forum","ths_hot")` 且 `_fetch_intelligence` 零调用；另有 `_call_llm` 侧效应断言禁止 LLM）+ `tests/test_node4_title_shortlist.py` 新增 4 例（当日早前行回灌、自然日龄标签、上限截断、与当日实时同名去重）；`pytest tests/test_news_collector.py tests/test_node4_title_shortlist.py tests/test_node4_ambush_events.py` **38 passed**，全部 news/Node4 相关 13 文件 **194 passed**。实跑 `main.py --news-collect`：日志仅 `sina/cls/wallstreetcn/eastmoney/newsnow` 五线（无 NGA/同花顺/Intelligence），**31 s**、`原始=150, 新增=128, 更新=22`，日龄补读对真库返回当日行 `carried_age_days=0`。
  **服务器部署（2026-10-27，文件级同步，服务器 `/opt/dsa/code` 非 git 仓库）**：`news_collector.py`（新）+ `news_feeds.py`/`news_analysis.py`/`catalyst_config.py` 整文件落位，`main.py` 先按「服务器基线 + 本功能两 hunk」部署（14:00），同日通知重构的 `src/got/notify_payload.py` 同步后 `main.py` 整文件已与工作区一致（md5 相同），**不存在半套状态**；原文件备份 `/opt/dsa/backup/news-collect-20261027/`。`dsa-news-collect.{service,timer}` 14:01 enable，首跑 14:10（29 s / 新增 23 / 更新 127 / exit 0），补读池 615 条（0/1/3 日龄 = 252/184/179，周末批已入池）。

- [feat(portfolio)] **手机发持仓截图 → OCR → 对账 → 确认落库（2026-10-27，真实券商截图驱动）**：
  **通道**：`bot/platforms/ntfy_stream.py` 新增附件接收——只收 `image/*`（≤15 MB），下载到临时文件后以 `/portfolio` 分发，处理完立即删除；ntfy 对纯附件自动填的 `You received a file: x` 不再被当成自由文本（首版实测因此没进 dispatch）。
  **命令**：新增 `bot/commands/portfolio.py`（别名 持仓/pf/p）：发图即 OCR + 对账，**只预览不落库**；`/portfolio confirm` 才逐笔 `record_trade`，`/portfolio cancel` 丢弃，预览 30 分钟过期；无图则显示当前持仓快照。清单注册数 10 → 11。
  **真实版面解析**：用户真实截图（中信证券 APP）暴露出两个既有解析器盲区——① **一只票横跨两行**（上行=名称/盈亏/数量/成本，下行=市值/涨幅/可用/现价）且**无代码列**；② OCR 把千分位读成 `.`（`68.484.00`、`-2.942.86`）。`portfolio_ocr_import.py` 新增 `_number_variants`（一个 token 给出全部合理读数）+ `_parse_two_row_layout`（用 `qty×(现价-成本)≈盈亏` 恒等式挑选读数，容差 `max(5, 2%|pnl|, 0.2%×qty×price)`，不满足则整行丢弃）。主解析与两行解析**合并**而非二选一（强校验结果优先，主解析补齐未覆盖名称）。
  **名称→代码**：新增 `_load_name_map()` 三级来源——磁盘缓存 `data/stock_name_cache.json` → **TDX(mootdx `stock_all()`，复用 `MootdxFetcher._mootdx_session()` 的验活节点池)** → akshare 兜底；命中失败退化为 `None` 不阻塞预览。裸码经 `_bare_code()` 归一（`SH600519`/`600519.SH` → `600519`）。
  **实测（真实截图 466 KB）**：`润建股份(002929) 1300 股 成本 54.945 现价 52.680 市值 68,484 盈亏 -2,945`——与图面 `-2,942.86` 相差 1.6（恒等式自校验通过）；系统持仓为空 → 生成 1 笔初始买入预览。akshare 名称库当时被东财断连，TDX 通道一次成功并落盘缓存。
  **确认队列落盘（2026-10-27 实测缺陷修复）**：预览状态原为纯内存，服务重启/发版后用户回 `/portfolio confirm` 得到「没有待确认」——实测发生一次（部署新解析器重启）。改为 `data/pending_portfolio.json` 落盘 + 内存优先读取（`trade_date` 以 ISO 序列化，`PORTFOLIO_PENDING_PATH` 可覆盖），TTL 判断移到 confirm 处以便区分「没有预览」与「预览已过期」。
  **OCR 内存治理（2026-10-27）**：模型不再常驻 bot 进程。① 新增 `src/services/ocr_worker.py` 子进程 worker，`ocr_boxes()` 默认（Linux，`OCR_SUBPROCESS=1`）把识别丢进短命子进程，跑完进程退出、内存由内核回收；实测 bot RSS **440 MB → 152 MB**（余下是 pandas/SQLAlchemy 的持仓快照开销），小图冷启动 4.1 s。② 子进程失败自动回退进程内，且进程内路径保留空闲卸载：`OCR_IDLE_UNLOAD_SECONDS`（默认 180，0 关闭）+ 30 s 看门狗 + `gc.collect()` + `libc.malloc_trim(0)`（实测 132 → 94 MB，ONNX/glibc 仍会保留一部分堆，所以子进程才是根治）。③ 子进程超时 `OCR_SUBPROCESS_TIMEOUT=300`，`PYTHONPATH` 自动带仓库根，`OCR_SUBPROCESS=0` 防递归。
  **清仓语义（2026-10-27 补）**：系统持仓是**交易流水回放**（FIFO）而非快照覆盖，因此「旧票已卖出」表现为截图里消失 → `compute_sync_trades` 生成 `sell` 全量清仓单。原实现遇到 `last_price<=0` 会静默跳过（该票永久残留），现改为退回 `avg_cost` 按成本价清仓并在 note 标注「缺现价」；连成本都没有才留待人工。数量为 0 的行解析器不产出，等价于消失，走同一路径。新增 3 例测试覆盖（消失+有价、消失+仅成本、消失+全无）。
  **未匹配保护**：持仓未解析出代码时**不进交易**（`record_trade` 需要 symbol），回执单列「未匹配到代码（不写入）」；全部未匹配则直接给提示、不生成预览。名称解析在自建映射之外再复用项目既有 `src/services/name_to_code_resolver.resolve_name_to_code`（本地 `STOCK_NAME_MAP` 反查 + 拼音 + 自带 akshare 缓存），与 `forum_sentiment`/`import_parser` 同一口径。
  **验证**：`tests/test_portfolio_ocr_two_row.py` 4 例（千分位/三位小数歧义、两行版面、数值不自洽拒绝、多只持仓）、`tests/test_portfolio_command.py` 9 例（预览不写库、confirm 落库、cancel、无待确认、过期、OCR 失败、无代码持仓剔除、快照、"发图"提示）、`tests/test_ntfy_bidirectional.py` 19 例（含附件下载→`/portfolio`、非图片忽略、占位文案映射）；本地 `-k "portfolio or ntfy or bot or notification"` **534 passed**；服务器端到端：发图 → 解析 → 预览回帖 → `/portfolio cancel` 成功。

- [feat(notify)] **ntfy 双向通道落地：手机发指令 → CommandDispatcher → 回帖 out topic（2026-10-26）**：
  **背景**：服务器出网实测 `api.telegram.org`（IPv4 `202.160.130.145` TCP 超时 / 无全局 IPv6）、`web.telegram.org`、`discord.com`、`matrix.org`、`api.line.me` 全部 `000`，Cloudflare WARP 出口 `engage.cloudflareclient.com:2408` 也被封——**Telegram/Discord/Matrix 均不可达**；`ntfy.sh` 发布/订阅/附件全通（`speed.cloudflare.com` 通但仍无隧道价值）。国内可达的钉钉/飞书/企微/Server酱/PushPlus 均有内容审核，PushDeer/Bark/Gotify 单向，故取 **ntfy**（开源、无审核、可自建、topic 分项目、收发同协议）。
  **新增 `bot/platforms/ntfy_stream.py`**：`GET <server>/<cmd-topic>/json` NDJSON 长连接（实测 keepalive 45 s、流可持活）→ 逐事件解析 → 只处理 `event=message` → 转 `BotMessage(platform="ntfy")` → 既有 `CommandDispatcher.dispatch()` → 回复分段（≤3800 B）发回 out topic；**回声防护**：本进程发出的消息带 `bot` tag，订阅端见 tag 即跳过，故 cmd 与 out 可同 topic；**断线续传**：`since=<最后一条 id>`，首连用 `since=15m` 不回放历史指令；`stop()` 关闭活动响应解除读流阻塞。入口 `start_ntfy_stream_background()`，`main.py:start_bot_stream_clients` 按 `NTFY_STREAM_ENABLED` 启动。
  **`src/notification_sender/ntfy_sender.py` 长消息**：实测 ntfy 单条 message 上限 4096 B（5 KB → HTTP 500、3 KB → 200），28 KB `reasoning.md` 直发必挂。改为：≤3800 B 走原 JSON；超限先 `PUT <server>/<topic>`（`Filename` 头）上传全文取 `attachment.url`，再发「摘要 + `attach`」消息；上传失败退回按字节分段（CJK 按字符硬切）。`split_message()` 保证每段 ≤3800 B 且拼接无损。
  **配置**：`NTFY_URL`（出站 topic）、`NTFY_CMD_URL`（入站 topic）、`NTFY_STREAM_ENABLED`、沿用 `NTFY_TOKEN`。
  **常驻形态**：新增 `main.py --bot`（只起 Stream/ntfy 订阅，不跑分析、不起 Web）+ `deploy/systemd/dsa-bot.service`（`Type=simple`、`MemoryMax=700M`、`Restart=on-failure`）。服务器实测：`systemctl enable --now dsa-bot` 后 `active`，**空闲 RSS 93 MB**（懒导出生效，pandas 只在 `/analyze` 时拉起），`NRestarts=0`。
  **重放缺陷（实测发现并修复）**：首连原用 `since=15m`，重启后会把 15 分钟内的旧指令再执行一遍（实测同一 `/status` 回了两条）。改为**首连锚定进程启动时间戳**（`since=<started_at>`），此后用 `since=<最后一条 id>` 精确续传；重启后发 `/help` 实测**恰好 1 条**回帖。
  **单 topic 对话模式（2026-10-27 落地）**：`NTFY_CMD_URL` 与 `NTFY_URL` 配成同一 topic 即变成一条对话流——手机在同一个主题里发指令、收回复、收日报。为防止自激与误触发，三道闸门：① 机器产出一律打 `robot` tag（`NtfySender` 日报 + 监听端回帖），订阅端见 tag 直接跳过（兼容早期 `bot` tag）；② 与 dispatcher 同一解析路径判定「像不像指令」——`/cmd` 或中文命令词命中才算「在叫机器人」，未命中且短于 200 字符/10 行则保留 NL 路由但 `mentioned=False`（不触发问候），否则按机器文本丢弃；③ 单 topic 模式下回帖与日报同流，用户可在 App 内直接看到上下文。实测：`/status` 恰 1 条回帖、3 KB 日报推送 0 次 dispatch、0 条问候（`grep 收到命令` 仅 1 行）。
  **验证**：`tests/test_ntfy_bidirectional.py` 16 例（端点解析与 sender 实现逐 URL 一致、keepalive/open/`robot`+`bot` tag/空白消息均不分发、整段机器文本丢弃、短自由文本保留 NL 但不伪装 @、分发后回帖带 `robot` tag、首连时间戳锚定与 `since` 续传、长回复分段、长消息附件优先与上传失败回退、`stop()` 关流与 `is_running` 语义）；**本地 `pytest`：`test_ntfy_bidirectional.py` 16 passed；`-k "bot or ntfy or notification or dispatcher"` 383 passed**。服务器实测（`/opt/dsa` venv py3.14）：向合并 topic 发 `/status` → **2 s 内**收到带 `robot` tag 的状态卡。

- [perf(bot)] **常驻监听链 import 瘦身：PEP 562 懒导出砍掉 pandas/akshare/SQLAlchemy（2026-10-24，ECS 1.7 GiB 内存治理）**：
  **病灶**：飞书 Stream 监听入口 `bot.platforms.feishu_stream` 的 import 链把 `bot/__init__`（→`dispatcher`→`commands/analyze|ask`→`data_provider.base`）、`bot/platforms/__init__`（→`dingtalk` webhook 客户端）全部拖起来——**命令处理与 webhook 平台的重量级依赖被常驻进程白白驻留**。`bot.platforms.feishu_stream` import 单独实测 `+116 MB`（pandas+akshare，`npimport_probe`+`smaps_probe2` 两口径互相印证），`import bot.dispatcher` 经 `src.storage` 再 `+19 MB`（SQLAlchemy）；1.7 GiB 机器上这笔常驻开销决定 `dsa-bot` 能不能常驻。
  **改法**：`bot/__init__`、`bot/platforms/__init__`、`bot/commands/__init__` 三个包 `__init__` 全部转 PEP 562 懒导出（`__getattr__` + `globals()[name]` 缓存），`ALL_PLATFORMS` 从空 dict 改为**访问时惰性构建**（`from bot.platforms import ALL_PLATFORMS` 时才 import `DingtalkPlatform`，webhook 模式 `get_platform('dingtalk')` 不回归）；`ALL_COMMANDS` 同样访问时才逐个 import 十个命令模块。命令实现里三处顶层重量 import 降为方法内：`analyze.execute`/`ask._parse_stock_codes` 的 `data_provider.base.canonical_stock_code`、`ask` 预热点的 `src.storage.get_db`——`/analyze`、`/ask` 真正执行时才拉 pandas/SQLAlchemy。
  **验证**：本地 `pytest -k "bot or dispatcher or command"` 106 passed（`test_ask_command.py` 的 prewarm 断言 patch 目标同步改指 `src.storage.get_db`）；`test_feishu_stream(_ordering)?`/dispatcher/status/market 37 passed。服务器实测：`import bot.platforms.feishu_stream` 链 RSS 232 MB（改前同口径 348 MB，**省 116 MB**），pandas/akshare/numpy/SQLAlchemy 零驻留；`ALL_PLATFORMS=['dingtalk']`、`get_dispatcher()` 注册 10 命令、`ALL_COMMANDS` 10 个类均不回归；`from-import` 形式（`from bot.platforms import X`）走 `__getattr__` 语义成立，无动态属性引用破坏（`grep` 全仓核对）。

- [feat(portfolio)] **持仓截图 OCR 在 py3.14 上可用 + 版面解析（2026-10-24，为「消息里发截图更新持仓」铺路）**：
  **病灶一**：`paddlepaddle` 在 Python 3.14 无 wheel（`pip index versions paddlepaddle` → No matching），ECS 上 OCR 直接不可用。改 `PortfolioOcrImportService._load_backend()` 双后端：paddle 装了优先，否则退 `rapidocr-onnxruntime`（ONNX 版 PP-OCR，模型随包，离线可跑）；`ocr_boxes()` 统一输出 `{text,score,x0,y0,x1,y1}`，paddle 2.x/3.x/rapidocr 三种返回格式都归一化。
  **病灶二**：`parse_holdings_from_ocr` 的 `pattern1` 是「名称 数量 成本 现价」紧邻匹配，遇到券商常见的「平安银行 000001 1000 11.20 11.35」会把 `000001` 当数量 → `int("000001")=1` 股、成本变 1000，**静默毁数据**。新增 `parse_holdings_from_boxes()` 按版面（y 聚类成行 + 列语义：名称=CJK 串、代码=6 位合法前缀、数量=整数、价格=小数）解析，并在有市值列时用「数量×现价≈市值」自检；`parse_holdings()` 统一入口默认走 boxes，纯文本老路径保留但标注风险。
  **病灶三**：`compute_sync_trades` 直接拿 `h.symbol` 与 `system_positions` 键做集合差，而持仓表存裸码（`record_trade`→`canonical_stock_code` 只 upper）、`match_stock_name()` 返回 `SH600519` → 同一只票会同时被判「新增买入 + 清仓卖出」。新增 `_symbol_key()` 双侧归一化（剥 `SH/SZ/BJ` 前缀与 `.SH/.SZ/.BJ` 后缀）后再比对。
  新增 `PortfolioScreenshotIngestService`（`src/services/portfolio_screenshot_ingest.py`）：图片字节/路径 → OCR → 解析 → 对账 → 可直接回执的文本；默认只算不改库。
  **验证**：`tests/test_portfolio_ocr_boxes.py` 11 例（代码列不被当数量、价格列序、表头行不算持仓、行聚类、旧正则确实误读的对照、SH/SZ 前缀与裸码对账不发生幽灵交易、数量差跨格式仍生效）+ `tests/test_portfolio_screenshot_ingest.py` 6 例；服务器实测（`rapidocr` 后端 + 合成持仓截图）：21 boxes → 3 条持仓 `平安银行 1000 股 / 贵州茅台 100 股 / 宁德时代 300 股`，`sync(同量同价) trades=0`、缺失行进 buy、`generate_initial_trades=3`，`RESULT: OK`。
  **未闭环**：IM 双向（推送 + 收图）需飞书自建应用真实凭据与权限；`bot/platforms/feishu_stream.py:413-415` 目前显式丢弃非文本消息（`message_type != "text"`），收图分支待凭据到位后接入。

- [docs(bot)] **新增 `docs/bot/ntfy-channel.md`（2026-10-27）**：把 ntfy 双向通道写成可复用的对接文档——拓扑（单 topic 对话流 / 收发分离）、硬限额（**单条 4096 字节（中文约 1300 字）**、公共实例附件 2 MB/3 h 过期、250 条/日、keepalive 45 s）、可复用代码清单（sender / stream / `--bot` / unit）、多项目两种形态（多进程零改动 vs 单进程多 topic 需 60–80 行）、以及 6 条实测排错对照（如 `Server` 字段误填整条 topic URL 导致 WS 404、confirm 丢队列、5 KB 纯文本 500）。`docs/INDEX.md` 同步登记。

- [change(deploy)] **GOT 定时改 13:20（2026-10-27）**：`dsa-got.timer` 由 `Mon..Fri 18:00` 改为 `Mon..Fri 13:20`——用户约定的是午后开盘后（约 13:30）跑，18:00 是迁移时按「盘后完整 bar」自行选的，无用户确认。**行为变化**：13:20 早于 `_is_bar_complete()` 的 15:00 阈值，当日 `bar_complete=false`，报告自动标注「价格与止损基于盘中快照，非收盘价」，P6 兑现追踪按该标记分组。仍不设 `Persistent`（避免开机补跑在任意时点用不完整 bar 出决策）。服务器实测：`NEXT = 2026-10-27 13:20 CST`。

- [change(deploy)] **ECS 改「只跑 GOT + 推送」，WebUI 下线（2026-10-24 用户裁决）**：`dsa-web`（`--serve-only`）与 `dsa-schedule`（`--schedule`）双双 `disable --now` 并删除 unit —— 后者的每日任务跑的是「选股 + 常规分析」链、**不含 GOT**，与目标不符；前端产物 `static/`（5.1 MB）与 `apps/dsa-web/node_modules/`（305 MB）一并删除（磁盘 8.7→8.4 G，可用内存 695→1193 MB）。新增 `deploy/systemd/dsa-got.{service,timer}`：`OnCalendar=Mon..Fri 18:00`、**不设 `Persistent`**（否则关机错过后会在次日开盘前补跑，用不完整 bar 出决策并推送），`Type=oneshot` 跑 `main.py --got`（`TimeoutStartSec=3600`、`MemoryMax=1000M`，为将来 `dsa-bot` 常驻预留）、`SyslogIdentifier=dsa-got`；节假日由 GOT 内部 `is_market_open()` 跳过并 exit 0，timer 无需自维护日历。配套：`SCHEDULE_ENABLED` 改回 `false`（`--schedule` 服务已下线，留着会让裸跑 `python main.py` 阻塞等 18:00）、logrotate 由 `dsa-web.conf` 更名 `dsa.conf`（GOT 确实写 `/opt/dsa/logs/stock_analysis_*.log`，旧名字误导）。**实测**：`systemctl start dsa-got.service` 于周六 15:02 触发 → journal 记录「[GOT] 2026-10-24 非交易日，跳过」，`Result=success / ExecMainStatus=0`；`systemctl list-timers` NEXT = `Mon 2026-10-26 18:00:00 CST`；8000 端口无监听、`unit` 与 `code/deploy/` 副本 diff 一致。**未闭环**：推送通道仍全是占位符（`DINGTALK_APP_KEY=xxxx`、`FEISHU_APP_ID=xxxx`、webhook 全注释），`Config.has_notification=false` → GOT 跑完**不推送**，需真实 webhook/token 才能达成「跑完即推」。

- [docs(deploy)] **新增 `deploy/`：ECS 单机部署的事实与配置（2026-10-24）**：`deploy/systemd/dsa-web.service`（`--serve-only`，MemoryMax 900M）、`deploy/systemd/dsa-schedule.service`（`--schedule`，MemoryMax 900M）、`deploy/logrotate/dsa-web.conf`，以及 `deploy/README.md`——**（注：这两个 unit 已于同日后一轮随「只跑 GOT」裁决下线删除，现存 `dsa-got.{service,timer}`，见上一条；README 已相应改写）**记录三区布局、**`/opt/dsa/code/data → /opt/dsa/data` 软链（不建则 GOT 归档/跨日记忆另起空树）**、`.env` 覆盖键、`requirements-core.txt` 生成规则、前端预构建、跑批与跑完自检、访问方式（ssh 隧道；`workbench forward` 不可用），并附 2026-10-24 15:00 CST 采集的远端现状快照（系统 / Python / 包版本 vs 工作站漂移 / 服务 / 入站可达性实测 / 数据规模），每块带复测命令。**同步边界**：`systemd/*.service` 与 `logrotate/dsa-web.conf` 已与服务器上已安装副本 diff 一致（这两类是运行配置，改动需手工 install）；`README.md` 只是文档副本，**没有自动同步机制**，改完需自行上传。

- [fix(deps)] **补齐 requirements.txt 三处未声明/不可解的硬依赖（2026-10-24，ECS 迁移实跑暴露）**：
  ① `mootdx`（0.11.x）**完全缺失**——`data_provider/mootdx_fetcher.py`、`src/got/nodes/sector_analysis.py` 等 10 处 import，是 TDX TCP 取数（`quotes.stock_all()`、板块 K 线）的唯一来源；迁移后首跑 `status=degraded`、`RuntimeError: [GOT] Node 2 板块分析失败`，编排器中止。开发机是 conda 环境手工装过，故清单里一直没有。
  ② `playwright` **完全缺失**——`src/services/ths_screener.py:116` 在 `_do_query` 内硬 import 无头浏览器且无 HTTP 降级，缺包时 `--screener` 必失败；装后还需 `python -m playwright install chromium`。
  ③ `httpx`/`tiktoken` 版本区间不可解：`mootdx` 硬钉 `httpx>=0.25,<0.26`，而 `litellm>=1.83` 声明需 `httpx>=0.28`；`tiktoken<0.12` 在 Python 3.14 无 wheel（只有 sdist → 需 Rust，`Failed building wheel for tiktoken` 会整单回滚）。改：`litellm<1.83`（锁定被验证过的 1.82.x 线）、`httpx>=0.25,<0.26`、`tiktoken>=0.8.0,<0.14,!=0.12.*`（0.12.0 即 #537 回归版）。
  **验证（Ubuntu 26.04 / Python 3.14.4 venv，阿里云 cn-shanghai ECS）**：`pip check` 无冲突；`MootdxFetcher().get_daily_data('600519', days=5)` 返回 8 根（末根 2026-10-23）；`tiktoken.get_encoding('cl100k_base')` 与 `litellm.token_counter(...)` 均正常；`litellm 1.82.6 + httpx 0.25.2` 实测可调 deepseek；完整 `--got` 重跑 exit 0（signal=cautious conf=0.81），`--screener` exit 0（问财取到 28 只）。

- [change(got/phase2)] **板块身份校验去掉规则裁剪，检索全文交 LLM 判「是否蹭概念」（2026-10-23 用户裁决）**：
  **病灶**：选股侧用的是问财 `所属同花顺概念板块是X` 精确过滤（成员来源=问财），Node 7 再用「来源白名单 + 同段共现」规则二次验成员——冷门板块名（软件服务/化工机械/财税数字）在公开文本里几乎不同句共现，实测 4 套查询模板 × 4 只票只有 1 条严格命中；3 个交易日 12 个校验对象 member 仅 2 个，10-23 四只标签（用户已确认全对）全被清空、板块维度被剔除。
  **改动**：① 删除 `is_allowed_source` 白名单与 `evidence_supports_membership` 同段护栏（`src/got/sector_evidence.py` 及单测一并删除）；② 每票 2 模板 × 可用引擎顺位的检索结果（标题+摘要+URL，每票最多 12 条）整段送 LLM；③ prompt 明确「判断是否真属于该板块 / 是否蹭概念」，串票片段（如 601778 军贸归属 688084）原样进入 prompt 由 LLM 判 `not_member`；④ 判定落地改为：`member` 保留、`not_member` 清空、`uncertain` **保留标签只留痕**（新增 `_sector_verify_kept_uncertain` 审计字段）。
  **验证**：`tests/test_verify_candidate_sectors.py` 重写为 10 条契约（全文进 prompt/URL 留痕、uncertain 保留、invalid verdict 按 uncertain、零查询 raise、601778 串票进 prompt、缓存复用、死顺位收敛）；Phase 2 相关 8 个测试文件 **189 passed**。
- [change(got/ambush)] **升格门与准入闸对齐到 0.6（2026-10-23 用户裁决）**：`_build_ambush_channel` 蓄水池升格分支与 `_deterministic_catalyst_promotion` 条件 A 原先仍用 `CATALYST_CONFIDENCE_GATE=0.7`，与 10-22 回测结论（已兑现事件按 0.6/0.65/0.7 三档：方向正确率 0.40/0.42/0.40、平均收益 +0.10/+0.15/+0.24%，Wilson95 全重叠）不一致。改为 `CATALYST_CONFIDENCE_GATE_LOW=0.6`；两档相等后「晋升影子（0.60 会放行吗）」区块恒空，一并删除。**影响（实跑 10-23 20:42 验证）**：确定性提升实际生效——`AI手机PC caution→actionable (催化1条, 池内=False)`，同一日历事件经 glue 归属到 880541，事件置信 0.65（旧 0.7 门必拦），Level 1 actionable **5→6**。蓄水池升格仍为 0：满驻留 2 天的 5 条连 0.6~0.69 事件都没有（日志无 `晋升影子` 行），另 10 条卡在 `RESERVOIR_PROMOTION_MIN_DAYS=2`。**遗留**：选股侧 P5 催化剂重排仍用 0.7（实跑影子「门槛 0.60 会多放行 3 个事件/3 板块」），本次未动。**验证**：`test_node4_ambush_events.py::test_gate_low_admits_060_narrative` 改为新契约（0.62 提升=1）+ `test_ambush_relations_wiring.py::TestPromotionGateAligned`（0.62 升格 / 0.58 仍拦）。

- [feat(got/glue)] **Node 4 事件改走 glue 归属（board_code 直连，名称扇出降级兜底，2026-10-23）**：`major_events` 原先无 `event_id`，glue relations 引用不到，只能靠 `affected_sectors` 名称扇出（`AI手机/PC` vs `AI手机PC`、`液冷散热` vs `液冷服务` 之类词表差异直接漏配）。改动：① 新增 `AggregatorNode._major_event_id(me)`（标题 MD5 前 12 位，稳定且与顺序无关），**统一三处 id 方案**——glue prompt、`_build_node4_glue_index` 回连、`_collect_pipeline_events`（后者原为 `major_{i}_{type}` 索引式，顺序变动即漂移，已替换）；② `_build_glue_prompt` 的重大事件区块补 `event_id`，并在 prompt 注明「催化事件与重大事件都可归属」；③ 新增 `_build_node4_glue_index(glue, news)` → `{board_code: [major_event]}`（过滤口径同名称索引：impact ∈ high/medium 且非负面）；④ `_build_ambush_channel` 的 Node 4 支路优先 `node4_glue_index[detail.code]`，未覆盖时回退名称索引。**验证**：`TestNode4GlueIndex` 3 例（id 稳定性 / relations 按 code 命中 / 利空+未知 id 过滤）；ambush 全量回归 **104 passed**；实跑（10-23 20:37–20:49，758s）：glue 产出 9 条 relations、其中 5 条引用 `major_*`（改前 8 条、0 条引用 Node 4 事件），日志 `Node 4×glue 归属: 9 条 relations → 5 个板块`；**铜缆高速（880523）**由 glue 关系 `智能算力增长→算力硬件互联` 归到「工信部算力规模 2592EFLOPS」事件，正式池 **0 → 1**（名称索引无「铜缆高速」桶，纯名称路径必漏）。
- [fix(got/archive)] **催化剂日历正文被 0.7 二次截断导致「有 N 个事件、正文一条不渲染」（2026-10-23 实际运行发现）**：`_write_reasoning_md` 里 `high_conf = [e for e in cal.events if e.confidence >= 0.7]` 与 `CATALYST_CONFIDENCE_GATE` 的既定口径（「低于 0.7 只进日历展示、标参考、退出评分」）冲突；10-22 起宏观日历退出（`GOT_MACRO_CALENDAR_ENABLED=false`）且 global_map 补锚定词收紧，日历只剩 narrative 事件（conf 0.50~0.60），于是标题写「5 事件」、正文全空。修：渲染全量 `cal.events`，低于 `CATALYST_CONFIDENCE_GATE` 的行标 `·参考`，未来事件行补「置信xx%」。**验证**：重渲染 10-23 归档后 5 条事件全部出现（10-27 券商 / 10-30 半导体设备 / 11-03 算力+HBM / 11-05 数据要素）。

- [fix(got/aggregator)] **蓄水池回写把「分数未达标」误标成 `no_catalyst`（2026-10-23）**：`_enrich_ambush_sector_intel` 对 `reservoir_map` 命中的板块一律写 `ambush_gate_fail="no_catalyst"`，丢弃 `AmbushReservoirEntry.reason`——10-23 15 条蓄水池里 9 条真实原因是 `near_miss:score_below_floor(0.28~0.29<0.30)`，仅 6 条无催化，读归档会误判成「埋伏板块全无事件支撑」。修：原样回写 `reservoir_map[name].reason`（空则回退 `no_catalyst`）。**验证**：`tests/test_ambush_pool_consumption.py::TestAmbushGateFailReason` 2 例（分数未达标保留原串 / 无催化仍报 no_catalyst）。

- [fix(data_provider)] **通达信 2026-09 新版行情协议适配（2026-10-23 实跑阻塞修复）**：TDX 公开行情服务器拒绝老客户端——`get_security_count`/`get_security_list`/`get_finance_info` 正常，但 K线（`0x010c`）静默回空；当日 70+ 主站（含 7 月 issue #157 标注可用节点）签名一致，Node 2 板块K线 **0/112** → 编排器 `status=degraded` 中止。修：新增 `data_provider/tdx_protocol.py` 运行时 monkeypatch（不改 site-packages，幂等）——① `TdxHq_API.setup` 换成 `0c02`(init) + `0c03`(42B tdxlevel) 两段握手；② `GetSecurityBarsCmd`/`GetIndexBarsCmd` 出包仅把版本标识（字节 2:6）`0x01016408` → `0x01007B18`，响应解析沿用 tdxpy 原实现；`mootdx_fetcher` 导入即应用。**验证**：`DataFetcherManager.get_board_kline("880597"/"881121")` 返回 2026-10-23 bar（含 `up_count`/`down_count`）；`tests/test_tdx_protocol_patch.py` 2 例（出包版本标识 + 握手载荷逐字节）。**遗留**：`get_security_quotes` 仍回空（旧版报价命令未被接受），Node 2 不依赖；回滚=删除 `mootdx_fetcher.py` 中的 `apply_tdx_protocol_patch()` 调用。

- [feat(got/catalyst)] **催化剂日历架构修正：Node 4 事件进埋伏通道 + 置信度改排序权重 + 产业级事件召回（2026-10-22，用户批准方案 `node4-ambush-events-plan`）**：
  **病灶**（归档回测，n=已兑现 `llm_narrative`+`global_map` conf≥0.6 共 401 条）：① 埋伏通道只读日历——`_build_ambush_channel` 只消费 `news.catalyst_calendar.events`，10-21 的 11 个产业级事件（稀土出口管制/厄尔尼诺/MLCC 交期 52 周/液氩涨价）对埋伏零贡献，`major_events`×当日 ambush 板块重叠率仅 **7%**，59 个归档日里正式池 >0 只有 **16 天（27%）**、均值 0.47 个/日；② 0.7 硬闸把 LLM 保守输出全杀（10-21 六条 narrative conf 0.50–0.65 全被拦，正式池 0、蓄水池大量 `no_catalyst`）；③ 三档阈值（0.6/0.65/0.7）已兑现方向正确率 **0.40/0.42/0.40**、平均收益 +0.10%/+0.15%/+0.24%，Wilson95 全部重叠——**降阈值不损失质量**（文献：自报置信度只配 ordinal 排序，不配 cardinal 硬闸）；④ `_detect_fermenting_events` 全仓零调用（唯一实现「跨日发酵」的机制从未进 prompt）；⑤ 两个用户反例（厄尔尼诺×农业、DeepInfer×算力）经查根因都是**事件没进候选池**，不是事件类型选错。
  **改动**：① `NewsEvent` 增 `catalyst_weight`（impact×sentiment 确定性映射，非概率）与 `catalyst_onset_days`（LLM 自评滞后期，预留）；`EVENT_EXTRACTION_PROMPT` 要求输出 `onset_days`。② 埋伏通道新增第二支路 `_build_node4_event_index`（按 `affected_sectors` 归一化分桶，过滤 `impact∈{high,medium}` 且非负面），候选循环内与日历同套 L1/L2 查找（`_lookup_sector_events` 泛化，不写第二套）；日历零合格事件时走 `_compute_node4_catalyst_score`（`impact_weight(high=2.0/medium=1.0) × 0.5 固定 proximity × mean(weight)`，`category_tag=fade`、`top_event_date=today`——P4 fade 分支判据是 `today > event_date`，留 None 会让该类持仓永远走不到「事件日次日卖」，且与 P6 同口径），两类都命中仍以日历算分、Node 4 仅补 `gate_notes`。③ ambush **准入**改用 `CATALYST_CONFIDENCE_GATE_LOW`（0.6）；`_deterministic_catalyst_promotion` 与 `_check_reservoir_promotion`（加重注/升格）**仍用 0.7**。④ Node 4 主催化候选补登 P6（`register_event`，`event_id=_make_event_id("node4_event","narrative",sector,date)` 幂等，`CATALYST_SOURCE_TIER` 与 `_cleanup_stale_events` 同步登记 `node4_event`；replay 零持久化副作用）。⑤ `_format_fermenting_section` 把 `_detect_fermenting_events` 的跨日发酵原始新闻拼进 narrative prompt 的「跨日发酵信号」段（appearance_count 降序、≤20 条），输出侧 D1–D4 守卫不变；**调用侧加两道边界**（真库核查：`node4_raw_news` 13,392 行恒 active、检测器命中 170 行最老 8-24、count 前 20 全是陈旧论坛长文/裸个股名）——`last_seen_date ∈ [today−3, today]`（兼作回放前视守卫）+ 标题长度 ≥8（裸实体无事件信息），170 → 6 条。⑥ 新增 `_recall_industry_candidates`：按「供给侧动作词 × 产业实体词」形态从全量 raw_news 召回短名单之外的条目（≤8 条，`source_count` 降序），带 `[产业]` 前缀并入阶段 2 细读池。
  **验证**：新增 `tests/test_node4_ambush_events.py` 15 例（入池+P4 fade 出口可达/别名 L2/负面排除/日历优先/开关回退/0.6 准入/0.58 拦截/提升仍 0.7/P6 `source=node4_event`/发酵 prompt 与开关/发酵时效与裸实体过滤/周末守卫/DeepInfer 召回 + `source_index` 回连 url/召回上限与双关键词组）；10 文件回归 **145 passed**；真库临时 SQLite 冒烟：`CATALYST_SOURCE_TIER["node4_event"]=="reported"`、`_make_event_id` 幂等、`register_event` 二次同 id 不产生重复行。回滚三开关互不依赖：`GOT_NODE4_AMBUSH_EVENTS_ENABLED=false` / `GOT_CATALYST_CONFIDENCE_GATE_LOW=0.7` / `GOT_NARRATIVE_FERMENTING_INPUT=false`（或 `GOT_FERMENTING_RECENCY_DAYS` 收紧）/ `GOT_INDUSTRY_RECALL_ENABLED=false`。判据见 `docs/got/verification.md`「2026-10-22 架构修正」。

- [feat(got/news)] **Node 4 两段式：全量标题 LLM 初筛 + 昨日漏读补读 + weight 热度项删除（2026-10-22，用户批准方案直接生效）**：
  **病灶**：Node 4 把当日采集的新闻按 `_calculate_weight` 排序后截前 50 条喂 LLM（`news_analysis.py:409` 未覆写默认 `max_items=50`），其余丢弃。10-20 实测 364 条原始新闻 → LLM 只看 ≤50 条，《DeepSeek开源推理框架DeepInfer，V4.1 Flash登顶Agent Arena开源榜》从未进入 `node4_events`，10-21 催化剂日历完全没有它。weight 的「热度」项度量媒体热度而非对 A 股的影响深度：同来源/同时效/同源数下，一条个股涨停播报 22.4 压过产业级开源发布/全球气候异常 11.6 达 **1.9 倍**。
  **改动**：① 阶段 1 把当日**全部**候选标题（无条数上限、无规则打分、无 weight 排序）一次性交 LLM 初筛（`_shortlist_titles`，≤`GOT_NEWS_TITLE_SHORTLIST_SIZE`=12），阶段 2 只对选中条目细读抽事件；粗筛失败契约=**不降级到 weight 前 N**（空响应/JSON 失败/零有效 index → 当日不产事件，宁可漏不错）。② 昨日漏读补读 `_load_unabsorbed_prev_titles`：`node4_raw_news` 中近 3 日（覆盖周末）未吸收标题**全量**前缀 `[昨日]` 回灌粗筛池，仅按标题归一化去重（`_normalize_title`）。**计划原案「`appearance_count` 降序取前 20」经验证不成立**：真库复跑该排序被多轮热榜刷高计数的娱乐/社会条目占满，DeepInfer 被挤掉（与 weight 热度项同类误判）——故删除热度排序与预截断，候选价值只交 LLM 判定；吸收判定直查 `Node4EventRecord.title`（fading 事件不重复回灌）。③ `news_feeds._HOT_KEYWORDS` 与热度项删除，weight 改为来源 40%/时效 35%/多源 25%（仅展示顺序与 NGA 保底，不决定 LLM 能否看到）。④ `_format_news_for_prompt` 默认 `max_items=None`（不截断），回滚路径显式传 `LEGACY_PROMPT_MAX_ITEMS=50`。文献：FrugalGPT(2305.05176)/Lost in the Middle(2307.03172)/Drowning in Documents(2411.11767)。
  **验证**：新增 `test_node4_title_shortlist.py` 15 例（200/200 标题覆盖、无 snippet、空响应/坏 JSON/零选中/全越界→`[]`、size 上限保持 LLM 顺序、补读排除已吸收/去重不预截断、DeepInfer 端到端回连 url、回滚键回前 50 条）+ `test_news_weight_scrub.py` 4 例；回放：涨停潮 22.2 / 英伟达财报 21.0 / DeepInfer·厄尔尼诺 14.8（同条件含/不含「涨停」均 14.8）；真库临时 SQLite 冒烟通过（`sources_seen` 为空的历史行不再让加载失败，`_load_recent_news_events` 加 `max(1, ...)` 护栏）。回滚：`GOT_NEWS_TITLE_SHORTLIST_ENABLED=false`。

- [feat(got/catalyst)] **global_map 收紧为固定事件目录 + 一次性大事件单列通道（2026-10-22）**：
  **病灶**：`GLOBAL_TO_A_SHARE_MAP` 被当成小事件收容所——10-21 日历里 SK 海力士事件的 `source_news` 是《三星8纳米再延续一代…》工艺新闻，标题却写「SK 海力士/三星财报及 HBM 指引」（13 条中仅 #3/#12 有 `anchor_keywords`，其余可被宽关键词蹭入）。
  **改动**：① 13 条全补 `event_type_fixed`（earnings/policy/product/data/macro/geo/commodity）与 `review_note`；`_map_global_events` 加 fail-closed 守卫：日期确定型（earnings/policy/data/macro）缺 `anchor_keywords` → 跳过并 warning（#2 美国 CPI-PCE / #7 台积电-ASML 财报 / #8 礼来-诺和诺德财报 共 3 条按过渡态停用，补锚定词即恢复；国内 PMI/CPI/社融/LPR 仍由 `macro_rule` 覆盖。#1 FOMC 无 macro_rule 兜底，补会议语义锚定词后标 `policy` 保留，裸提法仍被排除）。② 新增 `src/got/global_events_manual.py`：一次性大事件单列登记（必带 `occurred_date`/`a_share_linkage`/实值 `impact_level`/`confidence`，缺省 7 天有效期，不继承 map 缺省），`_manual_global_events` 在 full/incremental 两路径接线，`source=global_manual` 进 `CATALYST_SOURCE_TIER`（reported）与 `_cleanup_stale_events` 清理集合。
  **验证**：`test_global_events_manual.py` 7 例（有效期/幂等 ID/不继承 map 缺省/过期零事件/清理集合含 global_manual/确定性来源无条件清理）+ `test_catalyst_calendar_dedup.py` 新增 `TestGlobalMapGovernanceFields` 4 例（无锚财报映射→0、补锚→1、product 无锚放行、FOMC 会议锚定词保留/裸提法排除）+ `test_evidence_chain` 源枚举同步；6 文件矩阵 **79 passed**，14 文件合并回归 **170 passed**。

- [feat(got/catalyst)] **宏观日历退出（2026-10-22 用户裁决）**：PMI/CPI/社融/LPR 不再进入催化剂日历。2026-10-21 已把 macro_rule 退出评分/排序/晋级/退出入口（`CATALYST_MACRO_AS_REGIME_REF`），但事件仍留在日历与 P6；实测 `catalyst_realizations` macro 已兑现 12 条**仅 1 条 |收益|>2%**（PMI：2026-08-31 券商 +0.57%、2026-09-30 券商 −0.50%），calibration 报告 §5.1 判定 macro n=17 样本不足 → 用户裁决日历也退出。实现：新增 `GOT_MACRO_CALENDAR_ENABLED`（缺省 false）——`_generate_macro_calendar` 直接返回空（不再生成/P6 注册）、`_load_active_events` 过滤存量 `macro_rule`（增量不复活）、`_cleanup_stale_events` 纳入 `macro_rule`（全量重建删存量）。落地：删除存量 9 条 macro 行（`catalyst_events` 归零，P6 `catalyst_realizations` 历史 17 行保留）。**口径更正**：`drift_direction_correct` 对 `binary` 的判据是 `abs(actual_return)>2.0`，此前「方向正确率 1/12」的表述应读作「1/12 条波动超 2%」。**验证**：`test_catalyst_calendar_dedup` 新增 `TestMacroCalendarSwitch` 4 例（缺省关/开关恢复规则并钉 PMI=10-31 周六不平移/增量过滤存量/清理含 macro_rule），6 文件矩阵 **78 passed**、14 文件 **169 passed**。**影响面**：PMI / CPI-PPI / 社融-M2 / LPR 四类一并退出，无 per-rule 开关（LPR 虽为 ★★★，P6 实测 10-20 银行 −0.50%/券商 −0.27%、09-20 银行 −0.42%/券商 +0.36%，同样无大波动）；恢复只能整类回滚：`GOT_MACRO_CALENDAR_ENABLED=true`。

- [fix(got/catalyst)] **full 重建残留清理：确定性来源无条件清理（2026-10-21 晚实跑发现）**：`_cleanup_stale_events` 的 `produced_sources` 只清「本次有产出」的来源（2026-08-31 降级护栏）。实跑暴露边界：1021 full 重建 `global_map` 产出 0（全部被锚定词/当日无命中过滤），库里 9 条旧行（5 条「OpenAI 拟融资 300 亿美元」顶了「OpenAI/Google/Meta 发布会」、3 条「三星 8nm 工艺」顶了「SK 海力士财报」、1 条苹果供应链顶了「苹果发布会」）全部逃过清理；因 `event_date=当日`，同一交易日再跑走增量 `_load_active_events` 会把它们原样载回日历。修：`global_map`（纯规则映射）与 `global_manual`（静态人工目录）产出 0 = 当日无命中而非降级，**无条件**参与残留清理（`llm_narrative` 仍保留降级护栏）。验证：`test_global_events_manual` 新增「macro-only 事件也清 global_map/global_manual」例；用 1021 归档的 11 事件实跑清理 → 9 行清零，DB 仅剩 5 条 `macro_rule`。

- [fix(got/catalyst)] **前瞻日历剔除已过事件日记录（2026-10-21 1021 归档实测）**：`_load_active_events` 旧口径只按 `expires_at >= target_date` 过滤，把**已过事件日**的存量记录继续喂回前瞻日历——1021 归档 22 条里 4 条 event_date 早于当日（社融 10-13 ×2 / LPR 10-20 ×2，expires 10-22），且增量路径每日原样重发，读起来就是「持续传导的旧事件还在」。修：加载后按 `event_date >= target_date` 剔除并落日志（`前瞻窗口剔除已过事件日记录: n/N`）；事件日当天保留（day-0 催化），次日即落历；DB 行不动（P6 兑现追踪仍可回溯）。**口径连带**：实跑日历改为「事件日当天可见、次日落历」后，`_compute_catalyst_score` 的 past→proximity 0.6 分支在实跑不再命中（事件后漂移走 P6 `catalyst_realized` 通道），`CATALYST_DRIFT_TAIL_TRADE_DAYS` 现仅约束 `expires_at` 的库内保留期——两处注释已同步。**验证**：`test_catalyst_calendar_dedup` **25 passed**（新增 `TestForwardWindowLoad`：past/past/today/future 四行 → 只留 today+future）；真库只读复核 `_load_active_events(2026-10-21)` 由 18 → **14** 条（global 9 + macro 5），日期集 `{10-21, 10-31}`。

- [fix(got/catalyst)] **旧跟踪行不再钉死 global_map 聚合锚（新事件上不来的根因）**：`hits` 取「最早 `first_seen`」，而新闻窗口按 `last_seen` 选行——一条 10-16 的财报跟踪行（`last_seen` 10-21 仍在窗口）会让 10-22 的新财报锚定到 10-19 < `start`，整条 mapping 被 `continue` 丢弃（复现：10-16+10-22 两行 → **0 事件**；只给 10-22 → 10-23 事件）。修：聚合前加锚定可行性守卫——`min(_trade_days_offset(news_date,1), end) < start` 的旧新闻不参与「最早」竞争；纯旧新闻仍不产事件（行为不变）。**验证**：`test_catalyst_calendar_dedup` 新增 `TestGlobalMapStaleNewsDoesNotPoisonAnchor`（2 例：旧+新 → 10-23 且 `source_news` 取新行；纯旧 → 0 事件），连同 identity/macro 回归共 **117 passed**。

- [fix(got/archive)] **「历史事件传导」标签误标（2026-10-21 1021 归档实测）**：`reasoning.md` 催化剂日历段按**来源**拆「今日 vs 持续传导」——`source==global_map or category_tag==drift` 一律标 `←历史事件传导` 并注明「非今日发生」，与事件日期无关。结果：10-20 新闻 → 10-21 次日反应的 SK 海力士事件（`event_date=2026-10-21`，事件日当天）被误标为历史传导。修：按日期统一渲染「今日 N 个事件」，删除 carry 分桶与标签（日历本身已按 `event_date >= today` 过滤，事件日=今天即今日催化）；同时把 `today` 从 `date.today()` 改为 **`run_date`**——重渲染历史归档时必须锚定该归档日，否则隔天重跑会把旧归档的「未来事件」错判成过去。**验证**：用 20261021 归档 context 重渲染 `reasoning.md` → `历史事件传导` 出现 **0** 次，SK 三行落在「今日（2026-10-21）9 个事件」；用 20261020 归档重渲染 → 「今日（2026-10-20）2 个事件」= LPR×2，SK 归「未来 10-21」（修复前该日 LPR 会因 today≠run_date 从两桶同时消失）。

- [fix(got/catalyst)] **三星/海力士映射补锚定词，工艺类新闻不再伪造成 ★★★ 财报催化（2026-10-21）**：映射 #12「SK 海力士/三星财报及 HBM 指引」原先没有 `anchor_keywords`，任何含「三星」的新闻都命中——10-20 的《三星8纳米再延续一代，8LPU+年内推出》被映射成 impact 3 的财报/HBM 催化，这正是归档里读起来「无关的历史传导」的来源。修：补 `["财报","业绩","earnings","季报","指引","展望"]`（与 #3 英伟达同款，**只认财报语义**；HBM/DRAM/NAND/存储 留在 `global_keywords` 不进 anchor——否则「HBM供需缺口持续拉大…」这类产业新闻仍会伪造 ★★★ 财报催化）。**验证**：dedup 新增 `TestGlobalMapHbmAnchorGuard` 4 例（三星 8nm 工艺 → 0；HBM 供需缺口 → 0；SK海力士涨超7% → 0；「SK海力士财报发布，HBM 指引超预期」→ 10-21 单事件 impact 3；该句为测试夹具，非真实新闻）。**历史证据**：该模板在 08-20~10-21 共命中 **20 个事件日 / 55 行** P6，而带财报语义的三星/海力士新闻仅 07-29~07-31 三条（均已 archived）。

- [fix(got/sector-verify)] **板块校验误伤修复：军贸行业过滤 + 问财实际板块名透传 + 白名单扩容（2026-10-21）**：
  **三源问题**（backlog §7 #18/#19）：① 问财对「军贸概念」返回语义不稳定成分（同日两探针 30 只互斥、与生产 universe 15 只零交集），15 只里 11 只是绿电/消费票——根因之一是 `concept_industry_map` **无「军贸概念」行**，`_try_query` 的行业过滤整段 no-op；② Node 6 标签「液冷服务」在问财查询时被别名表改写为 THS 实名「液冷服务器」，但 Node 7 `_verify_candidate_sectors` 用 Node 6 原标签检索 → 检索词与真实板块名错位 → 001400/002076 被误拦（29 只成员实为 AI 服务器供应链）；③ 唯一可用 provider 是 Bocha，白名单外结果跳过 19 条，单源 `insufficient` ≠ 真实非成员。
  **① 行业过滤（#18）**：`concept_industry_map` 补 `"军贸概念": ["军工","国防","航空","航天","兵器","船舶","雷达","导弹"]`（`# ── 军工/航天 ──` 段尾）——`_try_query` 内既有过滤循环由 no-op 转生效，绿电/消费漂移票入池前被剔；过滤后为空由既有第 1.5 轮（DDE 0.5%→0.3%）+ 第 2 轮（不带 DDE）降级接管，仍空则当日该板块零票（fail-closed 可接受）。
  **② 实际板块名透传（#19）**：`_get_sector_constituents_filtered` 返回的每只 dict 增 `sector_query_board`（本次实际命中查询的 `_board_name`，healed 路径为 healed 名）→ `_extract_got_universe` universe 行 → `ScreenerStock.sector_query_board`（新字段，三处构造：主线轮次 5 / 埋伏道主路径 / fail-open）→ Node 7 校验模板改 `verify_board = getattr(stock,"sector_query_board","") or tagged_sector`，`boards` 候选把它置于首位（resolved 变体追加逻辑不动）。**Node 6 标签不动**：`sector_intel_map` 键、FXG 剔除名单、family 归因、`sector_verify_evidence[].sector` 全按标签工作——`sector_query_board` 只服务「检索词用哪个板块名」；空串（旧调用方/缓存行）逐字节回旧行为。`candidate_pool.json` universe 行同步补该键，D1 事后验证可核对标签 vs 实名差异样本。
  **③ 白名单扩容**：`EVIDENCE_SOURCE_ALLOWLIST` 12 → 15 域（`sohu.com`/`qianzhan.com`/`southmoney.com`，10-20 实测跳过 host 分布 top）。`is_allowed_source` 判定逻辑不变；**不改变单源约束**（Bocha 零结果仍判 insufficient），也**不放松身份护栏**（T1/T2/T3 逐字未改）。⚠️ 10-20 探针已摊开：那 22 条 off-list 里 21 条本就过不了护栏，本次扩容的预期收益在跨日 F10/概念成分页，不承诺对 10-20 样本提升召回。
  **验证**：`test_verify_candidate_sectors.py` **16 passed**（14 + 2：实名检索保标签 / 空串回落标签）；`test_sector_evidence.py` **12 passed**（11 + 1：扩容正例 + `evil-sohu.com`/`sohu.com.evil.cn` 后缀伪造反例）；新增 `test_junmao_industry_filter.py` **2 passed**（打桩问财返回 5 只混合行业 → 绿电两票被剔、船舶/航空三票保留；`液冷服务→液冷服务器` 写回 `sector_query_board`）；`test_family_topping_suppression.py` + `test_ambush_lane_split.py` **89 passed**（候选池快照新键断言）。端到端（真实 Bocha + LLM，001400 × 液冷服务）：`sector_query_board="液冷服务器"` → query 含实名、`sector_available=True`、evidence 引证券之星「江顺科技涨停:液冷服务器…」；对照组 `""` → query 回落标签「液冷服务」亦为 True（10-20 的 False 当日已不可复现，判据以 query 口径为准）。判据登记 [`verification.md`](got/verification.md)「2026-10-21 板块校验误伤修复」节。

- [feat(got/catalyst)] **催化剂价值分级 #1：全局事件身份重锚 + macro_rule 退出评分池（2026-10-21，用户裁决跳过影子纪律，直接生效）**：
  **三问起源**：① `event_id = MD5(source|type|sector|date)` 把日期编进 ID，新闻每日再命中同一模板（SK 海力士在「先进封装」横跨 08-20~10-19 累积 19 行 P6）→ 同一真实事件「永生」并稀释兑现样本；② PMI/社融/LPR 例行宏观无 surprise 时是否仍有催化剂价值；③ 催化剂进入/排序判据缺本地回测与文献支撑。
  **① 身份表**：新增 `catalyst_identities`（`CatalystIdentityRecord`，`identity_key=MD5('global_identity|'+global_event)`，不含日期/板块/方向），`_map_global_events` 改身份裁决——重锚判据是**陈旧度** `news_date - first_anchor_date >= CATALYST_WINDOW_DAYS`（14 日历日），**不是**「catalyst_date 是否仍在窗口」：增量路径 `start=today` 且只载 1 天新闻，窗口判据会在锚定到期次日必然重锚（新 event_id → 新 P6 行，等于没修）。同一实例内 `catalyst_date` 永不滚动、只递增 `anchor_count`，到期实例由 `catalyst_date < start` 自然落历。`_IdentityAnchorStore` 全方法吞异常降级（日历构建不因新表故障失败）。
  **② macro 降级**：`CATALYST_MACRO_AS_REGIME_REF`（`GOT_CATALYST_MACRO_AS_REGIME_REF`，缺省 true）——`source=macro_rule` 退出评分/排序/晋级入口共 6 处（aggregator 三处 `_group_events_by_sector` 入口 + screener `_ga2_gate`/`_score_catalyst_relevance` + portfolio_decision `_last_catalyst_supply`），**仍入 `catalyst_events` 与归档**（事件日 regime 参考属性另立 WI）。依据：`catalyst_realizations` 实测 macro 方向正确率 1/10 + Savor & Wilson (2013) 宏观公告溢价=事件日风险补偿而非板块方向。`_compute_ambush_exit_grace_shadow` 的影子路径刻意不排除（保持历史读数可比）。
  **③ 取证报告**：新增只读 `scripts/calibrate_catalyst_value_tier.py` → `reports/catalyst_value_tier_20261021.md/.json`（五节：身份重复度 523 identity 分布与首次/重复行对照 / 来源×类型×影响分档 / novelty 与 T-1 两项诚实 SKIP / 参数建议表）；只出证据不改生产参数。
  **验证**：`test_catalyst_identity_anchor` **10 passed**（7 身份/macro 例 + 3 calibration 纯函数例）+ `test_catalyst_calendar_dedup` 22 + `test_catalyst_calendar_stock_events` 5 + `test_catalyst_gate_forensics` 5 + `test_evidence_chain` 7；`test_node6_ambush_funnel`/`test_low_sector_rescue`/`test_ambush_lane_split`/`test_a6_wiring` **65 passed**；真库形状冒烟（临时 SQLite）四跑：首锚 1 行 identity → 次日 event_id 全等且 `catalyst_events`/`catalyst_realizations` 零新增 → 到期落历零事件且不重锚 → 超窗（>14 日）重锚 `anchor_count` 重置 1；2026-10-20 回放端到端：日历仍含 9 条 macro_rule，`_group_events_by_sector` 三次调用实收 macro_rule **0**。回滚：`GOT_CATALYST_MACRO_AS_REGIME_REF=false`（identity 锚定无开关，回退=回退代码）。判据登记 [`verification.md`](got/verification.md)「2026-10-21 补登」节，待办 [`backlog.md`](got/backlog.md) §7 #20。

- [fix(screener)] **funnel_stats.lane_merge_mode 零埋伏 finalist 日缺键（2026-10-20 e2e 实测发现）**：`_filter_got_finalists` 合并块被 `if lane_finalists:` 守卫，`lane_merge_mode` 只在 `_merge_lane_finalists` 内写入——22 只埋伏全 G-A1 flow_negative 的交易日（恰是当日）funnel_stats 永久缺该键，逐日对比口径断档。修：合并块 else 分支无条件落键（`stock_screener.py:2356`）；`test_zero_lane_finalists_still_records_merge_mode` 走真实漏斗回归。**验证**：`test_ambush_lane_split` **32 passed**（31+1）。

- [feat(got/sector-verify)] **板块身份校验换裁判：联网检索证据 + LLM 判定，efinance 退役（2026-10-20）**：
  **背景**：efinance 板块列表与问财/同花顺属两套 taxonomy，跨概念名结构性误杀（东财无「液冷服务器」「数据中心(AIDC)」「军贸概念」）。2026-10-20 三只 finalists 全被清空、`allocation=[]`，其中 001400 江顺科技经检索证实确属「液冷服务器」；同日实测军贸概念 15 只成员 11 只与该概念无关。
  **新流程**（`_verify_candidate_sectors` 整体重写）：每票 2 模板 × 2 provider 顺位检索（`days=365`，**以护栏通过为停止条件**，单票 ≤4 次）→ 财经站白名单 `is_allowed_source`（12 域名，hostname 后缀匹配）→ `evidence_supports_membership`（T1 同段绑定 / T2 指代词+标题绑定 / **T3 指名式概念列表** `核心概念 白酒概念`，段长 ≤120）→ 通过护栏的原文批量交 LLM 一次判 `member / not_member / uncertain`。
  **落地**：`member` 保留；`not_member`/`uncertain`/无证据 清空标签（Q9-B 强制 watch），reason = `llm_rejected`/`llm_uncertain`/`insufficient_evidence`。**fail-closed 保持**：响应级 LLM 失败或全部查询零结果 → `SectorVerifyUnavailable` → `status="failed"` + 退出码 1；单案例脏输出只降级该票为 uncertain；零候选直接返回；有结果但全被白名单滤掉 → insufficient（不判为检索故障）。
  **配套**：`SearchService.search_generic`（单 provider 切片、故障转移交给调用方）；`PortfolioDecision.sector_verify_evidence` 新字段 + `sector_verify_unverified` reason 枚举更新；`reasoning.md` Node 7 段新增「### 板块身份校验（web 证据）」；删除 `_verify_candidate_sectors` 内全部 efinance 调用与 L1 别名比对。
  **评审轮加固（同日）**：①`available_provider_count` 收敛死顺位——SearXNG 公共实例模式 `is_available` 只看配置意图，实例池拉不下来仍报可用，offset=1 每票白等 ~5s；现 offset≥1 整轮无结果即类级记死顺位（主顺位 0 永不停用），配好第二家自动恢复；②`(symbol, sector) → 决策` 类级缓存——FXG 基线影子双跑 `_score_candidates` 不再二次检索/二次 LLM（实测双跑 0 查询 vs 首跑 13 查询）；③白名单略过统计升级为 host 分布 top5 日志；④逐条摊开 31 条原始结果：22 条 off-list（sohu×11 行情页/qianzhan×4/askci×2 财报页/southmoney×2 题材页/chinadaily×2）**护栏全不过，扩白名单零召回收益**；唯一例外 xueqiu×1 UGC 帖过护栏但被白名单有意挡下（UGC 信任取舍，当天唯一真实召回损失）——000799 判 insufficient = Bocha 未召回 F10 概念列表页（检索源覆盖问题，当日买不进，安全侧）。
  **验证**：`test_sector_evidence` **11 passed**（T3 3 例 + 白名单 2 组）、`test_search_generic` **4 passed**、`test_verify_candidate_sectors` **14 passed**（原 11 例 + 死顺位整轮停用 + FXG 双跑决策缓存复用；含 601778 他票片段反例、白名单滤空、三态 verdict、响应级失败、全空、零候选、部分空）。

- [fix(got/sector-verify)] **板块身份校验改 fail-closed：LLM 仲裁不可用即报错，不再降级出决策（2026-10-20，用户裁决）**（裁判已于同日换为 web 检索证据，见上一条；本条仅保留 fail-closed 语义与出口契约的历史记录）：
  **裁决**：「llm不可用报错，这是决策系统，又不是应用，断了总比拿错误数据出决策好」——废止审核 3.3 的「LLM 不可用 → 清空标签继续出决策」路径（把数据缺失伪装成判定不匹配，仍在降级态产出 watch/空仓建议，痕迹只埋在 `data_quality`）。
  **落码**：新增模块级 `SectorVerifyUnavailable(RuntimeError)`；`_llm_arbitrate_sector_match` 的六类坏输入全部 raise（适配器/网络异常、返回空、无 JSON 数组、JSON 解析失败、非数组、覆盖不全缺案例），防幻觉 guard 由「丢弃命中」改「报错拒绝按坏数据裁决」；`_verify_candidate_sectors` 删除保守清空分支 → 置 `_sector_verify_degraded/_pending_count` 后原样上抛，输入 stock 不被静默修改；LLM 判「不匹配」的正常路径不变（清空标签 + `sector_available=false`）。
  **出口**：`orchestrator.run()` Phase 2 except → `logger.exception` + 落 `status="failed"` 占位 `portfolio_decision.json`（`allocation=[]`、`warnings` 载原因）→ 当日无交易建议。**且 `main.py --got` 必须以非零码收口**：新增 `_phase2_exit_code(decision)`，决策缺失或 `status=="failed"` → `return 1`（normal/partial → 0）——否则占位产物会让调度器看到"成功"，相当于把静默清空换成静默无决策。错误消息区分「LLM 不可用」与「LLM 输出不可信」（覆盖不全 / 列表外板名）。**未叠加额外重试**：适配器路由已配 `num_retries=2`（`llm_adapter.py:404/441`）。
  **未改动**：`efinance.get_belong_boards` 取不到时仍保留问财标签（同类静默信任，触发频率更高），是否一并 fail-closed 待拍板。
  **观测补丁（同轮）**：新增 `PortfolioDecision.sector_verify_unverified: [{symbol, sector, reason}]`（reason ∈ `efinance_empty`/`efinance_error`）＋日志与完成行计数——让「没验过即放行」在归档里可与「真命中」区分。动机实测：20261016 石油/石油开采/路桥三只即此路径放行，而 `data_quality` 显示 `sector_verify_degraded=null / sector_available_false_count=0`，两者同形。
  **行为证据判别实验（否定结论，同轮）**：残差相关（减 881100 基准）+ 超额同号率，3 个已知样本（601778×国防军工代理 / 000799×白酒 / 001400×液冷服务）；正负例 ρ 差仅 0.07–0.17（N=20/40 噪声带内）、001400 出现 ρ≈0 与同号率 0.70 的指标分裂、单基准残差仍含风格/市值暴露 ⇒ **不可作闸门，也不得作排序特征，只能观测**；若将来转正需风格中性化 + ≥100 带标注观测 + 预登记阈值 + walk-forward，前置条件已登记 verification.md。
  **样本源解耦（同轮）**：`candidate_pool.json` 快照不再挂在 `GOT_FAMILY_TOPPING_GATE_ENABLED` 下（原 `portfolio_decision.py:4258`），改无条件落盘（try/except、~15KB/日）——它是 D1 分道预算事后验证与板块匹配度评估的**唯一带标签样本源**，挂在无关闸门上会让「关 FTS」静默断样本且无法从归档判断断点。
  **验证**：`tests/test_verify_candidate_sectors.py` **11 passed**（新增/改写 4 例：LLM 异常 raise 且不污染输入、列表外板名 raise、覆盖不全 raise、efinance 空/异常保留标签并记 `sector_verify_unverified`）+ `tests/test_phase2_exit_code.py` **3 passed**（缺失/failed → 1，normal/partial → 0）+ `tests/test_family_topping_suppression.py` **57 passed**（快照解耦 1 例）；判据与旧/新对照表登记 [`verification.md`](got/verification.md)「板块身份校验改 fail-closed」节。

- [feat(got/lane-budget)] **D4 并集化 + D3 分道预算接线 + ACTIVE 转正 + 决策编排层输出（2026-10-20，用户裁决跳过影子纪律，直接生效）**：
  **背景三缺陷**：① D4 合并是「主线优先 + top_n 补位 + 溢出即丢」，生产 Phase 2（`top_n=10`）下主线先占满名额 → 埋伏候选整道不可见；② D3 是 `raise NotImplementedError` 骨架，「等分×ROLE_MULTIPLIER 打折」让混合道日预算蒸发（2 主线+1 埋伏 = 0.85×B、纯埋伏 3 只 = 0.55×B）；③ `GOT_AMBUSH_SPLIT_ACTIVE` 缺省 0 → lane 过闸但 `lifecycle_phase=None` 的埋伏票 `priority=50` → `watch`，永远进不了 `buy_candidates`。三者任一缺失「决策层能买到埋伏票」即不通。
  **① D4 union**：`_filter_got_finalists` 增 `lane_merge_mode`（缺省 `topn_fill` 保 `--screener` 展示入口逐字节不变），Phase 2 传 `"union"`——主线仍 Round 5 `top_n` 截断，埋伏道 `lane_finalists` 全量追加不丢（道内 `AMBUSH_LANE_TOPK=3` 兜底），`lane_overflow_n` 恒 0，撞票仍归主线；合并块抽 `_merge_lane_finalists` 供确定性单测；`lane_trace` 增 `lane_merge_mode`；生产 `unified_scoring` 上限 10→13；`candidate_pool.json` universe 行补 `sector_type` 道标签。
  **② D3 分道预算接线**：删骨架 raise，`GOT_LANE_BUDGET_ENABLED` 缺省翻 `1` 且改 **call-time 直读**（`=0` 一键回滚，无需重载）→ `per_stock_budget(c) = buy_budget × w(lane(c)) / Σ w(lane(top3))`，恒等式 `Σ = buy_budget`；选股仍取复合分降序 top3（不引入跨道单尺重排，架构边界：非 bear 禁止单尺重排）；`_stype_to_role` 从 C4 shadow 块提升为模块级（`active_mainline`→`mainline`、`ambush`→`ambush`、裸 `mainline`（schema 缺省值＝身份缺失）及其余→`unknown`；`sector_name` 为空的未验证票强制 `unknown` 保 0.55 安全垫）；数值沿用 D2 登记的 `LANE_BUDGET_PARAMS`，不引入新参数；D3 生效时 `sector_limit_detail` 补 `lane`/`lane_weight`/`lane_budget_applied` 供金额复算（**门控在开关内**：`=0` 时 detail 与 C4 trace 道归属均逐字节回旧口径）；C4 trace 道归属按实际生效道、`lane_budget_applied` 改读 call-time 开关。
  **③ ACTIVE 转正**：`GOT_AMBUSH_SPLIT_ACTIVE` 缺省 `0→1`，catalyst_config 登记值与 `portfolio_decision.py:4139` 运行时消费点两处同步（保持 call-time `os.getenv`，测试 monkeypatch 语义不变）；拦点#1 lift 与拦点#8 tech 折算生效；bear 闸豁免 2026-10-13 已无条件，不随本开关。
  **④ 决策编排层留痕**：`PortfolioDecision.trade_plan_log: list[str]` + `decision_plan.reconcile_log_lines/assemble_log_lines` 纯函数（error/空/有记录三态）+ `run()` 开头 reconcile / 末尾 assemble 两处接线回填 + `archive._append_phase2_reasoning` Node 7 段渲染「### 决策编排层（trade_plans 装配/对账）」（空列表不渲染 → off 态逐字节不变；同日重跑段落数恒 1）。
  **版本与判据**：`SCORING_FORMULA_VERSION` `2026-10-15.1`→`2026-10-20.1`（跨版本前向统计不可混池）；原 C4 四判据降级为事后验证——`scripts/backtest_lane_budget.py` lane 判定改「优先读 `sector_type` 标签、无标签回退 `sector.json` 名单」，新样本积样 ≥20 交易日/≥30 票后重跑，区分度差即 env 回滚。**纪律注记**：跳过「影子 ≥10 交易日」为用户明示裁决（2026-10-20），出处登记 [`verification.md`](got/verification.md)「2026-10-20 转正补登」节。
  **验证**：`tests/test_lane_budget_d3.py` **7 passed**（混合道全额投放 + 埋伏票入 allocation / 纯埋伏 B÷3 / 纯主线等分 / `=0` 旧路径 0.85×B / 未验证板块 unknown / 两开关缺省=1 / 映射表）；`tests/test_trade_plan_log.py` **10 passed**（纯函数三态 + 渲染幂等 + off 态无段）；`test_ambush_lane_split.py` **29 passed**（含 union 全量追加 / `topn_fill` 溢出丢弃 / 撞票计数 / ACTIVE 缺省 lift→`buy_dip` 与 `=0` 对照 / Phase 2 调传 `"union"`）；计划内九文件回归 **150 passed**（lot_rejection 的 `test_affordable_candidate_buys_whole_lots` 由 100 股改 200 股——旧断言钉在 0.55 折后预算，D3 下道权重自相抵消）。

- [fix(got/ambush-lane)] **G-A2 催化闸门双死点修复：pool fast-pass + day-0 窗口口径对齐（2026-10-16）**：
  **死因（当日两跑实测）**：埋伏道连续 0 输出（`G-A1→15 G-A2→0`），两个独立死点先后咬人——①首跑东数西算：G-A2 按日历 affected_sectors 名字级匹配，GTC 事件扇出名单无「东数西算」（board_aliases 无桥接别名），唯一 pool 供给全灭；②重跑军民融合：三要素两过（affected_sectors 直含 + conf 0.8≥0.7），但事件 dated today，G-A2 窗口谓词 `0 < days_to_event <= 14` 严格不等号剔除当日事件，而 Node 6 `_compute_catalyst_score` day-0 分支（proximity 0.6）照常入池计分——两套窗口口径分裂。
  **修法（用户拍板：窗口 + pool 放行）**：①G-A2 增设 pool fast-pass——候选板块 ∈ Node 6 `ambush_pool` 直接通过催化判定进位置安全带（`g_a2_pool_pass` 独立计数）；Node 6 入池已完成语义匹配（T5/relations）与 D4/G4 折信，道内不复算，D4 折信作用域 = pool 准入/排序不下沉道内闸门（与 P5 同口径），绝不拿折后 0.35 对原置信 0.7 gate；②窗口谓词 `0 <` → `0 <=`（含当日事件，与 Node 6 对齐）；位置安全带抽 `_ga2_position_belt` 共用（pool 通道不豁免 close/high20/ma20 三件套）。
  **接线**：pool 白名单来源 `got_context.unified_context.ambush_pool`——归档路径 `_load_got_context` 本就装载；Node 7 内存装配（portfolio_decision.py got_context dict）补 `unified_context` 键对齐。生产 `--got` 路径 lane 候选恒 ⊆ pool 成员（aggregator pool-members-only + Node 7 actionable 裁剪），pool fast-pass 构造性生效；`--screener` 回放路径 lane 候选可含非池板块，名字匹配兜底（归一化→别名变体）在该路径仍是活的，勿当死代码删除。
  **验证**：新增回归 3 用例（day-0 事件入窗 / pool fast-pass + 位置安全带不豁免 + `g_a2_pool_pass` 计数 / 无日历+空事件双分支 pool 放行）；`test_g_a2_proximity_window` 文档串同步新窗口语义；test_ambush_lane_split **23 passed**，catalyst/ambush/screener 选择集 **147 passed**（test_discord_platform collection error 为既有问题，与本次无关）；真实归档端到端回放（20261016 context + 15 只军民融合合成 lane 输入）：`G-A1→15 G-A2→15(pool_pass=15) G-A3→15 G-A4→1`，对照修复前实测 `G-A2→0`——供给恢复至 G-A4 排序闸。

- [feat(got/decision-orch)] **决策编排层 B 门五项拍板落地第一版——thesis 四态状态机 + 全账户 positions 聚合（2026-10-15）**：
  **五项裁决**（资料包 [`got/plans/决策编排层_step0_裁决资料包_20261015.md`](got/plans/%E5%86%B3%E7%AD%96%E7%BC%96%E6%8E%92%E5%B1%82_step0_%E8%A3%81%E5%86%B3%E8%B5%84%E6%96%99%E5%8C%85_20261015.md)，按推荐倾向）：**B1-b** GOT 侧 `_all_positions` helper（多账户合并视图，PortfolioService 不动）；**B2-c** 双表并存、thesis 唯一入场锚源 + 埋伏行 `catalyst_event_id` 关联回填；**B3** 四态状态机 intact/drift_watch/thesis_broken/closed（`is_open` 降级派生保留兼容）；**B4-C** 退出按角色分道只登记判据（实施挂 WI-P-7 解冻）；**B5-b** `_family_relay_match` 现状核对结案零改动。
  **B1**：`portfolio_decision.py` 四处 `accounts[0]` 错配改写（`_estimate_current_drawdown` / `_score_holdings` no_accounts 三态保留 / `_close_thesis_snapshots` / `_close_ambush_snapshots` / `_allocate` 中 4 处 positions 侧）；`total_*` 全账户读法本就正确未动；空账户 10 万虚拟兜底保留。
  **B2/B3 storage**：`HoldingThesisRecord` 新增 `lifecycle_state`（server_default intact）+ `catalyst_event_id` 两列；迁移 `_migrate_thesis_lifecycle_state`（模式同 catalyst_realizations）。**迁移踩坑**：SQLite `ALTER TABLE ADD COLUMN ... DEFAULT` 给存量行直接回填 DEFAULT 值 ⇒ `IS NULL` 谓词永不命中，改**证据冲突驱动**回填（`lifecycle_state='intact' AND (is_open=0 OR drift_watch_since IS NOT NULL)`），幂等天然成立；生产库已迁移，存量行 688598 修正回填 drift_watch。
  **B3 转换接线**：买入/同日重写=intact（`_write_thesis_snapshots`，重写分支同步清 `drift_watch_since/drift_family`——不变式 intact⟹since NULL，防重启回填翻回）；漂移观察=drift_watch（`_apply_drift_watch`）；**thesis_broken**=新 helper `_mark_thesis_broken`（`_score_holdings` 三处漂移 sell 且无宽限分支调用，判定函数 `_drift_action_with_grace` 保持纯净零写库）；清仓=closed（`_close_thesis_snapshots`）。零卖出决策路径改动；WI-P-7 冻结四条 shadow 未触碰。
  **验证**：新增 `tests/test_thesis_lifecycle_state.py` **15 用例**（迁移回填三行证据 + 单例重置幂等 + 新库建列 + 四转换路径含 `_apply_drift_watch`/`_close_thesis_snapshots` **真实方法直调**（部分减仓保持/清仓关闭）+ intact 重写清 since 不变式 + thesis_broken 不触 closed 行 + `_all_positions` 三态）；回归 test_phase2_audit_fixes(20) + test_portfolio_decision_lot_rejection(8) + 全矩阵 5 文件(113) = **156 passed 全绿**。判据登记 [`got/verification.md`](got/verification.md) 补登 #5（含 B4-C 退出分层判据表）；backlog OPEN-1 W3 修订段 + WI-P-5 行（GOT 消费侧聚合闭环、外币折算仍开放）；CHANGELOG [Unreleased] 新条目。

- [feat(got/lane-budget)] **三问裁决 Phase D：D3 门控骨架 + D4 分道预算 trace 增强 + F-1 测试污染事故处置（2026-10-15 晚）**：
  **D3 门控骨架（零决策，fail-fast）**：`GOT_LANE_BUDGET_ENABLED`（catalyst_config.py:387，缺省 `0`）→ `_allocate` 预算行前门控块（portfolio_decision.py:5205-5209），`=1` 时 `raise NotImplementedError`——分道预算接线被 D1 标定判据阻塞，O3 纪律禁止静默等权旁路；转正四判据（前向超额分箱显著区分度 + Layer 2 积样 ≥20 交易日/≥30 票 + `SCORING_FORMULA_VERSION` bump + 基线 diff 归零）登记 [`verification.md`](got/verification.md)「2026-10-15 晚间补登」节。
  **D4 trace 增强（`GOT_LANE_BUDGET_SHADOW=1` 道条目内三新键）**：`lane_budget_applied`（直读 D3 开关，恒 false 直至转正）/ `would_be_split`（screener `sector_type` 词表→`sector_role` 词表 `_stype_to_role` 映射 miss→unknown，假想预算比 = `LANE_BUDGET_PARAMS[lane]×n/Σ`，纯计算不触碰 allocation）/ `catalyst_supply_by_lane`（P5 同门槛 `CATALYST_CONFIDENCE_GATE=0.7`，供数块 `_score_candidates`→`_last_catalyst_supply` 归一化键 + 查询侧同归一化，别名变体不落空）；schemas.py `lane_budget_split_shadow` description 同步。**冒烟实证**：2 主线+1 埋伏 → `would_be_split={mainline: 0.7843, ambush: 0.2157}`（和=1）、无幽灵 `active_mainline` 条目、`catalyst_supply_by_lane={半导体: 2}` 命中。
  **F-1 测试污染事故（candidate_pool.json）**：4 个测试 harness（test_bear_entry_gate / test_meg_momentum_exhaustion_gate / test_winrate_optimization / test_family_topping_suppression FtsGate）不 chdir + stub universe 残缺形态 + `GOT_FAMILY_TOPPING_GATE_ENABLED=1` → `_score_candidates` F-1 快照分支把 stub 写进 **repo cwd 生产归档**（1013/1014/1015 三日 mtime 晚于主 run 1-5 小时实锤）；修复 = 实例级打桩 `node._save_candidate_pool_snapshot = lambda *a, **k: None`（FxgPhase2 harness / orchestrator.py:79 同款先例，不 chdir 保 `_save_shadow_records` 等写盘真实性）；污染文件删除（md5 留痕 `.review_tmp/*.bak`）。
  **⚠️ D1 数据地基结论**：candidate_pool 6 日快照全部无效（1012 旧 getattr bug 全空行 + 1013-1015 测试 stub 单行）→ **D1 回测样本源归零**，D1 报告（n=2，无统计意义）作废；样本自修复后自然积累，按 C4 判据重跑 `scripts/backtest_lane_budget.py`。
  **验证**：全矩阵 6 文件（bear_entry_gate/meg_gate/winrate/ambush_pool/fxg_pair/family_topping）**134 passed × 2 轮**，candidate_pool.json mtime 冻结（桩生效铁证）；D3 缺省态 `ast.parse` 语法检查过；D4 冒烟脚本（GOT_LANE_BUDGET_SHADOW=1 直驱 `_allocate`）四断言全过。

- [feat(got/intraday)] **Phase 6-1 盘中 0→1 确认闸门落码（2026-10-15，A6 批准，实施稿 §4 十项全落码）**：
  **裁决背景**：GOT 生产链路只有收盘后 0→1（日频），盘中无确认/否决能力；`--got-replay` 探测（Phase 6-0，[`got/plans/Phase6-0_盘中数据地基探测_20261015.md`](got/plans/Phase6-0_%E7%9B%98%E4%B8%AD%E6%95%B0%E6%8D%AE%E5%9C%B0%E5%9F%BA%E6%8E%A2%E6%B5%8B_20261015.md)）证实四接口可行性——F3 五档买量可用（mootdx `quotes()` bid/ask 列）、F2 分钟序列现全量检索无接口（需 TCP minute()）→ 实施稿 [`got/plans/Phase6-1_盘中闸门影子期实施稿_20261015.md`](got/plans/Phase6-1_%E7%9B%98%E4%B8%AD%E9%97%B8%E9%97%A8%E5%BD%B1%E5%AD%90%E6%9C%9F%E5%AE%9E%E6%96%BD%E7%A8%BF_20261015.md) 拍板三时点（0926/1125/1445）纯规则旁挂闸门（Node 8），影子期零决策消费。
  **改动面**：① `data_provider/realtime_types.py` 五档量价四字段（`bid_prices`/`ask_prices`/`bid_volumes`/`ask_volumes`，`to_dict` optional 上报）；② 数据面三源：mootdx `get_realtime_quote` 补 bid/ask_volumes + 新增 `get_minute_data`（TCP minute()）、akshare 腾讯 fields 9-28 五档解析、efinance 列名变体探测（缺列 → None 走 I1 降级），`base.py` stub + manager failover 路由；③ `schemas.py` 新增 `IntradayVerdict` / `IntradayShadowRecord`；④ `nodes/intraday_confirm.py` 三时点纯规则闸门——reject>confirm>wait 优先级、几何平均置信度、委比/高开幅/VWAP 判据，候选读 `portfolio_decision.unified_scoring` TopN，产物落 `data/got_daily/YYYYMMDD/intraday/{session}/verdicts.json` + 当日 `summary.json` 合并，零 LLM；⑤ `catalyst_config.py` 三开关 `INTRADAY_GATE_ENABLED`（缺省 false，6-2 影子运营才翻 true）/ `INTRADAY_GATE_SHADOW`（缺省 true；**⚠️ 一票否决线：6-4 转正前禁止置 false**——影子期 I1-I6 双达标 + 负责人批准才允许实盘消费，写侧另有防御性拦载兜底）/ `INTRADAY_GATE_TOPN`（缺省 8）+ 四判据阈值；⑥ `main.py` `--got-intraday --intraday-time {0926|1125|1445}`（非交易日跳过）；⑦ `archive.py` `intraday/` 归档搬运分支（防原子替换删除）+ `load_intraday_shadow_record` 回放读取；⑧ `.env.example` 开关块登记，backlog A6 行补落码修订，`verification.md` 补登 #4（I1-I6 影子期判据 + 一票否决线登记）。
  **验证**：`tests/test_intraday_confirm.py` **23 passed**（判据优先级/几何平均/降级/拦载/摘要合并）；邻接回归 17+18 passed；CLI 冒烟通过。影子期样本纪律：仅计跑满三时点的交易日（I5 收盘补跑作废/I6 缺勤日不计分母），schtasks 三时点注册待影子期启动。

- [feat(got)] **10-15 白天批——裁决轮/第二批拍板/实施轮当日落码 + #14④ jump penalty 影子先行 + §8#1 meta 记次 + 家族 WI 代码侧（2026-10-15）**：
  **A2 OPEN-2 情绪三出口落码**：`schemas.py` 删 `trading_signal`/`signal_confidence` 展示层死字段，新增 `sentiment_dispersion_state`（no_data/normal/high_consensus）；`contrarian_signal` 归并 `stage_state.contrarian_alert` 单源，`_classify_reflexivity` 收窄；11 个测试文件 kwarg 清理。
  **A3 antibias 判据**：补「仅计有买入/持有标的交易日」+ 无标的日 `skipped_reason` 观测行。
  **A5 家族 WI 代码侧**：`family_embed.py` FALLBACK 收缩至 PROMPT 12 族（两-term 打分 `cos×0.6+keyword×0.4`），V2 三 env 键登记（缺省关）。
  **A8 OPEN-D10 结项**：库内 771 行 direction 全净核验 + `param_calibration_20261015.md` 报表重算（方向准确率 39.4%，与 10-06 取证 44% 差异归因样本窗口）+ calibrate 脚本 per-source 0.0% 展示缺陷修复（新增 `_per_source_direction_accuracy()` 独立重算）。
  **#14④ regime jump penalty 影子先行**（backlog §7 #14 拍 ④，结构性删除两日确认层的影子预研）：`MarketRegime.raw_regime_score`、`aggregator.py` `_jump_argmin`/`_apply_regime_jump_model`/`_compute_regime_jump_shadow`、`SHADOW_REGIME_JUMP_ARTIFACT` 归档渲染+搬运分支、`scripts/calibrate_regime_jump_penalty.py` λ 标定（45 日回放，λ=0.25 缺省≈0.5σ，标定表六档见 verification 补登 #2）；两级开关 `GOT_REGIME_JUMP_SHADOW=1`（缺省开）/`GOT_REGIME_JUMP_PENALTY=0`（缺省关，拍板前禁置 1），影子判据 clock_start=2026-10-16（5 交易日：方向一致率 ≥80%、不低于无记忆基线、切换次数低于现行状态机、零决策红线）。
  **§8 #1 meta 记次**：`archive.py` 新增 `_read_meta_safe`/`_build_meta` runs 数组/archive_phase2 runs enrichment（phase1→full + allocation_summary + position_ratio），观测性元数据无开关。
  **A-2 宏观双开关翻 true**（本轮唯一生产改动）：`GOT_MACRO_FANOUT_ENABLED` / `GOT_MACRO_SECTOR_ALIGN_ENABLED` 缺省 true。
  **验证**：regime state 31 + family 26/12 + antibias 6 + jump/宏开关联跑 21/35/174 全绿；`verification.md` 补登 #2（jump 两级开关判据 + λ 标定表）/#3（meta 记次/家族 WI/Phase 6-0 探测）判据块登记。

- [fix(got/ambush-lane)] **G-A2 位置安全带接线（2026-10-14，backlog §1 row 52 拍板修法③）**：
  **根因**：`_build_sector_kline_lookup` 只从 `sector_context.ambush[].klines` 建缓存，但 `SectorDetail` 无 `klines` 字段 → Node 2 组装 `SectorDetail` 时 raw dict 里的 klines 被 Pydantic 静默丢弃 → 归档 ambush 条目永不携带（1009/1012/1013/1014 四日实测 `any klines = False`）→ 生产 lookup 恒 `None` → `g_a2_position_unsafe` 永不可能非零，安全带因子 soft-pass（闸门退化双因子）。抓取层无故障（`_fetch_sector_data` 90d K 线拉取/重试/data_quality 记账全正常），断点仅是 `SectorDetail` 映射出口缺失。
  **修法裁决**：三选一（①选股侧实时拉加 I/O ②schema 加 klines 字段归档膨胀+与 §1.J 删除面冲突 **③Node 2 预计算三标量·拍板**）——close/high20/ma20 注入 `indicators` 自由 dict（schema 零改动、归档体积零增长、选股路径零新增 I/O），判据留在闸门（trace 三分与 J1-J4 转正判据口径不破）。口径：high20 取 **high 列**（对齐 PLAN_v2 §166 重放校准与 `_detect_launch_signal` 惯例；high 缺失/非正回退 close），MA20 用收盘算。
  **改动**：Node 2 新增静态助手 `_compute_position_scalars(df)`（`sector_analysis.py:1399`，≥20 根产出 `{pos_close,pos_high20,pos_ma20}` round 4 位；短史/ma20 NaN/high20 非正 → 空 dict 保 soft-pass），`_analyze_single_sector` 组装前 `indicators.update(...)`；选股侧 `_build_sector_kline_lookup` 改读 indicators 三键建缓存，klines 旧路径删除。
  **生效边界**：首个生产实跑归档起 ambush 条目才携带三标量，新归档前生产仍 soft-pass；旧归档条目走 soft-pass 回放兼容。生效判据：新归档 ambush `indicators` 含三键且实跑 `pos_unsafe=` 出现非零。
  **验证**：`test_ambush_lane_split.py` 新增 `TestGA2PositionScalars` 6 例（含 failing-before 端到端：生产归档形态〔无 klines〕恒 soft-pass → 修后 `position_unsafe=1` 真拦截）；.venv 与 conda 双 **152 passed**（五套）。

- [refactor(got/node3)] **删除 `CapitalContext.confidence_multiplier`（2026-10-14，backlog §1.J 二 实施）**：
  **背景**：该字段原意为「资金面证据维度覆盖度」（可用维度/有效维度总数），但 DDE 摘除 + 北向永久豁免后 `effective_total=1`，**退化为无中间态的 1.0/0.0 二值常量**（`0.0` 只伴随 `status=failed` 整块降级、那次已在 Step 3 提前返回），且 Node 6 / Phase 2 / 选股零消费——接进 composite 只做无谓缩放，故负责人裁「删除」而非接线（覆盖 [`got/plans/regime与数据流断路_文献对照分析_20261011.md`](got/plans/regime与数据流断路_文献对照分析_20261011.md)（原根目录 `GOT_问题分析_PLAN.md`）分析二的接线建议）。**零决策面变更**（删的是本就不进决策的字段）。
  **改动面**：① `schemas.py` 删字段（原 `institutional_score` 与 `single_dimension` 之间留四行「为何不得复活」注记，防下一个会话把它当有用字段加回来）；② `capital_analysis.py` 七处：日志行去「置信度」段、failed 构造、`confidence_multiplier`/`effective_total` 计算体、normal 构造、降级 prompt、`_mock_output`；③ **`EXEMPT_DIMENSIONS` 常量一并删除**（全仓仅定义与被删分母两处引用，留着即 §9 死代码谱系；北向豁免语义由 `DIMENSION_WEIGHTS` 上方注释与「缺失不标记 fetch_failed/missing」路径继续承载）；④ **顺手拆掉一条结构性假约束**：降级 prompt 原文「置信度 {…:.0%}。若置信度≤50%，请在结论中提示……」走到该分支时恒 100%，“≤50%”永不触发（等于给 LLM 一条永不生效的假规则），改为不带假百分比的限定措辞——**仅在 `missing_dimensions` 非空的日子生效，无开关态**；⑤ **14 个测试文件 / 17 处** kwarg/dict/属性赋值清理（一次性脚本 `.review_tmp/drop_conf_mult_tests.py`：独占行整行删、折行续行只删片段，保留原文件换行/BOM；`tests/` 残留引用=0）；⑥ 文档两处：`CLAUDE.md` Node 3 输出表改列 `single_dimension`，[`got/architecture.md`](got/architecture.md) §4.1 加订正注（该段 0831 快照的「有效维度=2、confidence=available/2」已变 1 且字段已删，原文保留供追溯）。**保留 `single_dimension`**（同一降级事实的诚实表述）。
  **兼容与验收**：全量 **54 份历史 `capital.json`** 逐个 `CapitalContext(**json)` 加载全部通过、`model_dump()` 不再含该键（探针 `.review_tmp/_conf_mult_probe.py`）——`schemas.py` 无任何 `model_config`/`extra` 声明，Pydantic v2 缺省 `extra='ignore'` 使旧归档多余键静默忽略（与【埋伏计划】§3.1 #5 同条款的反向应用），`run_phase2_only.py:56` 这类从归档 dict 重建的路径同受保护；**下一生产日起 `capital.json` 形状变少一键**（属预期）。R-C3 十锚点 `verify_glue_metrics.py --date 20260910` **PASS 逐位一致**（glue 口径未触）；回归：14 个被改测试文件 **230 passed**，got/capital/sentiment 域 **448 passed / 0 failed**（3836 deselected；需 `--ignore=tests/test_discord_platform.py`（`nacl` 预存依赖问题、与本改无关）与 `-s`（本仓该域会让 pytest 捕获层报 `I/O operation on closed file` 而丢掉 summary））。`.env`/`.env.example` 无该键。**两条登记时的保守预警经实测证伪并已就地更正**：`test_node3_multiday` 的 `0.0` 用例无需改写断言（它断言的是 `strength=="missing"`，真相源为 `status`）；验器无需加「按设计 SKIP」标注（`verify_layer_b_consistency._collect_state_fields` 按显式字段清单只取 unified/sector/portfolio，从不读 capital.json）。

- [docs(got/backlog)] **§1.J 数据流断路四条改分侧处置 + 两处事实订正（2026-10-14，零代码改动）**：
  **裁决**（负责人，取代原「接线 vs 删除 vs 口径标注、四条整体结项」的打包口径）：**`confidence_multiplier` 删除**（转真·待实施）｜**`sentiment_dispersion` 先合并**（并入 §3.1 OPEN-2「情绪面信号只到展示层」同段死支路一并裁）｜**`industry_top` / `cross_node_signals` 转待分析实施**（新登 §7 **#15 / #16**，紧接 #14；§7 原无 #15，文档层的 #15 属 §8 不冲突）。
  **删除项改动面全量复核**（`tests/*.py` 逐行匹配、非截断读取）：`schemas.py:483` 必填字段 + `capital_analysis.py` 七处（`:134`/`:261`/`:277`注释/`:279`+`effective_total`/`:299`/`:604`/`:667`）+ **14 个测试文件 / 17 处** kwarg（含一处 dict 形态、一处属性赋值 `capital.confidence_multiplier = 1.0`、一处专测 `0.0` 降级语义需**改写断言**而非只删 kwarg）+ `CLAUDE.md:303`/`architecture.md:331` 两处文档。**归档兼容已核**：`schemas.py` 无任何 `model_config`/`extra` 声明 ⇒ Pydantic v2 缺省 `extra='ignore'`，旧 `capital.json` 多出的键 load 时静默忽略、读侧不炸；形状变更按裁决 #5a / `raw_position_ratio` 先例标「按设计 SKIP 不计 FAIL」。**保留 `single_dimension`**。依据：`DIMENSION_WEIGHTS` 仅 2 维且 `EXEMPT_DIMENSIONS={northbound_realtime}` ⇒ `effective_total=1`，该字段**已退化为 1.0/0.0 二值常量**（20261014 读数 `1.0` + `single_dimension=True`；`0.0` 只伴随 `status=failed` 整块降级），故本裁决**覆盖** [`got/plans/regime与数据流断路_文献对照分析_20261011.md`](got/plans/regime与数据流断路_文献对照分析_20261011.md)（原 `GOT_问题分析_PLAN.md`）分析二「接为 Step 2 乘法置信权重」的建议。
  **两处事实订正**（防按错口径开工）：① `cross_node_signals` 原记「产 11 类信号」**错**——检测函数只产 **4 类**（`CrossNodeTemporalSignal` docstring 与 `signal_type=` 字面量均为 4 个），11 类属 Phase 3b `disagreement.py` 冲突分类；② `industry_top` 原记「消费仅生产者自身」不全——`scripts/calibrate_optimal_stop.py:116`（GATE-2）已把它列入扫表名单，但主线判据要求 `state in {active_mainline}` 而该宇宙 state 恒 `neutral` ⇒ **名义被扫、实质零贡献**。
  **新落实证**（全为只读复算）：`industry_top` 54 日零缺失（今日 10 条 98–99 分、state/tier 全空）；`sentiment_dispersion` 46 个有值日全落 **0.087–0.104**、判据线 0.35 从未逼近，且 `0.0` 同时是三条无数据路径兜底值（接线前置：须先拆二义性）；`cross_node_signals` 54 日触发分布 `range_reversal 18 / 其余 3 类 0`（今日 0 条），且 `bull_confirmation` 的门 `regime=="bull" and dwell≤2` 读的是被钉死的 Node 6 regime ⇒ **与 §7 #14 串联**，不解 #14 则接线也只有一类信号可接。同轮同步 [`got/README.md`](got/README.md) 的 §7 条数索引（原记「现共 12 条」已过期 → 16 条）。**本轮零生产代码变更、零开关变动**，删除类工作仅挂号未开工。

- [fix(got/phase2)] **date.today() 双写入点贯通 as_of（2026-10-14，backlog §7 #12 拍板修法①·实例态变体）**：
  **根因**：`portfolio_decision.py` 两个归档直写点用墙钟 `date.today()` 拼 `data/got_daily/YYYYMMDD/`，不接上游 `target_date`——生产当日无差；带外跑历史日/重放 harness 时产物错落墙钟日目录。20261010 孤儿归档（周六仅 1 个 `candidate_pool.json`，生产链路周六被 `NonTradingDayError` 拦）即带外手动运行残片；1009 `candidate_pool.json` mtime 23:59:58 与引入提交 `49274d9`（10-10 00:00:55）仅差 57 秒，同一跨零点带外运行把产物劈进两个日期目录。
  **改动**（实例态变体，非加参）：`__init__` 增 `self._as_of: Optional[date] = None`；`run(as_of=...)` 头部写入 `self._as_of`（orchestrator `:254` 本就传 `as_of=target_date`，零新接口）；`_save_shadow_records` 与 `_save_candidate_pool_snapshot` 两写入点 `day_dir` 及快照 `record["date"]` 改读 `self._as_of or date.today()`。不加参理由：`_save_candidate_pool_snapshot` 调用点在 `_score_candidates` 内部（`:4052`），加参波及其签名与测试桩形参面；实例态沿 `self._last_funnel_stats`/`self._bear_gate_shadow` 既有交接先例，改动面最窄。
  **语义**：缺省兜底 `date.today()`＝旧行为逐字节不变（基线实例等未跑 `run()` 的直调安全）；生产当日 `as_of==today()` 零行为变化；带外跑历史日产物落业务日目录（正确语义）。修法②（孤儿检测告警）不在本条范围可另立；两份残片（20261010/1009）保留不删。
  **验证**：`test_fxg_veto_baseline_pair.py` 新增 `TestSaveSnapshotAsOf` 三例（None 兜底 today / 显式 `as_of=2026-10-10` 落业务日目录 + 墙钟日不产 / shadow 五件套同面 tombstone 落业务日）；conda 与 .venv 双 **104 passed**（四套：bear_entry_gate 16 / fxg_veto_baseline_pair 14 / family_topping_suppression 56 / shadow_reasoning 18）。历史归档零改动。

- [fix(got/phase2)] **bear gate 拦单留痕生产丢失修复（2026-10-14，backlog §1.H 收口，修法② tombstone + FXG 基线子块）**：
  **复核订正（1014，推翻 1012 归因）**：1012 当日 09:49 run 为**单次** `_score_candidates` 调用——10:02「命中 1 单」日志出自 **FXG veto 双跑的基线实例**（`orchestrator._maybe_capture_fxg_baseline` 内独立 `PortfolioDecisionNode`，不走 `run()/_save_shadow_records`，永不落盘），10:06 真实轮 0 命中 ⇒ 无产物。「每实例内多轮重置互相覆盖」在生产拓扑（每实例单次调用）下不存在，**修法①（跨轮 `date+symbol` 累积去重）作废**；真实盲区两条：基线实例只进日志零归档 + 0 命中日无 tombstone（缺文件三义混写：无拦单/基线轮未落盘/重跑覆盖）。1014 单次调用归档 `shadow_bear_gate.json`(hits=4) 本就是真实轮自身记录，「末轮幸存者」说法同样作废。
  **改动 A（tombstone）**：`portfolio_decision.py` 组装块删 `if _bear_gate_hits:` 门，每轮**无条件**组装汇总 dict——0 命中写 `{"executed": false, "hits": 0, "flagged_stocks": []}`；`executed` 语义改为「闸门条件是否激活」（`bool(_bear_day_gate)`，非旧恒 True）；新增 `node1_regime` 记触发口径（闸门只认 Node1 raw regime），`regime` 保留 Node6 unified 展示口径（1014 实况 Node1=bear/Node6=transition 并存属正常）。`_save_shadow_records` 早退判据改 `isinstance(self._bear_gate_shadow, dict)`、bear_gate 落盘行改「dict 必落、未组装（list）不落」⇒ 0 命中日 tombstone 覆盖 stale 旧命中文件。**改动 B（基线子块）**：`_maybe_capture_fxg_baseline` 产 `fxg_baseline_scoring.json` 时增 `bear_gate` 子块（`dict(_bg) if isinstance(_bg, dict) else None`），基线实例拦单观测随既有产物留痕、不开新文件（窄契约风格，同 F-1）。
  **判据重定**（`docs/got/verification.md` 2026-10-14 段）：`shadow_bear_gate.json` **缺文件＝当日未跑 Phase 2**；文件在＝「跑过就有产物」，0 命中日读 `hits:0`（tombstone），命中日读 `flagged_stocks`；`fxg_baseline_scoring.json` 新增 `bear_gate` 子块口径。`archive.py` `SHADOW_PROGRESS_RULES` bear_gate 行 note 同步更新；`_render_shadow_bear_gate` 渲染同步——缺文件文案改「缺失 = 当日未跑 Phase 2（或旧归档）」、tombstone 两态分句（`executed:false`→「非 bear 日闸门未激活」/ `executed:true`+`hits:0`→「bear 日闸门开启、0 拦单」）、命中行并列 Node1/Node6 双 regime。**同日重跑语义=last-writer-wins（有意，与归档覆盖惯例一致）**：末轮 tombstone 覆盖前轮命中属「早轮被取代」非「丢失」，生产每实例单次调用下无日常影响。
  **验证**：`test_bear_entry_gate.py` 新增 5 例（tombstone 组装/命中携带 flagged/双 regime 口径/stale 覆盖落盘/基线子块 ×2）+ `test_fxg_veto_baseline_pair.py` 集成冒烟补 `bear_gate` 子块端到端断言；conda 与 .venv 双 **101 passed**（四套：bear_entry_gate 16 / fxg_veto_baseline_pair 11 / family_topping_suppression 56 / shadow_reasoning 18）。历史归档 `data/got_daily/*`（1012/1014）零改动。

- [docs(got/verification)] **“缺回放对 / 两跑快照”四项集中结案（2026-10-14，零代码/零 schema 变动）**：
  **范围**：`docs/got/verification.md` §4/§5 中因验收材料缺失长期挂起的四项红线 **R1/R3**（同日两跑幂等）、**R-C7**（C 层零行为变更验收）、**R-B3/R-B8/R-B9**（B 层一致性）、**R-C6**（M2×M3 对照矩阵判读）。**取证与结论**：① **R1/R3 正式结案**——当日 A 股午休窗口 11:30-13:00 内 (11:32→12:02) 完成一次真正的同条件双跑（两跑均 `bar_complete=False`），三条铁证：`node4_events.peak 19→19` / `node4_raw_news.peak 47→47` 完全不变（幂等未坏）、`node4_events` 1774 条老事件零漂移、`node4_raw_news` 仅 2 条漂移且都正确触发跨日守卫（旁证 `last_seen='2026-10-14'` 行数差 30 = 28 新增 + 2 更新）；**R4/R5 首次给出 PASS**（分别印证 A-P0-1 “按日推进”与 backlog 裁决 #3a “按日去重”均真生效，原预计 10-11 归零已达成）；脚本机械 FAIL 读数属两跑之间新闻源动态推进、LLM 微秒波动，**不属幂等缺陷**。新增 §5.2.1 完整结案章节，§5.2 尾注补「本局限已于 10-14 由同条件双跑消除」。② **R-C7 已由 C3 行为面单测替代结案**——`tests/test_a6_wiring.py::TestC3ShadowPeriodEquivalence` 锁定 A6=false 与影子期基线逐字段一致，语义上强于“归档 diff”；C-P1…C-P10 已合入历史、前一次归档不可重生成。§4/§5 两处 R-C7 行同步。③ **R-B3/R-B8/R-B9 依 backlog §7 裁决 #6a 直接结案**——B 层修复位置在 Node 2（`family_keywords.py` + `sector_analysis.py`）而 `--got-replay` 设计上不跑 Node 2 ⇒ 回放对产不出；扩链路需可复现当日行情、历史不可复现，成本/收益不对等（裁决日期 2026-09-26、本次落笔）。零行为侧语义保护由 §5.1 防误修清单 + 单测承接。④ **R-C6 同日判读完成**：保 A 版为默认 prompt（现行不变更），结论转 C-P13 接地式架构修正。三条同方向证据：曲线非单调属真信号（不只看均值，0914 A6 转正后 B 优势已削平）、B 版有真·过度归属幻觉（分层预审实际 M3_B > 20%，现有 10 行抽不到高扇出病态事件）、0902 A 版属生成故障已整日作废（有效对照取 8 天）。**零代码变更、零 prompt 默认变更**（现行 A 版即默认），与同日已录 “M1–M6 解散、M2/M4-a 随 C-P13 休眠”裁决同方向。**本轮不改 `verify_layer_b_consistency.py` 不重跑（B 层三项已转人工归档留痕口径），`verify_same_day_idempotency.py` 仅作为结案存档保留可重跑。取证日志：`.review_tmp/_r13_run1.log` / `_r13_run2.log` / `_r13_verify.txt` / `_r13_dbdiff.txt` + 一次性只读脚本 `_r13_dbdiff.py`（不入库）。**本轮四项结案后 `docs/got/verification.md` §4/§5 已无开放项，§5 引言与实跑复算 block 同步。**

- [fix(got/phase2)] **F-1 候选池快照自上线即废数据修复（2026-10-14，backlog §7 #13 负责人拍板「dict 直修·窄契约」）**：
  **根因**：`portfolio_decision._save_candidate_pool_snapshot._row()`（原 `:3518-3529`）按对象属性口径 `getattr(s,"code","")` 取值，但生产 universe 来自 `stock_screener._extract_got_universe`（返回 `List[Dict]`，板块键为 `sector` 非 `sector_name`）⇒ `getattr` 两把都够不着 dict 键，五份归档全空：20261009(1行)/20261010(1)/20261012(53)/20261013(1)/20261014(24)（20261012 当日修订行记 61 行、现盘 53 行系同日重跑覆盖，两读数都真），真实 finalists 也恒标 `is_finalist=false`。单测 `test_family_topping_suppression.py::test_candidate_pool_snapshot` 传 `_make_stock` 对象、复刻了实现者的错误假设 ⇒ 测试恒绿掩盖生产 mismatch（「单测同病」）。
  **修复**：`_row()` 全改 dict 键口径 `s.get(...)`（板块键钉死 `s.get("sector")`）、`finalist_codes` 保持 getattr（finalists 生产形态本就是 `List[ScreenerStock]`，经 `_filter_got_finalists`）；**不写对象兜底分支**——唯一对象调用方是错误单测本身，类型二义性不固化进函数（不采纳「双口径兼容」；「统一转对象」改动面大需回归选股全链，也不采纳）；docstring 钉死输入契约（universe=List[Dict] / finalists=List[ScreenerStock]）。**单测同修（拍板硬要求）**：改传生产双形态（dict universe + ScreenerStock finalist）并加非 finalist 行 `is_finalist=false` 断言。
  **消费方更正**：快照当前**零消费方**——`scripts/fxg_pair_report.py` 只读 `fxg_baseline_scoring.json` + `portfolio_decision.json`，从不 load candidate_pool.json（本文件 FXG 条目原文「对照脚本扫三份产物」表述已就地订正）；S-4「离线复算补位来源」设计意图未落地，属预留观测件。五份废档按「不改历史文件」惯例保留为废证据；**2026-10-14 为最后污染日**，gate `GOT_FAMILY_TOPPING_GATE_ENABLED=1` 保持开，修复自下一生产 run 生效。
  **验证**：failing-before/passing-after 闭环——旧 `_row` 逻辑对 dict 逐字段复现归档废行（`symbol:""/name:""/sector_name:""/family:null/is_finalist:false` 与 20261010 归档全等）；修复后生产形态 smoke（dict universe + ScreenerStock finalist 真实落盘读回）`{"symbol":"600513","name":"联环药业","sector_name":"创新药","family":"医药","sector_score":90,"is_finalist":true}`；`tests/test_family_topping_suppression.py` **56 passed**。生产读数待下一 `--got` 实跑验证 candidate_pool.json 非空。

- [feat(got/archive)] **影子段逐线「转正进度 + 到期提示」+ 补渲染 3b 风控覆盖 / GATE-1（2026-10-14）**：
  **动机**：`reasoning.md`「🧪 影子模式观测」段此前只报当日读数、无累计进度；且 `verification.md` 在册且有转正判据的 `risk_override_shadow`（≥10 有效样本日）、`tradability_shadow`（GATE-1）两条线从未进正文（§8 #10/#13「影子线必登判据但无人渲染」同谱系）。**改动**：① 逐线追加进度行（`SHADOW_PROGRESS_RULES` + `_build_shadow_day_index` 一次扫归档得各线「有效样本日」计数），按判据分 active(≥N 数值天数) / qualitative(无固定门槛) / frozen(已冻结不积算) / gate(闸门生效非影子线) 四种措辞，到期用工作日启发式估算并显式标「仅供参考」，只报事实不作达标判定（归负责人）；进度行用短标签（MEG/FTS/FXG/3b风控/GATE-1…）不带全名，避免推理档按线名计数重复。② 新增 `_render_shadow_risk_override`（RiskOverridePlan：mode/冲突分类/decision_path_hint/veto_buy/调整计划）与 `_render_shadow_tradability`（GATE-1：mode + 被拦截/待顺延卖单，空仓无卖单如实标 0 属正常≠被证伪）；≥10 判据从「3b 分歧摘要」归位到其真正对应的「3b 风控覆盖」，分歧摘要改定性观测。**向后兼容**：不传 `base_dir/run_date`（如单测）不产出进度行、旧输出逐字节不变；纯观测、不触决策路径，`_read_json_quiet` 已对半截损坏归档降级不中断。**测试**：`test_shadow_progress_lines_emitted_and_clock_filtered`（新块内容/进度/无产物回退 + MEG 起算日过滤），`test_shadow_reasoning` 18 绿。commit `2c5052f`。

- [docs(got/verification)] **M1–M6 解散整体捆绑 + M2/M4-a 随 C-P13 休眠（2026-10-14，负责人裁决）**：
  **背景**：glue 已于 2026-09-17 A6 转正在线，六条转正红线当初「影子期能否转正」的捆绑前提已消失；且 C-P13 接地管线 2026-09-28 阶段2 收口已因 realized 收益率证伪转入休眠、无既定解冻。**裁决（分流，非一刀切休眠）**：① **M3** 已结案（10-06）→ 转日常预警；② **M1 / M5 / M6** → 降为「已上线层水位观测」继续（`verify_glue_metrics.py` 每日零成本读数、不产 PASS/FAIL、不门禁），红线重标与收口时机归负责人；③ **M2 + M4-a** → 随 C-P13 一并休眠——两者修正/重标定的结构解都挂在接地管线（M2 的 variant_b 只是把「覆盖不足」转嫁成 M3「低置信编造」，不算独立杠杆；M4-a 红线 0.6 的 τ 已被收益率口径证伪关闭），解冻绑定 `archive/cp13_phase2_closeout_20260928.md` §6，休眠期读数留存但不推进达标、不作转正依据。**纯文档登记，零代码/零开关改动**：`docs/got/verification.md` §3 加「10-14 裁决」块并改 M1/M2/M4-a 三行状态列；`docs/got/backlog.md` OPEN-C6 追加裁决尾注。**未动**：R-C3 锚点复算口径、`verify_glue_metrics.py` 计算逻辑（本就对 M1/M6 不判 PASS/FAIL）。

- [fix(got/screener)] **埋伏道静默失效修复 — P5 块变量遮蔽 + 闸门存活链 INFO + funnel_trace 扁平化（2026-10-13）**：
  **根因（生产事故 2026-10-13）**：`_filter_got_finalists` 入口拆分埋伏票后，P5 催化剂重排块（旧 L2133）重新赋值同名变量 `ambush_candidates = [c for c in candidates if sector_type=="ambush"]`——此时 `candidates` 已是纯主线池 → 覆盖为 `[]` → `if GOT_AMBUSH_SPLIT_ENABLED and ambush_candidates:` 恒假 → **埋伏道四闸门（G-A1~A4）整段永不执行**，`ambush_lane_shadow` 恒空壳 `{}`、`g_a*` trace 键不落。当日铁证：日志自相矛盾 `P5催化剂重排跳过: max_confidence=0.80 < 0.7`（0.80≥0.7，elif 被触发只因列表为空）；拆分日志 `主线 21 只, 埋伏 50 只` 在前、`ambush_lane_shadow: {}` 在后。
  **修复**：① P5 块改局部 `p5_ambush`，不再触碰外层拆分变量；elif 拆双分支——`p5_ambush` 非空→旧置信度消息，空→`P5催化剂重排跳过: 主线池无 ambush 候选（埋伏道拆分已隔离，归埋伏道四闸门）`；② `_run_ambush_lane` 返回后新增 INFO 存活链行 `埋伏道闸门: in=… G-A1→… G-A2→… G-A3→… G-A4→… (soft= flow_neg= dde_deg= no_cat= low_conf= pos_unsafe= knife= …)`，存活数由损失计数精确推导（`_a1_out = g_a1_pass+g_a1_soft_pass`；`_a2_out/_a3_out` 逐闸递减），G-A1/G-A2/G-A3 分类穷尽性已逐分支核验；③ `funnel_trace` 改**合并块之后单时点 `funnel_trace.update(lane_trace)` 扁平汇入**（形状归一化，非修复——`_compute_funnel_stats` 对 `_last_funnel_trace` 是 `dict()` 整拷贝、`funnel_stats: Optional[dict]` 无内层校验，嵌套本可透传；扁平只是让 `g_a1_*` 键与 `r0/r1` 轮次键同层可读）。**时点必须在合并块后**：`cross_lane_collision`/`lane_overflow_n` 由合并块写入 lane_trace，早扁平则 int 按值拷贝看不到后写。嵌套 `ambush_lane_trace` 键不再出现在 `meta.json → funnel_stats.round_survival_counts`（trace 完整内容仍经实例属性 `_ambush_lane_trace` 独立落 `portfolio_decision.json`）。INFO 行不带 `overflow`（合并块才写入、恒 0，且有独立溢出日志）。
  **回归**：`tests/test_ambush_lane_split.py` 新增 2 例（全链路 `_filter_got_finalists` 断言 `_run_ambush_lane` 被调用 1 次 + 单主线情形不误触发埋伏道），14 绿；**失败注入验证**：临时还原遮蔽写法 → 新回归 FAIL（call_count 0≠1）→ 还原修复 → 14 绿，failing-before/passing-after 闭环。**提交前附加**：`test_g_a2_proximity_window`/`test_g_a2_three_way_split` 暴露滚动日期漂移（测试基准写死 2026-10-13、`_ga2_gate` 内 `date.today()`，次日跑「+15 天超窗」事件恰落 14 天被吸进窗口，stash 旧码复跑同 FAIL 证与本次编辑无关）——`_ga2_gate` 加 `today: Optional[date] = None` 注入参数（默认 `date_cls.today()`，生产签名不变），两例显式传基准日期，测试从此确定性。
  **当日重放归因**（归档上下文 + 真实闸门函数，一次性脚本已删）：50 埋伏票 → G-A1 50 过（黄金概念 +9.28 正流 pass；黄金/影视音像/有色 lookup 别名命中 pass；小金属 +1.43 正流 pass——**均非负流**）→ G-A2 30 过（**影视音像 10 只 no_catalyst：唯一事件 OpenAI 发布会 event_date=2026-10-13=运行日，`0 < days_to_event` 排除同日事件**；黄金概念 10 只 no_catalyst：事件仅挂「黄金」「有色金属」，无「黄金概念」别名且 `sector_name_variants` 为空）→ G-A3 30 过（重放用中性 change/vol）→ G-A4 Top3：黄金(0.42)/有色/小金属。**本应产出 3 只埋伏道终选**。遗留待裁决：同日事件窗口语义（`0 < d <= 14` 是否应含 `d=0`）；`candidate_pool.json` 写入器键错位（空壳字段）〔已于 2026-10-14 修复拍板，见上方 F-1 条目与 backlog §7 #13〕。

- [feat(got/screener)] **埋伏道拆分 v2 — 抽道/四闸门/汇总层/开关落码 + Node 7 拦点接线（2026-10-13）**：
  **Step 0 胜率测试 PASS**（`.review_tmp/ambush_lane_winrate/`）：J1 lane Top3 T+5 超额 vs ambush 等权 +0.716%（bootstrap CI95 [0.121, 1.361], block_len=5）；J2 PASS（T+1 +0.498% > T+5−2pp）；J3 FAIL-informational（TopK 填充 17.6%，`no_catalyst` 176/218=80.7%——供给面量化读数，Layer 2 判据②校准基）；J4 PASS（sh000852 同窗读数在案）。Arm2 hard-pass-only 更强：T+5 +1.304% vs ambush_eq / +3.576% vs sh000852。`censored_days=9`, `walk_forward_skipped=sample_lt_train`。
  **落码清单**：① 抽道 `_filter_got_finalists` 入口 + 四闸门 `_ga1/_ga2/_ga3/_ga4` + 汇总层 D4 主线优先 + D1' `cross_lane_collision` + `lane_overflow_n`；② Node 7 拦点 #1 action 豁免（ACTIVE 抬 priority≥55 + `action_gate_watch`）/ #2 bear gate 扩展（`_bypass_ambush_sectors` ∪ lane 过闸板块 + `bear_gate_watch`）/ #3 `capital_gate_watch` / #4 `sector_verify_miss` / #8 composite tech 折算 `ambush_rank_score×100`（ACTIVE）/ #9 `meg_fts_overheat_triggers` / #13 `funnel_stats["ambush_lane_trace"]` / #14 顶层 `ambush_lane_shadow`；③ Schema：`UnifiedScoringResult.sector_type` default `"mainline"` + `PortfolioDecision.ambush_lane_shadow/ambush_lane_trace` Optional[dict]=None（DEVIATION：plan §3.1 #5 写 `default_factory=dict`，实装从 6 个既有 shadow 字段先例取 `Optional[dict]=None`——declaration  survives dump→load 硬要求满足）；④ 开关 `GOT_AMBUSH_SPLIT_ENABLED`（default "1"，false=A4 唯一回滚分支）+ `GOT_AMBUSH_SPLIT_ACTIVE`（default "0"，影子语义：ENABLED=1 抽道+四闸门+trace 全开但 Node 7 豁免不生效；ACTIVE=0 时 Node 7 行为与拆分前逐字节一致）。
  **拦点处置 14 项抄录表**（plan §3.1 → 实装对照）：

  | # | 处置 | trace 字段 |
  |---|---|---|
  | 1 | 分道豁免（ACTIVE 抬 priority≥55） | `action_gate_watch` |
  | 2 | 分道豁免扩展（ACTIVE 扩 `_bypass_ambush_sectors`） | `bear_gate_watch` |
  | 3 | 保持共用 | `capital_gate_watch` |
  | 4 | 保持共用 + miss 留痕 | `sector_verify_miss` |
  | 5 | Schema 声明（Optional[dict]=None，从先例） | （字段即产物） |
  | 6 | Schema 声明加 `sector_type` default `"mainline"`（兜底缺省；生产 passthrough 携带 screener 词表 `active_mainline`/`ambush` 原样不 remap，所有闸门判 `=="ambush"` 兜底值永不等价 → 语义安全；生产实测 `unified_scoring[0].sector_type="active_mainline"`） | （字段即产物） |
  | 7 | 原计划 D2/D4 已覆盖（埋伏道绕过 R1–R4） | `funnel_trace["ambush_lane"]` |
  | 8 | Step 3 折算（ACTIVE 时 `ambush_rank_score×100`） | `ambush_lane_shadow` |
  | 9 | 保持共用 + trace | `meg_fts_overheat_triggers` |
  | 10 | 不动（持仓层，与入场拆分正交） | — |
  | 11 | 写明现状 + trace | `g_a1_dde_degraded` |
  | 12 | T1 默认 OFF 保持；若开 T1 须补喂 ambush 上下文，reject 只降级 | — |
  | 13 | `funnel_stats["ambush_lane_trace"]` 直接加键（仅 `--screener` 独立模式经 `run_got_mode`→`_save_got_result` 落 `got_screener/*.json`；生产 `--got` Phase 2 绕过 `run_got_mode` 直调 `_extract_got_universe`+`_filter_got_finalists`，trace 经实例属性 `_ambush_lane_trace` 进 `portfolio_decision.json`） | （字段即产物） |
  | 14 | 顶层 `ambush_lane_shadow`（无 Pydantic 约束；同 #13，仅 `--screener` 独立模式写文件） | （字段即产物） |

  **pre-T1 caveat**：生产 15:11 终选含 T1 reject 降级（000736/怡亚通/捷成股份 → *ST中地/怡亚通/捷成股份），归档重放断言只对 pre-T1 名义终选成立（重放无 LLM 不跑 T1）。Step 0 胜率测试的 J1-J4 判据同样只对 pre-T1 名义终选口径成立。T1 开启后的 ambush 上下文补喂与 reject 语义（只降级不移除）为条件性文档项（拦点 #12），未开 T1 时不涉及。
  **trace null/扁平语义注记（2026-10-13，扁平化后更新）**：`meta.funnel_stats.round_survival_counts` 内不再有 `ambush_lane_trace` 键（改扁平 `g_a*` 同层键）；`lane_in` 键存在 = 埋伏道跑过，**整段 lane 键缺席** = ENABLED 但当日埋伏供给 0 未跑（非关闭、非异常；全 disabled 时亦缺席）。
- [修复] **问财 5 日 DDE 列名漂移 + ambush 资金面静默回退改 fail-fast（2026-10-12）**：① **根因**——问财把「近5个交易日的dde大单净量合计」在响应里物化为新键名「区间dde大单净量总和[YYYYMMDD-YYYYMMDD]」，`ths_screener.py` 的 contains 匹配找旧措辞「dde大单净量合计」永不命中 → `dde_5d_sum` 全部静默落 0.0 → `_score_capital` 的 ambush 回退分支把全埋伏板块静默踢进单日组，与当日爆发票（单日 DDE≥0.5%）同场百分位，吸筹票结构性垫底（当日实证：黄金概念 15 只全灭于轮次 2，10:01/10:04/13:58/14:01 四次运行 ambush 5 日桶均为 0；原始响应复放确认新列名为 float，单日列才带 str）。② **修复**——`ths_screener.py` 列匹配改为新旧两种措辞都认（`'dde大单净量合计' in k or '区间dde大单净量总和' in k`），实测复放 30/30 只 `dde_5d_sum` 非零；③ **契约收紧**——`stock_screener.py` `_score_capital` 静默回退（`c["dde_degraded"]=True` 无任何告警）改为 fail-fast：未打标 `dde_degraded` 的 ambush 候选缺 5 日值 = 解析与初筛口径契约破坏，直接 `raise ValueError`（对齐 A6 `GOT_A6_ENABLED=false` 的 fail-fast 先例），仅 W10 初筛降级票（第 2 轮查询天然不带 5 日条件、进评分前已打标）保留单日回退路径并新增聚合 WARNING（"N 只 ambush 候选 5 日口径缺失（初筛降级票），回退单日组: …"）。④ **测试**——`tests/test_ths_dde_5d_parse.py` 新增 5 例锁定新旧列名措辞/缺列默认 0/单日相近列名不误配（`dde大单净流入量`/`最新dde大单净额`）；`tests/test_phase2_audit_fixes.py` 2.2 节回退测试改判新契约（未打标缺值 assertRaises(ValueError) + 已打标回退 assertLogs 聚合告警），25 passed。

- [文档] **A1 #4：影子五线转正门槛分两侧定稿（2026-10-12，零代码改动）**——“影子线必登判据”四要素中的最后一项（转正门槛）自 10-11 补登以来一直空着，而影子样本每天都在积，属“先跑后写判据”。负责人裁决分两侧：**① 3b `risk_override_shadow` 定门槛（不属持仓侧，继续爬坡，到期即可测）**——有效样本日=`risk_override_shadow` 非 null 且当日 `agent_glue.status=ok`，需 ≥10 个；测法为按 **11 类冲突分类分层**抽查（每类 ≥2 条，全量归档只出现过 2 类 ⇒ 不足类用合成上下文补测并如实标注）、负责人逐条判“分类 + `decision_path_hint`”一致率 ≥80%；硬红线：零条“应买入/应减仓被判成 `veto_buy`”（enabled 后该判定会把 `buy_budget` 归零）。并明写**不得拿“与 `contradictions[]` 差异 0”当判据**（两套口径互比只说明没打架）。**② 其余四条全部冻结**（`sell_source` / `exposure_enforce` / `signal_size` / `mature_reduce`）——均为持仓/卖出侧观测，样本前提卡在 W3 多账户口径 / conf_w 未校准 / 空仓日无买入分配 / T-2 生命期标签断供，继续积天数只会得出成堆 `null` 并制造“已在观察”的假安心；去留统一归持仓文档新增的 **WI-P-7**（大改时逐条判“留/改/删”，裁决前不得开对应 enabled 开关）。取证：`risk_override_shadow` 自 0928 上线以来仅 **6 个有效样本日**（0928/0929/0930/1008/1009/1012，glue 均 ok），且 `conflict_type` 只有 2 种取值——该实情写进判据以免拿 2/11 类的样本当全量达标。同步更新 verification.md 影子五线表（逐行标冻结/已定门槛）与 10-12 实跑节、backlog §5 Phase 3b、§1.I、§8 #13（本项四要素至此齐）、`got/README.md` 第 4d/5 行。

- [代码+数据] **A1 #3：`mature_reduce_shadow` 由裸 list 改为三态自描述 dict（2026-10-12，纯观测零决策改动）**：C-B2 盘点字段原语义不可归因——`[]` 同时表示「无账户」「有账户无持仓」「有持仓但无标的落入 `14<day≤30` 窗口」，10-09/10-10 两天的零命中读数因此无法定性（与同日发现的 antibias 零产物零告警同谱系）。现改为 `{hits, holdings_seen, skipped_reason}`：`null`=本次未调 `_score_holdings`；`skipped_reason=no_accounts/no_holdings`=跑了但无可观测对象（不得读作“没有到期持仓”）；`holdings_seen>0 且 hits 空`=真读数。先 grep 确认该字段无渲染/无决策消费者（仅 `schemas.py:1325` 声明 + `portfolio_decision.py:306` 落盘）⇒ 就地改形不需双写兼容；旧归档（1008/1009 的 `[]`）与新 dict 不得对拍，已在 verification.md 五线表与 schemas 描述里声明形状变更。**接线到持仓大改文档**（负责人要求）：`plans/持仓模块重构_待分析与待实施清单_20261010.md` 新增 **WI-P-6** 与 **§六「大改返工钩子」表**——明列该三态依赖 `accounts[0].positions` 形状与成熟窗口定义，§二.1 真相源 / §二.3 退出分层 / §二.4 T-2 归属任一落地都必须同步重做观测与 verification 语义（同表还列入 WI-P-2/3 四字段与 T-1 no-op 规则的返工条件）；§二.3 加反向指针，§五 复现锚点行号随本批代码行移修正。新增 `tests/test_mature_reduce_shadow_probe.py`（5 例：三态、写入时机、schema 形状）；回归 mature_reduce + trailing_stop + phase2_audit_fixes = **41 passed**。

- [修复+文档] **A1 #2：承认 A6 无可用的运行期回滚路径，清除四处同文谎言（2026-10-12，零行为变更）**：backlog §8 #14 原本只要求“给 `GOT_A6_ENABLED` 在 `.env.example` 补一行回滚说明”，实施前核代码发现**该说明本身是假的**，负责人遂拍选 A（承认不可回滚，保留 fail-fast）。代码事实：`aggregator.py:259` 对 `not GOT_A6_ENABLED` 直接 `raise RuntimeError`（硬崩非回退）；`:1305-1306` 对 `not AGENT_SEMANTIC_GLUE_ENABLED` 同样 raise；`AGENT_SEMANTIC_GLUE_SHADOW=true` 只在 `:1432` 打日志 / `:1445` 加 `[SHADOW]` 标记，Pass 1.5 在 `:1562` 无条件调用替换（helper 内 `:1661` 的守卫被入口 raise 抢先）；又因规则侧三件套已随 A6 阶段 2（09-26）退役，**已无「影子期行为」可回**（关掉得到的是 neutral/missing 垫底退化态）。四处同文全部改为真话：`catalyst_config.py` 两处注释（:207 / :220）、`.env.example` GOT Track B 段头新增【A6 / glue 全家】登记块（实际缺的不止 A6 一个键，`AGENT_SEMANTIC_GLUE_ENABLED/_SHADOW/_IN_REPLAY` 从未在册，均注释行不改默认值）、backlog **§4.3 整节重写**（原三态表逐行对照实装）+ **§8 新增 #15**（本缺陷挂号与收口）。**防线**：`tests/test_a6_wiring.py::TestC3` docstring 原写“验证单 env 回滚即可回到影子期”亦同步更正（它直调 `_build_sector_intel` 绕开了 `run()` 守卫，所以它绿不代表可回滚——与同日 Node 4 prompt 崩溃同谱系），并新增 `tests/test_aggregator_run_smoke.py::test_run_rejects_a6_disabled` 以 run() 级 `pytest.raises` 锁住“false 即 raise”真语义（防谎言复发）。回归：test_aggregator_run_smoke + test_a6_wiring 共 **11 passed**；否决的选 B（拆 raise 重建退路）会通向中性垫底态、等于推翻 09-26 裁决，若日后真要热回滚，前置是先重建规则侧兜底。

- [文档] **A1 裁决首批：`wave_days` 历史回填作废（2026-10-12，零代码改动）**——负责人对 backlog §1.G「v3.5 波次化未落地尾巴」下裁：**② `scripts/backfill_wave_days.py` 回填不做、按作废处理**（依据与原判定一致：波次口径自 09-01 起在线累积，旧逻辑产物回填值本就不得进校准样本，且回填无任何使用方）。① NUL 幽灵行清理已于 10-10 实施（`70e4748`）⇒ **v3.5 计划至此无待实施项**，仅剩 S5 校准等埋点满足 40 个交易日（属待验证类）。同步四处落点：backlog §1.G（表头改历史性描述 + 处置行改已裁决）、`ambush_split_plan.md` §3.2 第 3 条与 v3.5 状态头（作废注记 + 日期行）、`got/README.md` 第 3 行（原「残留两项未落地」已过期）。另登记一条实施前新发现（待负责人选处理方向，本批未改代码）：`catalyst_config.py:207` 声称的 A6 两条回滚路径均不成立——`GOT_A6_ENABLED=false` 在 [`aggregator.py:259`](../src/got/nodes/aggregator.py) 直接 `raise RuntimeError`（不是回退而是硬崩），`AGENT_SEMANTIC_GLUE_SHADOW=true` 只在 `:1432` 打日志/加标签而不拦 Pass 1.5 消费（`:1562` 无条件调用，helper 内的 `:1661` 守卫被入口 raise 抢先）；而规则三件套已随 A6 阶段 2 退役 ⇒ `A6=false` 能回到的不是「影子期」而是三件套中性垫底的退化态，即**已无「影子期行为」可回**。测试 `test_a6_wiring.py::TestC3` 绿是因为它直调 `_build_sector_intel` 绕开了 `run()` 守卫——与今日 prompt 崩溃同一谱系（承诺只在绕开入口的路径上成立）。

- [修复] **Node 4 事件提取 prompt 模板大括号转义（2026-10-12，commit `e691fac`）**：上一批（`5a4f29f` 竞品补强 A）在 `EVENT_EXTRACTION_PROMPT` 示例 JSON 里插 `{src_index_example}` 时少转义一层大括号，`str.format()` 在模板解析阶段即抛 `ValueError: Single '}' encountered in format string` ⇒ **`GOT_NEWS_EVIDENCE_GATING` 缺省关也照样崩**（off 态「prompt 逐字同改造前」的登记断言自该提交起从未成立）。10-12 盘中全流程首次实跑即在 Node 4 崩、`orchestrator` 数据路 raise ⇒ 零归档。修法为一处转义（`}}]}}` → `}}}]}}`），已验 off 态渲染与改造前逐字一致、两态示例 JSON 均可 `json.loads`；全仓 AST 扫 237 个模板常量仅此一处非法。**防线补齐**：`tests/test_news_grounding.py` 原 6 例从不执行模板 `.format()`（崩了仍全绿，与 F-1「单测不传生产口径」同谱系），新增 2 例覆盖 gated 开/关两态并断言渲染结果。本条目随文档批入库（代码与测试已在 `e691fac`）。
- [文档] **2026-10-12 全流程实跑读数订正 + 三项新挂号（零生产代码改动）**：首份含 10-09~10-11 全部新闸门的完整归档（09:49→10:06，510.1s）。① `verification.md` 顶部新增 10-12 节并就地更正四处被实测推翻的原文：三闸门时钟自 2026-10-12 起算（MEG 2 / FTS 1 / FXG 1，`shadow_fts.json` 与 `fxg_baseline_scoring.json` 首份落盘）、GATE-1 首日 `mode=shadow`（但空仓无卖单 ⇒ `quote_unavailable`/误拦截两门槛无样本）、**D-3 基线起算日 = 2026-10-12**（`meta.json.funnel_stats` 首次非空，actionable 7/零入选率 0.571）、`akshare_*` 计数 = 0（个股级事件退日历验收首次通过）、GATE-1 单测数 17→**26**（`--collect-only` 实测）。② `backlog.md`：**§1.H** 「工作流 B 拦单留痕」由 ✅ 已实施**改判真·待实施**（10:02 命中 1 单 / 10:06 命中 0 单 → `shadow_bear_gate.json` 零产物；`:4027` 每轮重置 + `:4306` 仅命中时组装 + `:3492` `or None` ⇒ 多轮场景下留痕必丢，误伤率依旧算不出）；**§1.A** F-1 补硬证据（`candidate_pool.json` `universe_size=61` + 61 行全空、真实 finalists 全标 `is_finalist=false`）；**§9 新增 #7** antibias 转正判据无「有样本」前提（无买入/持有标的时零产物零告警）。③ `got/README.md` 三行同步，其中第 8 行原记「竞品补强 A–G 零落地、四文件均不存在」为已过期断言，按实态改为一期已实施。④ `docs/AGENTS.md` 候选新规范（待负责人定）：含 prompt/schema 变更的批次合入后至少跑一次 `--got` 全流程作硬前置。

- [修复] **外网对标实施计划（2026-10-11，项一/项二/项三 3a）**：① **项二 OPEN-D10**——`direction` 字段承载价格方向与事件类型两套正交语义，致 `_check_direction` 对 drift/fade 恒判 False、约 35% 兑现样本误判；修法采「统一价格语义 + 事件类型只用 `category_tag`」：`GLOBAL_TO_A_SHARE_MAP` 13 条逐条重标、narrative prompt 枚举收窄为 positive/negative/binary、`_parse_llm_narrative` 加 drift/fade→binary 守卫、`schemas` 描述澄清；新增一次性迁移 `scripts/migrate_direction_semantics.py`（默认 dry-run，`--apply` 翻转即重算、幂等可二次修复），存量 catalyst_events 19 行 + catalyst_realizations 280 行→binary，direction_accuracy 27.9%→39.0%（+11.1pp）。② **项一 埋伏事件→板块盲区**——`MACRO_CALENDAR_RULES` 伪类目「周期/消费」从不命中 concept_set 致宏观催化恒无效，改为 Node2 真实板块名 + `_full_build` 归一化回填 affected_sectors；macro 走 T5 语义对齐与新扇出均置于 `GOT_MACRO_SECTOR_ALIGN_ENABLED` / `GOT_MACRO_FANOUT_ENABLED`（缺省关，影子先行）门控下。③ **项三 3a funnel_stats 生产接线**——Phase 2 直调不经 `run_got_mode`，D-3 候选池归零率基线 0 天；补算 funnel_stats→`PortfolioDecision.funnel_stats`→`archive_phase2` 合并进 meta.json（纯观测不进决策）。
- [新功能] **竞品经验补强计划一期（2026-10-11，按批准序 D0→D→G→A→B→C→E1→E3→F，均影子/观测先行、零决策面变化）**：D0 个股批量涨跌幅 `TencentFetcher.get_stock_quick_change`（单请求多 symbol）；D `news_pulse` 持仓异动归因（三维侦察+LLM 四段，默认关）；G `output_review` 产出前运行时自查（一期 warn，落 review.json）；A 证据链与出处（`evidence_tier`/`source_url` 入 catalyst_events + node4_raw_news url/published_at 列幂等迁移、source→tier 映射、乘数一期 confirmed==reported==1.0 零变化、Node4 三步 url 确定性回连受 `GOT_NEWS_EVIDENCE_GATING` 默认关门控、first_seen 72h 新锚定）；B `antibias` 反偏见（counter_thesis/red_line_hit 落 shadow 产物，默认开观测）；C1 `data_consistency` 双源抽样交叉验证（复用 D0、缺源诚实标注）+ C2 `data_freshness` 全局新鲜度工具（capital 改引用禁二口径）；E1 `net_preflight` 网络预检观测落 meta.json（移出事件循环）+ E3 `get_belong_boards` 当日缓存 + source_type 死枚举对齐；F `backtest_discipline` run card 强制 + block-bootstrap/walk-forward/benchmark + `SCORING_FORMULA_VERSION` 入归档。E2 回放按计划前置阻塞未开工。新增 9 个离线单测文件、影子判据登记 `docs/got/verification.md` 2026-10-11 补登节，全部默认开关符合影子先行红线。
- [代码+数据] **A-2 埋伏事件→板块映射盲区修复（2026-10-11，纯数据/词表覆盖扩张，零决策语义改动）**：
  改动 4 文件：① `data/capital_flow_aliases.json` P0-1 别名桥扩表 3→61 条（58 条 `source: a2_plan`，target 均经 20261009 sector_flow_all 宇宙逐一验证存在）；② `src/got/nodes/news_analysis.py` P0-2a 事件提取概念列表去硬编码——`concept_list_str` 从「科技成长（AI、半导体、机器人、新能源）」改为 `_build_concept_list_str()`（懒导入 `sector_analysis._CATEGORY_GROUP` 派生 60 标签，异常降级回旧文案，prompt 模板零改动）；③ `src/got/nodes/sector_analysis.py` P0-2b `CONCEPT_UNIVERSE`/`_CATEGORY_GROUP` 扩词表（consumer 补 food_daily/coal_oil/ports_transport/utilities，新增 `cyclical_ext`/`telecom_media` 两组，innovative_drug/power_grid 补词）；④ `src/got/catalyst_calendar.py` `_flatten_concept_universe` 别名 dict 增 28 对 TDX↔THS 双向别名。
  离线回放验证（20260914→20261009 共 14 归档日、209 候选、复用生产 `_run_ambush_gates`/`_lookup_flow_map`/`_supplement_from_relations`；冻结口径经脚本 `freeze_missing=True` 参数模拟，对应 `.env:888` 既有 `GOT_AMBUSH_G4_FREEZE=1`——该开关为 G4 冻结决策批预置，本批未改 `.env`）：资金流命中 184/209（**88%**，改前生产口径 94/214=44%）；逐日残余 miss 0–5 属计划声明的**硬天花板候选**（有色金属/EDA/公共交通/西藏/贵州/聚氨酯/日用化工/医疗保健等——东财宇宙内无代理板，走 `flow_data_missing` ×0.5 乘数照旧，不伪造数据，详见计划 §P0-1「硬天花板如实声明」）；词表覆盖 35/35 全命中（改前 27/35）；`relations_supplement` 补挂 matched=4/supplemented=6；G4 冻结口径模拟入池 9（无冻结 8），对照同期生产 final_pool 合计 **11** 精确吻合计划基线。⚠️ 口径注记：计划基线「182/214 + 模拟入池 17」出自 10-11 一次性脚本（未入库已灭失），214 候选数无法从现有归档复现（ambush 全集=209=unified.ambush_reservoir 合计；ambush+near_miss=335；20261010 归档不完整无 sector.json）——本回放以生产函数口径与生产入池 11 的锚点等价为准；入池 9（过闸且合格事件）与 21（查表有合格事件，未过闸）构成基线 17 的上下界括号。
  P0-3 门槛裁决（建议项，零代码）：**暂不降 `CATALYST_CONFIDENCE_GATE=0.7`**——OPEN-D3 confidence 无兑现区分度、OPEN-D10 direction 污染 35%，前置条件为 D10 修复后用 `scripts/calibrate_catalyst_confidence_gate.py` 重标定。
  回归：`test_ambush_nomination` / `test_ambush_relations_wiring` / `test_node6_ambush_funnel` / `test_catalyst_calendar_dedup` 54 passed + 相关 news/sector 测试 86 passed。
  生产路径端到端回放（`--got-replay 20261009`，别名桥扩表后）：G4 evaluated 1→11、flow_data_missing skipped 11→0、frozen_released 0→1（「有色」——硬天花板候选经冻结兜底 + ×0.5 入池，`accumulation_detail="G4冻结放行(无资金流证据)"`）、final_pool 0→1；5 个候选被真实吸筹证据正当拒绝（如电气设备净流出 12.7 亿）；代理板标注机制（`[代理板通信服务]`）在真实链路生效。回放对比 7 板块 composite 漂移系 glue LLM 非确定性（回放重跑 glue），非本批改动所致。
  剩余：下一交易日生产归档观测 `ambush_gate_funnel`（G4 evaluated 1→10+、`frozen_released` 0→正、relations `no_relations_hit` 10→降、final_pool 转正频率）。

- [代码+测试+文档] **持仓观测三件套 + 工作流 B 拦单留痕 + NUL 脏数据清理 + OPEN-C3 复核更正（2026-10-10，WI-P-1/2/3 + §1.H B-① + §1.G NUL + §3.3 OPEN-C3）**：
  ① **WI-P-1 近似 HWM 守卫**（T-1 修）：`_lifecycle_sell_signal` 加 `hwm_approx: bool = False`，追踪止盈分支判据追加 `and not hwm_approx`（approx 时追踪 no-op，硬止损优先级不变）；守卫测试 `test_approx_hwm_deep_loss_noop`（688689 口径 approx→hold + approx=False 对照→sell）。
  ② **WI-P-2/3 观测字段**：`UnifiedScoringResult` 新增 `hwm_gain`/`hwm_approx`/`entry_source`（thesis/ambush/none）与 `lifecycle_phase_source`（sector_intel/anchor/extended/None），`_score_holdings` 落盘；持仓清单三行标结项。
  ③ **工作流 B 拦单留痕**（backlog §1.H B-① 建的待实施项）：`_score_candidates` 闸门命中逐单留痕 → `shadow_bear_gate.json`（经 `_save_shadow_records` 第 5 项落盘）；`archive.py` 新增 `_render_shadow_bear_gate` 渲染线接入 `reasoning.md`「🧪 影子模式观测」段；判据登 `verification.md`「bear gate 拦单留痕产物」节。性质注记：闸门本身生效（hard/veto/soft 真实改动作），留痕为观测旁挂，无影子转正开关。**同批修复汇总自引用 bug**（组装 dict 时 `self._bear_gate_shadow` 已被覆盖致 `flagged_stocks` 恒 `[]`）。
  ④ **NUL 幽灵行清理**（§1.G）：`node2_sector_trends` 48 组合/95 脏行（08-03…08-20）——46 组合删脏行（有干净孪生）、2 组合（880856 定增股、880823 微盘股）`REPLACE(char(0),'')` 改名保留，复查 0 脏行；库已备份 `data/stock_analysis.db.bak_nul_20261010`。
  ⑤ **OPEN-C3 复核更正（简报事实链被推翻，不删分支）**：0928 简报 F3/F4 称「生产端从不写 `persistence_regime`、48 归档日全 `<absent>`」——实测错误：`sentiment_analysis.py:229` 自 03bb3d1（08-28 v2.1）起写该字段；简报测的是 `market_regime.persistence_regime`（`MarketRegime` schema 无此字段），分支实际消费 `sentiment.persistence_regime`。铁证：sentiment.json 0827/0910/1009 均 `"low"`、`range_reversal` 信号 18 个归档日（0827–0924）真实触发、monitoring.json node5 自 0923 连续 7 日 `"low"`。处置改判：**不删**（删了丢真实信号）；简报加更正头、backlog §3.3 二次更正；遗留真问题（`short_term_reversal` 无决策消费，仅 `archive.py:564` emoji 渲染）待拍板。

- [文档] **三闸门影子读数补登 + FXG 对照措辞订正（2026-10-10，接第 2 条逐条核对；零代码行为变更）**：按「影子线必登判据与实测值」口径，在 [`verification.md`](got/verification.md) 顶部三闸门节新增「**三闸门实测读数（as of 10-10）**」表：MEG 唯一可读日 20261009 评估 5 只、层2 命中 0、层3 缩仓 0 ⇒ **无正例，命中率/假阳性均不可判**（不等于门无效）；FTS `shadow_fts.json` 0 份、`family_suppression` 1008/1009 均 `None`，但 `topping_risk` 档大都在（1008=无归属 wave3、1009=医药 wave4）⇒ **部署生效后首日就有触发候选**；FXG 基线 0 份，`scripts/meg_shadow_report.py` 与 `scripts/fxg_pair_report.py` 已实跑验证工…
  ⚠️ **同时改两处错描述（负责人指出 + 本仓自查，比上一轮的盘中/收盘错更实质）**：① 上一轮我建议「FXG 零对照 ⇒ 考虑退回 shadow 攒对照再切 veto」，**该建议作废**——对照是自建的：`GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW=1` 与 veto **同一次跑内双跑**（veto 真剔池 + 独立实例再跑 FXG-off 原模式评分落 `fxg_baseline_scoring.json`），故「基线 0 份」只代表部署后尚无实跑，不是“开了却没对照”；且取向已记在案（负责人原话「丢收益总比接盘亏要好」）⇒ 已在 FXG 行写明「回退 shadow 只在看中证伪后触发，不因暂无读数提前回退」。② MEG 行补写「**影子所验的不是设计稿完整版**」（`peak_dd` / `_CONFIRM` 未实装未回测）⇒ 转正时授权范围要与实际验过的那一版对齐，不能把设计稿当成已验证对象。本轮不改任何开关值、不动任何代码。

- [文档] **工作流 A 裁决结案 + 下游归因另立分析稿（2026-10-10，零代码行为变更）**：负责人裁「**A 本身已完成，不要拆验收条款；A-2 的分析另立文档，不与旧内容混在一起**」，据此：
  ① **新建** [`plans/埋伏池归零下游拦点分析_20261010.md`](got/plans/%E5%9F%8B%E4%BC%8F%E6%B1%A0%E5%BD%92%E9%9B%B6%E4%B8%8B%E6%B8%B8%E6%8B%A6%E7%82%B9%E5%88%86%E6%9E%90_20261010.md)（分析稿·未批准实施）——改写前的实测推翻了两个既有假设：**不是 `CATALYST_CONFIDENCE_GATE=0.7` 拦的**（1009 全库 50 事件 43 条 ≥0.7，`macro_rule` 恒 1.0 / `global_map` 恒 0.8 属硬编码恒过，全天仅钠电池 1 例被拦），也不是 glue 的 confidence 在起作用（glue relations 只在名称查表落空时**补挂事件**，自报 confidence 按护栏不作数，见 `aggregator.py:3185`/`:3192`）；真拦点是**事件→板块映射覆盖盲区**：16 个候选中 **11 个整链零事件**（生产口径 `relations_supplement`：14 落空里 10 个连 relations 也未命中），两个板块宇宙不相交（`GLOBAL_TO_A_SHARE_MAP` 13 条/33 板块与当日名单精确交集仅 2 个；narrative 34 板块交集 1 个；`macro_rule` 10 条 `affected_sectors` **全为空**，L1 退化为单 `sector` 扇出）。附三条候选修法（均未拍板）与四条局限。
  ② **【入场分道总计划】只改状态头**（不动正文各节，避免新旧混排）：A 标为已结案（证据：日志 1006「冻结放行 6/7」、1009 09:11「9/16」，归档「蓄水池=15 含数据缺失 **0**」），`final_pool>0` 移出 A；B 标为已在生产生效（Node1 raw regime 0930/1008/1009 均 `bear`，命中 2–3 单/日）**但缺命中计数落盘**（`_bear_gate_hits` 只在内存与日志，`portfolio_decision.json` 无 bear 字段）；C 标为开关未开（等的是开关而非样本）。§7.2 原文未动。**（本行的 B 描述已被下方 ④ 逐条定性细化，以④为准）**
  ③ **backlog 新增 §1.H**接住两条下游待实施（事件映射覆盖盲区、B 计数落盘），并明写「资金流数据源修复后重调 G4」已不是 A 的卡点（G4 已被冻结旁路）；顺手修正 §1.G/§1.H 的注记归属（NUL 方法陷阱说明重新挂回 §1.G 尾）。索引同步：`INDEX.md` plans 段新增一行（六份→七份，本轮再→八份），`got/README.md` 新增 4c 行。零代码/零开关值改动。
  ④ **B 的验收面逐条定性（负责人：「①要修；②本质是条件不全；③那是 A-2 的问题，不在 B 这管」）**，已改写 backlog §1.H 与本计划状态头：（①）**拦单留痕未落盘＝唯一真缺口，建待实施项**（含设计草案：`_bear_gate_shadow` 随 `_save_shadow_records()` 落 `shadow_bear_gate.json`，逐单记 symbol/`from_action→to_action`/是否豁免，带 date+symbol 故可用现有前向窗口算**误伤率**——真正的验收指标；纯观测不改 `action`/`buy_budget`，实施时须同步把判据登进 verification.md）；现状实测：1001–1010 全部归档 json+md 搜「收手闸门」**0 命中**，`unified_scoring` 只存终选+持仓 6 行 ⇒ 被拦候选整行不入档（而同文件已有 `sell_source_shadow` 等六个影子字段，就是没 bear gate 的）。（②）**非 bear 日零行为变化＝条件不全不是缺陷**，不建 WI，等一个 transition/bull 交易日再验（近三日连续 bear，attack_power 0.150/0.1633/0.1735）。（③）**「埋伏被放行」从 B 的验收面移出，归 A-2 线**（埋伏池为 0 → 没有埋伏买入可豁免；单测 `test_bear_ambush_exempt` 仍是现有覆盖），`INDEX.md` / `got/README.md` 两处同步。另登记一条数据问题：`data/got_daily/20260917/portfolio_decision.json` 为**半截损坏文件**（在 `"timestamp":` 处截断，`json.load` 直接报错）——属 `_atomic_write_json` 改造前的历史遗留，任何离线复算必须容错截断否则会静默少一天。
  ⑤ **工作流 C 直接终结 + 新建持仓模块文档**（负责人：「这跟持仓模块有关系，持仓模块后续要大改，C 直接终结，以 C 作为开头另设一份待分析/待实施文档」）：新建 [`plans/持仓模块重构_待分析与待实施清单_20261010.md`](got/plans/%E6%8C%81%E4%BB%93%E6%A8%A1%E5%9D%97%E9%87%8D%E6%9E%84_%E5%BE%85%E5%88%86%E6%9E%90%E4%B8%8E%E5%BE%85%E5%AE%9E%E6%96%BD%E6%B8%85%E5%8D%95_20261010.md)，定为**持仓侧问题的唯一归集点**；开头三条即 C 的移交项：**T-1 近似 HWM 误触发**（`_batch_fetch_hwm:2368-2375` 无快照时 HWM 取 60 日窗口最高点 + 成本按 `current/(1+pnl)` 反推 ⇒ 「曾盈利≥10%」对深套票形同恒真；10-09 实测 P&L −17.6%、回撤 56.1%、反推高点收益 +176%，已过 12% 清仓线 ⇒ **开关一开就误清仓**；设计只要求「标注 `[近似HWM]`」（已实现），没要求「近似不得触发动作」）、**T-2 生命期标签断供**（持仓板块被踢出当日名单 ⇒ `lifecycle_phase=None`，`mature_reduce_shadow` 盘点因此零命中）、**T-3 `hwm_gain`/`approx` 不入档**（只能解 reasoning 文本，故 T-1 的影响面至今无法量化）。另实地量了地基与开关态：`holding_thesis_snapshots` **1 行**、`ambush_position_snapshots` **0 行**；`.env` 对持仓/卖出侧 22 个 `GOT_*` 开关**一个都没设**（全走缺省），而 `portfolio_decision.json` 已有 6 个 `*_shadow` 在产 ⇒ **买入侧三闸门在爬坡、卖出侧零转正**的不对称就是大改起点。文档内含 6 条待分析议题（真相源/入场锚/退出分层/出名单归属/近似纪律/可成交性）与 5 条 WI-P-* 待实施项（**WI-P-1 最优先**）。同步：总计划与 backlog §1.H 的 C 描述从「等开关上线」改判为「已终结，修完 WI-P-1 前不得开 `GOT_TRAILING_STOP_ENABLED`」（**上几轮我写的「C 等的是开关而非样本」属不准，已作废**），backlog 新增 §1.I 接挂 WI-P-1/2・3/4/5，`INDEX.md` / `got/README.md` 各新增一行（plans 八份）。已合入代码不回退（开关缺省关，三个观测字段继留产）；本轮零代码行为变更。

- [文档] **口径收口第二批：四处「文档与实测不符」（2026-10-10，接 `0b84f19`；零代码行为变更、零开关值改动）**——都是按文档去开工就会踩错的事：
  Ⅰ [`verification.md`](got/verification.md) 顶部三闸门「读数起算」原写“均自 2026-10-09 部署日起算”，**不成立**：扫 `data/got_daily/` 实测可数产物为 **MEG 1 天 / FTS 0 天 / FXG 0 天**（`shadow_meg.json` 仅 20261009，`shadow_fts.json` 与 `fxg_baseline_scoring.json` 至今一份都没有）。归因不是“写了不跑”：20261009 那次运行的 `meta.json` 记 `git_commit=d391345` / `run_timestamp=13:55:03`，而 FTS 合入在其后（`3947500` 15:47、前置修复 `d5bf3ca` 22:14），FXG 基线更晚（`9074d7c` 10-10 09:03）；且 **10-10 目录只有 `candidate_pool.json`**（无完整全流程归档）。⇒ “观测 ≥1 周”的计时实际从下一个全流程交易日才开始。**订正一条我自己写错的口径**：初版把 MEG 那 1 天的 `bar_complete=false`（盘中跑）当成判读障碍，**这是反的**——项目约定为盘中（约 13:30）执行，收盘跑属补跑/测试例外（`archive._is_bar_complete` docstring 自述“标记收盘后**补跑**版…与 13:30 节奏配合”；audit_20260922 Q4 裁的是“降档标记 + `bar_complete` 接 P6”，故产物侧已有 `intraday_snapshot` 与盘中快照警告）。⇒ 判读口径应为**按 `meta.bar_complete` 分组（盘中日作主口径，收盘补跑日单列不混池）**；真正残留风险不在时点而在量纲：MEG 的 `decay_2d` 当日项 `rrg.rs_momentum` 盘中为未定版值，与 §7.8.1 回测用的收盘日线不同构（属 WI-P2-8 per-source availability 射程）。
  Ⅱ backlog §5 Phase 5c 的“4.2 P1-1 分位重标 DEFER（`glue_consumed` 样本 6<10）”已过期——实测 **`glue_consumed=true` 达 11 个归档日**（0917/0918/0921/0922/0923/0924/0928/0929/0930/1008/1009）≥ 10 ⇒ **4.2 解锁可开工**；已明写它与 OPEN-C1a「glue 样本满 40 交易日」是两个窗，后者仍未满足（不顺手把 OPEN-C1a 也标成已解锁）。
  Ⅲ backlog §8 #8（**R-D2 附录 A 基线不可复现**）重标可脚本化的两项（全 52 个归档日 20260724…20261010，`.review_tmp/_rd2_rebaseline.py` 只读）：`divergent_trigger_days` = **11 日 / 154 条**且**全部落在 A6 转正后（0917 起）**⇒“规则时代 0 命中”定性不变（glue 侧对照口径 `capital_read=="low"` = 17 日 / 217 条，已明写两者不是同一个量、不得互证）；蓄水池顶格（`ambush_reservoir` ≥ `RESERVOIR_MAX_SIZE=15`）= **20 日**（0926 所记 15 日已过期），**最长连续 8 日不变**。③ `dead_signal_count` / ④ `dead_branch_count` 属代码级人工清点，**本轮不擅自给值**，仍需单独一次全量清点（归档文档 `got_layer_d_plan_0911.md` 正文不改，符合「归档不再更新」）。
  Ⅳ [`architecture.md`](got/architecture.md) 文首**时效注记刷新至 10-10**（原只到 0929）：补入场分道 A/B/C（`4be371b`）、MEG/FTS/FXG、个股级事件退出催化剂日历（净 −332 行）、WI-P1-11 基本面取数、M3 结案、批次 5 审查修复（0929 `import os` 崩溃）六档，并**列出四个已知脱节区**（Node 6 三件套已删但正文仍在描、Phase 2 买入侧闸门未入文、催化剂日历不含个股级事件、§12 开关清单滞后）。本次仍**不改 987 行正文**（重写属独立一次 WI，未拍板前只做到“读者不会被误导”）。
  **旁证一条（未写进账本，归入场分道验收线）**：`ambush_pool` 近 12 个归档日为 `0,0,0,1,0,1,1,0,0,0,0,0`——与【入场分道总计划】§七「`final_pool` 仍为 0」相互印证，G4 冻结放行开了之后池子依旧没起来。同步面：`INDEX.md` / `got/README.md` 四处同行与页脚。**验证**：五份文档相对链接自检 missing = 0；本轮未改任何 `.py`，故不重跑测试（上批 `0b84f19` 的 108 passed 仍有效）。

- [文档] **计划状态头口径不一致收口（2026-10-10，梳理待办清单时发现；零代码行为变更，仅注释文案）**：[`docs/got/plans/ambush_split_plan.md`](got/plans/ambush_split_plan.md) 是两份计划的合稿，文首 v3 已于 09-28 更正为「主体已实施」，而内嵌「GOT v3.5：波次化生命周期」（第 293 行起）仍挂「设计收口，待实施」，且 [`docs/INDEX.md`](INDEX.md) 与 [`docs/got/README.md`](got/README.md) 各行说法互相矛盾——按任一份读都会误排期。逐条代码实测后落定：
  **已实施（非标注过期）**——S2 `decide_wave_days`（`sector_analysis.py:73`）+ `_calc_wave_days`（`:4292`）+ 三列落库与幂等迁移（`storage.py:1113-1116`/`:1596-1600`）+ 埋点 `_write_wave_calibration`（`:4165`，`data/wave_calibration.jsonl` 实测 1331 行 / 22 个交易日 2026-09-01…2026-10-09）、S3 `_wave_coeff` + `WAVE_LIFECYCLE_COEFF`（`aggregator.py:125-149`，与决策 5 逐字一致）、S4 `MainlineEndurance` 全链路（`aggregator.py:4022` → `archive.py:1320` → `portfolio_decision.py:5212` → `main.py:1494`，并已长出计划外的 FTS/FXG 消费）、单测 `tests/test_wave_days.py`。
  **S5 属等时间窗而非缺实现**：`scripts/calibrate_wave_coeff.py` 已在库，首跑门槛「≥40 交易日」未满足（当前 22）。
  **两条真正未落地的计划项（新增 [backlog](got/backlog.md) §1.G 挂号）**：① §3.1 幽灵行清理脚本 `scripts/cleanup_nul_sector_names.py` 从未创建，只做成了写入侧 `strip('\x00')`（`sector_analysis.py:3989`/`:4124`），**存量实测仍在库**：`node2_sector_trends.sector_name` 脏行 95 行 / 48 个 `(code,name)` 组合，脏行日期 2026-08-03…09-01（写入侧生效后零新增），其中仅 2/48 组合的「最近一条」仍是脏名（`.review_tmp/_nul_scan2.py`/`_nul_scan3.py`，DB `mode=ro`）。⚠️ **探测本身踩到一处方法陷阱**：SQLite 的 `WHERE col LIKE '%' || x'00' || '%'` **不是**「含零字节」判据（Blob 拼接后模式等价 `'%'`），它把 `node2_sector_trends` 全表 9459/9459、乃至确定无脏行的 `news_intel.name` 212/212 全报成命中——95 这个数是改用 Python 侧逐行 `"\x00" in value` 复算出来的（绑参含 NUL 会被 sqlite3 直接拒，此类扫描只能取回值后在宿主语言判），差点把这份假读数写进账本；② §3.2 回填脚本 `scripts/backfill_wave_days.py` 从未创建，且 `sector_analysis.py` 模块头注释（`:58-60`）与 `decide_wave_days` docstring 把它写成「与在线实现共用」的既有模块——属代码注释里的死引用（同一类「写了不跑/引用不存在」缺陷谱系），已改为「`decide_wave_days` 是唯一口径，日后补回填亦须复用」的约束文案（不进任何计算路径）；backlog 对该项建议改判「不做」（波次口径自 09-01 在线累积，旧逻辑回填值本不得进样本）。
  同步面：该计划文首加合稿注记 + v3.5 状态头重写（另更正 §六 决策总账标题计数 **18 → 21**，系 0831 增补 #19–#21 后未回改）；`backlog.md` 新增 §1.G；`INDEX.md` / `got/README.md` 两处同行改写并补修订行。未动任何判定逻辑、未改开关值、未新增依赖。**验证**：`py_compile src/got/nodes/sector_analysis.py` 通过；`.venv` 下 `pytest tests/test_wave_days.py tests/test_family_topping_suppression.py tests/test_sector_state_machine.py` **108 passed**；五份改动文档的相对链接自检 missing = 0。

- [文档] **docs 目录治理整理（2026-10-10，零代码/零行为变更，纯文档结构与引用路径）**：按「现行清单 / 待实施计划 / 历史归档」三区把已漂移的目录重新收回单一真源。① **`docs/got/` 顶层只留四份清单类 + 一份待拍板简报**：八份完结过程稿（`audit_20260922`、`decision_brief_20260926`、`cp12_*`×3、`cp13_*`×3）`git mv` 入 [`docs/got/archive/`](got/archive/)，带路径的引用逐处改指（`src/got/glue_pipeline.py`、`src/got/catalyst_config.py`、`scripts/cp13_l1_smoke.py`、`tests/test_glue_pipeline.py`、`tests/test_glue_prompt_variant.py`、`docs/CHANGELOG.md`、`backlog.md`）——**仅改注释/docstring 里的文档路径，不动任何逻辑与判定**；`decision_brief_openc3_20260928.md` 因 OPEN-C3 **仍待拍板**保留在顶层。② **仓库根目录不再留 PLAN/审核/报告稿**：`埋伏冻结与持仓延续性_PLAN.md`、`回测审核_入场分道与持仓延续性_20261001.md`、`持仓板块漂移_PLAN.md`、`文档更新执行报告.md`、`ULTRA_REVIEW_20260928_batch5.md`（未入库/被 `.gitignore` 逐条忽略）与 `FIX_SUMMARY.md`（改名 `FIX_SUMMARY_sector_drift_20260918.md`）共 6 份移入 `docs/got/archive/` 并补状态头（原位置/被谁取代/不得重复实施），`.gitignore` 两条逐条忽略规则作废。③ **`竞品经验补强计划_20261006.md` 首次入库**（此前一直是 untracked——唯一整块待批准计划不在版本控制里）。④ **索引重建**：[`docs/INDEX.md`](INDEX.md) 二区拆为「清单类 / plans 六份计划 / 已归档过程稿」三段并逐份标状态与开工前置；[`docs/got/README.md`](got/README.md) 补上此前缺失的入场分道总计划与竞品补强两行、改写 architecture_optimization 为§11 实际进度、修正「§7 共 6 条开放裁决」为现有 12 条的真实拍板态；[`docs/got/archive/README.md`](got/archive/README.md) 新增「2026-10-10 新收 14 份」登记表。⑤ **三闸门判据入验收清单**：[`docs/got/verification.md`](got/verification.md) 顶部新增 MEG / FTS / FXG 三道买入侧影子闸门节（开关缺省值、影子产物 `shadow_meg.json`/`shadow_fts.json`/`fxg_baseline_scoring.json`、读数工具 `meg_shadow_report.py`/`fxg_pair_report.py`、转正门槛与**五条**已诚实标注的局限**。⑥ **防再漂移**：[`docs/AGENTS.md`](AGENTS.md) 新增「文档治理规范」7 条（计划单一落点、过程稿完结即归档、待拍板件例外、状态头强制、索引同步义务、**影子线必登判据（只写 CHANGELOG 不算登记）**、交付前死链自检）；`.qoder/plans/` 七份本地排期稿逐份加状态头（已作废/已并入/已结项/局部过期），避免后续按废稿开工。**验证**：链接自检 `.review_tmp/doc_link_check.ps1`——范围内相对链接破损 **0**（已修 `decision_brief_20260926.md` 内 3 处指向 backlog 的相对链接，补上 `../` 前缀）；其余 74 条 PATH 命中均为归档文档里的「原位置」历史叙述或 `xxx.md` 占位符，非断链。本次不改任何代码行为、不改决策、不改开关值。
  ⚠️ **代码审查后的两处审核订正**（2026-10-10，本批自查）：① 首版 `verification.md` 的 MEG 行照抄了设计稿 §7.6 的六开关表，但 `_PEAK_DD` / `_CONFIRM` **代码未实装**（`sector_analysis._compute_momentum_state` 只算 `decay_2d ≤ 阈值`，无 peak_dd OR 分支、无连续确认日数；全仓无这两个环境变量）——已改为只登记 4 个真开关，并把未实装项写进该节「读数起算与局限」第 4 条；MEG/FTS 子参同时改全名（原 `_MODE` 简写会被误读成 `..._GATE_MODE`）。② 归档区 `README.md` 与 `got/README.md` 沿用旧声明「文件内容一字未改」，与本轮给三份过程稿改链接/给六份根目录稿加状态头的事实矛盾——已改口为「正文与节号未改，但非零字节改动，并逐份列出改动面」。**另：本批一度把在途特性（`catalyst_config.GLOBAL_TO_A_SHARE_MAP` 的 `anchor_keywords` 字段）连同注释路径一起误暂存，已退回工作区（unstaged）**，它与 `catalyst_calendar.py` 的消费侧守卫属同一个未提交 WI。

- [新功能] GOT **FXG 家族级见顶上游排除闸门（off→shadow→veto，含 veto 双跑原模式基线，2026-10-09~10，负责人裁定「丢收益总比接盘亏要好」批准切 veto）**：
  **根因**：Node6 见顶示警（`endurance_band=topping_risk` / `family_suppression.aggravated`）历史上**没有任何 Phase2 选池/打分消费者**，FTS 层A `sector_exposure_limits` 是死链（全仓无执行消费者）、层B 只 `buy→watch` 改标签，且都发生在**选池之后** → 结构上解释「node6 示警、phase2 猛推」。FXG 复用 FTS 已落地的 `family_suppression` 靶子（不新造判据），在 `actionable_names` 构造阶段把见顶家族的板块**真删出选股池** → 腾出的 Top-10 坑只能在其它家族重算 → 促跨家族轮动（唯一有效杠杆）。
  **判据**：家族 `aggravated==True` 且 `days_suppressed>=N`（`GOT_FAMILY_TOPPING_EXCLUDE_MIN_DAYS`，缺省 2，0 基→实际第 3 个见顶日生效）且 `days_healthy==0`（评审 W-1：仅今日仍活跃见顶才硬剔，恢复期 latch 不剔避免踏空）；粒度=家族级（见顶家族的扩散补涨腿是后段接盘票，非该保护的新枝）；**正向证据豁免**（举证责任翻转）：成员板块 `lifecycle_phase==launch` 且 `news_check==catalyzed` 且 `capital_check==aligned` → 不剔。
  **三态**：off（缺省，`EXCLUDE_ENABLED=0`，逐字节不变）/ shadow（只留痕 `shadow_fts.json` 的 `fxg` 子块 + `candidate_pool.json`，不动池）/ veto（`actionable_names -= excluded_sectors` 真删）。减法置于 **ambush 补入之后、`filtered_mainline` 之前** + ambush bypass 循环加 veto 守卫（评审 C-2：防被剔家族经蓄水池升格旁路回流）。`_build_sector_intel_map`（纯函数零 IO）上提到 actionable 块前供豁免判据（评审 C-1）。缺省 off 逐字节不变为硬约束。
  **随附前置修复**（已提交 `d5bf3ca`，均属 FTS 正确性回归）：C-2 FTS 合成块 `try/except` 兜底不击穿 Node6；C-3 `family_name` 按 `_fts_gate_on` 门控（off 态恒 null）；env 数值 clamp（`max(1, int(...))` 防 `EXIT_HEALTHY=0` 之类打崩 `days_suppressed` 计数）。
  **veto 双跑原模式基线**（本 WI，`9074d7c`）：`GOT_FAMILY_TOPPING_EXCLUDE_BASELINE_SHADOW=1` 时，`orchestrator._maybe_capture_fxg_baseline` 在真实 `run()` 前用**独立实例**额外跑一次 FXG-off 的无状态 `_score_candidates`，落 `fxg_baseline_scoring.json`（「剔前原模式本会怎么选」，`target_date` 目录、字段按 `UnifiedScoringResult.family_name/sector_name` 映射）；**Node6 与台账/分配/影子落库仍只由真实 veto `run()` 执行一次**（评审修正：Node6 对同日重跑幂等但整管线双跑会重复产其它副作用；`_score_candidates` 独立于 `_write_thesis_snapshots`）；基线实例打桩 `_save_candidate_pool_snapshot` 不 clobber 正式 veto 版；helper 自带 `try/except` 吞尽基线侧异常、绝不 raise（评审：防纯观测崩溃被 orchestrator 外层 except 打成 `status=failed` 波及实盘）。四条件全假（任一不满足）=单跑、逐字节不变。`GOT_FAMILY_TOPPING_EXCLUDE_ENABLED` 独立于父 `MODE`，便于 FXG 单独爬坡。
  **对照**（`99d6f3a`，研究用只读）：`scripts/fxg_pair_report.py` 扫每日 `fxg_baseline_scoring.json` + `portfolio_decision.json`(veto)，主口径=家族/板块池成员差集（①见顶家族被删 ②补位者），③前向 T+5/T+10 best-effort（取不到即 N/A 不臆造）；docstring 明示局限：基线=评分阶段 vs veto=分配阶段口径有别、家族差集混入当日榜单自然漂移（「两次独立抓取」路线固有代价）。**未进 reasoning.md**（archive 不读基线文件，用户裁定不加）。reasoning.md 的 FXG 段（commit2 已有）veto 态会显示「veto，已剔池」。〔订正 2026-10-14：本条原文曾写「+ `candidate_pool.json`」，与代码不符——对照脚本从不 load candidate_pool.json，F-1 修复复核时确认快照零消费方；原文保留备考〕
  **提交**：`d5bf3ca`(前置修复)/`49274d9`(FXG 特性)/`da5d91b`(.env.example)/`9074d7c`(veto 双跑基线)/`99d6f3a`(对照脚本)/`cf5d769`(集成冒烟)。**验证**：conda `daily_stock_analysis`（Py3.10.20）全套 **107 passed**（FXG 基线 10 + FTS 56 + MEG 24 + shadow 17）；集成冒烟证明真实 `_score_candidates` 经 helper standalone 跑通、落 `fxg_baseline_scoring.json`、不产 candidate_pool、env 隔离并复原。当前 `.env`=veto+BASELINE_SHADOW=1（gitignored）；真实端到端待下一交易日生产路径（`--got` 非交易日抛错、`--got-replay` 不经过 Phase2）。**切 veto 后连续 ≥1 周用 `fxg_pair_report.py` 证真补位净正/中性方可长期保留，否则回退 shadow。**

- [修复] GOT **WI-P1-11 审核收口：`forecast_type` 选行改净利润口径（2026-10-07，由「审核对话全部实施内容」三维度评审发现（范围 `ad1506c^..HEAD`，无 Critical、3 Warning）；负责人拍「W1 修 + W2 改口 + W3 只登记」；【架构计划】§11.11 新增审核订正段 + §6 两处表述限定；backlog §7 #11 并入两个观察面；⚠️ 改生产代码，属同一 WI 的取数保真延续，不动决策语义/预算配置）**：
  **W1（准 Critical，取数保真缺陷）**：预告表是「一股多行、每行一个预测指标」（营收/归母/扣非…），旧实现 `forecast_df.iloc[0]` 拿**首个指标行**——若先命中营业收入行，「营收预增 + 净利首亏」会被标成 `forecast_type=预增`，恰好喂错 RiskAgent 预亏通路要避的信号。难堪的是**本取证自己早就算过冲突面 32.45%（audit 脚本 canonical 按净利润口径）却没应用到选行上**。修法：新增 `_pick_canonical_forecast_row`，口径与取证脚本 `PROFIT_PRIORITY` 一致（归母 > 扣非 > 净利润，首个命中即断；无净利口径行退化取首行，不静默丢数据），`get_fundamental_bundle` 的 `forecast_summary`/`forecast_type` 均改从 canonical 行取。新增 `TestForecastCanonicalRow` 4 例（归母压营收 / 扣非压普通净利润 / 无净利口径回退首行 / 端到端多指标表喂进 bundle 断言「首亏」），adapter + context **46 passed / 3.7 s 零触网**（原 42 + 新 4）。
  **W2（表述限定，代码不动）**：旧条目「一个报告期只拉一次 ⇒ 只有首股付这份钱」严格说是「**非空表**只拉一次」——失败/空表不写缓存（防瞬时空返钉死 TTL，既有测试钉住），代价是未公布报告期（如现 10 月初的 20260930）被逐股重复拉取（O(股数) 次失败请求）；负缓存（30~60 s 短 TTL 与 1800 s 正缓存分开）待实施，随 backlog #11 观察期一并拍。
  **W3（登记不修，fail-open 权衡）**：adapter 每 manager 单例 + `_get_report_table` 实际拉取在锁外，`_run_with_timeout` 8 并发下首批股票惊群重复拉全市场表（瞬时 IO/延迟尖峰，非正确性 bug）；若要消除需 per-key single-flight，本轮不做，随 #11 观察期一并看。评审其余三条 Suggestion（symbol 守卫正则变体漏检 / aggregator 手抄源码守卫偏弱 / 缓存淘汰 FIFO 非 LRU）均为守卫强度与可维护性事项，未动（主契约已由渲染逐字测试与签名 inspect 断言钉住）。

- [新增] GOT **WI-P1-7(b) shadow 首批真实读数：离线回放 10 个归档日，93 个「其他」板块全灭（2026-10-06，负责人拍「离线回放攒 shadow 读数」；【架构计划】新增 §11.13 + §11.7 转正三问回写；⚠️ 零生产代码改动、开关仍缺省 off）**：不必等交易日——`GOT_FAMILY_EMBED_FALLBACK` 在读取期取值，`.review_tmp/p17b_shadow_run.py` 拿 20260916~20260930 归档 `unified.json` 的 `confirmed_sectors`（板块 250）逐日直调 `resolve_family_overrides(mode="shadow")`（向量缓存按 (model+text) 为键跨日共享，14 条原型文本必命中；首跑因 `.review_tmp` 不经 `setup_env` 报 key 未配置，补 `load_dotenv` 后跑通）。
  **核心读数**：落「其他」93/250（**37.2%**，即 (b) 的受益面）；可兜底 **0**——`low_score` **93/93** 全量拒绝，ambiguous/no_candidate 均未出现；score min 0.3081 / median 0.4153 / **max 0.5143**，全部低于阈值 0.55 ⇒ **`GOT_FAMILY_EMBED_MIN_SCORE=0.55` / `MIN_MARGIN=0.05` 这对未标定工程启发式在真实数据上不可达，照现值把 enabled 转正是彻底空转**，转正前必须先重标定（§11.7 三问①已由这批读数回答，②现阈值下 flip 为空集无清单可抽，③不必做——enabled 冻结中）。
  **降档敏感性（排序能力对、绝对值阈值错）**：S=0.50→5、S=0.48→17、**S=0.45/M=0.05→20 行（去重 11 板块）**、S=0.40→32；S=0.45 档抽检：动力电池→新能源、第三代半→AI硬件、酿酒→消费、小金属/稀缺资源→周期资源、汽车整车→智能驾驶（×3 日稳）、医疗保健→医药（×4 日稳）、农用机械→农业、水力发电→新能源、机械基件→机器人、NFT概念→前沿科技——**11 板块判属全部成立、无一误判**（AI 自检，转正评审需负责人复核）；根因是原型文本为关键词清单而板块名只 2~5 字，cosine 天然偏低，不是判别力不足。margin 单独只另拒 6 行，但它拦的是「靠名字相似的伪命中」，降档时不应一起砍。
  **与上一条同源化裁决的张力（诚实登记）**：首选家族分布里**前沿科技出现 10 次、华为生态 2 次**——恰是 prompt 从未告知 LLM 的两个盲区家族，即 **embedding 判得出 glue 永远产不出的家族**；不推翻「暂不告知」（在线行为变更需 replay），但给后续「阈值重标定 WI」追加必答题：`FALLBACK_FAMILIES`（14 候选含盲区）是否应与 `PROMPT_EXPOSED_FAMILIES`（12）对齐——enabled 绕过 glue 直写 `frame_group`，它写得出盲区家族而 M3/glue 口径里没有。
  **另一条样本注意**：只取近三周归档是刻意的——「其他」存量随词表演进塌缩（8 月单日到 44，近三周 6~15），拿旧档会高估负荷；同时复证 §11.8「词表收一个板块，兜底就少一个自由裁量面」。**下一步不是转正**：开关继续 off；「阈值重标定 + 盲区候选集裁决」WI **已于同日追加拍板（负责人：「另开阈值重标定 WI」，登 backlog §7 #12：扩样本 / 0.45~0.50 定 S/M / FALLBACK 14 vs PROMPT 12 对齐 / 达标后才谈 enabled）**。读数工件 `.review_tmp/p17b_shadow_readout.{json,md}`（gitignored）；本轮改动面＝【架构计划】§11.13 新增 + §11.7 回写 + §11.2 转正阻塞项改口 + backlog §7 #12 + 本条。

- [修正] GOT **prompt 家族白名单同源化：消除「第二份名单」本身（2026-10-06，负责人拍「只做同源化 + 守卫测试」；【架构计划】新增 §11.12 / §11.8 那条待决改口；⚠️ 零行为变更，盲区本身不变）**：§11.8 发现的漂移坐实——glue 任务2 的家族名单在三处各存一份：prompt 手抄 **12** 族（`nodes/aggregator.py:1077`）/ 校验侧 `controlled_set` **14 + 无归属**（`:1153`，从 `CONTROLLED_FAMILIES` 派生）/ 审计脚本**第三份**手抄 `PROMPT_VOCAB`（`export_glue_family_audit.py:50`）⇒ 「华为生态 / 前沿科技」从未告知 LLM，而 LLM 万一产出这两族 **S4 不拦**（盲区不是保守态，是不自洽态）。现收为单一名单源：`family_keywords.PROMPT_EXPOSED_FAMILIES`（12 族）——prompt 改 `{' / '.join(...)}`（渲染后**逐字不变**），审计脚本改 `PROMPT_VOCAB = list(PROMPT_EXPOSED_FAMILIES)` 别名（「未列给 LLM」标记照引）。
  **为何本轮不补两个盲区家族**（重要，否则后人会当成「改两行就好」）：告知新家族是**在线 prompt 行为变更**——「华为概念/欧拉」与 AI硬件的「华为算力/华为海思」、智能驾驶的「华为汽车」直接竞争（词表侧按声明序首命中已被先收的族拿掉），一旦告知，LLM 会把华为系板块往「华为生态」拉而规则侧同类板块给另一族 ⇒ **制造新的回收/推翻差异，M3 读数口径会漂**；需走 C-P12 式 replay 对照（A/B 各跑几天）才能转正。
  **守卫四例**（`tests/test_node6_glue.py::TestGluePrompt`）：渲染行逐字等于 join 结果 / exposed ⊆ controlled（否则 LLM 产出被 S4 拦掉）/ **盲区必须显式声明**（`controlled − exposed == {华为生态, 前沿科技}`，新增家族时此例报错，逼作者停下来声明而不是一错再错）/ 源码扫描 `aggregator.py` 不得再出现 `AI软件 / AI硬件` 手抄形式。顺手修正该文件 docstring 里过时的「CONTROLLED_FAMILIES 12 + 1」→ 14。
  **验证**：十套消费侧同跑 **179 passed / 6.48 s**（node6_glue / family_embed / m3_review / glue_pipeline / aggregator_run_smoke / reverse_lookup / cross_node_signals / p2_9 / node6_candidates / a6_wiring）；`export_glue_family_audit.py --days 20260929 20260930` 重跑正常（候选 34 行，both_none=6/dropped=8/recovered=12/same=8）。

- [修复] GOT **WI-P1-11 基本面适配层「业绩预告/快报」取数修复（2026-10-06，由上一条目附带发现的活 bug 引出，负责人拍板「立即另开 WI 修」；【架构计划】§1 矩阵新增 P1-11 行 + §6 WI-P1-11 全节 + 新增 §11.11 + §11.10 该段改口；backlog §7 #10 由「待拍」改为已拍已修；⚠️ **本轮改生产代码**，但不引入任何新的在线决策路径**）**：
  **根因（inspect 实测签名，不猜）**：`stock_yjyg_em / stock_yjbb_em / stock_yjkb_em` 三个 akshare 接口**只接受 `date`（报告期）**，而 `data_provider/fundamental_adapter.py:343-348` 的候选串传 `{"symbol": code}` → 必 `TypeError` → 回退空参 → 拿默认报告期（2020-03-31 / 2021-12-31，陈旧 4~6 年）再按代码过滤 → 绝大多数股票取不到行。
  **实测危害比登记时估的大一档**（`.review_tmp/probe_bundle_before.py`，复刻 `get_fundamental_bundle` 真实调用）：修前 **600187 8.83 s / 600519 6.47 s，`earnings` 块恒为空键 `[]`**；而 `base.py:3079-3085` 给整个 bundle 的预算是 `min(fundamental_fetch_timeout_seconds=3.0, remaining)`（`src/config.py:985`），`_run_with_timeout`（`base.py:2499-2534`）超时即丢弃结果、`fundamental_retry_max=1` 重试也救不回 ⇒ **整条 growth / earnings / institution 基本面上下文长期超时失效**，不止预亏一个字段。消费者含 `core/pipeline.py:408`、`services/alphasift_service.py:2162`、`agent/tools/data_tools.py:460`（**RiskAgent 的 get_stock_info 工具**）。
  **改动面**（`data_provider/fundamental_adapter.py`，不新增依赖、不改决策语义）：① `_recent_report_periods()` 取最近 2 个**已结束**报告期（新→旧）+ `_get_report_table()` 按 (接口, 报告期) 进程内缓存全市场表（TTL 1800 s、上限 6 张、满则淘最旧）+ `_fetch_report_rows()` 逐期回退并表内过滤本股 ⇒ 逻辑上从「一只股票拉一次全市场表」变成「一个报告期拉一次」；② `_filter_rows_by_code(..., strict=True)` 新增口径：全市场表无代码列一律返回空表，**不做「取首行/整表」降级**（那等于把另一只股当本股），默认口径不变；③ 预告链路**只保留 `stock_yjyg_em`、移除 `stock_yjbb_em` 降级**；④ 快报 `quick_report_summary` → 结构化 `earnings.quick_report {revenue, net_profit, net_profit_yoy, announce_date}`；⑤ **新增 `earnings.forecast_type`**＝「预告类型」（预增/首亏/减亏/扭亏/续亏…）——**WI-P1-10 方向分流与 WI-P2-13 风险提示真正需要的输入**，此前在单股层整列被丢弃（本 WI 只负责取到它，消费不属本 WI）。
  **三次「修到一半发现自己错」（不修就留下新 bug）**：① `stock_yjbb_em` 是业绩报表不是预告，旧关键词列表在它的列序上首命中「公告日期」——实测 600519 得到 `forecast_summary="2026-08-15"`（一个日期当摘要喂给 LLM），且单期 **11,449 行 / 10.8 s** 会吃光整个预算 ⇒ 从预告链路移除；② yjkb 表 16 列里**根本没有摘要文本列**，`quick_report_summary` 无论怎么修取数都只会输出一个日期 ⇒ 删除该 key 改结构化字段（grep 确认全仓无消费者依赖旧 key）；③ 未公布的报告期在 akshare 内部报 `TypeError: 'NoneType' object is not subscriptable`（实测 yjkb/yjbb 的 20260930），照原样上报则**每只股都带一条假错**、`earnings` 块会被误标 failed ⇒ 收紧为只有「一个表都没拿到」才上报且去重（第一版写成「成功即丢弃」，粒度不对）。
  **修后实测**：600187 → `forecast_type=扭亏` + 真实预告文本（**9.84 s，首股仍付全市场表拉取以超时**）；688137 → `增亏` **2.46 s** / 688265 → `预减` **2.43 s**（表已在进程内缓存）；600519 → `earnings` 留空 **3.01 s**——**这才是正确读数**（茅台近两个报告期没有业绩预告，不是取数失败）；errors 只剩既存的 `stock_gdfx_top_10_em:KeyError`。
  **遗留（明确不属本 WI）**：(a) `fundamental_fetch_timeout_seconds=3.0` 与逐源延迟**不匹配**——修后**首股仍 9.8 s 超时**，只是后续股票降到 2.4~3.0 s 能进预算（一个报告期只拉一次 ⇒ **只有首股付这份钱**）〔审核订正 2026-10-07：严格说是「非空表只拉一次」，且本条遗留 (a) 所涉取数另有选行缺陷已修——均见本文件最上方 WI-P1-11 审核收口条目与【架构计划】§11.11 审核订正段〕，**预算调参已拍「先挂 backlog 观察、不动配置」（backlog §7 #11）**——先在真实逐日跑里统计 bundle 延迟分位与各块实际超时率，有读数再定是提预算还是把基本面取数从 3 s 主链路挪出；⚠️ 随之登记一条**不得摊平的限制**：单股一次性分析路径（`services/alphasift_service.py:2162`、RiskAgent `get_stock_info`）**不存在「后续股票摊薄」，它就是首股，可能长期拿不到基本面**，观察期统计需与逐日批量路径分开计；(b) 同族已实测但未修（都需先定口径，不是照抄这个修法）：`stock_institute_hold({})` 默认 `symbol='20051'` 陈旧季度表 / `stock_gdfx_top_10_em` 实测 `KeyError`（`top10_holder_change` 部分失效，默认 date 是 20210630）/ `stock_individual_fund_flow({"symbol":…})` 死候选（主候选 `{"stock":…}` 正常，属清理而非纠错）；(c) `forecast_type` 的消费属 WI-P1-10 / WI-P2-13。
  **验证**：`tests/test_fundamental_adapter.py` 新增 `TestReportPeriodTables` 12 例，含两例守卫——`source_never_passes_symbol_to_period_table_apis`（regex 扫本模块源码，`"<接口>",\s*\{\s*"symbol"` 必须零命中）与 `test_installed_akshare_period_apis_take_only_date`（akshare 存在时 inspect 断言三接口有 `date` 且**没有 `symbol`**，防下次升级又改回去而无人知）。adapter + fundamental_context **42 passed / 3.4 s 零触网**；全目录收集基线对账（`git stash push` 这两个文件）＝**1941 items / 13 errors** → 改后 **1953 items / 13 errors**（+12 新例，13 个收集错误全为既存如 `python-multipart` / yfinance 缺失）；消费者子集 **378 passed, 1 failed**，该失败 `test_multi_agent::test_agent_research_returns_timeout_response` 经 stash 对账为**既存**（基线同样 1 failed / 148 passed）。

- [新功能] GOT **业绩预告方向取证 + 减持/业绩预亏定性落档（2026-10-06，由负责人裁决「减持和业绩预亏看一下怎么做，这俩是负面性事件」引出；【架构计划】§1 矩阵新增 P1-10/P2-13 两行 + §6 两节 + WI-P2-4 范围行打指针 + 新增 §11.10；backlog §7 新增拍板记录 #10；⚠️ 本轮零生产代码改动、零在线决策变更）**：新建离线只读脚本 `scripts/audit_earnings_forecast_direction.py`（DB 仅 `mode=ro`，统计口径 `importlib` **直接 import** 解禁取证件 `audit_lockup_risk_events.py` 的 `_norm_day`/`find_anchor_index`/`anchor_window_returns`/`sign_test`/`wilson_interval` 与 `WINDOWS=(1,3,5)`/`SIGN_ALPHA=0.05`，基准同为中证1000，**不另立第二套口径**），产物 `data/earnings_forecast_evidence.json`。
  **三条裁决与落点**：① 预亏反向 bug＝**先取证再分流** → 新增 **WI-P1-10**（`_fetch_earnings_calendar` 停止丢弃「预告类型」+ `_build_structured_events` 按类型给 `direction`，三态开关 `GOT_EARNINGS_DIRECTION`，`category_tag` 保持 drift、`impact` 保持 2）〔⚠️ 后续裁决指针 2026-10-08/10-10：个股级财报整体撤出催化剂日历（verification.md 10-08 裁决，退役 9 函数 −332 行）⇒ 本句所述改动面已退役，WI-P1-10 残余 scope＝bundle→RiskAgent 侧消费 `earnings.forecast_type`，开工前须先改写 WI，详见【架构计划】§200 状态头〕；② 减持源＝**暂用 LLM 新闻通路** → 裁决不变但**理由换成真实的那条**（见下方更正③）；③ 决策参与度＝**先停在「停止污染」** → 负面事件净效果只允许「加分归零」，个股级提示走新增 **WI-P2-13**（输出进 `portfolio_decision.warnings:4859-4889` → `archive.py:1055-1058/1201-1203` 渲染「风险提示」，**不进日历、不参与评分/排序/退出/降仓**）。
  **取证读数**（4 个报告期 20250930/20251231/20260331/20260630，13,829 原始行 → 5,414 条 (股,公告日) 公告；分层抽样 397 条、可算 345）：取值面 **11 个真实类型**（预增 1438 / 首亏 769 / 减亏 705 / 预减 620 / 扭亏 606 / 增亏 604 / 略增 308 / 续亏 214 / 略减 100 / 续盈 42 / 不确定 8），**指标间类型冲突 32.45%**（同一公告日营收与归母净利的预告类型不一致，故 canonical 行按净利润口径取：归母 > 扣非 > 净利润）、净利润口径覆盖 99.76%。价格面 **个股级 T+5 减基准：没有任何一个类型显著跑输基准** ⇒ 产物 `ready_to_fix_direction=false`、`negative_types=[]`；负面合并组（首亏/续亏/增亏/预减/略减，n_pr=187）T+5 超额 **+0.66%（p=0.143）**、跑输占比 0.444、Wilson95 **[0.375, 0.516] 跨 0.5**（连「多数跑输」都判不出）；正面合并组（n_pr=154）T+5 **+1.80%（p=0.012）显著跑赢**；8 类可算样本已达门槛 30（第一版没达到，见下方方法缺陷那条）；唯一逐类型显著是**减亏 → significant_positive（+1.30%，p=0.047）**，但 11 类×3 窗多重比较下不单独作接线依据。⚠️ **字面语义与读数多处相反**：首亏 +1.06% / 增亏 +1.86% / 续亏 +1.27% 全部跑赢，略增 −0.26% / 续盈 −0.85% 全部跑输 ⇒ **「照名字打方向标签」不成立**，WI-P1-10 的 `negative` 只能是**语义标签**并明写「未经价格标定」——它的正当根据是「**预亏不是正面催化剂**」（弱命题，不需要价格标定），而不是「预亏会跌」（强命题，本样本给不出）。另：全体裸收益为负（−1.4%~−4.0%）而超额多为正 ⇒ 样本期基准本身在跌，**不做减基准就会把市场下跌误读成预亏利空**。
  **取证自身的一处方法缺陷与一次负结果（不修就没有可用证据）**：初版沿用 `DataFetcherManager.get_daily_data`（`base.py:1379-1510`，**首个非空即返回、不校验区间忠实性**），397 条里 **126 条 `anchor_out_of_range`**、其中 **95 条挤在 2026-07 中报预告高峰**、有效样本 76%（189/249）塌缩到 2026-01 单月（抽查缓存 blob：Mootdx 把 000977/300763/301068 请求的 06-28~08-07 缩成**区间末日 1 行**；Tencent 不守区间，要 06-28~08-07 给 06-26~09-30）。加「覆盖校验 + 逐源重取（计分只看区间内行数）」后**第一次是负结果**：n_priced 249→233，根因＝拿整段超集序列判覆盖（首尾天然在区间外 → 假通过/误拒混合）＋ code 级 union 窗口过宽；第二版加 `_in_range()` 并把缓存按 `(code, 窗口起点)` 分档后才转正：**可算 233→345、越界 126→8**，106 档由 Efinance(64)/Tencent(42) 补回，仍 48 档无数据（主为北交所，对应 `no_kline=44`）。
  **四条自查更正（本轮走过的错路，留给后人别再走）**：① 曾断言「降级路 `ak.stock_report_disclosure()` 无参调用会 TypeError」——不成立，无参等价 `market='沪深京', period='2021年报'`，日期落在 2022、与 2026 前瞻窗口永不相交 ⇒ **降级路不报错、但也是恒空**；② 曾断言「预亏被当利好置顶是活的、每天在跑」——不成立，49 个归档日里 `event_type='earnings'` **仅 3 条且是同一条**（`*ST国中(600187)`，event_date=2026-08-19，drift/impact=2/confidence=0.95，随增量残留在 0820/0821/0824 重复带出），而它真实预告类型是**扭亏 +115.83%＝真利好** ⇒ 定性改为「**机制缺护栏、危害未兑现**」，优先级也因此定 **P1 而非 P0**（复核这一步差点走错：按「标题含财报」粗筛会把 global 事件误计成 15~30 条，必须按 `event_type` 严格口径）；③ 曾断言「akshare 1.18.91 无减持接口」——不成立，`stock_ggcg_em(symbol="股东减持")`（实测 113,021 行 / 111 s，列含公告日/变动数量/占总股本比例，**无日期参数**）与 `stock_hold_management_detail_cninfo(symbol="减持")`（16,581 行 / 3.3 s，仅董监高）都在；真缺口是**没有前瞻的「减持计划/拟减持」公告源**（全库两词零命中）——已发生的变动是滞后确认，而卖压信号主要在主披露时点；④ 覆盖校验首版的负结果（见上一条）。
  **附带发现的活 bug（当时未修登记待拍，**已于同日后续拍板并修复**，见本文件最上方 `[修复]` WI-P1-11 条目与【架构计划】§11.11）**：`data_provider/fundamental_adapter.py:343-348` 以 `stock_yjyg_em(symbol=code)` 调用，实测该接口签名只有 `date`（默认 `'20200331'`）⇒ TypeError 落第二候选 `("stock_yjyg_em", {})` ⇒ 拿 **2020-03-31 报告期**的预告（陈旧 6 年）再按 code 过滤 ⇒ `earnings.forecast_summary` 要么空要么陈旧；`stock_yjbb_em`（默认 20200331）/`stock_yjkb_em`（默认 20211231）同病，消费者含 `core/pipeline.py:408`、`services/alphasift_service.py:2162`、`agent/tools/data_tools.py:460`（**RiskAgent 的 get_stock_info 工具**）⇒ 单股分析体系的「业绩预亏」否决通路长期喂空/陈旧预告字段，**比催化剂日历那条更活**。
  **改动面**：新增 1 个只读取证脚本 + 守卫测试 `tests/test_earnings_forecast_direction_audit.py` **17 passed**（钉住口径复用、覆盖判定必须用区间内切片、逐源计分只看区间内、覆盖够即早停、坏源拉黑、合并组不吞类型、窄窗分档≤`MAX_WINDOW_DAYS`、超集旧档可复用而区间内残缺必拒、canonical 取净利润口径、太新事件剔除）+ 本地产物（`data/` gitignored）；文档四处（本条 + 【架构计划】§1 两行/§6 两节/WI-P2-4 范围行指针/新增 §11.10 + backlog §7 #10 + 给上一轮 WI-P2-4 条目的「范围只做解禁」那句加后续指针）；**未动任何生产代码、未跑任何在线链路**（实现留待排期）。产物 `caveats` 数组为生成时 9 条，其后脚本又补 2 条（多重比较未修正 / 逐源拉黑阈值「累计 3 次」对接口抖动偏严格），**以 §11.10 为准**。

- [变更] GOT **WI-P2-4 解禁重定：从「板块级负向退出证据」降级为「个股级中性提示」（2026-10-06 负责人裁决，【架构计划】§1 矩阵 / §6 WI-P2-4 / §11.6 注记 / 新增 §11.9 / backlog §7 #9；零代码改动、零在线决策变更）**：负责人裁「解禁是个股层级的事件，最多也就是个中性事件；而且分析解禁也只用分析持仓和每日的推荐票的解禁信息」。这句话答的不是 §11.6 留下的「该正向是不是扇出假象」，而是**把度量对象换掉了**。原设计三处错：① **层次错配**——解禁是个股事件却按「个股→板块扇出」度量，一次解禁最多带 13 个不相干板块；板块级跑赢不能否定「解禁股自身走弱」（同板其他成分可以把板块指数带上），所以**「解禁=利空」这个命题在板块口径下从未被真正检验过**；② **定性错配**——中性事件被当退出侧负向证据接进 P0-1，弱信号接进了强动作；③ **通路冗余**——`risk_events` 通道不必要：`_fetch_lockup_calendar:515` 返回的就是 `{stock_code, stock_name, event_date}` 个股粒度，只在 `_full_build:292` / `_build_structured_events:632` 两处被丢弃。
  **§11.6 读数一律不改，只改定性**：板块级 T+5 超额 +0.99%（p=0.00016）由「前提被证伪」改标为**「不适用」**；扇出统计（均值 2.89、最大 13）由待答问题转为「板块级通路不可用」的结论性证据；Q3「解禁临近持仓命中 0」的阻塞自动消解（新形态按持仓 code 直查，不需与板块事件求交集）。`GOT_RISK_EVENTS` 随通道作废**不再引入**（纯展示无决策可开关），「两问未解前不得进入 shadow」那条红线随之失效。
  **重定后的形态**：输入＝持仓 code ∪ 当日推荐票 code，一次按日期区间取数后**本地按 code 过滤**；输出＝解禁日 + 距今 N 交易日 + 解禁规模占比；**只在报告里作一行提示，不进 `catalyst_events`、不参与评分/排序/退出/降仓**，取数失败降级为不提示、不得编数。可白拿的改进：现取数按位置只拿前 4 列（序号/代码/简称/解禁时间），**解禁数量与占比列全丢**（同一次返回即含、零额外网络成本；列序需实测确认——`_fetch_lockup_calendar` 自陈「列名编码不稳定」），否则占比 0.01% 与占总股本 12% 会提示成同样一行。未定参数（实现时再拍，本重定不预设）：前瞻窗口天数、占比提示阈值。范围收窄：**只做解禁**，减持与业绩预亏剔除（减持的实际卖出压力与解禁性质不同，需单独定性）。**〔两者已于同日后续定方向并取证，见本文件最上方业绩预告取证条目与【架构计划】WI-P1-10 / WI-P2-13 / §11.10〕**
  **更正本轮给出的一个错误警告（重要，避免留下假依据）**：曾断言「若把 lockup 恢复进 `catalyst_events`，P5 会把有解禁的股票 `score=100` 置顶当利好」。**该断言不成立**——`stock_screener.py:2728-2733` 明确 `direction == "negative" → continue`（注释即「利空个股事件（如限售解禁）不置顶」），板块级另有 `direction_coeff=0.5` 降权；这是 **2026-08-25 催化剂日历瘦身**那轮就修掉的（同日 CHANGELOG 条目把「P5 存在 direction bug」列为已修项），当时只读了 `_score_catalyst_relevance` 的分桶段（`:2685-2700`）未读到消费段（`:2720+`）就下了结论。⇒ **不把解禁放回日历的结论不变，但理由换成真实的那条**：瘦身的动机是**稀释**（121/242 条事件为 lockup、30 股扇 52 板，narrative/macro 信息密度被砍半）＋日历是**前瞻板块池**、覆盖不到「按 code 精查持仓这一只」的需求。方向污染不是理由，它已被修好。
  **一处编号冲突提醒**：backlog §2 处置表的「P2-4」是 0902 审计报告的「其他家族垃圾箱」（已生效），与【架构计划】**WI-P2-4**（解禁）**同号不同物**，本轮在 §2 该行与 §7 #9 都加了互斥标注。改动面：本计划 §1 矩阵 P2-4 行 + §6 WI-P2-4 全节重写 + §9 对账指针 + §11.2 延后项改口 + §11.6 就地注记 + 新增 §11.9；backlog §7 新增拍板 #9 与 §2 编号提示；上一轮取证条目加本裁决指针。**未动任何代码、未跑任何在线链路**（实现留待后续排期）。

- [变更] GOT **M3 直接结案转正：放弃 §8.1 的 `--sample-mode full` 复跑前置（2026-10-06，【verification】§6 M3 / 【架构计划】§11.8 / backlog §3.3 OPEN-C2；**纯口径文案与文档状态，零抽样/零计分/零生产开关变更**）：负责人裁决「M3 直接转正，有问题之后再现跑」。本条**记录裁决依据，不是事后合理化的省事理由**：分层样本里难点（回收+推翻）占 26/36＝**72.2%**，而同期全量分母里只占 36/65＝**55.4%** ⇒ 已读的 2.8% 是**偏高**读数，拿偏高读数作验收属**保守方向**（不会把差的说成好的）；按层外推（post-stratification，唯一误配在回收层：35/65 × 1/25）回全量口径得 **≈2.2%**，两读数同处 15% 红线内且与红线差一个数量级——复跑只补仪式、不改变结论。
  **同时更正上一轮条目对代价的高估**（当时据此建议「不转正、需 full 复跑」，估计本身错了就要说）：全量分母不是 120 行而是 **65 行/5 日＝日均约 13 条**；120 是含 glue 弃权（dropped 35）与双空（both_none 20）的总候选，它们按 §8.1 定义不入分母、也不需要判读。日均 13 条本就在 §8.1 自己写的「≤10 分钟/日」预算内，所以**日后想补随时可补**（`--sample-mode full` 保留未删），转正不是永久放弃复核能力。
  **结案后口径的新定位（本次代码改动的实质）**：`scripts/build_m3_review_package.py` 三处文案同步——模块 docstring 的「口径可比性」、`process_note()` 的「转正」行改为「结案」、`--score` 末尾由「⚠️ 转正须用 full 复跑」改为「ℹ️ 已结案，本口径作日常预警」。**不改**抽样逻辑、三档判据、计分公式、`--sample-mode` 缺省值（仍 stratified）；改后 `--score` 复跑读数仍为 **2.8% / status=met** 逐位不变。动机是消除**假告警**：保留原文案会让每日评审包继续向使用者要求一个永远不会被满足的前置。stratified 此后偏高且样本小（单日 7~8 条里 1 条无关系即 **14.3% 贴线**，同一条放全量口径只 1/13≈**7.7%**）⇒ **破线需人工复核再决定是否回到全量，不自动回滚**。
  **残留风险（转正不消除，显式保留）**：真正的短板不在口径而在**统计功效**——n＝36，Wilson95 上界 14.17% 只是勉强压住 15%；弱关系堆积 4/36＝11.1%（评审包脚本定性为 prompt 收紧信号）本轮未处理；prompt 硬编码 12 族 vs `CONTROLLED_FAMILIES` 14 族的漂移仍待决（需走 C-P12 replay）。**本项只结 M3，不是 glue 上线闸门**：glue 已于 2026-09-17 A6 阶段 1 转正在线（`AGENT_SEMANTIC_GLUE_ENABLED` 缺省 true、`_SHADOW` 缺省 false，`aggregator.py:1283` 关闭即报错），M3 是上线后的质量审计指标；§8.2「双达标」里的 M1（34.4%）/ M2（28.1%）/ M4-a（0.591）仍是未达的水位监控项，本次不动它们。
  新增守卫测试 `test_process_note_records_promotion_decision`（断言 note 已记录结案裁决且不再出现「必须用 `--sample-mode full`」，防前置被人写回去）——`tests/test_m3_review_package.py` **18 passed**。文档：【verification】§0 人工抽查行与 **§3** M3 行改「✅ 已结案转正」（**并更正交叉引用误标**：M1–M6 红线表一直在 §3，第十二轮那条与上一轮 [修正] 条目里写的「§6 M3 行」实指 §3；本轮同时把 §6「需人工或等时间窗」表的 M3 / OPEN-C2 两行从旧口径改口）+ §8 补评审包/`--score` 复现命令 + **第十三轮**修订行（同时在第十二轮那句「分层初筛 ≠ 转正」就地标「该前置已于第十三轮作废」）；【架构计划】§11.1 改口 + §11.8 新增「结案决策/口径新定位/残留风险/≠上线闸门」四段与外推读数表 + 结案轮改动面行；backlog §3.3 OPEN-C2 由「唯一未闭环是转正口径」改为**本项已结**；【glue 计划】§8.1（archive）只加执行注记、不改原表内容。

- [修正] GOT **M3 家族归属人工终审首次读齐 + 唯一误配的词表对症收口（2026-10-06，【verification】§6 M3 / 【架构计划】§11.8；⚠️ 本轮含在线决策变更，非纯观测）**：负责人逐行判完评审包 36 行（归档日 0923/0924/0928/0929/0930，分层口径 seed=42）→ `--score` 实测：误配率 **2.8%**（无关系 1 / 弱关系 4 / 强关系 31）、Wilson95 **[0.49%, 14.17%]**（上界仍压住 15% 红线）、逐日 0.0/0.0/0.0/**14.3%**/0.0% → 末尾连续 5 日达标 `status=met`。⚠️ **分层初筛不等于转正**：§8.1 要求 `--sample-mode full` 复跑，本轮未跑；且 0929 的 14.3% 是贴线（1/7，同日再一条即 28.6% 破线）。**〔该复跑前置已于同日后续裁决作废，见上方 [变更] 条目〕**
  **机制发现（比读数值钱）**：`aggregator.py:1075` 任务2 原文是「families — 家族归属（**仅对落「其他」的板块**）」——即 **规则侧词表能命中的板块，glue 根本没有家族裁量权**。治理 glue 家族误配的杠杆点不在 prompt 而在**词表覆盖度**，这把 WI-P1-7(a)（词表治理）与 (b)（embedding 兜底）串成同一根因链：词表收一个板块，glue 就少一个自由裁量面，(b) 的受益面就是「其他」存量本身。另同时发现：prompt 硬编码家族只有 **12 个**（`aggregator.py:1077`，`export_glue_family_audit.py:50` 的 `PROMPT_VOCAB` 同步记录），而校验侧 `controlled_set` 是 **14 族 + 无归属**（`aggregator.py:1153`）——**华为生态 / 前沿科技 从未告知 LLM**（产出也不会被 S4 拦），本轮未改（改 prompt 需走 C-P12 replay），列为待决。
  **两条非达标判定的处置（一人一策，不是一律改词表）**：① **#25 低空经济→智能驾驶（无关系，计入分子）——改**。先跨日取证：20260921 / 20260929 **两个抽查日都被 glue 判为智能驾驶**，是稳定复现的系统性偏置（LLM 把 eVTOL/无人机的「自主飞行」语义拉到「自动驾驶」）而非一次性抖动。收口到**军工**（`family_keywords.py` 军工族新增关键词 "低空"）：低空载具属航空产业链，成分股多为主机厂与机载设备商。规则侧命中后 glue 不再介入该板，误配源头切断。② **#34 电气设备→新能源（弱关系，不计分子）——不改**。M3 口径只把「无关系」计入，弱关系是允许态；把「电气设备」硬绑「新能源」会把弱关系**固化为强关系假象**，还误伤传统电气/电机板块（同样跨日稳定拿新能源，属可接受弱锚定）；仅登记为弱关系堆积观察项（占比 4/36=11.1%，评审包脚本注释定性为 prompt 收紧信号）。附带发现：「日用化工」是推翻行（规则侧周期资源 / glue 侧消费，负责人判 glue 对）——「化工」子串同时吸了上游石化与下游日化，语义过粗；但它是 `overridden` 而非 `recovered`，本轮不收（收就是反向限制 glue 推翻规则侧的能力，方向与 M3 结论相反），归 WI-P1-7(a) 后续细粒度拆族。
  **影响面与验证**：改动面仅一词表条目 + 守卫测试 `test_dikong_resolves_to_military_family`（断言「低空」唯一登记于军工、`_build_frame_group("低空经济")=="军工"`、不再落「其他」、智能驾驶族不得沾「低空」）。`_audit_keyword_overlaps()` 仍 `[]`（无新增跨族重叠）、`CONTROLLED_FAMILIES` 仍 14（不动 `test_node6_glue` 的 ==14 断言）。同跑：gate1/family_embed/node6_glue/m3_review 四套 **92 passed**；Node 2 三套 + 消费侧七套 **180 passed**，零回归。**下游会跟着动的地方**：`低空经济` 的 `frame_group` 由「其他」变「军工」→ 家族拥挤检测计数、主线家族聚合、Node 6 家族中继与 `family_anchor_map` 漂移宽限；首个生产日跑后，0921/0929 类归档的家族口径与新归档不可直接对比（与「换电」治理同性质，但那轮是零行为变更，本轮不是）。评审包 md 判定列已按 csv 同源回写（含从负责人手工填的 #1/#2/#9/#10，染料涂料按手工判定归弱关系）。文档：【verification】§6 M3 行由「未测」改为首实测 + 第十二轮修订行；【架构计划】§11.1 加 M3 终审条 + 新增 **§11.8**（读数表 / 机制发现 / 两条处置论证 / 改动面与在线决策声明）。

- [新功能] GOT **WI-P1-7(b) 「其他」板块 embedding 家族兜底（2026-10-06，【架构计划】§4 P1 工作项 WI-P1-7 b 项 / §11.7；三态开关缺省 **off**，off 态零触网零改判）**：新建生产模块 `src/got/family_embed.py`（家族原型文本 + `select_family` 保守阈值裁决 + 按日向量缓存）+ Node 2 `run()` **Step 3.4c** 接线 + `SectorContext.family_embed_shadow`（缺省 None）。计划原文要求「复用 `glue_pipeline` 的 L1 检索接地能力**但接进生产路径**」，同时 §315 又红线「不把 glue_pipeline 当生产家族粘合（replay-only）」——两者并存的不破口径落地：**只借四个件**（`embed_texts`/`retrieve_topk`/`_cache_key`/`L1_EMBED_CACHE_ROOT` 按日缓存惯例），cosine 与缓存 **不另写第二套口径**，`glue_pipeline` 本身一行未改，兜底走独立开关 `GOT_FAMILY_EMBED_FALLBACK`。
  **三态**：off（缺省）立即返回 `skipped_reason=mode_off`，不触网、不读/写向量缓存、不改 `frame_group`；shadow 照常算向量并落「若兜底会改判的板块清单」（`unmatched_boards` 逐板含 reason/score/margin/前 3 名 + `overrides`/`would_flip_count`）、**不改任何 frame_group**；enabled 才按 overrides 真写 `SectorDetail.frame_group`（只改家族不改 state/评分）+ notes 留痕。**接线顺序红线**：Step 3.4c 必排在 Step 3.4a 家族拥挤之前，否则拥挤降权、主线家族聚合、Node 6 家族中继/漂移宽限（`frame_group_lookup`）都看不到兜底后的家族；`_build_theme_families` 走数据驱动相关性不用词表，故不受影响。只给「不命中任何关键词」的板块做兜底（命中词表的板块走 `collect_unmatched` 直接排除）；保守阈值 `GOT_FAMILY_EMBED_MIN_SCORE=0.55`/`_MARGIN=0.05`（平票不兜底）**属未标定工程启发式**（按 §179「保守阈值 + 留痕人工复核」拍的缺省值，无数据依据），**不得直接当转正阈值**；任何一步失败（无 key / 数量不匹配 / IO 异常）只写 `skipped_reason`，双层兜底（模块内 try + Node 2 外层 try）保证 embedding 不可用绝不能拉挂 Node 2。
  **离线自检读数**（`.review_tmp/p17b_selfcheck.py`，强制清空 embedding key 不触网）：off→`mode_off`、trace 空、零缓存文件；shadow→`embedding_unavailable`、overrides 0、cache_hits/misses 均 0；**非法开关值（`nonsense`）回落 off**（不得意外开启生产改判）；shadow 下命中词表板块正确排除（skipped_count=1）；候选家族 14 个（= FAMILY_KEYWORDS 去掉「其他」）。本轮**只落能力 + 离线自检，不产真实 shadow 读数**（`.env` 已配 `GLUE_EMBEDDING_API_KEY`，真实读数需首个非交易日显式 `GOT_FAMILY_EMBED_FALLBACK=shadow` 跑）；enabled 转正三问（「其他」占比与拒绝原因分布 / `would_flip` 人工抽检 / enabled 首日下游 diff）已记 §11.7。
  新增 `tests/test_family_embed_fallback.py` 26 例（原型文本关键词截断与族名保留、`select_family` 四口径 ok/low_score/ambiguous/no_candidate 且拒绝也留首选痕迹、开关归一与非法值回落 off、词表复核分流、缓存命中时**不请求 embedding**、未命中落按日文件、数量不匹配报错、三态生产入口（off 零 IO零网络/ shadow 只留痕/ enabled 才出 overrides）、margin 门槛卡住不得改判、改判谓词 off/shadow 不改只 enabled 改、接线守卫 Step 3.4c 先于拥挤检测且模式仅由 family_embed 读一次）。与 `test_sector_state_machine`/`test_wave_days`/`test_sector_rotation` 同跑 **98 passed**；与 `test_glue_pipeline`/`test_aggregator_run_smoke`/`test_reverse_lookup`/`test_cross_node_signals`/`test_p2_9_context_consistency`/`test_node6_candidates`/`test_a6_wiring` 同跑 **108 passed**（消费侧零回归）；与本轮另 4 套（lockup/M3/锚重建/GATE-2）+ Node 2 另 3 套同跑 **163 passed**。文档：【架构计划】§4 WI-P1-7 加落地行、§11.1 拆成 (a)/(b) 两条、§11.2 新增「enabled 转正」延后项、新增 **§11.7**。

- [新功能] GOT **P2-4 解禁 risk_events 通道：解冻前置取证（2026-10-06，【架构计划】§6 WI-P2-4 / §11.2；离线只读、**不建通道不接在线决策**、零在线改动）**：新建 `scripts/audit_lockup_risk_events.py` → 工件 `data/lockup_risk_evidence.json`（`data/` 已 gitignore）。该 WI 原文要新建 risk_events 通道把「解禁临近」当退出负向证据，但 §11.2 已判本轮不启动且**通道根本不存在**——本轮只回答「值不值得立」三问：Q1 数据地基 / Q2 负向证据强度（减基准）/ Q3 与持仓的可接线性。**口径纪律（禁第二套口径）**：板块名→板码全量复用生产 `CatalystRealizationTracker._get_board_code`（三级解析，`object.__new__` 跳过 `__init__` 不建 DB）；T+N 锚定镜像生产 `_find_event_date_index`（精确优先、±3 日先 + 后 −）+ `_compute_multi_window_returns`（事件日收盘→T+N 收盘），并逐条与 DB 已落库 `day_N` **对账**（match/diff/na 如实报覆盖率）；基准=中证1000（与 GATE-2 同文件同口径）；Wilson 区间复用 `calibrate_optimal_stop.wilson_interval`；**同板同日多只解禁股聚合为 1 个板块日样本**（否则一次板块移动被重复计入）；DB 以 `file:...?mode=ro` 只读打开（测试钉住写入必报错），不触网。
  **真实读数（as_of 2026-10-06）——本 WI 的立项前提被证伪**〔该定性已于同日后续裁决改标为**「不适用」**：解禁是个股层级事件，板块级代理并不检验「解禁=利空」这个命题，见【架构计划】§11.9 与下方「后续裁决」注记；以下读数原样保留不改〕：`catalyst_realizations` 现存唯一解禁历史样本 **127 条 lockup**（2026-08-19→2026-09-02，丢弃生效后零新增；`catalyst_events` 表 lockup=0 已断流），覆盖 57 板块，44 个独立个股事件扇出 127 条板块记录（均值 2.89、**最大 13**），可算样本仅 46/127（no_board_code 27 + no_kline 54，受 142 板块缓存覆盖限制）。关键：生产落库 `day_N` 是**未减基准的裸板块收益**，减基准后解禁板块 T+1 超额 **+0.19%**（p=0.883）、T+3 **+0.81%**（p=0.0045）、T+5 **+0.99%**（跑输占比仅 0.217，**p=0.00016**）——**方向反指：解禁板块显著跑赢基准**。照原设计（解禁临近→提前降仓/退出）接线会在现有票池上系统性做反向操作；且「入场后 ≤5 交易日同板解禁」命中 **0**（埋伏快照仅 3 条、持仓 0 条）→ P0-1 退出后验无观测地基。结论 `unlock_ready=False`，解冻评估需先回答两问：① 该正向是否板块扇出/小样本假象；② 解禁是否该按**个股级**而非板块级度量。两问未解前 `GOT_RISK_EVENTS` 不得进入 shadow。对账 diff（T+1 16/46）归因于当时解析到的板码／K 线截止与现在不同，本表读数不得当生产口径复用。
  新增 `tests/test_lockup_risk_events.py` 18 例（日期归一/日线读取缺列降级/锚定精确优先与回退顺序/窗口越界不编数/同板同日去重/扇出统计/对账三态/符号检验精确概率与全零不做判定/汇总只用可比格/四态方向判定/主窗选 T+5/可接线性窗内窗外/解冻结论三分支含「反指不得归因样本不足」/`mode=ro` 拒写/无 K 线端到端诚实降级）；与 M3・锚重建同跑 55 passed。文档：【架构计划】§6 WI-P2-4 加 ⚠️ 取证行、§11.2 阻塞项升级为「前提被证伪」、新增 **§11.6** 全量读数与红线。**〔后续裁决（2026-10-06）：本条据以得出的「前提被证伪（方向反指）」已被负责人重定推翻——解禁是个股层级事件、最多中性，板块级代理不适用；WI-P2-4 改为个股级中性提示，见【架构计划】§11.9 与 backlog §7 #9、上方置顶 [变更] 条目〕**

- [新功能] GOT **OPEN-C2 · M3 人工终审评审包（2026-10-06，【verification】§6 / backlog §3.3 OPEN-C2；离线只读、**不代判**、零在线改动）**：新建 `scripts/build_m3_review_package.py` → 产物 `data/glue_family_audit/m3_review_YYYYMMDD.md/.csv/.json`（已 gitignore）+ `--score` 计分工件。把【C 层计划】§7.2 OPEN-C2 只有一句「建议分层抽 10、三档判据」的人工流程**钉成可复现口径**：① 谁判＝负责人（§8.1 原文已定 M3 无法自动化，ground truth 是市场共识）② 抽样＝按日分层 回收 5 + 冲突 3 + 随机对照 2，seed 固定且**逐日独立子流**（多一天不改变原有日样本），**池不足只记缺口、绝不跨层补齐**（拿一致样本填冲突空缺会把误配率稀释成假达标）③ 判据＝强/弱/无关系 + 「无法判定」兜底（不入分子也不入分母、单独披露，防把「没依据」算成「没错」）④ 计分＝误配率 = 无关系/（强+弱+无），**glue 弃权/双空不入分母**（§8.1 定义 M3 只针对「给出了归属」的行）⑤ 红线与转正＝单日 ≤15%（10 样本 → 2 条无关系即 20% 破线）、末尾**连续 5 个抽查日**逐日达标（无数据日不入连续序列也不假装达标）⑥ **口径可比性**：分层对难点超采、读数偏高，只作预警初筛；**转正必须 `--sample-mode full`（§8.1 每日全量）复跑**——评审包页头直接写明这一约束，避免拿初筛数当转正依据。**〔该复跑前置已于同日后续裁决作废，见上方 [变更] 条目；脚本与评审包文案已同步〕**抽样与类别判定全部复用现有审核工具 `export_glue_family_audit.py`（`discover_days/load_day/classify/flag_cross_day_instability`），Wilson 区间复用 `calibrate_optimal_stop.py::wilson_interval`，**不另写第二套口径**；判定档位与 HTML 页 对/错/存疑 键位互认（`GRADE_TO_HTML_BTN`）。
  **评审包真实读数（as_of 2026-10-06，最近 5 归档日 0923/0924/0928/0929/0930）**：候选 120 行 → 抽 36 行（回收 25 / 一致 10 / 推翻 1）。⚠️ **OPEN-C2 设想的「冲突 3」层当前无样本可抽**：`overridden` 池 5 日合计仅 1 行（0914 那批曾有 6 行）——家族词表同源修复后规则侧与 glue 侧已趋一致，**M3 主战场实为「其他回收」层（35 行/5 日）**；另有 55/120（46%）属 glue 弃权/双空（不入 M3 分母，但是 M1/M4 侧信号）。旧版评审包 9/14 实跑材料（128 行、待判 6 行）仍在 `family_audit.html`。
  新增 `tests/test_m3_review_package.py` 17 例（分层确定性/不跨层补齐/逐日独立子流/对照层只抽已归属行/判定别名归一与未知值不蒙对/只有无关系计分子/红线边界 10%·20%/无法判定与未判不入分母/glue 弃权不入分母/连续达标与 pending/not_met/无数据日/评审包判定列必留空与池表不破格/csv utf-8-sig 回环）；与 M3·GATE-2·锚重建三套同跑 47 passed。**本项不产生 M3 结论**（判定列全空，首次终审归负责人）。文档：【verification】§6 M3/OPEN-C2 两行转「已定义+已产」 + backlog §3.3 OPEN-C2 登记。

- [新功能] GOT **B4-1 前置·事件锚重建脚本（2026-10-06，【总计划】§四 C-B4 落地改法第 1 条；离线只读、零在线改动）**：新建 `scripts/rebuild_event_anchor_map.py` → 工件 `data/event_anchor_map.json`（`data/` 已 gitignore，脚本入库、产物不入库）。把每日催化剂日历事件重建为**事件 → 880 板块码**锚表并对 43 条首次埋伏信号测出覆盖率与无锚归因。**零自研匹配**（计划红线「禁止另写简化版」）：四层全部 import 生产件 —— `AggregatorNode._build_relations_index`（继承「direction=negative 不补挂」「relation 的 event_id 不在当日日历即丢」护栏）、`CatalystRealizationTracker._get_board_code`（registry+`board_aliases`+difflib 0.68，用 `object.__new__()` 跳过 `__init__` 避免离线建 DB）、`AggregatorNode._match_sector(min_substr_len=3)`（`generic`/`narrow` 不采信，同 `_lookup_flow_map`）、`sector_analysis._build_frame_group`（家族同族，最弱层，可 `--no-family-layer` 消融）。PIT：板块码↔名对照按日**累计**（只用 ≤ 信号日见过的名）、事件取信号日当天 `news.json`、窗=0 已落地事件归无锚桶、超出归档末尾单列右删失桶。
  **读数**：有前瞻锚 20/43（46.5%），但**硬锚（rank≤2）仅 4/43（9.3%）、含硬锚候选上限 5/43（11.6%）**，其余 15 条全靠家族同族弱锚（消融关家族层 → 20→5）；无锚归因 `no_covering_event_for_board` 21 + `event_beyond_archive_end` 2；锚窗长 p50=1.5/mean=1.95/max=6 交易日，分型 drift 12/fade 5/binary 3；旧裸子串基线 9（严格剔窗=0 后 8，旧口径把已落地事件当锚属口径错误）；取证面 47 归档日、事件 1443（零锚 61）、板块宇宙 465 码、registry 仅 112 码、**32/47 日无 glue**。**结论**：天花板是日历覆盖 + glue 归档稀疏，不是匹配算法 → B4-1 事件对齐窗只能对 `anchor_rank≤2` 子集启用，`family_match` 不得进验收口径，其余保留固定窗 fallback。
  **两个坑（已写进注释 + 测试钉住）**：① 证据等级必须按**板码**粒度记 `code_rank`，只记事件级 `sources` 会把家族弱锚误报成硬锚（初版实踩报「20 锚全硬锚」，靠 `--no-family-layer` 消融暴露矛盾）；② `sector.json.active_mainline` 顶层是家族分组、板块在 `members[]`，必须递归下钻（GATE-2 同款坑）。新建 `tests/test_event_anchor_map.py` 20 例（归档日过滤/快照目录排除/members 下钻/relations 反查与孤儿丢弃/negative 不补挂/名↔名走生产件且医药↔医药电商 必须落空/max_fanout 只留 exact·alias/code_rank 逐码定级/窗=0 不作锚/窗长优先再硬锚 tie-break/horizon 与右删失/归因拆桶/信号去重剔 control/新旧基线可比/覆盖率分层）；与 `test_optimal_stop_priors.py` 同跑 30 passed。文档：【总计划】§2.4 转已落地 + §四 C-B4 第 1 条补真实读数与验收红线。

- [新功能] GOT **GATE-2 最优停止 pooled/分层先验标定脚本（2026-10-06，【架构计划】§2 GATE-2 / §6 第 1 批地基；离线产物、零在线改动）**：新建 `scripts/calibrate_optimal_stop.py` → 工件 `data/optimal_stop_priors.json`（`data/` 已 gitignore，脚本入库、产物不入库，与 `wave_calibration.jsonl` 同惯例）。面板 = `unified.confirmed_sectors[].sector_role=mainline`（401）+ `sector.json active_mainline members`（111）= 512 主线板块-日 → 107 段（≥2 交易日）→ 剔 3 条右删失 → **102 段 / 173 板块**；段内收益直接用归档 `today_change`（生产当日所见，天然 point-in-time），段后续涨才读本地板块日线缓存（默认不触网，覆盖 58/102 如实披露）。**读数**：pooled μ 绝对 +1.21%/日、减基准超额 +0.66%/日（基准=中证1000，【总计划】§1.1 口径红线）；θ（段内峰值日）p50=2、P(θ≤3)=82%、P(θ≤5)=97%；峰值优于段尾仅 45.1%（Wilson 35.8–54.8%）；出主线后 fwd1 绝对 −3.43% / 超额 −1.21% → 支持快退。`家族 × regime` 24 箱仅 2 箱达 `min_samples=12`，其余 `w=n/(n+8)` 向 pooled 收缩（验收红线：箱不得为空）。旁证：`wave_calibration.jsonl` 1249 行 `wave_days` p50=2 / 97.2%<7 天 ↔ 实测段长 p50=2 / 98.0%<7 天，「主线寿命 93.5%<7 天」同向复现。
  **两个口径坑（已写进脚本注释 + 测试钉住）**：① `sector.json.active_mainline` 顶层是**家族分组**、板块在 `members[]`，不下钻就把活跃主线整批丢掉只剩 `exhausted_mainline` → θ 被系统性拉到第 1 天、pooled 段累计收益由真实的 +2.9% 误读为 −1.9%（初版实踩）；② 段尾正好压在归档最后一天的**右删失**段会把峰值拉早，默认剔除并计数。新增 `tests/test_optimal_stop_priors.py` 10 例（归档日过滤/下钻/切段断档/θ 与超额对齐/收缩与非空/基准取窗）。文档：架构计划 §11.1 已落地 + 新 §11.5 读数与待裁决项（P0-1 `posterior` 分支阈值定值需累积更多完整主线生灭周期）。

- [新功能] GOT **P1-1 催化剂置信度门槛分位重标取证脚本（2026-10-06，第 1 批 #1，只读不改值）**：`scripts/calibrate_catalyst_confidence_gate.py` 把 `catalyst_realizations`（窗口内 695 条 / 已兑现 464）转成负责人定值用的取证表——① 样本就绪度（glue_consumed 交易日、污染计数）② confidence 分箱质量（兑现率/净化方向正确率/前向收益/**减基准超额**，基准=中证1000 同 horizon，符【总计划】§1.1 口径红线）③ 门槛模拟（0.50/0.55/0.60/0.65/0.70/0.75/0.80 保留集质量 + 区分度/倒挂趋势判定）④ 归档埋伏漏斗 + 蓄水池 reason 聚合（近 30 归档日：候选 179→出池 11、7 日出池为 0；reason 分布 near_miss 200 / no_catalyst 110 / data_missing 62）⑤ 分类质量 ⑥ 决策位；产物 `reports/catalyst_confidence_gate_forensics_YYYYMMDD.md|.json`（reports/ 已 gitignore，不入库）。
  **取证读数**：减基准超额胜率随阈值 0.50→0.80 极差仅 3pp（无实质区分度），净化方向正确率反而 44%→34% 倒挂 → 现 confidence 分值对兑现质量无预测力，**不建议单纯调阈值**（归 WI-P1-6 闸门评分化 + 事件锚重建）；本次因 glue_consumed 9<10 交易日仅取证不改值。
  **新发现缺陷（登记 backlog §3.4 OPEN-D10）**：`catalyst_config.GLOBAL_TO_A_SHARE_MAP` 的 `direction` 存的是漂移类型词（drift/fade），而 `catalyst_realization._check_direction` 只认 positive/negative/binary→其余恒判错，污染 464 条中 **162 条（35%）**，使方向准确率由真实 44% 被拉到 29%；旧脚本 `calibrate_catalyst_params.py` 基于 direction_acc<50% 给出的「降门槛至 0.60」属伪结论，已在新脚本内过滤并显式告警。修源头/修判定二选一均改变历史统计读数，归新 WI 待拍板（本批不动生产代码）。
  新增 `tests/test_catalyst_gate_forensics.py` 5 例（纯函数：基准取窗/污染过滤/actual 口径 horizon 镜像/分箱与比例格式化/门槛模拟标现值）全部绿；backlog §3.4 OPEN-D3 行与 §5 Phase 5c 4.4 同步登记。

- [改进] GOT **3-D 埋伏重放 harness 参数化（2026-10-06，解锁 ambush 标定回放，零决策语义变化）**：【架构计划】§11.4 结论二指出的硬编 `date.today()` 阻塞项修复——`portfolio_decision.py` `run()` 新增可选形参 `as_of`（缺省 None → `date.today()`，旧路径逐字节不变），穿透 `_write_ambush_buy_snapshots`（entry_date 自然键）与 `_close_ambush_snapshots`（exit_date）；`orchestrator.py` Phase 2 调用处接 `as_of=target_date`（生产当日二者相等，行为不变；重放/标定传历史日）。配套新建 `tests/test_ambush_replay_harness.py`（3 例：缺省=today 保旧行为 / 传历史日 entry・exit 跟随 / fake session 不触真实存储）。回归：ambush/bear_gate/trailing/pr2/pool_consumption/funnel 目标集 65 passed。剩余阻塞：结构性样本稀缺仍需 10-08 实跑后积累；STALL/TIMEOUT 写死参数化与事件锚重建（B4-1）后续另 WI。依据排期蓝图第 3-D 批 / 【总计划】§四 C-B4。
- [新功能] GOT **C-B2 启用前盘点 shadow 观测字段 `mature_reduce_shadow`（2026-10-06，P0 第 0 批 #4，纯留痕零决策改动）**：工作流 C 成熟减仓阈值 30→14 切换前的冲击盘点——`src/got/nodes/portfolio_decision.py` `_score_holdings` 生命周期分支旁新增观测：持仓所涉板块 `mature` 且 `14 < lifecycle_day ≤ LIFECYCLE_MATURE_REDUCE_DAY`（现阈值 30 未命中、切 14 本应触发 reduce）且未被更高优先级 sell 拦截时，记 `{symbol, sector, lifecycle_day, action_kept}` 入新字段；`schemas.PortfolioDecision` 新增 `mature_reduce_shadow: Optional[list]`（仿 #5a/raw_position_ratio 先例不进 R-B8/幂等不变组，缺省 None 旧路径逐字节不变）。盘点前提：模拟账户 0922 清仓后空仓（启用前冲击清单现为空），历史回放 16 板块-日属已平旧仓；开关未动（`GOT_MATURE_REDUCE_DAY` 仍缺省 30，`GOT_TRAILING_STOP_ENABLED` 仍缺省关）。测试：portfolio/ambush/bear_gate/trailing 目标集 93 passed（test_bear_entry_gate/test_ambush_* ×4/test_data_tools_portfolio_snapshot 39 + test_trailing_stop/test_portfolio_pr2/lot_rejection/test_node6_ambush_funnel 54）；毒模块 test_discord_platform（缺 nacl，既有环境问题）排除。依据【总计划】§4.2 C-B2 启用前步骤 / 排期蓝图第 0 批 #4。

- [改进] GOT **reasoning.md 渲染升级（2026-09-29，转正内容格式更新 + 影子期内容集中可审阅，仅改 md 文本零数据产物改动）**：承接排期计划 P1。① **A6 glue 转正子块**（`src/got/archive.py`）——`_render_shadow_a5` 重写为 `_render_a6_glue_formal`，新增 Node 6 正文固定子块「🧬 A6 语义粘合（已转正）」，渲染 `glue_consumed`/`glue_status` + 关系/强度/家族计数 + 全量语义表，措辞由「影子，不影响决策」改「已转正，直接替换四件套；degraded 板块保留规则侧」；由 `_write_reasoning_md` 在节点 reasoning 循环前注入。② **🧪 影子段从三条线扩为五条线**——新增 `_render_shadow_market_theme`（读 `unified.json` 的 `market_theme_shadow`，展示 status/领先概念/宽度）、`_render_shadow_disagreement`（读 `conflict_summary_shadow`，展示冲突分类计数 + 与现行 `contradictions[]` 差异行数 + veto_buy）、`_render_shadow_sell_source`（读 `portfolio_decision.json` 的 `sell_source_shadow`，展示 mode/缺口/计划减仓/宽限跳过）；A5 移出影子段（正常态进 Node 6 正文），仅 `_render_glue_degraded_note` 在 `glue_status≠ok` 时留一条降级观测。③ **🧭 市场结构去重**——`_write_reasoning_md` 的 🧭 段改为仅当 `market_theme` 非 None（4b 转正）才升格主正文「市场结构（Phase 4b，已启用）」，影子态改由 🧪 承载，消除双处重复。④ **读数起算注**——🧪 段头新增 `SHADOW_READING_BASELINE_NOTE`，标明 2026-09-29 前 4b/3b/4.1 影子读数无效（P0 #1 修复前 `run()` 崩）。⑤ 删除因重构而孤立无调用者的 `_load_board_code_to_name`。测试：`tests/test_shadow_reasoning.py` 由 11→17 例（五条线渲染/缺失降级/头注 + A6 转正子块 + `_write_reasoning_md` Phase 1 注入回归 + 重跑不堆积扩展至五线）；回归 `test_phase2_audit_fixes + test_node6_glue + test_aggregator_run_smoke` 43 passed。文档：`CLAUDE.md`/`docs/AGENTS.md` GOT 运行规范「影子模式输出总结」示例由 A6 影子改批 5 影子线（sell_source 等），持久化表 `reasoning.md` 行同步五线口径。

- [修复] GOT **批次 5 审查修复收尾（2026-09-29，零行为变更·影子链路可观测性修复）**：针对 `ULTRA_REVIEW_20260928_batch5.md` 的三项发现收尾。① **Critical #1**——`src/got/nodes/aggregator.py` 顶层补 `import os`（原 `run()` 内 L326/L397 裸 `os.getenv("GOT_DISAGREEMENT_SHADOW"/"GOT_MARKET_THEME_SHADOW")` 因缺模块级 import 抛 NameError，使 **3b/4b 两个影子字段自 0928 合入以来从未真正产出**——run() 必崩；当时域回归 86 passed 未捕获因全仓无 run() 级测试）；② **Warning #3**——`src/got/market_theme.py` `_classify_phase` 删不可达 `unknown` 死分支（无效数据已由 `build_theme_context` 的 unknown兑底路径整体承担，本函数三互斥分支穷尽）；③ **Warning #2**——新增 `tests/test_aggregator_run_smoke.py`（仓库首个 `AggregatorNode.run(replay_mode=True)` 主路径冒烟测试，断言产出 UnifiedContext 且 3b `conflict_summary_shadow`/4b `market_theme_shadow` 非空——修复前必失败 NameError，作防回归锥）。④ **建议 #4**——`src/services/stock_screener.py` Round 2 收口处（`_apply_capital_floor` 后）补 `funnel_trace["r2"]`，修正 D-3 归零率画像缺 Round 2 出口（现仅 r0/r1/r1_5/r3/r4）；纯增量、`round_survival_counts=dict(trace)` 拷贝不破既有断言。⑤ **账本 #6**——`test_disagreement.py` 实测 14 例（commit/backlog 原记 13）→ backlog §5 批 5 行 13→14；并登记批 5 影子读数 0929 前作废重起（backlog §5 尾注）。**⚠️ 此前（≤ 0928）任何归档的 3b/4b 影子字段均为空/不存在，不可作 4b/3b 转正的标定读数输入**。测试：`test_aggregator_run_smoke + test_market_theme_step2 + test_disagreement + test_got_funnel_stats` 30 passed。

- [修复] GOT backlog §2 两个**小尾巴**（搭批 5 后捎带做，2026-09-28，零行为变更）：① **P1-1 持仓名缺失/等于代码兜底**（`src/got/nodes/portfolio_decision.py`）——新增静态方法 `_resolve_holding_name`，`_score_holdings` 两处消费点（L567/L598）当 `name` 为空串或等于 `symbol` 时走离线股票索引 `src.data.stock_index_loader.get_index_stock_name`（进程内 lazy 缓存、无网络；实测 600250→南京商旅 / 605566→福莱蒽特）兜底，不可用则回退原行为返回 symbol；**仅消费侧兜底、不动 `PortfolioService` 源头**（源头被 webui 共享），`name` 无计算副作用（仅展示/日志/归档）；新增 `tests/test_holding_name_fallback.py` 5 例（monkeypatch 不耦合数据文件）；R-C3 锤点逐位不变（`verify_glue_metrics.py --date 20260910` 0 FAIL）、portfolio 域 67 passed。❗ 计划原写“查 `a_share_codes.json` 代码→名称映射”经核实不成立（该文件为纯代码清单、无名称），改用现存离线索引。② **P1-3 热股 `mention_source` 标注**——核实已于 `sentiment_analysis.py:805`（eastmoney）/`:836`（xueqiu）+ 常量 L50-51 + `_format_mention_display` L843-847 落地，**数值不动**，本项无需新代码、仅账本转「已落地」。账本：backlog §2 P1-1/P1-3 行转已生效 + OPEN-D8 标结 + 统计行 10→12 项了结。

- [新功能] GOT 待实施项排期轮·**批 5（后续排期）落地（2026-09-28，全部影子态·零决策改动）**：① **Phase 5b TickFlow 能力探测**（只读，`TICKFLOW_API_KEY` 已就位）——SDK 0.1.25 实测证 **TickFlow 不能作 4b 板块排名源**（概念 universe=0、`get_by_universes`/`klines.batch`=PermissionError 套餐权限所限；`quotes.get_by_symbols` 个股实时可用），结论落 `verification.md` §7，mootdx 先行定案（拍板②）。② **B 层会前取证 OPEN-B4**（git 考古）——`关注指数 ×1000` 追至 commit `8ad119b`（提为 `EM_ATTENTION_SCALE` 常量并首标 B4），来历=东财原值 0~100 为塞 int 字段 ×1000、展示侧已 /1000 还原→**判定保留**（backlog §1.B）。③ **Phase 4b mootdx 板块排名 → MarketThemeContext Step 2**（`src/got/market_theme.py` `build_theme_context`，`get_board_quick_change` 批量自算禁逐板 kline、三时框同向判 phase、失败回退 unknown 零行为；builder 调用落 `orchestrator.py`——AggregatorNode 无 fetcher；`aggregator.py` 组装段 `GOT_MARKET_THEME_SHADOW`/`_ENABLED` 三态消费 + `schemas.UnifiedContext` 两观测字段 + `archive.py`「🧭 市场结构」段；相关性验收 `.review_tmp/cp4b_correlation_acceptance.py` 诚实 SKIP=enabled 转正前置；`test_market_theme_step2.py` 11 例）。④ **Phase 3b 分歧摘要 + RiskOverridePlan**（新建 `src/got/disagreement.py`：本地基座复用 `AgentOpinion`/`normalize_decision_signal` + SkillAggregator 式加权共识 + 11 类冲突分类学（§8.2 显式 7 类 + 同维度补全 4 类标 TODO）+ `memory=None` 退化等权；`aggregator.py` 矛盾检测段旁挂 `GOT_DISAGREEMENT_SHADOW`（默认开·落 `conflict_summary_shadow` 观测不改既有检测/仲裁）/`_ENABLED`（`decision_path_hint` 注入 T3 仲裁 prompt）；`portfolio_decision.py` `_allocate` Step 2 注入 RiskOverridePlan（enabled+veto_buy→`buy_budget=0` 与 `over_limit_forced` 并列·`risk_override_shadow` 留痕）；`test_disagreement.py` 13 例）。⑤ **Phase 4d 前置两小批**：`stock_screener.py` 5 轮漏斗补 `funnel_stats` 归零率观测落 GOT 归档（D-3 基线首次可落盘·`test_got_funnel_stats.py` 4 例）；`.review_tmp/cp4d_snapshot_connectivity.py` 取证 `79.push2.eastmoney.com` 当前**可达未封**（hotspot 因子无需降级，`verification.md` §7.1）。所有新观测字段按 #5a/raw_position_ratio 先例不进幂等/R-B8 不变组。**enabled 转正均归负责人 + 需影子期读数**（计划 §9.3）。回归：got/portfolio/node6 域 86 passed（含新增 28 例）；`verify_glue_metrics.py --date 20260910` **R-C3 十锚点逐位一致 PASS**（exit 2=0 FAIL/1 SKIP，与历史口径一致）。

- [新功能] GOT 待实施项排期轮·批 1–4 落地（2026-09-28，主战场 Phase 5c 行为变更批）：① **批 4.1 OPEN-1 W2/W1 卖出来源**（`src/got/nodes/portfolio_decision.py` `_allocate` 新增 **Step 1.5 组合层超限强制减仓**）——Step 1 个股无卖出但组合市值仍超 `position_limit` 时按 composite_score 升序削最弱持仓回落至上限（补 W1「超限无减仓路径/不减反买」），复用 Fix 3 宽限（消费 `UnifiedScoringResult.sector_drift_grace` 标记，不解析 reasoning 串→不触 W4，防 688598 式误卖）；**三态开关**：`GOT_SELL_SOURCE_ENABLED`（生效，默认关）真减仓 + Step 2 `over_limit_forced` 前置短路归零预算抑制回补；`GOT_SELL_SOURCE_SHADOW`（影子，默认开）每日跑减仓检测、落新观测字段 `PortfolioDecision.sell_source_shadow`（`{mode, excess_value, planned_trims, grace_skipped_symbols…}`）但不写 allocation/不改决策→逐字节零改动；两者皆关整体跳过还原旧行为。② **CodeReview 后修复三项计算缺陷**：Critical 重复卖单（Step 1.5 未排除 Step 1 已 sell/reduce 标的且取原始股数 → Step 1 维护 `remaining_qty` 真相）；零股负股数（去 `max(100,…)` 改整笔清仓）；W1 抑制被整手向上取整尾差击穿（`elif` 改前置短路）。③ **批 3 观测落盘**：`raw_position_ratio` pre-clip 原值（schemas + `_assess_risk`）；**批 4.5**：`aggregator` 家族梯队 `zip` 前长度错配金丝雀（OPEN-4 X1 同型防御，零行为）。④ **批 2 取证**：只读脚本扫 48 归档日 + OPEN-C3 死分支决策简报 `docs/got/decision_brief_openc3_20260928.md`（建议删分支，待拍板）；5c 样本 6<10 → 批 4.2 DEFER。测试：新增 `tests/test_sell_source_over_limit.py` **11 例**（生效 3 + 评审边界 4 + 影子 2 + 全关 1 + 默认影子 1）；回归域 421+59 passed（失败均判环境/既有）；R-C3 十锚点逐位一致；幂等静态形状 A1–A6 PASS。账本：`backlog.md` OPEN-1/Phase 5c 登记 + 四条修订尾注；未修 Warning（`.env.example` 录入、W3 聚合口径转正硬前置）已登记归负责人。

- [新功能] GOT C-P13 阶段1 接地式召回-验证管线（`src/got/glue_pipeline.py`，仅 `--got-replay` + `GLUE_PIPELINE_ENABLED=true` 生效，生产链路零影响）：L1 检索接地（dashscope embedding + cosine top-K 候选有界化，按日缓存 `data/got_daily/_cache/l1_embed_*.json`）→ L2 逐事件受约束生成（board_code 限 top-K 白名单 + 证据 span 存在性硬护栏）→ L3 CoVe 独立验证剪枝（批量 yes/no，失败保守保留）→ 护栏（去重/单事件上限 6 条/重复循环整日作废 day_void）；只替换 relations，`GlueRelation` schema 不变，strengths/families 仍由现行默认 prompt 产出；留痕 `prompt_variant="p"` + `pipeline_status`，trace 落 `_replay/pipeline_l1l3/glue_pipeline_trace.json`；配套 `scripts/cp13_l1_smoke.py` 冒烟闸门（0910 漏挂/0903 摊派两病例）、`export_glue_family_audit.py --variant`、`tests/test_glue_pipeline.py`（35 测试）；依据 `docs/got/archive/cp13_grounding_pipeline_research_20260926.md` §5.3 最小落地路径。
- [改进] GOT C-P13 阶段1 落地后续（2026-09-27）：① L1 冒烟闸门实测（`scripts/cp13_l1_smoke.py`）：K=12 时 0903 房贷病例 top-K 混入 cosine 0.251 噪声「短剧游戏」，K=8 两病例全过且 0910 漏挂病例不误伤 → `GLUE_L1_TOP_K` 默认 12→8（失败根因是 K 长尾噪声非文本构造，未动 build_*_text）；② 9 天矩阵跑批（pipeline_l1l3 变体）全 ok：M2 均值 39.0%（vs a 35.5% / b 46.3%，验收点 2 门槛 41.7% 未达但 b 的 46.3% 几乎全由 0902/0903 两个 100% 摊派日撑起（该两日 b 低置信 52%/63%）；同口径剔除两日后 b→30.9%、pipeline→31.4%（p 由落后 b 7.3 pp 转为反超 0.5 pp），且不管剔不剔除 p 均高于可信基线 a（逐日 p vs a：胜 6/负 2/平 1，p≥a 于 7/9 天）。**2026-09-28 负责人裁决=选项 A：M2 单指标门槛与 M3 结构性冲突（b 为摊派反面教材不宜定线），按诚实口径判验收点 2 ✅；阶段六条全过→阶段1 结项；后续跑批改用双指标 M2≥a 且 M3≤a）；低置信占比 11.1%（b 40.8% 的 1/4）；病例级断言全过（0910 含船舶/航运概念，0903 房贷事件仅 3 条无摊派）；CoVe 剪枝 624→333（-46.6%）；调用实测 659 次/~811k tokens（L2 583 + L3 67 + base 9，略超预算 10%）；结论 `docs/got/archive/cp13_stage1_matrix_20260927.md`；③ 修复 `verify_glue_metrics.py --compare` 双保险校验对 `pipeline_*` 变体的「疑似错跑」误报（留痕按设计为 "p"，不适用目录尾缀口径，variant_a/b 口径不变）；④ M3 人工终审（2026-09-28 负责人回填）：10 条分层抽样（种子 42，从跑批定稿产物重新生成并逐条核对一致）——真实 6 / 牵强 4 / **无关系 0**，**M3=0%**（计门槛≤5%，优于 variant_a 抽检发现的凭空捏造），验收点 3 判✅ ；高置信端 conf≥0.70 四条全真实、牵强 100% 落 conf≤0.50 → confidence 锚定校准有效。阶段2 靶子（新发现）：4 条牵强均为「弱传导/方向错」非硬误配（油价→航运方向搞反 / 日本AI·FOMC 跨境弱传导 / 脑机→半导体过度延伸），均指向 L5 方向/幅度校准。
- [决策] GOT C-P13 阶段2 收口休眠（2026-09-28）：realized 收益率回算标定 confidence「红线 0.6」= **证伪关闭**（19 天回放池 + `cove_detail` 埋点反事实扩样至 1521：glue vs 候选全集 alpha≈+0.10%/−0.09%、conf rho −0.06 倒U、CoVe kept−dropped realized 仅 0.04pp/rho −0.019）。**关键纠偏**：≠ confidence 无用——阶段1 人工 M3 显示 conf≥0.70 全真、牵强全≤0.50，收益率与归属正确率是两个对象，将来重标须用 M3 口径。**阶段1 接地管线休眠**：合入保留（`33d8e36`）+ 双与门 replay 不解除、不转正、不删除；`glue_pipeline.py` 新增 `cove_detail[]` 埋点（仅 replay trace 落盘、schema 不变、生产零影响）供解冻复评；对后续流程零阻塞（不碰生产 glue、不碰 Node6 仲裁 0.6 门、不阻塞埋伏分支且依赖反向）。详 `docs/got/archive/cp13_phase2_closeout_20260928.md`。
- [chore] 移除 `.github/copilot-instructions.md` 和 `.github/instructions/` 目录，简化 AI 配置入口；同步更新 `scripts/check_ai_assets.py` 校验逻辑，去掉 copilot 和 symlink 相关检查。
- [新功能] GOT T1/T1-H 影子模式产物落盘（`shadow_t1.json` / `shadow_t1h.json`）：与 A5 的 `agent_glue.json` 对称，由 `portfolio_decision._save_shadow_records()` 落盘到 `data/got_daily/{date}/`，供 `archive._render_shadow_section()` 渲染到 `reasoning.md` 末尾的「🧪 影子模式观测」段落；零 schema 改动（不碰 `schemas.py`，不影响 R-B8/R-C7 逐字段 diff）；重跑不堆积（`_append_phase2_reasoning()` 覆盖写机制天然处理）；「未执行」必带原因（开关关闭 / 无可审目标 / 具体哪只持仓不满足哪条筛选条件）；11 个测试用例覆盖（含重跑不堆积、「未执行+原因」分支）；端到端验证通过（0915 归档，T1 否决广立微 301095，T1-H 未执行）。
- [新功能] GOT Node 6 Agent 语义粘合层（agent_glue_plan_0903 Phase 0+1）：`_agent_semantic_glue` 单次全量 LLM 调用输出事件→板块关系图 + 板块强度 + 家族归属（受控词表 12+1），F1 八行降级契约完整实现（S1-S7），影子模式对照日志，`--got-replay` 回放 harness，`_collect_sector_candidates` 候选收集独立抽取，P0-B 数据完整性字段级检查（lifecycle/catalyst/crowded_families/capital_flow/hot_stocks_sectors），护栏空转 WARNING 日志。
- [新功能] Phase 2 §3 TencentFetcher（`data_provider/tencent_fetcher.py`）：腾讯免费指数 K 线 fallback 源，P1 优先级，接入 `DataFetcherManager` 并经 `_filter_fetchers_index_only` 门控为仅指数可用（个股 fallback 时排除，避免 pct_chg 口径漂移），注册顺序置于 akshare 之前，Node 1 大盘分析 5 指数 K 线实际第一可用源（mootdx/efinance 被 A 股指数路由跳过，akshare push2his 间歇失效）。
- [新功能] Phase 2 §4 指数注册表 + 身份解析（`src/got/index_registry.py` + `data/got_index_registry.json`）：`parse_analysis_target()` 消除裸码 000001 双身份歧义（裸码→股票，带后缀→指数），`matches_index_code()` 跨格式匹配（`sh000001` / `000001.SH` / `000001.SS` 等价），`CN_PROFILE.mood_index_code` 由 `"000001"` 改为 `"000001.SH"`。
- [改进] GOT Node 6 LLM 调用可观测性：`_call_llm_async` 加 `call_type`/`timeout` 形参，4 个调用点（refine/nominate/arbitrate/override）全部接入 `persist_llm_usage`，`get_llm_usage_summary(by_call_type)` 可按调用类型归因 token 消耗。
- [改进] GOT Node 6 `_build_data_quality` 字段级完整性自检新增 5 项（`lifecycle_unavailable`/`catalyst_events_empty`/`crowded_families_empty`/`capital_sector_flow_empty`/`hot_stocks_sectors_empty`），`missing_fields` 非空时显式 WARNING 日志（护栏空转可见）。
- [测试] GOT Node 6 新增 `tests/test_node6_candidates.py`（13 测试：4 路候选收集 + 去重 + 0903 基线 24 条）和 `tests/test_node6_glue.py`（17 测试：F1 降级/事件去冗余/prompt 组装/S3-S5 校验/调度/0903 回放），30 个 Node 6 测试全通过。
- [测试] Phase 2 新增 `tests/test_tencent_fetcher.py`（20 测试：symbol 转换/K 线解析/标准化/边界场景/5 指数端到端/manager 集成）和 `tests/test_index_registry.py`（26 测试：注册表查找/消歧/跨格式匹配/冲突消歧），46 个 Phase 2 测试全通过。
- [新功能] Phase 3 §15.2 审查专用 Agent profile（`src/agent/factory.py`）：`build_review_executor(review_type="t1"|"t1h")` 按审查类型装配独立 system prompt（T1 候选股终审 verdict=confirm/warn/reject；T1-H 持仓审查 verdict=hold/add/reduce/exit，confidence 0-1 非百分制）、独立温度 `AGENT_REVIEW_TEMPERATURE=0.2` 与独立用量归因 call_type（agent_review_t1/agent_review_t1h）；temperature/call_type 经 `executor → runner.run_agent_loop → llm_adapter.call_with_tools` 四层接线透传（均为带默认值的可选关键字参数，向后兼容），审查层与全量 Agent 的词表/参数彻底隔离，修复 T1-H 复用 T1 词表导致降档惩罚空转。
- [新功能] Phase 3 §15.4 T1-H 持仓审查（`src/got/nodes/portfolio_decision.py`）：按「埋伏持仓 > P4 退出信号 > 低分浮亏」优先级选目标（上限 `AGENT_HOLDING_REVIEW_MAX=3`），LLM 输出 hold/add/reduce/exit + confidence（安全转换 + clamp 到 0-1，低于 `AGENT_HOLDING_REVIEW_MIN_CONFIDENCE=0.6` 忽略），reduce/exit 对 composite_score 施加降档惩罚（系数下限 0.6）；审查 prompt 注入持仓数量与「买入以来相关标的新闻」（`news_context.major_events` 按 symbol/name 过滤，含标题/时间/情感/影响级）；影子回滚缓存原始分精确还原，消除双重 rounding 漂移。
- [新功能] Phase 3 §15.3 T1 候选股终审（`src/services/stock_screener.py`）：`_parse_agent_verdict_v2` 支持嵌套 JSON 解析 verdict=confirm/warn/reject + notes + decision_signal，reject 标记 `agent_decision_signal="demoted"`；影子模式（`AGENT_STOCK_REVIEW_SHADOW=true`，默认）跑审查并输出对照日志但不执行降级；转正后 verdict/notes/decision_signal 落入 `_save_got_result` 的 stock_data 白名单持久化，供下游看板与历史回溯。
- [修复] Phase 3 §14.1 A1/A2 AgentMemory（`src/agent/memory.py`）：A1 avg_confidence 不再硬编码 0.6，改由 `_compute_avg_confidence` 从 `AnalysisHistory.raw_result` 的 confidence/overall_confidence 计算真实均值（解析失败或缺字段跳过，无数据回退中性 0.5）；A2 `_compute_entry_outcomes` 修正取数链路——复用 `_fetch_outcome_klines` 一次性 K 线（同标的多条记录共享，避免逐条网络请求），5 日窗口 4-7 天 / 20 日窗口 18-22 天，bullish/bearish/hold（波动 <3% 视为正确）方向正确性判定，修复此前 import 错误与取数方法缺陷。
- [改进] Phase 3 置信度校准口径变更（W9）：`get_calibration` 的 `calibration_factor = historical_accuracy / avg_confidence`（clamp 0.5-1.5），avg_confidence 由硬编码 0.6 改为计算值后，对 `AGENT_MEMORY_ENABLED=true` 的 opt-in 用户校准分母随之变化——有历史数据时用真实均值，无数据时 0.5（factor=2×accuracy，语义中性）；样本不足 `min_samples` 仍返回 factor=1.0 不校准，未启用记忆的用户完全不受影响。
- [文档] Phase 3 新增/相关环境变量（默认关闭 + 影子，opt-in）：`AGENT_STOCK_REVIEW_ENABLED`（T1 终审开关，默认 false）、`AGENT_STOCK_REVIEW_SHADOW`（T1 影子，默认 true）、`AGENT_HOLDING_REVIEW_ENABLED`（T1-H 持仓审查开关，默认 false）、`AGENT_HOLDING_REVIEW_SHADOW`（T1-H 影子，默认 true）、`AGENT_SEMANTIC_GLUE_ENABLED`（Node 6 glue 开关，默认 false）、`AGENT_SEMANTIC_GLUE_SHADOW`（glue 影子，默认 true）；非 env 审查常量 `AGENT_REVIEW_MAX_STEPS=4` / `AGENT_REVIEW_TEMPERATURE=0.2` / `AGENT_REVIEW_TIMEOUT_S=30` / `AGENT_HOLDING_REVIEW_MAX=3` / `AGENT_HOLDING_REVIEW_MIN_CONFIDENCE=0.6` / `LLM_CALL_TIMEOUT_S=60`。
- [测试] Phase 3 新增 `tests/test_phase3_glue.py`（31 测试：§15.2 review profile 构建 / §15.3 T1 JSON 解析 + 影子 / §15.4 T1-H 降档惩罚系数 + 目标选择优先级 + P4 催化剂快照注入 / §14.1 A1 avg_confidence 无数据回退 + 真实均值正路径 / A2 outcome 回填 bullish/bearish/hold 正路径），31 个 Phase 3 测试全通过。本轮 agent_glue_plan_0903 三阶段总计 **107 个测试**全通过（Node 6 30 + Phase 2 46 + Phase 3 31）。
- [改进] Phase 3 §15.4 T1-H P4 催化剂快照增强（`src/got/nodes/portfolio_decision.py`）：`_build_holding_review_prompt` 由仅注入 `ambush_position_type`/`ambush_exit_signal` 两个代号字段，升级为优先注入完整 `AmbushPositionSnapshotRecord` 快照（催化剂事件标题/事件日期/影响等级★/共识预期/建仓日期与价格），并补上此前遗漏的 `ambush_exit_reason`（人可读退出理由）；`_score_holdings` 将 `ambush_snapshot_map` 暴露为 `self._ambush_snapshot_map` 供 Step 2.5 审查复用，快照不可用时回退 holding 代号字段（getattr 防御，不新增异常路径）。新增 5 个 `TestT1HCatalystSnapshot` 测试覆盖完整注入/退出理由/回退/未初始化防御/非埋伏持仓。
- [文档] Phase 3 §14.1 A2 后台回填任务排期（`docs/agent_glue_plan_0903.md` §11.2）：明确当前 outcome 回填为惰性触发（`get_stock_history` 读取时回填，闭环已补全），独立后台调度任务（覆盖「从不被查询的标的」）因需接入调度器、改动较大，列为单独排期项（非本轮）；同步修正 §11.5 测试覆盖汇总（补 `test_phase3_glue.py` 行，合计 92→123）。
- [修复] GOT 编排器交易日检查恢复生效：`_check_trading_day` 原 `import TradingCalendar` 引用的类在 `src/core/trading_calendar.py` 中不存在，恒抛 `ImportError` 且被 `except ImportError` 静默吞掉，周末/节假日照跑；改用模块级 `is_market_open()`（与 `src/core/pipeline.py` 同口径），并把 `try` 收窄到只包 import；`main.py --got` 新增 `except NonTradingDayError` 显式按「正常跳过」处理（`logger.info` + 退出码 0），不再被兜底 `logger.exception` 报成执行失败。注意：修复后非交易日不再运行 GOT。
- [修复] GOT Phase 2 买入分配整手取整归零不再静默：`quantity = int(per_stock_budget / price / 100) * 100` 对高价股取整后为 0 时，原实现 `if quantity > 0:` 无 else 分支，候选被无声淘汰且其预算不回填（0914 实测方邦股份 ¥145.36 买 100 股需 ¥14,536 > 单只预算约 ¥11.7k）；现改为守卫子句提前 `continue` 并输出 WARNING，明确「100 股需 X，超出单只预算 Y」。
- [文档] GOT 文档整合：13 份 GOT 文档整体归档至 `docs/archive/got/`（文件内容与节号一字未改，代码/脚本中的 `§X` 引用仍可解析），`docs/` 根目录只保留两份现行清单——`got_shadow_verification.md`（待验证 / 影子期：三条影子线可数性、M1–M6 定义与实测值、R-B/R-C/R1–R6 待回放红线、A-P0-4 已实测有效）与 `got_backlog.md`（待实施：四层未落地项、0902 报告 15 项处置表、OPEN 汇总、A6 转正与 17 项退役、后续 Phase、需裁决事项）。新增 `docs/archive/got/README.md` 归档索引与「已实施并完成验收」清单，防止归档后失联。
- [chore] 同步 GOT 文档下沉后的路径引用：`src/got/`（archive/catalyst_config/index_registry/intelligence_service/schemas/nodes/aggregator 共 13 处，含 `aggregator.py` 一处运行时 f-string 提示文案）与 `scripts/`（`check_audit_claims.py` 的两处硬读盘路径 + 5 个验收脚本的出处常量）全部指向 `docs/archive/got/`；`intraday_gate_plan_0907.md` 的 12 条相对链接由 `../` 补为 `../../../`（下沉两级后到达仓库根的深度变化）。新增 `scripts/export_glue_family_audit.py`（M3 家族误配率人工抽查工具，702 行）纳入版本控制。
- [新功能] GOT A6 阶段 1·接线（2026-09-17）：将 Agent 语义粘合层（glue）输出接入 Node 6 主链路——8 交易日影子期实测相对规则侧 M1 +25.5 pp / 13.1×、M6 +31.4 pp，M4-b/c 7/7 PASS，项目负责人裁决直接转正；0915 已归因 Node 2 上游服务器故障（`sector.frame_groups=0` → glue `S2_parse_failed`），不计入降级样本。新增 `GOT_A6_ENABLED` 环境变量（默认 true）作为主链路门，`AGENT_SEMANTIC_GLUE_SHADOW` 默认翻为 false。4 个消费点：① `_build_sector_intel` 新增 Pass 1.5 `_apply_glue_override_to_raw_data`，以 `_GLUE_CAPITAL_READ_MAP` / `_GLUE_NEWS_READ_MAP` / `_GLUE_SENTIMENT_READ_MAP` 三套词汇直接替换 `composite_score / capital_check / news_check / sentiment_check / confidence`，`glue.degraded_sectors` 命中板块保留规则侧兜底；② `run()` 中 `refine_events_map` 改走 `_glue_aware_events_map`（`code_to_name` 反查 + 未覆盖事件回退 T5 扇出）；③ `_llm_refine_sectors` 在 `self._a6_glue_active=true` 时跳过 adjustments 分支（narrative / cross_sector_risks 保留），避免与 glue 双入口改同一 `classification` 字段；④ `_synthesize_mainline_endurance` 新增 `glue` 参数 + family_name 三级优先（`glue.families[code].family` → `intel.frame_group` → `intel.sector_name`）。E2 公式：`MainlineEndurance.family_resonance` 分母从固定 50.0 改为当日家族 `std_dev` P90 自适应归一化（P90=0 时 fallback 50.0），使 A5 实测「24/24 全 None」的字段真正具备区分度。归档 schema：`UnifiedContext` 新增 `glue_consumed: bool` + `glue_status: Optional[str]` 两个字段，**不删除**任何现有字段（阶段 2 才做 §6.1 17 项退役）。回滚：`GOT_A6_ENABLED=false` 一个环境变量即回到影子期行为（glue 归档不消费），零代码回退；三件套 `_cross_check_*` 继续计算但不写入 SectorIntel，保留僵尸期作为 0915 类上游故障的最后一道兜底。新增 `tests/test_a6_wiring.py` 9 个用例（C1 glue 直接替换 + C2 degraded_sectors 保留规则侧 + C3 A6=false 与影子期基线逐字段一致作为行为面验收锚点、替代原 R-C7 + C4 S2_parse_failed 四调用点全回退不抛异常 + C5 E2 P90 自适应 vs 固定 50 分母对比），回归 `pytest tests/ -k "node or got or portfolio or ambush"` 293 passed。详见 `docs/got_shadow_verification.md` 顶部「2026-09-17 状态变更」与 `docs/got_backlog.md` §4。
- [chore] GOT A6 阶段 2·规则评分模式退役（2026-09-26）：删除阶段 1 保留的僵尸三件套与规则评分模式（`docs/got/archive/agent_glue_plan_0903.md` §6.1）。负责人两项裁决：① **放弃逐板块兜底**——漏命中/降级板块不再保留规则值，改 **neutral 中性垫底**；② **提前跨过 §4.2 条件 2**（T+3 样本未收齐即执行），依据是全局回退本已失效（`aggregator.py` `run()` 入口对 false/None/非 ok/degraded 全 `raise`），删除不损失活的安全网。`_compute_sector_scores` 精简为「tech + lifecycle 修正」，`capital_check`/`news_check` 返回 `neutral`、`sentiment_check=missing`、detail 文案「规则模式已退役」。删除 `_cross_check_capital`/`_cross_check_news`/`_cross_check_sentiment`、`_lookup_capital_flow`/`_divergent_threshold`/`_parse_capital_flows`/`_extract_news_keywords`、`_NEWS_*`（7）/`_CAPITAL_DIVERGENT_*`/`_CAPITAL_ALIGNED_MIN_YI`/`_UNRELIABLE_MATCH_TYPES` 常量、`_build_capital_divergence_info`（D-P1 诊断，`UnifiedContext.capital_divergence` 恒置 `None`）、`_detect_glue_capital_mismatch`（Q8-A2）、`_llm_refine_sectors` 的 `adjustments` 分支；Pass 1.5 删 `rule_capital_check`/`rule_capital_detail` 留存，`schemas.py` 删 `SectorIntel.rule_capital_check`/`rule_capital_detail`/`family_resonance`。**接线后重判为保留**（防误删，§6.1「删」作废）：`_match_sector`/`_GENERIC_ROOTS`（`_lookup_flow_map`→吸筹确认闸门仍用）、`_parse_sector_level_flows`/`_CAPITAL_CHANNEL_TERMS`（`_detect_family_capital_mismatch` 仍用）、`_group_events_by_sector`/`_lookup_sector_events`/`_extract_hot_sectors`（glue 事件供给 + 埋伏/蓄水池仍用）、`MainlineEndurance.family_resonance`（E2）。`GOT_A6_ENABLED`/`AGENT_SEMANTIC_GLUE_ENABLED`/`SHADOW` 保留为 fail-fast。测试：删 `test_node6_glue.py::TestGlueCapitalMismatch`、改写 `test_a6_wiring.py` adjustments 断言。回归 got 域 248 passed / 0 failed（全量 3704 passed，25 failed 均为无关环境项：docker/nacl/pypinyin/network/config）。单 commit 可回退。详见 `docs/got/backlog.md` §4.1/§4.2、`docs/got/archive/agent_glue_plan_0903.md` §6.1 修正注记。
- [文档] R-D1 收口复算（2026-09-26，A6 阶段 2 后）：重跑 `scripts/check_audit_claims.py`，`unverified` 基线 10 → 实算 **7**，逐项归属核实。原「待退役」4 项处置定案：P0-1（divergent 改由 glue 产出，0917 起 6 日触发，死分支症状不复存在）与 P0-2（confidence 塌陷解除，0924 实测 0.7 占比 10.7%）随退役消解；P0-5 自愈（近 12 日埋伏池规模 0–5，池空不可复现）；唯 **P2-1** 原「随 T5 对齐层退役消失」归属作废（接线后重判该层保留、`affected_sectors` 活消费），转 `backlog.md` §7 裁决 #8 待拍板。§8 #8（R-D2）同步刷新四子计数实算（divergent 6 / 饱和 8 / 死信号 2 / 死分支 1✅），维持「输入为过期基线、属文档缺陷非代码问题」定性，脚本 exit 1 系基线对照语义。详见 `docs/got/backlog.md` §2/§7/§8 收口注记。
- [修复] GOT §7.1 regime 状态机振荡修复（2026-09-26）：两层缺陷——① 确认死锁：0910 起 raw regime 在 bull↔bear 日日交替，`transition_pending` 每日被换挂，情形 2（raw==pending）永不触发，输出被永久钉死 `transition`（dwell 2→12）；② 计数器语义：`transition_count` 统的是 20 日窗口**输出值**相邻 diff 而非确认切换事件，被钉住后只减不增（OPEN-C1 时间炸弹实装形态），仓位 ×0.8 震荡惩罚与 `range_reversal` 信号均建立在失效输入上。**修法**（负责人拍板：迟滞豁免 + 事件列表计数 + 独立回滚开关）：`MarketRegime` 新增 `pinned_days` / `transition_events`（默认值向后兼容），`load_previous_regime_state` 回读两字段，新增 `seed_regime_transition_events`（旧输出 diff 作迁移种子，旧归档无事件记忆时回退、迁移期读数与旧口径一致）；`_apply_regime_state_machine` 新增情形 3-豁免——连续钉住 `REGIME_PINNED_EXEMPT_DAYS=3` 日后直接确认当日 raw（脱钉后纯交替信号被迟滞抑制，持续两日真实信号仍走情形 2，非强锁）；`transition_count` 改统确认切换事件（事件日期随归档落盘、20 日窗口计数、同日重跑幂等不重复 append）。回滚：`DISABLE_REGIME_OSCILLATION_FIX=true` → 无豁免 + 输出 diff 计数 + 不记事件（关闭后从迁移种子重建）。**审核修正（同日代码复审）**：① 迁移种子改为**播种进事件列表**续记、窗口判定改日期区间（历史最早日..今日含当日）——修复升级后首个真实切换日计数一夜归零（5→0）静默翻转 ×0.8 惩罚与 `range_reversal`；② 合并原「2-豁免/3-豁免」两条行为一致的分支，新增归档字段 `regime_switch_exempt` 标记豁免单日强切（与两日确认区分，供 R2 复盘）；③ 空列表照落保留「已迁移」标记（与 `None`=未迁移区分）。测试：`tests/test_node6_regime_state.py::TestRegimeOscillationFix` 8 例（含 0910 交替链路复现/事件窗口与幂等/种子续接/迁移空列表/开关回退），got 域回归 405 passed。详见 `docs/got/backlog.md` §7 裁决 #7 / §7.1 实施记录。
- [文档] 文档结构重排（三区整合）：`docs/` 顶层只保留 INDEX/CHANGELOG/AGENTS/CLAUDE 与资源目录；旧项目（非 GOT）基底用户文档 28 篇整体移入 `docs/guide/`；GOT 现行文档收进 `docs/got/`（`backlog.md` ← `got_backlog.md`、`verification.md` ← `got_shadow_verification.md`、`architecture.md` ← 根目录 `agent_cot_design.md`、`plans/` ← 根目录 `plan.md` 与 `选股胜率优化_PLAN.md`）；`docs/archive/got/` 平移为 `docs/got/archive/` 并收入 `docs/custom/` 中 3 篇已完结 GOT 文档；非 GOT 已完结计划入 `docs/archive/plans/`，`docs/custom/` 撤销。全仓路径引用同步修复：`src/got/`、`scripts/`（含 `check_audit_claims.py` / `backtest_got_timeseries.py` 读盘路径）、`tests/` 注释、根 README/CLAUDE 死链（`AGENTS.md` → `docs/AGENTS.md`）。修复 `docs/INDEX.md` 收录失效（重写为三区导航全量索引）。收口 `docs/got/backlog.md` §8 文档层缺陷 #1/#2/#3/#5。
- [新功能] GOT 选股胜率优化 4 步（`docs/got/plans/win_rate_optimization_plan.md`，9 月回测驱动：T+3 胜率 28.1% / 负 alpha -1.67%）：针对 4 个已定位根因落地 4 处修复，每步带独立 env 开关（默认开）可一键回滚。① **Step 1**（`src/got/nodes/aggregator.py` `_compute_market_regime`）Node 6 消费 Node 1 `attack_power`：新增偏移项 `(attack_power-0.5)*0.2` + clip 后分类前的**双档熔断**（`<0.15` 强制 bear 触发 -5% 硬止损 / `<0.30` 把 bull 压回 transition，阈值复用 Node 2 主线裁剪阶梯），修复 bear 市场采 bull 仓位；`market_state` 缺失时 getattr 防御不熔断。② **Step 2**（`portfolio_decision.py` `_lifecycle_priority_score`）acceleration 从固定 70 分改为按 lifecycle_day 衰减（day≤3→75 / ≤7→65 / 8+→50，day 未知回退 70）。③ **Step 3**（`_score_candidates` action 判定）资金确认硬闸门 `CAPITAL_GATE=35`：capital<35 且 capital_available 时 buy_trend/buy_dip 降级为 watch（只影响 action 建议不影响排序入选，capital 缺失不触发）。④ **Step 4**（`_score_candidates` 过热降权）过热惩罚从仅乘 sector 维（25% 权重）扩展为半幅传导到 composite（`0.5+0.5*overheat_penalty`），与 Step 3 正交。新增 env：`GOT_WINRATE_STEP1_ENABLED` / `GOT_WINRATE_STEP2_ENABLED` / `GOT_WINRATE_STEP3_ENABLED` / `GOT_WINRATE_STEP4_ENABLED`（默认 "1"）、`GOT_WINRATE_CAPITAL_GATE`（默认 "35.0"）；`portfolio_decision.py` 顶部补 `import os`。
- [测试] 新增 `tests/test_winrate_optimization.py`（16 测试）：Step 1 三组 attack_power 输入（0.17→transition / 0.10→bear / None→不熔断）+ 关开关反证回 bull；Step 2 day 1/5/14 衰减 + 关开关反证回 70 + day=None 回退；Step 3 capital=20→watch / 关开关反证→buy_trend / capital=64→buy_trend / capital_available=False 不触发；Step 4 overheat=0.75→composite×0.875（对拍开关）+ overheat=1.0 无影响。回归 `pytest tests/ -k "node or got or portfolio or ambush or winrate" --ignore=tests/test_discord_platform.py` **309 passed**（基线 293 + 16；`test_discord_platform.py` 的 `nacl` 缺失为预存环境问题，与本次无关）。
- [chore] 仓库清理：删除 `scripts/` 下 73 个已废弃的开发期脚本（GOT 开发 runner、遗留回测、一次性分析/调试脚本，已被 `main.py --got` / `tests/` 取代）；删除根目录 `ths_screener_result.json`（零引用过期产物）和 `uv.lock`（无 `[project]` 段的孤儿锁文件）；`git add -f` 转正 7 个被 `.gitignore:76` 误伤的验收脚本（`verify_flow_archive/position_sizing/remote_data_sources/risk_fix/t5_alignment/ths_integration/ths_syntax`），收口 `backlog.md` OPEN #6；本地清理 `logs/`（保 7 天，释放 ~150MB）、`__pycache__/`（1181 目录）和 `data/_*` 临时文件（~3MB）。保留 `run_got_screener_v2.py`（T1 归档唯一生产路径，`backlog.md` §1.C）、`run_phase2.py`（端到端验证步骤，`ambush_split_plan.md`）、`backtest_got_timeseries.py`（Phase 5b 2026-11 待用）；暂缓删除 `backtest_september.py` / `run_phase2_only.py`（胜率优化计划收口前保留证据源）。
- [改进] GOT 三类未结项分诊·第一批零行为变更项落地（2026-09-26）：按「可直接修正 vs 需拍板」分诊，先落零行为变更的一批。① **D-P2**（`src/got/nodes/aggregator.py`）埋伏闸门漏斗 `gate_funnel` 各闸补 `skipped`/`skipped_reason`，新增 `_gate_stat` 计数唯一入口（确定、与输入顺序无关）；死闸（`hot_sectors` 空 ⇒ G2/G3）与资金流缺失（`accum_strength=="missing"` ⇒ G4）改记 `skipped` + reason、不再虚计 `evaluated`，**不改 `_run_ambush_gates` 判定逻辑**；新增 `tests/test_node6_ambush_funnel.py`（5 例：空 hot_sectors→skipped、flow 缺失→G4 skipped、决策路径 pool/reservoir/veto 快照不变、同日重跑逐字节幂等）。② **D-P12**（`src/got/nodes/portfolio_decision.py`）`sector_exposure_binding` 从已生成 `allocation` 反推补 `buy_allocation_count` + `binding_count`（命中显式 limit 的买入次数，不归一化），**仅可观测留痕、不参与决策**（接线归拍板）。③ **C-P8**（`scripts/verify_glue_metrics.py` docstring + `docs/got/archive/got_layer_c_plan_0911.md` §6.1 + `docs/got/verification.md` §3）加「红线口径声明」三项（M4 判定系数=Spearman / M4-a 独立性以 B1 修复为条件且 B1 现状仍未修 / 0.6 标待标定归 C-P13），**不改任何计算与 PASS/FAIL 逻辑**。④ 文档收口：`schemas.py` `ambush_gate_funnel`/`sector_exposure_binding` 描述同步 + `mention_source` 补 `nga_mention` 预留值说明（§7 #2 半结）；`got_layer_d_plan_0911.md` 附录 A 加基线时效注记；`backlog.md` §1.C/§1.D/§1.F/§7 行更新（§1.F.b 标 ✅ 已自愈——`shadow_t1.json` 6 日实测）。回归：定向 64 passed（含新增 5 例）+ portfolio 32 passed + `verify_glue_metrics.py --date 20260910` R-C3 十锚点逐字节一致。C-P11 回填、C-P12 变体隔离、决策简报后续批次推进。
- [新功能] GOT 三类未结项分诊·第二批（2026-09-26）：C-P12 变体隔离前置 + C-P11 回填跑批。① **`GLUE_REPLAY_VARIANT` 变体目录隔离**（`src/got/nodes/aggregator.py` `_save_glue_result`）——仅 `replay_mode` 生效、默认空不改现路径；设值时 A/B prompt 变体回放各落 `{date}/_replay/<variant>/agent_glue.json`，修复两版变体同写 `_replay/agent_glue.json` 互相覆盖；生产链路（非 replay）永不受该变量影响，守 A1「不写归档」契约。`scripts/verify_glue_metrics.py` 配套：`_replay_sample_inventory(variant="")` 参数化、新增 `_discover_replay_variants()`、R-C6 证据行仅在存在变体目录时追加（无变体时输出逐字节不变）。新增 `tests/test_glue_replay_variant.py` 6 例（变体落盘隔离/无变体回退基线/非 replay 忽略变体 + 基线盘点不误计变体/按变体盘点/变体发现）。② **C-P11 回填跑批**（负责人批准：先冒烟后批跑）——开 `AGENT_SEMANTIC_GLUE_IN_REPLAY=true` 逐日 `python main.py --got-replay` 跑 20260901/0902/0903/0904/0907/0908/0909/0910 共 8 日（真实 glue LLM 调用），8 份产物均 `status=ok` 且 md5 两两互异（非占位态），产物落 `_replay/` 不污染生产归档；`verify_glue_metrics.py` **R-C6 有效样本由 0 天→ 8 天**（旧 §4.1 占位态覆辙已避），R-C6 仍 SKIP（待 C-P12 两版 prompt）、R-C3 仍逐字节一致。backlog §1.C C-P11/C-P12/C-P13 行同步。
- [改进] GOT 决策简报拍板落地（2026-09-26，第三批后续）：负责人对七项待裁决项给出判定并同日实施。① **#3a** `signal_dates` 按日去重（`src/got/persistence.py`）——每种信号类型每日最多记 1 条（根因：`catalyst_confirmation` 在按板块循环中同日多产，0915 曾单日占 7 席），`signal_count_30d` 语义由「30 日次数」变「30 日天数」，逐板块明细仍于 unified.json 可取证；存量历史不改写随 30 日窗口自然衰减；单测 2 例（同日多板块/同日重跑幂等）。② **#4b** 4 份测试污染产物（`got_daily/20260911、20260912` 的 `agent_glue.json` 根目录 + `_replay/` 各一份）移入 `data/got_daily_backup_20260926_1700/`，含 README 留档清单（md5 与 verification.md §2.2/§4.1 互证）。③ **#5a** R-B8 不变组移除 `market_regime` 跨日状态机 5 字段（`scripts/verify_layer_b_consistency.py`）——`_collect_state_fields` 不再提取，`check_r_b8_r_b9` 对该子集单独比对并标「按设计 SKIP 不计 FAIL」，消除回放语义下的永久性假 SKIP。④ **#1a** `DEFAULT_SECTOR_ROLE` 保持 mainline（零改动，注释标已拍板）；**#6a** 不扩回放链路；**C-P13a/OPEN-C1a** 等 C-P12 A/B 样本齐后同批标定。文档同步：`decision_brief_20260926.md` 改「拍板登记与执行结果」+ 速览表更新、`backlog.md` §7 全部标已拍板、verification.md §4/§5.1 同步。回归：`test_cross_node_signals.py` 18 passed。待办不变：C-P12 文案→各跑 8 天→解锁 C-P13/OPEN-C1；R5 复测 10-11 后。
- [新功能] GOT C-P12 prompt A/B 双版回放落地（2026-09-26，D-C6 裁决执行）：出两版 glue prompt 草案并各跑 9 天 `--got-replay`，产出 M2×M3 对照矩阵作为 C-P13 红线 0.6 标定 / OPEN-C1 `transition_count` 阈值重标的样本输入。① **双版文案**（`docs/got/archive/cp12_prompt_variants_draft_20260926.md`）：A 保守版（= 现行生产 prompt + 「省略不是错误」澄清句）vs B 全量归属版（任务1 禁 unknown 强制 low 垫底 + 任务3 全量归属低置信保留 + 「禁止编造」放弃通道护栏），两版仅在「无把握时的输出策略」一个自由度上不同，输入区块/JSON schema/任务2 families/temperature=0 全共用。② **`GLUE_PROMPT_VARIANT`（a/b）+ `GLUE_MAX_TOKENS`（默认 16000）env**（`src/got/catalyst_config.py`）：均仅 `replay_mode` 生效、未知值回退默认、生产 prompt 与 max_tokens 零改动；`aggregator._build_glue_prompt` 加 `variant` 形参 + 变体文案分支，`agent_glue.json` 增记 `prompt_variant` 留痕字段（`schemas.py`，schema 增量不改形状）。③ **`--compare` 模式**（`scripts/verify_glue_metrics.py`）：按变体盘点逐日输出 M1′/M2/低置信占比/放弃事件，出 A/B ΔM2 对照矩阵 + 目录尾缀一致性校验（防错跑）+ `--m3-audit` 分层抽样预审表（种子=42，低5+高3+随机2）。④ **跑批**：A/B 各回放 0901/02/03/04/07/08/09/10/14 共 9 日，产物落 `{date}/_replay/variant_a|b/agent_glue.json`，均 `status=ok`、`prompt_variant` 正确、md5 互异。矩阵实测：A 均值 M2=35.5% / 低置信 6.8%，B 均值 M2=46.3% / 低置信 40.8%（印证 §2.6 M2↔M3 拉锯；ΔM2 非单调——0903 A37.5→B100 大升、0901 A23.1→B17.9 反降）。⚠️ **数据质量披露**：0902 因 B 版输出体量撞 max_tokens=16000 截断（S1_empty），临时用 24000（A）/32000（B）重跑，该日 A(191 relations)/B 与其他日(16000)不对称，破坏 0902 单日「对照同参」，标定读数须剔除或标注。R-C6 对照矩阵完整（两版各 9 天 ≥8），达标判读归负责人；新增 `tests/test_glue_prompt_variant.py`（变体文案选择 + 共用区块锁死 + replay 门控 + 生产忽略 env + 未知值回退）。回归：`--date 20260910` R-C3 锚点 PASS、R-C6 按设计 SKIP（无曲线判读即 SKIP）；`test_node6_glue + test_glue_prompt_variant + test_glue_replay_variant + test_a6_wiring` 46 passed。
- [文档] GOT C-P12 事件级复核 + C-P13 接地管线设计笔记（2026-09-27）：对 C-P12 A/B 产物做**事件级覆盖对照**（`docs/got/archive/cp12_event_level_ab_20260926.md`，分析脚本 `.review_tmp/cp12_ab_event_table.py` 不入库）——A relation=336 / B=428，同事件同板块交集 168 / B 补挂 229 / A 有 B 无 104。暴露两项此前披露不足的质量问题：① **0902 A 版为退化重复循环**（191 条仅 28 命中事件，属生成/解码故障非 prompt 内容，C-P13 标定应整日作废）；② **B 版存在真·过度归属幻觉**（0903 房贷→24 板含减肥药/短剧游戏、金价→短剧游戏），现有 10 行分层预审样本抽不到高扇出病态事件→ M3_B 会被低估。据此基于外网论文（THEME CIKM'25 / Chain-of-Verification ACL'24 / Context-Aware Decoding NAACL'24 / CausalStock NeurIPS'24 / FinRipple / LangExtract）产出 **C-P13 设计笔记** `docs/got/archive/cp13_grounding_pipeline_research_20260926.md`：诊断 A/B 为同一生成架构在召回/精确单旋钮上的两端（§2.6 拉锯结构性无解），给出「接地式召回-验证」5 层管线（检索接地候选有界化 + CAD 受约束生成 + CoVe 精筛 + 图谱衰减传播 + 校准置信度，红线 0.6 应标在校准后置信度而非自报 confidence）。纯文档登记，零代码/行为变更；`docs/INDEX.md` / `backlog.md` §1.C C-P13 / `verification.md` §4 R-C6 同步。

### 上游同步（daily_stock_analysis 开源主线）

- [文档] README 与繁中 README 快速开始入口补充视频教程链接，并将桌面客户端入口文案调整为客户端配置教程。
- [修复] 问股从历史报告进入后的追问会持续携带当前标的，切回或重载已有会话时可从历史消息恢复基础当前标的，并由后端阻断未明确切换时的错误股票工具调用、交易所片段和指标缩写误路由。
- [修复] 自选股加入和删除按等价股票代码匹配港股及大小写美股变体，避免 `00700`、`HK00700`、`00700.HK` 或 `aapl`、`AAPL` 被误判为不同标的。
- [改进] #1390 P0 为个股分析与历史/回测展示新增可选八态 `action` / `action_label` 建议动作字段，保留 `operation_advice` 自由文本和 `decision_type=buy|hold|sell` 统计口径，不新增迁移或配置项。
- [新功能] #1390 P1 新增独立 `DecisionSignal` 存储、Repository、Service 与 `/api/v1/decision-signals` API，支持按来源类型/市场/股票/动作/期限/阶段去重、按 `source_report_id` / `trace_id` 查询、同源过期信号续期且保留来源身份字段、禁止 expired 直接 PATCH 复活、价格计划校验、状态更新、懒过期、cache-only 持仓过滤、敏感信息脱敏、敏感 `trace_id` 拒绝和仅清理 `source_type=analysis` 历史绑定信号的历史删除联动。
- [改进] #1390 P1 补充 Web decision-signals typed API wrapper 与契约隔离测试，暂不接入 UI。
- [修复] #1390 收紧建议动作 legacy fallback：英文 `not to ...` 与 `avoid selling/reducing/trimming ...` 等否定/回避表达不再误判为买卖动作，Web 旧记录不再把中文金融上下文、`buy or sell`、多 guard 歧义文本或 `buyback` / `buy-back` / `buy back` / `selloff` / `sell-off` / `sell off` 等英文复合词渲染成 action badge，并在有结构化 `action` 时让回测/历史趋势等入口按界面语言显示 action 标签。
- [改进] 完善运行时日志上下文，补充 logger name、触发来源、市场统计与实时行情预取链路状态，便于排查调度、API、Bot 和数据源降级路径。
- [修复] #1662 股票新闻与多维情报搜索在相关度排序后新增域名无关的准入过滤，剔除下载/安装包/应用评分页及成人/招嫖服务垃圾页，并在同批已有有效标的/行业候选时移除 `score=0` 背景填充项，避免无关或低质量页面进入 Prompt、Agent 工具输出和历史情报缓存。
- [新功能] 新增分析任务与历史报告运行流快照 API，提供 lanes、nodes、edges、events、summary 等统一契约，并从任务队列、运行诊断和 AnalysisContextPack overview 构建脱敏数据流/信息流。
- [新功能] Web 端为活跃任务、历史报告和大盘复盘报告补充运行流视图入口，支持查看运行摘要、拓扑节点、事件流和基础排障详情。
- [修复] 修复历史报告运行流快照在混合时区事件时间戳下返回 500 的问题。
- [改进] #1459 持仓管理页新增持仓账户删除入口，复用现有账户软删除接口，误建账户会从默认列表、快照、风险、录入入口和事件列表隐藏且不物理清理历史流水。
- [修复] 修复运行流 live SSE 事件未复用快照层递归脱敏规则的问题，避免本地路径、prompt/raw response、代理头等敏感诊断字段在 refetch 前短暂暴露。
- [改进] AlphaSift 依赖锁定更新到 `d038c52c468543726fc1fd830b53c27d3f09d6da`，并为新版 last-good snapshot、日线历史、行业/概念 provider cache、hotspot 具体题材榜单、题材发酵路线、概念股详情、上次成功热点缓存与 post-analysis 元信息补齐 DSA 运行期和 Web 选股页适配；默认不启用 DSA deep-analysis 回调。
- [修复] 桌面发布打包改用冻结可执行文件运行时探针校验 `alphasift.dsa_adapter`，避免 macOS PyInstaller 将模块内嵌进可执行文件时被文件系统/zip 扫描误判为缺失。
- [改进] #1381 个股分析新增按当日/市场复用的大盘环境摘要，普通 Pipeline 与 Agent 分析 Prompt 可读取低敏大盘背景，并在高风险/退潮环境下软化激进买入建议。
- [改进] #1381 新增默认开启的 `DAILY_MARKET_CONTEXT_ENABLED` 配置，默认把大盘摘要注入个股分析并启用保守护栏，仍允许用户显式关闭且保留大盘复盘报告独立运行。
- [文档] #1381 的范围为后端 runtime：为单股分析接入当日复用的大盘环境摘要，未新增独立 API、Web 阶段结果独立展示、四阶段日报结构化持久化或日报状态表变更，兼容边界仅聚焦运行时 LLM 路由读取链路。
- [文档] #1381 仅复用现有配置读取语义，不改动 provider/model/base_url 默认值或持久化/清理/迁移链路（`SystemConfig` 写入路径与配置降级逻辑保持不变）；官方语义依据见 https://docs.litellm.ai/docs/providers/openai_compatible 与 https://platform.openai.com/docs/api-reference/chat/create，回退路径为常规发布回滚（撤销相关提交）。
- [测试] #1381 覆盖后端 runtime 与兼容核验：本轮受影响/直接执行的验收项为 `tests/test_main_schedule_mode.py`、`tests/test_pipeline_daily_market_context.py`、`tests/test_daily_market_context.py`、`tests/test_daily_market_context_guardrail.py`、`tests/test_agent_executor.py`、`tests/test_config_env_compat.py`、`tests/test_config_registry.py` 与 `apps/dsa-web/tests/system_config_i18n.test.ts`；配置兼容语义继续沿用既有回归：`tests/test_system_config_service.py`、`tests/test_system_config_api.py`、`tests/test_llm_channel_config.py`、`tests/test_market_review_runtime.py`。
- [新功能] 新增 AlphaSift 热点题材链路：后端新增 `/api/v1/alphasift/hotspots` 与 `/api/v1/alphasift/hotspots/{topic}` API，Web 选股页新增“热点题材”区域并支持发酵路线与概念股查看。
- [改进] 新增 AlphaSift 热点题材读取与刷新策略：默认优先读取上次成功热点缓存，手动刷新才实时拉取并覆盖缓存，实时拉取失败时尽量回退旧缓存。
- [修复] AlphaSift 热点题材默认加载在无缓存且旧适配层缺少 `alphasift.hotspot` 模块时返回空态，不再一打开选股页就显示 AlphaSift 未就绪；手动刷新仍会提示依赖需更新。
- [改进] 改造 `main.py --webui-only` 启动行为：若 FastAPI 监听端口已被占用，启动即 fail-fast 抛出明确错误并退出，避免 Windows 下 `WinError 10048`。
- [文档] 补充 `docs/alphasift-integration.md`：明确 AlphaSift 锁定 commit 来源、Hotspot 契约边界、LLM/LiteLLM 兼容语义与关闭开关下回退路径。
- [修复] 为 THS 发酵路线补充列名兜底：当 `stock_board_concept_summary_ths` 返回缺列时仅跳过该来源富化，不影响 `/api/v1/alphasift/hotspots/{topic}` 返回。
- [测试] 新增/更新后端回归：`python -m pytest tests/test_alphasift_api.py -q`、`python -m pytest tests/test_docker_entrypoint.py -q`、`python -m pytest tests/test_main_schedule_mode.py -q -k "start_api_server_fails_before_thread_when_port_is_busy"`.
- [测试] 新增 `tests/test_got_orchestrator_trading_day.py`（4 测试：交易日放行/非交易日抛错/不再触发导入降级告警）与 `tests/test_portfolio_decision_lot_rejection.py`（2 测试：整手归零留痕且不产生买单/预算充裕时高价股正常买入），前者同时填补 `_allocate` 此前零测试覆盖的盲区。
- [文档] 明确验收红线判定标准并重划文档归属：**一条验收红线只能量已实施的东西**，读数由未实施工作决定的是 burndown chart 而非验收红线。据此 `R-D1` / `R-D2` / `R2` / `R5` 四条从待验证清单移入 `got_backlog.md`（分别归为工作进度 / 文档缺陷 / 红线设计缺陷）；`got_backlog.md` 新增 §0.1 把「未实施」拆为六类处置（待退役 / 已改归属 / 降级口径 / 挂起待查 / 明确延后 / 真·待实施），§2 由「10 项未修」改为 15 项处置表（真·未排期 = 0），新增 §9 登记红线与验收脚本自身的 5 条缺陷。
<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->


## [3.21.0] - 2026-06-07

### 发布亮点

- feat: 新增 Web UI 中英文界面语言切换和飞书 App Bot 通知模式，提升多人部署和企业通知场景体验。
- feat: 大盘复盘报告、历史入口和个股栏继续收口到结构化数据与统一 Markdown/GFM 渲染，Web/API 人工触发入口不再被交易日 gate 短路。
- feat: AlphaSift 选股链路改为可恢复后台任务，并完善 DSA LLM runtime bridge、默认适配层预置和兼容回归。
- fix: 修复英文界面残留中文、诊断展示、运行时环境变量展示、健康检查、桌面更新路径、工作流变量读取和多处 Web 窄布局问题。

### 新功能

- WebUI 新增独立界面语言状态与中英文切换入口，覆盖主导航、首页、登录、设置页和通用控件文案；UI 语言与 `report_language` 解耦，不改写报告语言链路。
- 飞书通知新增应用机器人（App Bot）模式，支持通过 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` 配置，无需额外创建自定义机器人。
- Web 大盘复盘报告新增专用展示视图，历史入口和首页即时结果统一使用 Markdown/GFM 渲染并隐藏个股专属模块。
- 大盘复盘新增结构化 `market_review_payload`，Web、历史详情和推送统一基于结构化数据渲染，并保留 Markdown 兼容展示。
- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 明确控制，并保留 `/install` 作为显式修复路径。

### 改进

- Web/API 大盘复盘人工触发入口不再因交易日检查或相关市场休市而短路跳过；定时任务、GitHub Actions 手动运行和 CLI 默认入口仍保持原交易日 gate。
- AlphaSift Web 选股改为后台任务提交与状态轮询，新增可恢复任务状态展示，避免外部快照、行情或 LLM 变慢时浏览器长请求超时。
- AlphaSift 选股 API 与服务层收敛到 `AlphaSiftService`，endpoint 仅做路由参数接收与错误映射。
- AlphaSift 与 DSA 的运行时 LLM 兼容桥接改为调用期注入，保留 `provider/model/base_url/custom headers/fallback` 语义链路，不做持久化迁移。
- Web 首页侧栏不再单独展示大盘复盘历史集合，最新大盘复盘作为 `MARKET` 并入个股栏，按最近分析时间参与排序，并复用个股栏的选择、删除、完整报告与历史趋势查看能力。
- 多股通知报告将市场阶段收敛为总览下方单行 `市场状态`，不再在每只股票摘要下重复展示数据质量和限制详情。
- API 错误响应构造收敛到共享 helper，保持既有错误 envelope 形状并降低 endpoint 重复代码。
- WebUI 绑定公网地址或 CORS 全开放且未启用管理员认证时新增运行时 warning；仅增加可观测性，不阻断启动、不改写配置。
- 数据库初始化新增 `schema_migrations` baseline 标记表与幂等记录，用于后续 schema 演进追踪；不迁移、不清理、不改写既有业务表数据。
- #1386 P6 复用市场阶段与 AnalysisContextPack 公开摘要联动告警、持仓手动分析、历史、回测和通知展示，不新增数据库迁移。

### 修复

- Web 英文界面补齐回测、组合风险与告警规则相关文案本地化，避免英文模式下残留中文筛选器、按钮和枚举标签。
- 综合情报搜索中的机构分析与业绩预期维度改用 180 天 provider 请求窗口，避免默认短新闻窗口漏掉财报、研报等周期性财经材料。
- Web 个股栏和历史卡片在窄布局下不再让市场阶段标签遮挡股票名称。
- 问股自由文本追问不再将 TTM、PE、YOY 等金融缩写误识别为新股票代码。
- [修复] GitHub Actions 每日分析工作流读取 SearXNG 自建实例地址时支持 Variables 优先、Secrets 回退，修复仅配置 Variables 时 URL 不生效的问题。
- Web/桌面端左侧导航选中态改用 border 实现，避免蓝色竖条指示器溢出侧栏边界；侧栏展开宽度 116px -> 136px，新增 rail 紧凑模式。
- Windows 桌面端自动更新安装目录不再预先加引号，避免带空格路径在自动安装时触发“缺少快捷方式 / 找不到 Daily Stock Analysis.exe”的系统弹窗。
- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 修正大盘复盘结构化 `breadth` 的可用性判断：当市场不支持或抓取失败时不下发 `breadth`，前端展示“暂无数据”，避免误导性 0 值。
- 大盘复盘语言行为遵循全局 `report_language`，并在美股中文场景下本地化市场标签与策略蓝图，避免混入英文策略段落。
- Docker Web 设置页读取配置时在活跃 `.env` 文件缺项时回退展示启动注入的同名环境变量，并补清相关挂载边界文档。
- 报告页运行诊断会区分数据源抓取成功与进入 LLM 分析输入，相关新闻区标注为报告页补充/后续检索资讯，避免与输入数据块状态互相误读。
- `/health` 根路径健康检查现在始终返回 JSON，避免静态 Web fallback 吞掉健康探针；`/api/health` 与 `/api/v1/health` 继续保持兼容。
- `ALPHASIFT_ENABLED` 关闭时不触发 `alphasift` 运行时注入；开启后优先复用已配置的 DSA/provider 配置并注入 `LITELLM_*` 与 `LLM_*` 运行时变量。
- 补齐 openai-compatible 场景下 base URL、`extra_headers` 与 `LITELLM_FALLBACK_MODELS` 的兼容路径与回退链验证。
- 桌面/镜像打包链路保持与运行时一致的 AlphaSift 适配层预置，避免 `pip install` 作为线上修复依赖。

### 文档

- 明确 Issue #777 UI 语言切换采用仓内 `UiLanguageContext` + `uiText` 实现，持久化 key 为 `dsa.uiLanguage`，并补充对应可视化验收指引。
- 明确大盘复盘展示链路、结构化 payload、语言行为、交易日 gate 差异和回滚边界。
- 补充 LLM / LiteLLM 兼容键在 Settings 展示与校验上下文中的回退边界，说明不改写、不迁移、不清理用户现有 provider/model/base URL 持久化配置。
- 补齐 #1602 运行诊断口径修复覆盖范围，说明仅统一输入与展示口径，回滚方式为常规发布回滚。
- 明确 AnalysisContextPack P6 文档、迁移与回滚边界，并同步既有 `SAVE_CONTEXT_SNAPSHOT` 到 `.env.example`、配置注册表、Web 设置帮助和完整指南。
- 补齐 #1386 P7 盘前/盘中/盘后分析的入口、迁移、回滚和用户可见说明。
- 为 AlphaSift runtime bridge 增加官方兼容依据落点，明确 provider/model/base_url/extra_headers/fallback 与回退边界。

### 测试

- Web 方向执行 `npm run lint`、`npm run build`、相关 Vitest 和 smoke 命令；未设置 `DSA_WEB_SMOKE_PASSWORD` 时 smoke 用例按设计 skip。
- Web 测试运行时声明 Node `>=20.19.0 <27` 与 npm `>=10`，并补 localStorage 测试兜底以稳定 Vitest。
- 增补 AlphaSift runtime bridge 与打包脚本静态验证，覆盖 `LLM_CHANNELS`、`LITELLM_FALLBACK_MODELS`、`alphasift.dsa_adapter`、`--collect-all alphasift`。

### chore

- 移除随 issue / PR 验收流程误入库的截图资产，并明确一次性截图证据应保留在 PR 描述、评论、附件或 artifact 中，不作为仓库文件合入。

## [3.20.0] - 2026-06-03

### 发布亮点

- feat: 新增 AlphaSift 选股入口、自动安装与稳定适配层，支持 Web 策略执行、LLM 重排展示和默认关闭的可控启用。
- feat: 完善个股历史、自选队列、市场阶段与 AnalysisContextPack 可见性，增强 Web 报告和 API 的结构化上下文能力。
- feat: MiniMax 默认模型升级到 `MiniMax-M3`，并补齐相关价格、预设和测试覆盖。
- fix: 修复健康检查、Windows 桌面更新与首次运行编码、ETF 日线 secid、LLM base_url 校验和 Agent 日线上下文误判等稳定性问题。

### 新功能

- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 开启后经由稳定适配层读取策略并执行选股。
- Web 首页左侧栏改为个股栏，按股票去重展示，大盘复盘置顶，点击个股加载最新报告，支持按代码变体（.SZ/.SH/.SS）归一化去重合并。保留全选、批量删除和删除确认入口；新增按股票代码批量删除 API `DELETE /api/v1/history/by-code/{stock_code}`。
- 报告详情右侧栏新增自选操作入口，支持查看当前股票是否在自选队列、一键加入或移除；大盘复盘报告不显示该操作。
- 问股页面输入区上方新增自选操作按钮，用户发送包含股票代码的消息后自动显示加入自选/从自选删除入口。
- Web 报告页新增同股历史趋势抽屉入口，历史列表摘要补充趋势、摘要、模型和分析时行情字段，支持按当前股票查看历史分析并加载更多。
- AnalysisContextPack P4 低敏 overview 接入历史详情、同步分析响应、completed 任务状态和 Web 报告页，展示数据块状态、来源、缺失原因与降级摘要。
- #1386 P5 为个股分析报告新增 `dashboard.phase_decision` 盘中决策护栏，并在保存历史前按市场阶段与数据质量限制高置信盘中买卖结论。
- #1386 P4a 新增 `analysis_phase=auto|premarket|intraday|postmarket` API 参数，并在异步任务 accepted、内存 status、list、SSE 与分析 pipeline 中透传请求阶段。
- #1386 P4b Web 报告页新增最终市场阶段标签，任务面板展示请求阶段，并复用 AnalysisContextPack 低敏数据质量摘要。
- MiniMax 渠道模型列表升级：新增 `MiniMax-M3` 并作为默认，按官方 OpenAI-compatible 文档支持 1M 输入上下文（项目保守注册为 `<=512K` 价格档：context_window 512K、`max_tokens` 128K，对应 $0.6/M 输入、$2.4/M 输出，>512K 输入价格档未建模），保留 `MiniMax-M2.7` 与 `MiniMax-M2.7-highspeed`，并保留 `MiniMax-M2.5` legacy 价格条目以兼容现有用户配置的成本估算。Web 设置页 MiniMax 预设模型与价格按 M3 刷新。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 市场阶段低敏摘要接入历史详情、同步分析响应和 completed 任务状态的 report metadata。

### 改进

- 首次运行配置校验补充缺失 AI Key、空 STOCK_LIST、Telegram/邮件成对字段和 Webhook URL 前缀诊断。
- AlphaSift 选股入口在 Web 侧边栏中移动到“问股”下方，贴近 Agent/研究辅助工作流。
- Docker 镜像构建阶段预置默认 AlphaSift 适配层，与桌面发布包一样避免运行期额外安装。
- AlphaSift 选股改为依赖 `alphasift.dsa_adapter` 的稳定接口，Web 策略列表由 AlphaSift 动态提供，不再在前端硬编码。
- AlphaSift 选股页补充 Run ID、快照数、过滤后数量、因子和风险详情，展开候选时展示真实明细，并暂时仅开放当前支持的 A 股市场。
- Web 设置页新增 AlphaSift 选股开关卡片，可直接开启或关闭选股页签。
- 开启 AlphaSift 选股时先切换 `ALPHASIFT_ENABLED` 并检查适配层可用性，缺失时自动调用受控安装接口，不再要求用户额外点击安装。
- AlphaSift 已开启但适配层缺失时，策略列表和选股接口会串行化自动安装锁定来源，并强制重装以覆盖旧版 `alphasift` 包。
- AlphaSift 选股页合并重复的快照源 fallback 提示，并保留 AlphaSift 自身的 Tushare 优先快照源逻辑。
- AlphaSift 选股页在 LLM 重排降级时展示 warning/source error/parse error，并避免把本地因子评分误显示为 LLM 判断。
- Web 设置页不再把 `ALPHASIFT_ENABLED` 作为普通数据源配置项重复展示，该值仅作为“开启选股”按钮背后的持久化状态。
- AlphaSift 关闭时隐藏 Web 左侧“选股”导航入口，避免误导未开启用户。
- 补充 AlphaSift 选股自定义策略显示逻辑，避免未匹配预设项时误显示“均衡多因子”。
- 新增 GET /api/v1/history/stocks 端点按 code 分组返回不重复个股列表；新增 GET /api/v1/stocks/watchlist、POST /api/v1/stocks/watchlist/add、POST /api/v1/stocks/watchlist/remove 端点支持自选队列增删查。STOCK_LIST 读写保持原样，不做自动归一化；add/remove 时归一化比较判断等价代码变体。
- 新增 useWatchlist hook 统一管理自选队列前端状态，复用 SystemConfigService 的 STOCK_LIST 配置项实现持久化。
- AnalysisContextPack P5 增加数据质量评分、`fetch_failed` 状态、Prompt 数据限制区块和 Web 低敏质量展示。
- #1386 P2-full 在 AnalysisContextPack Prompt 数据限制中追加市场阶段与降级数据的交叉约束，并修正中文分析 Prompt 的阶段化行情标签。
- 通知报告默认发送路径恢复既有渠道兼容转换与分片逻辑，新增 renderer 能力仅保留为未来扩展基础。
- 关联板块缺少类型数据时改为单行展示板块名称，避免生成整列 `N/A` 的板块表格。
- 优化 Web 报告详情页信息层级，将输入数据块和运行诊断下移为主体内容后的折叠辅助信息。
- 盘中分析补齐实时行情获取时间、provider 时间、stale、fallback 与 partial/estimated 标记，供 AnalysisContextPack 映射输入数据限制。

### 修复

- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 注册 /api/v1/health 路由并加入认证豁免，修复该路径返回 404 以及开启 ADMIN_AUTH_ENABLED 后健康探针收到 401 的问题。
- Windows 本地首次运行环境检查兼容非 UTF-8 控制台输出，并将 `requirements.txt` 注释改为 ASCII 以降低默认代码页下的依赖安装失败概率。
- AlphaSift DSA 适配层默认开启 LLM 重排，后端显式请求 `use_llm=True`，选股页展示 LLM 分数、判断、覆盖率和关注项。
- AlphaSift 嵌入 DSA 时复用 DSA 已解析的 LLM 模型、渠道和密钥配置，避免 Web 已配置 LLM 但选股 LLM 重排仍因缺少 provider key 降级。
- AlphaSift 选股复用 DSA LLM 路由时过滤未声明的托管 provider 备选模型，并把已声明渠道模型补入回退链，避免残留 Gemini fallback 覆盖可用的 DSA 渠道。
- AlphaSift 默认安装来源改为锁定 commit 的受信任 GitHub 地址；桌面模式自动安装不要求管理员会话，非桌面部署要求管理员认证会话，并继续限制安装来源。
- 修复 Web 开启 AlphaSift 时先安装后写配置导致默认关闭状态无法开启的问题。
- AlphaSift 状态与安装接口不再返回 `install_spec` 明文，仅返回 `install_spec_is_default` 等非敏感状态字段。
- AlphaSift 状态探测区分可选依赖缺失与非预期异常，异常场景记录 warning 并返回非敏感诊断信息。
- 调整 AlphaSift 筛选调用兼容：`screen` 以 `max_results` 为主并支持历史 `max_output` 关键词，同时允许策略透传以对齐前端手动策略参数。
- AlphaSift Web 选股请求使用独立长超时，避免开启 LLM 重排后被通用 30 秒 API 超时提前中断。
- 桌面端打包阶段预置 AlphaSift 并收集适配层，避免发布包运行时再要求管理员自动安装。
- AlphaSift 自动安装仅在 `status` 诊断为 `missing_module` 时触发（仅模块缺失场景）；适配层可导入但运行时异常不再自动 `pip install`，而是返回 `424` 并保留诊断，避免把真实运行时故障掩盖为重装。
- 收口 Web 中文界面残留英文文案与设置页 help 缺口，回测页改为中文展示，并让 Web 设置页仅展示已注册且带说明的配置项。
- Windows 桌面端自动更新静默安装时显式复用当前安装目录，避免自定义安装目录场景下卸载旧版本文件失败。
- Windows 安装器重试旧卸载器时对 `_?=` 安装目录参数加引号，修复旧版本安装在带空格路径时返回 2 导致自动更新失败。
- Windows 桌面端自动更新传给 NSIS 的 `/D=` 目录参数在包含空格时自动加引号，避免安装位置注册表被截断。
- 加固 LLM channel base_url 校验，避免解析差异导致 SSRF 绕过。
- 修正 efinance ETF 日线 Eastmoney secid 路由，避免沪市 ETF 被按深市 quote id 查询导致日线为空。

### 文档

- 明确 AlphaSift 与 LiteLLM 兼容边界：仅桥接 DSA 已声明 provider/model/base URL 为调用期注入，不对 `.env` 做 provider/model 路由迁移；回退方式为关闭 AlphaSift 并恢复原有 `LITELLM_*`/`LLM_*` 配置。
- 明确 AlphaSift 仅复用 DSA 现有 LLM/LiteLLM 配置语义，不新增 `LITELLM_MODEL`、`OPENAI_MODEL`、`OPENAI_BASE_URL`、`LLM_TIMEOUT_SEC` 等模型语义迁移；失败提示与回退路径统一沿用既有系统配置链路，仅影响 AlphaSift 选股能力本身。
- 明确 AlphaSift 自动安装来源锁定、`missing_module` 与运行时异常行为边界，以及 LLM/provider/base URL 与自定义通道回退路径，便于问题溯源与回滚到原有 LLM 配置。
- 明确同股历史趋势新增模型字段为历史快照展示元数据，不影响运行时 LLM Provider/Model/Base URL 路由与配置迁移清理；回退方式为按常规发布回滚本变更。
- 明确 #1311 的兼容性边界：渲染层仅消费分析结果 `model_used` 展示字段，未改动 `wechat/slack/feishu/telegram` sender 发送链路，不触发 provider/model/base_url 兼容迁移。
- 明确 AlphaSift 锁定 commit 的 `alphasift.dsa_adapter` 契约依据，以及当前 DSA API/Web 调用结构的兼容边界。
- 明确 Settings 页面对 LLM 配置仅做展示分组与字段归并，不改写或触发 LLM 迁移/回退路径；兼容现有 `LLM` 配置保存与回退语义。
- 新增 AnalysisContextPack P0 上下文盘点。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。

### 测试

- 同步更新 `llmProviderTemplates`、LiteLLM fallback pricing 与 MiniMax 预设相关单测，断言新默认模型。
- 补充 ETF 日线数据源路由、输入变体、fallback 与 MA 字段回归覆盖。

### chore

- 新增通知报告渠道能力画像、PreparedMessage 与结构感知 Markdown 分片基础设施，为 #1311 全渠道渲染适配打底。
- 预置企业微信、飞书、Telegram、钉钉、Slack 平台 renderer 元数据，暂不改变默认推送报告入口和可见版式。

## [3.19.0] - 2026-05-29

### 新功能

- 落地 #1391 Phase 1 运行诊断最小链路：任务/SSE 追加 trace_id，并记录日线与实时行情 ProviderRun 快照。
- 告警中心新增 P7 大盘红绿灯结构化规则，支持 `market_light_status` 与 `market_light_score_drop` 并复用现有 worker、触发历史、通知和冷却链路。
- 落地 #1391 Phase 2 运行诊断摘要：生成用户可读 RunDiagnosticSummary，提供历史报告诊断 API 与脱敏复制文本。
- 落地 #1391 Phase 3 运行诊断可见性：报告详情和任务面板默认折叠展示运行状态、trace 与可复制排障信息；后端通过 `api/v1/history/{record_id}/diagnostics` 与 `context_snapshot.diagnostics` 提供历史链路回填。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 新增 AnalysisContextPack P2 builder，从普通分析 pipeline 已有 artifacts 组装内部上下文包。
- 问股新增默认关闭的可见对话上下文压缩，支持 Web 开关、Agent 高级 preset、滚动摘要和最近轮次原文保护，降低长会话 token 消耗。
- 股票自动补全索引默认支持从 GitHub main 远程刷新并缓存到本地，Web/CLI 分析入口失败时自动降级到内置索引，降低摘帽和更名后旧简称污染分析的概率。
- 普通分析与 Agent 运行时 Prompt 接入 AnalysisContextPack 低敏摘要，保持 history/API/Web 输出兼容。

### 改进

- `scripts/fetch_tushare_stock_list.py` 可对 A 股中带 `XD`/`XR`/`DR`/`N`/`C` 前缀的名称进行回填修正，供自动补全刷新流程默认使用。
- Web 路由页面改为按需加载，降低首包体积并增加路由加载失败恢复提示。
- Web 完整报告 Markdown 抽屉改为按需加载。
- 新增市场阶段推断基线并明确盘前、盘中、午休、临近收盘、盘后和非交易日语义。
- 新增运行态市场阶段上下文构造与降级测试。
- 设置页配置帮助阶段性补齐 Web 设置页实际展示/可配置字段的中英双语文案，覆盖 Agent、回测、报告、通知路由、系统运行时、AI legacy、数据源和通知高级配置。
- P2-min：LLM Prompt 注入市场阶段上下文。

### 修复

- 股票自动补全索引生成缺少 `pypinyin` 时改为直接失败，避免写出缺失拼音字段的降级索引。
- 归一腾讯实时行情成交量为股口径，避免量能变化倍数被放大并误导分析报告。
- Docker 默认部署移除 `.env` 单文件挂载，避免 WebUI 保存配置时因 `os.replace` 更新挂载点触发 `Device or resource busy`。
- 收敛 #1391 Phase 0 A 股代码归属边界：补齐 `SH`/`SZ` 前缀场景的归属一致性，明确 `data_provider/baostock_fetcher.py`、`data_provider/pytdx_fetcher.py`、`data_provider/tushare_fetcher.py` 的本轮修复范围。
- 修复 `STOCK_LIST` 使用裸 A 股代码时 Baostock 等数据源 fallback 的内部格式转换，保持用户配置继续使用 6 位股票编号。
- Windows 桌面端自动更新在用户确认重启安装后改为静默执行安装器，并在停止内置后端后清理进程引用，降低安装器提示“每日股票分析无法关闭”的概率。
- macOS 桌面端将运行时配置迁移到用户数据目录，并在旧 `.app` 包内文件仍可访问时迁移 `.env`、数据库和日志，避免后续替换升级后重新配置。
- 恢复 Agent/历史兼容快照中的关联板块与板块联动字段提取，修复新版首页报告缺少“板块联动”的回归问题。
- 修正 Web 设置帮助中 legacy 告警 JSON 字段名与静默时段投递语义说明。
- 修复 Web 中文设置页在数据源、通知、系统与 Agent 区域的配置标题、说明和关键下拉选项漏翻问题。
- 修复问股会话切换和首页任务重连后可能残留 Agent/分析任务进行中状态的问题。
- 问股 single-agent 新增 provider-aware trace 分轨，跨轮保留 DeepSeek V4 thinking + tool-call 的 `reasoning_content` 与工具协议材料。
- 为 Akshare 新浪/腾讯 A 股历史兜底接口增加调用级超时，并补齐 Tushare `605xxx` 沪市代码路由回归测试，避免定时分析因数据源无响应而挂起。
- 将 `exchange-calendars` 依赖下限提升到 `4.13.0`，避免 pandas 3 环境导入交易日历时因 Timedelta 单位 `T` 失效导致分析失败。
- 交互式命令（钉钉会话、飞书会话、Telegram）触发的分析结果只回到来源会话，不再同时广播到静态通知渠道。
- 适配 Longbridge OAuth 2.0 认证与 token 缓存恢复，避免新后台无 Legacy Access Token 时长桥数据源被误判为未配置。
- Longbridge OAuth 路径在当前 SDK 不支持 `OAuthBuilder` / `Config.from_oauth` 时明确日志降级，避免 Linux/Docker 仅可安装旧 SDK 时构建失败。
- 兼容 YFinance 日线返回未命名日期索引的场景，避免标准化后缺少 `date` 列导致美股日线 fallback 中断。

### 文档

- 新增 #1391 Phase 0 运行诊断契约文档，明确 trace_id、诊断摘要、关键链路范围与脱敏/fail-open/retention 边界。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。
- 说明本次桌面修复仅覆盖 Windows NSIS 更新安装链路与后端进程生命周期清理；未改动设置项保存/模型运行时清理语义。移除此前误入的 `docker/Dockerfile` `npm registry` 变更，恢复部署构建与更新修复的职责隔离。
- 新增 AnalysisContextPack P0 上下文盘点，明确字段质量状态、现有状态映射和首版 pack 边界。
- 明确 #1391 Phase 2 的结构化检测告警为非配置迁移信号：`agent_max_steps`/`agent_orchestrator_timeout_s` 非法值会 fallback 至默认并产生日志告警，新增诊断链路仅新增 `context_snapshot`/`RunDiagnosticSummary` 读写字段，不改写 `litellm_model`、`agent_litellm_model`、`openai_base_url`、LLM channel 路由或配置迁移语义。
- 补充 #1391 Phase 3 兼容性说明：记录后端诊断持久化、历史查询与通知回写链路变更边界与回滚策略，并补齐后端门禁级验证要求。

### 测试

- 收敛 #1391 Phase 3 后端/API 与 Web 回归检查：`./scripts/ci_gate.sh`、`test_pipeline_market_phase_context.py`、`test_analysis_api_contract.py`、`test_analysis_history.py`、`npm run lint`、`npm run build`。
- 执行 `python -c "import exchange_calendars as xcals; xcals.get_calendar('XSHG'); print('ok')"` 通过验证，以覆盖导入与交易日历初始化兼容性。

## [3.18.0] - 2026-05-21

### 发布亮点

- feat: 告警中心扩展到 P2-P6，补齐后台评估、真实通知结果、业务冷却、技术指标规则，以及自选股 / 持仓 / 账户联动规则。
- feat: 个股分析支持策略选择，新增热点题材、事件驱动、成长质量和预期重估策略，并为 HK/US 报告补充基本面、财务摘要、股东回报和关联板块。
- feat: 新增 Finnhub / AlphaVantage 美股数据源适配器，扩展美股日线 failover 链，提升美股行情获取韧性。
- fix: 修复桌面端发布打包、分析状态接口、AlphaVantage 涨跌幅、持仓实时估值、告警历史去重、数据库冷启动和 fallback pricing 注册等稳定性问题。

### What's Changed

- feat: Add alert-center P2-P6, Web strategy selection, HK/US fundamental context, static-report financial sections, and Finnhub / AlphaVantage US-market fallback.
- improve: Refine LiteLLM parameter recovery, yfinance currency/dividend handling, RSI calculation, market-review presentation, stock-news relevance ranking, and report table rendering.
- fix: Harden desktop packaging/update assets, completed analysis-status responses, AlphaVantage pct_chg routing, portfolio realtime snapshots, alert trigger dedupe, DatabaseManager cold start, and fallback pricing registration.
- docs/tests: Add beginner setup and settings-help docs, document compatibility/rollback boundaries, and extend regression coverage for API, alert, packaging, and release paths.

## [3.17.1] - 2026-05-16

### 发布亮点

- fix: 桌面端 Windows / macOS 打包脚本显式关闭 electron-builder 自动发布，避免 tag 构建时因缺少 `GH_TOKEN` 在本地打包完成后失败；Release workflow 继续负责上传和发布产物。

### What's Changed

- fix: Add `--publish never` to the Windows and macOS Electron packaging scripts so tag builds only create local artifacts and GitHub Actions handles release upload/publish.

## [3.17.0] - 2026-05-16

### 发布亮点

- feat: 新增 Alert API MVP，支持告警规则 CRUD、启停、一次性测试以及触发/通知结果查询，首版覆盖 `price_cross` / `price_change_percent` / `volume_spike` 并保持 legacy 配置兼容。
- feat: 通知网关新增 ntfy 与 Gotify 一等渠道，并补齐通知降噪、静态渠道隔离、诊断、Web 测试和 GitHub Actions env 对照校验。
- feat: Windows 桌面安装版接入自动更新安装链路，支持后台下载、确认重启安装、运行时文件备份/恢复和发布产物元数据校验。
- improve: 大盘复盘新增概念排行、人气股、涨停池等底层数据源，支持指数涨跌颜色语义配置，并将复盘结果写入历史记录。
- improve: Web 设置页支持 `.env` 配置备份导入/导出和通知/Agent 区域局部错误兜底；报告新增 `REPORT_SHOW_LLM_MODEL` 开关控制模型信息展示。
- improve: Docker 启动入口自动修复挂载目录权限并在日志目录不可写时降级到控制台，减少普通部署的手动修复步骤。
- fix: 数据源缺凭据或连接失败时更温和降级，Longbridge / Pytdx 加入冷却，资金流缺失时避免输出高置信买入结论。
- fix: 分析与报告链路兼容 OpenAI-compatible `content_blocks` 响应，归一策略价格字段，并修复大盘复盘滚动和历史记录丢失问题。
- docs: 补齐通知、告警中心、桌面打包、README / 指南和 PR title 治理说明，明确多处配置兼容边界与回滚路径。
- test: 增加 Alert API、通知降噪/路由、Docker entrypoint、数据源预取、桌面更新链路和分析历史等回归覆盖。

### What's Changed

- feat: Add an Alert API MVP with rule CRUD, enable/disable, one-shot testing, trigger history, notification results, and legacy config compatibility.
- feat: Promote ntfy and Gotify to first-class notification channels with Web tests, routing, Actions integration, diagnostics, and noise control.
- feat: Add the Windows desktop auto-update install flow with runtime state backup/restore and release artifact metadata verification.
- improve: Extend market review data sources, add configurable index color semantics, and persist market review results into analysis history.
- improve: Add Web `.env` backup import/export, local settings panel error boundaries, and a report model visibility toggle.
- improve: Harden Docker startup by repairing mounted directory permissions and falling back to console logging when mounted logs are not writable.
- fix: Cool down unavailable optional fetchers, reduce noisy Longbridge/Pytdx retries, and downgrade buy advice when capital flow data is missing.
- fix: Handle OpenAI-compatible `content_blocks`, normalize strategy price fields, and recover market review scrolling/history behavior.
- docs/tests: Update notification, alert, desktop packaging, README/guide, and governance docs; add focused regression coverage for the new release paths.

## [3.16.0] - 2026-05-10

### 发布亮点

- feat: Web 首页新增“大盘复盘”触发入口、任务轮询与完成后报告直出；首次启动配置状态可提示缺口并引导到系统设置。
- feat: 新增通知路由策略，支持按 report、alert、system_error 将通知收窄到指定渠道；Web 设置页支持通知渠道一键测试。
- feat: 系统设置页新增配置项帮助入口与多语言帮助文案基础设施，首批覆盖自选股、LLM 主模型、LLM 渠道、飞书 Webhook 与 WebUI 监听地址。
- improve: 大盘复盘 API、CLI、Bot 共用 `build_market_review_runtime` 装配路径，补齐 `litellm_model` / `llm_model_list` 与 legacy key 回退说明。
- improve: 个股报告操作建议结合支撑/压力、量能、筹码与主力资金流校准，减少买入/卖出剧烈切换，并补强 Agent 决策兜底。
- improve: Docker 镜像支持非 root 用户运行，LiteLLM 依赖约束放宽到后续安全 1.x 修复版本。
- fix: 修正 LLM 渠道测试中 `Model disabled`、provider blocked 等错误分类，避免被误报为网络异常。
- fix: 港股日线跳过不支持港股的内置历史数据源；北交所 `BJ` 前缀与 `.BJ` 后缀代码校验保持一致。
- fix: Web 大盘复盘按钮可观测性、Windows fallback 锁进程探测和催化线索展示更稳健。
- docs: 新增文档中心与配置帮助维护说明，清理 README、完整指南与配置指南中的临时 PR/文档同步说明。

### What's Changed

- feat: Add a Web home market-review trigger with task polling and inline report display; setup status now points users to missing configuration.
- feat: Add notification routing by report, alert, and system_error; add one-click notification channel testing in Web settings.
- feat: Add settings field help infrastructure with multilingual help text for the first batch of core configuration fields.
- improve: Share `build_market_review_runtime` across API, CLI, and Bot market review paths; document `litellm_model` / `llm_model_list` and legacy key fallback behavior.
- improve: Calibrate stock advice with support/resistance, volume, chips, and main-force capital flow; strengthen Agent decision fallback behavior.
- improve: Run Docker images as a non-root user and relax LiteLLM constraints to allow safe future 1.x fixes.
- fix: Classify `Model disabled`, provider blocked, and related LLM channel test errors more accurately instead of reporting them as generic network failures.
- fix: Avoid unsupported built-in historical providers for Hong Kong daily data; align Beijing Stock Exchange `BJ` prefix and `.BJ` suffix validation.
- fix: Improve Web market-review observability, Windows fallback lock probing, and market catalyst snippet rendering.
- docs: Add the documentation index and settings-help maintenance guide; remove temporary PR/doc-sync notes from README and user-facing guides.

## [3.15.0] - 2026-05-05

### 发布亮点

- LLM 渠道配置体验继续升级：新增 Anspire OpenAI-compatible 网关接入，并补齐常用服务商预设、官方来源、能力标签、配置注意事项和 GitHub Actions 显式映射。
- Web LLM 配置检测更可诊断：细分错误 reason，并支持用户显式触发 JSON、tools、vision、stream 运行时 smoke。
- LLM 运行时配置清理更稳健：只清理托管 provider 的失效运行时选择，并保留 `cohere/*`、`google/*`、`xai/*` 等直连 provider 兼容语义。
- 通知与 Bot 状态可观测性增强：自定义 Webhook 支持 JSON body 模板，Bot `/status` 展示更完整的 LLM、Agent 与通知渠道状态。
- 大盘复盘、实时告警、Agent weak 兜底和持仓估值继续补强，降低默认值覆盖、缺价污染和配置排障成本。

### 新功能

- 支持 `ANSPIRE_API_KEYS` 默认接入 Anspire OpenAI-compatible 大模型网关，并在 LLM 渠道编辑器补充 Anspire Open 预设。
- 自定义 Webhook 支持 `CUSTOM_WEBHOOK_BODY_TEMPLATE` JSON body 模板，便于适配 AstrBot、NapCat 和自建推送服务。
- 大盘复盘结构化区块新增大盘红绿灯结论，基于盘面温度输出 green/yellow/red、核心原因和操作建议。
- EventMonitor 支持 `price_change_percent` 涨跌幅阈值规则，可按上涨或下跌方向触发实时告警。
- Web LLM 渠道编辑器新增常用服务商配置模板与预设，覆盖 MiniMax、火山方舟、OpenAI、Claude、Gemini、Kimi、Qwen、GLM、豆包等入口。

### 改进

- Web LLM 配置检测补充细分错误分类，并新增显式触发的 JSON/tools/vision/stream 运行时 smoke；默认测试和保存流程不变，检测结果仅作为当前配置的一次 best-effort 诊断。
- Bot `/status` 展示统一 LLM 主模型、Agent 模型、渠道模式、YAML 配置和更多通知渠道状态。
- Web LLM 渠道编辑器展示 provider 能力标签、官方来源链接和配置注意事项提示；这些标签仅用于配置参考，不代表运行时能力已验证通过。
- 抽出 Web LLM provider preset 单一模板数据源，保持现有配置保存语义不变。
- 补齐 LLM provider channel 在 GitHub Actions 中的显式映射，并同步 `.env` 示例与配置文档。

### 修复

- Agent weak 完整性兜底在模型缺少评分、趋势、操作建议或 dashboard 关键块时优先保留本地趋势分析结果，并只补齐真正缺失的仪表盘字段，避免首页评分被默认 50 覆盖。
- 统一持仓快照输出现价、市值、浮盈亏、收益率与价格元信息，避免缺价或 stale 价格污染持仓估值。
- LLM 渠道测试补充结构化诊断与设置页排障提示，便于定位 provider、模型、Base URL 和鉴权配置问题。
- 明确 runtime 清理兼容边界：仅对托管 provider（`gemini`、`vertex_ai`、`anthropic`、`openai`、`deepseek`）触发保存前失效值清理，`cohere/*`、`google/*`、`xai/*` 直连值按 legacy 兼容路径保留，不做无提示迁移或覆写。
- 将 MiniMax 预设调整为官方 OpenAI-compatible Base URL 和当前模型示例，并补充 MiniMax、火山方舟、LiteLLM 兼容来源与回退说明。
- 移除截图识别对 Gemini 3 Vision 模型的过时降级逻辑，默认推断改用当前 Gemini 模型配置。

### 文档

- 完善 LLM provider 配置文档，补充配置方式选择、Actions 变量对照、运行时检测边界、错误 reason 排障和回滚路径（#1180）。
- 补充 LLM 渠道编辑器的官方来源、依赖兼容窗口、保存时的运行时模型清理规则，以及旧配置回退路径说明。
- 为 `cohere/*`、`google/*`、`xai/*` 直连语义补充官方 provider/model 说明、`litellm>=1.80.10,<1.82.7` 兼容依据引用，并明确示例模型名仅为配置保留行为说明而非可用性背书。
- 明确 `price_change_percent` 事件告警仅为配置与运行时规则扩展，未变更模型/provider/base URL/LiteLLM 兼容语义；回退路径为关闭/移除 Event Monitor 配置。
- 同步 README、DEPLOY、full-guide、Anspire、AIHubMix 与 SerpAPI 相关说明，统一外链、配置口径和评审一致性说明。

### 测试

- 补齐 AI 配置页与 `task_queue` 的 LLM 运行时清理/同步回归证据：恢复渠道模型时保留 fallback、编辑模型列表期间不静默清空运行时选择，渠道无可用模型时清理失效 runtime 引用，并覆盖 legacy key 与 `cohere/*`、`google/*`、`xai/*` 直连 provider 保留语义。
- 覆盖 Web LLM 配置检测的细分错误分类，以及 JSON、tools、vision、stream 运行时 smoke 的显式触发路径。

## [3.14.2] - 2026-04-30

### 发布亮点

- 大盘复盘扩展到港股，并让 Bot `/market` 与 CLI/调度入口使用一致的交易日过滤语义。
- 问股与 Agent 链路增强配置缺失、决策 fallback 和多策略选择体验。
- LLM 与分析报告链路提升稳定性：非法 JSON 响应会继续尝试备用模型，LiteLLM DEBUG 日志默认降噪。
- 新增只读首次启动配置状态接口，为后续配置向导和 smoke run 奠定基础。

### 新功能

- 大盘复盘支持港股市场：`MARKET_REVIEW_REGION` 新增 `hk` 选项；`both` 扩展为 A股+港股+美股，并新增港股指数（HSI/HSTECH/HSCEI）复盘链路。
- 新增只读首次启动配置状态接口 `GET /api/v1/system/config/setup/status`，用于识别 LLM、Agent、自选股、通知和本地存储配置缺口；该接口不会重载运行时、写入 `.env` 或创建数据库文件。

### 改进

- 问股页面支持组合选择多个 Agent 策略。

### 修复

- Bot `/market` 命令复用 `get_open_markets_today()` / `compute_effective_region()` 做交易日过滤：结果作为 `override_region` 透传给 `run_market_review`；若结果为空字符串则跳过复盘并推送“今日相关市场休市”，与 CLI/调度入口行为一致。
- 问股 Agent 在未配置可用 LLM 时保留后端真实错误原因并维持 `done.success=false` 失败语义，避免前端把配置缺失误当成成功回答。
- Agent 模式未生成有效决策仪表盘时保留本地趋势分析的评分、趋势和操作建议，并将强买/强卖 fallback 归一到兼容的 `buy`/`sell` 决策类型，避免首页结果被 `50 / 观望 / 未知` 缺省值覆盖。
- 持仓快照现价缺失时不再静默回退为持仓成本；当天快照优先使用历史收盘价，仅在缺失时使用实时价 fallback，缺价持仓不再污染市值与未实现盈亏汇总，并为持仓明细返回价格来源、日期、stale 与缺价状态。
- 分析 Prompt 在注入 `trend_analysis` 前按最终 `trend_status` / `ma_alignment` 清洗互斥理由：空头结构移除看多理由、多头结构移除空头结构风险，并在事件/技术冲突与异常放量（>10 倍）时强制提示“事件先行、技术待确认”与量能降权。
- LLM 返回非 JSON 响应时同样触发备用模型切换：主模型成功返回但无法解析 JSON 时，不再立即降级为纯文本 fallback，而是依次尝试 `LITELLM_FALLBACK_MODELS` 中的备用模型；所有模型均无法返回合法 JSON 时，再降级为文本 fallback。
- LiteLLM 内部 DEBUG 日志默认压低到 WARNING，避免流式生成时 token 级日志污染 `stock_analysis_debug_*.log`；如需排查 LiteLLM 内部细节，可临时设置 `LITELLM_LOG_LEVEL=DEBUG`（Fixes #1156）。

### 文档

- 补充 LLM 配置指南与 FAQ，明确问股 Agent 对 `LITELLM_CONFIG` / `LLM_CHANNELS` / legacy `GEMINI_*` `OPENAI_*` `ANTHROPIC_*` 的兼容优先级、回退路径与“不静默迁移旧配置”的结论。

### 测试

- 新增 `tests/test_bot_market_command.py`，覆盖 `MARKET_REVIEW_REGION=both` + open markets `{"cn","us"}` / `{"cn","hk"}` 的 `override_region` 透传断言，并覆盖全市场休市跳过与关闭交易日检查路径；新增 `tests/test_yfinance_hk_indices.py` 覆盖港股指数符号映射与部分/全部失败降级路径。
- 补齐 `task_queue` 轻量导入 stub 的股票代码规范化函数，恢复 `tests/test_task_queue_config_sync.py` 收集与运行。

## [3.14.1] - 2026-04-26
- [测试] 修正大盘复盘 prompt 测试对“明日交易计划”标题的断言，并同步桌面端版本号，恢复发布 gate。

## [3.14.0] - 2026-04-26

### 发布亮点

- 📊 **大盘复盘升级为盘后工作台式结构** — A 股复盘固定输出盘面温度、指数明细、板块 Top 表、新闻催化、明日交易计划和风险提示，减少纯文字复盘的重复与空泛。
- 🖥️ **桌面端新增 GitHub Release 更新提醒** — Windows/macOS 桌面端启动后自动检测新版本，也可从设置页手动检查并跳转下载页。
- 🤖 **Pipeline Agent 数据加载大幅降噪** — K 线工具改为 DB-first 并预热 240 天历史数据，避免同一只股票重复 HTTP 请求。
- 🐳 **Docker 发布链路整理** — 发布工作流收敛为正式发布与手动补发两条路径，官方 Docker Hub 镜像名统一为 `zhulinsen/daily_stock_analysis`。
- 🔧 **LLM 渠道与 DeepSeek V4 配置补强** — GitHub Actions 定时分析补齐多渠道变量透传，DeepSeek 官方渠道预设与示例同步到 V4。
- 🧩 **桌面端静态资源一致性校验** — 打包链路和运行时都能更早发现静态资源错配，降低 Release 包白屏排查成本。

### 新功能

- 🏠 **Web 首页历史报告区新增重新分析入口** — 支持基于原始 prompt 重做同一只股票同日期的分析。
- 🖥️ **Windows/macOS 桌面端新增 GitHub Release 更新提醒** — 启动后自动检测新版本，并支持从设置页手动检查后跳转下载页。

### 改进

- 📊 **A 股大盘复盘报告改为结构化盘后工作台版式** — 固定输出盘面温度、指数明细、板块 Top 表、新闻催化和明日交易计划。
- 🐳 **Docker 发布工作流收敛** — 更清晰地区分正式发布与手动补发链路，并统一官方 Docker Hub 镜像名为 `zhulinsen/daily_stock_analysis`。
- 🤖 **Agent 日线工具优先复用本地缓存** — 同时持久化新获取的日线与新闻情报，减少重复数据源调用。

### 修复

- 🤖 **Pipeline Agent K 线工具 DB-first 加载** — `get_daily_history` / `analyze_trend` / `calculate_ma` / `get_volume_analysis` / `analyze_pattern` 改为优先读取本地 DB，消除同一只股票 9x5=45 次重复 HTTP 请求（Fixes #1066）。
- 🤖 **Pipeline Agent 执行前按需预热 240 天 K 线历史到 DB** — 正常情况下 K 线工具调用无需重复网络请求。
- 🕒 **冻结 `target_date` 并通过 ContextVar 透传到 Pipeline Agent K 线工具线程** — 消除跨收盘边界时间漂移。
- 🪟 **Windows 桌面端后端日志转抄编码修复** — 转抄 stdout/stderr 时优先使用 UTF-8，并兼容本地代码页回退，避免中文日志乱码。
- ⚙️ **GitHub Actions 每日分析工作流补齐 LLM 渠道变量透传** — 支持 `LLM_CHANNELS`、多 Key 与常用 `LLM_<NAME>_*`，避免本地可用的多模型配置在云端定时任务中失效（Fixes #1063, #872）。
- 📈 **历史报告详情接口修正 `change_pct` 取值** — 使用 `is None` 判断避免把 0.0（平盘）当作缺失值丢弃，移除错误的 `change_60d` 兜底，并在缺失时回退到原始实时行情字段（Fixes #1084）。
- 🔧 **DeepSeek 官方渠道预设与示例配置同步到 V4** — 保留 legacy `deepseek-chat` 默认值并增加废弃提示，同时修正模型发现后旧运行时选择导致保存失败的问题（Fixes #1108, #1109）。
- 🧩 **桌面端打包链路新增静态资源一致性检查** — `scripts/check_static_assets.py` 会在源 `static/` 与 PyInstaller 产物中校验 `index.html` 引用的资源是否真实存在，运行时也会在错配时写入明确日志，避免重现 Release 包打开后白屏（Refs #1064 / #1065 / #1050）。
- 🧩 **后端 `/assets/*` 改为显式路由托管** — 资源缺失时返回与请求扩展名匹配的 `text/javascript` / `text/css` 404，减少默认 JSON 错误响应带来的排查误导（Refs #1064）。
- 🌙 **`kimi-k2.6` 自动使用固定温度** — 主分析、大盘复盘和 Agent 调用该模型时自动使用 `temperature=1.0`，避免模型拒绝默认温度请求（Fixes #1102）。

### 文档

- 🐳 **补充官方 Docker 镜像使用说明** — 增加镜像拉取、`docker run` 用法与 `.env` / 数据目录映射说明，不再只覆盖 Compose 部署路径。
- 📨 **修正飞书自定义机器人 Webhook 示例** — `feishu_sender.py` 中的示例改为 interactive card JSON，并补充飞书自动化 Webhook 触发器配置教程。
- 📚 **优化根 README 结构** — 保留首页级功能特性、技术栈、快速开始、推送效果、Web、Agent、赞助商和新闻源入口，将细配置、交易纪律和基本面语义收口到完整指南，并将 Docker 徽章指向官方镜像页。
- 🌐 **同步英文与繁中 README 的精简入口结构** — 同时补齐完整指南中的 LLM 用量 API 与持仓管理说明。
- 🤝 **调整 AI 协作与 PR 模板中的 README 维护规则** — 明确 README 非必要不更新，细节优先进入专题文档。

### 测试

- 🧪 **稳定市场复盘相关测试的 LiteLLM stub 行为** — 避免本机安装的 LiteLLM 在测试收集顺序变化时影响市场复盘单元测试。
- 🧪 **pytest 默认跳过前端依赖目录** — 本地存在 `apps/dsa-web/node_modules` 时不再被后端测试递归扫描，避免发布前 gate 被无关目录拖慢。

## [3.13.0] - 2026-04-21

### 发布亮点

- 🌉 **长桥 OpenAPI 数据源接入** — 美股/港股行情优先使用 Longbridge，YFinance / AkShare 自动兜底；未配置时行为不变。
- 📈 **Tushare 港股全链路扩展** — 港股日线通过 `hk_daily` 获取；筹码分布对港股返回 `None`；换算单位跟随港股口径，不再套用 A 股手/千元规则。
- 🔍 **Anspire Search 语义搜索接入** — 配置 `ANSPIRE_*` 后即可使用 Anspire Search 获取实时行情及资讯，未配置时完全透明。
- 🚀 **普通分析链路支持 LLM 流式生成** — 首页任务 SSE 新增 `task_progress` 事件，进度更细化；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web 渠道编辑器支持按需拉取可用模型列表** — `/v1/models` 统一模型发现入口，多选写回 `LLM_{CHANNEL}_MODELS`，拉取失败时保留手动输入降级。
- 🛡️ **Agent 稳定性与预算护栏全面补强** — `AGENT_MAX_STEPS` 语义统一、技能降级不中断管线、SSE 异常透传、技能加载 warning 日志补齐。
- 🛠️ **SQLite 写入链路原子化** — 批量原子 upsert + WAL + `busy_timeout` + 有限写入重试，显著降低批量分析并发锁竞争。

### 新功能

- 🌉 **集成 Longbridge OpenAPI 作为美股/港股可选数据源**（fixes #981）— 配置 `LONGBRIDGE_*` 后优先使用长桥获取日线与实时行情，YFinance / AkShare 兜底；未配置时行为与此前一致。联调使用 `tests/longbridge_live_smoke.py`（手动脚本，不参与 pytest 收集）。
- 📈 **Tushare 支持港股日线查询** — 配置 Tushare 凭证后调用 `hk_daily` 接口获取港股数据；权限不足时抛出异常，与原流程一致。
- 🔍 **集成 Anspire Search 可选语义搜索后端** — 配置 `ANSPIRE_*` 可使用 Anspire Search 获取实时行情及新闻资讯；未配置时行为与此前一致。联调使用 `tests/test_anspire_search.py`（手动脚本）。
- 🚀 **普通分析链路支持 LiteLLM 流式生成与更细任务进度** — 股票分析在 LLM 阶段优先尝试 `stream=True` 并在服务端累积 chunk，首页任务 SSE 新增 `task_progress` 事件与更细的 `message/progress` 更新；仅在最终 JSON 解析成功后持久化历史报告；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web AI 模型配置支持按渠道获取可用模型列表** — 渠道编辑器支持调用 `/v1/models` 拉取可用模型，并以多选方式写回 `LLM_{CHANNEL}_MODELS`；拉取失败时保留手动输入作为降级路径。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；仅对极少数高位且摘要不足的结果做延迟补抓，优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案统一为"主模型 / Agent 主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键保持兼容。
- 🧠 **IntelAgent 新增公司公告搜索与主力资金流工具** — 增加上交所/深交所/cninfo 公告搜索维度与 `get_capital_flow` 工具，修复 Agent 模式下公告和资金流数据经常缺失的问题。
- 📦 **后端股票名称解析优先复用 `stocks.index.json`** — 懒加载缓存前端静态索引，纯后端/缺失静态资源场景静默降级回 `STOCK_NAME_MAP` 与原有数据源回退链路。
- 📊 **TushareFetcher 港股单位适配** — `get_chip_distribution` 对港股直接返回 `None`（港股暂不支持筹码分布）；`_normalize_data` 对港股（`hk_daily`）不再做 A 股手→股、千元→元的缩放，与 Tushare 港股字段语义一致。
- ⏱️ **Agent 超步数错误增加 `AGENT_MAX_STEPS` 调整提示** — 帮助用户自助排查步数限制问题。
- ⚙️ **GitHub Actions 分析任务超时支持 `vars` 配置** — `daily_analysis.yml` 任务超时从 repository variables 读取，无需修改代码即可调整运行超时上限（fixes #1014）。

### 修复

- 📣 **大盘复盘链路接入 `REPORT_LANGUAGE`** — `REPORT_LANGUAGE=en` 时，A 股/合并复盘的 Prompt、章节标题、模板兜底文案与通知包装标题统一输出英文，避免英文正文搭配中文标题的混排问题。
- 📈 **EfinanceFetcher 指数开盘价映射兼容**（fixes #1043）— `get_main_indices()` 的开盘价映射改为兼容 `今开 → 开盘 → open`，修复部分 efinance 版本下指数开盘价被读成缺失值的问题。
- 🤖 **AGENT_MAX_STEPS 语义统一**（fixes #1026）— 在 orchestrator 多 Agent 模式下明确为"各子 Agent 步数上限而非硬覆盖"；TechnicalAgent 等高默认值 Agent 会被封顶，低默认值 Agent 保持原值；用户主动调高（>10）时统一覆盖所有子 Agent。修复了用户设置 12 但 TechnicalAgent 仍以默认 6 步运行并报 "Agent exceeded max steps" 的问题。
- 🛡️ **Specialist（Skill）Agent 失败改为优雅降级** — 技能 Agent 失败不再中断整个分析管线，与 intel/risk 保持相同的降级策略。
- 🔧 **MiniMax-M2.7 连接测试修复** — 修复 LLM 通道连接测试在 MiniMax-M2.7 下返回 "Empty response" 的问题；将 `max_tokens` 上限从 8 提升至 256 以容纳思考过程，并添加 `content_blocks` 格式解析逻辑。
- 📊 **移除 `sentiment_score` 范围约束**（fixes #942）— 移除 `HistoryItem` 与 `ReportSummary` 响应 Schema 中 `sentiment_score` 的 `ge=0/le=100` 约束，历史库中存储的超范围值不再触发 Pydantic ValidationError。
- 🖥️ **WebUI 前端资源缺失时发出明确警告** — `webui_frontend.py` 在 `static/index.html` 存在但 `static/assets/` 缺失时发出 warning，避免 CSS/JS 资源缺失导致页面异常变大却无从排查（fixes #944）。
- 🔗 **分析管线可选服务降级初始化** — `StockAnalysisPipeline` 搜索服务与社交舆情服务任一初始化异常时，记录 warning 并以禁用状态继续运行，避免外部依赖抖动阻塞主分析链路。
- 🖥️ **桌面端版本展示统一读取 `package.json`** — 统一读取 `apps/dsa-desktop/package.json`，移除 preload 中硬编码的 `0.1.0`，设置页展示真实桌面端版本；修复版本号显示错误（fixes #1048）。
- 🐋 **港股名称获取失败修复**（fixes #940）— 修复主数据源字段缺失时无法正确回退到备用字段获取港股名称的问题。
- 🔄 **SSE 任务流断开时 `CancelledError` 正确 re-raise**（fixes #967）— 修复 SSE 流中断时异常被静默吞掉导致故障无日志可查的问题。
- 🔄 **Agent SSE 清理阶段后台任务异常正确上报**（fixes #969）— 流结束时后台执行器异常现在正确记录并上报，避免错误无法感知。
- 🔇 **技能加载异常补充 `logger.warning` 日志**（fixes #970）— 在 `ask.py`、`skills/aggregator.py`、`skills/router.py` 的静默 except 块补充日志，确保技能列表为空时有日志可查。
- 🛠️ **SQLite 写入链路原子化**（fixes #878）— `stock_daily(code,date)` 使用批量原子 upsert；文件型 SQLite 连接默认启用 WAL + `busy_timeout` + 有限写入重试；"新增数"改按本次真正插入窗口计算。
- 💰 **多 Agent / 单 Agent 预算护栏语义统一** — 剩余预算低于最小阈值时主动跳过并降级；已完成阶段可构建降级报告时返回 `success=True` 并携带非空内容，否则返回 `success=False`。
- ⚙️ **GitHub Actions `daily_analysis.yml` 补齐 `REPORT_LANGUAGE` 注入**（fixes #1013）— 修复用户在 Secrets/Variables 中配置 `REPORT_LANGUAGE` 后不生效的问题。
- 📊 **任务状态 API 补齐实时价格字段**（fixes #983）— `GET /api/v1/analysis/status/{task_id}` 从数据库回填已完成任务时补齐 `current_price` / `change_pct`，修复首页报告股票名旁不显示实时价格的问题。
- 📅 **非交易日数据返回最近交易日**（fixes #1009）— 修复非交易日（周末/节假日）筹码分布与板块排行返回倒数第二个交易日数据的问题，现在正常返回最近交易日数据。
- 🔍 **A 股资讯搜索恢复中文优先** — `search_stock_news()` 在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面；非美股查询不再默认沿用 Brave 的 `en/US` 区域语言偏好。
- 📨 **飞书群机器人通知支持签名校验** — 飞书通知现在支持 `FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_KEYWORD`；Web 设置与文档明确区分 Webhook 推送模式和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 应用模式，降低误配风险。
- ⚡ **LLM 适配层新增 `RateLimitError` 和 `ContextWindowExceeded` 检测** — 识别并处理速率限制与上下文窗口超出错误，提升分析链路在高负载或长文本场景下的健壮性（fixes #1002）。

### 测试

- 🧪 **TushareFetcher 港股相关单元测试** — 新增 `get_chip_distribution` 筹码分布获取与 `_normalize_data` 港股/A 股/ETF 单位处理的单元测试，覆盖港股特殊路径。

### 文档

- 📘 **DEPLOY.md 补充 UI 元素异常变大排查步骤** — 新增重建 Docker 镜像或手动执行 `npm run build` 的排查指南；`deploy-webui-cloud.md` 同步更新。
- 📨 **飞书 Webhook 配置说明补全** — 强调 `FEISHU_WEBHOOK_URL` 是群通知必填项、签名校验须两端同时启用或关闭、`FEISHU_APP_SECRET` 仅用于应用/Stream Bot 模式；`.env.example` 补充内联注释；同步英文指南。
- 🤝 **FAQ 补充 Ollama 连接失败排障条目（Q12c）** — 覆盖服务未启动、URL 配置错误、模型前缀缺失、模型未下载、远程防火墙等 5 个检查点（fixes #854）。
- 🌉 **README 补充长桥数据源使用说明** — 中/英/繁 README 明确长桥"首选 / 兜底 / 未配置不调用"边界；`docs/` 内相对路径链接修复；`LONGBRIDGE_PRINT_QUOTE_PACKAGES` 配置与代码及 `.env.example` 对齐。
- 🐋 **Docker 安装场景版本说明** — 补充最小化文档，明确 Docker 安装场景下应以 Git tag / 镜像 tag 判断版本（fixes #1091）。

## [3.12.0] - 2026-04-01

### 发布亮点

- 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌，复用历史分析与 1 日回测结果，快速验证分析准确率。
- 🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有配置键保持兼容。
- 🐳 **Docker / WebUI 运行时稳态补强** — 修复系统设置保存后配置不生效、启动早期日志缺失、预构建静态资源复用等问题，降低容器化部署的运维摩擦。
- 🔒 **安全与并发稳定性同步增强** — Discord 入站 Webhook 补齐 Ed25519 验签，修复并发执行时共享状态未加锁、单股推送模式通知并发复用等问题。
- 🖥️ **桌面端与定时任务细节打磨** — Windows 安装器支持自选安装目录，内置定时调度器感知运行中 SCHEDULE_TIME 变更，断点续传改按市场时区判断。

### 新功能

- 📊 **回测页新增"次日验证 / 1 日窗口"视图** — 可按股票代码与分析日期范围查看 AI 预测、次日实际涨跌及筛选区间准确率，复用历史分析与 1 日回测结果实现。
- 🏷️ **Web 设置页新增版本信息卡片** — `apps/dsa-web` 现在会在构建时注入前端包版本与构建时间，系统设置页新增只读"版本信息"区块，展示 `WebUI 版本 / 构建标识 / 构建时间`；当 `package.json` 仍为占位版本 `0.0.0` 时，会自动回退为构建标识，方便 Docker 重建后快速确认当前静态资源是否已经生效。
- 🪟 **Windows 桌面安装器支持自选安装目录** — 安装器改为支持在安装向导中自定义安装目录，安装到非默认盘符后仍沿用现有打包态目录逻辑在安装目录旁读写 `.env`、`data/stock_analysis.db` 和 `logs/desktop.log`，同时保留 `win-unpacked` 免安装分发方式。安装器仅支持当前用户安装、已禁用管理员提权（`allowElevation: false`），并通过 NSIS `.onVerifyInstDir` 阻止选择系统保护目录。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案已统一收口为"主模型 / Agent 主模型 / 备选模型 / 模型渠道 / 高级模型路由配置"；Web 设置页、配置元数据、校验提示与中英文文档不再把 LiteLLM 当作普通用户默认必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键仍保持兼容。

### 修复

- 🚀 **启动早期失败时暴露真实根因** — `python main.py` 现在通过 stderr 暴露真实根因，bootstrap 阶段不再向硬编码 `logs/` 目录写入文件日志，文件日志推迟到 `config.log_dir` 可用后创建，避免健康启动在非预期路径残留日志文件。
- 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报"未找到前端项目，无法自动构建"，从而恢复 Docker 部署后的 WebUI 打开能力。
- 🐳 **Docker WebUI 系统设置保存后配置生效** — Docker 场景下 WebUI 保存 `STOCK_LIST`、`SCHEDULE_ENABLED`、`SCHEDULE_TIME`、`SCHEDULE_RUN_IMMEDIATELY`、`RUN_IMMEDIATELY` 后，`Config` 会优先读取持久化 `.env` 中的新值，避免被容器创建时注入的旧环境变量覆盖。
- 📈 **市场复盘 LLM max_tokens 提升** — 市场复盘生成链路将 LLM `max_tokens` 从 `2048` 提升到 `8192`，降低长复盘输出因 `MAX_TOKENS` 提前截断导致内容未完成的概率。
- ⏰ **内置定时调度器感知 SCHEDULE_TIME 运行时变更** — 调度器现在会在运行中感知 WebUI 保存后的 `SCHEDULE_TIME` 变化，并在下一轮检查时重绑 daily job。
- 🪟 **Windows Release 渠道编辑器保留 MiniMax 模型前缀** — 渠道模式下填写 `minimax/<模型名>` 时，后端归一化与 Web 设置页运行时模型列表都会保留该值原样，不再误改写成 `openai/minimax/<模型名>`。
- 🤖 **Discord 入站 Webhook 补齐 Ed25519 验签** — `DiscordPlatform` 现在会基于 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和原始请求体校验 Discord Interaction 签名；缺失签名头、公钥格式非法或签名不匹配时直接拒绝请求，同时对 timestamp 做 ±5 分钟时效窗口校验以防御重放攻击。
- ⚙️ **STOCK_GROUP_N / EMAIL_GROUP_N 配置关系明确化** — 明确与 `STOCK_LIST` 的关系，并在配置校验中对超出 `STOCK_LIST` 的邮件分组给出 warning。
- 🗓️ **断点续传改按市场时区和交易日历判断**（fixes #880）— 股票数据存在性检查不再直接使用服务器自然日，而是按 A 股 / 港股 / 美股各自市场时区解析"最新可复用交易日"。
- 📨 **单股推送模式不再并发复用共享通知实例** — `StockAnalysisPipeline.run()` 现在会保留个股分析并发，但把 `SINGLE_STOCK_NOTIFY=true` 下的即时通知挪到结果收集侧串行发送。
- 🔇 **实时行情降级提示收口为单次告警** — 分析主流程获取股票名称时不再提前触发一次实时行情查询，只有在全部数据源都不可用时才提示已降级为历史收盘价继续分析。
- 🔍 **A 股中文资讯搜索恢复中文优先** — `search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面。
- 🔒 **并发执行时共享状态补齐统一加锁** — 修复并发执行时共享状态缺少统一加锁的问题，避免多线程场景下的数据竞争。

### 测试

- 🧪 **补充设置页版本信息回归测试** — 新增 Web 设置页版本信息渲染断言，并覆盖占位版本 `0.0.0` 自动回退为构建标识的逻辑。
- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

## [3.11.0] - 2026-03-27

### 发布亮点

- 🎨 **Web 工作台完成一轮 UI 统一与双主题升级** — 首页、问股、回测、持仓和设置页进一步收口到统一设计 token、输入表面和状态表达；新增完整浅色主题，并支持浅色 / 深色一键切换与持久化保存。
- 🤖 **Bot / Agent 能力重新补回主分支** — 恢复 `/history`、`/strategies`、`/research` 等命令，`/ask` 继续支持多股对比与组合视角；Deep Research、事件监控与 schedule 轮询链路重新接回主线能力。
- 🔒 **安全性与运行稳态同步补强** — 修复 `X-Forwarded-For` 限流绕过风险，恢复 LiteLLM 官方 PyPI 安装路径，Tushare 初始化不再依赖本地 SDK，降低 Docker、桌面打包和环境重建时的脆弱点。
- 🖥️ **日常使用细节继续打磨** — 修复首页港股自动补全提交、登录页首屏主题闪烁、历史长股票名重叠，以及 Telegram Markdown 解析失败时整条通知发送中断等问题。

### 新功能

- 🎨 **全新浅色主题与双主题切换上线** — Web 工作台新增完整浅色主题，并支持在侧边栏中一键切换浅色 / 深色模式；主题选择会持久化保存，刷新页面后仍保持当前偏好。此次升级不是局部配色微调，而是对卡片层级、边界对比、输入表面、状态提示和页面背景做了一整套 light theme 重绘。
- 🤖 **补回主分支缺失的 Agent / Bot 能力** — `#648` / `#649` 已重新补回 `main`：Bot 恢复 `/history`、`/strategies`、`/research`，`/ask` 保留多股对比与组合视角；Deep Research 与 Event Monitor 的配置重新在 Web 设置页可见并可编辑，schedule 模式也重新接入事件告警轮询。

### 改进

- 🖥️ **核心页面统一到同一套工作台视觉语言** — `Home / Chat / Backtest / Portfolio / Settings` 进一步收口到共享设计 token、`input-surface` 输入体系、空态/错误态表达和抽屉遮罩语义，减少页面之间的视觉割裂与局部私有样式漂移。
- 💬 **问股交互可达性与反馈增强** — 问股页补强了会话导出、通知发送、消息复制、历史删除与追问上下文提示；AI 回复操作不再过度依赖 hover，触屏设备和小屏场景下也能直接触达关键按钮。
- 📊 **回测与持仓页表面和状态表达继续标准化** — 回测页筛选控件、布尔状态、结果表格与汇总卡片统一到共享输入/状态原语；持仓页的导入反馈、汇率刷新提示、空态与警示信息进一步归口到共享组件，减少页面级重复实现。
- 🧭 **导航与页面壳层协同优化** — 侧边栏主题切换、问股完成角标、移动端抽屉遮罩和主内容滚动契约进一步统一，首页、问股和回测在桌面端与移动端的切页体验更稳定。

### 测试

- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

### 修复

- 🌗 **Web 首屏默认主题预设为深色** — `apps/dsa-web/index.html` 现在会在 React 挂载前读取本地保存的主题偏好；若没有已保存值，则立即给 `<html>` 预设 `dark` 并同步 `color-scheme`，避免首页和登录页首屏先闪出浅色主题。
- 🔐 **登录页独立主题层收口** — 登录页输入框、标签、切换按钮和按钮文案现在使用独立的 `--login-*` 视觉 token，不再继承全局浅/深主题文字色；即使浏览器缓存了浅色主题，登录页仍保持稳定的深色视觉与青色密码输入表现，避免密码圆点和文案落成黑色。
- 🖥️ **首页港股代码输入修复** — Web 首页分析输入框现在可正确接受港股代码与自动完成选中的港股项，补齐 `00700.HK` / `HK00700` 等格式识别，避免提交时误报“请输入有效的股票代码或股票名称”。

- 🔒 **认证限流 X-Forwarded-For 取值修复（CWE-345）**（#841 / #842）— `get_client_ip()` 从取 `X-Forwarded-For` 最左值改为最右值，防止攻击者通过伪造首部旋转限流桶绕过暴力破解保护；仅影响 `TRUST_X_FORWARDED_FOR=true` 且单层可信反向代理的部署场景，多级代理环境需按部署文档评估配置。
- 📦 **恢复 LiteLLM 官方 PyPI 安装并锁定安全上限** — `requirements.txt` 重新使用 `pip install litellm` 的官方 PyPI 安装路径，并在保留历史最低要求 `>=1.80.10` 的同时增加 `<1.82.7` 的安全上限，避免误装已被移除的 `1.82.7` / `1.82.8` 风险版本；Windows 桌面打包脚本也同步回退到标准 `pip install -r requirements.txt` 链路，减少特殊下载分支带来的维护成本。
- 📨 **Telegram Markdown 解析失败回退纯文本**（fixes #850）— `src/notification_sender/telegram_sender.py` 现在会在 Telegram 返回 `HTTP 400` 且包含 `can't parse entities` / Markdown 解析错误时，自动去掉 `parse_mode` 后重试纯文本发送，避免 `*ST` 等正文内容直接导致整条通知失败。
- 🔢 **A 股同码实时行情保留交易所提示**（fixes #852）— `DataFetcherManager` 与 `TushareFetcher` 现在会保留 `SZ000001` / `000001.SZ` 这类显式沪深提示，旧版 Tushare 实时行情降级分支不再把深市 `000001` 误判成 `sh000001` 上证指数。
- 🎯 **多 Agent 次优买点不再盲目复制理想买点**（fixes #851）— 当多智能体结果缺少独立 `secondary_buy` 时，仪表盘现在优先展示 `N/A` 而不是把 fallback 值硬拷贝成与 `ideal_buy` 完全相同，减少误导性的双买点展示。
- 🧩 **Tushare 初始化不再强依赖本地 SDK 包** — `TushareFetcher` 现在直接使用内置 HTTP client 访问 Tushare Pro，不再在启动阶段先 `import tushare` 才能初始化；修复了 Docker、桌面打包或环境重建后因缺少 `tushare` 包而提前报 `No module named 'tushare'` 的问题，并补充对应回归测试。
- ⚙️ **`daily_analysis` 工作流补齐 `DEEPSEEK_API_KEY` 映射** — GitHub Actions 每日分析工作流现在会正确透传 `DEEPSEEK_API_KEY`，避免云端任务配置了密钥却在运行时拿不到对应环境变量。
- 🖥️ **历史列表过长股票名称截断与悬停展示**（fixes #815）— 历史列表中过长的股票名称, 现在会按字符类型自动截断（英文15/中文8/混合10字符），默认显示截断结果，悬停时展示完整名称；解决 1920x1080 分辨率下股票名称与右侧状态标签文字重叠的问题。新增 `stockName.ts` 工具函数并补充对应测试。

### 文档

- 🧾 **README 捐赠入口更新为小红书二维码** — README 及中英文说明中的赞助入口更新为小红书二维码素材，保持展示口径一致。

## [3.10.1] - 2026-03-24

### 新功能

- 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框，默认勾选；取消勾选时本次分析不发送 Telegram/企业微信等推送。API `POST /api/v1/analysis/analyze` 新增 `notify` 字段（`bool`，默认 `true`），不传时行为与修改前一致，Bot 和定时任务不受影响。

### 改进

- 🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径，移除部分硬编码高度限制，让导航框架内的填充与滚动行为更连贯。
- 🎨 **全局视觉与共享组件继续收敛** — Light theme 引入动态 HSL 阴影体系，统一侧边栏激活态、告警组件对比度和聊天气泡样式，并把部分零散内联样式收口为语义化 CSS 变量，提升一致性与可维护性。

### 修复

- 🖼️ **系统设置智能导入文件选择恢复** — 修复了“系统设置 > 基础设置 > 智能导入”模块中 “选择图片 / 选择文件” 两个按钮点击无响应的问题。
- 🖥️ **移动端滚动与交互层级修复** — 解决主题切换菜单在移动端被主内容遮挡的 z-index 冲突，并恢复首页长报告场景下的正常纵向滚动，不影响其他页面现有滚动行为。
- 🧾 **Markdown 纯文本复制清洗增强** — 改进纯文本导出算法，复制分析报告时会更稳定地清除表格分隔符等 Markdown 痕迹，提升分享和归档内容的纯净度。
- 🧠 **Trading philosophy injection 覆盖 legacy + Agent 全链路**（#810）— `GeminiAnalyzer`、单 Agent 模式和 skill-aware Prompt 现在共享同一套策略注入状态；只有隐式回落到内置默认 `bull_trend` 时才保留旧的趋势型提示，显式策略选择或自定义默认 skill 不再被偷偷叠加 `MA5>MA10>MA20` 多头基线。
- 🛠️ **后端 CI 依赖安装链路稳态化**（#835）— 拆分 backend gate 阶段、为依赖安装增加重试，并把 CI 用的 `litellm` 安装来源调整为更稳定的 GitHub 源，降低依赖解析抖动导致的 backend gate 偶发失败。
- 🪟 **Windows 桌面发版构建恢复 LiteLLM 安装兼容性** — `scripts/build-backend.ps1` 现在会先过滤 `requirements.txt` 中的 LiteLLM GitHub 源包，再下载对应 tag 的 zipball 到本地移除上游可选 `enterprise/` 目录后安装，绕过 Windows runner 上 Poetry 构建 wheel 时把目录误当文件打包导致的失败；同时补上 `pip install` 退出码检查，避免依赖安装失败后只在后续 `python-multipart` 校验阶段才暴露成次生报错。

### 测试

- 🧪 **问股 / 回测 / 智能导入回归覆盖补齐** — 同步更新 E2E 冒烟期望，补充 `DashboardStateBlock`、Chat 页、智能导入文件选择与相关交互回归断言，确保近期 UI 调整后的关键路径仍可稳定通过。

## [3.10.0] - 2026-03-24

### 发布亮点

- 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股，配套新增 Tushare 股票列表抓取工具与更完整的静态索引数据，让首页搜索入口从“能用”走向“更全、更稳”。
- 🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一；报告详情也补齐了 Markdown/纯文本复制与更可靠的按钮交互，减少历史报告查看与分享时的摩擦。
- 🤖 **Agent skill 与市场语义边界更清晰** — skill bundle、默认策略、回测汇总语义和兼容接口进一步收敛；同时分析 Prompt 不再默认写死 A 股上下文，美股和港股分析也能按各自市场规则生成更贴切的内容。
- ⏰ **定时与桌面配置能力更贴近真实使用场景** — 桌面端支持 `.env` 导入导出；`python main.py --schedule --stocks ...` 也不再把启动时股票快照错误带入后续计划执行，定时任务会跟随最新保存的 `STOCK_LIST`。
### 新功能

- 💾 **桌面端 `.env` 备份/恢复入口**（#754）— 桌面模式下的系统设置页新增 `导出 .env` / `导入 .env` 按钮，可直接备份当前已保存配置，或把备份文件中的键值合并恢复到当前桌面端 `.env`；导入沿用现有 `config_version` 冲突保护与运行时重载链路，不改变现有桌面端便携模式路径。
- 📊 **Tushare 股票列表获取工具** — 新增 `scripts/fetch_tushare_stock_list.py`，支持从 Tushare Pro 获取 A股、港股、美股列表信息并保存为 CSV，配有分页读取、智能限流、错误处理和进度提示；新增对应使用文档 `docs/TUSHARE_STOCK_LIST_GUIDE.md`。
- 🔎 **索引生成脚本多市场支持** — `generate_index_from_csv.py` 重构为支持 Tushare 和 AkShare 双数据源，同时覆盖 A股、港股、美股三个市场；新增按市场分类的别名映射（A股、港股常见别名，美股常用股票英文缩写）；添加 `--source` 参数切换数据源、`--test` 参数验证模式；严格过滤美股 DUMMY 记录。
- 🔎 **索引生成脚本增强** — `generate_stock_index.py` 新增 `--test`/`-t` 测试模式和 `--verbose`/`-v` 详细输出模式，添加市场分布统计，优化 JSON 输出格式。
- 📋 **首页完整报告支持双模式复制** — 历史报告详情头部新增“复制 Markdown 源码”和“复制纯文本”工具按钮；前者保留原始 Markdown 结构，后者去除常见 Markdown 格式符号，方便分享、归档和跨报告比对。复制按钮文案会跟随 `REPORT_LANGUAGE` 保持中英文一致，避免英文报告页出现中文固定文案。
- 🧩 **个股分析页补齐关联板块展示**（#669）— A 股分析写路径现在会把 `belong_boards` 一次性写入 `fundamental_context` / `fundamental_snapshot`，结构化报告详情同步新增 `belong_boards` 与 `sector_rankings` 字段，Web 个股分析页首屏可直接展示所属板块及其是否命中当日板块涨跌榜；无数据时保持 fail-open 隐藏，不影响现有分析主流程。

### 改进

- 🖥️ **Dashboard 面板统一化（PR7-2）** — 新增 `DashboardPanelHeader` 和 `DashboardStateBlock` 作为历史、报告、资讯、任务和透明度等面板的通用组件；统一了各面板标题层级、加载/空态/错误态和 CSS 变量 token。
- 🖥️ **HomePage 状态边界收口（PR7-2）** — 引入 `useHomeDashboardState` hook，集中 `stockPoolStore` 状态选取逻辑，移除 `HomePage` 中重复的本地状态派生和回调定义。
- 🧭 **Agent skill 统一到单一配置语义** — Multi-Agent runtime、API、Web chat 和配置元数据统一围绕 `skill` 概念收敛；`/api/v1/agent/skills` 成为主发现入口，`AGENT_SKILL_*` 成为主配置面，内置 skill 元数据也开始声明默认启用、排序优先级、market regime tag 等信息，减少默认策略散落在代码里的隐式耦合。
- 🔎 **自动补全索引数据更新** — 重新生成 `stocks.index.json`，涵盖 A股、港股、美股三个市场，提升自动补全覆盖率。
- 🧾 **Dashboard 字体与完整报告表格密度微调** — 收敛首页侧栏、空状态、历史操作区的字体层级，并将完整 Markdown 报告表格 `th/td` 的内边距调整到更紧凑的 4-6px 区间，让信息密度与现有 Dashboard 视觉节奏更一致。

### 修复

- ⏰ **定时模式不再锁定启动时 CLI 股票快照** — `python main.py --schedule --stocks ...` 现在不会让后续计划执行沿用启动时的旧股票列表；定时任务每次触发前都会重新读取最新保存的 `STOCK_LIST`，确保 WebUI 或 `.env` 更新后的自选股配置能参与后续推送。
- 🌍 **LLM Prompt 按股票市场动态注入上下文** — 分析链路不再把市场规则写死成 A 股；系统 Prompt 会根据股票代码识别 A 股、港股或美股，并注入对应的角色描述与交易规则提示，减少跨市场分析出现口径错位或结论失真的问题。
- 🔎 **美股自动补全复用 ticker 去重** — `generate_index_from_csv.py` 在导入 Tushare `us_basic` CSV 时会先按 `ts_code` 折叠复用的美股 ticker，优先保留更可能仍在使用的记录，避免 `stocks.index.json` 出现重复 `canonicalCode` 后让 Web 自动补全展示历史名称或提交歧义代码。
- 🧾 **Web 报告详情复制交互稳定性修复**（#749）— `ReportDetails` 中“原始分析结果 / 分析快照”的复制按钮补齐可点击层级，避免被下方 JSON 内容覆盖；两个面板的复制提示也改为各自独立，不再出现复制一个后两个按钮同时显示“已复制”的误导反馈。
- 📊 **Agent skill 回测与兼容接口语义收敛** — `get_skill_backtest_summary` 现在要求显式传入 `skill_id`，缺失时返回明确校验提示；仓库尚未持久化真实 skill 级汇总时会返回明确的 unsupported/info 响应，并保留 `normalized` 与 `*_pct` 兼容字段，避免沿用 overall 指标误导 Agent 或用户。
- 🔧 **Skill 默认选择与兼容层行为加固** — `allowed-tools` 会继续仅作为 `SKILL.md` bundle 元数据保留，不再泄露到运行时工具选择；`/api/v1/agent/strategies` 恢复旧 payload 形状；显式传入 `skills: []` 时会清空陈旧上下文；当用户明确选择策略 skill 时不再偷偷叠加默认 bull-trend，而在 `AGENT_SKILLS` 为空时则统一只回落到单一主默认 skill。

### 测试

- 🧪 **Dashboard 组件测试覆盖率扩展（PR7-2）** — 新增 `ReportNews` 和 `TaskPanel` 测试；对 `HistoryList`、`ReportDetails`、`HomePage`、`useDashboardLifecycle` 和 `stockPoolStore` 增强了断言覆盖，包括删除回退、移动端抽屉和任务生命周期等场景。
- 🧪 **多市场索引生成测试补齐** — 新增 `tests/test_generate_index_from_csv.py`，覆盖 Tushare/AkShare 双数据源解析、多市场判断、美股 DUMMY 过滤与重复 ticker 去重等核心路径。
- 🧪 **关联板块写入与 API 契约回归** — 新增 `tests/test_pipeline_related_boards.py`，并补充分析历史与分析接口契约测试，确保 `belong_boards` / `sector_rankings` 只做增量扩展且保持 fail-open。
- 🧪 **定时模式股票列表语义回归测试** — 新增 `tests/test_main_schedule_mode.py`，覆盖定时模式忽略启动时 `--stocks` 快照、单次运行仍保留 CLI 股票覆盖的边界场景。

### 文档

- 📘 **新增 Tushare 股票列表工具文档** — 新增 `docs/TUSHARE_STOCK_LIST_GUIDE.md`，说明股票列表抓取工具的使用方法、数据格式和常见问题。
- 🌍 **补齐定时模式与关联板块的双语说明** — `docs/full-guide.md` / `docs/full-guide_EN.md` 现在明确说明 scheduled mode 会在每次执行前重新读取 `STOCK_LIST`，并同步补充个股关联板块展示能力说明，减少配置预期偏差。
- 🧭 **调整 Agent 术语兼容文案** — README、双语文档、设置页与问股界面继续以“策略”作为用户入口主称呼，同时补充 `skill` 作为内部统一命名，降低迁移期理解成本。

## [3.9.0] - 2026-03-20

### 发布亮点

- 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路，普通分析与 Agent 报告也可通过 `REPORT_LANGUAGE=zh|en` 输出统一语言，减少“英文内容 + 中文壳子”这类混排问题，并允许团队分别权衡主分析与 Agent 的成本、速度和能力。
- 🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全，支持代码、中文名、拼音和别名检索；同时 Dashboard 状态收口到统一 store，历史、报告、新闻与 Markdown 抽屉的交互更稳定，“Ask AI” 追问也会优先携带当前报告上下文。
- 💬 **通知与检索能力继续外扩** — 新增 Slack 一等通知渠道；SearXNG 在未配置自建实例时可以自动发现公共实例并按受控轮询降级；Tavily 时效新闻链路修复后，严格时效过滤不再错误丢光有效结果。
- 💼 **持仓与市场复盘链路更稳** — A 股 market review 可选接入 TickFlow 强化指数与涨跌统计；持仓账本写入改为串行化以缩小并发超卖窗口；汇率刷新入口和禁用态提示也更加清晰，减少用户误判。

### 新功能

- 🔎 **Web 股票自动补全 MVP** — 首页分析输入框新增本地索引驱动的自动补全，支持股票代码、中文名、拼音和别名匹配；选中候选后会提交 canonical code，并透传 `stock_name`、`original_query`、`selection_source` 到分析请求、任务状态和 SSE 事件；索引加载失败时自动退回旧输入模式，不阻断原有提交流程。同步补充了静态索引加载器、索引生成脚本和前后端契约测试。分阶段进行开发，第一阶段仅支持 A 股。
- 💬 **Slack 一等通知渠道** — 新增 Slack 原生通知支持，同时支持 Bot Token 和 Incoming Webhook 两种接入方式；同时配置时优先使用 Bot API，确保文本与图片发送到同一频道；Bot Token 模式支持图片上传（raw body POST，不使用 multipart）；新增 `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_WEBHOOK_URL` 配置项，GitHub Actions 工作流同步补齐对应 Secrets 传递。
- 🌍 **报告输出语言可配置**（Issue #758）— 新增 `REPORT_LANGUAGE=zh|en`，默认 `zh`；语言设置会同步注入普通分析与 Agent Prompt，并覆盖 Markdown/Jinja 模板、通知 fallback、历史/API `report_language` 元数据及 Web 报告页固定文案，避免“英文内容 + 中文壳子”的混合输出。
- 🚀 **Agent 与普通分析模型解耦**（Issue #692）— 新增 `AGENT_LITELLM_MODEL`（留空继承 `LITELLM_MODEL`，无前缀按 `openai/<model>` 归一）；Agent 执行链路与 `/api/v1/agent/models` 的 `is_primary/is_fallback` 标记改为基于 Agent 实际模型链路；系统配置与启动期校验补齐 `AGENT_LITELLM_MODEL` 的 `unknown_model/missing_runtime_source` 检查；Web 设置页新增 Agent 主模型选择并与渠道模式运行时配置同步。
- 🔎 **SearXNG 公共实例自动发现与受控轮询**（#752）— 新增 `SEARXNG_PUBLIC_INSTANCES_ENABLED`，在未配置 `SEARXNG_BASE_URLS` 时默认从 `searx.space` 拉取公共实例列表，并按受控轮询顺序选择实例；同次请求内遇到超时、连接错误、HTTP 非 200 或无效 JSON 会自动切换到下一个实例。已配置自建实例的用户保持原有优先级与语义不变；`daily_analysis` GitHub Actions 工作流也已支持显式透传该开关并在启动日志中展示当前状态。
- 📈 **TickFlow market review enhancement** (#632) — 新增可选 `TICKFLOW_API_KEY`；配置后，A 股大盘复盘的主要指数行情优先尝试 TickFlow；若当前 TickFlow 套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时立即回退到现有 `AkShare / Tushare / efinance` 链路；板块涨跌榜回退顺序保持不变。接入层同时适配了真实 SDK 契约：主指数查询按单次请求上限分批拉取，并将 TickFlow 返回的比例型 `change_pct` / `amplitude` 统一转换为项目内部的百分比口径。

### 改进

- **Dashboard state slice and workspace closure** — moved Home / Dashboard state into `stockPoolStore`, consolidated history selection, report loading, task syncing, polling refresh, and markdown drawer handling under a single state slice.
- **Dashboard panel standardization** — kept the current dashboard layout contract stable while unifying history, report, news, and markdown presentation with shared tokens, standardized states, and bounded in-panel scrolling for the history list.
- **Dashboard-to-chat follow-up bridge** — routed “Ask AI” follow-ups through report-context hydration instead of direct cross-page state coupling, while keeping chat sends usable when enriched history context is still loading.
- 💼 **持仓账本并发写入串行化**（#742）— 持仓源事件写入/删除现在会在 SQLite 下先获取串行化写锁，减少并发卖出把超售流水写入账本的窗口；直接持仓写接口在锁竞争时返回 `409 portfolio_busy`，CSV 导入保持逐条提交并把 busy 计入 `failed_count`。
- 💱 **持仓页汇率手动刷新入口补齐**（#748）— Web `/portfolio` 页面现在会在“汇率状态”卡片中展示“刷新汇率”按钮，直接调用现有 `POST /api/v1/portfolio/fx/refresh` 接口；刷新后会仅重载快照与风险数据，并以内联摘要反馈“已更新 / 仍 stale / 刷新失败”的结果，减少用户对 `fxStale` 长时间停留的误解。

### 修复

- 🔎 **Web 自动补全 Enter 提交语义修正** — 股票自动补全在搜索命中候选时不再默认高亮第一项；候选列表展开但用户尚未用方向键或鼠标明确选中时，按 Enter 会继续提交原始输入，避免手动输入被第一条候选静默覆盖。
- 🌍 **补齐 `REPORT_LANGUAGE` 启动解析与历史展示本地化边界** — `Config` 在启动时继续遵循“真实环境变量优先、`.env` 兜底”的既有语义，并在两者冲突时输出显式告警，减少 `REPORT_LANGUAGE` 来源不清带来的误判；同时 `/api/v1/history/{id}` 英文详情响应会同步本地化 `sentiment_label`，历史 Markdown 也会正确识别英文 `bias_status` 的风险等级 emoji，避免出现 `乐观` 或 `🚨Safe` 这类中英混排/误报展示。
- 📰 **Tavily 时效新闻检索发布时间映射修复**（#782）— Tavily 在股票新闻和严格时效的情报维度中现在会显式使用 `topic="news"`，并兼容 `published_date` / `publishedDate` 两种发布时间字段；修复了 Tavily 明明返回结果却在后续硬过滤阶段被全部记为 `drop_unknown` 丢弃的问题，同时将机构分析、业绩预期、行业分析等分析型维度恢复为宽源搜索，不再被统一压缩成新闻模式。
- 💱 **持仓页汇率刷新禁用语义修正**（#772）— 当 `PORTFOLIO_FX_UPDATE_ENABLED=false` 时，`POST /api/v1/portfolio/fx/refresh` 现在会返回显式 `refresh_enabled=false` 与 `disabled_reason`，Web `/portfolio` 页面会明确提示“汇率在线刷新已被禁用”，不再误报“当前范围无可刷新的汇率对”。
- 🤖 **Agent timeout and config hardening** — `AGENT_ORCHESTRATOR_TIMEOUT_S` now also protects the legacy single-agent ReAct loop, parallel tool batches stop waiting once the remaining budget is exhausted, and invalid numeric `.env` values fall back to safe defaults with warnings instead of crashing startup.
- 🌐 **CORS wildcard + credentials compatibility** — `CORS_ALLOW_ALL=true` no longer combines `allow_origins=["*"]` with credentialed requests, avoiding browser-side cross-origin failures in demo/development setups.
- 🧭 **Unavailable Agent settings hidden from Web UI** — Deep Research / Event Monitor controls are now treated as compatibility-only metadata in the current branch and are removed from the Settings page to avoid exposing non-functional toggles.

### 文档

- 新增 Ollama 本地模型配置说明，同步更新 `README.md` 与 `docs/README_EN.md`（Fixes #690）
- 完善 Ollama 配置说明：`docs/full-guide.md` / `docs/full-guide_EN.md` 环境变量表与 Note 补充 `OLLAMA_API_BASE`，避免英文用户误以为 Ollama 不能作为独立配置入口；合并重复的 `OLLAMA_API_BASE` 条目为单一条目
- 明确文档同步治理边界：补充 `README.md`、专题文档、双语文档与交付说明之间的默认同步规则，减少后续文档漂移

## [3.8.0] - 2026-03-17

### 发布亮点

- 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验，桌面端加载背景也完成对齐。
- 📈 **分析上下文继续补强** — 美股新增社交舆情情报，A 股补齐财报与分红结构化上下文，Tushare 新接入筹码分布和行业板块涨跌数据。
- 🔒 **运行稳定性与配置兼容性提升** — 退出登录会立即让旧会话失效，定时启动兼容旧配置，运行中的 `MAX_WORKERS` 调整和新闻时效窗口反馈更清晰。
- 💼 **持仓纠错链路更完整** — 超售会被前置拦截，错误交易/资金流水/公司行为可以直接删除回滚，便于修复脏数据。

### 新功能

- 📱 **美股社交舆情情报** — 新增 Reddit / X / Polymarket 社交媒体情绪数据源，为美股分析提供实时社交热度、情绪评分和提及量等补充指标；完全可选，仅在配置 `SOCIAL_SENTIMENT_API_KEY` 后对美股生效。
- 📊 **A 股财报与分红结构化增强**（Issue #710）— `fundamental_context.earnings.data` 新增 `financial_report` 与 `dividend` 字段；分红统一按“仅现金分红、税前口径”计算，并补充 `ttm_cash_dividend_per_share` 与 `ttm_dividend_yield_pct`；分析/历史 API 的 `details` 追加 `financial_report`、`dividend_metrics` 可选字段，保持 fail-open 与向后兼容。
- 🔍 **接入 Tushare 筹码与行业板块接口** — 新增筹码分布、行业板块涨跌数据获取能力，并统一纳入配置化数据源优先级；默认按上海时间区分盘中/盘后交易日取数，优先使用 Tushare 同花顺接口，必要时降级到东财。
- 🧱 **Web UI 基础骨架升级** — 重建共享设计令牌与通用组件，新增 App Shell、Theme Provider、侧边导航，并同步调整 Electron 加载背景，为 Web / Desktop 的统一体验打底。
- 🔐 **登录与系统设置流程重做** — 重构 Login、Settings 与 Auth 管理流程，补上显式的认证 setup-state 处理，并让 Web 端与运行时认证配置 API 行为对齐。
- 🧪 **前端回归与冒烟覆盖补强** — 新增并扩展登录、首页、聊天、移动端 Shell、设置页、回测入口等关键路径的组件测试与 Playwright smoke coverage。

### 变更

- 🧭 **页面接入新 Shell 布局契约** — Home、Chat、Settings、Backtest 已统一接入新的页面容器、抽屉和滚动约定，降低 UI 迁移期间的页面行为不一致。
- 💾 **设置页状态同步更稳** — 优化草稿保留、直接保存同步与冲突处理，减少模块级保存后前后端配置状态不一致的问题。
- 🎭 **登录页视觉基线回归** — 登录页恢复到既有 `006` 分支的视觉基线，同时保留新的认证状态逻辑和统一表单交互模型。
- 🏛️ **AI 协作治理资产加固** — 收敛并加强 `AGENTS.md`、`CLAUDE.md`、Copilot 指令和校验脚本的一致性约束，降低治理资产长期漂移风险。

### Added

- **Web UI foundation refresh** — rebuilt shared design tokens and common primitives, introduced the app shell, theme provider, sidebar navigation, and Electron loading background alignment for the upgraded desktop/web experience
- **Settings and auth workflow overhaul** — rebuilt the Login, Settings, and Auth management flows, added explicit auth setup-state handling, and aligned the Web UI with the runtime auth configuration APIs
- **UI regression coverage and smoke checks** — expanded targeted frontend tests and added Playwright smoke coverage for login, home, chat, mobile shell, settings, and backtest entry flows

### Changed

- **Shell-driven page integration** — aligned Home, Chat, Settings, and Backtest with the new shell layout contract so routing, drawer behavior, and page-level scrolling are consistent during the UI migration
- **Settings state consistency** — refined draft preservation, direct-save synchronization, and conflict handling so module-level saves no longer leave the page out of sync with backend config state
- **Login visual baseline** — restored the login page visual treatment to the established `006` branch baseline while keeping the newer auth-state logic and unified form interaction model

### 修复

- ⏰ **定时启动立即执行兼容旧配置**（Issue #726）— `SCHEDULE_RUN_IMMEDIATELY` 未设置时会回退读取 `RUN_IMMEDIATELY`，修复升级后旧 `.env` 在定时模式下的兼容性问题；同时澄清 `.env.example` / README 中两个配置项的适用范围，并注明 Outlook / Exchange 强制 OAuth2 暂不支持。
- 🧵 **运行期 `MAX_WORKERS` 配置生效与可解释性增强**（#633）— 修复异步分析队列未按 `MAX_WORKERS` 同步的问题；新增任务队列并发 in-place 同步机制（空闲即时生效、繁忙延后），并在设置保存反馈与运行日志中明确输出 `profile/max/effective`，减少“参数未生效”误解。
- 🔐 **退出登录立即失效现有会话** — `POST /api/v1/auth/logout` 现在会轮换 session secret，避免旧 cookie 在退出后仍可继续访问受保护接口；同浏览器标签页和并发页面会被同步登出。认证开启时，该接口也不再属于匿名白名单，未登录请求会返回 `401`，避免匿名请求触发全局 session 失效。
- 🧮 **Tushare 板块/筹码调用限流与跨日缓存修复** — 新增的 `trade_cal`、行业板块排行、筹码分布链路统一接入 `_check_rate_limit()`；交易日历缓存改为按自然日刷新，避免服务跨天运行后继续沿用旧交易日判断取数日期。
- 💼 **持仓超售拦截与错误流水恢复**（#718）— `POST /api/v1/portfolio/trades` 现在会在写入前校验可卖数量，超售返回 `409 portfolio_oversell`；持仓页新增交易 / 资金流水 / 公司行为删除能力，删除后会同步失效仓位缓存与未来快照，便于从错误流水中直接恢复。
- 📧 **邮件中文发件人名编码**（#708）— 邮件通知现在会对包含中文的 `EMAIL_SENDER_NAME` 自动做 RFC 2047 编码，并在异常路径补充 SMTP 连接清理，修复 GitHub Actions / QQ SMTP 下 `'ascii' codec can't encode characters` 导致的发送失败。
- 🐛 **港股 Agent 实时行情去重与快速路由** — 统一 `HK01810` / `1810.HK` / `01810` 等港股代码归一规则；港股实时行情改为直接走单次 `akshare_hk` 路径，避免按 A 股 source priority 重复触发同一失败接口；Agent 运行期对显式 `retriable=false` 的工具失败增加短路缓存，减少同轮分析中的重复失败调用。
- 📰 **新闻时效硬过滤与策略分窗**（#697）— 新增 `NEWS_STRATEGY_PROFILE`（`ultra_short/short/medium/long`）并与 `NEWS_MAX_AGE_DAYS` 统一计算有效窗口；搜索结果在返回后执行发布时间硬过滤（时间未知剔除、超窗剔除、未来仅容忍 1 天），并在历史 fallback 链路追加相同约束，避免旧闻再次进入“最新动态/风险警报”。

### 文档

- ☁️ **新增云服务器 Web 界面部署与访问教程**（Fixes #686）— 补充从云端部署到外部访问的落地说明，降低远程自托管门槛。
- 🌍 **补齐英文文档索引与协作文档** — 新增英文文档索引、贡献指南、Bot 命令文档，并补充中英双语 issue / PR 模板，方便中英文协作与外部贡献者理解项目入口。
- 🏷️ **本地化 README 补充 Trendshift badge** — 在多语言 README 中同步补上新版能力入口标识，减少中英文说明面不一致。

## [3.7.0] - 2026-03-15

### 新功能

- 💼 **持仓管理 P0 全功能上线**（#677，对应 Issue #627）
  - **核心账本与快照闭环**：新增账户、交易、现金流水、企业行为、持仓缓存、每日快照等核心数据模型与 API 端点；支持 FIFO / AVG 双成本法回放；同日事件顺序固定为 `现金 → 企业行为 → 交易`；持仓快照写入采用原子事务。
  - **券商 CSV 导入**：支持华泰 / 中信 / 招商首批适配，含列名别名兼容；两阶段接口（解析预览 + 确认提交）；`trade_uid` 优先、key-field hash 兜底的幂等去重；前导零股票代码完整保留。
  - **组合风险报告**：集中度风险（Top Positions + A 股板块口径）、历史回撤监控（支持回填缺失快照）、止损接近预警；多币种统一换算 CNY 口径；汲取失败时回退最近成功汇率并标记 stale。
  - **Web 持仓页**（`/portfolio`）：组合总览、持仓明细、集中度饼图、风险摘要、全组合 / 单账户切换；手工录入交易 / 资金流水 / 企业行为；内嵌账户创建入口；CSV 解析 + 提交闭环与券商选择器。
  - **Agent 持仓工具**：新增 `get_portfolio_snapshot` 数据工具，默认紧凑摘要，可选持仓明细与风险数据。
  - **事件查询 API**：新增 `GET /portfolio/trades`、`GET /portfolio/cash-ledger`、`GET /portfolio/corporate-actions`，支持日期过滤与分页。
  - **可扩展 Parser Registry**：应用级共享注册，支持运行时注册新券商；新增 `GET /portfolio/imports/csv/brokers` 发现接口。

- 🎨 **前端设计系统与原子组件库**（#662）
  - 引入渐进式双主题架构（HSL 变量化设计令牌），清理历史 Legacy CSS；重构 Button / Card / Badge / Collapsible / Input / Select 等 20+ 核心组件；新增 `clsx` + `tailwind-merge` 类名合并工具；提升历史记录、LLM 配置等页面可读性。

- ⚡ **分析 API 异步契约与启动优化**（#656）
  - 规范 `POST /api/v1/analysis/analyze` 异步请求的返回契约；优化服务启动辅助逻辑；修复前端报告类型联合定义与后端响应对齐问题。

### 修复

- 🔔 **Discord 环境变量向后兼容**（#659）：运行时新增 `DISCORD_CHANNEL_ID` → `DISCORD_MAIN_CHANNEL_ID` 的 fallback 读取；历史配置用户无需修改即可恢复 Discord Bot 通知；全部相关文档与 `.env.example` 对齐。
- 🔧 **GitHub Actions Node 24 升级**（#665）：将所有 GitHub 官方 actions 升级至 Node 24 兼容版本，消除 CI 日志中的 Node.js 20 deprecation warning（影响 2026-06-02 强制升级窗口）。
- 📅 **持仓页默认日期本地化**：手工录入表单默认日期改用本地时间（`getFullYear/Month/Date`），修复 UTC-N 时区用户在当天晚间出现日期偏移的问题。
- 🔁 **CSV 导入去重逻辑加固**：dedup hash 纳入行序号作为区分因子，确保同字段合法分笔成交不被误折叠；同时在 `trade_uid` 存在时也持久化 hash，防止混合来源重复写入。

### 变更

- `POST /api/v1/portfolio/trades` 在同账户内 `trade_uid` 冲突时返回 `409`。
- 持仓风险响应新增 `sector_concentration` 字段（增量扩展），原有 `concentration` 字段保持不变。
- 分析 API `analyze` 接口异步行为契约文档化；前端报告类型联合更新。

### 测试

- 新增持仓核心服务测试（FIFO / AVG 部分卖出、同日事件顺序、重复 `trade_uid` 返回 409、快照 API 契约）。
- 新增 CSV 导入幂等性、合法分笔成交不误去重、去重边界、风险阈值边界、汇率降级行为测试。
- 新增 Agent `get_portfolio_snapshot` 工具调用测试。
- 新增分析 API 异步契约回归测试。

## [3.6.0] - 2026-03-14

### Added
- 📊 **Web UI Design System** — implemented dual-theme architecture and terminal-inspired atomic UI components
- 📊 **UI Components Refactoring** — integrated `clsx` and `tailwind-merge` for robust class composition across Web UI

- 🗑️ **History batch deletion** — Web UI now supports multi-selection and batch deletion of analysis history; added `POST /api/v1/history/batch-delete` endpoint and `ConfirmDialog` component.
- 🔐 **Auth settings API** — new `POST /api/v1/auth/settings` endpoint to enable or disable Web authentication at runtime and set the initial admin password when needed
- openclaw Skill 集成指南 — 新增 [docs/openclaw-skill-integration.md](guide/openclaw-skill-integration.md)，说明如何通过 openclaw Skill 调用 DSA API
- ⚙️ **LLM channel protocol/test UX** — `.env` and Web settings now share the same channel shape (`LLM_CHANNELS` + `LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`); settings page adds per-channel connection testing, primary/fallback/vision model selection, and protocol-aware model prefixing
- 🤖 **Agent architecture Phase 0+1** — shared protocols (`AgentContext`, `AgentOpinion`, `StageResult`), extracted `run_agent_loop()` runner, `AGENT_ARCH` switch (`single`/`multi`), config registry entries
- 🔍 **Bot NL routing** — two-layer natural-language routing: cheap regex pre-filter (stock codes + finance keywords) → lightweight LLM intent parsing; controlled by `AGENT_NL_ROUTING=true`; supports multi-stock and strategy extraction
- 💬 **`/ask` multi-stock analysis** — comma or `vs` separated codes (max 5), parallel thread execution with 150s timeout (preserves partial results), Markdown comparison summary table at top
- 📋 **`/history` command** — per-user session isolation via `{platform}_{user_id}:{scope}` format (colon delimiter prevents prefix collision); lists both `/chat` and `/ask` sessions; view detail or clear
- 📊 **`/strategies` command** — lists available strategy YAML files grouped by category (趋势/形态/反转/框架) with ✅/⬜ activation status
- 🔧 **Backtest summary tools** — `get_strategy_backtest_summary` and `get_stock_backtest_summary` registered as read-only Agent tools
- ⚙️ **Agent auto-detection** — `is_agent_available()` auto-detects from `LITELLM_MODEL`; explicit `AGENT_MODE=true/false` takes full precedence
- 🏗️ **Multi-Agent orchestrator (Phase 2)** — `AgentOrchestrator` with 4 modes (`quick`/`standard`/`full`/`strategy`); drop-in replacement for `AgentExecutor` via `AGENT_ARCH=multi`; `BaseAgent` ABC with tool subset filtering, cached data injection, and structured `AgentOpinion` output
- 🧩 **Specialised agents (Phase 2-4)** — `TechnicalAgent` (8 tools, trend/MA/MACD/volume/pattern analysis), `IntelAgent` (news & sentiment, risk flag propagation), `DecisionAgent` (synthesis into Decision Dashboard JSON), `RiskAgent` (7 risk categories, two-level severity with soft/hard override)
- 📈 **Strategy system (Phase 3)** — `StrategyAgent` (per-strategy evaluation from YAML skills), `StrategyRouter` (rule-based regime detection → strategy selection), `StrategyAggregator` (weighted consensus with backtest performance factor)
- 🔬 **Deep Research agent (Phase 5)** — `ResearchAgent` with 3-phase approach (decompose → research sub-questions → synthesise report); token budget tracking; new `/research` bot command with aliases (`/深研`, `/deepsearch`)
- 🧠 **Memory & calibration (Phase 6)** — `AgentMemory` with prediction accuracy tracking, confidence calibration (activates after minimum sample threshold), strategy auto-weighting based on historical win rate
- 📊 **Portfolio Agent (Phase 7)** — `PortfolioAgent` for multi-stock portfolio analysis (position sizing, sector concentration, correlation risk, cross-market linkage, rebalance suggestions)
- 🔔 **Event-driven alerts (Phase 7)** — `EventMonitor` with `PriceAlert`, `VolumeAlert`, `SentimentAlert` rules; async checking, callback notifications, serializable persistence
- ⚙️ **New config entries** — `AGENT_ORCHESTRATOR_MODE`, `AGENT_RISK_OVERRIDE`, `AGENT_DEEP_RESEARCH_BUDGET`, `AGENT_MEMORY_ENABLED`, `AGENT_STRATEGY_AUTOWEIGHT`, `AGENT_STRATEGY_ROUTING` — all registered in `config.py` + `config_registry.py` (WebUI-configurable)

### Changed
- 🔐 **Auth password state semantics** — stored password existence is now tracked independently from auth enablement; when auth is disabled, `/api/v1/auth/status` returns `passwordSet=false` while preserving the saved password for future re-enable
- 🔐 **Auth settings re-enable hardening** — re-enabling auth with a stored password now requires `currentPassword`, and failed session creation rolls back the auth toggle to avoid lockout
- ♻️ **AgentExecutor refactored** — `_run_loop` delegates to shared `runner.run_agent_loop()`; removed duplicated serialization/parsing/thinking-label code
- ♻️ **Unified agent switch** — Bot, API, and Pipeline all use `config.is_agent_available()` instead of divergent `config.agent_mode` checks
- 📖 **README.md** — expanded Bot commands section (ask/chat/strategies/history), added NL routing note, updated agent mode description
- 📖 **.env.example** — added `AGENT_ARCH` and `AGENT_NL_ROUTING` configuration documentation
- 🔌 **Analysis API async contract** — `POST /api/v1/analysis/analyze` now documents distinct async `202` payloads for single-stock vs batch requests, and `report_type=full` is treated consistently with the existing full-report behavior

### Fixed
- 🐛 **Analysis API blank-code guardrails** — `POST /api/v1/analysis/analyze` now drops whitespace-only entries before batch enqueue and returns `400` when no valid stock code remains
- 🐛 **Bare `/api` SPA fallback** — unknown API paths now return JSON `404` consistently for both `/api/...` and the exact `/api` path
- 🎮 **Discord channel env compatibility** — runtime now accepts legacy `DISCORD_CHANNEL_ID` as a fallback for `DISCORD_MAIN_CHANNEL_ID`, and the docs/examples now use the same variable name as the actual workflow/config implementation
- 🐛 **Session secret rotation on Windows** — use atomic replace so auth toggles invalidate existing sessions even when `.session_secret` already exists
- 🐛 **Auth toggle atomicity** — persist `ADMIN_AUTH_ENABLED` before rotating session secret; on rotation failure, roll back to the previous auth state
- 🔧 **LLM runtime selection guardrails** — YAML 模式下渠道编辑器不再覆盖 `LITELLM_MODEL` / fallback / Vision；系统配置校验补上全部渠道禁用后的运行时来源检查，并修复 `vertexai/...` 这类协议别名模型被重复加前缀的问题
- 🐛 **Multi-stock `/ask` follow-up regressions** — portfolio overlay now shares the same timeout budget as the per-stock phase and is skipped on timeout instead of blocking the bot reply; `/history` now stores the readable per-stock summary instead of raw dashboard JSON; condensed multi-stock output now renders numeric `sniper_points` values
- 🐛 **Decision dashboard enum compatibility** — multi-agent `DecisionAgent` now keeps `decision_type` within the legacy `buy|hold|sell` contract and normalizes stray `strong_*` outputs before risk override, pipeline conversion, and downstream统计/通知汇总
- 🛟 **Multi-Agent partial-result fallback** — `IntelAgent` now caches parsed intel for downstream reuse, shared JSON parsing tolerates lightly malformed model output, and the orchestrator preserves/synthesizes a minimal dashboard on timeout or mid-pipeline parse failure instead of always collapsing to `50/观望/未知`
- 🐛 **Shared LiteLLM routing restored** — bot NL intent parsing and `ResearchAgent` planning/synthesis now reuse the same LiteLLM adapter / Router / fallback / `api_base` injection path as the main Agent flow, so `LLM_CHANNELS` / `LITELLM_CONFIG` / OpenAI-compatible deployments behave consistently
- 🐛 **Bot chat session backward compatibility** — `/chat` now keeps using the legacy `{platform}_{user_id}` session id when old history already exists, and `/history` can still list / view / clear those pre-migration sessions alongside the new `{platform}_{user_id}:chat` format
- 🐛 **EventMonitor unsupported rule rejection** — config validation/runtime loading now reject or skip alert types the monitor cannot actually evaluate yet, so schedule mode no longer silently accepts permanent no-op rules
- 🐛 **P0 基本面聚合稳定性修复** (#614) — 修复 `get_stock_info` 板块语义回归（新增 `belong_boards` 并保留 `boards` 兼容别名）、引入基本面上下文精简返回以控制 token、为基本面缓存增加最大条目淘汰，并补齐 ETF 总体状态聚合与 NaN 板块字段过滤，保证 fail-open 与最小入侵。
- 🔧 **GitHub Actions 搜索引擎环境变量补充** — 工作流新增 `MINIMAX_API_KEYS`、`BRAVE_API_KEYS`、`SEARXNG_BASE_URLS` 环境变量映射，使 GitHub Actions 用户可配置 MiniMax、Brave、SearXNG 搜索服务（此前 v3.5.0 已添加 provider 实现但缺少工作流配置）
- 🤖 **Multi-Agent runtime consistency** — `AGENT_MAX_STEPS` now propagates to each orchestrated sub-agent; added cooperative `AGENT_ORCHESTRATOR_TIMEOUT_S` budget to stop overlong pipelines before they cascade further
- 🔌 **Multi-Agent feature wiring** — `AGENT_RISK_OVERRIDE` now actively downgrades final dashboards on hard risk findings; `AGENT_MEMORY_ENABLED` now injects recent analysis memory + confidence calibration into specialised agents; multi-stock `/ask` now runs `PortfolioAgent` to add portfolio-level allocation and concentration guidance
- 🔔 **EventMonitor runtime wiring** — schedule mode can now load alert rules from `AGENT_EVENT_ALERT_RULES_JSON`, poll them at `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`, and send triggered alerts through the existing notification service
- 🛠️ **Follow-up stability fixes** — multi-stock `/ask` now falls back to usable text output when dashboard JSON parsing fails; EventMonitor skips semantically invalid rules instead of aborting schedule startup; background alert polling now runs independently of the main scheduled analysis loop
- 🧪 **Multi-Agent regression coverage** — added orchestrator execution tests for `run()`, `chat()`, critical-stage failure, graceful degradation, and timeout handling
- 🧹 **PortfolioAgent cleanup** — `post_process()` now reuses shared JSON parsing and removed stale unused imports
- 🚦 **Bot async dispatch** — `CommandDispatcher` now exposes `dispatch_async()`; NL intent parsing and default command execution are offloaded from the event loop, DingTalk stream awaits async handlers directly, and Feishu stream processing is moved off the SDK callback thread
- 🌐 **Async webhook handler** — new `handle_webhook_async()` function in `bot/handler.py` for use from async contexts (e.g. FastAPI); calls `dispatch_async()` directly without thread bridging
- 🧵 **Feishu stream ThreadPoolExecutor** — replaced unbounded per-message `Thread` spawning with a capped `ThreadPoolExecutor(max_workers=8)` to prevent thread explosion under message bursts
- 🔒 **EventMonitor safety** — `_check_volume()` now safely handles `get_daily_data` returning `None` (no tuple-unpacking crash); `on_trigger` callbacks support both sync and async callables via `asyncio.to_thread`/`await`
- 🧹 **ResearchAgent dedup** — `_filtered_registry()` now delegates to `BaseAgent._filtered_registry()` instead of duplicating the filtering logic
- 🧹 **Bot trailing whitespace cleanup** — removed W291/W293 whitespace issues across `bot/handler.py`, `bot/dispatcher.py`, `bot/commands/base.py`, `bot/platforms/feishu_stream.py`, `bot/platforms/dingtalk_stream.py`
- 🐛 **Dispatcher `_parse_intent_via_llm` safety** — replaced fragile `'raw' in dir()` with `'raw' in locals()` for undefined-variable guard in `JSONDecodeError` handler
- 🐛 **筹码结构 LLM 未填写时兜底补全** (#589) — DeepSeek 等模型未正确填写 `chip_structure` 时，自动用数据源已获取的筹码数据补全，保证各模型展示一致；普通分析与 Agent 模式均生效
- 🐛 **历史报告狙击点位显示原始文本** (#452) — 历史详情页现优先展示 `raw_result.dashboard.battle_plan.sniper_points` 中的原始字符串，避免 `analysis_history` 数值列把区间、说明文字或复杂点位压缩成单个数字；保留原有数值列作为回退
- 🐛 **Session prefix collision** — user ID `123` could see sessions of user `1234` via `startswith`; fixed with colon delimiter in session_id format
- 🐛 **NL pre-filter false positives** — `re.IGNORECASE` caused `[A-Z]{2,5}` to match common English words like "hello"; removed global flag, use inline `(?i:...)` only for English finance keywords
- 🐛 **Dotted ticker in strategy args** — `_get_strategy_args()` didn't recognize `BRK.B` as a stock code, leaving it in strategy text; now accepts `TICKER.CLASS` format
- ⏱️ **efinance 长调用挂起修复** (#660) — 为所有 efinance API 调用引入 `_ef_call_with_timeout()` 包装（默认 30 秒，可通过 `EFINANCE_CALL_TIMEOUT` 配置）；使用 `executor.shutdown(wait=False)` 确保超时后不再阻塞主线程，彻底消除 81 分钟挂起问题
- 🛡️ **类型安全内容完整性检查** (#660) — `check_content_integrity()` 现在将非字符串类型的 `operation_advice` / `analysis_summary` 视为缺失字段，避免下游 `get_emoji()` 因 `dict.strip()` 崩溃
- 📄 **报告保存与通知解耦** (#660) — `_save_local_report()` 不再依赖 `send_notification` 标志触发，`--no-notify` 模式下本地报告照常保存
- 🔄 **operation_advice 字典归一化** (#660) — Pipeline 和 BacktestEngine 现在将 LLM 返回的 `dict` 格式 `operation_advice` 通过 `decision_type`（不区分大小写）映射为标准字符串，防止因模型输出格式变化导致崩溃
- 🛡️ **runner.py usage None 防护** (#660) — `response.usage` 为 `None` 时不再抛出 `AttributeError`，回退为 0 token 计数
- 📋 **orchestrator 静默失败改为日志警告** (#660) — `IntelAgent` / `RiskAgent` 阶段失败现在记录 `WARNING` 而非静默跳过，便于诊断

### Notes
- ⚠️ **Multi-worker auth toggles** — runtime auth updates are process-local; multi-worker deployments must restart/roll workers to keep auth state consistent

## [3.5.0] - 2026-03-12

### Added
- 📊 **Web UI full report drawer** (Fixes #214) — history page adds "Full Report" button to display the complete Markdown analysis report in a side drawer; new `GET /api/v1/history/{record_id}/markdown` endpoint
- 📊 **LLM cost tracking** — all LLM calls (analysis, agent, market review) recorded in `llm_usage` table; new `GET /api/v1/usage/summary?period=today|month|all` endpoint returns aggregated token usage by call type and model
- 🔍 **SearXNG search provider** (Fixes #550) — quota-free self-hosted search fallback; priority: Bocha > Tavily > Brave > SerpAPI > MiniMax > SearXNG
- 🔍 **MiniMax web search provider** — `MiniMaxSearchProvider` with circuit breaker (3 failures → 300s cooldown) and dual time-filtering; configured via `MINIMAX_API_KEYS`
- 🤖 **Agent models discovery API** — `GET /api/v1/agent/models` returns available model deployments (primary/fallback/source/api_base) for Web UI model selector
- 🤖 **Agent chat export & send** (#495) — export conversation to .md file; send to configured notification channels; new `POST /api/v1/agent/chat/send`
- 🤖 **Agent background execution** (#495) — analysis continues when switching pages; badge notification on completion; auto-cancel in-progress stream on session switch
- 📝 **Report Engine P0** — Pydantic schema validation for LLM JSON; Jinja2 templates (markdown/wechat/brief) with legacy fallback; content integrity checks with retry; brief mode (`REPORT_TYPE=brief`); history signal comparison
- 📦 **Smart import** — multi-source import from image/CSV/Excel/clipboard; Vision LLM extracts code+name+confidence; name→code resolver (local map + pinyin + AkShare); confidence-tiered confirmation
- ⚙️ **GitHub Actions LiteLLM config** — workflow supports `LITELLM_CONFIG`/`LITELLM_CONFIG_YAML` for flexible AI provider configuration
- ⚙️ **Config engine refactor & system API** (#602) — unified config registry, validation and API exposure
- 📖 **LLM configuration guide** — new `docs/LLM_CONFIG_GUIDE.md` covering 3-tier config, quick start, Vision/Agent/troubleshooting

### Fixed
- 🐛 **analyze_trend always reports No historical data** (#600) — now fetches from DB/DataFetcher instead of broken `get_analysis_context`
- 🐛 **Chip structure fallback when LLM omits it** (#589) — auto-fills from data source chip data for consistent display across models
- 🐛 **History sniper points show raw text** (#452) — prioritizes original strings over compressed numeric values
- 🐛 **GitHub Actions ENABLE_CHIP_DISTRIBUTION configurable** (#617) — no longer hardcoded, supports vars/secrets override
- 🐛 **`.env` save preserves comments and blank lines** — Web settings no longer destroys `.env` formatting
- 🐛 **Agent model discovery fixes** — legacy mode includes LiteLLM-native providers; source detection aligned with runtime; fallback deployments no longer expanded per-key
- 🐛 **Stooq US stock previous close semantics** — no longer misuses open price as previous close
- 🐛 **Stock name prefetch regression** — prioritizes local `STOCK_NAME_MAP` before remote queries
- 🐛 **AkShare limit-up/down calculation** (#555) — fixed market analysis statistics
- 🐛 **AkShare Tencent source field index & ETF quote mapping** (#579)
- 🐛 **Pytdx stock name cache pagination** (#573) — prevents cache overflow
- 🐛 **PushPlus oversized report chunking** (#489) — auto-segments long content
- 🐛 **Agent chat cancel & switch** (#495) — cancel no longer misreports as failure; fast switch no longer overwrites stream state
- 🐛 **MiniMax search status in `/status` command** (#587)
- 🐛 **config_registry duplicate BOCHA_API_KEYS** — removed duplicate dict entry that silently overwrote config

### Changed
- 🔎 **Fetcher failure observability** — logs record start/success/failure with elapsed time, failover transitions; Efinance/Akshare include upstream endpoint and classified failure categories
- ♻️ **Data source resilience & cleanup** (#602) — fallback chain optimization
- ♻️ **Image extract API response extension** — new `items` field (code/name/confidence); `codes` preserved for backward compatibility
- ♻️ **Import parse error messages** — specific failure reasons for Excel/CSV; improved logging with file type and size

### Docs
- 📖 LLM config guide refactored for clarity (#583)
- 📖 `image-extract-prompt.md` with full prompt documentation
- 📖 AkShare fallback cache TTL documentation
## [3.4.10] - 2026-03-07

### Fixed
- 🐛 **EfinanceFetcher ETF OHLCV data** (#541, #527) — switch `_fetch_etf_data` from `ef.fund.get_quote_history` (NAV-only, no OHLCV, no `beg`/`end` params) to `ef.stock.get_quote_history`; ETFs now return proper open/high/low/close/volume/amount instead of zeros; remove obsolete NAV column mappings from `_normalize_data`
- 🐛 **tiktoken 0.12.0 `Unknown encoding cl100k_base`** (#537) — pin `tiktoken>=0.8.0,<0.12.0` in requirements.txt to avoid plugin-registration regression introduced in 0.12.0
- 🐛 **Web UI API error classification** (#540) — frontend no longer treats every HTTP 400 as the same "server/network" failure; now distinguishes Agent disabled / missing params / model-tool incompatibility / upstream LLM errors / local connection failures
- 🐛 **北交所代码识别失败** (#491, #533) — 8/4/92 开头的 6 位代码现正确识别为北交所；Tushare/Akshare/Yfinance 等数据源支持 .BJ 或 bj 前缀；Baostock/Pytdx 对北交所代码显式切换数据源；避免误判上海 B 股 900xxx
- 🐛 **狙击点位解析错误** (#488, #532) — 理想买入/二次买入等字段在无「元」字时误提取括号内技术指标数字；现先截去第一个括号后内容再提取

### Added
- **Markdown-to-image for dashboard report** (#455, #535) — 个股日报汇总支持 markdown 转图片推送（Telegram、WeChat、Custom、Email），与大盘复盘行为一致
- **markdown-to-file engine** (#455) — `MD2IMG_ENGINE=markdown-to-file` 可选，对 emoji 支持更好，需 `npm i -g markdown-to-file`
- **PREFETCH_REALTIME_QUOTES** (#455) — 设为 `false` 可禁用实时行情预取，避免 efinance/akshare_em 全市场拉取
- **Stock name prefetch** (#455) — 分析前预取股票名称，减少报告中「股票xxxxx」占位符
- 📊 **分析报告模型标记** (#528, #534) — 在分析报告 meta、报告末尾、推送内容中展示 `model_used`（完整 LLM 模型名）；Agent 多轮调用时记录并展示每轮实际使用的模型（支持 fallback 切换）

### Changed
- **Enhanced markdown-to-image failure warning** (#455) — 转图失败时提示具体依赖（wkhtmltopdf 或 m2f）
- **WeChat-only image routing optimization** (#455) — 仅配置企业微信图片时，不再对完整报告做冗余转图，避免误导性失败日志
- **Stock name prefetch lightweight mode** (#455) — 名称预取阶段跳过 realtime quote 查询，减少额外网络开销

## [3.4.9] - 2026-03-06

### Added
- 🧠 **Structured config validation** — `ConfigIssue` dataclass and `validate_structured()` with severity-aware logging; `CONFIG_VALIDATE_MODE=strict` aborts startup on errors
- 🖼️ **Vision model config** — `VISION_MODEL` and `VISION_PROVIDER_PRIORITY` for image stock extraction; provider fallback (Gemini → Anthropic → OpenAI → DeepSeek) when primary fails
- 🚀 **CLI init wizard** — `python -m dsa init` 3-step interactive bootstrap (model → data source → notification), 9 provider presets, incremental merge by default
- 🔧 **Multi-channel LLM support** with visual channel editor (#494)

### Changed
- ♻️ **Vision extraction** — migrated from gemini-3 hardcode to `litellm.completion()` with configurable model and provider fallback; `OPENAI_VISION_MODEL` deprecated in favor of `VISION_MODEL`
- ♻️ **Market analyzer** — uses `Analyzer.generate_text()` for LLM calls; fixes bypass and Anthropic `AttributeError` when using non-Router path
- ♻️ **Config validation refinements** — test_env output format syncs with `validate_structured` (severity-aware ✓/✗/⚠/·); Vision key warning when `VISION_MODEL` set but no provider API key; market_analyzer test covers `generate_market_review` fallback when `generate_text` returns None
- ⚙️ **Auto-tag workflow defaults to NO tag** — only tags when commit message explicitly contains `#patch`, `#minor`, or `#major`
- ♻️ **Formatter and notification refactor** (#516)

### Fixed
- 🐛 **STOCK_LIST not refreshed on scheduled runs** — `.env` or WebUI changes to `STOCK_LIST` now hot-reload before each scheduled analysis (#529)
- 🐛 **WebUI fails to load with MIME type error** — SPA fallback route now resolves correct `Content-Type` for JS/CSS files (#520)
- 🐛 **AstrBot sender docstring misplaced** — `import time` placed before docstring in `_send_astrbot`, causing it to become dead code
- 🐛 **Telegram Markdown link escaping** — `_convert_to_telegram_markdown` escaped `[]()` characters, breaking all Markdown links in reports
- 🐛 **Duplicate `discord_bot_status` field** in Config dataclass — second declaration silently shadowed the first
- 🧹 **Unused imports** — removed `shutil`/`subprocess` from `main.py`
- 🔧 **Config validation and Vision key check** (#525)

### Docs
- 📝 Clarified GitHub Actions non-trading-day manual run controls (`TRADING_DAY_CHECK_ENABLED` + `force_run`) for Issue #461 / PR #466

## [3.4.8] - 2026-03-02

### Fixed
- 🐛 **Desktop exe crashes on startup with `FileNotFoundError`** — PyInstaller build was missing litellm's JSON data files (e.g. `model_prices_and_context_window_backup.json`). Added `--collect-data litellm` to both Windows and macOS build scripts so the files are correctly bundled in the executable.

### CI
- 🔧 Cache Electron binaries on macOS CI runners to prevent intermittent EOF download failures when fetching `electron-vX.Y.Z-darwin-*.zip` from GitHub CDN
- 🔧 Fix macOS DMG `hdiutil Resource busy` error during desktop packaging

### Docs
- 📝 Clarify non-trading-day manual run controls for GitHub Actions (`TRADING_DAY_CHECK_ENABLED` + `force_run`) (#474)

## [3.4.7] - 2026-02-28

### Added
- 🧠 **CN/US Market Strategy Blueprint System** (#395) — market review prompt injects region-specific strategy blueprints with position sizing and risk trigger recommendations

### Fixed
- 🐛 **`TRADING_DAY_CHECK_ENABLED` env var and `--force-run` for GitHub Actions** (#466)
- 🐛 **Agent pipeline preserved resolved stock names** (#464) — placeholder names no longer leak into reports
- 🐛 **Code cleanup** (#462, Fixes #422)
- 🐛 **WebUI auto-build on startup** (#460)
- 🐛 **ARCH_ARGS unbound variable** (#458)
- 🐛 **Time zone inconsistency & right panel flash** (#439)

### Docs
- 📝 Clarify potential ambiguities in code (#343)
- 📝 ENABLE_EASTMONEY_PATCH guidance for Issue #453 (#456)

## [3.4.0] - 2026-02-27

### Added
- 📡 **LiteLLM Direct Integration + Multi API Key Support** (#454, Fixes #421 #428)
  - Removed native SDKs (google-generativeai, google-genai, anthropic); unified through `litellm>=1.80.10`
  - New config: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `GEMINI_API_KEYS`, `ANTHROPIC_API_KEYS`, `OPENAI_API_KEYS`
  - Multi-key auto-builds LiteLLM Router (simple-shuffle) with 429 cooldown
  - **Breaking**: `.env` `GEMINI_MODEL` (no prefix) only for fallback; explicit config must include provider prefix

### Changed
- ♻️ **Notification Refactoring** (#435) — extracted 10 sender classes into `src/notification_sender/`

### Fixed
- 🐛 LLM NoneType crash, history API 422, sniper points extraction
- 🐛 Auto-build frontend on WebUI startup — `WEBUI_AUTO_BUILD` env var (default `true`)
- 🐛 Docker explicit project name (#448)
- 🐛 Bocha search SSL retry (#445, #446) — transient errors retry up to 3 times
- 🐛 Gemini google-genai SDK migration (Fixes #440, #444)
- 🐛 Mobile home page scrolling (Fixes #419, #433)
- 🐛 History list scroll reset (#431)
- 🐛 Settings save button false positive (fixes #417, #430)

## [3.3.22] - 2026-02-26

### Added
- 💬 **Chat History Persistence** (Fixes #400, #414) — `/chat` page survives refresh, sidebar session list
- 🎨 Project VI Assets — logo icon set, PSD, vector, banner (#425)
- 🚀 Desktop CI Auto-Release (#426) — Windows + macOS parallel builds

### Fixed
- 🐛 Agent Reasoning 400 & LiteLLM Proxy (fixes #409, #427)
- 🐛 Discord chunked sending (#413) — `DISCORD_MAX_WORDS` config
- 🐛 yfinance shared DataFrame (#412)
- 🐛 sniper_points parsing (#408)
- 🐛 Agent framework category missing (#406)
- 🐛 Date inconsistency & query id (fixes #322, #363)

## [3.3.12] - 2026-02-24

### Added
- 📈 **Intraday Realtime Technical Indicators** (Issue #234, #397) — MA calculated from realtime price, config: `ENABLE_REALTIME_TECHNICAL_INDICATORS`
- 🤖 **Agent Strategy Chat** (#367) — full ReAct pipeline, 11 YAML strategies, SSE streaming, multi-turn chat
- 📢 PushPlus Group Push — `PUSHPLUS_TOPIC` (#402)
- 📅 Trading Day Check (Issue #373, #375) — `TRADING_DAY_CHECK_ENABLED`, `--force-run`

### Fixed
- 🐛 DeepSeek reasoning mode (Issue #379, #386)
- 🐛 Agent news intel persistence (Fixes #396, #405)
- 🐛 Bare except clauses replaced with `except Exception` (#398)
- 🐛 UUID fallback for HTTP non-secure context (fixes #377, #381)
- 🐛 Docker DNS resolution (Fixes #372, #374)
- 🐛 Agent session/strategy bugs — multiple follow-up fixes for #367
- 🐛 yfinance parallel download data filtering

### Changed
- Market review strategy consistency — unified cn/us template
- Agent test assertions updated (`6 -> 11`)


## [3.2.11] - 2026-02-23

### 修复（#patch）
- 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
  - 根因：`get_analysis_context` 仅返回 2 天数据且无 `raw_data`，pipeline 中 `raw_data in context` 始终为 False
  - 修复：Step 3 直接调用 `get_data_range` 获取 90 日历天（约 60 交易日）历史数据用于趋势分析
  - 改善：趋势分析失败时用 `logger.warning(..., exc_info=True)` 记录完整 traceback

## [3.2.10] - 2026-02-22

### 新增
- ⚙️ 支持 `RUN_IMMEDIATELY` 配置项，设为 `true` 时定时任务触发后立即执行一次分析，无需等待首个定时点

### 修复
- 🐛 修复 Web UI 页面居中问题
- 🐛 修复 Settings 返回 500 错误

## [3.2.9] - 2026-02-22

### 修复
- 🐛 **ETF 分析仅关注指数走势**（Issue #274）
  - 美股/港股 ETF（如 VOO、QQQ）与 A 股 ETF 不再纳入基金公司层面风险（诉讼、声誉等）
  - 搜索维度：ETF/指数专用 risk_check、earnings、industry 查询，避免命中基金管理人新闻
  - AI 提示：指数型标的分析约束，`risk_alerts` 不得出现基金管理人公司经营风险

## [3.2.8] - 2026-02-21

### 修复
- 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
  - BOT `/analyze` 与 WEB UI 触发分析的股票代码统一为大写（如 `aapl` → `AAPL`）
  - 新增 `canonical_stock_code()`，在 BOT、API、Config、CLI、task_queue 入口处规范化
  - 历史记录与任务去重逻辑可正确识别同一股票（大小写不再影响）

## [3.2.7] - 2026-02-20

### 新增
- 🔐 **Web 页面密码验证**（Issue #320, #349）
  - 支持 `ADMIN_AUTH_ENABLED=true` 启用 Web 登录保护
  - 首次访问在网页设置初始密码；支持「系统设置 > 修改密码」和 CLI `python -m src.auth reset_password` 重置

## [3.2.6] - 2026-02-20
### ⚠️ 破坏性变更（Breaking Changes）

- **历史记录 API 变更 (Issue #322)**
  - 路由变更：`GET /api/v1/history/{query_id}` → `GET /api/v1/history/{record_id}`
  - 参数变更：`query_id` (字符串) → `record_id` (整数)
  - 新闻接口变更：`GET /api/v1/history/{query_id}/news` → `GET /api/v1/history/{record_id}/news`
  - 原因：`query_id` 在批量分析时可能重复，无法唯一标识单条历史记录。改用数据库主键 `id` 确保唯一性
  - 影响范围：使用旧版历史详情 API 的所有客户端需同步更新

### 修复
- 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常，统一美股历史数据源为 YFinance（Issue #311）
- 🐛 **历史记录查询和显示问题 (Issue #322)**
  - 修复历史记录列表查询中日期不一致问题：使用明天作为 endDate，确保包含今天全天的数据
  - 修复服务器 UI 报告选择问题：原因是多条记录共享同一 `query_id`，导致总是显示第一条。现改用 `analysis_history.id` 作为唯一标识
  - 历史详情、新闻接口及前端组件已全面适配 `record_id`
  - 新增后台轮询（每 30s）与页面可见性变更时静默刷新历史列表，确保 CLI 发起的分析完成后前端能及时同步，使用 `silent` 模式避免触发 loading 状态
- 🐛 **美股指数实时行情与日线数据** (Issue #273)
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 ^GSPC）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源
  - 消除重复的美股识别逻辑，统一使用 `is_us_stock_code()` 函数

### 优化
- 🎨 **首页输入栏与 Market Sentiment 布局对齐优化**
  - 股票代码输入框左缘与历史记录 glass-card 框左对齐
  - 分析按钮右缘与 Market Sentiment 外框右对齐
  - Market Sentiment 卡片向下拉伸填满格子，消除与 STRATEGY POINTS 之间的空隙
  - 窄屏时输入栏填满宽度，响应式对齐保持一致

## [3.2.5] - 2026-02-19

### 新增
- 🌍 **大盘复盘可选区域**（Issue #299）
  - 支持 `MARKET_REVIEW_REGION` 环境变量：`cn`（A股）、`us`（美股）、`both`（两者）
  - us 模式使用 SPX/纳斯达克/道指/VIX 等指数；both 模式可同时复盘 A 股与美股
  - 默认 `cn`，保持向后兼容

## [3.2.4] - 2026-02-18

### 修复
- 🐛 **统一美股数据源为 YFinance**（Issue #311）
  - akshare 美股复权数据异常，统一美股历史数据源为 YFinance
  - 修复 ADBE 等美股股票技术指标矛盾问题

## [3.2.3] - 2026-02-18

### 修复
- 🐛 **标普500实时数据缺失**（Issue #273）
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 `^GSPC`）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源

## [3.2.2] - 2026-02-16

### 新增
- 📊 **PE 指标支持**（Issue #296）
  - AI System Prompt 增加 PE 估值关注
- 📰 **新闻时效性筛查**（Issue #296）
  - `NEWS_MAX_AGE_DAYS`：新闻最大时效（天），默认 3，避免使用过时信息
- 📈 **强势趋势股乖离率放宽**（Issue #296）
  - `BIAS_THRESHOLD`：乖离率阈值（%），默认 5.0，可配置
  - 强势趋势股（多头排列且趋势强度 ≥70）自动放宽乖离率到 1.5 倍

## [3.2.1] - 2026-02-16

### 新增
- 🔧 **东财接口补丁可配置开关**
  - 支持 `EFINANCE_PATCH_ENABLED` 环境变量开关东财接口补丁（默认 `true`）
  - 补丁不可用时可降级关闭，避免影响主流程

## [3.2.0] - 2026-02-15

### 新增
- 🔒 **CI 门禁统一（P0）**
  - 新增 `scripts/ci_gate.sh` 作为后端门禁单一入口
  - 主 CI 改为 `backend-gate`、`docker-build`、`web-gate` 三段式
  - CI 触发改为所有 PR，避免 Required Checks 因路径过滤缺失而卡住合并
  - `web-gate` 支持前端路径变更按需触发
  - 新增 `network-smoke` 工作流承载非阻断网络场景回归
- 📦 **发布链路收敛（P0）**
  - `docker-publish` 调整为 tag 主触发，并增加发布前门禁校验
  - 手动发布增加 `release_tag` 输入与 semver/changelog 强校验
  - 发布前新增 Docker smoke（关键模块导入）
- 📝 **PR 模板升级（P0）**
  - 增加背景、范围、验证命令与结果、回滚方案、Issue 关联等必填项
- 🤖 **AI 审查覆盖增强（P0）**
  - `pr-review` 纳入 `.github/workflows/**` 范围
  - 新增 `AI_REVIEW_STRICT` 开关，可选将 AI 审查失败升级为阻断

## [3.1.13] - 2026-02-15

### 新增
- 📊 **仅分析结果摘要**（Issue #262）
  - 支持 `REPORT_SUMMARY_ONLY` 环境变量，设为 `true` 时只推送汇总，不含个股详情
  - 默认 `false`，多股时适合快速浏览

## [3.1.12] - 2026-02-15

### 新增
- 📧 **个股与大盘复盘合并推送**（Issue #190）
  - 支持 `MERGE_EMAIL_NOTIFICATION` 环境变量，设为 `true` 时将个股分析与大盘复盘合并为一次推送
  - 默认 `false`，减少邮件数量、降低被识别为垃圾邮件的风险

## [3.1.11] - 2026-02-15

### 新增
- 🤖 **Anthropic Claude API 支持**（Issue #257）
  - 支持 `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_TEMPERATURE`、`ANTHROPIC_MAX_TOKENS`
  - AI 分析优先级：Gemini > Anthropic > OpenAI
- 📷 **从图片识别股票代码**（Issue #257）
  - 上传自选股截图，通过 Vision LLM 自动提取股票代码
  - API: `POST /api/v1/stocks/extract-from-image`；支持 JPEG/PNG/WebP/GIF，最大 5MB
  - 支持 `OPENAI_VISION_MODEL` 单独配置图片识别模型
- ⚙️ **通达信数据源手动配置**（Issue #257）
  - 支持 `PYTDX_HOST`、`PYTDX_PORT` 或 `PYTDX_SERVERS` 配置自建通达信服务器

## [3.1.10] - 2026-02-15

### 新增
- ⚙️ **立即运行配置**（Issue #332）
  - 支持 `RUN_IMMEDIATELY` 环境变量，`true` 时定时任务启动后立即执行一次
- 🐛 修复 Docker 构建问题

## [3.1.9] - 2026-02-14

### 新增
- 🔌 **东财接口补丁机制**
  - 新增 `patch/eastmoney_patch.py` 修复 efinance 上游接口变更
  - 不影响其他数据源的正常运行

## [3.1.8] - 2026-02-14

### 新增
- 🔐 **Webhook 证书校验开关**（Issue #265）
  - 支持 `WEBHOOK_VERIFY_SSL` 环境变量，可关闭 HTTPS 证书校验以支持自签名证书
  - 默认保持校验，关闭存在 MITM 风险，仅建议在可信内网使用

## [3.1.7] - 2026-02-14

### 修复
- 🐛 修复包导入错误（package import error）

## [3.1.6] - 2026-02-13

### 修复
- 🐛 修复 `news_intel` 中 `query_id` 不一致问题

## [3.1.5] - 2026-02-13

### 新增
- 📷 **Markdown 转图片通知**（Issue #289）
  - 支持 `MARKDOWN_TO_IMAGE_CHANNELS` 配置，对 Telegram、企业微信、自定义 Webhook（Discord）、邮件发送图片格式报告
  - 邮件为内联附件，增强对不支持 HTML 客户端的兼容性
  - 需安装 `wkhtmltopdf` 和 `imgkit`

## [3.1.4] - 2026-02-12

### 新增
- 📧 **股票分组发往不同邮箱**（Issue #268）
  - 支持 `STOCK_GROUP_N` + `EMAIL_GROUP_N` 配置，不同股票组报告发送到对应邮箱
  - 大盘复盘发往所有配置的邮箱

## [3.1.3] - 2026-02-12

### 修复
- 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题

## [3.1.2] - 2026-02-11

### 修复
- 🐛 修复 Docker 一致性问题，解决关键批次处理与通知 Bug

## [3.1.1] - 2026-02-11

### 变更
- ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一

## [3.1.0] - 2026-02-11

### 新增
- 📊 **ETF 支持增强与代码规范化**
  - 统一各数据源 ETF 代码处理逻辑
  - 新增 `canonical_stock_code()` 统一代码格式，确保数据源路由正确

## [3.0.5] - 2026-02-08

### 修复
- 🐛 修复信号 emoji 与建议不一致的问题（复合建议如"卖出/观望"未正确映射）
- 🐛 修复 `*ST` 股票名在微信/Dashboard 中 markdown 转义问题
- 🐛 修复 `idx.amount` 为 None 时大盘复盘 TypeError
- 🐛 修复分析 API 返回 `report=None` 及 ReportStrategy 类型不一致问题
- 🐛 修复 Tushare 返回类型错误（dict → UnifiedRealtimeQuote）及 API 端点指向

### 新增
- 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）
- 🔍 搜索结果 TTL 缓存（500 条上限，FIFO 淘汰）
- 🔧 Tushare Token 存在时自动注入实时行情优先级
- 📰 新闻摘要截断长度 50→200 字

### 优化
- ⚡ 补充行情字段请求限制为最多 1 次，减少无效请求

## [3.0.4] - 2026-02-07

### 新增
- 📈 **回测引擎** (PR #269)
  - 新增基于历史分析记录的回测系统，支持收益率、胜率、最大回撤等指标评估
  - WebUI 集成回测结果展示

## [3.0.3] - 2026-02-07

### 修复
- 🐛 修复狙击点位数据解析错误问题 (PR #271)

## [3.0.2] - 2026-02-06

### 新增
- ✉️ 可配置邮件发送者名称 (PR #272)
- 🌐 外国股票支持英文关键词搜索

## [3.0.1] - 2026-02-06

### 修复
- 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题
- 🔧 CI 流程简化

## [3.0.0] - 2026-02-06

### 移除
- 🗑️ **移除旧版 WebUI**
  - 删除基于 `http.server.ThreadingHTTPServer` 的旧版 WebUI（`web/` 包）
  - 旧版 WebUI 的功能已完全被 FastAPI（`api/`）+ React 前端替代
  - `--webui` / `--webui-only` 命令行参数标记为弃用，自动重定向到 `--serve` / `--serve-only`
  - `WEBUI_ENABLED` / `WEBUI_HOST` / `WEBUI_PORT` 环境变量保持兼容，自动转发到 FastAPI 服务
  - `webui.py` 保留为兼容入口，启动时直接调用 FastAPI 后端
  - Docker Compose 中移除 `webui` 服务定义，统一使用 `server` 服务

### 变更
- ♻️ **服务层重构**
  - 将 `web/services.py` 中的异步任务服务迁移至 `src/services/task_service.py`
  - Bot 分析命令（`bot/commands/analyze.py`）改为使用 `src.services.task_service`
  - Docker 环境变量 `WEBUI_HOST`/`WEBUI_PORT` 更名为 `API_HOST`/`API_PORT`（旧名仍兼容）

## [2.3.0] - 2026-02-01

### 新增
- 🇺🇸 **增强美股支持** (Issue #153)
  - 实现基于 Akshare 的美股历史数据获取 (`ak.stock_us_daily()`)
  - 实现基于 Yfinance 的美股实时行情获取（优先策略）
  - 增加对不支持数据源（Tushare/Baostock/Pytdx/Efinance）的美股代码过滤和快速降级

### 修复
- 🐛 修复 AMD 等美股代码被误识别为 A 股的问题 (Issue #153)

## [2.2.5] - 2026-02-01

### 新增
- 🤖 **AstrBot 消息推送** (PR #217)
  - 新增 AstrBot 通知渠道，支持推送到 QQ 和微信
  - 支持 HMAC SHA256 签名验证，确保通信安全
  - 通过 `ASTRBOT_URL` 和 `ASTRBOT_TOKEN` 配置

## [2.2.4] - 2026-02-01

### 新增
- ⚙️ **可配置数据源优先级** (PR #215)
  - 支持通过环境变量（如 `YFINANCE_PRIORITY=0`）动态调整数据源优先级
  - 无需修改代码即可优先使用特定数据源（如 Yahoo Finance）

## [2.2.3] - 2026-01-31

### 修复
- 📦 更新 requirements.txt，增加 `lxml_html_clean` 依赖以解决兼容性问题

## [2.2.2] - 2026-01-31

### 修复
- 🐛 修复代理配置区分大小写问题 (fixes #211)

## [2.2.1] - 2026-01-31

### 修复
- 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
  - 修复新版 yfinance 返回 MultiIndex 列名导致的数据解析错误

## [2.2.0] - 2026-01-31

### 新增
- 🔄 **多源回退策略增强**
  - 实现了更健壮的数据获取回退机制 (feat: multi-source fallback strategy)
  - 优化了数据源故障时的自动切换逻辑

### 修复
- 🐛 修复 analyzer 运行后无法通过改 .env 文件的 stock_list 内容调整跟踪的股票

## [2.1.14] - 2026-01-31

### 文档
- 📝 更新 README 和优化 auto-tag 规则

## [2.1.13] - 2026-01-31

### 修复
- 🐛 **Tushare 优先级与实时行情** (Fixed #185)
  - 修复 Tushare 数据源优先级设置问题
  - 修复 Tushare 实时行情获取功能

## [2.1.12] - 2026-01-30

### 修复
- 🌐 修复代理配置在某些情况下的区分大小写问题
- 🌐 修复本地环境禁用代理的逻辑

## [2.1.11] - 2026-01-30

### 优化
- 🚀 **飞书消息流优化** (PR #192)
  - 优化飞书 Stream 模式的消息类型处理
  - 修改 Stream 消息模式默认为关闭，防止配置错误运行时报错

## [2.1.10] - 2026-01-30

### 合并
- 📦 合并 PR #154 贡献

## [2.1.9] - 2026-01-30

### 新增
- 💬 **微信文本消息支持** (PR #137)
  - 新增微信推送的纯文本消息类型支持
  - 添加 `WECHAT_MSG_TYPE` 配置项

## [2.1.8] - 2026-01-30

### 修复
- 🐛 修正日志中 API 提供商显示错误 (PR #197)

## [2.1.7] - 2026-01-30

### 修复
- 🌐 禁用本地环境的代理设置，避免网络连接问题

## [2.1.6] - 2026-01-29

### 新增
- 📡 **Pytdx 数据源 (Priority 2)**
  - 新增通达信数据源，免费无需注册
  - 多服务器自动切换
  - 支持实时行情和历史数据
- 🏷️ **多源股票名称解析**
  - DataFetcherManager 新增 `get_stock_name()` 方法
  - 新增 `batch_get_stock_names()` 批量查询
  - 自动在多数据源间回退
  - Tushare 和 Baostock 新增股票名称/列表方法
- 🔍 **增强搜索回退**
  - 新增 `search_stock_price_fallback()` 用于数据源全部失败时
  - 新增搜索维度：市场分析、行业分析
  - 最大搜索次数从 3 增加到 5
  - 改进搜索结果格式（每维度 4 条结果）

### 改进
- 更新搜索查询模板以提高相关性
- 增强 `format_intel_report()` 输出结构

## [2.1.5] - 2026-01-29

### 新增
- 📡 新增 Pytdx 数据源和多源股票名称解析功能

## [2.1.4] - 2026-01-29

### 文档
- 📝 更新赞助商信息

## [2.1.3] - 2026-01-28

### 文档
- 📝 重构 README 布局
- 🌐 新增繁体中文翻译 (README_CHT.md)

### 修复
- 🐛 修复 WebUI 无法输入美股代码问题
  - 输入框逻辑改成所有字母都转换成大写
  - 支持 `.` 的输入（如 `BRK.B`）

## [2.1.2] - 2026-01-27

### 修复
- 🐛 修复个股分析推送失败和报告路径问题 (fixes #166)
- 🐛 修改 CR 错误，确保微信消息最大字节配置生效

## [2.1.1] - 2026-01-26

### 新增
- 🔧 添加 GitHub Actions auto-tag 工作流
- 📡 添加 yfinance 兜底数据源及数据缺失警告

### 修复
- 🐳 修复 docker-compose 路径和文档命令
- 🐳 Dockerfile 补充 copy src 文件夹 (fixes #145)

## [2.1.0] - 2026-01-25

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 📈 **MACD 和 RSI 技术指标**
  - MACD：趋势确认、金叉死叉信号（零轴上金叉⭐、金叉✅、死叉❌）
  - RSI：超买超卖判断（超卖⭐、强势✅、超买⚠️）
  - 指标信号纳入综合评分系统
- 🎮 **Discord 推送支持** (PR #124, #125, #144)
  - 支持 Discord Webhook 和 Bot API 两种方式
  - 通过 `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` + `DISCORD_MAIN_CHANNEL_ID` 配置
- 🤖 **机器人命令交互**
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
- 🌡️ **AI 温度参数可配置** (PR #142)
  - 支持自定义 AI 模型温度参数
- 🐳 **Zeabur 部署支持**
  - 添加 Zeabur 镜像部署工作流
  - 支持 commit hash 和 latest 双标签

### 重构
- 🏗️ **项目结构优化**
  - 核心代码移至 `src/` 目录，根目录更清爽
  - 文档移至 `docs/` 目录
  - Docker 配置移至 `docker/` 目录
  - 修复所有 import 路径，保持向后兼容
- 🔄 **数据源架构升级**
  - 新增数据源熔断机制，单数据源连续失败自动切换
  - 实时行情缓存优化，批量预取减少 API 调用
  - 网络代理智能分流，国内接口自动直连
- 🤖 Discord 机器人重构为平台适配器架构

### 修复
- 🌐 **网络稳定性增强**
  - 自动检测代理配置，对国内行情接口强制直连
  - 修复 EfinanceFetcher 偶发的 `ProtocolError`
  - 增加对底层网络错误的捕获和重试机制
- 📧 **邮件渲染优化**
  - 修复邮件中表格不渲染问题 (#134)
  - 优化邮件排版，更紧凑美观
- 📢 **企业微信推送修复**
  - 修复大盘复盘推送不完整问题
  - 增强消息分割逻辑，支持更多标题格式
  - 增加分批发送间隔，避免限流丢失
- 👷 **CI/CD 修复**
  - 修复 GitHub Actions 中路径引用的错误

## [2.0.0] - 2026-01-24

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 🤖 **机器人命令交互** (PR #113)
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
  - 支持选择精简报告或完整报告
- 🎮 **Discord 推送支持** (PR #124)
  - 支持 Discord Webhook 推送
  - 添加 Discord 环境变量到工作流

### 修复
- 🐳 修复 WebUI 在 Docker 中绑定 0.0.0.0 (fixed #118)
- 🔔 修复飞书长连接通知问题
- 🐛 修复 `analysis_delay` 未定义错误
- 🔧 启动时 config.py 检测通知渠道，修复已配置自定义渠道情况下仍然提示未配置问题

### 改进
- 🔧 优化 Tushare 优先级判断逻辑，提升封装性
- 🔧 修复 Tushare 优先级提升后仍排在 Efinance 之后的问题
- ⚙️ 配置 TUSHARE_TOKEN 时自动提升 Tushare 数据源优先级
- ⚙️ 实现 4 个用户反馈 issue (#112, #128, #38, #119)

## [1.6.0] - 2026-01-19

### 新增
- 🖥️ WebUI 管理界面及 API 支持（PR #72）
  - 全新 Web 架构：分层设计（Server/Router/Handler/Service）
  - 核心 API：支持 `/analysis` (触发分析), `/tasks` (查询进度), `/health` (健康检查)
  - 交互界面：支持页面直接输入代码并触发分析，实时展示进度
  - 运行模式：新增 `--webui-only` 模式，仅启动 Web 服务
  - 解决了 [#70](https://github.com/ZhuLinsen/daily_stock_analysis/issues/70) 的核心需求（提供触发分析的接口）
- ⚙️ GitHub Actions 配置灵活性增强（[#79](https://github.com/ZhuLinsen/daily_stock_analysis/issues/79)）
  - 支持从 Repository Variables 读取非敏感配置（如 STOCK_LIST, GEMINI_MODEL）
  - 保持对 Secrets 的向下兼容

### 修复
- 🐛 修复企业微信/飞书报告截断问题（[#73](https://github.com/ZhuLinsen/daily_stock_analysis/issues/73)）
  - 移除 notification.py 中不必要的长度硬截断逻辑
  - 依赖底层自动分片机制处理长消息
- 🐛 修复 GitHub Workflow 环境变量缺失（[#80](https://github.com/ZhuLinsen/daily_stock_analysis/issues/80)）
  - 修复 `CUSTOM_WEBHOOK_BEARER_TOKEN` 未正确传递到 Runner 的问题

## [1.5.0] - 2026-01-17

### 新增
- 📲 单股推送模式（[#55](https://github.com/ZhuLinsen/daily_stock_analysis/issues/55)）
  - 每分析完一只股票立即推送，不用等全部分析完
  - 命令行参数：`--single-notify`
  - 环境变量：`SINGLE_STOCK_NOTIFY=true`
- 🔐 自定义 Webhook Bearer Token 认证（[#51](https://github.com/ZhuLinsen/daily_stock_analysis/issues/51)）
  - 支持需要 Token 认证的 Webhook 端点
  - 环境变量：`CUSTOM_WEBHOOK_BEARER_TOKEN`

## [1.4.0] - 2026-01-17

### 新增
- 📱 Pushover 推送支持（PR #26）
  - 支持 iOS/Android 跨平台推送
  - 通过 `PUSHOVER_USER_KEY` 和 `PUSHOVER_API_TOKEN` 配置
- 🔍 博查搜索 API 集成（PR #27）
  - 中文搜索优化，支持 AI 摘要
  - 通过 `BOCHA_API_KEYS` 配置
- 📊 Efinance 数据源支持（PR #59）
  - 新增 efinance 作为数据源选项
- 🇭🇰 港股支持（PR #17）
  - 支持 5 位代码或 HK 前缀（如 `hk00700`、`hk1810`）

### 修复
- 🔧 飞书 Markdown 渲染优化（PR #34）
  - 使用交互卡片和格式化器修复渲染问题
- ♻️ 股票列表热重载（PR #42 修复）
  - 分析前自动重载 `STOCK_LIST` 配置
- 🐛 钉钉 Webhook 20KB 限制处理
  - 长消息自动分块发送，避免被截断
- 🔄 AkShare API 重试机制增强
  - 添加失败缓存，避免重复请求失败接口

### 改进
- 📝 README 精简优化
  - 高级配置移至 `docs/full-guide.md`


## [1.3.0] - 2026-01-12

### 新增
- 🔗 自定义 Webhook 支持
  - 支持任意 POST JSON 的 Webhook 端点
  - 自动识别钉钉、Discord、Slack、Bark 等常见服务格式
  - 支持配置多个 Webhook（逗号分隔）
  - 通过 `CUSTOM_WEBHOOK_URLS` 环境变量配置

### 修复
- 📝 企业微信长消息分批发送
  - 解决自选股过多时内容超过 4096 字符限制导致推送失败的问题
  - 智能按股票分析块分割，每批添加分页标记（如 1/3, 2/3）
  - 批次间隔 1 秒，避免触发频率限制

## [1.2.0] - 2026-01-11

### 新增
- 📢 多渠道推送支持
  - 企业微信 Webhook
  - 飞书 Webhook（新增）
  - 邮件 SMTP（新增）
  - 自动识别渠道类型，配置更简单

### 改进
- 统一使用 `NOTIFICATION_URL` 配置，兼容旧的 `WECHAT_WEBHOOK_URL`
- 邮件支持 Markdown 转 HTML 渲染

## [1.1.0] - 2026-01-11

### 新增
- 🤖 OpenAI 兼容 API 支持
  - 支持 DeepSeek、通义千问、Moonshot、智谱 GLM 等
  - Gemini 和 OpenAI 格式二选一
  - 自动降级重试机制

## [1.0.0] - 2026-01-10

### 新增
- 🎯 AI 决策仪表盘分析
  - 一句话核心结论
  - 精确买入/止损/目标点位
  - 检查清单（✅⚠️❌）
  - 分持仓建议（空仓者 vs 持仓者）
- 📊 大盘复盘功能
  - 主要指数行情
  - 涨跌统计
  - 板块涨跌榜
  - AI 生成复盘报告
- 🔍 多数据源支持
  - AkShare（主数据源，免费）
  - Tushare Pro
  - Baostock
  - YFinance
- 📰 新闻搜索服务
  - Tavily API
  - SerpAPI
- 💬 企业微信机器人推送
- ⏰ 定时任务调度
- 🐳 Docker 部署支持
- 🚀 GitHub Actions 零成本部署

### 技术特性
- Gemini AI 模型（gemini-3-flash-preview）
- 429 限流自动重试 + 模型切换
- 请求间延时防封禁
- 多 API Key 负载均衡
- SQLite 本地数据存储


---

## GOT 架构实施记录（从 CLAUDE.md 归档）

> 以下内容原为 CLAUDE.md 中的实施计划详细记录，于 2026-09-02 归档至此。包含各 Phase 实现记录、Node 重设计、修复日志和文件改动清单。
> ℹ️ 2026-09-17 文档重排说明：本节为**历史实施流水**，位于 1.0.0 之后的旧版本段之后，不参与 Keep-a-Changelog 版本语义；GOT 现行有效文档见 [`docs/got/`](got/README.md)，更早的原始文档归档于 [`docs/got/archive/`](got/archive/README.md)。

**Phase 1b 修复记录：**
- ✅ A 股指数数据获取 KeyError 修复（`data_provider/base.py` + `akshare_fetcher.py`）
- ✅ 多时间框架信号 5 日评分为 0 修复（`src/got/nodes/market_analysis.py`）
- ✅ 设计文档更新至 v0.4（`agent_cot_design.md`）

**Phase 1c-1 Node 2 实现记录：**
- ✅ 5 维强度评分（量价/动量/宽度/相对强度/资金流，加权 0.30/0.25/0.20/0.15/0.10）
- ✅ 启动信号检测（3 硬门槛 + 5 维加权打分，strong_launch ≥ 0.75 / weak_launch ≥ 0.55）
- ✅ 过热信号检测（强背离/量能背离/宽度极端/换手率极端，任一触发）
- ✅ 6 级状态判定（启动 > 过热 > 持续强势 > 持续弱势 > 埋伏 > 防守 > 中性）
- ✅ 主线分级 S/A/B（政策+规模+产业链+龙头+连续性评分）
- ✅ 生命周期分类（tier 依赖阈值 → launch/acceleration/mature/late_stage/mature_exhausted）
- ✅ 精选概念列表 ~25 概念（AI 产业链 + 科技防守，关键词匹配东财板块）
- ✅ LLM reasoning 生成（纯文本总结，不影响信号逻辑）
- ✅ DataQuality 字段级质量追踪 + 降级记录

**Phase 1c-1 关键技术决策：**
- **数据源**：akshare `stock_board_concept_name_em()` 为主（单次拉全量 ~500 板块，避免 push2 分页限流），push2 直连降级
- **反爬**：集成 `src/patches/eastmoney_patch.py`（随机 UA + NID 令牌 + 请求休眠），启动时调用 `eastmoney_patch()`
- **关键词匹配**：精确 > 前缀 > 长关键词子串（覆盖 ≥60% 板名），防止短关键词误匹配（如"电机"→"发电机概念"）
- **端到端验证**：495 板块获取 → 8 概念匹配 → K 线 → 5 维评分 → LLM reasoning，全链路正常

**Phase 1c-1 已知限制：**
- push2 分页限流：2025-04 起东财加强反爬（TLS 指纹+频率），第 3 页后易失败，已改为 akshare 单页全量规避
- 非交易时段数据：周末/节假日 `涨跌幅/上涨家数` 全 0，板块分析意义有限（建议交易时段运行）
- LLM 配置依赖：需在 `.env` 中配置 `LITELLM_MODEL` 及 API Key，否则 reasoning 降级为简单文本

**Phase 1c-1 文件改动清单：**
- `src/got/nodes/sector_analysis.py`（新建）：Node 2 完整实现
- `src/services/sector_rotation.py`（修改）：`fetch_board_list` 改为 akshare 优先 + 字段提取补全
- `scripts/run_node2.py`（新建）：本地运行脚本 + 反爬补丁预加载
- `scripts/diagnose_network.py`（新建）：网络连通性诊断工具

> ⚠️ **后续演进**：Phase 1c-1 的 5 维评分体系已被 Node 2 v2（08-04）重写为 4 维截面百分位排名；主线框系统已于 2026-08-31 废除，职责收编到状态机 `exit_pressure_days`

**Phase 1c-2 Node 3 实现记录：**
- ✅ 5 维度评分体系（北向资金/龙虎榜/ETF申赎/融资融券/大宗交易，权重 0.25/0.25/0.25/0.15/0.10）
- ✅ 降级机制（P2-4）：缺失维度权重归一化 + 置信度按可用比例下调
- ✅ 龙虎榜机构评分（`stock_lhb_jgmmtj_em` 区间机构净买入 / 5e8 归一化）
- ✅ 融资融券评分（`macro_china_market_margin_sh` 融资余额日变化 / 2e9）
- ✅ 大宗交易评分（`stock_dzjy_mrtj` 成交额加权折溢率 → 分段评分：甩货/抢筹/平价）
- ✅ 北向资金自动降级（2024-08 后 `当日成交净买额` 全 NaN，自动标记缺失）
- ✅ ETF 申赎自动降级（`fund_etf_scale_sse/szse` 仅快照无历史，无法计算日度变化）
- ✅ LLM reasoning 生成（DeepSeek 驱动，含主线板块机构态度分析）
- ✅ DataQuality 字段级追踪 + 降级状态（normal/degraded/failed）

**Phase 1c-2 关键技术决策：**
- **数据源**：4 个 akshare API 并发获取（北向资金沪/深股通 × 2 + 龙虎榜 + 融资融券 + 大宗交易）
- **大宗交易阈值**：折价 > 5% → 甩货信号（score -1~-0.3），溢价 > 2% → 抢筹信号（score 0.3~1.0），中间 → 平价成交
- **北向资金停披处理**：2024-06 起沪深港通日度数据不再公开，akshare `stock_hsgt_hist_em` 的 `当日成交净买额` 列自 2024-08 后全 NaN。Node 3 自动检测并降级，不阻塞其他维度
- **ETF 申赎限制**：`fund_etf_scale_sse/szse` 仅返回当前快照（单一日期），无法计算日度份额变化。待后续接入历史数据源（如数据库逐日存储）后启用
- **实际可用维度**：3/5（龙虎榜 + 融资融券 + 大宗交易），置信度 0.6

**Phase 1c-2 已知限制：**
- 北向资金维度失效：2024-08 后数据不可用 → **已标记为永久豁免维度(2026-08-25)**，不计入 confidence 分母
- ETF 申赎维度失效：无历史份额序列，需逐日积累数据或使用第三方数据源
- 融资融券仅沪市：`macro_china_market_margin_sh` 只覆盖上交所，深市数据需额外接入 `macro_china_market_margin_sz`
- 龙虎榜去重：同一股票可能因多日上榜出现重复，当前仅取 top 5 不严格去重
- 非交易时段：大宗交易/龙虎榜在周末无新数据，返回空集时自动降级

**Phase 1c-2 文件改动清单：**
- `src/got/nodes/capital_analysis.py`（重写）：Node 3 真实数据实现，含 5 维度评分 + 降级 + LLM reasoning
- `scripts/run_node3.py`（新建）：本地运行脚本（使用 Node 1/2 mock 上下文 + Node 3 真实数据）

**Phase 1d-1 Node 4 实现记录：**
- ✅ 新闻采集层：`SearchService.search_market_news()` 新增通用市场级搜索方法（跳过个股 relevance 评分）
- ✅ 3 类 query 并发采集（policy/industry/global，6 个搜索词）
- ✅ 去重（title 相似度 ≥0.45 合并）+ 多源确认（`source_count` 统计独立来源域名数）
- ✅ 3 次 LLM 串行调用：事件提取（temp=0.2）→ 概念发现（temp=0.5）→ 风险总结（temp=0.3）
- ✅ 空响应重试（max_retries=3）+ 规则兜底（风险从负面事件提取）
- ✅ 阈值过滤 + 显著度加权评分：low 不参与，medium 权重 1.0，high 权重 2.0，source_count≥3 额外 1.5x
- ✅ `NewsContext` Schema 新增 `status` 字段（对齐 CapitalContext 模式）
- ✅ DataQuality 字段级追踪（available/missing/failed）
- ✅ 端到端验证：Bocha 搜索 → 5 事件 + 1 概念 + 1 风险 + reasoning，评分 0.75 positive

**Phase 1d-1 关键技术决策：**
- **数据源**：完全依赖 `SearchService`（Web 搜索），项目无 akshare 新闻 API、无财联社/雪球电报接入
- **搜索方法**：新增 `search_market_news()` 而非复用 `search_stock_news()`，因后者有个股 relevance 评分管道不适合市场级 query
- **串行 LLM**：原设计 3 并发，实测 DeepSeek API 并发时空响应率高，改串行执行保障稳定性
- **风险兜底**：DeepSeek 对风险总结 prompt 间歇返回空，增加规则降级——从 `sentiment="negative"` 事件中自动提取

**Phase 1d-1 已知限制：**
- 搜索依赖 API Key 配置（Bocha/Tavily/Brave 等），无 Key 则全降级
- Bocha 免费版有请求频率限制（6 并发 query 中约 1 个可能被限频）
- LLM 模型需保持可用状态（`deepseek-chat` 即将下线需切换至 `deepseek-v4-flash`）
- 概念发现偶尔 JSON 解析失败（中文编码 + max_tokens 边界），已通过增大 token 限制缓解

**Phase 1d-1 文件改动清单：**
- `src/search_service.py`（新增方法）：`SearchService.search_market_news()` 通用市场级搜索
- `src/got/schemas.py`（小改）：`NewsContext` 新增 `status` 字段
- `src/got/nodes/news_analysis.py`（重写）：Node 4 真实数据实现（~500 行）
- `scripts/run_node4.py`（新建）：本地运行脚本

**Node 4 消息面修正 v1→v2（2026-07-27~28）：**

**v1 问题（已废弃）：**
- CLS 旧端点 `nodeapi/telegraphList` 返回 404，v1 签名逆向后只覆盖 38 分钟滚动窗口——对每日分析无意义
- 东财 `stock_news_em("000001")` 搜索关键词而非热榜，漏掉长鑫等热门股
- 搜索型数据源（SearchService）query 不覆盖 IPO/宏观场景，核心事件遗漏

**v2 方案（参考 pre-market-brief 项目）：**
- ✅ **S1 新浪文章流**（`feed.sina.com.cn/api/roll/get`）：4 栏目 × 30-50 条，**覆盖 21+ 小时**，长鑫 10 条命中
- ✅ **S2 CLS 签名版**（`v1/roll/get_roll_list`）：`sign = MD5(SHA1(sorted_query))`，零 key，A 股核心快讯
- ✅ **S3 华尔街见闻**（`api-one.wallstcn.com/apiv1/content/lives`）：全球宏观，免签，含 `is_important` 标记
- ✅ **S4 东财快讯**（`np-listapi.eastmoney.com`）：国内媒体聚合，免签
- ✅ **S5 NewsNow 热榜**（`newsnow.busiyi.world/api/s`）：8 平台（微博/百度/知乎/澎湃/头条/财联社/抖音/B站），~200 条，覆盖国内时政/气候/社会/舆情
- ✅ 权重排序系统：来源权重(30%) + 时效性(25%) + 热度关键词(20%) + 跨源确认(25%)
- ✅ 砍掉 SearchService（搜索型），全部改为推送型
- ✅ 实测 5 源全活，长鑫在 7 个平台命中并作为独立事件提取（瑞穗证券目标价 70 元）
- ⚠️ 中热度消息（厄尔尼诺）在滚动窗口内短暂出现后滚出，5 源均无法稳定捕获，需持久化层解决

**v2 已知限制：**
- **中热度消息丢失**：5 源全是滚动窗口（新浪 21h / CLS 1h / 华尔街见闻 1h / 东财 2h / NewsNow 实时），中热度消息（如厄尔尼诺形成、政策预研）在窗口内只出现几小时，一旦滚出永久丢失。**需持久化层解决（见下方待办）**
- CLS 窗口仅 ~1 小时，分页再深也是同一小时数据重复
- 权重系统让高频低价值快讯（CLS 税务/统计）占前排，中热度消息被截断
- LLM 提取事件时会把相关新闻归入当日主线叙事（如长鑫被归入"韩日暴跌→存储芯片重挫"），不一定作为独立事件输出
- 概念发现（concept_suggestions）LLM 间歇返回空
- NewsNow 的 cls-hot 与 S2 CLS 有内容重叠，但去重会处理（不同数据通道，cls-hot 可捕获 CLS 签名版窗口外的热榜条目）

**待办：事件生命周期追踪（持久化层）**
- [x] 每次 `_fetch_news()` 后将全量原始数据写入 SQLite（`node4_raw_news` 表），按 title + source + date 去重 ← **已完成(2026-08-25, v3.3.1 P1-1)**
- [x] 下次运行时：当前窗口数据 + 历史库 = 完整视图 ← **已完成**
- [x] 中热度事件检测：出现在历史库但不在当前窗口 → 标记"已消退"；连续多天出现 → 标记"正在发酵" ← **已完成（跨日发酵检测）**
- [x] 可复用 `src/got/persistence.py`（GOT 上下文持久化）或 `src/storage.py`（SQLite 25+ 表）← **已使用 storage.py**

**关键发现：**
- 新浪有两个完全不同的 API：`zhibo.sina.com.cn`（直播流，38 分钟）vs `feed.sina.com.cn`（文章归档流，21+ 小时）
- CLS 签名算法 `MD5(SHA1(sorted_query))` 来自 simonlin1212/a-stock-data 项目（7.7k stars）
- NewsNow（`newsnow.busiyi.world/api/s`）是免费公开 API，聚合微博/百度/知乎/澎湃/头条/财联社/华尔街见闻/抖音/B站 9 平台热榜，单接口覆盖国内全域
- pre-market-brief 项目的 9 维 LLM 评估（催化指数/发酵潜力/博弈建议）是后续优化方向
- TrendRadar（60k+ stars）的热榜采集思路（NewsNow API）已集成到 S5

**v2 文件改动清单：**
- `src/got/news_feeds.py`（重写 v2）：5 源推送型采集（新浪/CLS/华尔街见闻/东财/NewsNow）+ 权重排序 + 跨源标记
- `src/got/nodes/news_analysis.py`（修改）：`_fetch_news()` 调用 `fetch_all_news()`，删除搜索型代码
- `src/got/schemas.py`（小改）：`NewsEvent` 新增 `source_type` 字段

**Phase 1d-2 Node 5 实现记录：**
- ✅ 数据源切换：NGA 论坛已关闭，改用 akshare 雪球热榜（关注/交易/讨论 3 榜并发）
- ✅ 市场宽度：akshare `stock_zh_a_spot_em` 获取涨跌家数/涨停跌停（代理受限时自动降级）
- ✅ 市场宽度数据源修复（2026-07-30）：东财 push2 `stock_zh_a_spot_em()` 被反爬封锁 → 改用新浪 `stock_zh_a_spot()` 优先，push2 降级为备用。修复后 breadth_data 从 missing 恢复为 available，unified_signal 从 neutral 修正为 bearish
- ✅ 搜索舆情：SearchService 搜索散户情绪关键词 → 正负面词频统计
- ✅ 情绪评分：3 维度加权（市场宽度 0.50 + 雪球热度 0.25 + 搜索舆情 0.25），缺失维度权重归一化
- ✅ 反身性 5 阶段分类（P2-2）：euphoria/optimism/neutral/pessimism/capitulation
- ✅ 极端情绪反向信号：euphoria → contrarian_sell，capitulation → contrarian_buy
- ✅ 市场温度（0=冰点, 1=沸点）+ 散户关注度 + 消息面偏离
- ✅ LLM reasoning + hot_topics 生成（JSON 输出，失败时规则降级）
- ✅ SentimentContext Schema 补全：retail_attention_count / market_temperature / news_drift
- ✅ 端到端验证：雪球 30 只热门股 + 搜索舆情 → 评分 -0.31（悲观）→ pessimism 阶段

**Phase 1d-2 关键技术决策：**
- **数据源**：NGA 已关闭（板块不存在），改用 akshare 雪球热榜。3 个 API（`stock_hot_follow_xq`/`stock_hot_deal_xq`/`stock_hot_tweet_xq`）各返回 5610 只股票，取 Top 30
- **不单独建 Service**：数据获取逻辑直接嵌入 Node 5（与 Node 1/2/3/4 保持一致），避免过度抽象
- **串行 LLM**：同 Node 4 教训，DeepSeek 并发空响应率高，改为串行调用
- **降级策略**：市场宽度失败时（东财 push2 代理限流），自动归一化到雪球+搜索两维度，不阻塞节点

**Phase 1d-2 已知限制：**
- 雪球 API 较慢（每个 ~20 秒，3 并发总计 ~20 秒），超时设 90 秒
- 市场宽度依赖东财 push2 接口，代理环境下可能失败（自动降级）
- 情绪评分依赖关键词匹配（搜索舆情维度），精度有限
- 反身性阈值（0.5/0.2）为初始值，运行 3 个月后需回测调整

**Phase 1d-2 文件改动清单：**
- `src/got/schemas.py`（小改）：`SentimentContext` 新增 3 字段（retail_attention_count/market_temperature/news_drift）
- `src/got/nodes/sentiment_analysis.py`（重写）：Node 5 真实数据实现（~480 行）
- `scripts/run_node5.py`（新建）：本地运行脚本

**Node 5 v2 重设计（2026-08-04）：**

> 基于 8 天运行数据（07-24~08-04）暴露的 6 大致命缺陷 + 3 篇学术论文（国金证券舆情选股/清华投资者情绪/fffinstill Fear & Greed Index）

**问题诊断**：
- ❌ hot_stocks sentiment 全部硬编码 "positive"（50 只股票无真实情绪判断）
- ❌ 雪球数据陈旧（07-24~07-31 热门股列表完全相同，API 返回缓存数据）
- ❌ 搜索舆情仅 28 个关键词匹配（无否定检测、无强度区分）
- ❌ NGA 间歇性缺失导致权重偏移（07-24~07-31 正常，08-03~04 缺失）
- ❌ 情绪评分与市场严重脱节（08-04 涨停 243 日 score 仅 0.085）
- ❌ 缺少关键维度（无恐贪指数、无时序对比、不利用 Node 4 新闻）

**核心变更**：
- ✅ 新增东财千股千评数据源（`stock_comment_em()` → `datacenter-web.eastmoney.com`，非 push2，5190 股 ~3s）
- ✅ 新增评论详情采样（`stock_comment_detail_*` 系列，15 只热门股并发获取参与意愿/关注度/机构参与度历史）
- ✅ 6 维置信度加权评分（东财评论 25% + 市场宽度 15% + NGA 20% + 搜索舆情 15% + 散户行为 10% + 时序动量 15%）
- ✅ 置信度加权聚合（参考 fffinstill F&G Index）：缺失维度权重自动重分配
- ✅ 情绪分歧度（参考国金证券研究）：`_compute_sentiment_dispersion()` 计算综合得分变异系数，低分歧+极端均值 → 反向信号
- ✅ 时序动量：`_compute_sentiment_momentum()` 加载近期 sentiment.json 计算 EMA(5) 偏差
- ✅ 恐贪指数：`fear_greed_index = (score + 1) / 2`，映射到 [0, 1]
- ✅ HotStocks 改造：使用东财综合得分判断真实情绪（≥70 positive, ≤30 negative），不再硬编码
- ✅ 关键词匹配改进：词库从 28 扩展到 44，新增否定词翻转检测（"不看好"→负面而非正面）
- ✅ 反身性分类改用东财关注指数（替代雪球的缓存数据）
- ✅ LLM reasoning prompt 增加分歧度/反向信号信息
- ✅ SentimentContext 新增 4 字段：fear_greed_index / sentiment_dispersion / contrarian_signal / sentiment_momentum
- ✅ Archive 覆盖修复：`archive_daily_run()` 开头清理旧档，确保当天重跑全量覆盖

**效果对比（08-04 涨停 243 日）**：

| 指标 | 旧版 | 新版 |
|------|------|------|
| sentiment_score | 0.085（中性） | 0.316（乐观） |
| hot_stocks sentiment | 全 "positive" | positive/neutral 真实区分 |
| fear_greed_index | 不存在 | 0.658（贪婪侧） |
| sentiment_dispersion | 不存在 | 0.092（低分歧=一致看多） |
| sentiment_momentum | 不存在 | +0.219（情绪升温） |

**数据源端点确认**：
- 东财千股千评 → `datacenter-web.eastmoney.com` ✅ 不走 push2
- 东财评论详情 → `datacenter-web.eastmoney.com` ✅ 不走 push2
- 数据时效性：实测返回当日数据

**Phase 2 性能优化（2026-08-04）**：
- ✅ `_get_sector_constituents_filtered()` 新增携带 `turnover_rate`/`volume` 字段（问财已返回，无需重复获取）
- ✅ `_score_technical_wave()` 换手率直接用候选数据，消除逐只实时行情获取（~5s/只）
- ✅ `_score_risk()` 移除 `get_realtime_quote` 依赖，全部使用 K 线数据
- ✅ 预估提速：Phase 2 从 ~11 分钟降到 ~3 分钟（43 次行情获取 × 5s → 0s）

**Node 5 v2 文件改动清单：**
- `src/got/nodes/sentiment_analysis.py`（核心重写 ~650 行）：6 源采集 + 置信度加权评分 + 分歧度 + 动量
- `src/got/schemas.py`（+4 字段）：fear_greed_index / sentiment_dispersion / contrarian_signal / sentiment_momentum
- `src/services/stock_screener.py`（性能优化）：携带问财字段 + 消除逐只行情获取
- `src/got/archive.py`（覆盖修复）：当天重跑清理旧档
- `scripts/run_node5.py`（更新）：打印新字段

**Phase 1e Node 6 聚合实现记录：**
- ✅ 12 步完整流水线（1018 行）：路径级校验 → 加载前日 JSON → 10 对矛盾规则检测 → LLM 批处理 → 消息面覆盖三层防护 → 时间一致性校验 4 类异常 → critical 强制人工审核 → 最终研判 LLM + 规则降级 → 仓位日限幅 ±0.2 → key_risks/opportunities 汇总 → UnifiedContext 构建 → 持久化
- ✅ 10 对矛盾检测（含 ④b/④c 子矛盾）
- ✅ 消息面覆盖三层防护（系统性关键词 → 历史验证 → LLM）
- ✅ 时间一致性校验 4 类异常（sudden_trend_flip / confidence_collapse / rapid_mainline_shift / position_limit_swing）
- ✅ 降级感知：`capital.status == "failed"` 的维度不参与矛盾检测
- ✅ LLM 调用：`LLMToolAdapter` 同步 + `asyncio.to_thread` 包装，3 次重试 + 空响应检测
- ✅ JSON 解析兼容 markdown code block

**Phase 1e 文件改动清单：**
- `src/got/nodes/aggregator.py`（新建）：Node 6 完整实现（~1018 行）

**Node 6 v2 重设计（2026-07-30）：**

> 基于 15+ 篇论文（GOT/AAAI 2024、Debate-or-Vote/NeurIPS 2025、ConsensAgent/ACL 2025、AoR/LREC 2024 等）

**核心变更**：
- ✅ 从「硬编码 10 对矛盾 + 3 次 LLM」→「三阶段流水线（最多 2 次 LLM）」
- ✅ Phase A：信号标准化 + 置信度加权聚合（纯规则，零 LLM）
- ✅ Phase B：自动矛盾检测（替代硬编码）+ 矛盾分类（factual/temporal/opinion）+ EMA 时间一致性
- ✅ Phase C：条件 LLM 精炼（agreement < 0.6 或 high 异常时触发；否则规则输出）
- ✅ 收敛剖面（ConvergenceProfile）：5 种模式 + Surprising Popularity 异见调查标记
- ✅ 新增 Schema 类型：DimensionSignal、ConvergenceProfile、DimensionContribution、AggregationResult
- ✅ UnifiedContext 新增 5 字段：convergence_profile、weighted_score、dimension_contributions、llm_refinement_used、ema_baseline
- ✅ EMA 基线持久化：`data/aggregator_contexts/ema_baseline.json`（α=0.3 + 滚动标准差）
- ✅ 砍掉死代码：`_classify_event_accuracy()`（静态历史准确率表）、`confidence_adjustment` 从未被使用
- ✅ 砍掉硬崩溃：旧版 Step 1 `validate_path_inputs` 任一路径失败直接报错 → 新版 failed 维度自动跳过

**关键设计决策**：
- **投票优于辩论**（Debate-or-Vote, NeurIPS 2025）：置信度加权投票覆盖多轮辩论大部分收益
- **概率聚合在 LLM 外部**（ThinkSum, ACL 2023）：规则推断比 LLM 推断更稳定
- **反 Sycophancy**（ConsensAgent, ACL 2025）：C2 阶段明确「批判者角色」
- **Surprising Popularity**（VOTING/ISP, 2024）：lone_outlier.confidence > avg × 1.5 → 标记调查

**Node 6 v2 文件改动清单：**
- `src/got/schemas.py`（+50 行）：新增 4 个类型 + UnifiedContext 5 字段
- `src/got/persistence.py`（+50 行）：EMA 基线 load/save
- `src/got/nodes/aggregator.py`（重写 ~530 行）：三阶段流水线替代 12 步
- `src/got/__init__.py`（小改）：更新文档注释
- `agent_cot_design.md`（更新）：Node 6 设计文档同步

**Node 6 埋伏通道 P3（2026-08-19）：**

> 基于 plan.md 的 GOT 埋伏分支拆分设计。P1（催化剂日历）+ P2（Node 2 闸门重构）已完成。

**核心变更**：
- ✅ Level 1 平行分支：`_build_ambush_channel()` 与主线 mini-GOT 独立
- ✅ 3 层流水线：闸门层（★★★利空否决 + 吸筹确认 + 防买顶双闸 + 拥挤黑名单）→ 排序层（B 公式）
- ✅ B 公式催化剂排序：主催化分 = 影响权重 × 临近系数 × 置信度 + 冗余加成（封顶 +40%）
- ✅ 拆除 ±5 主线尺子偏见：埋伏板块 `sentiment_check=="low"` 不再扣 5 分
- ✅ 蓄水池机制：闸门通过但无合格催化剂 → `AmbushReservoirEntry`（4 周自动除名）
- ✅ 矛盾否决日志：`ambush_veto_log` 记录所有否决理由
- ✅ Schema 扩展：`AmbushCandidate` + `AmbushReservoirEntry` + `SectorIntel` 4 个埋伏字段 + `UnifiedContext` 3 个输出字段
- ✅ Archive 集成：`reasoning.md` 新增埋伏通道摘要（正式池 + 蓄水池 + 否决记录）

**验证结果（20260814 归档数据，10 个埋伏板块）**：
- 9 个入蓄水池（无催化剂事件 → no_catalyst）
- 1 个被吸筹闸拦截（OLED概念，当日净流出 -1.71 亿）
- ±5 拆除验证通过：主线 composite=55 vs 埋伏 composite=60，差值 +5.0

**P3 文件改动清单**：
- `src/got/schemas.py`（+55 行）：`AmbushCandidate` + `AmbushReservoirEntry` + `SectorIntel` 4 字段 + `UnifiedContext` 3 字段
- `src/got/nodes/aggregator.py`（+230 行）：7 个新方法 + ±5 拆除 + run() 集成
- `src/got/archive.py`（+25 行）：reasoning.md 埋伏通道摘要
- `agent_cot_design.md`（+30 行）：§2.6.2b 埋伏通道设计文档

**Phase 2 埋伏仓位与退出 P4（2026-08-19）：**

> 基于 P3 正式池输出，消费 `category_tag`（drift/fade/binary）实现类型化仓位和催化剂驱动退出分支。

**核心变更**：
- ✅ 类型化仓位系数：`_assess_risk()` 中 `sector_limits` 公式追加 `catalyst_coeff`（drift×1.0 / fade×0.7 / binary×0.4）
- ✅ 埋伏退出分支：`_ambush_exit_signal()` 实现 6 种退出触发（fade_sell / drift_exhausted / drift_break_ma20 / drift_timeout / event_cancelled / stop_loss）
- ✅ 买入催化剂快照：`_write_ambush_buy_snapshots()` 在 Phase 2 建仓时写入 `ambush_position_snapshots` 表
- ✅ 退出关闭快照：`_close_ambush_snapshots()` 触发退出时更新 is_open=False + exit 字段
- ✅ `_score_holdings()` 集成：加载快照 → 退出检测 → 覆盖 action → 填充 `ambush_exit_signal` 字段
- ✅ `PortfolioDecision.ambush_exit_signals` 新增字段，归档集成到 `trading_recommendations.md` + `reasoning.md`
- ✅ Schema 扩展：`AmbushPositionSnapshot`（Pydantic）+ `UnifiedScoringResult` 3 埋伏字段 + `TradeAllocation` 4 催化剂字段 + `PortfolioDecision.ambush_exit_signals`
- ✅ Storage 新增：`AmbushPositionSnapshotRecord` 表（买入快照 + 退出追踪 + 复盘记录）

**退出触发清单**：
| 类型 | 触发条件 | exit_signal |
|------|---------|-------------|
| fade | 事件日次日 | fade_sell |
| drift | 事件后 ≥10 日 + 涨幅 <2% | drift_exhausted |
| drift | 破 MA20 | drift_break_ma20 |
| drift | 持有 >20 交易日 | drift_timeout |
| binary | 事件日 >2 日未发生 | event_cancelled |
| 通用 | P&L < -7% | stop_loss |

**验证结果（单元测试）**：
- ✅ `_ambush_exit_signal` 8 个场景全部通过（fade 退出/等待、binary 落空/等待、drift 衰竭/超时/持有、硬止损）
- ✅ `_assess_risk` 类型化系数：drift/binary 仓位比 = 2.85（预期 ~2.5）
- ✅ `AmbushPositionSnapshotRecord` 表读写 + 关闭流程正常

**P4 文件改动清单**：
- `src/got/schemas.py`（+65 行）：`AmbushPositionSnapshot` + `UnifiedScoringResult` 3 字段 + `TradeAllocation` 4 字段 + `PortfolioDecision.ambush_exit_signals`
- `src/storage.py`（+40 行）：`AmbushPositionSnapshotRecord` 表
- `src/got/nodes/portfolio_decision.py`（+200 行）：`_ambush_exit_signal()` + `_load_ambush_snapshots()` + `_write_ambush_buy_snapshots()` + `_close_ambush_snapshots()` + `_assess_risk` 类型化系数 + `_score_holdings()` 集成
- `src/got/archive.py`（+30 行）：`trading_recommendations.md` + `reasoning.md` 埋伏退出摘要
- `agent_cot_design.md`（+45 行）：§2.6.2c Phase 2 埋伏仓位与退出设计文档
- `scripts/test_p4_ambush.py`（新建 ~300 行）：P4 单元测试脚本

**Phase 2 催化剂关联度重排 P5（2026-08-19）：**

> 消费催化剂日历，在选股漏斗轮次 4（风险排雷）之后、轮次 5（板块分散）之前，按催化剂关联度对 ambush 候选重排。

**核心变更**：
- ✅ 新增 `_score_catalyst_relevance()` 方法：从 `news_context.catalyst_calendar` 加载事件，为每只候选股计算关联度分数
- ✅ 个股级事件（earnings）→ title 含股票代码 → 当事股直接置顶（score=100，direction=="negative" 跳过）
- ✅ 板块级事件（narrative/global/macro）→ sector 匹配 → score = confidence × impact × proximity × direction_coeff × 50
- ✅ 置信度 < 0.7 事件不参与评分（保持技术排序）
- ✅ 仅对 ambush 板块候选生效，主线保持原序
- ✅ ScreenerStock 新增 5 个催化剂字段（relevance_score/event_title/event_date/event_type/confidence）
- ✅ `_generate_got_reasoning()` 新增催化剂关联度展示（事件标题/类型/日期/置信度）

**验证结果**：
- ✅ 个股级事件置顶：600519 score=100（earnings 匹配）
- ✅ 板块级事件评分：000858 score=85.5（narrative，confidence=0.75）
- ✅ 低置信过滤：601318 score=0（confidence=0.5 < 0.7）

**P5 文件改动清单**：
- `src/services/stock_screener.py`（+120 行）：ScreenerStock 5 字段 + `_score_catalyst_relevance()` + 漏斗集成 + reasoning 展示

**催化归档表 P6（2026-08-19）：**

> 每个催化剂事件兑现后记录预期 vs 实际，用途：参数校准源、反向查询存废、否决准确率、故事寿命本地重估。

**核心变更**：
- ✅ `CatalystRealizationRecord` 表：event_id(唯一) + event_type/source/source_type + 预期字段 + 兑现结果(is_realized/actual_return/drift_direction_correct) + 故事寿命(story_start/peak/end/lifetime_days)
- ✅ `CatalystRealizationTracker` 类：`register_event()`（日历构建时注册）+ `check_and_record_realization()`（每日运行，检测过期事件并计算板块收益）+ `get_realization_stats()`（兑现率统计）+ `get_story_lifetimes()`（故事寿命统计）
- ✅ 催化剂日历集成：`_save_events()` 末尾自动注册到归档表
- ✅ 编排器集成：`run()` 末尾调用 `check_and_record_realization()`
- ✅ 与 AmbushPositionSnapshotRecord 通过 event_id 关联

**四大统计用途**：
1. 按 event_type/source_type/category_tag 分组兑现率
2. 漂移方向预判准确率
3. 各类事件的故事寿命分布（avg/median/min/max）
4. 反向查询/否决/蓄水池通道的独立准确率

**验证结果**：
- ✅ CatalystRealizationRecord 表读写 + 兑现更新正常
- ✅ 统计方法：total=3, realized=2, rate=0.67, direction_accuracy=0.50
- ✅ 故事寿命：earnings avg=10.0天

**P6 文件改动清单**：
- `src/storage.py`（+50 行）：`CatalystRealizationRecord` 表
- `src/got/catalyst_realization.py`（新建 ~280 行）：`CatalystRealizationTracker` 类
- `src/got/catalyst_calendar.py`（+10 行）：`_save_events()` 注册到归档表
- `src/got/orchestrator.py`（+10 行）：`run()` 末尾兑现检测
- `scripts/test_p5_p6.py`（新建 ~220 行）：P5/P6 验证脚本

**催化剂日历瘦身：限售解禁移除（2026-08-25）：**

> 限售解禁是利空风险事件，不应作为催化剂正面信号参与埋伏评分。0824 数据显示 121/242 条事件（50%）为 lockup，30 只股票扇出到 52 个板块，严重稀释 narrative/macro 事件信息密度。且 P5 存在 direction bug（negative 个股事件 score=100 置顶），利空被当成催化剂推荐。

**核心变更**：
- ✅ `_full_build()` 跳过 lockup：`all_stocks = earnings_raw`（消除无效板块映射 IO）+ 删除 lockup 事件生成循环
- ✅ `_build_structured_events()` 防御性早返回：`event_type == "lockup"` → 空列表
- ✅ P5 `_score_catalyst_relevance()` 两个 `if` 改 `elif` 互斥（防重复计分）
- ✅ P5 个股级事件增加 `direction == "negative"` 跳过（利空不置顶）
- ✅ `_fetch_lockup_calendar()` 保留（未来风险通道复用），但不再进入日历
- ✅ 行为变化：个股事件（如财报）不再外溢到同板块其他候选股的关联度分（收紧合理）
- 预期效果：日历事件从 242 降至 121，信息密度提升一倍
- 现有 lockup 记录由 `expires_at` 自然过期（最长 14 天）

**催化剂日历 LLM 叙事回声过滤（2026-08-25）：**

> 0825 数据显示 LLM 叙事事件出现"已发生事件回声"问题：新闻"美股存储光通信板块重挫"（8-25 已发生）被 LLM 映射到 8-29（周六），出现在"未来事件"列表。本质是时态问题（事后描述冒充前瞻性事件），非极性问题。

**核心变更**：
- ✅ A 增强版 prompt：注入今日日期 + 时态判断表格（区分"已发生事件"vs"已发生事件的后续影响"）+ 正反 few-shot 示例
- ✅ D1 回声检测：`_is_echo_event()` — title 与 source_news 互相包含或字符级 Jaccard ≥ 0.6 + direction="negative" → 丢弃
- ✅ D2 周末守卫：`event_date.weekday() >= 5` 直接丢弃（A 股周末不开盘）
- ✅ Schema 扩展：`CatalystEvent` 新增 `source_news` 字段（保留 LLM 输出的来源新闻标题）
- ✅ Storage 扩展：`CatalystEventRecord` 新增 `source_news` 列（DB 迁移需手动 ALTER TABLE）

**验证结果**：
- 单元测试 3/3 通过（周末守卫 + 回声检测 + 相似度阈值）
- 全量重建后 8-29 存储芯片事件不再出现，无周末事件

**文件改动清单**：
- `src/got/schemas.py`：`CatalystEvent` 加 `source_news` 字段
- `src/storage.py`：`CatalystEventRecord` 加 `source_news` 列
- `src/got/catalyst_calendar.py`：Prompt 增强 + `_parse_llm_narrative` 加 D1/D2 过滤 + `_save_events`/`_load_active_events` 同步 source_news
- `scripts/test_catalyst_fix.py`（新建）：3 项单元测试 + 全量重建验证

**Node 1 重设计（2026-07-30）：**

> 基于 Node 6 v2 重跑 Phase 1 暴露的问题

**核心变更**：
- ✅ 砍掉 market_breadth 维度：东财 `stock_zh_a_spot_em` 被反爬封锁 → 4 维权重归一化
- ✅ 新增跨指数一致性（`_compute_cross_index_consistency`）：5 指数方向分类替代 breadth 的"参与度"
- ✅ confidence 三因素加权：跨指数一致性 35% + 时间框架一致性 35% + ADX 强度 30%（替代旧版 `|score|`）
- ✅ 时间框架 mini GOT 改 score 级：矛盾检测从 categorical 比较改为 `|short-long| > 0.3`，加模式分类
- ✅ T-0 实时修正：技术面方向与当日实际反向 → confidence ×0.7
- ✅ LLM reasoning 生成：1 次调用，失败降级到规则拼接
- ✅ score→trend 阈值收窄：±0.5 → ±0.3
- ✅ 数据置信度 vs 信号置信度分离（延续 Node 6 v2 思路）
- ✅ **市场宽度恢复（2026-07-30 下午）**：数据源修复（新浪优先）后，Node 1 加回 `_fetch_market_breadth()` + `_apply_breadth_adjustment()`。技术面与市场宽度方向不一致时 confidence ×0.7。`MarketContext` schema 新增 `market_breadth` 字段

**Node 1 v2 效果**：
- confidence: 0.30 → 1.00（5 指数全向一致 → cross_index_consistency=1.0）
- score: -0.371 → -0.490（砍掉 breadth=0 死重后空头信号增强）
- trend: neutral → bearish（阈值收窄）
- contradiction_type: null → broad_decline（模式分类正确）
- Node 6 sig_conf_avg: 0.50 → 0.64（insufficient_data 门控不再误触发）

**Node 1 v2 已知限制**：
- short/medium 分数完全相同（-0.540 = -0.540）：时间框架差异化不够，实际只有 2 个独立信号
- cross_index_consistency 衡量 MA20 方向一致 ≠ 当日涨跌一致：MA20 全下方但当日风格分化时误报高一致性
- confidence=1.0 可能偏高（三因素全满 + T-0 同向×1.1 触顶）

**Node 1 v2 文件改动清单：**
- `src/got/indicators.py`（修改）：`calculate_all_indicators` 移除 market_breadth，4 维权重归一化
- `src/got/nodes/market_analysis.py`（重写核心方法）：confidence/cross_index/时间框架/T-0/LLM
- `agent_cot_design.md`（更新）：§3.1 设计文档同步

**Node 2 v2 重设计（2026-08-04）：**

> 基于 7 天运行数据（07-27~08-04）暴露的三个核心问题 + 3 个参考项目（sector-rotation-v2, STMS 模型, Athene）

**问题诊断**：
- ❌ `volume_price` 对所有概念板块恒定 0.3（30% 权重报废）— mootdx 板块 K 线的 volume 是指数型加总，波动率低导致 vol_ratio 全部 < 1.2
- ❌ `capital_flow` 全部 clip 到 ±1.0（10% 权重报废）— 概念板块成交额日变化轻松超 5 亿，固定 clip 范围太小
- ❌ 40% 评分权重失效 → 两天分数四舍五入后一模一样
- ❌ 行业板块与概念板块混杂评分，成分股高度重叠（AIGC vs 人工智能 5/6 重叠）
- ❌ `_FRAME_GROUP_KEYWORDS` 硬编码合并粒度不合理（黄金+有色合并、华为系聚合错误）
- ❌ DataQuality 内部追踪但不输出到 reasoning.md，降级不可见

**核心变更**：
- ✅ 砍掉失效维度（volume_price + capital_flow），引入 turnover_expansion + breadth_health
- ✅ 所有维度改为**截面排名百分位**（参考 STMS 模型），自适应牛熊市
- ✅ 行业/概念**双视角分离**：独立评分、独立排名、不混合（参考 sector-rotation-v2）
- ✅ 板块聚合改为**主线家族制**（~10 个家族，关键词自动归类），消除硬编码重叠
- ✅ 降级信息输出到 reasoning.md 顶部（数据质量摘要 + 可信度标记）

**新版评分体系（4 维，全部有效）**：
```
price_momentum     30%  ← 5 日涨幅的截面百分位排名
turnover_expansion 25%  ← 成交额比（5d均值/20d均值）的截面百分位排名
relative_strength  25%  ← 20 日涨幅的截面百分位排名
breadth_health     20%  ← 板块内上涨个股占比的截面百分位排名
```

**主线家族（Level 1，~10 个）**：
AI硬件 / AI软件 / 机器人 / 智能驾驶 / 新能源 / 医药 / 金融 / 周期资源 / 军工 / 消费

**题材地位判定（Level 2）**：
主线核心（score>70 + resonance>0.5）/ 主线扩散 / 独立异动 / 修复观察 / 高位降温

**数据源验证**：
- mootdx `get_board_kline` 支持概念（880xxx）+ 行业（881xxx）✅
- akshare `stock_board_industry_name_em()` 提供行业板块列表 ✅
- breadth_health 依赖 mootdx K 线是否返回 up_count/down_count（待验证）
- 行业板块的 breadth_health 暂缺，用其他 3 维权重归一化降级

**Node 2 v2 文件改动清单**（已执行）：
- `src/got/indicators.py`（+10 行）：新增 `percentile_rank()` 函数
- `src/got/schemas.py`（+20 行）：新增 `ThemeFamily`，`SectorContext` 加 `industry_top`/`theme_families`/`confidence_level` 字段
- `src/got/nodes/sector_analysis.py`（核心重写）：评分体系 + 双视角 + 主线家族 + 降级可见性
- `src/got/archive.py`（+10 行）：reasoning.md 加降级摘要
- `agent_cot_design.md`（更新）：§3.2 设计文档同步

**Node 2 白名单补全（2026-08-07）：**

> 基于用户反馈：元件、电子化学品、半导体等板块未出现在主线中。经系统审查，白名单（CONCEPT_UNIVERSE + TDX_BOARD_REGISTRY）覆盖不全，大量东财活跃概念板块未被追踪。

**问题诊断**：
- ❌ 元件、电子化学品：完全不在白名单中
- ❌ 半导体：虽在注册表但被归入过大（19 个成员）的家族，共振率仅 0.11
- ❌ ~30 个当前热点板块缺失（IGBT、碳化硅、BC电池、PEEK材料等）
- ❌ ~15 个冗余/过时条目（CPO/共封装光学重复、电力物联网过时、MR/AR/VR冗余）
- ❌ ~10 个命名错位（EDA vs EDA概念、6G vs 6G概念、鸿蒙 vs 鸿蒙概念）

**核心变更**：
- ✅ 新增 ~30 个板块（半导体上游/电池技术/新材料/数字经济/新内容/消费等）
- ✅ 剔除 ~15 个冗余/过时条目
- ✅ 修正 ~10 个命名对齐（匹配东财实际板块名）
- ✅ 新增 8 个 category 子分类（electronic_components/power_semiconductor/battery_tech/new_materials/digital_economy/new_content/frontier/consumer）
- ✅ FAMILY_KEYWORDS 同步更新，新增华为生态 + 前沿科技家族
- ✅ _FRAME_GROUP + _FRAME_GROUP_KEYWORDS 同步更新
- ✅ TDX_BOARD_REGISTRY 添加 TODO 注释（~25 个板块代码待查找）

**待完成**：
- [ ] 运行 `scripts/find_missing_board_codes.py` 查找缺失板块的 TDX 代码
- [ ] 填入 TDX_BOARD_REGISTRY 中对应的 TODO 条目
- [ ] 重新运行 Node 2 验证输出

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：CONCEPT_UNIVERSE + FAMILY_KEYWORDS + _CATEGORY_GROUP + _FRAME_GROUP + _FRAME_GROUP_KEYWORDS
- `data_provider/tdx_board_mapping.py`：TDX_BOARD_REGISTRY（添加 TODO 注释）
- `scripts/find_missing_board_codes.py`（新建）：查找缺失板块 TDX 代码的工具脚本

> ⚠️ **后续演进**：白名单补全已被"板块噪声过滤升级 v2"（2026-08-25）覆盖——从白名单思路改为黑名单思路（222 条精确集合 + 语义模式兜底），覆盖 652 条 TDX 板块全量分析

**Node 2 状态机 HI3 移除（2026-08-24）：**

> HI3（大盘暴跌全局拦截）与输出层主线裁剪职责重复，且破坏状态机马尔可夫性——未破位板块（如白银 96.4 分、leading）也被强制踢成 exhausted，回主线需重走 2 天确认。暴跌日的风险控制已统一到 attack_power → 主线裁剪层。

**问题诊断**：
- ❌ HI3 在状态机最前面拦截（`market_crash=True → exhausted_mainline`），让主线裁剪层在暴跌日整体失效
- ❌ 与 8/20 的 floor=1 + 独立行情豁免修复矛盾——同一堵墙上开了个绕过的后门
- ❌ 候选池归零陷阱的第三次变体（Node 2 埋伏闸门归零 → 主线 DDE 全灭 → HI3 全杀）

**核心变更**：
- ✅ 删除 `_apply_state_machine` 的 `market_crash` 参数和分支
- ✅ 删除 `_determine_state_v3` 的 `market_crash` 变量计算
- ✅ 保留主线裁剪层（`attack_power < 0.25 → max_mainline=1`）作为暴跌日唯一防守
- ✅ 保留独立行情豁免（组分数≥90 + S/A≥3 → 豁免裁剪，最多 2 个家族）
- ✅ 暴跌日未破位强者走正常破位判据（broken = 5日涨幅≤0 / 跌破MA20 / ...），未被破位的被裁剪层限制到最多 1 条

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：`_apply_state_machine` 删 `market_crash` 参数+分支 + `_determine_state_v3` 删 `market_crash` 计算

> ⚠️ **后续演进**：主线框系统（`sector_frames` 表）已于 2026-08-31 彻底废除，职责收编到状态机 `exit_pressure_days`（RRG 四象限 + 6 态 Markov 统一管控）

**v3.3.1 硬闸门消除 + 埋伏评分质量修复（2026-08-25）：**

> 基于 plan_v3_hard_gate_elimination.md 的 5 个任务 + 08-24 回归暴露的双轨合并伪并集 + 评分区分度倒挂问题。

**v3.3.1 计划实施（5 个任务）**：
- ✅ P0-1: 埋伏 6 AND 硬闸门 → 6 维加权评分（rs_ratio/rs_momentum/turnaround/accumulation/volume_position/strength_cap）+ 排名截断（Top-K + floor）
- ✅ P0-2: 问财 DDE 双 AND → 单条件 0.8%→0.5%，排序改 DDE，Top 10→15
- ✅ P1-1: Node 4 原始新闻持久化（`node4_raw_news` 表 + title 归一化 + Jaccard 相似度去重 + 跨日发酵检测）
- ✅ P1-2: 蓄水池跨日合并（`_merge_reservoir()` + `days_in_reservoir` 递增 + ≥20 交易日自动除名 + 升格重走闸门）
- ✅ TODO 标记：催化剂置信度/蓄水池过期/升格天数/埋伏退出 4 组参数标记待校准

**08-24 回归修复（双轨合并 + 评分质量）**：

问题诊断：
- ❌ 伪并集：Track1(状态机) 15 个候选被合并后全局排序截断到 Top 8，7+ 个被 Track2 竞选淘汰
- ❌ 评分倒挂：风格板块(昨成交20/基金减仓/高市净率)霸占"越低越好"维度，冷启动 RRG 默认值(rs_ratio=0.00/rs_momentum=0.50)同时拿两维高分
- ❌ 方向约束丢失：旧 AND 闸门"近 3 日不跌"在评分化时未迁移，暴跌板块照样排前列

核心变更：
- ✅ 真并集合并：Track1 无条件保留（上限 AMBUSH_TRACK1_MAX=16），Track2 只补位剩余名额 `max(0, TOP_K - len(T1))`
- ✅ Track1 成员不受 floor 过滤（状态机权威 > 单一截面分）
- ✅ 冷启动惩罚：`strength_trajectory` 不足 5 天 → 线性渐入系数 `len(trajectory)/5.0`，0 天轨迹 → 分数归零
- ✅ 方向惩罚：近 3 日累计跌幅每 1% → 总分打 0.9 折（乘性，下限 0.3），找回"近 3 日不跌"方向约束
- ✅ 噪声过滤增强：新增"昨成交/今日成交/涨幅排名/跌幅排名/换手率排名"关键词
- ✅ Schema 更新：`AmbushReservoirEntry` 新增 `days_in_reservoir` + `entered_strength`；`SectorContext` 新增 `strength_lookup`

验证结果（单元测试 5/5 通过）：
- 冷启动(0天轨迹) → score=0.0000（完全归零）
- 近 3 日暴跌 -5% → score 打 0.61 折（0.9^5≈0.59，符合设计）
- 完美 RRG + 0 天轨迹 → 0.0000（即使 RRG 满分，无轨迹=零分）

文件改动清单：
- `src/got/nodes/sector_analysis.py`：6 维评分常量 + 冷启动惩罚 + 方向惩罚 + 真并集合并 + 噪声过滤增强
- `src/got/nodes/aggregator.py`：蓄水池跨日合并 + 升格重走闸门 + `_run_ambush_gates` 提取
- `src/got/nodes/news_analysis.py`：原始新闻持久化 + 跨日发酵检测
- `src/got/schemas.py`：`AmbushReservoirEntry` 2 字段 + `SectorContext.strength_lookup`
- `src/services/stock_screener.py`：DDE 放松 + 排序改 DDE + Top 15
- `src/storage.py`：`Node4RawNewsRecord` 表
- `src/got/catalyst_config.py`：TODO 标记 + `RESERVOIR_EXPIRY_DAYS`/`RESERVOIR_PROMOTION_MIN_DAYS`
- `src/got/nodes/portfolio_decision.py`：TODO 标记（4 个退出参数）
- `scripts/test_ambush_fix.py`（新建）：5 项验证测试

**埋伏评分修复 + 蓄水池收紧 + 北向豁免（2026-08-25）：**

> 基于 0824/0825 运行回归。半导体强度分从 49.7 崩到 11.2（momentum 从 0.726 崩到 0.106），却被 `_score_turnaround` 反给满分误入 ambush 且锁死出不来（ambush_days 累积到 4）。同时蓄水池无上限膨胀（14→22 条），capital 每天恒定 degraded。

**问题诊断**：
- ❌ turnaround 逻辑反转：`below_count/len(recent)` 把"从未转正"当"刚转正"给满分
- ❌ 状态机 E14 锁死：退出要求 `not is_ambush`，但"越低越好"三维（rs_ratio/turnaround/strength_cap）让崩塌板块 is_ambush 恒 True
- ❌ 蓄水池无上限 + 结转无再评估：14→22 条，低质量条目挂 20 天才走
- ❌ 北向资金 2024-08 起官方停披，每天恒定 degraded 拖累置信度

**核心变更**：
- ✅ P0-1 turnaround 上穿检测：`_score_turnaround` 改检测 `<0.5→≥0.5` 翻转点 + 距今天数衰减（1天=1.0/2天=0.7/3天=0.4），无上穿→0 分
- ✅ P0-2 飞刀驱逐 E12b：ambush 分支新增硬退出 `rs_momentum<0.2 且 strength<25 → neutral`，不依赖 is_ambush 翻假
- ✅ 病因D trajectory 斜率约束：强度轨迹连续 3+ 天单调递减 → 乘性惩罚（0.8/0.6/0.4）
- ✅ P1 蓄水池收紧：`_merge_reservoir` 增加再评估（今日不在近失名单+驻留≥3天→除名）+ 容量上限 `RESERVOIR_MAX_SIZE=15` + 过期 20→10 交易日
- ✅ P2 浮点格式化：`stop_loss`/`risk_reward_ratio` 计算后 `round(,2)`，消除 reasoning 中的 `6.602999999999999` 泄漏
- ✅ P2 日历板块名对齐：`_normalize_sector_name` 双向别名映射（"半导体概念"↔"半导体"等 10 组）+ `_flatten_concept_universe` 双向展开
- ✅ P3 北向永久豁免：`EXEMPT_DIMENSIONS` 集合 + `confidence_multiplier` 分母排除豁免维度（3→2 有效维度）

**验证结果（0825 全量运行）**：
- 半导体：ambush→neutral（ambush_days 归零），落入蓄水池 near_miss(score 0.29<0.3)
- 蓄水池：22→10 条（低于上限 15）
- Capital：北向缺失不再计入，missing_dimensions 从"北向"变为"DDE"（真正缺失项）
- Phase 2：从"误买大为股份 ¥49k"转为"维持空仓"（评分更保守）

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：turnaround 上穿检测 + E12b 飞刀驱逐 + trajectory 斜率约束
- `src/got/nodes/aggregator.py`：蓄水池再评估 + 容量上限 + 调用方传入 near_miss_names
- `src/got/catalyst_config.py`：`RESERVOIR_EXPIRY_DAYS` 20→10 + `RESERVOIR_MAX_SIZE=15`
- `src/got/nodes/portfolio_decision.py`：stop_loss/risk_reward_ratio round(,2)
- `src/got/catalyst_calendar.py`：板块名双向别名 + `_normalize_sector_name`
- `src/got/nodes/capital_analysis.py`：`EXEMPT_DIMENSIONS` + confidence 分母修正
- `src/got/archive.py`：trading_recommendations.md 止损/风险收益比 `.2f` 格式化
- `src/services/stock_screener.py`：P5 催化事件 individual vs sector 互斥 + 利空不置顶

**板块噪声过滤升级 v2（2026-08-25）：**

> TDX 概念板块列表（880xxx）共 ~650 条，混入大量非可交易条目（统计指数/行情事件/财务标签/机构持仓/风格因子/公司财务动作）。旧版 `_is_noise_board` 仅过滤 49 条（7.5%），"创业涨跌/科创涨跌/北证涨跌/昨日突涨/昨成交20"等漏网进入 Node 2 板块分析，污染主线/埋伏判定。

**核心变更**：
- ✅ 全量抓取 TDX 板块列表 652 条逐条分析
- ✅ `_NOISE_BOARD_EXACT` 精确集合：222 条（7 族分类：N1 统计指数 / N2 行情事件 / N3 财务标签 / N4 机构持仓 / N5 风格市值 / N6 公司动作 / N7 边界）
- ✅ `_is_noise_pattern` 语义模式兜底：统计指数族后缀（`{主板|创业|科创|北证|全A|ETF}+{总值|流通|...}`）、事件状态前缀（`昨日|近期|最近|历史|即将|连续|预计`）、TDX 前缀
- ✅ 三层判定：精确集合 + 语义模式 + 动态发现（不写白名单，新产业概念自动进入）
- ✅ 地域板块（32 省）保留
- ✅ 验证结果（652 条真实数据）：噪声 222（34%）/ 保留 430（66%）/ 0 误杀 / 0 漏网

**文件改动清单**：
- `src/got/nodes/sector_analysis.py`：`_is_noise_board` 升级 v2（三层判定）+ `_NOISE_BOARD_EXACT` + `_is_noise_pattern`

**Phase 2 问财板块查询修复（2026-08-25）：**

> 0825 全流程 `unified_scoring: []`，Phase 2 选股池 0 只股票。主线A有水产品（98分）、埋伏有汽车芯片（55分），actionable 板块 2 个，但问财初筛全空。

**根因（三层叠加）**：
- ❌ **问词语法错误**：`所属同花顺板块是X` 只匹配行业板块，Node 2 输出的概念板块全部查空。`所属同花顺概念板块是X` 才能匹配概念板块
- ❌ **L2 缓存被股票代码污染**：`akshare.stock_board_concept_name_ths()` 返回列 `['name','code']`，代码 `df.columns[1]` 取到 code 列，缓存的 375 个"板块名"全是股票代码，L2 从未工作
- ❌ **TDX→THS 映射缺口**：水产品在 THS 概念列表中不存在，需跨类别映射到行业板块"养殖业"

**核心变更**：
- ✅ `board_name_resolver.py` 重写：取列修复（按优先级 `name/名称/概念名称/板块名称`）、同时拉取行业板块列表、缓存格式升级（`concept_names` + `industry_names` 分离）、脏缓存检测（>50% 6位纯数字→重建）、新增 `resolve_with_kind()` 返回 `(name, kind)`、`confirm()` 支持 kind 持久化
- ✅ `stock_screener.py` 双语法多轮：按 kind 排序 board_clauses（concept 优先 or industry 优先），第 1 轮带 DDE + 第 2 轮降级无 DDE，每轮按序尝试各 clause 命中即返回。新增 水产品/汽车芯片 行业关键词
- ✅ `portfolio_decision.py` 两处成分股查询同步双语法化
- ✅ `wencai_board_aliases.json` 新增 水产品→养殖业（kind=industry）

**验证结果（0825 数据重放）**：
- 水产品（kind=industry）→ 行业语法命中"养殖业" → 1只（DDE 4.00%）
- 汽车芯片（kind=concept）→ 概念语法命中 → 28只 → 行业过滤 22只 → Top 15
- 宇宙从 0 → 16 只，Phase 2 管道恢复

**文件改动清单**：
- `src/services/board_name_resolver.py`（重写）：取列修复 + 行业列表 + resolve_with_kind + confirm(kind) + 脏缓存检测
- `src/services/stock_screener.py`（核心改动）：双语法多轮查询 + 水产品/汽车芯片行业关键词
- `src/got/nodes/portfolio_decision.py`（两处改动）：成分股查询双语法化
- `data/wencai_board_aliases.json`（+1 条）：水产品→养殖业(kind=industry)
- `data/ths_concept_boards.json`（删除）：旧脏缓存重建
- `tests/test_board_name_resolver.py`（重写 23 用例）：kind 相关 + 列名适配 + 脏缓存检测

**选股漏斗 v2.1 改进（2026-08-25）：**

> 与问财修复同步提交的多项漏斗层改进。

**核心变更**：
- ✅ R4 软保底机制：主通道 Top 6（risk>50）+ 保底至多 2 只（risk>35），仅对 actionable 板块提供保底，防止强势板块垄断全部名额
- ✅ 风险评分梯度化：RSI 从硬阈值(-30)→3 级梯度(>85/-30, >80/-20, >75/-10)；5 日涨幅从 2 级→4 级；波动率从 1 级→2 级
- ✅ 新增风险维度：换手率衰减（近3日/前7日<0.5 → -10）、MA20 偏离度（abs >15%/-15, >10%/-8, >7%/-3）
- ✅ 布林带超卖修正：`boll_pos < 0` 从 +0 → +3（触下轨逆向策略正超额收益，Ni et al. 2020）
- ✅ 横盘过滤校准标记：`_STAGNATION_CALIBRATION_DATE` + `_STAGNATION_SAMPLE_COUNT` 标记回测时效，建议每 3 个月重新校准
- ✅ 资金面数据源标记：`sector_flow_all`(~300 板块) vs `top_concepts`(~8 板块) 区分日志，窄截面时输出警告
- ✅ 问财 DDE 放松轮（v1.5）：主线板块 DDE≥0.5% 仅返回 ≤2 只时，自动放松至 ≥0.3% 补充

**文件改动清单**：
- `src/services/stock_screener.py`：`_apply_r4_filter` + `_score_risk` 梯度化 + 布林带修正 + stagnation 标记 + DDE 放松轮
- `tests/services/test_stock_screener.py`（+323 行）：R4 软保底 5 个场景测试

**GOT 结构缺陷修复与 Agent 化增强（2026-08-28）：**

> 基于 `plan_agent_enhancement.md` 的两轨道实施。轨道 A 修复规则层结构缺陷（板块名契约/匹配标准分裂/门槛硬编码），轨道 B 补充语义层 Agent 能力（事件对齐/叙事校验/Agent 终审/矛盾仲裁）。

**轨道 A：规则层结构修复（无 Agent）**

- ✅ **A1 P6 兑现闭环复活**：`_get_board_code` 三级解析（name→code 反向映射 + 别名变体 + difflib 模糊 0.68）；`CatalystRealizationRecord` 新增 `resolution` 列三态化（NULL/resolved/unresolvable）；不可解析记录不再无限扫描；可观测性："本次兑现 N / 不可解析 M / 总待兑现 K"；清理脚本 `scripts/fix_p6_resolution.py`（366 滞留记录 → 260 可解析/106 不可解析）
- ✅ **A2 晋升路径归一化**：`_group_events_by_sector` 归一化 key + T5 多板块扇出消费；新增 `_lookup_sector_events` 别名变体查找替代精确匹配；晋升/P5 事件覆盖率从 2/15 上升；可观测性："蓄水池 N 板块，催化事件覆盖 M 个"
- ✅ **A3 置信度门槛统一**：`stock_screener.py` 0.7 字面量改为引用 `CATALYST_CONFIDENCE_GATE`；影子评估日志："门槛 0.60 会多放行 N 个事件"
- ✅ **A4 mini-GOT 全量资金**：`_cross_check_capital` 优先从 `sector_flow_all`（464 板块）查找，Top-8 保留作兜底；归一化 + 别名查找
- ✅ **A5 板块规范层收口**：新增 `_match_sector(name, target)` 统一入口（归一化精确 → 别名变体 → 受控子串 ≥3 字）；替代 `_cross_check_news/sentiment/capital` 4 处 ad-hoc 匹配
- ✅ **A6 泛化词护栏**：`_GENERIC_ROOTS` 集合（AI/芯片/电力等 ≤2 字高频词根）；命中时降权 50%（消息面 magnitude ×0.5）或降级为 neutral（资金面）/ 不计入有效匹配（情绪面）

**轨道 B：Agent 化增强（全部带影子开关）**

- ✅ **T5 事件→板块语义对齐层**（最高优先级）：`CatalystEvent` 新增 `affected_sectors` 字段；`_align_events_to_sectors` LLM 批量映射叙事/全球事件到板块宇宙（≤150 个）；一次调用产出多板块扇出，同时喂饱 A2 晋升/P5 重排/A1 解析兜底；影子模式 `SECTOR_ALIGN_SHADOW=true`（默认）；验证结果：12/12 事件对齐，8/12 多板块扇出，板块覆盖从 12 → 21（+75%）
- ✅ **T2 叙事校验者**：`_critic_verify_narrative` 反 Sycophancy 批判角色；4 项检查（时态/方向/板块映射/证据支撑）；批处理一次调用；影子模式 `NARRATIVE_CRITIC_SHADOW=true`（默认，2 周）；验证结果：成功识别时态回声 + 证据缺失事件
- ✅ **T1 Agent 终审**：`_agent_final_review` 复用 `src/agent/factory.py` 对 Top N 候选股做最终审查；只有否决权（reject → 降级展示移末位，不删除）；`ScreenerStock` 新增 3 字段（agent_verdict/agent_notes/agent_decision_signal）；默认关闭 `AGENT_STOCK_REVIEW_ENABLED=false`；预算上限 `AGENT_STOCK_REVIEW_MAX=2`
- ✅ **T3 矛盾仲裁者**：`_arbitrate_contradictions` 对 medium/high 矛盾调用 LLM 语义裁决；输入：矛盾描述 + 4 维证据 + 跨节点共振信号；权限受限（可 upgrade/downgrade 一级，不可 exclude）；`confidence < 0.6` 忽略；`Contradiction` 新增 3 字段（arbitration_verdict/reasoning/confidence）
- ✅ **T4 参数校准脚本**：`scripts/calibrate_catalyst_params.py` 读 P6 兑现统计 + 故事寿命 + PEAD 分析 → 产出 `reports/param_calibration_YYYYMMDD.md`；样本 < 30 只提示不出建议

**影子模式开关（`catalyst_config.py`）**：

| 开关 | 默认值 | 对应任务 | 转正条件 |
|------|--------|---------|---------|
| `SECTOR_ALIGN_SHADOW` | `true` | T5 对齐层 | 影子 1 周 + 人工抽查 ≥30 条准确率 ≥85% |
| `NARRATIVE_CRITIC_SHADOW` | `true` | T2 校验者 | 影子 2 周 + 误杀率 <20%（依赖 A1 兑现数据） |
| `AGENT_STOCK_REVIEW_ENABLED` | `false` | T1 Agent 终审 | 手动开启，归档重拍对比次日走势 |
| `AGENT_STOCK_REVIEW_MAX` | `2` | T1 成本预算 | R7 约束，单次全流程新增 LLM ≤8 次 |

**文件改动清单**：
- `src/got/catalyst_realization.py`：`_get_board_code` 三级解析 + `_build_name_to_code_map` + `check_and_record_realization` 三态标记 + NULL 安全查询
- `src/got/nodes/aggregator.py`：`_lookup_sector_events` + `_match_sector` + `_GENERIC_ROOTS` + `_arbitrate_contradictions` + `_cross_check_capital` 全量化 + A5/A6 三处 cross_check 统一
- `src/services/stock_screener.py`：`_agent_final_review` + `_parse_agent_verdict` + `_score_catalyst_relevance` 归一化 + 影子评估
- `src/got/catalyst_calendar.py`：`_align_events_to_sectors` + `_critic_verify_narrative`
- `src/got/schemas.py`：`CatalystEvent.affected_sectors` + `Contradiction` 3 仲裁字段
- `src/got/catalyst_config.py`：4 个影子开关常量
- `src/storage.py`：`CatalystRealizationRecord.resolution` 列
- `scripts/fix_p6_resolution.py`（新建）：A1 存量清理
- `scripts/calibrate_catalyst_params.py`（新建）：T4 校准脚本
- `scripts/verify_t5_alignment.py`（新建）：T5 端到端验证

**历史档系统（2026-07-24）：**
- ✅ `src/got/archive.py`（新建）：每日历史档存档模块
- ✅ `main.py`（修改）：新增 `--got` 入口（`python main.py --got`）
- ✅ `src/got/orchestrator.py`（修改）：`run()` 末尾自动调用 `archive_daily_run()`
- ✅ 目录结构：`data/got_daily/YYYYMMDD/` 包含 `market.json` / `sector.json` / `capital.json` / `news.json` / `sentiment.json` / `unified.json` / `reasoning.md` / `meta.json`
- ✅ 每个 JSON = `ctx.model_dump(mode="json")` 全量存档（含 None 字段，方便分析数据缺失模式）
- ✅ `reasoning.md` 汇总所有 6 个节点的 LLM 推理文本（人读友好）
- ✅ `meta.json` 记录运行元数据（耗时 / 数据质量摘要 / 缺失维度 / LLM 模型 / git commit）
- ✅ 永久保留（单档 ~50KB，一年 ~12MB）
- ✅ `data/got_daily/` 已被 `.gitignore` 的 `/data/` 规则覆盖

**GOT 驱动选股系统（2026-07-29）：**

> 基于每日 Phase 1 GOT 输出，从主线/埋伏板块成分股中精选 Top 2-3 只，用于波段操作（持仓周期约 2 周）

**核心设计**：
- ✅ 动态加载：读取 `data/got_daily/YYYYMMDD/` 下的 6 个节点 JSON，不硬编码任何板块
- ✅ 选股池提取：从 `sector_context` 提取主线（`active_mainline`）+ 埋伏（`ambush`）板块成分股
- ✅ 5 轮漏斗筛选：信号过滤 → 资金面 Top 15 → 波段技术 Top 8 → 风险排雷 Top 5 → 板块分散 Top N
- ✅ 评分体系（消息+资金主导）：资金面 30% + 消息面 20% + 板块强度 20% + 技术面(波段) 15% + 风险因子 15% → **已更新为**资金面 15% + 消息面 15% + 板块强度 25% + 技术面(波段) 25% + 风险因子 20%（2026-08-25）
- ✅ 波段技术指标：MA20趋势/布林带位置/周线趋势/RSI12/20日突破/MACD金叉/均线多头排列
- ✅ 回测支持：保存买入价/目标价(+10%)/止损价(-7%)到 `data/got_screener/YYYYMMDD.json`，次日可回测胜率

**关键方法**（`src/services/stock_screener.py`）：
- `run_got_mode(got_date, top_n)` — GOT 选股入口
- `_load_got_context(got_date)` — 动态加载 6 个节点 JSON
- `_extract_got_universe(got_context)` — 提取主线/埋伏板块成分股
- `_get_sector_constituents(sector_name)` — 成分股获取（三级降级：问财→东财→空）
- `_filter_got_finalists(universe, got_context, top_n)` — 5 轮漏斗筛选
- `_score_capital()` — 资金面评分（DDE百分位 + 板块资金流百分位）
- `_score_news()` — 消息面评分（LLM 语义匹配 + CONCEPT_UNIVERSE 展开降级）
- `_llm_score_sector_news()` — LLM 调用：事件→板块情感映射
- `_fallback_sector_match()` — 降级方案：CONCEPT_UNIVERSE 展开 + 子串匹配
- `_parse_sector_flow()` — 解析 Node 3 sector_flow 字符串列表
- `_score_technical_wave/risk()` — 技术面/风险评分
- `_compute_got_composite_score()` — 综合评分
- `_generate_got_reasoning()` — 推荐理由生成

**运行脚本**：
```powershell
# 使用今日 GOT 输出选股
python scripts/run_got_screener_v2.py

# 使用指定日期 GOT 输出
python scripts/run_got_screener_v2.py --date 2026-07-29 --top 3

# 次日回测
python scripts/backtest_got_screener.py --date 2026-07-29
```

**文件清单**：
- `src/services/stock_screener.py`（+500 行）：新增 GOT 选股模式（12 个方法）
- `scripts/run_got_screener_v2.py`（新建 ~150 行）：GOT 选股运行脚本
- `scripts/backtest_got_screener.py`（新建 ~200 行）：回测脚本
- `scripts/test_got_data_loading.py`（新建 ~80 行）：数据加载测试

**已知限制**：
- 成分股获取依赖问财/东财，反爬环境下可能失败
- 回测脚本假设次日可获取行情数据，非交易日无法回测

**选股评分重构（2026-08-10）：**

> 修复资金面和消息面评分区分度为零的问题

**资金面 `_score_capital()` 重构**：
- ✅ 砍掉死代码：龙虎榜(+40) — `CapitalEvidence` schema 无此字段
- ✅ 砍掉失效维度：北向资金(2024-08停披)、大宗交易(信号极少)
- ✅ 砍掉废弃参数：`fetcher_manager` 不再传入
- ✅ 新公式：DDE百分位(0-50) + 板块资金流百分位(0-50) = 0-100
- ✅ 新增 `_parse_sector_flow()` 方法：解析 Node 3 的 `sector_flow.top_concepts/industries` 字符串列表（格式 `"板块名(金额亿)"`）
- 效果：分数分布从 0-30 拓宽到 5-47.5

**消息面 `_score_news()` 重构**：
- ✅ LLM 语义匹配：将 Node 4 事件 + `CONCEPT_UNIVERSE`（~100个合法板块名）交给 LLM
- ✅ LLM 输出 `{sector: relevance × sentiment}` 映射，候选股 sector 精确匹配
- ✅ 新增 `_llm_score_sector_news()` 方法：LLM 调用 + JSON 解析
- ✅ 新增 `_fallback_sector_match()` 降级：CONCEPT_UNIVERSE 展开 + 子串匹配
- ✅ 砍掉 `_search_individual_stock_news()` + 关键词列表（依赖 SearchService API Key，匹配粗糙）
- ✅ 砍掉 `_sector_matches()` 静态方法（无调用方）
- ✅ 基础分从 50 降到 40（更保守先验）
- 效果：从全部 50/100 (10/20) → 40-100/100 (8-20/20)，AIGC概念等可命中满分

**Phase 2 架构评审与优化（2026-08-03）：**

> 基于 4 篇论文（FINCON NeurIPS 2024, RAPTOR NeurIPS 2025, FinCoT 2025, LLM-G-DRL 2025）的架构评审

**问题识别**：
- ❌ 原串行设计（持仓判断 → 选股 → 建议）存在循环依赖
- ❌ 候选股筛选条件过宽松（80-100 只），Stage 2 资金面评分耗时 100s+
- ❌ 问财"最低价"条件无意义且有害（限制波动在 0-2%，过滤掉活跃股）

**解决方案**：
- ✅ 采用 FINCON 风格的**统一决策节点**（3 Step 封装在 1 个节点，1 次 LLM 调用）
  - Step 1: 风险评估（约束设定）→ 仓位上限/止损线/板块限制
  - Step 2: 统一打分（持仓 + 候选股一起评）→ 消除循环依赖
  - Step 3: 组合优化（规则 + LLM 混合）→ 在约束下生成最优配置
- ✅ 问财初筛改为 **DDE 策略**（`dde大单净量>=0.8%`，替代宽松的`主力资金净流入>0`）
- ✅ 移除无意义的"最低价"条件，保留 DDE + 上市天数 + 非ST
- ✅ 每板块从 10 只降到 5 只，总候选从 80-100 降到 30-40
- ✅ Stage 2 耗时从 100s 降到 ~50s
- ✅ **板块类型适配 DDE 筛选**（2026-08-03）：
  - 主线板块：`dde>=0.8%`（当天强势，确认主力介入）→ **已放松至0.5%，≤2只时再降至0.3%(2026-08-25)**
  - 埋伏板块：`dde连续3天为正`（持续吸筹信号，不要求当天爆发）
  - 学术依据：DDE 连续性比单日阈值更重要（主力可操纵单日 DDE，但难以连续 3 天伪装）
  - 测试效果：主线板块平均 DDE=4.11%，埋伏板块平均 DDE=0.03%（连续为正）

**文件改动清单**：
- `src/services/stock_screener.py`（修改）：
  - `_get_sector_constituents_filtered()` 改为 DDE 策略
  - 新增板块类型适配：主线用 `dde>=0.8%`，埋伏用 `dde连续3天为正`
- `src/services/ths_screener.py`（修改）：
  - 扩展 `THSStock` 数据模型，增加 `price/change_pct/dde_net_pct` 字段
  - 实现 `_parse_json_api()` 解析问财 JSON API 返回的完整表格数据
- `src/got/nodes/portfolio_decision.py`（新建 ~500 行）：统一决策节点
- `src/got/schemas.py`（+4 个 Pydantic 模型）：Phase 2 Schema
- `src/got/orchestrator.py`（修改）：集成 Phase 2 调用
- `src/got/archive.py`（+2 个方法）：`archive_phase2()` + `load_phase2_decision()`
- `main.py`（修改）：`--got` 入口显示 Phase 2 输出
- `scripts/run_phase2.py`（新建 ~100 行）：独立运行脚本

**学术依据**：
- **FINCON (NeurIPS 2024)**：Manager-Analyst 层级，单一决策点同时处理选股和配置
- **RAPTOR (NeurIPS 2025)**：组合构建是优化问题，不是顺序决策流水线
- **FinCoT (2025)**：CoT 结构必须匹配领域实际工作流
- **LLM-G-DRL (2025)**：CoT 用于感知层（信息处理），RL/规则用于决策层（数值优化）

---

## 数据源架构升级（2025-07-23）

### 背景
push2.eastmoney.com HTTP 接口被东财反爬封锁（RemoteDisconnected），导致 EfinanceFetcher(P0) 和 AkshareFetcher(P1) 的东财源频繁失败。

### 解决方案：组合方案 B+D
- **Phase B**：引入 mootdx（通达信 TCP 7709）作为 P0 数据源 — TCP 协议不受 HTTP 反爬影响
- **Phase D**：重构 GOT 节点统一走 DataFetcherManager + 东财 HTTP 统一限流

### 数据源优先级（升级后）

| 用途 | P0 (TCP) | P0 (HTTP) | P1 | P2 | P3 | P4 |
|------|----------|-----------|----|----|----|-----|
| **K线** | MootdxFetcher | EfinanceFetcher | AkshareFetcher | PytdxFetcher | Baostock | Yfinance |
| **行情** | MootdxFetcher | EfinanceFetcher | AkshareFetcher(EM→Sina→Tencent) | — | — | — |
| **板块** | — | — | AkshareFetcher(via Manager) | — | — | — |
| **资金面** | — | — | AkshareFetcher(via Manager) | — | — | — |

### 关键文件

| 文件 | 作用 |
|------|------|
| `data_provider/mootdx_fetcher.py` | mootdx TCP 数据源（P0，BESTIP 自动探测服务器） |
| `data_provider/em_rate_limiter.py` | 东财 HTTP 统一限流（1s间隔+随机抖动+3次重试+连接池） |
| `data_provider/base.py` | 7 个新接口 + Manager 代理方法 |
| `data_provider/akshare_fetcher.py` | 实现 7 个新方法（北向/龙虎榜/融资融券/大宗/市场宽度/板块K线/板块排名） |
| `src/got/nodes/sector_analysis.py` | Node 2 重构：走 DataFetcherManager |
| `src/got/nodes/capital_analysis.py` | Node 3 重构：走 DataFetcherManager |
| `src/got/nodes/sentiment_analysis.py` | Node 5 重构：市场宽度走 DataFetcherManager |

### 技术亮点
- **TCP vs HTTP**：通达信 TCP 7709 协议与 push2 HTTP 是完全不同的协议栈，不在同一反爬战场
- **mootdx BESTIP**：自动探测最优服务器（带数据验证），解决旧 TDX 服务器 IP 过期问题
- **统一限流**：`em_get()` 入口保证所有东财 HTTP 请求串行化（threading.Lock + 1s间隔 + 抖动）
- **GOT 节点统一**：Node 2/3/5 从直调 akshare 改为走 DataFetcherManager，支持多源自动降级
- **BaseFetcher 扩展**：7 个新可选方法（北向/龙虎榜/融资融券/大宗/市场宽度/板块K线/板块排名）

### 已知限制
- **push2 HTTP 被封**：当前网络环境下 push2/push2his 返回 RemoteDisconnected（东财反爬）
- **mootdx TCP 可用**：TCP 7709 不受 HTTP 反爬影响，K线+行情正常
- **北向资金停披**：2024-08 后 `当日成交净买额` 全 NaN → **已标记为永久豁免维度**（不计入 confidence 分母）
- **ETF 申赎无历史**：akshare 仅返回快照，无法计算日度变化
- **雪球热榜保留直调**：Node 5 的雪球 3 榜仍需特定过滤逻辑，暂未走 Manager

---

[Unreleased]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.0...HEAD
[3.21.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.20.0...v3.21.0
[3.20.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.19.0...v3.20.0
[3.19.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.18.0...v3.19.0
[3.18.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.1...v3.18.0
[3.17.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.0...v3.17.1
[3.17.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.2...v3.15.0
[3.14.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.1...v3.14.2
[3.14.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.0...v3.14.1
[3.14.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.10...v3.5.0
[3.4.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.0...v3.4.7
[3.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.22...v3.4.0
[3.3.22]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.12...v3.3.22
[3.3.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.11...v3.3.12
[3.2.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.10...v3.2.11
[2.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.5...v2.3.0
[2.2.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.14...v2.2.0
[2.1.14]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.13...v2.1.14
[2.1.13]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.12...v2.1.13
[2.1.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.11...v2.1.12
[2.1.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.10...v2.1.11
[2.1.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.9...v2.1.10
[2.1.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.8...v2.1.9
[2.1.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.7...v2.1.8
[2.1.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.6...v2.1.7
[2.1.6]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.5...v2.1.6
[2.1.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.4...v2.1.5
[2.1.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v1.0.0
