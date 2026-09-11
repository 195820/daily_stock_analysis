# GOT B 层修复方案：同一概念多处计算与同名两义

> **定位**：本轮只修 **B 层（概念一致性）** —— 即「同一个语义量在管道里被算两次或换名两次，
> 导致下游拿到的不是上游那个」这一类缺陷。A 层（运行模型 / 同日重跑幂等）见
> [`got_run_model_plan_0911.md`](got_run_model_plan_0911.md)，不在本文射程。
>
> **遵循既有工程约定**（与 `agent_glue_plan_0903.md` / `got_run_model_plan_0911.md` 同构）：
> 事实核查先行（§2）、**被推翻的错误结论公开撤销**（§2.7）、**防误修清单**（§2.8）、
> 已确认决策链（§4）、**可计数的强制验收红线**（§6）、范围外与挂起项显式列出（§7）。
>
> **状态：待实施（用户已确认 B1–B6 处置）**。创建日期：2026-09-11。
> 事实核查基准：HEAD `a803e09` + 工作区快照（2026-09-11）。**若实施时代码已变动，以代码为准。**
>
> **修订台账**：[`got_revision_ledger_0911.md`](got_revision_ledger_0911.md) ——
> 本文的「2026-09-11 修订」注已汇总在该台账（16 条外部质询 + 13 处同源传播 + 11 处复核漏网 + 3 处自曝）。
>
> **实施审核（2026-09-11）**：[`got_impl_review_0911.md`](got_impl_review_0911.md) —— 本轮实施的审核结论（**2 个 P0** / 8 个 P1 / 红线达标总表 / 承诺脚本缺失清单）。

---

## 0. 结论摘要

B 层的缺陷**不是**「同一概念被独立计算了 N 次」（那是设计分歧，需要选权威值），
而是两种更具体、更可判定的形态：

| 形态 | 判据 | 条目 |
|---|---|---|
| **同一信号被消费两次** | 两处读的是**同一个变量**（如同一 `SectorIntel.capital_check` 字段），各加一次分 | **B1**（资金/消息/情绪）、**B7**（板块分类） |
| **同名不同量纲 / 派生幽灵** | 同一个名字下有量纲不同的两个数，或一个零消费者的派生值 | **B4**（`risk_budget`）、**B6**（`mention_count`） |

另有两条属于「输出有两条来源、其中一条是死的」：**B3**（仓位上限的 `base_position` 死参数）。

**B 层与 A 层是同一个病的两个面**：A 层是**时间维度的自引用**（把当天当前一天），
B 层是**概念维度的自引用**（同一个概念在管道里被重新定义）。

**关键区分（决定修法）**：

> 判别「两套定义」还是「重复计数」，看两处读的是**同一个变量**还是**各自重算的输入**。
> - 同一个变量被消费两次 → **重复计数** → 只需要问"这次加分是不是多余的"
> - 各自重算的输入 → **两套定义** → 需要选权威值（是设计决策）

0910 里 A 层与 B 层**同时**起作用：`market_regime.regime` 因 A 层自引用从 `bull` 变 `transition`（错输入），
而 `REGIME_MULTIPLIER` 这类映射在 Node 6 与 Phase 2 各有一份（B1/B3 类）—— 即使输入对了，两处也会各算各的。

---

## 1. 触发：0910 实证

同一份 0910 输出里，读者能同时看到：

| 现象 | 位置 |
|---|---|
| 「板块强度 **84** 分」但实际计权用的是 **88.8** | `trading_recommendations.md` vs `portfolio_decision.json:dimension_scores["sector"]` |
| 「风险预算 **50%**」与「风险预算 **5.5%**」并列 | `reasoning.md:9` vs `trading_recommendations.md:8` |
| 同一只股在同一次 LLM 输入里带 **93600** 与 **13提** | `sentiment_analysis.py:1184` vs `:1207` |
| 「航运概念」被归入「军工」家族 | `sector.json:frame_group_lookup` |
| 「三层仓位系统」中的第一层（波动率调整）不存在 | `aggregator.py:263-265` 注释 vs `:2988` 死形参 |

---

## 2. 事实核查

> 约定：所有代码引用为 **HEAD `a803e09` 行号**；若实施时代码已变动，**以代码为准**。

### 2.1 B1 —— 板块强度：四个口径 + 交叉验证被计两次

#### (a) 四个口径

| # | 口径 | 位置 | 公式 | 0910 粮食概念 | 0910 MLCC |
|---|---|---|---|---|---|
| ① | Node 2 `strength_score` | `sector_analysis.py:439-444 / 1579-1614 / 1656-1666` + 热度加成 `:1393-1409` | 4 维截面百分位加权 ×100，THS 热度 +1/+3/+5，cap 100 | **83.8** | **43.6** |
| ② | Node 6 `tech_score` | `aggregator.py:1236, 1369` | = ①（透传副本，不变换） | 83.8 | 43.6 |
| ③ | Node 6 `composite_score` | `aggregator.py:1277-1304` | ② + 资金 +10/−15 + 消息 −24~+20 + 情绪 +5/−5 + 生命周期 +8/−15，clip(0,100) | **86.8** | **53.6** |
| ④ | Phase 2 `dimension_scores["sector"]` | `portfolio_decision.py:756-812` + 过热乘性 `:814-854` | ③（或①，见 (c)）+ 角色 +10/+8/+3/+1/−20 + 资金 +5/−10 + 消息 +5/−10 + 情绪 +3/−5，clip 后 ×过热系数 | **88.8** | **51.6** |
| ⑤ | `reasoning` 展示值 | `stock_screener.py:2715-2717` | 取 ① | **84** | **44** |

**同一条输出里同一个板块最多出现 4 个不同数值。** 排序在三处均保持（船舶 > 粮食 > MLCC），
但**展示值 84 与计权值 88.8 不是同一个数**。

节点内部另有聚合口径（非同口径、各有用途）：`MainlineGroup.group_score`（成员 max，`:3309`）、
`ThemeFamily.family_score`（成员 mean，`:3813`）、`industry_top`（行业板块**独立截面**重算，`:3901-3924`）。

#### (b) 核心缺陷：同一组信号被计两次

`SectorIntel` 在 `aggregator.py:1371/1373/1375` 携带 `capital_check` / `news_check` / `sentiment_check`
三个字段往下传。**Node 6 用它加过分（`:1282-1294`），Phase 2 从同一个 intel 字典再读一遍，再加一次
（`portfolio_decision.py:789-808`）**：

| 信号 | Node 6 已加（`:1282-1294`） | Phase 2 再加（`:789-808`） | 合计 |
|---|---|---|---|
| `capital_check = aligned` | **+10** | **+5** | +15 |
| `capital_check = divergent` | **−15** | **−10** | −25 |
| `news_check = catalyzed` | 最高 **+20** | **+5** | +25 |
| `sentiment_check = high` | **+5** | **+3** | +8 |
| `sentiment_check = low` | **−5**，**ambush 豁免**（`:1293`） | **−5**，**不豁免**（`:807-808` 无 role 判断） | ambush 侧被补回 −5 |

**最后一行是静默撤销**：Node 6 特意为埋伏板块免掉情绪低分惩罚，Phase 2 又把同一笔罚回来。

#### (c) base 来源不一致

同一条 `_adjust_score_by_sector_intel`，两种基准：

- **候选股**（`portfolio_decision.py:2541-2543`）：`base = stock.sector_score` ← `stock_screener.py:1076,1096`
  的 `sector_info.get("strength_score")` = **①（Node 2 原始分）**
- **持仓股**（`:462-475`）：`base = _find_stock_sector(...)["strength"]`；一级命中 = **③（`intel.composite_score`，`:1433`）**，
  降级 = `strength_lookup` = **①**

#### (d) 两个 LLM 入口看不同的数

| 入口 | 位置 | 喂入值 |
|---|---|---|
| A6 语义粘合（`temperature=0`） | `aggregator.py:719-720` | **① `strength={detail.strength_score:.1f}`** |
| Node 6 板块精炼（`temperature=0.3`） | `aggregator.py:1810` | **③ `score={s.composite_score:.0f}`** |

### 2.2 B2 —— 家族归类

#### (a) 三套并存，只有一套被消费

| 套 | 来源 | 词表 | 消费者 |
|---|---|---|---|
| `frame_group` | Node 2 **硬编码关键词** `FAMILY_KEYWORDS`（`sector_analysis.py:168`）+ `_build_frame_group`（`:582-587`，**声明顺序短路，首个命中胜出**） | **14** 族 | **10+ 处**：政策加分 `:3007-3020`、家族过热 `:3476-3484`、MainlineGroup `:3279-3297`、主线续航 `aggregator.py:3470-3474`、Phase2 家族限仓 `portfolio_decision.py:519-525,698`、过热降权 `:838-852`、持仓拥挤度 `:2390-2416`、`main.py:1386-1394` |
| A6 `families` | LLM（`aggregator.py:942-1038`），受控词表 `CONTROLLED_FAMILIES`（`schemas.py:1033-1036`） | **12** 族 | **零**（影子，仅 `aggregator.py:1033` 被 `len()` 记日志） |
| `ThemeFamily.family_name` | Node 2 相关性聚类（`:3607-3682`），fallback 才用 `frame_group`（`:3790-3842`） | 动态命名 | Node 2 自身 prompt、Node6 `_detect_family_capital_mismatch` |

#### (b) 三处缺陷

1. **词表三处不一致**：`FAMILY_KEYWORDS` = **14** 族（含 `华为生态`、`前沿科技`）、
   `CONTROLLED_FAMILIES` = **12** 族（缺这两个）、`agent_glue_plan_0903.md` 说 **13** 族（L771 / L884）。
   → 转正后 `华为生态`/`前沿科技` 两个族的板块会被 S4 越界校验判为越界。
2. **「其他」占 43.8%**：0910 `frame_group_lookup` 217 条中 95 条落「其他」。
3. **已知错分**（含 0903 §5.3.3 L579-588 自列但未修）：

| 板块 | 现分组 | 成因（关键词表） | 本轮人工期望（**R-B4 的判据源**） |
|---|---|---|---|
| `航运概念` | 军工 | `:245` 军工族关键词含 **`"航运"`** | `周期资源` |
| `航空` | 军工 | `:245` 军工族含 `"航空"` | **待定** —— 民航 ≠ 军工；候选新建 `运输` 族。**未确认则本轮不动、不计入 R-B4** |
| `OLED概念` | AI软件 | `:180` AI软件族含 `"OLED"` | `AI硬件` |
| `MiniLED` / `折叠屏` | AI软件 | `:180` 与 `OLED` 同簇 | `AI硬件`（**须与 `OLED` 同批同移**，见 §5.2 改法 2） |
| `光刻机` / `玻璃基板` | AI硬件 | `:175` 半导体上游词混入 AI硬件 | `光刻机` → 设备子集；`玻璃基板` **保留 AI硬件** |
| ~~`保险`~~ | 金融 | — | **非错分**：`:233` 金融族**已含** `"保险"`，`_build_frame_group("保险") == "金融"` 是**正确命中**。真实问题是「无独立保险族」（粒度问题），**移出本轮射程** |

#### (c) 0910 两套对照：14 条不一致，仅 3 条真冲突

真冲突：`880945 OLED概念`（glue=AI硬件 / fgl=AI软件）、`880642 供销社`（glue=消费 / fgl=农业）、
`880728 航运概念`（glue=周期资源 / fgl=军工）。其余 11 条属「fgl=其他，glue 补了归属」。

注：**glue 对全部 32 个候选都给了 family**，而 prompt 只要求「仅对落『其他』的板块」——指令未被遵守。

#### (d) 0903 自身有一处未调和的矛盾（**决定 B2 修法的关键**）

| 位置 | 内容 |
|---|---|
| L555（§5.3.3 范围定义） | 「**范围 = 候选里落「其他」的 11 个**，不是 `frame_group_lookup` 里全部 95 个」 |
| L579-588（同节的「已知错误分组（**Agent 应能纠正**）」） | 列出 `航空`(军工)、`OLED概念`(AI软件)、`光刻机`/`玻璃基板`(AI硬件)、`保险`(金融) —— **全部不是「其他」** |

**按 L555 的范围，Agent 永远看不到这几个错分板块**，与 L579 的「Agent 应能纠正」直接冲突。

另需注意 0903 的两条保留决策：
- **P1**（L771）：`FAMILY_KEYWORDS` + `_FAMILY_NAMES` + `_build_frame_group` **保留**，作 glue 给 `null` 时的兜底
- **F1-5**（L872）：`frame_group` 兜底链 = `glue.families` → `frame_group_lookup.get(name)` → `_build_frame_group(name)`，**不变**

→ **词表不会退役**，「修了也被覆盖」的假设不成立。

### 2.3 B3 —— 仓位上限

#### (a) `base_position` 是死参数

`aggregator.py:2985-3034` 完整签名 `(regime, confirmed, base_position)`，
**`base_position` 在函数体内从未被引用**（全文仅出现在形参列表）。
调用点 `aggregator.py:260` 传入 `market.position_limit`（Node 1 的波动率调整仓位）后**直接丢弃**。

函数实际逻辑：

```
position = 0.4 + 0.08 * actionable_count + 0.1 * avg_confidence      # :3011
position *= REGIME_DWELL_SCALE[(regime.regime, dwell_stage)]          # :3013-3025
if regime.transition_count >= 5: position *= 0.8                      # :3028
position = min(position, regime.risk_budget)                          # :3032
return clip(position, 0.0, 0.8)                                       # :3034
```

#### (b) `avg_confidence` 是「数据可用性计数器」，不是置信度

`SectorIntel.confidence`（`aggregator.py:1307-1316`）：

```
confidence = 0.5
+ 0.2  若 detail 存在（有 Node 2 数据）
+ 0.1  若 capital_check != "neutral"
+ 0.1  若 news_check   != "neutral"
+ 0.1  若 sentiment_check != "low"
```

即**「有多少个维度给出了非中性信号」**。故 `final_position_limit` 的真实自变量 =
`(regime, 板块数量, 信号数量)` —— **维度越多信号，仓位上限越高**，与"风险"无直接关系。

#### (c) Node 1 的波动率仓位全链丢弃

| 字段 | 产出 | 读取点 |
|---|---|---|
| `vol_adjusted_position` | `market_analysis.py:661` | **零**（仅 `schemas.py:152` 定义） |
| `regime_scale` | `market_analysis.py:662` | **零**（仅 `schemas.py:156` 定义） |
| `position_limit` | `market_analysis.py:653` | `aggregator.py:260`（作未用的 `base_position`）、`:4304/4317/4341-4347`（`_simplified_news_override` 锚点，仅 LLM override 时生效） |

0910 实证：Node 1 `market.position_limit = 0.365`（= 0.5 × 0.73），
`unified.final_position_limit = 0.496` —— **两者无关**。

#### (d) 一个被删除的安全限幅，理由基于不存在的层

`aggregator.py:263-265`：

```python
# 移除日限幅：三层仓位系统（波动率调整 + 状态缩放 + 回撤保护）已提供平滑过渡
# 旧版日限幅 bug：当 market.position_limit=0.0 时，clip 到 [−0.2, 0.2] 导致锁死 20%
```

「三层」中的**第一层（波动率调整）** 正是被丢弃的 `base_position`。**理由不成立。**

#### (e) 统计（独立复现）

35 个 8 位日期目录；其中 8 天（20260724–0804）`unified.json` 无 `market_regime.risk_budget`，
**可比 27 天**：

- **严格相等 19/27 = 70.4%**；不等 8 天，**全部** `fpl < risk_budget`
- `actionable≥6` 的 12 天：**10/12 = 83.3%**（不等的是 0901、0902）

| 日期 | fpl | risk_budget | 差 |
|---|---|---|---|
| 20260819 | 0.300 | 0.505 | −0.2050 |
| 20260827 | 0.5112 | 0.666 | −0.1548 |
| 20260828 | 0.5112 | 0.650 | −0.1388 |
| 20260825 | 0.550 | 0.651 | −0.1010 |
| 20260820 | 0.560 | 0.629 | −0.0690 |
| 20260901 | 0.495771 | 0.528 | −0.0322 |
| 20260902 | 0.444500 | 0.470 | −0.0255 |
| 20260806 | 0.6400 | 0.654 | −0.0140 |

`final_position_limit` 只由 `market_regime` + `confirmed_sectors.confidence` 决定，**与 Node 1 无关**
（按当前公式复算 20260814–20260910 全部命中；20260805–20260813 旧归档不匹配，属历史代码版本）。

#### (f) 0903 已独立发现同一结论

`agent_glue_plan_0903.md` L148：「撤销作废。`aggregator.py` 的 `base_position` 全文仅 1 处 = 形参，
**确实是死参数**」。但**处置未定** —— 不在该文档 §6.1 退役清单（L762-765）或 §6.2 保留清单（L767-775）中。

### 2.4 B4 —— `risk_budget` 一名的两值

| | Node 6 `MarketRegime.risk_budget` | Phase 2 `PortfolioRiskAssessment.risk_budget` |
|---|---|---|
| 公式 | `aggregator.py:463-471`，按 regime 分三支，`clip(0.2, 0.8)` | `portfolio_decision.py:260-265`，`0.03 + (x-0.2)*0.05/0.6` |
| 0910 值 | **0.496** | **0.0547** |
| 语义（schema） | `schemas.py:629`「风险预算（决定最大仓位暴露）」 | `schemas.py:925`「风险预算（单笔最大亏损占总资产比例）」 |
| 消费者 | `:3032` `min(position, risk_budget)` **（数值）**；`portfolio_decision.py:263` **（数值，派生）**；`aggregator.py:1832`（T3 prompt 文本）、`:2578`（埋伏提名 prompt 文本）、`:4374`（跨日上下文）、`archive.py:201`（md 展示） | **仅 `:276`（拼自己的 `constraint_reasoning`）+ `:284`（写自己）**；`scripts/run_phase2.py:117`（打印）、`archive.py:610`（写 md） |

**Phase 2 的 `risk_budget` 零数值消费者、零 LLM 消费者** —— 全项目 `rg risk_budget` 已逐条核对。
它是一个**只出现在人读的 md 里的派生幽灵值**，且与真实约束「仓位上限」（＝同一个 Node 6 值的另一种身份）**同名并列**。

0910 读者看到：`reasoning.md:9`「风险预算: **50%**」/ `constraint_reasoning`「仓位上限 50%, …… 风险预算 **5.5%**」。

**推导出的隐含尺寸**：按 `schemas.py:925` 的语义（单笔最大亏损占总资产比例）+ -7% 止损，
`0.0547` 隐含单笔仓位 ≈ `0.0547 / 0.07 ≈ 78%`。**「按风险预算定仓」这套方法在系统里被算出来、印出来，但没有接线。**

### 2.5 B6 —— `mention_count` 三义

| 义 | 位置 | 量纲 | 0910 实测 |
|---|---|---|---|
| Node 5 东财 | `sentiment_analysis.py:790` `int(float(row.get("关注指数",0)) * 1000)` | 关注指数 ×1000 | **93600 ~ 96000** |
| Node 5 NGA | `sentiment_analysis.py:549`（`forum_sentiment.py:49` 定义） | 论坛提及次数 | **数十** |
| Node 4 事件 | `news_analysis.py:693/715` `+= 1` | 跨日累计出现次数 | 1 / 3 / 10 |

**`× 1000` 是量纲伪装**，同一文件对同一列还有另两种处理：

```
sentiment_analysis.py:719   attention_ratio = min(avg_attention / 80, 1.0)   # 注释：「80+ 为极高关注」
sentiment_analysis.py:790   mention_count = int(float(row.get("关注指数",0)) * 1000)
sentiment_analysis.py:850   return int(df["关注指数"].sum())                  # 原样，不缩放
```

`:713` 的注释自证关注指数是 **0~100 量纲**（0910 ≈ 93.6~96.0）。

**真正的碰撞在 Node 5 内部同一次 prompt**：

```
sentiment_analysis.py:1184   f"{s.stock_name}({s.sentiment[0].upper()},{s.mention_count})"   → 远东股份(N,93600)
sentiment_analysis.py:1207   f"{s['name']}({s['mention_count']}提)"                          → 远东股份(13提)
```

同一只股、同一次 LLM 输入、量级差 4 个数量级、其中一个无单位。

**Node 4 与 Node 5 的 `mention_count` 从不共存于同一消费者** —— A6 glue prompt 经 `_glue_strip_event`
（`aggregator.py:690-704`）剥掉了事件字段。但 **glue prompt 也喂了 Node 5 那个值**
（`aggregator.py:752` → `:786`）。

**改动半径**：`tests/test_node6_glue.py:89` 手工构造 `mention_count=100`，**无单位断言**；
`tests/services/test_forum_sentiment.py` 针对 ForumService 另一模型。无测试会拦住量纲修复。

### 2.6 B7 —— 板块分类的两个 LLM 入口（本轮新增）

| 入口 | 位置 | 改什么 | 护栏 |
|---|---|---|---|
| Node 6 板块精炼 `adjustments` | `aggregator.py:1845`（prompt）/ `:1861-1892`（应用） | **`s.classification`**（`:1890`） | 仅允许上调（`:1875`）+ 过热闸门拦截（`:1880-1888`） |
| A6 glue（转正后） | 按 0903 F1-1 `composite_score = Agent strength × 100` | **`composite_score`** | 准入仍由 `_classify_all_sectors` 6 条护栏决定（D5：只换强度不换准入） |

两者**都影响准入**（一个直接改分类，一个改 `composite >= 50` 的闸门），但走**不同字段**。

⚠️ **0903 L765 的表述「adjustments 与 glue 改同一字段」不准确** —— 见 §2.7 撤销第 7 条。

0910 实证：`unified.json: llm_refinement_used = False` → **0910 的 `classification` 是纯规则产出**，
`adjustments` 分支当日未生效（0909/0908 均为 `True`）。

### 2.7 被推翻的结论（公开撤销，防后人重踩）

| # | 曾被主张 | 核查结论 | 依据 |
|---|---|---|---|
| 1 | Node 6 `composite` 是在技术分上加「约 **+3/−5** 量级」的项 | ❌ 实际为 资金 **+10/−15**、消息 **+20/−24**、情绪 **+5/−5**、生命周期 **+8/−15**，区间 max **+38** / min **−59**。+3/−5 是 Phase 2 的数 | `aggregator.py:1282-1302` |
| 2 | `final_position_limit ≡ risk_budget` 为 **23/31 天**；`actionable≥6` 时 **100%** | ❌ 可比 **27 天**（8 天无 `risk_budget`），严格相等 **19/27 = 70.4%**；`actionable≥6` 的 12 天为 **10/12 = 83.3%** | §2.3(e) |
| 3 | B2 的两套不一致清单含「**880430 航空**」 | ❌ 880430 **两套一致**（都是军工）。真冲突是 **880728 航运概念**；14 条不一致中仅 **3 条**是真冲突 | §2.2(c) |
| 4 | B4 是「**一名的两义**」（两个独立定义） | ⚠️ 不准确。Phase 2 的值是 Node 6 值的**线性派生**，且**零消费者** —— 是**派生幽灵**，不是第二套定义 | §2.4 |
| 5 | B6 是「**2 义**」（Node5 vs Node4） | ❌ 实为**三义**；且 Node4 与 Node5 **从不共存于同一消费者**。真碰撞在 **Node 5 内部同一次 prompt** | §2.5 |
| 6 | B1 是「板块强度 **3 套**并存」（各自独立计算） | ⚠️ 不准确。核心缺陷是**同一组信号在 Node 6 与 Phase 2 被消费两次**（读的是同一个 `SectorIntel` 字段），而非三套独立公式 | §2.1(b) |
| 7 | 0903 L765：「`adjustments` 与 glue **改同一字段**」 | ❌ `adjustments` 改 `classification`（`:1890`），glue 转正后改 `composite_score`。两者都影响准入但字段不同 | §2.6 |
| 8 | 本文档 A 层版本的 §7.1 中，B 层编号为 B1/B2/B3/B4/**B6** | ⚠️ **跳过了 B5**（原编号缺失）。本文档重排为 B1–B7 连续编号 | 本文 §2.6 将原「双 LLM 入口」定名 **B7** |

### 2.8 已核查、判定「**不需修 / 勿误伤**」（防误修清单）

| 项 | 为什么不该动 |
|---|---|
| **「其他」占 43.8%** | 0903 D3-b（L568-570）明确「**把「其他」从护栏输入里摘掉，而不是把 95 个板块重新分类**」。**收缩「其他」= 主动废掉 A6 的用武之地** |
| **A6 `families` 零消费者** | 影子模式设计（`SHADOW=true`），非缺陷 |
| **`ThemeFamily.family_name`** | 相关性聚类产出，用途与 `frame_group` 不同（Node 2 自身 prompt + 资金错位检测），非重复 |
| **`MainlineEndurance.family_resonance`** | 活代码（`aggregator.py:2996-3003`）。⚠️ 与 `SectorIntel.family_resonance`（0903 实测 24/24 全 `None`，待删）**是两个字段，勿混** |
| **`llm_refinement_used`** | 0903 F1-7 说它硬编码 `True` —— **已接线**（`aggregator.py:338` = `_llm_refine_succeeded`），0910=False / 0909=True / 0908=True |
| **`data_quality.missing_fields`** | 0903 F1-8 说它恒空 —— **已接线**（0910 有 2 项，0908 含 `agent_semantic_glue`） |
| **`industry_top` / `group_score` / `family_score`** | 节点内部聚合口径，各有独立用途，非同口径重复 |
| **`_divergent_threshold` 的 `min(分位, -80)`** | 属 D 层（与 0902 报告对账），不在 B 层射程 |
| **`portfolio_decision.py` 的 `base_position`** | ⚠️ **与 `aggregator.py` 的同名死参数是两个东西，勿混**。`_apply_drawdown_scaling(current_drawdown, base_position)`（`:332-336`）在 `:366` 做 `scaled = base_position * multiplier`，由 `_assess_risk`（`:192`，Phase 2 Step 1）调用 —— **回撤保护层，活代码**。**为凑 R-B5 指标而删它 = 删掉三层仓位里的回撤保护**；`scripts/verify_position_sizing.py:104/115` 的同名副本同理 |

---

## 3. 根因

**三类，缺一不可**：

1. **信号跨节点被重复消费**（B1 / B7）。`SectorIntel` 把 `capital_check` / `news_check` / `sentiment_check`
   作为**已判定的结论**向下传递，但下游（Phase 2）把它当作**原始证据**又判了一次。
   → 根因是「**结论与证据没有分界**」：同一个字段既是被 Node 6 消费过的结论，又被 Phase 2 当输入。
2. **派生值与它的输入同名**（B4 / B3）。`risk_budget` 派生后仍叫 `risk_budget`；
   `base_position` 是被丢弃的输入却留在签名里。→ 根因是「**派生没有命名隔离**」。
3. **量纲在传递中丢失**（B6）。`关注指数`（0~100）→ `×1000` → 无单位的整数 → 与 `N 提` 并列进同一 prompt。
   → 根因是「**量纲不是类型的一部分**」，只能靠注释与命名承载，而两者都已失真。

`min(·, risk_budget)` 与 `clip(0, 100)` 这类下游巧合会掩盖 1 的后果，使问题在"最终仓位数字"上不可见。

---

## 4. 已确认决策（本轮不再重议）

| # | 决策 | 用户原话/确认 |
|---|---|---|
| **D-B1** | B1：**只标注口径 + 记下重复计数**，不改任何数值 | 「只标注口径+记下重复计数（推荐）」 |
| **D-B2** | B2：**定「只判落『其他』的」范围** + **修规则侧词表**（词表同源 + 修正已知错分）。接受家族拥挤度降权的行为变更 | 「只判「其他」+ 修规则侧词表（推荐）」 |
| **D-B3** | B3：**只清死参数 + 修注释**，不接线波动率调整 | 「只清死参数+修注释（推荐）」 |
| **D-B4** | B4：**改文案消歧义**，字段与数值不动 | 「改文案消歧义（推荐）」 |
| **D-B6** | B6：**只改 prompt 自述单位**（存储值与字段名不动） | 「只改 prompt 自述单位（推荐）」 |
| **D-B7** | B7：**待定**（本节未决，见 §7.2 OPEN-B1） | — |

**贯穿约束**（承接 A 层）：本轮**不改任何阈值/判据**；行为变更仅限 D-B2 与 D-B6 两项，且均已在 §6 列出可计数验收。

---

## 5. 修复清单

### 5.1 B1 —— 口径标注 + 重复计数留痕（**零行为变更**）

- **位置**：`aggregator.py:719-720`、`aggregator.py:1810`、`portfolio_decision.py:756-765`（docstring）、
  `archive.py` / `stock_screener.py:2715-2717`（展示）
- **改法**：
  1. `aggregator.py:719-720`（A6 glue prompt）标签改为 `strength_raw=`（口径 = Node 2 原始技术强度）
  2. `aggregator.py:1810`（Node 6 精炼 prompt）标签改为 `composite=`（口径 = 综合分）
  3. `portfolio_decision.py:_adjust_score_by_sector_intel` docstring 增补一段：
     **「`capital_check` / `news_check` / `sentiment_check` 已在 Node 6 `_build_sector_intel_from_scores`
     计过一次分（`aggregator.py:1282-1294`）。本函数的对应加减项是第二次计入，幅度不同。
     ⚠️ 若将来统一为一次，必须同时调整 `composite_score` 的 clip 区间。」**
  4. `stock_screener.py:2715-2717` 展示值旁标注「（Node 2 原始分，非计权值）」
- **不做**：不改任何加减项、不改 base 来源、不收窄 clip。
- **理由（D-B1）**：重复计数是否为缺陷属**判据判断**，本轮不动；但"读者以为展示值就是计权值"是纯信息损失，必须消除。

### 5.2 B2 —— 词表同源 + 修正已知错分（**行为变更**）

- **位置**：`sector_analysis.py:168-267`（`FAMILY_KEYWORDS`）、`schemas.py:1033-1036`（`CONTROLLED_FAMILIES`）
- **改法**：
  1. **词表同源（DRY）**：把 `FAMILY_KEYWORDS` **下沉到独立常量模块**（新建 `src/got/family_keywords.py`），
     该模块同时定义 `CONTROLLED_FAMILIES = [name for name, _ in FAMILY_KEYWORDS]`；
     `schemas.py` 与 `nodes/sector_analysis.py` 均**从该模块 import**（`sector_analysis.py` 本就位于
     `schemas.py` 下游，方向不变）。
     ⚠️ **不得**在 `schemas.py` 内写 `CONTROLLED_FAMILIES = [name for name, _ in FAMILY_KEYWORDS]`：
     `schemas.py:10-12` 的 import 只有 `datetime` / `typing` / `pydantic` 三行、**零项目内依赖**，
     而 `sector_analysis.py:38` 反向 import `schemas` —— **照原主方案写必然 ImportError**（原文档把
     唯一可行路径写成了条件句，此处对调）。
     ⚠️ `无归属` **不进** `CONTROLLED_FAMILIES`：它只存在于 `aggregator.py:853` 的运行时并集
     `set(CONTROLLED_FAMILIES) | {"无归属"}`，入库词表内没有它（原文档「保留 `无归属` 作为附加项」
     的措辞是 R-B2 达标线矛盾的来源，已删）。
     ⚠️ **改动半径**（`tests/test_node6_glue.py` **共 4 处**，勿只改断言值）：
     ① `:106` `assertEqual(len(CONTROLLED_FAMILIES), 12)` → `14`；
     ② `:104` 测试方法名 `test_twelve_plus_one` → `test_fourteen_plus_one`（否则名实不符）；
     ③ `:105` docstring「12 个真实家族 + `'无归属'` = 13 个合法值」→「14 + 1 = **15**」；
     ④ `:10` 模块 docstring「受控词表（CONTROLLED_FAMILIES 12 + 1）」→「14 + 1」。
     ⚠️ **这 4 处属 B2 实施时同批修改，本轮不预改测试代码** ——
     当前 `CONTROLLED_FAMILIES` 仍是 12 族（`schemas.py:1033-1036`），**先改测试会立即红**。
     ℹ️ 另：`:105` 揭示测试口径把 `无归属` 当**第 13 个合法值**，派生后它成为**第 15 个**。
     它**不在 R-B2 的覆盖范围内**（R-B2 只验 `set(CONTROLLED_FAMILIES) == FAMILY_KEYWORDS` 族名集），
     而是由 `aggregator.py:853` 的运行时并集 `set(CONTROLLED_FAMILIES) | {"无归属"}` 单独保证
     —— 属**红线覆盖面缺口**，非错误；若待实施时要补，建议在 R-B2 后加一句断言而非新开红线。
     ℹ️ `sector_analysis.py:270` 已有 `_FAMILY_NAMES = {name for name, _ in FAMILY_KEYWORDS}`
     （当前零消费者）——正是本项要的派生模式，可直接复用/迁移，不必新写。
  2. **修正已知错分**（`FAMILY_KEYWORDS` 内容修正，**不改家族顺序**；人工期望以 §2.2(b)3 表为准）：
     - 军工族移除 `"航运"`（`:245`）→ `航运概念` 归 `周期资源`
     - **显示簇整簇同移**：`OLED`、`MiniLED`、`折叠屏`（`:180`）从 AI软件 移到 AI硬件
       （面板/display 归硬件链）。⚠️ **只移 `OLED` 会把显示簇劈成两半** —— AI软件族另有
       `消费电子` / `混合现实`，AI硬件族只有 `玻璃基板`；同簇词必须同批同移，否则与本项自述判据矛盾。
       ⚠️ `AI硬件` 是**声明序 0（首个命中胜出）**，新增词会优先吞占任何含该子串的板块名，
       须按改法 3 做夺占检测。
     - `"光刻机"` 从 AI硬件 的「半导体上游」组中移出（→ 独立或设备子集）；
       `玻璃基板` **保留** AI硬件（属面板材料）
     - ~~金融族补 `"保险"`~~ —— **删除此项**：`:233` 已含 `"保险"`，补是 no-op。
       「是否新建独立 `保险` / `非银金融` 族」是**粒度决策**，不在本轮射程（要做则另立专项）
     - `航空`：0903 L587 标注其当日 `role=ambush`，属民航 —— 需人工确认归属（候选：新建 `运输` 族）；
       **未确认则本轮不动**（不计入 R-B4）
  3. **《`_build_frame_group` 的顺序敏感性》**：`sector_analysis.py:205-214` **已有**夺占检测注释
     （含「农业族必须早于医药族……`转基因` 被误归医药（登海种业/苏垦农发实测）」与「已做夺占检测」）。
     本项**不是新写**，而是把这段**分散注释提升为文件级约定**，并把现成案例
     （农业 vs 医药）直接固化为回归用例。
- **风险**：改 `frame_group` → 改 `crowded_families` → 改 Phase 2 家族拥挤度降权（`:838-852`）。
  **须在 §6 R-B3 用同期归档回放对比降权触发次数**。
- **理由（D-B2）**：范围定义「只判落『其他』的」（0903 §5.3.3 L555）与「已知错分组 Agent 应能纠正」
  （L579-588）矛盾，**选前者则规则侧词表是唯一修复途径**。同时消除 14/12/13 三处不一致。

### 5.3 B3 —— 清死参数 + 修注释（**零行为变更**）

- **位置**：`aggregator.py:2985-2989`（签名）、`aggregator.py:260`（调用点）、
  `aggregator.py:263-265`（注释）、`schemas.py:152/156`（`vol_adjusted_position` / `regime_scale` 的 description）
- **改法**：
  1. 删除 `_compute_position_from_regime` 的 `base_position` 形参；调用点**共 7 处**须同步去掉第三个实参：
     生产 `aggregator.py:260` + 测试 `tests/test_node6_regime_state.py:237/238/250/251/263/264`
     （**漏改测试 ⇒ 删形参后 6 处 `TypeError`**）
     ⚠️ **只清 `aggregator.py` 的死参数**：`portfolio_decision.py:332-336` 的
     `_apply_drawdown_scaling(…, base_position)` 是**活代码**（回撤保护层，`:366` `base_position * multiplier`，
     由 `:192` `_assess_risk` 调用），**禁止一并删除** —— 见 §2.8 防误修清单
  2. 修正 `:263-265` 注释：「三层」→ 实际只有两层（状态缩放 + 回撤保护）；
     **波动率调整（Node 1 `position_limit`）当前未接入 `final_position_limit`**
  3. `schemas.py:152/156` 的两个字段 description 补「**参考值，当前不参与 `final_position_limit`**」
- **不做**：不接线（接线属行为变更，见 §7.2 OPEN-B2）；不删 schema 字段（保归档可比）。
- **理由（D-B3）**：`base_position` 是 0903 L148 已确认的死参数但**处置未定**；一个被删除的安全限幅
  若以"不存在的层"为理由，必须在注释里明确标出，否则下一个读代码的人会重蹈覆辙。

### 5.4 B4 —— 改文案消歧义（**零行为变更**）

- **位置**：`aggregator.py:201`（日志，英文）、`archive.py:201`（md）、
  `portfolio_decision.py:267-277`（`constraint_reasoning`）
- **改法**：
  1. `archive.py:201`：`- **风险预算:** {regime.risk_budget:.0%}`
     → `- **仓位暴露上限:** {regime.risk_budget:.0%}`（该值经 `aggregator.py:3032` 直接作为仓位硬上限）
  2. `portfolio_decision.py:276`：`风险预算 {risk_budget:.1%}`
     → `单笔风险预算 {risk_budget:.1%}（派生展示值，当前未接线）`
  3. `schemas.py:925` description 补「**派生自 `MarketRegime.risk_budget`，当前无消费者**」
- **不做**：不删字段（保归档 schema）、不改数值、不接线（见 §7.2 OPEN-B3）。
- **理由（D-B4）**：零消费者 + 与真实约束同名并列 = 纯误导；改文案是最低成本的消除方式。

### 5.5 B6 —— prompt 自述单位（**行为变更：LLM 输入变化**）

- **位置**：`sentiment_analysis.py:1184`（Node 5 自身 prompt）、`aggregator.py:752/786`（A6 glue prompt）、
  `sentiment_analysis.py:790`（写入侧加注释）
- **改法**：
  1. `sentiment_analysis.py:1184`：`f"{s.stock_name}({s.sentiment[0].upper()},{s.mention_count})"`
     → `f"{s.stock_name}({s.sentiment[0].upper()},东财关注指数{s.mention_count/1000:.1f})"`
  2. `sentiment_analysis.py:1207`：`f"{s['name']}({s['mention_count']}提)"`
     → 保持但补前缀，如 `NGA提及{s['mention_count']}`
  3. prompt 头部的图例同步（`:1233` 的 `热门股（P=正面/N=负面/N=中性）` 中 `N` 重复，一并修正为 `P/N/中`）
  4. `sentiment_analysis.py:790` 加注释说明为何 `×1000`（若确系历史遗留，标注"存疑，待 §7.2 OPEN-B4 裁定"）
  5. **A6 glue prompt 同步**（`aggregator.py:786`）——**只改 Node 5 不够**，影子侧也喂同一字段
- **不做**：不改存储值、不改字段名、不动 `:719` 的 `/80` 与 `:850` 的 `sum()`（各自用途不同）。
- **理由（D-B6）**：读者是 LLM，量纲伪装会污染 `hot_topics` 推断；prompt 层修复是最小且可归因的。

### 5.6 B7 —— 待定（§7.2 OPEN-B1）

---

## 6. 验收红线（可计数）

| # | 指标 | 达标线 | 数据源 | 可度量 |
|---|---|---|---|---|
| **R-B1** | 口径标注覆盖率：§5.1 的 4 处标注全部落地 | 4/4 | 代码 grep | ✅ |
| **R-B2** | 词表同源：`set(CONTROLLED_FAMILIES) == {name for name, _ in FAMILY_KEYWORDS}`（**集合相等，不是子集**） | 14 == 14；修复前 12 ≠ 14 → FAIL | 单测断言 | ✅ |
| **R-B3** | 家族降权影响面：同一期归档回放，`crowded_families` 非空天数与 `家族拥挤降权` 触发次数变化 | **有界**（给出前后对比表，无单日增幅 > 100%） | 归档回放 | ✅ |
| **R-B4** | 已知错分修正：`航运概念`→`周期资源`、`OLED概念` / `MiniLED` / `折叠屏`→`AI硬件` 的 `_build_frame_group` 返回值 | 与 §2.2(b)3 表「本轮人工期望」列**逐字一致** | 单测 | ✅ |
| **R-B5** | 死参数清除：`_compute_position_from_regime` 形参数 = 2，且 **`src/got/nodes/aggregator.py` 内** `base_position` 0 命中 | 0 命中（**限定文件，非全项目**） | `rg base_position src/got/nodes/aggregator.py` | ✅ |
| **R-B6** | 文案消歧义：两份 md 中「风险预算」出现次数 = 1（仅 Phase 2 侧且带「派生展示值」标注） | 1 | `reasoning.md` / `trading_recommendations.md` | ✅ |
| **R-B7** | prompt 单位自述：`东财关注指数` 与 `NGA提及` 两个前缀在 Node 5 prompt 与 glue prompt 中各出现 ≥1 次 | ≥1 各 | 代码 grep | ✅ |
| **R-B8** | 值不变性（零行为变更项）：B1/B3/B4 修复前后，同期 `unified.json` 的 `final_position_limit` / `composite_score` / `confirmed_sectors` 逐字段一致 | 100% 一致 | 归档 diff | ✅ |
| **R-B9** | 行为变更留痕（B2/B6）：同期归档的 `classification` 与 `dimension_scores["sector"]` 变化逐条列出 | 有清单 | 归档 diff | ✅ |

⚠️ **R-B2 的方向陷阱（2026-09-11 修订）**：原指标列写 `⊆`、达标线写「相等」——**两者方向不一致，且 `⊆` 方向是空红线**（实测 `CF=12 ⊆ FK=14` 修复前已 `True`，按 `⊆` 验证则修复前后都 PASS，不检验任何东西）。已统一为**集合相等**。
⚠️ **R-B5 的作用域陷阱（2026-09-11 修订）**：原达标线写「全项目无 `base_position` 引用」——**会诱导误删活代码**（`portfolio_decision.py` 的回撤保护层）。已限定到 `aggregator.py`，并在 §2.8 登记白名单。

### 6.2 R-B8 的字段清单（**不是全文件 diff**）

⚠️ 同 A 层 §6.2：`data_quality.timestamp`、`meta.*`、`reasoning` 等天然变化，**只比状态字段**：

```
unified.json:        final_position_limit, market_regime.*, confirmed_sectors[].{composite_score, classification, frame_group, confidence}
sector.json:         frame_group_lookup（B2 后有预期变化，须逐条列出）
portfolio_decision.json: risk_assessment.{position_limit, sector_exposure_limits}, unified_scoring[].dimension_scores
两份 md:             展示文案（B4 后有预期变化）
```

### 6.3 验收手具

B 层**不需要**同日两跑（那是 A 层的验收形态）。B 层用**同期归档回放**：
以 0910（或最近一个完整日）的 6 个节点 JSON 为输入，跑 Node 6 + Phase 2，与既有归档 diff。
建议并入 A 层的 `scripts/verify_same_day_idempotency.py` 所在目录，新增
`scripts/verify_layer_b_consistency.py`，按 §6.2 清单输出 R-B1…R-B9 的逐项 PASS/FAIL。

---

## 7. 范围外 / 挂起项

### 7.1 范围外（本轮不做）

| 层 | 项 | 证据位置 |
|---|---|---|
| C | **C 层（影子验收设计）已独立成文** → [`got_layer_c_plan_0911.md`](got_layer_c_plan_0911.md)：C1 转正门禁**自锁**（M1/M6 读规则侧字段，影子期恒定不动）、C3 **M4 三条红线共线**（实测 `ρ(tech_score, composite_score) = 0.985` ⇒ 三条只剩 1 个自由度）、C6 M2 红线与 prompt 指令**方向相反**、C7 `transition_count` 跨方法学断点 + **≈2026-09-25 时间炸弹**。⚠️ 原表述「M1/M6 结构上不可改善 / 无 `kind` 字段 / L466 对照不公平」均被修订，**六条转正红线实测 0/6 达标**（2 条门禁自锁 + 2 条不达标 + 1 条不可度量 + 1 条未测） | `docs/got_layer_c_plan_0911.md` |
| D | **D 层（与 0902 报告对账）已独立成文** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md)：15 项实测（生效 3 / 未修 10 / 症状自行消失 1 / 不修 1），**紧急项完成率 1/6**。⚠️ **P0-2 已确证归入本层 B1** —— `confidence` 是 `capital_check`/`news_check`/`sentiment_check` 三态的纯函数（0910 `(neutral,neutral,low)→0.7` 占 30/32），三态近空正是 B1；**修 B1 自动修 P0-2**，0902 报告原修法（提基础分）是给下游投影打补丁；另 `_divergent_threshold` 病因表述已被修订 | `docs/got_layer_d_plan_0911.md` |
| 0903 | 0903 §6.1 未实施的退役项 17 条（含 `SectorIntel.family_resonance` 24/24 全 None、`score>=50`/`composite<40` 阈值） | `agent_glue_plan_0903.md` §6.1/§6.2 |
| E | **E 层（其他具体缺陷）已收口（2026-09-11）** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md) **附录 C**。12 项全部实测：**4 项被吸收/推翻**（E1 / E7 / E8 / E9），**7 项分属三种形态**（含 **E2 与 E6**，归属本层口径），**E12 不处置**（4+7+1=12）。⚠️ **E2 与 E6 归属本层（口径）**：E2 的 `news=40.0` 把「LLM 未映射」与「判中性」压成同一个值、E6 的 `catalyst_score` 与 `ambush_score_v2` 是两个不可比的 0–1 —— 均按决策 E-5「加口径字段、不改数值」处理。⚠️ **E8 碰撞与 0902 报告 P1-2 同族**（板块名匹配）：真碰撞是 `旅游概念 → 旅游/旅游景点/旅游服务`、`种植业与林业 → 林业/种植业` | `docs/got_layer_d_plan_0911.md` 附录 C |
| A | 运行模型（同日重跑幂等 + 末版基线） | `got_run_model_plan_0911.md` |

### 7.2 OPEN

| # | 项 | 待决 | 建议默认 |
|---|---|---|---|
| **OPEN-B1** | **B7 双 LLM 入口**：`adjustments` 改 `classification`、glue 转正后改 `composite_score`，两者都影响准入。0903 L765 要求删 `adjustments` 分支，但未实施 | 删除、保留、还是限定权限？ | **保留**（现状已有「仅上调 + 过热闸门」双重护栏），在 0903 文档里**修正 L765 的字段描述**，转正时一并处理 |
| **OPEN-B2** | **波动率调整接线**：`final_position_limit` 是否应消费 Node 1 的 `position_limit`？ | 接线（0910 的 0.496 → 0.365）还是不接？ | **不接**（属判据变更，需独立回测）；仅在注释与 schema description 标注「未接线」 |
| **OPEN-B3** | **单笔风险预算接线**：`risk_budget = 0.0547` 隐含单笔仓位 ≈ 78%，是否应真正约束单笔仓位？ | 接线与否 | **不接**（同上）；标注「派生展示值，未接线」 |
| **OPEN-B4** | **`关注指数 ×1000` 的来历**：为何 ×1000？ | 是历史遗留还是某消费者依赖？ | 先查 git 历史；若确系遗留，**考虑下一步改为 `attention_index` 存原值**（跨归档可比性问题需一并处理） |
| **OPEN-B5** | **A 层缺口回填**：a803e09 给归档清理加了 `preserved_files = {"agent_glue.json"}`，同日重跑时上一轮 glue 输出被保留，与 A 层 D-1「末版为准」冲突 | 保留还是覆盖？ | **覆盖**（末版为准）；已在 **A-P0-8-a** 落地（含修正后的触发条件与 C-P1/C-P2 同批约束），见 §7.3 |
| **OPEN-B6** | **`航空` 的家族归属**：现落 `军工`（`:245` 军工族含 `"航空"`），0903 L587 标注其当日 `role=ambush`、属**民航** | 归 `军工`（不动）、新建 `运输` 族、还是并入 `消费`？ | **本轮不动、不计入 R-B4**（避免在数据源缺失时改判据）；归属由你确认后另立专项 |
| **OPEN-B7** | **`保险` 是否独立成族**：`:233` 金融族已含 `"保险"`，现与 `银行` 同族 | 粒度要不要拆（新建 `保险` / `非银金融` 族）？ | **不拆**（属粒度决策、非错分；拆族会改族数 14→15，牵连 R-B2 与家族降权回放） |

### 7.3 对 A 层计划的修订请求

**`docs/got_run_model_plan_0911.md` A-P0-8 需补一条**：

> **同日重跑时 `agent_glue.json` 应被覆盖，而非保留。** 现状 `archive.py:88-105` 的
> `preserved_files = {"agent_glue.json"}`（a803e09 新增）会保留上一轮的影子输出，
> 使归档里的 `agent_glue.json` 可能来自**当天更早的运行**，与 D-1「**末版为准**」直接冲突。
> A-P0-8 的 tmp 方案须明确：`agent_glue.json` 与 `_replay/` 的语义不同 ——
> `_replay/` 是旁路产物可保留，**`agent_glue.json` 是当日影子结论，必须随本轮覆盖**。
>
> ⚠️ **已按 A-P0-8-a 修正两处**（2026-09-11 回写）：
> 1. **触发条件比本项写的窄**。`_save_glue_result`（`aggregator.py:1086`）的落盘路径**就是归档目录本身**，
>    所以同一次运行内 `preserved_files` 只是"别删掉本轮刚写的"。只有**本轮走了 6 条不落盘 `return` 之一
>    （`:962 / :967 / :970 / :988 / :993 / :1003`，见 C 层 C-P1）**时，目标目录里才残留上一轮的。0910 实测同轮（`+6s`），**未观察到污染**。
> 2. **tmp 方案有一个会丢数据的坑**：tmp 目录里没有 glue ⇒ 直接 swap 会让影子样本**彻底消失**。
>    正解是"替换前把目标目录的 glue 搬进 tmp"，而这**依赖 C 层 C-P1 已落地**，否则搬进来的正是上一轮。
>    ⇒ 落地约束见 `got_run_model_plan_0911.md` §5.1 A-P0-8-a（C-P1/C-P2 必须同批，`_replay/` 必须搬运）。

**B1 的修复状态是 C 层 M4 红线的前提（新增，来自 C 层 §2.3 / §7.3 第 8 项）**：

> **B1 不修，则 M4-a 与 M4-c 是同一个检验。** 实测 0910：`ρ(tech_score, composite_score) = 0.985`
> （Spearman 0.965），`mean|composite − tech| = 2.56 分`（`tech_score` 标准差 25.3）。
> 根因是 B1 描述的四维修正**几乎全空转**——`sentiment_check` 32/32 全 low、
> `capital_check` 31/32 neutral、`news_check` 31/32 neutral ⇒ `composite` 退化为 `tech_score` 的
> 近似单调变换 ⇒ M4-a 与 M4-c 数学上测同一个量。
> 0903 §8.1 声称 M4-b/M4-c 构成「必须改变，但不能乱改」的**双向约束**，实测**退化为一条红线**
> （0910 出现「M4-a 不过 / M4-c 过」的分裂，即退化的直接证据）。
> **故 C 层把 M4-a 的红线数值标为「待标定」，并要求 0903 §8.1 增注「M4-a 的独立性以 B1 修复为条件」。**
> B1 修复（提高 `news_check`/`capital_check` 的实际覆盖率）会让两个对照量分离，M4 才恢复为两条独立红线。

---

## 附录 A：0910 B 层污染一览（修复前基线）

| 量 | 0910 实际 | 语义应然 |
|---|---|---|
| `reasoning.md` 粮食概念展示强度 | `84` | 计权值 `88.8`（展示与计权不同源） |
| Phase 2 `dimension_scores["sector"]`（粮食） | `88.8` | = base(83.8) + 角色 +10 + 情绪 −5，其中资金/消息已在 Node 6 计过 |
| ambush 板块情绪惩罚 | Node 6 豁免、Phase 2 **补罚 −5** | 两处应一致 |
| `frame_group_lookup` 中「其他」占比 | `95/217 = 43.8%` | 设计上应由 A6 处理，非规则侧收缩 |
| `航运概念` frame_group | `军工` | 非军工（关键词表误置） |
| `unified.final_position_limit` | `0.496` | Node 1 波动率调整给出 `0.365`（被丢弃） |
| Node 1 `vol_adjusted_position` / `regime_scale` | 有值 | 零消费者 |
| `reasoning.md` / `trading_recommendations.md` 「风险预算」 | `50%` 与 `5.5%` 并列 | 后者零消费者且语义为「单笔最大亏损占比」 |
| Node 5 prompt 中同一只股 | `(N,93600)` 与 `(13提)` | 前者是 `关注指数×1000` 的量纲伪装 |
| `llm_refinement_used` | `False` | `adjustments` 分支当日未生效（0909/0908 为 `True`） |

## 附录 B：本文事实核查约定

沿用 `got_run_model_plan_0911.md` 附录 B：所有代码断言带文件路径 + 行号，均为
**HEAD `a803e09` + 2026-09-11 工作区快照**的只读核查结果；若实施时代码已变动，**以代码为准，不以本文为准**。
§2.7 记录本轮被推翻的 8 条错误主张，§2.8 记录已核查但**判定不需修**的 9 项（防误修），
§7.1 记录已核查但不在本轮射程的 C/D/E 层问题。
