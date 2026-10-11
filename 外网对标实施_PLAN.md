# 四项真·待实施 · 外网对标实施计划（2026-10-11）

> 性质：实施规格 + 拍板清单。**实施状态（2026-10-11）：项二/项一/项三 3a 已实施（均影子先行/零决策面变化，单测绿）；项三 3b 受 D-3 基线前置 + AlphaSift 接口缺口阻塞未开工；项四（竞品补强 A–G）另列独立多批次计划、已按批准序实施一期。代码审查三处 Warning（宏观扇出无门控 / Node4 prompt 无门控 / 迁移一致性陷阱）已修复。影子判据登记于 [`docs/got/verification.md`](docs/got/verification.md) 2026-10-11 补登节，变更录于 [`docs/CHANGELOG.md`](docs/CHANGELOG.md)。**原写“四项均未开工”。所有修法经仓内代码/数据闭环核验 + 外网文献/工程实践对标。

## 0. 外网对标结论摘要（三路检索有效；CoVe/证据分级路 exa 反复超时，以仓内 `archive/cp13_grounding_pipeline_research_20260926.md` 已登记的 THEME/CoVe/CAD/CausalStock/FinRipple/LangExtract 为准）

| 文献/实践 | 对本计划的直接启示 |
|---|---|
| **FinTexTS**（arXiv 2603.02702，语义多级配对数据集） | 新闻↔标的的**关键词精确匹配结构性失败**（检索不到未点名的语义相关新闻）；embedding 语义配对在 12 个预测模型上全面优于关键词匹配。⇒ 盲区修法应走**语义对齐**而非扩静态词表 |
| **FinRipple**（ACL 2025 Findings，事件涟漪效应） | 事件影响沿关系图传播，不直接提及的标的也受影响；纯 RAG embedding 检索会漏"深层市场关系"。⇒ macro_rule 静态 5 板块扇出太窄，且扇出目标应感知当日候选名单 |
| **PTEC**（arXiv 2309.12075，行业多标签分类） | 多标签行业归属任务上，**受约束解码 + 分类头**优于自由生成（避免不存在的标签）。⇒ 与仓内 C-P13 结论一致：L2 受约束生成（board 白名单）是正解 |
| **FinBERT 方向性分类系**（Fazlija & Harder 2022；arXiv 2602.00086 等） | 金融新闻三分类（正/负/中性）是成熟任务，DeBERTa/FinBERT 准确率 65–75%；**方向（direction）与事件类型（type）是两个正交维度**，学术上从不混用。⇒ OPEN-D10 修法①（字段分工）有文献背书 |
| **Shadow deployment 工程共识**（SE-ML/Snowflake/AWS/专利 TD Commons 12029 等） | 影子线五要件：①异步旁路不进决策路径 ②成对留痕 primary+shadow+同一 request_id ③预算/采样控制 ④**promote 判据先写后跑**（数值阈值预先文档化，防主观转正）⑤影子只能证明"变了什么"，不能证明"更好"——必须 ground truth join（DSA 的对应物 = P6 兑现数据）。转正参考窗口：低流量系统 4–8 周或 ≥10 交易日 |

---

## 1. 项一：埋伏候选事件→板块映射覆盖盲区（backlog §1.H）

### 1.1 根因（已代码级+数据级闭环，1009 实证 + 多日复核）

三层叠加，**不是 0.7 门槛问题**（全库 43/50 事件 ≥0.7，全天仅 1 例被拦）：

1. **两个板块宇宙不相交（主因，供给面）**：埋伏名单来自 Node 2 技术通道（吸筹/回落），当日 16 候选以周期/消费/服务类为主（动力电池/旅游服务/铅锌/酒店餐饮/煤炭/运输服务/电信运营…）；事件宇宙来自 Node 4（宏观规则+海外映射+LLM 叙事），以题材类为主（半导体链/军工/AI）。精确交集仅 2（黄金概念/固态电池）。多日复核（0928–1009 四个有效 suppl 读数日）：`legacy_miss_and_no_relations_hit` 稳定在 5–10/7–14。
2. **macro_rule 扇出与候选恒零交集（子盲区）**：`_full_build`（catalyst_calendar.py:301-316）按 `MACRO_CALENDAR_RULES` 扇出（券商/银行/周期/消费/房地产），且 `affected_sectors` 从未赋值（DB 实测 macro_rule 10 行全 NULL，分桶回退 `[event.sector]`）⇒ **宏观催化对埋伏通道恒无效**，尽管 confidence=1.0 恒过门槛。1009 实测 macro 扇出板块与 16 候选 0 交集。
3. **口径面：`affected_sectors` 词表 ≠ Node 2 板块词表**：金价事件 `sector='黄金概念'`、`affected_sectors=['有色金属']`，名单同时有「黄金/黄金概念/有色」三名——能否挂上取决于 board_aliases 桥接运气。glue relations 补挂只能覆盖题材类（1009: legacy_miss=14, relations_matched=4, still_missed=10）。

**关键区分**（写进任何修法的前提）：
- **题材类缺口**（黄金概念/固态电池/有色/化工机械等 ~5 个）= 映射/口径问题，**可修**；
- **事件源结构性缺口**（动力电池/旅游服务/铅锌/酒店餐饮/汽车服务/煤炭开采/煤炭/电气设备/运输服务/电信运营等 10–11 个周期/消费/服务类）= 7 源推送新闻天然不产此类前瞻事件，**修映射表治不了**——属数据源供给问题（与竞品计划 D 批同源）。

### 1.2 候选修法

| 方案 | 内容 | 外网对标 | 风险/约束 |
|---|---|---|---|
| **②+②b（推荐）** | **②** 事件扇出侧：`affected_sectors` 归一（统一走 `normalize_sector_name`/`board_aliases` 口径）+ `MACRO_CALENDAR_RULES` 补 `affected_sectors` 值（扇出列表扩到资源/周期/消费类常见板块，走词表归一）。**②b** macro 扇出增走 T5 语义对齐支路：复用 `_align_events_to_sectors`（候选域=当日埋伏候选+蓄水池前 150），macro 事件不再只靠静态扇出挂板块 | FinTexTS（语义配对>词表）、FinRipple（扇出应感知候选名单）、PTEC（受约束生成） | 改历史事件可比性；须 shadow 先行；与 #8a 先例（affected_sectors 不区分正负面）不冲突——只归一名字，不动正负语义 |
| ① | Node 2 出名单时带"事件供给覆盖"观测字段，名单选择感知事件供给 | — | 改 Node 2 通道语义（触候选池红线）；须 shadow；治标（名单还是那批板块，只是知道谁没事件） |
| ③ | 扩 GLOBAL_TO_A_SHARE_MAP 静态表覆盖 | — | 内容扩张非结构修复；13 条表维护性已被质疑；**治不了结构性缺口** |

**推荐组合执行序**：②（口径归一，纯管道修复，影子可量化：`relations_supplement.still_missed` 应降）→ ②b（macro 语义扇出，复用现有 T5，LLM 成本 = 每日 1 次批量对齐已含）→ 结构性缺口单独拍板（§4 拍板点 2）。

### 1.3 实施规格（按推荐②+②b）

- **文件锚点**：
  - `src/got/catalyst_config.py` `MACRO_CALENDAR_RULES`(:294-331)：4 条规则补 `affected_sectors`（词表用 Node 2 口径名）；新增扇出板块归一化声明
  - `src/got/catalyst_calendar.py` `_full_build`(:286 macro 分支 :301-316)：扇出时 `affected_sectors=归一后列表`（消 DB NULL）；`_generate_macro_calendar`(:383) 透传不变
  - `src/got/catalyst_calendar.py` `_align_events_to_sectors`(:822)：调用面扩到 macro_rule 事件（现仅 narrative/global_map）
  - `src/got/nodes/aggregator.py` `_group_events_by_sector`(:3083)：affected_sectors 值经 `normalize_sector_name` 归一后入桶（防第二套口径：归一入口唯一）
- **影子判据**（登记 verification.md）：
  - 观测字段：`ambush_gate_funnel.relations_supplement.legacy_miss_and_no_relations_hit`（现值 10/14）
  - 转正门槛（数值待拍板，建议值）：**题材类候选零事件率 < 30%**（现 ~100%）且 `still_missed` 较基线降 ≥50%，连续 ≥10 交易日
  - 回滚：`GOT_MACRO_SECTOR_ALIGN_ENABLED=false`（默认关，影子先行）
- **依赖**：与项二（OPEN-D10）无文件冲突可并行；但**门槛校准（5c 4.4）必须等项二修完**——在污染的 direction 上标定不可信。

---

## 2. 项二：OPEN-D10 `direction` 字段语义污染（162/464 兑现样本误判）

### 2.1 事实（DB + 代码闭环）

- `direction` 承载**两套正交语义**：
  - **价格方向**（positive/negative）：消费点 = aggregator 9 处 `direction != "negative"` 利空排除（:2094/:2148/:2172/:2527/:2813 等）+ P6 `_determine_pead_direction`(:558)/`_check_direction`(:742)
  - **事件类型**（drift/fade/binary）：消费点 = 埋伏仓位类型化系数 `CATALYST_TYPE_COEFF`（portfolio_decision.py:369 drift×1.0/fade×0.7/binary×0.4）+ aggregator:3521 `category_tag` 回退
- `GLOBAL_TO_A_SHARE_MAP` 13 条 direction 全用**类型语义**（drift7/fade3/binary3）；narrative prompt 允许 5 值混用（catalyst_calendar.py:579）；`schemas.py:524` 描述只写 positive/negative/binary（与实现不一致）
- DB 存量 35 条：binary13/drift15/fade4/negative2/positive1，**positive/negative 行混不同 category_tag**（negative+binary 2 条、positive+drift 1 条）= 字段混用直接证据
- 后果：`_check_direction` 只认 positive/negative/binary ⇒ drift/fade 样本恒 `False` ⇒ 162/464（35%）兑现误判，污染 P6 `direction_accuracy` 全部下游统计（含 0.7 阈值/衰竭窗口的标定输入）

### 2.2 修法二选一

| 方案 | 内容 | 文献对标 | 风险 |
|---|---|---|---|
| **①修源头（推荐）** | `direction` 统一为**价格方向**（positive/negative/binary）；事件类型**只用 `category_tag`**（已是独立字段，映射表已全部同值填写）。改动面：`GLOBAL_TO_A_SHARE_MAP` 13 条 direction 逐条人工改写（drift 条目如英伟达财报本质是"结果决定方向"→binary；fade 条目→按事件性质定正负或 binary）；`MACRO_CALENDAR_RULES` 4 条 binary 保持；narrative prompt 枚举收窄为 positive/negative/binary；schemas 描述修正；存量 DB 迁移脚本（drift/fade 行 direction←binary 或人工重标，category_tag 不动） | FinBERT 系文献：方向与事件类型是正交维度，从不混用 | 13 条逐条人工定方向（负责人过目）；存量 35 行迁移；`_determine_pead_direction` 的 binary 判据（\|r\|>1%）对原 drift 条目是否合适需复验 |
| ②修判定 | `_check_direction`/`_determine_pead_direction` 增 drift/fade 分支（drift→按显著移动判定，同 binary；fade→按衰减判定） | — | 治标：双语义继续共存，新代码仍会写错；fade"衰减"判定标准无实证依据（编的）；违背"禁第二套口径" |

**推荐①**。理由：`category_tag` 已存在且全量填写，迁移是字段分工不是新造口径；②会让污染永久化。

### 2.3 实施规格（按①）

- **文件锚点**：
  - `src/got/catalyst_config.py` GLOBAL_TO_A_SHARE_MAP(:18-139) 13 条 direction 重标（category_tag 保留原 drift/fade/binary 不动）
  - `src/got/catalyst_calendar.py`：narrative prompt :579 枚举收窄；:611 few-shot 示例同步；`_map_global_events` :512 利空线 `direction="negative"` 不变（本就是价格语义）
  - `src/got/schemas.py:524` CatalystEvent.direction 描述修正
  - `src/got/catalyst_realization.py`：`_check_direction`/`_determine_pead_direction` 不需改（收窄后输入只剩合法值）；新增一次性迁移脚本 `scripts/migrate_direction_semantics.py`（存量 drift/fade → binary + 留痕原值到 category_tag 已有）
- **验收**：迁移后 DB direction 取值 ∈ {positive, negative, binary}；重跑 `generate_report` direction_accuracy 口径复算，162 误判样本重判；单测覆盖 `_check_direction` 三合法值
- **依赖序**：**本项应最先做**（项一的门槛校准、5c 4.4、P6 标定都以干净的 direction 为输入）

---

## 3. 项三：Phase 4d §4.3 AlphaSift 影子接线

### 3.1 前置现状（关键改写：D-3 基线 0 天，从未开始累积）

- `funnel_stats` 计算只在 `run_got_mode`（stock_screener.py:913），落盘只在 `_save_got_result`(:2970) → `data/got_screener/{date}.json`
- **生产 Phase 2 不走这条路**：`portfolio_decision.py:3966/3972` 直调 `_extract_got_universe`+`_filter_got_finalists`，funnel_trace 留在 `self._last_funnel_trace` 无人消费落盘；main.py 两处只调 `screener.run()` 非 GOT 模式
- `data/got_screener/` 仅 6 文件（最新 20260825）；`data/got_daily/*/meta.json` 0 个含 funnel_stats ⇒ **4.3 的"攒 ≥10 交易日"前置实际 0 天**
- 回填可行性：`scripts/run_got_screener_v2.py:82` 调 `run_got_mode(got_date=…)`，`_load_got_context` 优先读 `data/got_daily/YYYYMMDD/` 归档 ⇒ 历史日可回填（但历史日的数据源快照与当日生产有差异，回填读数只能作基线参考，不作生产等价物）

### 3.2 实施规格（分 3a/3b 两 WI）

**3a（前置修复，独立 WI）：funnel_stats 接进生产链路**
- `src/got/nodes/portfolio_decision.py`：:3966/:3972 直调后，取 `screener._last_funnel_trace` → `_compute_funnel_stats` → 落 `meta.json`（复用现有字段路径，归档层零新文件）
- 验收：连续生产日 `meta.json.funnel_stats` 非空；D-3 基线从接线日起算
- 备选：只回填不接生产（`run_got_screener_v2.py` 逐历史日跑）——不推荐，回填读数≠生产读数，基线失真

**3b（4.3 本体）：AlphaSift 旁挂影子**
- 架构（对标 shadow deployment 五要件）：
  - `portfolio_decision.py` Step 3 后旁挂 `alphasift_service.screen(strategy=auto, context={sector_filter: actionable_sectors})`，**try/except 全包 + 独立超时**，失败只 log 不抛（决策路径零影响）
  - 产物落 `data/got_daily/{date}/shadow_alphasift.json`：`{picks[], factor_scores, strategy, agreement: {topk_overlap, jaccard, rank_corr}, latency, error}`
  - 开关：`ALPHASIFT_SHADOW=true` 默认开（真影子：跑但不生效）；转正开关复用 `ALPHASIFT_ENABLED`
  - **ground truth join**：shadow picks 与主漏斗 picks 同记 symbol，P6 兑现追踪对两组分别记 PEAD 多窗口收益（复用 catalyst_realizations 基建，加 `source: primary|alphasift_shadow` 维度）
- 转正门槛（数值待拍板，建议值）：≥10 交易日影子 + 候选池归零率（D-3 基线）不劣于主漏斗 + shadow picks 兑现率（P6）≥ 主漏斗 −5pp + 无超时/报错 >10%
- 前置：3a 完成 + D-3 基线攒满 ≥10 交易日（从 3a 接线日起算）

---

## 4. 项四：竞品经验补强计划批次 A–G（规划稿零落地）

- 现状：`竞品经验补强计划_20261006.md` 已过 UltraReview、已入库、未批准实施；建议序 **D0→D→G→A→B→C2→C1→E1→E3→E2→F**
- 本轮外网增量对标（不重写计划，只补证据）：
  - **D 批**（news_pulse 持仓异动归因 P0）：FinRipple 涟漪传播 + FinTexTS 语义多级配对支持"持仓股异动→事件归因"用 embedding 检索而非关键词；FinBERT 方向分类可作归因的方向面
  - **结构性缺口联动**：项一识别的 10–11 个周期/服务类板块零事件问题，本质与 D 批同源（事件源供给）——若批 D 批，可把"周期类板块事件源"并入 D 批范围
- 拍板点：开工范围（全量按建议序 / 只批 D 批先行 / 暂缓）

---

## 5. 依赖顺序总览

```
项二（OPEN-D10 治本，direction 干净化）
  └→ 项一（盲区修法②+②b，影子判据标定依赖干净 direction）
       └→ 5c 4.4 门槛校准（解锁条件一并满足）
项三 3a（funnel_stats 生产接线，独立）
  └→ D-3 基线攒 ≥10 交易日
       └→ 项三 3b（AlphaSift 影子）
项四（竞品计划，独立批；若批，D 批与项一结构性缺口合并考虑）
```

## 6. 拍板点汇总（已于当日问询，结论见 §7）

1. **项一修法**：②+②b（推荐）/ ①前置对齐 / ③扩表 / 组合
2. **结构性事件源缺口处置**：接受周期类板块走纯技术通道（零事件加分，现状）/ 加数据源产周期类事件（并入竞品 D 批）/ Node 2 名单降权零事件板块
3. **项二 OPEN-D10**：修源头①（推荐）/ 修判定②
4. **项三 AlphaSift 4.3**：3a 生产接线 + 3b 影子（推荐）/ 只回填不接生产 / 放弃 4.3
5. **项四竞品计划开工范围**：全量按建议序 / 只 D 批 / 暂缓
6. **影子判据数值确认**：项一转正门槛（题材类零事件率 <30%？）；项三转正门槛（兑现率差 ≤5pp？）；窗口 ≥10 交易日是否维持

## 7. 拍板结论（2026-10-11，负责人已确认）

1. **项一修法**：✅ **②+②b**（affected_sectors 口径归一 + MACRO_RULES 补扇出 + macro 走 T5 语义对齐）
2. **结构性事件源缺口**：✅ **接受现状**（周期/服务类板块纯技术通道零事件加分，不并入竞品 D 批、不改 Node 2 名单语义）
3. **项二 OPEN-D10**：✅ **修源头①**（direction 统一价格语义，事件类型只用 category_tag；13 条重标 + 存量迁移）
4. **项三 AlphaSift 4.3**：✅ **3a+3b**（funnel_stats 生产接线 → D-3 基线攒满 → AlphaSift 旁挂影子 + P6 兑现对照）
5. **项四竞品计划**：✅ **批准全量按建议序** D0→D→G→A→B→C2→C1→E1→E3→E2→F 逐批实施逐批验
6. **影子判据数值**：✅ **采纳建议值**——项一：题材类候选零事件率 <30% 且 still_missed 较基线降 ≥50%；项三：shadow 兑现率 ≥ 主漏斗 −5pp 且无超时/报错 >10%；窗口均 ≥10 交易日

> 全部六项均为推荐方案。实施序按 §5：项二最先（direction 干净化是项一判据标定与 5c 4.4 的前置）→ 项一 → 项三 3a（可与项二并行，独立链路）→ 3b（等 D-3 基线）→ 项四按批准序逐批。每项单 WI 单 commit，影子先行，判据登记 verification.md，转正需人工改默认值。
