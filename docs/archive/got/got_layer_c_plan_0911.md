# GOT C 层修复方案：影子验收设计的可度量性（C 层）

> **定位**：C 层 = **影子模式的验收设计**。本轮只回答一个问题：**0903 §8.1 的六条转正红线，
> 到底能不能被度量、被满足？** 答案是：**六条没有一条达标（0/6）** ——
> 其中 **2 条门禁自锁**（M1/M6：转正条件依赖一个「转正之后才会改变的字段」，结构上不可动）、
> **2 条实测不达标**（M2 四日 13.6–28.1% vs 红线 70%；M4-a 0.591 vs 0.6）、
> **1 条当前不可度量**（M5，依赖 `call_type` 落库）、**1 条未测**（M3，依赖人工抽查）。2+2+1+1 = 6。
>
> **不在本轮射程**：A 层（运行模型）、B 层（概念一致性）、D 层（与 0902 报告对账）、E 层。见 §7.1。
>
> **遵循既有工程约定**（与 `got_run_model_plan_0911.md` / `got_layer_b_plan_0911.md` 同构）：
> 事实核查先行（§2）、**被推翻的错误结论公开撤销**（§2.9）、已确认决策链（§4）、
> **可计数的强制验收红线**（§6）、范围外与挂起项显式列出（§7）。
>
> **状态：待实施（用户已确认全部 7 条决策 D-C1…D-C7）**。创建日期：2026-09-11。
> 事实核查基准：HEAD `a803e09` + 工作区快照（2026-09-11）。实测数据源：
> `data/got_daily/2026*/agent_glue.json`（4 份）+ `unified.json`（11 份）+ `logs/stock_analysis_20260908.log`。
>
> **修订台账**：[`got_revision_ledger_0911.md`](got_revision_ledger_0911.md) ——
> 本文的「2026-09-11 修订」注已汇总在该台账（16 条外部质询 + 13 处同源传播 + 11 处复核漏网 + 3 处自曝）。
>
> **实施审核（2026-09-11）**：[`got_impl_review_0911.md`](got_impl_review_0911.md) —— 本轮实施的审核结论（**2 个 P0** / 8 个 P1 / 红线达标总表 / 承诺脚本缺失清单）。

---

## 0. 结论摘要

C 层的病不是「红线不够严」，是**红线从未被实测**。0903 §8.1 的六条指标，**基线列与红线列测的不是同一个量**：

- M1/M6 的**基线**取自规则侧字段，**红线**也写在同一个规则侧字段上——而影子期 glue「接受但不使用」
  （`aggregator.py:1119`），该字段不因 glue 而变 ⇒ **M1 恒 3.1%、M6 恒 65.6%，红线永不达成**。这是**死锁**，不是「指标偏严」。
- M2/M4 的红线写在**只有 glues 能产出的量**上，但实测值域远低于红线（M2 四日 13.6%–28.1% vs 红线 70%；
  M4-a 0.591 vs 红线 0.6），且 M4 三条子指标因规则侧退化而**共线**（`ρ(tech, composite) = 0.985`）。
- M5 依赖 `call_type` 落库，**当前不可度量**。
- M3 依赖人工抽查，本轮无法验证。

**修复目标（一句话）**：

> 六条红线每一条都要能被一个脚本在当天复算出来，且其基线、对照量、数据类型都要与红线同源。

达成路径：**先修可观测性（拿到干净样本）→ 再按实测标定红线**（D-C1）。
本轮的**行为变更项为零**——全部改动落在「落盘 / 指标定义 / 脚本 / 文档」，唯一例外是 §5 的 P4 实验组（回填跑批 + prompt A/B），
它们只在影子路径上运行，不改变生产输出。

**范围（用户已确认，§4）**：7 条决策 D-C1…D-C7 覆盖 S1/S2 落盘、数据源修正、三处指标定义、
回放日期 bug、判据可观测化、prompt A/B、影子对照删除。

---

## 1. 触发：六条红线的首次实测（0910 + 四日）

> 这些数字不是估算。全部由 `agent_glue.json` 的 `strengths[]/families[]/relations[]` 与
> `unified.json` 的 `confirmed_sectors[]` 逐板块 join 后计算，脚本逻辑见 §6.3。

| 红线 | 0903 定义 / 数据源 | 0910 实测 | 四日实测 | 判定 |
|---|---|---|---|---|
| **M1** ≥60% | `unified.json` `confirmed_sectors[].news_check != "neutral"` | **1/32 = 3.1%** | — | 🔴 **影子期不可动（死锁）** |
| M1′ （glue 侧对照） | `agent_glue.json` `strengths[].news_read` 非 low/unknown | **11/32 = 34.4%** | — | 🔴 实测 < 红线 |
| **M2** ≥70% | ≥1 条 relation 的候选占比 | **9/32 = 28.1%** | 21.1% / 13.6% / 21.9% / 28.1% | 🔴 **4/4 不达标** |
| **M3** ≤15% | 人工抽查误配率 | — | — | ⚠️ 未测 |
| **M4-a** ≥0.6 | `ρ(glue.strength, tech_score)` | **0.591 P / 0.419 S** | — | 🔴 不达标且 n=32 不可判定 |
| **M4-b** <1.0 | `ρ(glue.strength, 旧 composite)` | 0.646 P / 0.500 S | — | ✅ |
| **M4-c** ≥0.3 | `ρ(glue.strength, 旧 composite)` | 0.646 P / 0.500 S | — | ✅ |
| **M5** ≤10% | 触发 S1–S5 任一场景的交易日占比 | — | — | 🔴 **当前不可度量** |
| **M6** ≥80% | `unified.json` `confirmed_sectors[].frame_group != "其他"` | **21/32 = 65.6%** | — | 🔴 **影子期不可动（死锁）** |
| M6′ （glue 侧对照） | `families[]` 非「无归属」占比 | **31/32 = 96.9%** | — | ✅（但分子被污染，见 §2.2） |

**事件侧同样低**：glue 覆盖的 `event_id` 占归档 `catalyst_events` 的
**30.0% / 30.0% / 37.8% / 46.7%**（0904/0907/0909/0910）。

**影子期实际进度**：`agent_glue.json` 仅存在于 **4 天**（0904/0907/0909/0910），**0908 缺失**，
而 §8.2 要求「≥10 个交易日」+「M2/M3 连续 5 日达标」。**0908 的缺失原因在 §2.5 有实证。**

`★ Insight ─────────────────────────────────────`
**0903 §8.1 的「0903 基线」列与「转正红线」列，测的不是同一个量。** M1 的基线 `0/24` 取自
规则侧 `news_check`，红线 `≥60%` 也写在同一个规则侧字段上——作者想表达的是「转正后这个字段应该到 60%」，
但把它写成了**转正的前置条件**。于是出现一个自指的门禁：**要通过门禁，必须先通过门禁。**
这类「条件是自己的结果」的形态，在验收设计里比「阈值偏严」隐蔽得多——阈值偏严会被实测打回，
自指门禁只会**永远停在原地**，看起来像「影子期还没跑够」。
`─────────────────────────────────────────────────`

---

## 2. 事实核查

> 约定同 B 层：所有断言带文件路径 + 行号，均为 HEAD `a803e09` 的只读核查；实施时以代码为准。

### 2.1 C1 — 转正门禁自锁（M1/M6 数据源错）

**机制**：glue 在影子期是「只归档、不生效」，这一点在代码里有明确契约：

```python
# src/got/nodes/aggregator.py:1119
#   glue: Agent 语义粘合层输出（A4 阶段接受但暂不使用，A6 正式消费）。

# src/got/nodes/aggregator.py:215-217
#   A4 阶段：调用 + 归档，glue 结果暂不影响 _build_sector_intel 行为
glue = await self._agent_semantic_glue(
    candidates_for_glue, news, capital, sentiment, regime,
)
```

而 M1/M6 读的两个字段，在影子期**只由规则侧写**：

| 字段 | 写入点 | 读方消费点 |
|---|---|---|
| `news_check` | `aggregator.py:1272` `_cross_check_news(name, news_keywords)` | `portfolio_decision.py:797-801` |
| `frame_group` | `aggregator.py:1352-1359`（`detail.frame_group` → `frame_group_lookup.get(name)`） | `aggregator.py:3473` `_synthesize_mainline_endurance` |

⇒ 影子期内 M1 ≡ 3.1%、M6 ≡ 65.6%，**与 glue 质量无关**。
§8.2「M1/M4/M6（自动）**全部达标**」⇒ **永假**。

**改数据源后的实测对照**（同一天、同一批 32 个候选）：

| 指标 | 规则侧 | glue 侧 | 差 |
|---|---|---|---|
| M1 | 3.1% | **34.4%** | 11× |
| M6 | 65.6% | **96.9%** | — |

⇒ **修数据源解除死锁，但不解除「红线不可达」**（M1 的 60% > 34.4%）。**两个独立问题，不可混修。**

### 2.2 C2 — M6 的分子被污染（把规则的功劳记给 Agent）

0910 的 32 个候选中，规则侧已有归属 **21 个**、「其他」**11 个**。glue 对全部 32 个给出了家族，
其中对 11 个「其他」的回填情况：

```
超市连锁 → 消费      焦炭加工 → 周期资源   培育钻石 → 消费
水运     → 周期资源   火力发电 → 周期资源   汽车服务 → 消费
通信设备 → AI硬件     建材     → 周期资源   MLCC概念 → AI硬件
第三代半 → AI硬件     陶瓷     → 无归属
```

已归属的 21 个里，glue 与规则 **18 一致 / 3 冲突**（一致率 85.7%）：

| 板块 | 规则侧 | glue | 定性 |
|---|---|---|---|
| 880642 供销社 | 农业 | 消费 | 真冲突 |
| 880728 航运概念 | 军工 | 周期资源 | 真冲突（B 层已记录） |
| 880945 OLED概念 | AI软件 | AI硬件 | 真冲突（B 层已记录） |

**问题**：`frame_group != "其他"` 这个定义把规则侧本来就分好的 21 个算了进去。
⇒ **Agent 一个都说错时，M6 仍能拿 65.6 分**。它测的是「规则侧分好了几个」，不是「Agent 救回了几个」。

**正确指标**：**「其他」回收率** = glue 命名的原「其他」板块数 / 原「其他」板块数 = **10/11 = 90.9%**
（陶瓷 880345 判「无归属」，规则侧本来也无归属 ⇒ 计为未回收）。

| 定义 | 分子 | 0910 | 无 glue 时基线 | 红线 |
|---|---|---|---|---|
| 现状（覆盖率） | 21 规则 + 10 glue | 96.9% | **65.6%** | ≥80% |
| 回收率 | 只数 glue 救回的 | **90.9%** | **0%** | ≥80% |

**反向风险**：回收率只奖励「给出家族名」，会诱导 Agent 强行套词表（陶瓷判「无归属」就是一个可疑案例，
同批的建材 880344 拿到「周期资源」）。**正因如此 M3（人工抽查误配率 ≤15%）不能省。**

### 2.3 C3 — M4 三条红线退化为一条（对照量无独立信息量）

0910（n=32，逐板块 join）：

| 量 | Pearson | Spearman |
|---|---|---|
| `ρ(glue.strength, tech_score)` | **+0.591** | **+0.419** |
| `ρ(glue.strength, composite_score)` | +0.646 | +0.500 |
| **`ρ(tech_score, composite_score)`** | **+0.985** | **+0.965** |

`mean|composite_score − tech_score| = 2.56 分`（`tech_score` 标准差 25.3）。

**两个对照量之间 ρ=0.985** ⇒ M4-a 与 M4-c **测的是同一个量**；M4-b 的 `<1.0` 也自动满足（0.646 离 1 很远）。
三条红线**只有 1 个自由度**。0910 出现「**M4-a 不过（0.591 < 0.6）、M4-c 过（0.646 ≥ 0.3）**」——
同一天、同一个检验、分列一条红线两侧，这就是退化的直接证据。

**根因在数据里可见**：

```
sentiment_check: {low: 32}                      ← 32/32 全 low
capital_check:   {neutral: 31, aligned: 1}
news_check:      {neutral: 31, catalyzed: 1}
```

四维修正合计只挪动 2.56 分 ⇒ `composite` 是 `tech_score` 的近似单调变换。
**这是 B 层 B1（多维修正空转）在验收层的投影：一个没有独立信息量的对照量，不能用来做对照。**

**根因链再往上追一层（D 层实测，2026-09-11 补）**：`sentiment_check` 恒 `low` 不是"情绪面中立"，
而是 `_cross_check_sentiment`（`aggregator.py:1624-1625`）在 `not hot_sectors` 时直接返回
`("low", "无热榜数据")`；而 `hot_sectors` 的唯一生产者 `_extract_hot_sectors`（`:3322-3327`）
遍历 `stock.associated_sectors` —— 该字段**三个数据路径都没填**（`sentiment_analysis.py:792` / `:822` / `:543-551`），
恒为 `[]`。即：**M4 共线的根因链起点是 `associated_sectors` 恒空**，详见
[`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md) §2.3。同一处空值还使埋伏闸门
**G2 的注意力子闸**与 **G3 拥挤度黑名单**结构性失效（G2 的**已发酵子闸**不依赖 `hot_sectors`、**仍然活着**）——**不是「实际只剩 2 层」**，见 D 层 §2.3 修订表。

**但共线不是设计属性，是 B1 的后果**：B1 修好后修正项不再是常数，两个对照量会分离，M4-a 与 M4-c
会重新成为两个独立检验。⇒ **此时删除 M4-a 会把这个「由 B1 引起的共线」固化成永久设计决定。**（决策见 D-C2）

**附加**：M4-a 在 n=32 下**不可判定**。Spearman 标准误 ≈ 1/√(n−1) = 0.18，
ρ=0.591 的 95% 区间约 ±0.35 —— 红线 0.6 落在噪声带内。「0.591 不过 0.6」是一次抽样，不是一个结论。

**0903 §8.1 的标题写着「单一 Spearman 无诊断力」，但 M4-a/b/c 的表里从未写明用哪个系数。**
同一份数据，Pearson 给 0.591（差 0.009 达标）、Spearman 给 0.419（差 0.181 达标）——**判定由未指定的自由参数决定。**

### 2.4 C4 — `GlueStrength` 无口径声明字段

```python
# src/got/schemas.py:1049-1057
class GlueStrength(BaseModel):
    """Agent 语义粘合层 — 板块强度（替代 Segment C/D/E）"""
    board_code: str
    strength: float = Field(ge=0.0, le=1.0)      # ← 0~1，无量纲声明
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = Field(default="")
    capital_read: str = Field(default="unknown")
    news_read: str = Field(default="unknown")
    sentiment_read: str = Field(default="unknown")
```

`strength` 是 **0~1**，而它要替代的 `composite_score` 是 **0~100**（`schemas.py:698`）。
0903 D5「强度替换、准入保留」定了替换对象是 `composite_score`，但**这个约定不在 schema 里**，只在文档里。

实测证明确有必要显式声明，因为 Agent 在**重估**而非缩放：

```
|Δ| = |glue×100 − tech_score| > 10 的板块: 25/32 = 78.1%    中位 Δ = −12.4
  光刻机     tech=30.7  comp=30.7  glue=60.0   Δ=+29.3
  半导体     tech=34.0  comp=54.0  glue=65.0   Δ=+31.0
  旅游景点   tech=86.8  comp=81.8  glue=45.0   Δ=−41.8
  渔业       tech=78.1  comp=81.1  glue=40.0   Δ=−38.1
```

这是 D5 想要的（科技上调、消费农业下调），但它意味着 **M4-a 的对照对象只能靠约定存在**——
D5 一漂移，M4-a 就变成不可判定的红线。

### 2.5 C5 — 最该复盘的样本是唯一不落盘的样本

`_agent_semantic_glue`（`aggregator.py:942`）有 **8 个返回点**，只有 **2 个落盘**：

```python
:962   if not AGENT_SEMANTIC_GLUE_ENABLED:   return self._glue_f1_degraded(...)      # S6 —— 不落盘
:967   if self._replay_mode:                 return self._glue_f1_degraded(...)      # replay —— 不落盘
:970   if not candidates:                    return SemanticGlueResult(degraded=True) # 无候选 —— 不落盘，且【无 reason】
:988   except Exception (LLM 调用失败):       return self._glue_f1_degraded(...)      # S1 —— 不落盘
:993   if not raw or not raw.strip():        return self._glue_f1_degraded(...)      # S1 空响应 —— 不落盘
:1003  except (JSONDecodeError, ValueError):  return result                          # S2 解析失败 —— 不落盘
:1014  except Exception (validate 异常):      return result                          # S2 校验失败 —— 落盘（:1013 调用）
:1038  正常路径                               return result                          # ok —— 落盘（:1021 调用）
```

⚠️ **2026-09-11 修订**：本节原写「6 个返回点 / 3 个落盘」，清单列 7 行（其中 `:1013`/`:1021` 是
**落盘调用**而非 return）——**散文、清单、实测三处数字互不相容**（6 / 7 / 3 vs 实测 8 / 2）。
已按实测重列。**漏掉的是 `:970`**：它是八个降级态里**唯一不带 `reason` 的**（`SemanticGlueResult`
当前连 `reason` 字段都没有，`reason` 是被 `_glue_f1_degraded` 塞进 `warnings` 的），
即**最纯粹的「文件不存在」歧义态**，且**生产可达**（候选为空即触发；0910 run#1 `Node 2` 拿到
0/172 板块 —— 当日是否走到此路径**待核**，但可达性本身由代码决定）。
⇒ C-P1 若不补 `:970`，R-C1「消除文件不存在的歧义态」**仍不完整**。

⇒ **失败样本（S1/S2）不落盘，且 `raw_response` 只挂在内存对象上，函数一 return 就没了**
（`SemanticGlueResult.raw_response` 带 `exclude=True`，即便落盘也不含原文）。

**0908 就是这个缺陷的实证**（`logs/stock_analysis_20260908.log`）：

```
13:47:13 | WARNING | aggregator.py:1000 | [Node 6] glue JSON 解析失败，F1 降级: Expecting ',' delimiter: line 490 column 6 (char 13660)
13:47:18 | WARNING | aggregator.py:319  | [Node 6] 数据质量告警: missing_fields=['hot_stocks_sectors_empty', 'agent_semantic_glue']
```

0908 的**畸形 LLM 输出被完整产生、被解析器逐字符定位到第 490 行**，然后被丢弃。
**这一天从此在验收数据集里不存在**：事后无法区分「没跑」「跑了失败了」「开关关了」——
三种情况的磁盘表现都是「文件不存在」。

**附带（M5 的当日口径未定义）**：§8.1 M5 =「触发 S1–S5 任一场景的**交易日**占比」，
但一天可能有多次运行（0910 有 4 次启动 / 2 次成功归档）。run#1 降级而 run#4 成功时，
**这一天算不算降级？** 按 D-1「末版为准」，当日的脸是**最后一次运行**。
⇒ M5 应定义为「当日**最后一次运行**降级的交易日占比」。**现有表述无法判定，也就无法度量。**

### 2.6 C6 — M2 的红线与 prompt 的指令方向相反

```
prompt（aggregator.py:796）    - 没把握的事件不要输出（不要编造）
M2 红线（0903 §8.1）            至少关联到 1 条 catalyst event 的候选占比 ≥ 70%
```

**这两条不能同时满足。** 实测（四日）：

| 日期 | 候选 | relations | 命中候选 | M2 |
|---|---|---|---|---|
| 0904 | 19 | 6 | 4 | 21.1% |
| 0907 | 22 | 6 | 3 | 13.6% |
| 0909 | 32 | 22 | 7 | 21.9% |
| 0910 | 32 | 25 | 9 | **28.1%** |

Agent 越守 prompt 的纪律，M2 越低。0910 只给 9 个板块归属，是因为它判断其余 23 个没有「有把握」的传导链——
**它做对了，M2 判它错。**

**且 M2 与 M3 是拉锯关系**：把覆盖推上去，误配率就上来。
**prompt 的松紧就是这两条红线之间的取舍**，而 0903 从没把这个取舍显式化——它给 M2 设 70%、给 M3 设 15%，
然后让 prompt 自己去撞。**这是 C 层唯一需要新实验（而非新定义）才能决定的一项。**

### 2.7 C7 — `range_reversal` 恒真，且埋着一颗定时炸弹

触发条件（`aggregator.py:3169-3171`）：

```python
# ── 模式 4: range_reversal ──
# 高震荡（transition_count >= 5）+ 低持续性（persistence_regime == "low"）
if transition_count >= 5 and persistence_regime == "low":
```

**两个条件在 11/11 天都成立**：

```
transition_count  11/11 天: 11,11,11,11,12,12,13,13,13,14,14   ← 恒 ≥ 5
persistence_regime 11/11 天: "low"                              ← 恒
```

⇒ 信号零区分度。**且同类判据还有一处**：

```python
# aggregator.py:3027-3029
# 震荡惩罚：transition_count ≥ 5 → × 0.8
if regime.transition_count >= 5:
    position *= 0.8
```

11/11 天成立 ⇒ 一个标注「震荡惩罚」的因子实际是**一笔恒定 20% 的税**。
`_compute_position_from_regime` 的输出再被 `min(·, regime.risk_budget)` 压死（A 层已证 19/27 天严格相等），
所以这笔税在最终仓位上不可见——**又一个「错误被下游巧合掩盖」**。

**病因比「阈值偏松」深**。`transition_count` 的来源是（`aggregator.py:557-562`）：

```python
transition_count = 0
if len(regime_history) >= 2:
    for i in range(1, len(regime_history)):
        if regime_history[i][1] != regime_history[i-1][1]:
            transition_count += 1
```

数的是 `load_regime_state_history(days=20)`（`persistence.py:355`）里**相邻日 regime 变化次数**。
而这份 20 日历史**横跨一个方法学断点**——状态机字段首次出现在 **0827**：

| 日期 | regime | dwell | tcount | 备注 |
|---|---|---|---|---|
| 20260813–20260826 | `T B T B B T T B T B R B T` | — | — | **状态机不存在，regime 是每日原始值**（17 对里翻 11 次） |
| 20260827 | bull | 2 | **11** | 状态机上线 |
| 20260831 | bull | 2 | 11 | |
| 20260901 | transition | 7 | 12 | |
| 20260902 | bear | 8 | 13 | |
| 20260904 | bull | 3 | 14 | |
| 20260907 | bull | 4 | 14 | |
| 20260909 | bull | 6 | 12 | |
| 20260910 | transition | 2 | **11** | |

把 0910 的 20 日窗口（0813–0909）拆开数：

| 段 | 天数 | 序列 | 变化次数 |
|---|---|---|---|
| 0813–0826（**状态机从未处理过**） | 10 | `T B T B B T T B T B R B T` | **7** |
| 0827–0909（状态机产物） | 10 | `B B B T R R B B B B B T` | **4** |
| | | | **11** ✓ 与归档一致 |

**0910 的 `transition_count=11` 里，7 次来自一段状态机从未处理过的原始噪声；状态机自己的产出只有 4 次——低于阈值 5。**

⇒ **定时炸弹**：0813–0826 这 10 个原始日每过一个交易日滚出一个，约 **2026-09-25 前后**全部滚出窗口，
`transition_count` 将落到状态机真实值（≈4–6），**横躺在阈值 5 上**。
届时同样的代码、同样的行情，仓位上限会**自行跳动约 25%**，`range_reversal` 会忽有忽无——**没有任何代码改动**。

（`archive.py:196` 把它打印成「**近20日切换次数**」，所以这个数在推理报告里一直可见。）

**⚠️ 与 A 层 D-2 的口径耦合**：本节的 `11` 与炸弹倒计时，均按**当前读取口径**计算——
`load_regime_state_history(20)`（`persistence.py:368`）**不排除当天**。A-P0-2 会把它改为
`before=date.today()`，即 `transition_count` 只统计历史日。**单跑日数值不变**，
同日重跑时旧口径会把当天早轮值计入（正是 A 层 §2 的 D3 自引用）。
⇒ **A 层落地后须重算一次倒计时**（"≈2026-09-25"是按旧口径推的），
并用 C-P10 的 `transition_count_raw_segment_days` 复核。

### 2.8 C8 — 0903 §8.3 声明的两项「硬阻塞」已失效

0903 §8.3 把 V1（无 GOT 日期回放入口）与 V2（无 Context 反序列化器）列为 **A5（开影子期）的硬阻塞**。
**两项在 HEAD 上都已存在**：

```
main.py:359-365    --got-replay YYYYMMDD
                   help='回放指定日期的 GOT Node 6 聚合（从归档加载 5 个 Context，不跑 Node 1-5，不写归档）'
main.py:1324       from src.got.archive import load_daily_contexts
main.py:1328       contexts = load_daily_contexts(replay_date)
main.py:1343       replay_mode=True
main.py:1358       replay_dir = Path("data/got_daily") / args.got_replay / "_replay"
aggregator.py:176  replay_mode: 回放模式（agent_glue_plan_0903 §8.3 A1）
```

`git log -S` 显示 `--got-replay` 与 `load_daily_contexts` **都出自 `a5fdc64`**——即按 0903 §10 A1 实施的那一项，
**已经建完了**。V1/V2 失效，A5 对它们的阻塞自然解除。

**但回放显式跳过 glue**（`aggregator.py:965-967`）：

```python
if getattr(self, '_replay_mode', False):
    logger.info("[Node 6] replay_mode: 跳过 _agent_semantic_glue")
    return self._glue_f1_degraded(candidates, reason="replay_mode")
```

⇒ 回放能重建规则侧，**不能重建影子样本**。**0908 依然无法靠现有能力恢复**（除非改这一处，见 §5 C-P3）。

**且回放路径有个潜伏 bug**：

| 写入方 | 目录基准 | 位置 |
|---|---|---|
| replay 的 `unified.json` | `{replay_date}/_replay/` | `main.py:1358` |
| glue 落盘（`_save_glue_result`） | **`date.today()`** | `aggregator.py:1085` |

跨日回放时两者会落进**两个不同的 `_replay/` 目录**。现有唯一一次回放
（`data/got_daily/20260903/_replay/unified.json`，写于 09-03 21:40）是**当日**跑的，所以侥幸未暴露；
**回填 0901–0908 必踩**。

**顺带验证 M4 不需要回放**：本层 §2.3 的三个相关系数，是用 `agent_glue.json` + `unified.json` 直接算出来的，
**一个字节的回放都没用**。⇒ 0903「无回放 ⇒ M4 无从实施」的推论对 M4 不成立（只对 M5 与「重跑纠错」成立）。

### 2.9 公开撤销（防后人重踩）

| # | 曾被主张 | 核查结论 | 依据 |
|---|---|---|---|
| 1 | 「M1/M6 在影子期**结构上不可改善**」（A 层 §7.1 C 行） | ⚠️ **表述不准且低估**。准确说法：**门禁自锁**——不是「改善不了」，是「M6 改对数据源后实测 96.9% 达标，但达标发生在转正之后，而达标是转正的条件」。 | §2.1 |
| 2 | 「`strengths` 无 `kind` 字段」（A 层 §7.1 C 行） | ⚠️ **应命名为「无口径声明字段」**。缺的不是「种类」，是「它替代哪个量」的声明（`basis`）。名称错会导向错的修法（加枚举 vs 加契约）。 | §2.4 |
| 3 | 「`range_reversal` 11/11 天零区分度」（A 层 §7.1 C 行） | ✅ 成立，但**定性应升级**：不是「阈值偏松」，是 `transition_count` **跨方法学断点**+ 一颗 2026-09-25 的时间炸弹。 | §2.7 |
| 4 | 「L466 新旧对照**本身就不公平**（规则侧资金覆盖 3/24）」（0903 自述） | ⚠️ **方向对但结论弱**。真正的病不是「对照不公平」，是**对照量没有独立信息量**（`ρ(tech, composite)=0.985`）。覆盖率低只是现象，共线才是使得 M4-a ≡ M4-c 的机制。 | §2.3 |
| 5 | 「M1 的 0903 基线 = `0/24`」（0903 §8.1） | ⚠️ **数值不可复用**：候选数逐日变化（0904=19 / 0907=22 / 0909=32 / 0910=32），0903 的 24 不是稳定分母；0910 实测为 **1/32**。 | §1 |
| 6 | 「M4-b/M4-c 合起来是双向约束，是相对 T5 影子期的**核心加严**」（0903 §8.1） | ❌ **实测退化为一条**。两个对照量 ρ=0.985，三条红线只有 1 个自由度；0910 出现 M4-a 不过 / M4-c 过的分裂。 | §2.3 |
| 7 | 「`_log_shadow_comparison` 是 M4-b/M4-c 的**度量源**」（`aggregator.py:1045` docstring + 0903 §8.1） | ❌ **不成立**：只比 `tech_score` 不比 `composite_score`、截断 8 条、只打日志不落盘；**且它记录的数磁盘上已有**（§2.3 的三个 ρ 未使用它）。 | §2.5 / §5 C-P4 |
| 8 | 「V1/V2 是 A5 的**硬阻塞**」（0903 §8.3） | ❌ **已失效**：两者均在 `a5fdc64` 落地（同 commit 即 0903 §10 A1 的实施）。 | §2.8 |
| 9 | 「删 `adjustments` 分支，理由：`adjustments` 与 glue 改同一字段」（0903 L765） | ❌ 已在 B 层记录：`adjustments` 改 `classification`（`aggregator.py:1890`），glue 转正后改 `composite_score`。**本层不重复，仅交叉引用**。 | B 层 §2.7 |
| 10 | 我上一轮把「收缩『其他』」列为 B 层修复选项之一 | ❌ 已在 B 层 §2.8 记录：收缩「其他」= 主动废掉 A6 的用武之地（0903 D3-b 明写「把『其他』从护栏输入里摘掉，而不是把 95 个板块重新分类」）。 | B 层 §2.8 |

### 2.10 防误修清单（本轮**不得**顺手改的东西）

| # | 看起来像缺陷 | 核查结论 | 依据 |
|---|---|---|---|
| 1 | 「M1 只有 34.4%，离 60% 太远 ⇒ 应改 prompt 让 Agent 多判 news_read」 | **不要在标定前改 prompt**。M1 与 M2 共用同一个「保守 vs 激进」自由度；分开改会得到两条彼此矛盾的 prompt。**统一走 D-C6 的 A/B。** | §2.6 |
| 2 | 「M6 应把分母改成全部候选（更全面）」 | **不要**。改回覆盖率就是把规则的 21 个算成 Agent 的功劳（§2.2）。覆盖率降为观察项，**不进红线**。 | §2.2 |
| 3 | 「M4 共线 ⇒ 直接删掉 M4-a」 | **不要**。共线是 B1 的后果不是设计属性；B1 修好后两者会分离。**加 `basis` + 标注前提**，红线留到标定阶段。 | §2.3 |
| 4 | 「删掉 `_log_shadow_comparison` 后就没人算 M4 了」 | **不会**。M4 需要的三个量（`glue.strength` / `tech_score` / `composite_score`）**已全部逐板块落盘**，本层 §2.3 就是证明。 | §2.5 |
| 5 | 「`transition_count` 恒 ≥5 ⇒ 把阈值从 5 提到 10」 | **不要在本轮动**。病根是窗口跨方法学断点，提阈值只是把 09-25 的跳变推后。且改阈值 = 行为变更，与 D-3/D-4 冲突。 | §2.7 |
| 6 | 「回放能重建一切 ⇒ 0908 用回放补上，`replay_mode` 那行删掉」 | **不要删**。`replay_mode` 跳过 glue 是 0903 A1 的「**不产生任何持久化副作用**」契约（`aggregator.py:177-178`）。改为**加开关**（默认仍跳过），不要改默认行为。 | §2.8 / §5 C-P3 |
| 7 | 「M1 数据源改对后就该顺手把 M1 红线降到 34%」 | **不要**。D-C1 明确「**先**拿到连续 10 日干净样本，**再**标定」。4 天样本 + 候选数 19/22/32/32 波动不足以定红线。 | D-C1 |
| 8 | 「`CONTROLLED_FAMILIES` 只有 12 个，应补 `华为生态`/`前沿科技`」 | **属 B 层**（B 层已记：词表同源 = must fix），**不在 C 层**。C 层只关心 glue 实际输出的家族名是否越界——0910 实测 0 越界（`warnings=[]`，`degraded=False`）。 | B 层 §2.2 |

---

## 3. 根因

C 层的病是**一个**：**验收指标的设计没有经过「这个量能不能被算出来」的检验**。它有三个具体形态：

1. **指标读错了字段**（M1/M6）。红线写在影子期不可动的规则侧字段上 ⇒ 门禁自锁。
   根因：**「度量」与「被度量对象」没有建立绑定**——没有一条约定说「M1 必须读 glue 产出的那个量」。
2. **指标的定义把无关的东西算进了分子/分母**（M6 覆盖率、M2 候选分母）。
   根因：**指标名与指标式不匹配**——「家族覆盖率」这个名字下挂的是「非其他占比」，两者在 glue 缺席时差别是 65.6 vs 0。
3. **指标所需的对照量没有独立信息量**（M4）。`composite` 与 `tech_score` ρ=0.985 ⇒ 三条红线一条自由度。
   根因：**对照量继承了被对照对象的缺陷**（B1 的空转修正项）。

**共同的可操作判据**：

> 一个验收指标要成立，必须能回答三问：
> ① **谁产出**这个字段？（若产出方在验收期内不生效，指标就是死锁）
> ② **基线**取值为多少？（若基线已经离红线不足 15 个点，指标主要在测别的东西）
> ③ **对照量与被对照量**的相关系数是多少？（若 >0.9，两个指标是同一个）

**C 层与 A/B 层是同一个病的第三个面**：A 层是「状态被推进 N 次」，B 层是「同一概念算了 N 次」，
C 层是「**验收了 N 次，但只测了一个量**」。

---

## 4. 已确认决策（本轮不再重议）

| # | 决策 | 用户原话/选择 |
|---|---|---|
| **D-C1** | **先修可观测性，再标定红线**：修 S1/S2 落盘 + 修 M1/M6 数据源，攒满连续 10 日干净样本，再按实测重标红线。理由：现在只有 4 天、候选数 19/22/32/32 波动，任何红线标定都无统计意义；且 n=32 下 0.6 这种精度不可判定 | 「先修可观测性，再标定红线（推荐）」 |
| **D-C2** | **保留 M4-a/b/c，加 `basis` 字段**；标注「M4-a 的独立性以 B1 修复为条件（B1 未修时实测 M4-a ≡ M4-c）」；三条红线留到标定阶段按实测重设 | 「保留三条，加 basis，标注共线前提（推荐）」 |
| **D-C3** | **M6 改为「其他」回收率**（基线 0%，每个百分点都是 Agent 挣的）；覆盖率降为**观察项**，不进红线；防诱导靠 M3（人工抽查误配率 ≤15%） | 「改为「其他」回收率，覆盖率降为观察项（推荐）」 |
| **D-C4** | **回放回填**：修 `_save_glue_result` 的日期 bug + 给 replay 加 glue 开关，把 0901–0910 补成 8 天影子样本（含恢复 0908），再实盘补 2 天。**M5 仍只能靠实盘**（回放无网络抖动，降级诱因不同） | 「回放回填（推荐）」 |
| **D-C5** | **记录 + 落入可观测性**：判据不动，但把「本次运行震荡惩罚是否生效」作为布尔量落进归档；记 OPEN-C1 带日期（≈2026-09-25 自发跳变） | 「记录 + 落入可观测性（推荐）」 |
| **D-C6** | **先做 prompt A/B**（保守版 vs 全量归属版），用 M2/M3 **双指标**判读，再定 prompt 与红线。回填期本来就要跑 8 天，A/B 成本只是再跑一遍 | 「先做 prompt A/B，再定红线（推荐）」 |
| **D-C7** | **删 `_log_shadow_comparison`**，新增 `scripts/verify_glue_metrics.py` 从归档复算 M1–M6（回填期与实盘期同一脚本） | 「删日志，改归档复算脚本（推荐）」 |

**设计推论（由 D-C1 + D-C7 导出）**：
「可观测性」的定义在本轮被收紧为——**任何一个红线指标，都必须能被一个只读归档的脚本复算出来，
且该脚本不得依赖任何运行期状态（日志、内存、DB 计数器）**。因为红线的有效期是 10 个交易日，
而运行期状态的有效期是一次运行。

---

## 5. 修复清单

### 5.1 P1 — 可观测性（D-C1 / D-C7，**零行为变更**）

#### C-P1 S1/S2 失败样本落盘

- **位置**：`aggregator.py:960-1038`（**8 个返回点**，见 §2.5）
- **改法**：
  1. `SemanticGlueResult`（`schemas.py:1066-1078`）新增 `status: str`，取值
     `ok` / `no_candidates` / `S1_call_failed` / `S1_empty` / `S2_parse_failed` / `S2_validate_failed` / `S6_disabled` / `replay_mode`；
     ⚠️ **`no_candidates` 不可省**（原枚举 7 值漏了它）：`:970` 的
     `if not candidates: return SemanticGlueResult(degraded=True)` 是唯一不带 `reason` 的降级态
  2. `raw_response` 去掉 `exclude=True`（**仅在 `status != "ok"` 时写**，成功路径仍不写，避免 90KB 膨胀——`_save_glue_result:1096` 已有大小告警）；
  3. `:962 / :967 / :970 / :988 / :993 / :1003` **六个** `return` 前各补一次 `self._save_glue_result(...)`
     （余下 `:1014` / `:1038` 两个已落盘 ⇒ 6 + 2 = 8，与 §2.5 一致）
- **效果**：`agent_glue.json` **在每个降级路径上都会存在**，消除「文件不存在」这个歧义态（0908 的病因）。
- **理由**：S1/S2 是唯一值得复盘的样本，却是唯一不落盘的样本（§2.5）。
- **⚠️ 与 A-P0-8 的落地约束**：`_save_glue_result` 的落盘路径（`aggregator.py:1086`）**就是归档目录本身**
  `data/got_daily/{today}/`，而 `archive.py:96` 的 `preserved_files = {"agent_glue.json"}` 依赖"本轮一定写过"
  这个前提才成立。改归档为原子替换时，若本轮走了降级路径（不落盘），把目标目录的 glue 搬进 tmp
  就等于把**上一轮**的搬进来——正是 A-P0-8 要修的形态。
  ⇒ **C-P1 必须先于或同批于 A-P0-8**；见 [`got_run_model_plan_0911.md`](got_run_model_plan_0911.md) §5.1 A-P0-8-a。

#### C-P2 `_save_glue_result` 目录基准改为运行日期

- **位置**：`aggregator.py:1085` `today_str = date.today().strftime("%Y%m%d")`
- **改法**：引入 `self._run_date`（在 `run()` 里初始化：`getattr(self, '_run_date', None) or date.today()`），
  `_save_glue_result` 改用它。与 `main.py:1358` 的 `{replay_date}/_replay/` 对齐。
- **理由**：跨日回放时 glue 产物会落进今天的 `_replay/`，与 unified.json 分家（§2.8）。
- **⚠️ 与 A-P0-8 的落地约束**：A-P0-8 的 tmp 方案要求"替换前把 `_replay/` 搬进 tmp"，
  而搬运的对象是否正确取决于**本项的日期基准**。不修本项 ⇒ 回放样本落在 `{today}/_replay/`，
  A-P0-8 会把 `{today}/_replay/` 整体搬走 ⇒ C-P11 的回填样本（0901–0903）落错日期目录，
  **R-C4 的"连续 7 天"直接不成立**。⇒ **C-P2 与 A-P0-8 必须同批**。

#### C-P3 `replay_mode` 下允许 glue（受开关控制，默认保持现契约）

- **位置**：`aggregator.py:965-967`
- **改法**：把硬跳过改为 `if getattr(self, '_replay_mode', False) and not AGENT_SEMANTIC_GLUE_IN_REPLAY:`
  （新增开关 `catalyst_config.py`，**默认 `false`**，与 `AGENT_SEMANTIC_GLUE_SHADOW` 同构）。
- **理由**：D-C4 的回填是恢复 0908 的**唯一**路径。默认值不变 ⇒ 不改 0903 A1「不产生任何持久化副作用」的契约
  （`aggregator.py:177-178`）。

#### C-P4 删除 `_log_shadow_comparison`

- **位置**：`aggregator.py:1040-1071`（函数）+ `:1028-1029`（调用点）+ `:1045` docstring
- **改法**：整体删除，不留 stub。
- **理由**：只比 `tech_score` 不比 `composite_score`、截断 8 条、不落盘，**且它记录的数磁盘上已有**（D-C7）。

#### C-P5 新增 `scripts/verify_glue_metrics.py`

- **接口**：`python scripts/verify_glue_metrics.py [--date YYYYMMDD] [--all] [--json]`
- **输入**：只读 `data/got_daily/{date}/agent_glue.json` + `unified.json` + `news.json`
- **输出**：M1 / M1′ / M2 / M3（人工栏位留空）/ M4-a·b·c（**Pearson + Spearman 都出**）/ M5（读 `status` 字段）/
  M6（回收率）/ M6′（覆盖率，观察项），逐日 PASS/FAIL + 汇总
- **硬要求**：**输出必须与本文 §1 的数字逐位一致**（可复现性锚点，见 R-C3）。
- **理由**：D-C7；并让「红线」第一次有唯一权威口径（现在 Pearson/Spearman 未指定，§2.3）。

### 5.2 P2 — 指标定义（D-C2 / D-C3，**零行为变更**）

#### C-P6 M1 数据源改 glue 侧

- **改法**：M1 = `agent_glue.json` `strengths[].news_read ∉ {low, unknown}` 的占比；
  规则侧 `unified.json` 的 `news_check != neutral` **并列展示为对照列**（不消失，便于看两者差）。
- **理由**：§2.1。对照列的差值（0910：3.1% vs 34.4%）本身就是「glue 有多大信息量」的度量。

#### C-P7 M6 改为「其他」回收率

- **改法**：M6 = `|{原「其他」板块中 glue 给出真实家族名的}|` / `|{原「其他」板块}|`；
  原「其他」集合取自 `unified.json` 的 `frame_group == "其他"`；「无归属」**计为未回收**；
  `frame_group != "其他"` 占比降为**观察项**。
- **理由**：§2.2。现状定义的基线 65.6% 与红线 80% 之间只有 14.4 个点属于 Agent。

#### C-P8 明确 M4 的系数与前提

- **改法**：0903 §8.1 的 M4 表增列「系数」= **Spearman**；增注「M4-a 的独立性以 B1 修复为条件」；
  M4-a 的红线数值**标记为「待标定」**（不保留 0.6，避免一个未实测算过的数字继续充当红线）。
- **理由**：§2.3。同一份数据 Pearson 0.591（差 0.009）vs Spearman 0.419（差 0.181），判定由未指定的自由参数决定。

#### C-P9 `GlueStrength` 新增 `basis` 字段

- **位置**：`schemas.py:1049-1057`
- **改法**：新增 `basis: str = Field(default="composite", description="strength 所替代的口径（当前恒为 composite_score，0-100 百分制）")`；
  并在 `_build_glue_prompt` 的任务 2（`aggregator.py:798-806`）与输出格式示例（`:820`）里写明
  「`strength` 替代的是**综合评分**（技术+资金+消息+情绪），不是只替代技术面」。
- **理由**：§2.4。实测 78.1% 的板块 |Δ|>10，Agent 在重估而非缩放；无 `basis` 则 M4-a 的对照对象只靠文档约定。

### 5.3 P3 — 判据可观测化（D-C5，**零行为变更**）

#### C-P10 归档 `meta.json` 记录「震荡惩罚是否生效」

- **位置**：`archive.py:_build_meta`（`meta.json` 构造处）+ `aggregator.py:3027-3029`
- **改法**：`meta.json` 新增两个**观测字段**（与 A-P0-9 的 `snapshot_time`/`bar_complete` 同一处）：
  - `regime_choppiness_penalty_applied: bool`（= `regime.transition_count >= 5`）；
  - `transition_count_raw_segment_days: int`（20 日窗口中落在状态机生效日之前的天数）。
- **不做**：不改 `>= 5` 这个阈值，不改 `range_reversal` 的触发条件，不改任何仓位数值。
- **理由**：D-C5。让「×0.8 常驻」与「09-25 前后会自发跳变」在归档里可见，
  否则届时会被误判为「市场变了」。**第二个字段是关键**：它把「时间炸弹还剩几天」变成可读数字。

### 5.4 P4 — 实验（D-C4 / D-C6，**只在影子路径运行**）

#### C-P11 回填跑批

- **命令**：`--got-replay 20260901` … `--got-replay 20260908`（含 0908），前置 `AGENT_SEMANTIC_GLUE_IN_REPLAY=true`
- **产出**：`data/got_daily/{date}/_replay/agent_glue.json` × 4，影子样本 **4 → 8 天**（0904/0907/0908/0909/0910 + 0901/0902/0903）
- **验收**：`verify_glue_metrics.py --all` 输出 8 行、`status=ok` ≥ 7
- **不做**：不把回填产物写进非 `_replay/` 目录；不覆盖既有 4 天的实盘样本

#### C-P12 prompt A/B

- **变量**：任务 1 的指令口径
  - A（保守，现行）：「没把握的事件不要输出（不要编造）」（`aggregator.py:796`）
  - B（全量归属）：「**每条有传导链的事件都要给出归属**；把握不足的用低 `confidence`（<0.5）表达，不要省略」
- **判读**：在回填 8 天 + 实盘续跑上各跑一遍，输出 **M2（覆盖）× M3（误配）对照曲线**（不是 PASS/FAIL）
- **不做**：不在拿到曲线前改 prompt 默认值

#### C-P13 红线标定（**依赖 C-P11/C-P12 的产出，本轮不执行**）

- **改法**：依据 ≥10 日干净样本，重设 M1 / M2 / M4-a / M6 红线；§8.2「双达标」补
  「M5 以**当日最后一次运行**的 `status` 为准」（§2.5 的口径缺省）。
- **理由**：D-C1。**本层只把「怎么标定」写清楚，不预设数值。**

---

## 6. 验收红线（可计数）

### 6.1 七条

| # | 指标 | 达标线 | 数据源 | 可度量 |
|---|---|---|---|---|
| **R-C1** | **失败样本必落盘**：制造 S1（断网/空响应）与 S2（畸形 JSON）各一次，`agent_glue.json` **仍存在**且 `status` 取值正确 | 2/2 存在 | `data/got_daily/{date}/agent_glue.json` | ✅ |
| **R-C2** | **回放目录正确**：`--got-replay 20260901` 的 glue 产物落在 `20260901/_replay/`，**不在 `{today}/_replay/`** | 路径精确匹配 | 同上 | ✅ |
| **R-C3** | **复算脚本可复现**：`verify_glue_metrics.py --date 20260910` 输出与本文**对应章节**逐位一致。必需锚点（**每项标注口径 —— 禁止跨口径比对**）：`M1=3.1%`（规则侧，§1）/ `M1′=34.4%`（glue 侧，§1）/ `M2=28.1%`（§1）/ `M4-a=0.591P,0.419S`（§1）/ `M4-b/c=0.646P,0.500S`（§1）/ `M6=65.6%`（**修复前**规则侧，§1）/ `M6′=96.9%`（glue 侧，§1）/ 「其他」回收率 `90.9%`（**C-P7 修复后定义**，§2.2） | 全部相等 | 脚本输出 | ✅ |
| **R-C4** | **影子样本达标**：`status=ok` 的交易日 ≥ **7**，且自回填日起**连续**。⚠️ **输出须逐日标注来源**（实盘 `{date}/agent_glue.json` vs 回填 `{date}/_replay/agent_glue.json`）；**纯实盘计数当前只有 4 天、缺 0908、本身不连续**（§1），故达标判定**必然**含回填样本 —— 但二者不得混为一条不加标注的计数 | 7 天（**分列来源**） | `agent_glue.json` `status` + **所在目录** | ✅ |
| **R-C5** | **无残留**：`_log_shadow_comparison` 在 `src/` 与 `tests/` 中**零引用** | 0 处 | `rg` | ✅ |
| **R-C6** | **A/B 有曲线**：M2 × M3 在 8 天回填样本上的对照表产出，两版 prompt 各有 8 个点 | 2×8=16 点 | `verify_glue_metrics.py --json` | ✅ |
| **R-C7** | **零行为变更**：C-P1/P2/P3/P4/P6/P7/P8/P9/P10 落地后，同一天归档的 `final_position_limit` / `unified_signal` / `confirmed_sectors[].classification` **逐字段不变** | 100% 一致 | 归档 diff | ✅ |

⚠️ **R-C3 的口径陷阱（2026-09-11 修订）**：原锚点写 `M1=34.4% / M1′=3.1%`，与 §1 表**恰好互换**
（§1：M1=3.1% 规则侧、M1′=34.4% glue 侧）；`M6=90.9%` 则是 **C-P7 修复后**的定义，而 §1 的 M6=65.6% 是**修复前**。
即原锚点同时混用了 **C-P6 后的 M1 / §1 修复前的 M1′ / C-P7 后的 M6** 三套口径，却自称「与 §2 逐位一致」——
**实施者照它校验只会得到假失败**。已逐项钉死口径与出处。
⚠️ **R-C4 的方法学陷阱（2026-09-11 修订）**：本节 §2.7 严厉批评 `transition_count` **跨方法学断点合并计数**，
而 R-C4 原表述对**同一形态**无任何约束 —— 回填样本（`_replay/`，2026-09-11 事后重跑、用**今日**的 LLM 与
prompt 版本）与实盘样本（父目录）被并成同一条「连续 7 天」计数。C 层 D-C4 已承认「回放无网络抖动，
降级诱因不同 ⇒ M5 只能用实盘」，**同一区分必须适用于 R-C4**。已改为强制分列来源。

### 6.2 R-C7 的对照字段清单

⚠️ **不能对整文件做 diff**：`data_quality.timestamp`、`meta.run_timestamp`、
新增的 `status` / `basis` / `regime_choppiness_penalty_applied` 等字段天然是新增项。
R-C7 **只比既有行为字段**：

```
unified.json:        market_regime.{regime, dwell_days, previous_regime, transition_count, transition_pending}
                     final_position_limit, unified_signal, contradictions[]
                     confirmed_sectors[].{classification, composite_score, frame_group, news_check,
                                          capital_check, sentiment_check, confidence}
meta.json:           （只比新增字段是否存在，不比数值）
agent_glue.json:     relations[]（数量与内容）；新增的 status / basis 是预期差异
```

⚠️ **对照必须限定在"单跑"条件下**：清单里的 `transition_count` 是 A-P0-2 的修改对象
（`load_regime_state_history` 改为排除当天）。**单跑日数值不变**；若拿**同日重跑**的归档做 diff，
`transition_count` 必然变化——那是 A 层 D-2 的预期修复，**不是 C 层回归**，不得据此判 R-C7 失败。
⇒ A-P0-1…A-P0-9 与本层 C-P1…C-P10 全部落地后，R-C7 的对照须在**同一天、各只跑一次**的条件下做。
（本层 C-P4 删除 `_log_shadow_comparison` 与 A-P0-8 的归档改动无冲突：前者在 `src/`，后者在归档写盘。）

### 6.3 验收手具

**唯一手具**：`scripts/verify_glue_metrics.py`（C-P5）。

它同时承担三个角色：
1. **红线的唯一权威口径**（消除 Pearson/Spearman 的未指定，R-C3）；
2. **回填期与实盘期的同一把尺**（不因数据来源不同而换算法）；
3. **本文所有数字的可复现锚点**（R-C3 要求逐位一致——任何一位不符，说明脚本或本文有一个错）。

**不需要新的回放能力**：M4 的三个相关系数已在 §2.3 用「只读归档」算出（§2.8）。
`--got-replay` 只用于**恢复影子样本**（D-C4），不用于算指标。

---

## 7. 范围外 / 挂起项

### 7.1 范围外（本轮不做）

| 层 | 项 | 文档 |
|---|---|---|
| A | 运行模型（同日重跑幂等 + 末版基线）：9 读侧 + 6 写侧 + 归档原子替换 + 快照标签 | [`got_run_model_plan_0911.md`](got_run_model_plan_0911.md) |
| B | 概念一致性（B1–B7）：**C3 的共线根因是 B1**，B1 不修则 M4-a 永远是 M4-c 的副本 | [`got_layer_b_plan_0911.md`](got_layer_b_plan_0911.md) |
| D | **D 层（与 0902 报告对账）已独立成文** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md)：15 项实测（生效 3 / 未修 10 / 症状自行消失 1 / 不修 1）。⚠️ **与本层 C3 同一根因链** —— `associated_sectors` 恒空（三处数据路径都不填）⇒ `_cross_check_sentiment` 恒返回 `("low","无热榜数据")`（0910 32/32）⇒ B1 ⇒ **M4 共线 ρ=0.985**；同一处空值还使埋伏闸门 **G2 的注意力子闸**与 **G3 拥挤度黑名单**结构性失效（G2 的**已发酵子闸**不依赖 `hot_sectors`、**仍然活着**）——**不是「实际只剩 2 层」**，见 D 层 §2.3 修订表。两层的可观测性修复应**同批实施** | `docs/got_layer_d_plan_0911.md` |
| E | **E 层（其他具体缺陷）已收口（2026-09-11）** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md) **附录 C**。12 项全部实测：**E9 已升格为本层 C7**；E1（0910 `sentiment_check` = low × 32/32）归 D 层 §2.3 链；其余分属三种形态。⚠️ **E10 的一项与本层直接相关**：`_agent_semantic_glue(..., regime)` 的 `regime` 形参**从未进 prompt**（`_build_glue_prompt` 无该形参）—— 决策 E-4 定为**删形参、不接进 prompt**，否则会改变 glue 输出、使本层影子对比失去基线 | `docs/got_layer_d_plan_0911.md` 附录 C |
| — | **`CONTROLLED_FAMILIES` 与 `FAMILY_KEYWORDS` 同源问题** | B 层 §2.2（不重复） |
| — | **删 `adjustments` 分支** | B 层 §2.7 第 9 行（0903 L765 的字段描述有误） |

### 7.2 OPEN

| # | 项 | 待决 | 建议默认 |
|---|---|---|---|
| **OPEN-C1** | **`transition_count` 的时间炸弹**：约 **2026-09-25** 前后，0813–0826 那 10 个「状态机从未处理过」的原始日全部滚出 20 日窗口，`transition_count` 落到 ≈4–6，**横躺在阈值 5 上** ⇒ 仓位 ×0.8 与 `range_reversal` 会自行跳动 | 何时修（窗口限定为状态机生效日之后 + 重标阈值）。**本轮只做可观测化（C-P10）**。⚠️ 09-25 这个日期按**旧口径**推得：A-P0-2 会改 `load_regime_state_history` 的排除当天语义 ⇒ **A 层落地后须重算倒计时**，否则专项会按错日期排期 | 在 09-25 之前开专项；用 C-P10 的 `transition_count_raw_segment_days` 监控倒计时 |
| **OPEN-C2** | **M3 的人工流程**：误配率 ≤15% 是唯一的人工指标，但 0903 只写了「≤10 分钟/日」没有流程定义 | 抽查样本怎么抽（全量 32 个？分层抽样？）、判据是什么、谁判 | 分层抽 10 个（「其他」回收的 5 个 + 冲突的 3 个 + 随机 2 个），把「该事件与该板块无实质传导关系」写成三档（无关系 / 弱关系 / 强关系），只把「无关系」计入 |
| **OPEN-C3** | **`persistence_regime == "low"` 也是 11/11 恒真**（§2.7）——`range_reversal` 的两个条件**都是常数** | 它是 Node 5 的产物还是被算成了常数？本轮只记录了 `transition_count` 侧 | 与 OPEN-C1 同批排查：查 `sentiment_analysis.py` 的 `persistence_regime` 计算路径 |
| **OPEN-C4** | **M1 的信息量到底有多大**：glue 侧 34.4% vs 规则侧 3.1%，11 倍提升；但 0903 想把 T5 的 0/24 修到 60% | 34.4% 是否已够（红线该定在哪），还是 prompt A/B 的 B 版能把它推到 60%+ | 由 C-P12 的曲线回答，**不预设** |
| **OPEN-C5** | **`agent_glue.json` 的当日覆盖语义**：A-P0-8 已定「末版为准，`agent_glue.json` 不再保留」 | 与 C-P3 的回填产物（`_replay/agent_glue.json`）是否同语义 | 是——回填产物同样按 `{date}/_replay/` 覆盖；「末版为准」在 `_replay/` 内同样成立 |

### 7.3 对既有文档的修订请求

| # | 文档 | 位置 | 修订 |
|---|---|---|---|
| 1 | `agent_glue_plan_0903.md` | §8.3 V1/V2 | 标注「**已在 `a5fdc64` 落地**」；删除「V1+V2 是 A5 硬阻塞」的结论；补「`replay_mode` 显式跳过 glue（`aggregator.py:965-967`），故回放不能重建影子样本」与「`_save_glue_result` 用 `date.today()`（`:1085`），跨日回放会落错目录」 |
| 2 | `agent_glue_plan_0903.md` | §8.1 M1/M6 数据源 | 改为 glue 侧（`strengths[].news_read` / `families[]`）；并补 M1/M6 的规则侧对照列 |
| 3 | `agent_glue_plan_0903.md` | §8.1 M4 | 增列「系数 = Spearman」；增注「M4-a 独立性以 B1 修复为条件」；M4-a 红线标「待标定」 |
| 4 | `agent_glue_plan_0903.md` | §8.1 M5 | 补「以**当日最后一次运行**的 `status` 为准」 |
| 5 | `agent_glue_plan_0903.md` | §8.1 M2 / §7.2 prompt | 显式记录 M2 红线与 prompt 指令的**反向关系**及 M2↔M3 的拉锯，指向 D-C6 的 A/B |
| 6 | `agent_glue_plan_0903.md` | `_log_shadow_comparison` 相关表述 | 删除「M4-b/M4-c 度量源」的说法（该函数将随 C-P4 删除） |
| 7 | `got_run_model_plan_0911.md` | §7.1 C 行 | 替换为指向本文档的指针，并标注原表述被修订/升格 **3** 条（§2.9 第 **1–3** 行）。⚠️ 原写「**4** 条（§2.9 第 1–4 行）」**多算 1 条** —— §2.9 第 4 行的来源是「0903 自述」而非 A 层 §7.1 C 行；A 层 `:409` 亦写作「**三条**」，二者现已一致 |
| 8 | `got_layer_b_plan_0911.md` | §7.3 或 §2.8 | 交叉引用：**C3 的共线根因是 B1**，B1 的修复状态直接决定 M4 是否退化为一条红线 |
| 9 | `got_layer_d_plan_0911.md` | 已落地 | 交叉引用（**已完成**）：C3 的共线根因链起点是 `associated_sectors` 恒空 ⇒ `sentiment_check` 恒 `low` ⇒ B1 ⇒ M4 共线；同一处空值还使埋伏闸门 **G2 的注意力子闸 / G3** 失效（G2 的已发酵子闸仍活）。**C-P1…C-P5 与 D-P1…D-P5 的可观测性修复应同批实施** |

---

## 附录 A：0910 实测数据一览（修复前基线）

| 量 | 值 |
|---|---|
| 候选板块数 | 32 |
| 规则侧 `frame_group` 分布 | 其他 11 / AI硬件 7 / 农业 5 / 消费 3 / 医药 3 / 周期资源 2 / AI软件 1 |
| 规则侧 `news_check` | neutral 31 / catalyzed 1 |
| 规则侧 `capital_check` | neutral 31 / aligned 1 |
| 规则侧 `sentiment_check` | **low 32 / 32** |
| `classification` | actionable 17 / caution 15 |
| glue `strengths` | 32 条，`degraded=False`，`warnings=[]`，`prompt_bytes=34946` |
| glue `relations` | 25 条，覆盖 9 个板块 / 14 个 `event_id`（归档 events 30 个 ⇒ 事件侧覆盖 46.7%）|
| glue `families` | 32 条：AI硬件 11 / 消费 6 / 周期资源 5 / 农业 4 / 医药 3 / 军工 2 / 无归属 1 |
| glue vs 规则家族 | 一致 18 / 冲突 3（供销社、航运概念、OLED概念） |
| 「其他」回收 | 10/11（陶瓷 880345 判「无归属」） |
| `ρ(glue, tech)` | +0.591 P / +0.419 S |
| `ρ(glue, composite)` | +0.646 P / +0.500 S |
| **`ρ(tech, composite)`** | **+0.985 P / +0.965 S** |
| `mean\|composite − tech\|` | **2.56 分**（tech sd = 25.3） |
| glue vs tech 的 Δ | 中位 −12.4；`\|Δ\|>10` 占 25/32 = 78.1%；max +31.0（光刻机）/ min −41.8（旅游景点）|
| `transition_count` | 11（其中 7 次来自 0827 前未经状态机的原始段）|
| `persistence_regime` | low |
| 六条红线达标数 | **0/6**（M1 死锁 3.1% / M2 28.1% / M3 未测 / M4-a 0.591 不达标 / M5 不可度量 / M6 死锁 65.6%）；门禁自锁 **2**（M1、M6）；实测不达标 **2**（M2、M4）；不可度量 **1**（M5）；未测 **1**（M3）。⚠️ **M4-b / M4-c 是 M4 的两个子指标，不是两条独立红线** —— 不能计入「六条」的分子 |

## 附录 B：本文事实核查约定

沿用 `agent_glue_plan_0903.md` 附录 B：所有代码断言带文件路径 + 行号，均为 **HEAD `a803e09`** 的只读核查结果；
若实施时代码已变动，**以代码为准，不以本文为准**。

§1 与附录 A 的数字均由只读 join 计算得出，**可用 `scripts/verify_glue_metrics.py` 逐位复现**（R-C3）——
这是本文档与 0903 §8.1 的关键差别：0903 的红线是**设定**的，本文的数字是**算出**的。

§2.9 记录本轮被修订/升格的 **5** 条 + 被推翻的 **5** 条（共 **10** 条；其中 6 条源自 0903、3 条源自 A 层 §7.1、1 条源自上一轮的选项设计），
§2.10 记录 8 项防误修，§7.2 记录 5 项 OPEN，供后续开专项时直接接手。
