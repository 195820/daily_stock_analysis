# E1 A 案：rule_verdict → 四档关键词映射表（草案 v1，2026-10-15）

> **性质**：纯统计口径对齐规则（零生产改动）。
> **消费入口**：`t3_routing_shadow`（`portfolio_decision.json`，`GOT_T3_ROUTING_SHADOW` 缺省 1）；输入与 Node 6 `contradictions[].resolution` 同源。
> **状态**：✅ **负责人复核通过（2026-10-15，映射口径认可）** → 不一致率统计启用（读数起点自首个 `t3_verdict` 有值日计；首日 4 样本 dwell<2 T3 skip 均无 `t3_verdict`，不构成对齐样本）。两个待拍板点随本稿提交：① 四档语义定义；② avoid→keep 11 条「非不一致」单列口径。

## 口径
- 输入：`t3_routing_shadow[].rule_verdict`（Node 6 `Contradiction.resolution` 中文裁决文本）。
- 输出：`{avoid, reduce, hold, buy}` 四档（统计口径对齐用，不改任何生产字段）。
- `contradiction_id` 格式 `pair:timeframe`：`pair` 为模板判别主键、`timeframe` ∈ {cross, cross_dimension} 仅作展示，不参与映射。
- 排除规则：`rule_verdict is None`（如 `regime_vs_sectors` 生产者无 resolution）→ 不参与统计；`t3_verdict is None`（dwell<2 T3 skip / 置信<0.6 / LLM 失败）→ 不构成对齐样本。
- 判别顺序：按行首序匹配，命中即停；全部未命中 → `uncounted`（新模板出现信号）。

## 四档语义（负责人已拍板，2026-10-15）
- **avoid** = 阻止**新买入**（追涨约束/拦新买）；不约束既有持仓。
- **reduce** = 对既有主线/仓位**去风险**（降置信/降仓/去敞口）。
- **hold** = 口径声明/观望/维持现状（含「以综合口径为准」「供复核」「蓄势」）。
- **buy** = 正向倾向（低估/逢低建仓/加仓）。
- **豁免口径（负责人已拍板，2026-10-15）**：avoid→keep 11 条（「单板块…勿仅因资金流入追涨」模板）按「**非不一致**」单列，**不进不一致率分母**——T3 keep 维持的是矛盾严重度，不否定「勿追涨」约束；avoid 拦新买、keep 维持观察，两者动作指向不同层面。11 条另列观察项，若后续出现该模板被 T3 downgrade 则仍属真不一致。

## 映射表（模板级优先 + 关键词兜底）

| # | 模板判别（pair / 文本指纹） | 四档 | 依据 |
|---|---|---|---|
| 1 | 文本含「单板块资金领先」且含「勿仅因资金流入追涨」 | **avoid** | 警惕追涨 = 阻止新买入 |
| 2 | 文本含「建议降低」且含「是否被技术面低估」 | **MIXED(uncounted)** | 双向（降 X + 抬 Y），单档不可表达 |
| 3 | 文本含「降低置信度」且含「避免追高站岗」 | **reduce** | 对既有主线去风险 |
| 4 | 文本含「降低仓位或观望」 | **reduce** | 动作实义为降仓，观望为并列弱化 |
| 5 | 文本含「谨慎追涨」 | **avoid** | 阻止新买入 |
| 6 | 文本含「以综合口径为准」 | **hold** | 口径声明，无动作 |
| 7 | 文本含「供复核」（glue） | **hold** | 维持现状 |
| 8 | 文本含「蓄势」 | **hold** | 底部震荡无动作 |
| 9 | 文本含「逢低建仓」或「长期逻辑加仓」 | **buy** | 正向倾向 |
| 10 | 关键词兜底：含「低估」/「逢低」/「布局」→ buy；含「降置信」/「降仓」/「减仓」→ reduce；含「追涨」/「追高」/「勿买」→ avoid；含「观望」/「维持」/「复核」/「口径」→ hold | — | 未见过新文本时兜底 |
| — | 其余 | **uncounted** | 新模板告警，人工裁决后扩表 |

## 与 t3_verdict 的对齐语义（统计启用时用）
- t3 `downgrade`（规则层遗漏风险，降一级）↔ rule 层 avoid/reduce —— 一致
- t3 `keep`（维持规则层）↔ rule 层 hold —— 一致
- t3 `upgrade`（规则层过度悲观）↔ rule 层 avoid/reduce 且 t3 upgrade —— 已知张力，单独计数
- t3 `keep` + rule 层 avoid（「单板块…追涨」模板 11 次）→ **非不一致**：模板语义是「勿追涨」（约束动作）而非「降级板块」，T3 keep 维持的是矛盾严重度。对齐表按此注记。

## 历史对照（85 条 resolution，20260803–20261015；源 `unified.json` 的 `contradictions[].resolution`，与 `rule_verdict` 同源）
| 四档 | n | 其中 t3 有值 | t3=keep | t3=downgrade | t3=None（T3 前时代/skip/失败） |
|---|---|---|---|---|---|
| avoid | 33 | 25 | 11 | 14 | 8 |
| reduce | 3 | 1 | 1 | 0 | 2 |
| hold | 30 | 26 | 26 | 0 | 4 |
| buy | 2 | 0 | — | — | 2 |
| MIXED(uncounted) | 5 | 0（全部 0806–0817，早于 T3 LLM 仲裁时代） | — | — | 5 |
| uncounted | 0 | — | — | — | — |

## 关键观察（供负责人复核参考）
1. 模板全集封闭（Node 6 生产者硬编码 9 模板，其中 `regime_vs_sectors` 无 resolution）；映射表 10 行覆盖全部 85 条历史文本，uncounted=0。
2. avoid↔downgrade 强相关（14/25），hold↔keep 强相关（26/26）——映射表与 T3 语义兼容。
3. 已知张力：avoid→keep 11 次（「单板块…追涨」模板，T3 keep 维持的是矛盾严重度而非否定「勿追涨」约束）——对齐统计时按「非不一致」单列，避免虚高不一致率。
4. `t3_verdict` 首个有值日待 `dwell_days>=2` 后的下一交易日；归一化统计自映射表复核通过后启用。
5. **数据卫生注记（2026-10-15 冒烟发现）**：早期归档 20260805–20260814 的 `unified.json` 内嵌 `\u0000` 转义序列（glue 前身 LLM 输出未清洗，如 `「CPO概念\x00」`，散布于 `key_opportunities`/`confirmed_sectors[].sector_name`/`reasoning` 等 21 字段/日）；0815 起归档干净。归档原文按纪律**不回改**；凡从归档抽取文本二次加工（含本稿附录样本 4 的 `description`，已剔除 4 个 NUL）须 `text.replace('\x00','')` 消毒，否则产出文件含 0x0 字节、`file` 报 data、UTF-8 读取器拒收。


## 附录：抽检样本 10 条（请负责人逐条核「四档」判定）

1. **[20260818]** `sector_capital_mainline_mismatch:cross_dimension` sev=high → 四档 **avoid** | t3=None
   - 文本：「银行」单板块资金领先但与整个家族的综合口径（技术+资金+消息+情绪）排名错位，可能为个别板块单兵抢跑或口径未确认，建议以综合分复核其主线地位，勿仅因资金流入追涨
   - 描述：资金流领先板块「银行」（净流入 26亿，板块级）所属家族「银行」在 Node 2 综合排名仅第 6，资金与家族综合评分未共振

2. **[20260901]** `sector_capital_mainline_mismatch:cross_dimension` sev=high → 四档 **avoid** | t3=downgrade
   - 文本：「银行」单板块资金领先但与整个家族的综合口径（技术+资金+消息+情绪）排名错位，可能为个别板块单兵抢跑或口径未确认，建议以综合分复核其主线地位，勿仅因资金流入追涨
   - 描述：资金流领先板块「银行」（净流入 72亿，板块级）所属家族「银行」在 Node 2 综合排名仅第 5，资金与家族综合评分未共振

3. **[20260827]** `capital_vs_sentiment:cross_dimension` sev=medium → 四档 **avoid** | t3=None
   - 文本：资金强流入但情绪平淡，警惕资金单兵抢跑后回落，谨慎追涨
   - 描述：机构态度 +0.61（强流入）vs 恐贪指数 0.57（中性区间），资金与散户情绪未共振

4. **[20260806]** `sector_capital_mainline_mismatch:cross_dimension` sev=high → 四档 **MIXED** | t3=None
   - 文本：建议降低「黄金等5板块」置信度，关注「光通信等18板块」是否被技术面低估
   - 描述：主线错位：Node 2 核心主线「黄金等5板块」（黄金, 有色, 铜）在资金 top-5 中无匹配，而「光通信等18板块」独占资金 top-1（70亿）但 Node 2 排名仅第 3

5. **[20260819]** `sector_capital_no_support:cross_dimension` sev=medium → 四档 **reduce** | t3=None
   - 文本：技术领先但资金未确认的主线应降低置信度，等待资金面后续跟进，避免追高站岗
   - 描述：Node 2 核心主线家族「化纤」（综合第 1，共振100%）在板块级资金流中无任何正向流入，技术强势缺乏资金确认

6. **[20261015]** `regime_fresh_switch:cross` sev=low → 四档 **reduce** | t3=None
   - 文本：regime 处于过渡期，信号不稳定，建议降低仓位或观望
   - 描述：regime 刚切换（dwell=1），所有矛盾检测低置信度

7. **[20260831]** `tech_composite_rank_divergence:cross_dimension` sev=medium → 四档 **hold** | t3=keep
   - 文本：综合分（技术+资金+消息+情绪）与纯技术分排名背离，决策以综合口径为准
   - 描述：「半导体」技术分第 7 / 综合分第 3，技术分与综合分排名落差 4 位

8. **[20260923]** `glue_vs_node3_capital:cross_dimension` sev=medium → 四档 **hold** | t3=keep
   - 文本：保留 glue 判定（A6 直替契约），仅标记口径矛盾供复核
   - 描述：「减速器」glue 资金口径(divergent) 与 Node 3 规则口径(aligned) 不一致: 减速器净流入4.3亿(小额/全量)

9. **[20260728]** `short_tech_vs_capital:short_term` sev=medium → 四档 **buy** | t3=None
   - 文本：机构借技术回调逢低建仓，短期偏差不影响中长期布局。
   - 描述：短期技术面偏空但机构在加仓

10. **[20260728]** `medium_tech_vs_capital:medium_term` sev=high → 四档 **buy** | t3=None
   - 文本：中期技术面受结构性担忧拖累，机构基于AI长期逻辑加仓。
   - 描述：中期技术面偏空但机构在加仓
