# GOT 统一实施方案：语义粘合层重构 + 远端模块整合（Agent 定强度、规则守准入）
> **原位置**：`docs/archive/got/agent_glue_plan_0903.md`（2026-09-17 文档结构重排移入当前位置）

> **本文是 [`grilling_0903_solutions.md`](./grilling_0903_solutions.md) 的重写版，并已整合
> [`remote_integration_plan.md`](./remote_integration_plan.md) 的实施路线图，是 GOT 演进的单一入口。**
> 原方案基于 0903 输出反推了 10 项漏洞并给出 10 套并列的评分调参方案；经 grilling 逐项对账后确认：
> **其中 6 项断言与代码事实不符，3 项方案存在惰性错误，验证框架整体虚构。**
> 更关键的是，原方案没有识别出真正的单一根因，因此 10 套调参方案里有 7 套是在修补症状。
>
> 本文遵循 [`remote_integration_plan.md`](./remote_integration_plan.md) 的既有约定：
> 事实核查修正块、影子模式先行、**可计数的**强制验收红线、可复用性三分类（§14.3）、
> 以及治理原则「规则守决策、Agent 做证据收集与语义仲裁」（§15.4）。
>
> **§0-§9 是 glue（Node 6 纵向重构）的完整设计；§10 把 glue 与 remote 的远端模块整合合并为单一路线图**
> （含跨文档冲突裁定 + 模块地图 + Phase 0-5 排序）。remote 各模块的实现细节仍见其原文，本文只整合顺序与依赖。
>
> **状态：Phase 0/1/2/3a/3c/4a/4b/4c 已实施完成（2026-09-04）。**
> Phase 0 共同地基（A1 回放 harness + A2 call_type/超时/temperature）✅；
> Phase 1 A3（`_collect_sector_candidates` 抽出）✅、A4（`_agent_semantic_glue` 实现）✅；
> P0-B 数据完整性（B1 字段级检查 + B2 护栏空转日志）✅；
> A5 影子基础设施（`[SHADOW]` 对照日志）✅；
> Phase 2 §3 TencentFetcher（指数 K 线 fallback 源）✅、§4 指数注册表 + 身份解析 ✅；
> Phase 3a §15 T1/T1-H 审查层（review profile + try_parse_json + 持仓降档）✅；
> Phase 3c §14.1 A1/A2 AgentMemory 修复（avg_confidence 真实计算 + outcome 惰性回填）✅；
> Phase 4a §6 Intelligence 情报系统（2 SQLite 表 + IntelligenceService + Node 4 集成）✅；
> Phase 4b §9 canonical_id 双写（`StockDaily.canonical_id` + `generate_canonical_id`）✅；
> Phase 4c §5 MarketThemeContext Step 1（模型定义 + unknown 默认构造）✅；
> **下一步：A5 + T1/T1-H 双影子运营（≥10 交易日）→ A6 转正+退役 → §8 分歧摘要 + §5 Step 2。**
> 详见文末 §11 实施进度追踪。
> 全部决策由项目负责人在 grilling 会话中逐条确认（见 §4、§10.1）。

---

## 0. 结论摘要

0903 的板块消息面命中率是 **0/24**。根因不是阈值没调好，是**四套互不兼容的板块词表**做子串匹配，
在结构上不可能命中。为此项目在过去一个月里叠了三层护栏修补它，而每层护栏自身又引入新偏差，
形成死循环（§3）。

**解法**：废除语义归属侧的全部映射资产，由 Agent 单次全量调用输出「事件→板块关系图 + 板块强度 +
家族归属」；规则侧保留全部准入护栏（TOP-N / 过热 / 拥挤拦截 / 组继承 / 硬拒绝）。

即 **Agent 定强度，规则守准入**。

这条边界之所以成立，有一条决定性的代码事实支撑：

> `_compute_market_regime`（`aggregator.py` L334-360）的取数全部来自 5 个节点的**全局聚合字段**
> （`market.tech_growth.score` / `sector.market_direction` / `capital.institutional_score` /
> `news.impact_score` / `sentiment.fear_greed_index`），**不消费 `sector_intels`**。
>
> → `regime` / `risk_budget` / `dwell_days` / `transition_count` / `exit_pressure` 整条跨日状态机
> 与板块 miniGOT 的输出**无数据依赖**。**改造 miniGOT 不污染跨日连续性。**

**与 `remote_integration_plan.md` 的关系（本次整合新增）**：两份文档的实施顺序存在**交叉**（glue 与 remote §8
同改 `aggregator.py`；remote §4 与 glue OPEN-4 同改 `market_analysis.py`）与**共享基底**（超时 / call_type 落库 /
回放 harness / temperature 传参是双方的前置）。故合并为 §10 的单一 Phase 0-5 路线图：**Phase 0 共同地基最先做**，
glue 接线（Phase 1）与 remote P0 数据源（Phase 2）并行，remote §8/§15（Phase 3）排在 glue 转正之后。
经核查，remote §14 有 2 处「现状问题」描述已被 glue 的代码核查推翻（call_type、超时），须按 glue 修订（§10.1 A 类）。

---

## 1. 触发事件：T5 转正 48 小时后命中率 0/24

`src/got/catalyst_config.py` L159-163：

```python
SECTOR_ALIGN_SHADOW = os.environ.get("SECTOR_ALIGN_SHADOW", "false").lower() == "true"
  # T5: 事件→板块对齐层（2026-09-01 转正；影子观察已验证无异常，回滚设 SECTOR_ALIGN_SHADOW=true）
```

- **2026-09-01**：T5 事件→板块对齐层转正。验收理由是「影子观察已验证无异常」。
- **2026-09-03**：24 个确认板块的消息面校验**全部**返回 `neutral` + `"无直接消息催化"`。命中率 **0/24**。

T5 的实现是**映射，不是语义**：

```python
# aggregator.py L2100-2146
def _group_events_by_sector(events):                 # T5 扇出
    from src.got.board_aliases import normalize_sector_name
    key = normalize_sector_name(raw_sector)

def _lookup_sector_events(name, events_by_sector):   # A2：精确 → 别名变体 → 空
    result = events_by_sector.get(name)
    if result: return result
    from src.got.board_aliases import sector_name_variants
    for variant in sector_name_variants(name): ...
    return []
```

### 1.1 从这次失败中提取的两条治理教训

**教训一：验收标准错了。** 「影子观察已验证无异常」= 「没崩」，不是「有效」。
本方案的红线必须是**可计数的命中率**（§8）。

**教训二：这一层根本没用 Agent。** 它用 `normalize_sector_name` + `sector_name_variants` +
`CONCEPT_UNIVERSE._match_sector_hint` 三张表做归一化。所以失败不能归因于「用了 LLM」，
恰恰相反——**该用语义的地方用了查表**。

---

## 2. 事实核查修正

### 2.1 对 `grilling_0903_solutions.md` 十项断言的逐条对账

| # | 原方案断言 | 代码/数据事实 | 判定 |
|---|---|---|---|
| 1 | `_cross_check_news` 是扁平子串匹配，需改为分级 | 它**已经是**分级修正：`_NEWS_POS_ADJUST={high:12,medium:8,low:4}`、`_NEWS_NEG_ADJUST={high:-18,...}`、多源 ×1.3、global ×1.3、fading ×0.5、上限 +20/−24（L1009-1107）。0903 真实故障是 **24/24 走零命中分支**（L1044-1045） | **描述过时** |
| 2 | 催化剂闸门 `CATALYST_CONFIDENCE_GATE=0.7` 太严，应降到 0.6 | 107 条事件里 **≥0.7 有 69 条**（64.5%），≥0.6 有 102 条。闸门不是瓶颈。真瓶颈是**只有 36/107 带 `affected_sectors`**（33.6%），且标签是产业级粗粒度 | **根因错，降门槛无效** |
| 3 | 当前仓位 100%，与 `position_limit=0.631` 矛盾 | 实际是 **376%**，被 `portfolio_decision.py` L273 `min(1.0, current_position)` **静默夹取**成 1.0。「100%」是夹取后的假象 | **前提错** |
| 4 | `base_position` 传入后被忽略 | **确认，且比原方案说的更严重**：`base_position` 在 `aggregator.py` 全文**只出现 1 处 = L2464 的形参**（`_compute_position_from_regime` def@L2461），函数体 L2474-2510 从未引用。Node 1 的 `position_limit` 是死输入。<br>⚠️ 勿与 `portfolio_decision.py` 的**同名不同参数**混淆：那里 L327/L338/L358 属 `_apply_drawdown_scaling`，L358 `scaled = base_position * multiplier` **真实使用** | **确认 + 加重** |
| 5 | `contrarian_signal` 是死字段 | **原方案对，我方 grilling 中的反驳错**。存在**两个同族字段**：<br>① `SentimentContext.contrarian_signal`（schemas L560，0903=`False`）→ `aggregator.py` **0 个消费者**，全 `src/` 仅生产者 `sentiment_analysis.py` L210/L215-217/L225 ⇒ **真死字段**；<br>② `SentimentStageState.contrarian_alert`（schemas L532，0903=`False`）→ L2548/L2577/L3907 **有消费**，但消费者只有 `archive.py` L445 的归档渲染与 L3926 的监控 JSON ⇒ **展示态**。<br>我方曾引 L2578 `components = {"contrarian_signal": 0.75, ...}` 当作「有消费方」——那是 dict 的**字符串键**，不是读取 | **确认（分两个字段）** |
| 6 | `broken` 判据的 `one_day_drop<=-4%` 误伤，应加 `today_change>2.0` 缓冲 | 该项**已于 2026-09-02 P0-3 移除**（`sector_analysis.py` L2272-2292 附 29 日校准理由）。提议的缓冲对原方案引用的三例（+1.69% / −0.72% / −2.45%）**一个都不生效** | **修的是已删除的代码** |
| 7 | `FAMILY_KEYWORDS` 是 dict，应改分组逻辑 | 它是**有序 `list[tuple]`**（L164-168），首个命中即返回；`_build_frame_group` 是**模块级函数**（L582-587）非方法。原方案 §8.4 的代码写法会破坏优先级语义 | **类型错** |
| 8 | Node 1 `tech_growth` 加第 3 个指数，「5 行改动」 | `market_analysis.py` L341-352 `zip(scores, weight_values)` 会**静默截断**第 3 个：新指数进 `constituents`（L383）和一致性检测（`signs`），但**权重为 0** | **引入更隐蔽的 bug** |
| 9 | §2.4 方案 A 的 composite 修正公式 | 该公式**重复计入催化剂**：`composite += catalyst_adjust` 之后又 `+= max(news_adjust, catalyst_adjust)` | **代码错** |
| 10 | §12.3 验证框架：`--replay-date` 回放 + `tests/got/` 单测 | `--replay-date` **不存在**；`tests/got/` 目录**不存在**；GOT 节点**无任何回放 harness**（只有只读审计脚本 `scripts/replay_node2_states.py`） | **整体虚构** |

**附加核出的事实（原方案未提及）：**

| 事实 | 位置 | 影响 |
|---|---|---|
| `llm_refinement_used=True` 是**硬编码** | `aggregator.py` L299 | L1385-1387 `except` 降级后仍写 `True`。不只是死字段，是**说谎字段** |
| `_build_data_quality` 的 `missing_fields=[]` **恒空** | L3258-3284 | 原方案问题 8 判断正确。降级无法被观测 |
| 原方案 §2.1/§3.1 的分母错 | — | 当日 confirmed 是 **24** 个不是 10 个；资金 neutral 是 **21/24** |
| §3.4 声称 tanh 映射到 [-15,+12] | — | 实算为**对称 ±12.2**；且 `adjust *= 0.4` 处理 generic/narrow 会**反转 2026-09-02 A6 的刻意决策** |
| `AMBUSH_SCORE_FLOOR=0.30` 的文件标错 | 实际在 `sector_analysis.py:2559` | 原方案 §6.2 标为 `aggregator.py` |
| `agent_orchestrator_timeout_s=600` | `config.py` L778-791 | `remote_integration_plan.md` §14.2 B1 说「默认 0」是**过时事实**（但 `agent_arch="single"` 下不走 orchestrator，600 也不生效） |
| **`sentiment_check` 0903 = `"low"` × 24/24**，`sentiment_detail` = **`"无热榜数据"` × 24/24** | `unified.json` | 情绪面维度与消息面**同型 100% 空转**。原方案与本文初稿**都漏了这条**（只统计了 news/capital）。详见 §5.2「情绪侧」与 §8.1 M1' |
| **`trading_signal` 是全仓库死字段** | `schemas.py` L541；生产者 `sentiment_analysis.py` L195/L1327 | 0903=`"neutral"`。全 `src/` **0 个消费者**（`aggregator.py` 0 命中）。两份源文件均未提及 |
| **`sentiment_dispersion` 在 Node 6 无消费者** | `schemas.py` L556；0903=**0.094** | 原方案 §7.2「死字段」判断**正确**。生产者 `sentiment_analysis.py` L158/L209/L879 |
| **资金流底表规模与代码注释不符**：`sector_flow_all` 0903 实测 **90 条** | L622 注释写「A4: 全量 **464** 板块」；L923 P0-1 注释写「资金流表有 **476** 个板块」 | 两处注释**均与实际不符**。三表规模 1d=90 / 3d=477 / 5d=362，且 **1d ⊂ 3d、5d ⊂ 3d**（union=477）。详见 §5.2「资金侧」 |
| **候选在资金流表的精确同名命中率：1d 3/24、3d 12/24、5d 11/24** | `_parse_sector_level_flows` L2749 只读 `sector_flow_all`（1d，90 条） | ⇒ 资金面底表本身只覆盖 **3/24**。P0-1 宣称的「匹配率 33%→80%」是**靠子串匹配一张 90 条的表**得到的 |
| **`_CAPITAL_DIVERGENT_MIN_SAMPLES=60` vs 当日 n=90** | L889 / L908-909 | 0903 勉强走分位路径；**任何一天 <60 条即静默回退到 `-80.0` 绝对阈值，无告警**。该常量随 §6.1 #9 退役，但退役前是活的 |

### 2.2 对 grilling 过程中我方错误推荐的自我纠正

遵循 `remote_integration_plan.md` §5「事实核查修正块」约定，公开记录设计过程中的错误：

| 我方推荐 | 被什么推翻 | 修正 |
|---|---|---|
| 建一个 `sector_alias` 归一化层（产业级→板块级） | 负责人否决：「映射试了一个月了错漏太多，而且映射处理不了扩散之类的逻辑关联」 | 接线层**禁止任何映射**，含自动派生的桥 |
| 用东财 BK 成分股做集合运算 | `remote_integration_plan.md` §1 实测表：**东财板块排名 ❌、push2 单股 ❌**；`get_board_constituents` 名字骗人（实为板块排名，仅走东财 push2，THS 已移除，`base.py` L545-555 / `akshare_fetcher.py` L2617-2627）；全仓库**无 TDX 成分股解析器**（`get_and_parse_block_file` / `blockname` / `BlockData` 全部 0 命中） | 唯一活路是问财 NL + `data/ths_concept_boards.json`（当天自动刷新、不截断） |
| 成分股签名法派生桥（带 confidence、可自愈） | 负责人否决：「桥我也不想做」 | 不做任何形式的 `code↔name` 表，包括自动生成的 |
| 担心 Agent 输出非 stationary 会毁掉跨日状态机 | `_compute_market_regime` **不消费 `sector_intels`**（§0） | 该担心在板块层不成立，(乙) 可行性大幅提升 |
| `sector.json` ~88K tokens | 192,842 B 是**磁盘缩进后**大小；紧凑 JSON 实为 **134,931 B ≈ 33.7K tokens** | 已更正（§5.2） |
| `catalyst_calendar` 是「107 条列表」 | 它是 **dict（7 个 key）**，`catalyst_calendar.events` 才是 107 条 | 已更正（§5.2） |
| 「Agent 化之后 composite 语义变更会撞旧阈值」 | 实测**旧阈值现在就已半失效**：埋伏绕过 TOP-N（L651-655），`旅游景点 99.1→caution` / `物业管理 54.9→actionable` / `汽车整车 35.7→actionable` | 严重性下调，**B3 从「推荐」升级为「必选」**（D10） |
| 「删 `family_resonance`」 | 有**两个**同名字段：`SectorIntel.`（24/24 全 None，死）与 `MainlineEndurance.`（L2996-3003 活代码，续航支撑信号 ≥0.7） | 只删前者（D7） |
| 🔴 **写作阶段错误：曾在本文 §9「公开撤销 `base_position` 是死参数」** | 撤销时查错了**文件**：查的是 `portfolio_decision.py` L327/L338/L358（`_apply_drawdown_scaling`，真实使用），而原方案与 §2.1 第 4 项指的是 **`aggregator.py`** L2464 | **撤销作废**。`aggregator.py` 的 `base_position` 全文仅 1 处 = 形参，**确实是死参数**。§9 的错误撤销块已删除 |
| 🔴 **写作阶段错误：`contrarian_signal` 与 `contrarian_alert` 混淆** | 与 `family_resonance`、`base_position` **同一类错误，第三次**：把 `SentimentContext.contrarian_signal`（顶层，真死）当成了 `SentimentStageState.contrarian_alert`（嵌套，展示态），并误引 L2578 的 dict 字符串键作为「有消费方」证据 | §2.1 第 5 项与 OPEN-2 已按**两个字段分开**重写。原方案 §7.2 的判断是对的 |
| 🔴 **写作阶段错误：OPEN-5 Y3「`_is_hard_exclude` 的 lifecycle 分支全量空转」** | 复核：sector 侧 `lifecycle` 在走 `_classify_all_sectors` 的 **16 个候选上 16/16 非空**（`active_mainline` 11/11 + `cooling_down` 5/5）；ambush 8 个为 null 但 L640-644 **已将其排除**，根本不进 `_is_hard_exclude`。原方案说的「13/13 null」是 **`news.json` `major_events[].lifecycle_state`**，唯一消费者是 `_cross_check_news` L1076 的 fading ×0.5 | **Y3 作废重写**（见 OPEN-5）。护栌 P5-1 **正常运作**，不存在「实际不存在」的问题 |
| 🔴 **写作阶段遗漏：§5.2 输入契约与 §5.3.2 输出契约断裂** | §5.1 Segment C 把 `_cross_check_capital` / `_cross_check_sentiment` 交给了 Agent，§5.3.2 又要求输出 `capital_read` / `sentiment_read`，但 §5.2 **只喂了 `sector.json` + `news.json`** —— 无输入却要输出 | 已补齐资金侧与情绪侧（§5.2），并经负责人确认：喂 **`sector_flow_all_3d`（477 条）+ 瘦身 `hot_stocks`（50 条）** |

### 2.3 事实核查修正块（2026-09-10）：mootdx 已修复，§2.1 第 1 项技术前提部分失效

> 沿用 §2.2 约定。**§2.1 第 1 项与 §3.1 的部分技术前提已失效，但两者的结论均不变** ——
> 变化的是"有没有别的路"，不是"该不该建表"。

#### 变更 1：mootdx 从「全废」变为「可用」

§2.1 第 1 项与 §3.1 均以 mootdx 不可用为前提写成。2026-09-10 实测：

| 项 | 实测结果 |
|---|---|
| 根因 | **不是通达信协议更新**，是 mootdx 内置 `HQ_HOSTS` 38 台名单（末次更新 2024-07）整批失效 |
| 网络环境 | 裸 TCP 出口 `36.28.211.141`（浙江电信），**无代理、无 TUN** —— 排除网络环境因素 |
| 38 台内置 | **0 台能取 K 线**（14 台 TCP 通但回空表，24 台连不上） |
| 73 台外部候选 | **9 台可取数**：国泰君安 `117.34.114.13/14/15/16/17/18/20/27` + 安信 `59.36.5.11` |
| 修复 | `data_provider/mootdx_fetcher.py` 弃用 BESTIP（它只在陈旧名单内选优），改 **pin 验活主站池 + 真实取数验活 + 顺序故障转移** |

**副产品**：`index_bars('880xxx')` 板块指数 K 线恢复（含 `up_count` / `down_count`）；
`stock_all()` 返回 51,989 行含 1,120 个 `88` 开头板块指数。
Node 2 板块 K 线 **`0/112` → `217/217`**，GOT 全流程由 `exit 1` 恢复为 `exit 0`。

#### 变更 2：TDX 板块成分股解析器**存在**（§2.1「0 命中」是搜索范围局限）

§2.1 第 1 项的判据是「全仓库 0 命中 `get_and_parse_block_file` / `blockname` / `BlockData`」——
搜索范围是**本仓库**，而实现在 **site-packages**：

| 层 | 位置 |
|---|---|
| 入口 | `mootdx.quotes.StdQuotes.block(tofile=...)` |
| 协议 | `tdxpy.hq.TdxHq_API.get_and_parse_block_info` |
| 解析 | `tdxpy/reader/block_reader.py` → 产出带 **`blockname`** 字段的 DataFrame（即 §2.1 判为「0 命中」的那个字段） |

#### 变更 3：但该通路**当前不通**，卡在 tdxpy 一处 `or` 写法

```python
# tdxpy/reader/block_reader.py:41
data = (type(name) is bytearray) and name or Path(name).read_bytes()
```

服务器返回空块时 `file_content` 是**空 `bytearray`**（falsy）→ `... or Path(name)` 被求值
→ `TypeError: expected str, bytes or os.PathLike object, not bytearray`。
**空响应被掩盖成类型错误**；非空时该链可用。属 §3.3 同型的「静默失败伪装成别的问题」。

#### 变更 4：东财 push2delay **部分可用**（§3.1「数据源已被实测封禁」需限定主机）

| 主机 / 端点 | 结果 |
|---|---|
| `push2.eastmoney.com` | 连接被拒（`RemoteDisconnected`） |
| `push2delay.eastmoney.com` 板块排行 | **可用**：`fs=m:90+t:3` → 300 条 |
| `push2delay.eastmoney.com` 板块成分 | **可用**：`fs=b:BK0729+f:!50` → `total=15` |
| `push2delay.eastmoney.com` + `kline/get` | **不可用**：板块与个股均返回 `dktotal=0` / `klines=[]`（该主机是延迟**行情**主机，不供历史 K 线） |

代理（US 出口）与直连均实测，结论一致。

#### 变更 5：`_prefetch_constituent_stats` 实测映射覆盖率（补 §3.1「手写映射不可行」证据）

| 项 | 实测 |
|---|---|
| TDX registry | 112 个板块 |
| 东财板块表 | 300 条 |
| `sector_name_variants` 命中 | **36 / 112 = 32.1%** |
| 未命中样例 | `芯片` `玻璃基板` `EDA概念` `第三代半` `液冷服务` `铜缆高速` `人形机器` `机器人概` … |

未命中项**全部落在 §3.1 已记录的「TDX 8 字节 GBK 定长 → 4 汉字硬截断」规律内**。

**结论不变：不修映射**（撞 §2.2「不做任何形式的 `code↔name` 表」）。
且 `_prefetch_constituent_stats` 的失效是**设计内降级**——`sector_analysis.py:1234` 明写
「D7 回退 `today_change` 代理、D9 回退宽度代理，**方向安全：0 = 无过热**」，
故 `0/25` 的后果是**过热维度退回代理口径**，非故障。

**唯一确认待修项**：`sector_analysis.py:1316` 把失败原因吞在 `logger.debug` —— 降级可以，原因应可见。

#### 由此重新变得可行的选项

变更 1 + 2 使「**用 TDX 自身取板块成分股**」从「不可行」变为「可能」（前置：先过变更 3 的 tdxpy 缺陷）。
该路线**不需要任何 `code↔name` 表**——TDX 的 `blockname` 与 TDX 板块代码同源，**不违反 §2.2 的禁令**。

> ⚠️ 这是本修正块唯一需要裁决的项：**启动该路线，还是按 §2.1 等问财路线接线**
> （`data/ths_concept_boards.json` 已在每日刷新，实测 2026-09-10 17:59 写入 7,604 B，
> 但全仓库仅 `src/services/board_name_resolver.py:41` 在读，**Node 2 尚未接**）。

---

## 3. 根因：三层护栏修补同一个映射失败

### 3.1 四套词表 + 物理截断

| 词表 | 来源 | 特征 |
|---|---|---|
| TDX 板块名 | 通达信 8 字节 GBK 定长 | **4 汉字硬截断**：`全国性银` / `第三代半` / `装配式建` |
| `TDX_BOARD_REGISTRY` | `data_provider/tdx_board_mapping.py`，手写 112 条 | 自身携带截断名与**错误分类**：`"880608": {"name": "第三代半", "category": "eda_ip"}`（应为半导体材料）。名字长度分布 `{4字:65, 2字:18, 3字:15, 5字:6, 6字:4, 7字:2, 1字:2}` —— 65/112 恰好 4 字，是截断的统计学证据 |
| 东财 BK 完整名 | push2 | 不截断，但**数据源已被实测封禁** |
| 同花顺问财词表 | `data/ths_concept_boards.json` | 当天自动刷新、不截断；含 `5G` / `AI PC` / `IP经济(谷子经济)` 等**同样过不了 `min_substr_len=3`** 的名字 |

0903 `sector.json` 实测：112 个板块名，**106 个 ≤5 字**。

### 3.2 护栏与映射表互相拆台

```python
# aggregator.py L2827-2835
_GENERIC_ROOTS = frozenset({"AI","芯片","电力","新能源","医药","军工","消费","金融",
                            "周期","地产","汽车","数据","软件","硬件","网络","通信",
                            "光伏","储能","机器人","智能","科技","信息","生物"})
def _match_sector(name, target, min_substr_len=3): ...
```

→ 事件标签 `"AI"`（2 字）被 `min_substr_len=3` 直接拒；`"金融"` / `"半导体"` 命中 `_GENERIC_ROOTS` 被降权。
**映射表里写的短词，被护栏主动拒绝。**

### 3.3 死循环的完整因果链（物证：2026-09-02，即 T5 转正次日）

`_lookup_capital_flow`（L913-950）的原始注释：

```python
# P0-1（2026-09-02 审核）：原实现只用 dict.get + 别名循环，实测 0902
# 15 个确认板块仅命中 5 个（33%）——资金流表有 476 个板块且命名口径与
# Node 2 不同（"旅游" vs "旅游概念"、"橡胶" vs "橡胶制品"）。
# 改为复用统一匹配入口 _match_sector，子串门槛放宽到 2 字后匹配率 33%→80%。
#   2. "narrow"（目标比查询更窄，如"化工"→"磷化工"）与 generic 同等降级：
#      窄类的资金流不能代表宽类，否则以偏概全（实测 11/15 通用名会误配）
hit, match_type = self._match_sector(name, flow_name, min_substr_len=2)
```

```
① 映射匹配率 33%
② → 降门槛 3字→2字，匹配率提到 80%
③ → 但 11/15 = 73% 通用名误配
④ → 加 A6 护栏：generic/narrow 强制 neutral（_UNRELIABLE_MATCH_TYPES，L130-135）
⑤ → A6 把 divergent(−15) 转成 neutral(+0) = 单向利多，白送 15 分
⑥ → 加第二层护栏 _CAPITAL_ALIGNED_MIN_YI=1.0（L892-896：0902「橡胶」子串匹配到 0.02 亿
     被判 aligned 并加 10 分；注释原文「匹配率从 33% 提到 80% 后此类噪声必然暴露」）
⑦ → 0903 结果：旅游资金净流出 0.6 亿，narrow 匹配 → 强制 neutral → 99.7 分
```

**三层护栏全在修补同一个映射失败，而每层护栏自身又引入新偏差。** 这就是「映射试了一个月」的完整病历。

### 3.4 0/24 的机制证明：子串匹配在结构上不可能命中

事件侧 `affected_sectors` 共 **53 个标签**，板块侧候选 **24 个名字**。

**精确同名命中：只有 3 个** —— `AIGC概念`(4次)、`汽车整车`(3次)、`短剧游戏`(2次)。

其余 50 个标签对应的传导关系人一眼就懂，但任何子串/别名/embedding 方案都救不了：

| 候选板块 | 相关事件标签 | 关系 | 规则为何失败 |
|---|---|---|---|
| `光刻机` | `半导体设备`(1) | 光刻机**是**半导体设备 | 互不为子串 |
| `航空` | `航天航空`(1) | **"航空" 是 "航天航空" 的子串** | 长度 2 < `min_substr_len=3` → **护栏主动拒绝** |
| `华为鸿蒙` | `华为手机`(1) | 同属华为链 | 互不为子串 |
| `OLED概念` / `玻璃基板` | `折叠屏`(1) / `消费电子`(3) | 折叠屏用 OLED + 玻璃基板 | 互不为子串 |
| `第三代半` | `半导体`(3) / `AI芯片`(4) / `国产芯片`(1) | TDX 截断名 = 第三代半导体 | 字面交集只有一个「半」字 |
| `数字水印` / `知识产权` | `AI应用`(5) / `多模态AI`(4) | 版权保护 ← AIGC 内容治理 | 互不为子串 |

**`航空` 这一行是决定性的**：它本来**能**命中，是护栏把它拒了。其余 5 行需要的是**产业知识，不是查表**。

> 板块名截断对人是灾难，**对 LLM 不是**——「第三代半」它读得懂，`_match_sector(min_substr_len=3)` 读不懂。
> 这正好解释了为什么 Agent 方案成立而映射方案不成立。

---

## 4. 核心决策链（已确认）

| # | 决策项 | 结论 |
|---|---|---|
| **D1** | 范围重排 | 先接线后调参：**P0-A 接线** / **P0-B 数据完整性** / **P1 调参**。原方案 10 项并列调参重排为此三段 |
| **D2** | 接线层禁映射 | 废弃一切 `code↔name` / `事件标签↔板块名` 人工表，**含自动派生的桥** |
| **D3** | 归属机制 | **A+C**：LLM 抽实体与传导链 + 代码算 exposure。**允许问财自然语言查询** |
| **D4** | 粘合层权限 | **(II) 关系层** → 细化为 **(乙) 强度替换、准入保留**（§5.1） |
| **D5** | Agent 输入 | **只喂状态机候选**（24 个 / 31,600 B = 23.4%）。全板块输出是规则侧备份（§5.2） |
| **D6** | 家族归属 | **D3+D1 混合**：`FAMILY_KEYWORDS` 留 Node 2 规则侧；落「其他」的 11 个交 Agent 判（§5.3） |
| **D7** | `family_resonance` | **只删** `SectorIntel.family_resonance`（24/24 全 None）；**保留** `MainlineEndurance.family_resonance` |
| **D8** | 续航公式 | **E2**：`1 − std_dev/50.0` 的分母 `50.0` → **当日全体候选 composite 的标准差** |
| **D9** | 调用契约 | **(甲) 单次全量调用** + **F1 降级到纯技术分**，显式标记（§5.4 / §7.2） |
| **D10** | 阈值 | **B3 排名不变式**：删除 `score>=50`（TOP-N 准入）与 `composite<40`（hard_exclude）两个**绝对**闸，只保留排名 + 护栏 + Agent confidence 门限 |

### 4.1 D4 的备选方案与否决理由

- **(甲) 全量替换**（Agent 直出 composite + classification，删 `_compute_sector_scores` 和 `_classify_all_sectors`）
  → **否决**：`_classify_all_sectors` 那 6 条护栏每条都带校准日期（§5.1 表），是防高位接票的物理闸。
  **Agent 是会被说服的**——一只过热板块在叙事上完全能被论证成主线；护栏不会。
- **(丙) 现状扩权**（保留 `_llm_refine_sectors`，放开「只上调」为双向，`REFINE_MAX_SECTORS` 提到 24）
  → **否决**：不解决 0/24。病灶在三个 `_cross_check_*` 的子串匹配，扩权只是让 LLM 在**错误的分数**上做标签微调。

### 4.2 D6 的备选方案与否决理由

- **(D1 纯) 家族分组全交 Agent** → **否决**：让准入护栏的输入由会被说服的一方提供。
- **(D2) 家族概念整体退役** → **否决**：2026-09-01 的校准理由（「同族多成员若各自占名额会挤压其他家族的
  actionable 配额」）会原样复发。0903 实测：`知识产权` 组 3 个成员全部 actionable（90.8/88.5/87.5），
  `消费` 组 3 个成员 2 个 actionable——没有组粒度名额竞争，TOP-12 会被同族占满。
- **(D3+D1) 采纳**：规则能分的用规则，**分不出来的（落「其他」）交 Agent**。把垃圾桶变成 Agent 的输入队列。

### 4.3 D10 的备选方案与否决理由

- **B1 分位数映射**（把 Agent strength 分布对齐到规则 composite 历史分布，下游阈值不动）
  → **否决**：规则口径的 0/24 命中说明它的分布本身是退化的（composite ≈ 纯技术分）。
  把退化分布当对齐目标等于承认它是对的。
- **B2 人工重定阈值** → **否决**：没有样本量。`CATALYST_CONFIDENCE_GATE` 那条
  `# TODO: 待校准 (当前样本量: 0)`（`catalyst_config.py` L147）就是这么来的。

---

## 5. 架构设计

### 5.1 分层边界：Agent 定强度，规则守准入

板块 miniGOT 拆成 8 段，逐段裁定归属：

| 段 | 内容 | 位置 | 归属 | 理由 |
|---|---|---|---|---|
| **A** | 候选收集（`active_mainline` 组+成员展开 / `ambush` / `cooling_down` / `ambush_pool` 池外补入） | L549-618 | **规则（保留）** | Node 2 跨日状态机产物，是可复现性锚点 |
| **B** | 维度取数（`_parse_capital_flows` / `_parse_sector_level_flows` / `_extract_news_keywords` / `_extract_hot_sectors`） | L620-624 | **规则（保留）** | 纯解析，无语义判断。⚠️ **但解析成功不等于取到数据**：`_extract_hot_sectors`（L2798-2803）在 0903 返回 `[]`，因为上游 `hot_stocks[].associated_sectors` **50/50 全空**。这不是解析失败，是**个股→板块归属缺失**，属语义任务 → 由 Agent 补（§5.2 情绪侧） |
| **C** | 跨维校验（`_cross_check_capital` / `_cross_check_news` / `_cross_check_sentiment`） | L954-1107 | **→ Agent** | **病灶所在**：0/24 命中、21/24 neutral |
| **D** | composite 合成（`tech_score ± 10/15 + news_adjust ± 5/8 …`） | L706-820 | **→ Agent** | 硬编码加减分，改为 Agent 输出的连续 strength |
| **E** | confidence（`0.5 + 0.2 + 0.1×3` 离散阶梯） | L706-820 | **→ Agent** | 与 `remote_integration_plan.md` §14.1 A1（`avg_confidence` 硬编码 0.6）同源 |
| **F** | 准入分类（`_is_hard_exclude` / `_wave_coeff` 排序 / TOP-N / 过热闸门 / 拥挤拦截 / 组继承） | L1140-1237 | **规则（保留）** | 6 条护栏，防高位接票的物理闸 |
| **G** | `_build_sector_intel_from_scores` → `SectorIntel` | L822-875 | **规则（保留）** | 纯装配 |
| **H** | `_llm_refine_sectors`（narrative + cross_sector_risks + adjustments） | L1268-1387 | **拆分** | `adjustments` 分支**删除**；`narrative` / `cross_sector_risks` 保留 |

**Segment F 保留的 6 条护栏（每条都有校准日期，不得交给 Agent）：**

| 护栏 | 位置 | 校准日期 |
|---|---|---|
| `_is_hard_exclude`（late_stage/mature_exhausted + divergent/negative） | L1174 / L1251-1266 | — |
| 排序键 `composite × _wave_coeff(wave_days)`（0.85 / 0.90 / 1.0） | L1185 / L99-114 | v3.5 S3 |
| TOP-N=12 名额截断 | L1207 | — |
| 过热准入 `OVERHEAT_ADMISSION_LIMIT=0.3` | L1211 / L125 | 2026-09-01 |
| 组拥挤拦截 `hot_count>=3` 整组拦截 + 名额让渡 | L1195-1202 | 2026-09-01 |
| 组成员继承（不占名额，防同族挤压配额） | L1225-1235 | 2026-09-01 |

> **D10 在此处的具体落地**：删除 L1207 的 `and score >= 50` 与 L1259 的 `if composite < 40: return True`。
> 「矮子里拔将军」改由 **Agent confidence 门限** 防，不再用绝对分数。

**为什么 (乙) 天然消灭原方案问题 4（reasoning 与 action 分裂）：**

现状的分裂机制是「规则算完分 → LLM 写文案 + 单向抬标签」，两者**不在同一条因果链**：

```python
# L1345-1377 现状：LLM 只有单向权限，且只改离散标签，不改 composite/confidence
if new_rank <= old_rank:
    logger.warning(f"[Node 6] 跳过降级: ...")              # ← LLM 无权下调
elif new_class == "actionable" and overheat > OVERHEAT_ADMISSION_LIMIT:
    logger.warning(f"[Node 6] 拦截 LLM 上调（过热）: ...")   # ← 过热闸门
else:
    s.classification = new_class
```

(乙) 之后是「Agent 给强度和证据 → 规则据强度做准入」，reasoning 和 action 第一次在同一条链上。

### 5.2 Agent 输入契约（D5：只喂候选）

#### 板块侧：`sector.json` 134,931 B 的字节分布

> **更正**：磁盘文件 192,842 B 是**缩进后**大小；紧凑 JSON 实为 **134,931 B ≈ 33.7K tokens**。

| 是否进候选 | 字段 | 条数 | 字节 | 占比 |
|---|---|---|---|---|
| ✅ | `active_mainline`（4 组：旅游/银行/短剧游戏/知识产权，共 11 成员） | 4 组 | 11,678 | 8.7% |
| ✅ | 组成员增量（旅游景点/酒店餐饮/保险/AIGC概念/华为鸿蒙/C2M概念/网红经济） | 7 | 7,155 | 5.3% |
| ✅ | `ambush` | 8 | 7,627 | 5.7% |
| ✅ | `cooling_down` | 5 | 5,140 | 3.8% |
| **候选合计** | | **24** | **31,600** | **23.4%** |
| ❌ 备份 | `exhausted_mainline` | **63** | **64,744** | **48.0%** |
| ❌ 备份 | `theme_families`(32) / `near_miss_ambush`(9) / `industry_top`(10) / `top_by_score`(5) / `defensive_rotation`(2) / `reasoning` | — | 32,809 | 24.3% |
| ❌ 规则侧自用 | `strength_lookup`(217) / `frame_group_lookup`(217) / `overheat_lookup`(110) | — | 12,188 | 9.0% |

**候选 count=24 精确等于 `unified.json` 的 `confirmed_sectors=24`** —— 核算逻辑与 `_build_sector_intel` 完全一致。

三个 lookup **Agent 不需要**：它们是 `_resolve_overheat` / `_wave_coeff` / 组继承的输入，属 Segment F（规则侧）。

> **全喂的真正代价不是 token，是注意力污染**：`exhausted_mainline` 63 条 vs 候选 24 条
> → 全喂时 Agent 视野里 **72% 是它无权处理的衰竭板块**。

`exhausted_mainline` **保留在 `sector.json`**，因为规则侧 `_synthesize_mainline_endurance` 需要它查 `wave_days`
（L2956-2960 的 `detail_lookup` 构造遍历 `cooling_down` / `exhausted_mainline` / `top_by_score`）。
即「备份」的准确含义是：**不进 Agent 上下文，但留在归档供规则侧使用**。

#### 事件侧：`news.json` 73,629 B

| 字段 | 字节 | 占比 | 条数 |
|---|---|---|---|
| `catalyst_calendar`（**dict，7 个 key**；其中 `.events` 是 107 条列表） | 62,797 | 85.3% | events=107，avg 585 B |
| `major_events` | 7,511 | 10.2% | 13 |
| `reasoning` / `key_risks` / `concept_suggestions` / `data_quality` / `impact_score` / `overall_impact` / `status` | 3,321 | 4.5% | — |

单条事件 15 个字段里 **4 个纯冗余**，去冗余后 62,612 B → ~45,000 B（省 ~28%）：

```
consensus_expectation = '全球事件传导: SK 海力士/三星财报及 HBM 指引'   # = title 加前缀
category_tag          = 'drift'                                      # = direction
is_global             = True                                         # = event_type == 'global'
created_at            = None                                         # 107/107 全 None
```

#### 资金侧：`capital.json` 21,843 B（**本轮新增，已确认**）

> 🔴 **为何必须补**：§5.1 Segment C 把 `_cross_check_capital` 交给了 Agent，§5.3.2 要求输出 `capital_read`，
> §7.2 F1-2 又写「Agent `capital_note` 语义 + 规则侧金额直读」——但初稿的输入契约里**没有任何资金数据**。
> 而「规则侧金额直读」在 0903 只能读到 **3/24**（见下表），剩下 21 个无据可依。

| 字段 | 条数 | 字节 | 候选精确同名命中 | 处置 |
|---|---|---|---|---|
| `evidence.sector_flow.sector_flow_all`（1d） | **90** | 1,794 | **3/24**（银行、保险、汽车整车） | ❌ **不喂**（是 3d 的子集） |
| **`evidence.sector_flow.sector_flow_all_3d`** | **477** | **10,366** | **12/24** | ✅ **喂这张** |
| `evidence.sector_flow.sector_flow_all_5d` | 362 | 7,739 | 11/24 | ❌ 不喂（也是 3d 的子集） |
| `evidence.dde` / `reasoning` / `data_quality` | — | 1,318 | — | ❌ 不喂 |
| `evidence.northbound_realtime` / `individual_main_force` | — | **均为 `null`** | — | ❌ 不喂（归 OPEN-5 Y5） |

**选 3d 不选 1d 的理由**：实测三表名字集合满足 **1d ⊂ 3d、5d ⊂ 3d**（union = 477 = 3d 的条数），
所以只喂 3d 就是**全集**，且精确命中率从 3/24 提到 **12/24**（×4）。
剩下 12 个仍靠 Agent 做语义归属（例：`旅游` → 表里的 `旅游概念`），**这正是 D2「接线层禁映射」的同一逻辑**：
归属交给语义，规则只负责拿到归属后**直读金额**。

⚠️ **附带修正：`_parse_sector_level_flows`（L2749）只读 1d 表**。若保留它作为规则侧对照（影子期 M4-b/M4-c 需要），
则影子期内规则侧仍是 3/24 覆盖 —— **新旧对照本身就不公平**，这一点必须在 §8.1 M4 的解读里声明。

#### 情绪侧：`sentiment.json` 7,776 B（**本轮新增，已确认**）

> 🔴 **为何必须补**：0903 实测 `sentiment_check` = **`"low"` × 24/24**、`sentiment_detail` = **`"无热榜数据"` × 24/24**。
> 情绪面维度与消息面**同型 100% 空转**，而本文初稿只给消息面设了指标 M1。

**根因链（完整、可验证）：**

```python
# sentiment.json: hot_stocks[].associated_sectors = []   ← 50/50 全空
# ↓
# aggregator.py L2798-2803
def _extract_hot_sectors(sentiment) -> list[str]:
    sectors = []
    for stock in sentiment.hot_stocks:
        sectors.extend(stock.associated_sectors)   # ← 全空，extend  nothing
    return list(set(sectors))                      # → []
# ↓
# aggregator.py L1115-1116（_cross_check_sentiment def@L1109）
if not hot_sectors:
    return "low", "无热榜数据"                      # ← 24/24 全部走这一行
```

**喂什么（瘦身后仅 2,258 B，占基线的 2.7%）：**

| 字段 | 原始 | 瘦身后 | 说明 |
|---|---|---|---|
| `hot_stocks`（50 条） | 6,008 B | **2,258 B** | 只留 `stock_name` / `mention_count` / `sentiment`；删 `stock_code`（Agent 不需）与 `associated_sectors`（**就是它全空才导致空转**） |
| `hot_topics`（3）/ `forum_mood` / `market_temperature` / `news_drift` / `reasoning` | 761 B | 761 B | 全喂（极小） |
| `sentiment_score` / `fear_greed_index` / `sentiment_dispersion` / `contrarian_signal` / `trading_signal` / `stage_state` | ~530 B | ~530 B | 全喂（供 Agent 判全局情绪基调） |
| `hot_stocks` 全量不瘦身 | — | 6,008 B | ❌ 不选：`associated_sectors` 全空，喂进去只是噪声 |

**Agent 要做的任务**：把 50 只热榜股归属到当日 24 个候选板块（例：`天娱数科`/`中文在线` → 短剧游戏，
`星网锐捷` → AI硬件）。这是**纯语义任务**，与 D2 一致；成本 2,258 B，**是所有修复里性价比最高的一项**。

> ⚠️ **不要求 Agent 覆盖全部 50 只**。热榜股与当日候选无交集是正常的（例：`浙江世宝`/`中国巨石`），
> Agent 只对它**有把握的**归属输出，其余留空 —— 留空走 F1-4 `"unknown"`，**不得编造**。

#### token 预算核算：D5 是**必须的**，不是优化

| 方案 | 字节 | ~tokens | vs `agent_deep_research_budget=30000` |
|---|---|---|---|
| 全喂 `sector.json` + `news.json` | 208,560 | ~52–70K | **爆 1.7–2.3 倍** |
| 只喂候选 + 事件（去冗余）+ major_events | ~85,000 | ~21–28K | 卡在预算边缘 |
| **✅ 上述 + `sector_flow_all_3d` + 瘦身情绪侧（本文选定）** | **~97,624** | **~24–33K** | **可行**（+12,624 B = 基线的 14.9%） |
| 全喂 `capital.json` + `sentiment.json` + `market.json` | ~117,020 | ~29–39K | **顶破预算**，不选 |
| 只喂候选 + 事件（confidence 筛后）+ 本轮新增两侧 | ~67,600 | ~17–23K | 有余量（备选） |

> **取舍已定**：资金侧只喂 3d 表（不喂三表合并的 16,580 B），情绪侧只喂瘦身热榜。
> 若影子期发现 ~33K 顶到上限，**第一选择是筛事件 confidence（降到 ~67,600 B）而不是砍掉资金/情绪侧** ——
> 因为后者是两个**当前 100% 空转**的维度，砍掉等于宣告不修。

#### 顺带修正：Agent 视野覆盖缺口

`catalyst_config.py` L169-170：`REFINE_TOP_N = 10`、`REFINE_MAX_SECTORS = 20`。
0903 有 24 个 confirmed → **至少 4 个板块从未进入 Agent 视野**；
而 `_compute_position_from_regime` 的 `actionable_count=15` 是按**全部 24 个**统计的。

→ **`REFINE_MAX_SECTORS` 必须 ≥ 当日 confirmed 数**（动态取值，不再是常量 20），
否则产生「部分板块有 Agent 强度、部分只有规则强度」的混合口径，比现状更糟。
本方案下切片逻辑（L1284-1289）随 `adjustments` 分支一起退役，Agent 直接吃全部候选。

### 5.3 Agent 输出契约

#### 5.3.1 关系图（替代 T5）

```
[(event_id, board_code, strength, direction, propagation_path, confidence), ...]
```

- **必须输出 `board_code` 而非板块名**。做法：把当日候选板块清单（code + 截断名 + 当日涨幅）作为
  **受控词表**喂给 Agent，让它在 Node 2 自己那份清单上选择。→ **根本不需要 TDX↔同花顺 桥**（D2 的落地方式）。
- `propagation_path` 的每一跳是**实体**（公司/产品/技术），**只有终点必须落在受控词表内**。
  这样扩散链可以自由表达（`SK海力士HBM指引 → 存储芯片 → 半导体材料 → 第三代半`），
  而不要求中间跳板也是当日候选板块。
- **结构化约束替代概率门限**：`propagation_path` 每一跳都要有可验证的实体。
  这是对「confidence 不可信」（§14.1 A1：`avg_confidence` 硬编码 0.6）的补偿。

#### 5.3.2 板块强度（替代 Segment C/D/E）

```
[(board_code, strength ∈ [0,1], confidence, evidence, capital_read, news_read, sentiment_read), ...]
```

`composite_score = strength × 100`（语义变更，见 §7.1 与 D10）。

#### 5.3.3 家族归属（D6：只判落「其他」的）

**范围 = 候选里落「其他」的 11 个**，不是 `frame_group_lookup` 里全部 95 个：
`知识产权`、`C2M概念`、`网红经济`、`石油贸易`、`热泵概念`、`装配式建`、`数字水印`、`物业管理`、`汽车整车`、`第三代半`、`建材`
（11/24 = **45.8%** 的确认板块落在「其他」）。

**受控词表约束（硬要求）**：Agent 只能从规则的 **12 个真实家族**里选，**不得自创家族名**，
否则组护栏（名额竞争 / 拥挤拦截 / 组继承）无法与规则分组咬合：

```
AI软件 / AI硬件 / 周期资源 / 新能源 / 农业 / 医药 / 军工 / 消费 / 智能驾驶 / 金融 / 机器人 / 生态环保
+ 无归属
```

判 **`无归属`** 的板块 **不参与任何组级护栏**，按独立板块处理。
这同时实现了 **D3-b**：95 个互不相关的板块共享「其他」这一个家族名，用它做拥挤判定
（`hot_count>=3` 在 95 个里几乎必然触发）是纯粹的误伤源——**把「其他」从护栏输入里摘掉**，
而不是把 95 个板块重新分类。

**`frame_group_lookup` 现状质量（217 板块 → 13 家族）：**

```
其他 95 (43.8%) | AI软件 18 | AI硬件 18 | 周期资源 17 | 新能源 16 | 农业 13
医药 11 | 军工 8 | 消费 7 | 智能驾驶 5 | 金融 4 | 机器人 4 | 生态环保 1
```

已知错误分组（Agent 应能纠正）：

| 板块 | 现分组 | 问题 |
|---|---|---|
| `保险` | 金融（与 `银行` 同组） | tier=A 主线组「银行」的成员是 `银行,保险`——保险不是银行 |
| `知识产权` / `C2M概念` / `网红经济` | **其他** ×3 | 一个 **tier=A 主线组的全部 3 个成员**整组落「其他」 |
| `光刻机` / `玻璃基板` | AI硬件 | 半导体设备 / 面板材料 |
| `OLED概念` | AI软件 | 面板 |
| `航空` | 军工 | 民航（当日 role=ambush） |
| `全国性银` | **不在 217 条表内** | lookup 覆盖不了当日板块 |

### 5.4 执行时序改造（D9 甲：单次全量调用）

#### 现状时序

```python
# aggregator.py run()
L173  regime = self._compute_market_regime(market, sector, capital, news, sentiment)  # 不依赖 sector_intels
L179  regime = self._apply_regime_state_machine(regime)                               # 跨日状态机
L188  ambush_pool, ambush_reservoir, ambush_veto_log = await self._build_ambush_channel(...)
L193  sector_intels = self._build_sector_intel(...)          # ← 同步 def！内部 L645 调 _classify_all_sectors
L198  self._enrich_ambush_sector_intel(...)
L216  refine_events_map = self._group_events_by_sector(...)  # ← T5 映射，退役
L219  sector_intels = await self._llm_refine_sectors(...)    # ← adjustments 分支退役
L224  promoted_n = self._deterministic_catalyst_promotion(...)  # ← 内部 _lookup_sector_events，T5 映射
L231  position_limit = self._compute_position_from_regime(regime, sector_intels, market.position_limit)
L274  mainline_endurance = self._synthesize_mainline_endurance(...)  # ← L2949 用 intel.frame_group 分组
```

#### 改造后时序

Agent 的家族归属要喂给 `_classify_all_sectors`（在 `_build_sector_intel` 内部 Pass 2），
所以 **Agent 调用必须前移到 L193 之前**：

```python
L173  regime = self._compute_market_regime(...)                       # 不动
L179  regime = self._apply_regime_state_machine(regime)               # 不动
L188  ambush_pool, ... = await self._build_ambush_channel(...)        # 不动
NEW   candidates = self._collect_sector_candidates(sector, ambush_pool)   # 抽出 Segment A（同步，纯规则）
NEW   glue = await self._agent_semantic_glue(candidates, news, regime)    # ★ 单次全量调用（甲）
      #   → glue.relations   事件→板块关系图（替代 T5）
      #   → glue.strengths   板块 strength/confidence/evidence（替代 Segment C/D/E）
      #   → glue.families    11 个「其他」板块的家族归属（受控词表）
      #   → glue.degraded    F1 降级标记
L193  sector_intels = self._build_sector_intel(..., glue=glue)        # ★ 改为接受 glue；内部保持同步
L216  refine_events_map = glue.relations                              # ← 替代 _group_events_by_sector
L219  sector_intels = await self._llm_refine_sectors(...)             # 只保留 narrative / cross_sector_risks
L224  promoted_n = self._deterministic_catalyst_promotion(...)        # 内部改查 glue.relations
L274  mainline_endurance = self._synthesize_mainline_endurance(...)   # frame_group 已由 glue.families 回填
```

**关键实现选择**：不让 `_build_sector_intel` 变成 `async def`，而是**把 Agent 调用抽到它之前**，
结果作为参数 `glue` 传入。理由：`_build_sector_intel` 内部 3 个 Pass 全是纯计算，
改 async 会污染调用链且无收益；抽出 `_collect_sector_candidates` 后 Segment A 的边界也更清晰。

**受影响的调用点是 4 个，不是 3 个**（grilling 过程中新发现第 4 个）：

| 点 | 现状 | 改造 |
|---|---|---|
| L216 `_group_events_by_sector` | T5 映射扇出 | 退役 → `glue.relations` |
| L219 `_llm_refine_sectors` | narrative + adjustments（单向抬标签） | `adjustments` 分支**整个删除**（否则两个 Agent 入口改同一字段）；narrative / cross_sector_risks 保留 |
| L224 `_deterministic_catalyst_promotion` | 内部 `_lookup_sector_events`（T5 映射） | 改查 `glue.relations` |
| **L274 `_synthesize_mainline_endurance`** | L2949 `family_name = intel.frame_group or intel.sector_name` | `frame_group` 由 `glue.families` 回填；**续航公式改 E2**（§7.1） |

#### 调用次数核算（`remote_integration_plan.md` §15.6 红线：每日 LLM 调用 ≤ 20）

Node 6 现有 4 个调用点：

| 调用点 | 宿主 | 参数 |
|---|---|---|
| L1335 | `_llm_refine_sectors` (def@L1268) | temp=0.3, max_tokens=500 |
| L2072 | `_fmt_flow` (def@L2036，疑为嵌套 helper，宿主待确认) | temp=0.2, max_tokens=400 |
| L3418 | `_arbitrate_contradictions` (def@L3315)，**按矛盾条数串行循环** | temp=0.3, max_tokens=300 |
| L3743 | `_simplified_news_override` (def@L3703) | temp=0.2, max_tokens=300 |

新增 1 次（`_agent_semantic_glue`），`max_tokens` 需从 500 量级提到 **~5000**（输出含 24 板块强度 + 关系图 + 11 家族）。
**余量充足**，但需在实施时确认 `_arbitrate_contradictions` 的循环次数上界。

#### (甲) 的否决备选

- **(乙') 两次串行**（第 1 次归属、第 2 次强度）→ **否决**：上下文膨胀，且第 2 次失败时第 1 次成果白费。
- **(丙) 按板块分批 3×8** → **否决**：① 扩散链断裂（`SK海力士HBM → 半导体 → 光刻机/第三代半/玻璃基板` 是一条链）；
  ② **token 反而更贵**（107 事件重复喂 3 次 = 135K B）；③ 同一事件可能在两批给出矛盾归属。

## 6. 退役清单

> 本节回答「拆掉什么、必须留什么」。`remote_integration_plan.md` §14.3 的「反面教材」栏当前只有 1 项
> （LEGACY/新双轨 Prompt 开关），本次新增 17 项，供后续整合直接引用。
>
> ⚠️ **退役 ≠ 删除文件**。下表严格区分三种处置：**【删】**代码移除、**【停】**保留定义但断开消费、
> **【留】**必须保留（误删会破坏其他链路）。

### 6.1 「反面教材」栏新增项（17 项）

#### 第一组：跨维校验三件套（D5 的直接对象）

| # | 符号 | 位置 | 处置 | 退役理由 |
|---|---|---|---|---|
| 1 | `_cross_check_capital` | `aggregator.py` def@L954，调用@L758 | **删** | 三层护栏死循环的中枢；aligned/divergent/neutral 三态由 Agent 的 `capital_note` + 规则侧金额直读取代 |
| 2 | `_cross_check_news` | def@L1019，调用@L763 | **删** | 0903 实测 24 个候选**全部** neutral（0/24）；关键词匹配无法处理传导关系 |
| 3 | `_cross_check_sentiment` | def@L1109，调用@L766 | **删** | 同上，`hot_sectors` 命中即加分，无传导语义 |
| 4 | `_NEWS_POS_ADJUST` / `_NEWS_NEG_ADJUST` / `_NEWS_SOURCE_MULTIPLIER` / `_NEWS_GLOBAL_MULTIPLIER` / `_NEWS_FADING_MULTIPLIER` / `_NEWS_POS_CAP` / `_NEWS_NEG_CAP` | L1011-1017（**7 个常量**） | **删** | 随 #2 一起失效；这 7 个手调数字是「消息面 ±12/±18/×1.3/×1.3/×0.5，封顶 +20/−24」的全部依据 |
| 5 | `_UNRELIABLE_MATCH_TYPES` | L135（`frozenset({"generic","narrow"})`） | **删** | A6 护栏本体；它强制 neutral 的同时**单向放行利多**，是 0903 白送 15 分的机制 |
| 6 | `_GENERIC_ROOTS` | L2828（22 个泛化词根） | **删** | 仅服务 `_match_sector` 的 generic 判定（L2890） |
| 7 | `_match_sector` | def@L2835（`min_substr_len=3`） | **删** | 统一匹配入口；`min_substr_len=3` 主动拒绝了 `航空`←`航天航空` 这类**真子串**关系 |
| 8 | `_lookup_capital_flow` | def@L913，调用@L966 | **删** | 「映射试了一个月」最硬的物证（见下方引用块） |
| 9 | `_CAPITAL_DIVERGENT_TAIL_PCT` / `_CAPITAL_DIVERGENT_MIN_SAMPLES` / `_CAPITAL_DIVERGENT_FALLBACK_YI` / `_CAPITAL_ALIGNED_MIN_YI` + `_divergent_threshold` | L888-896（4 个常量）+ L899（classmethod） | **删** | 全部是 #1 的配套阈值；2026-09-02 P0-1 刚校准完，**校准完即退役** |

> **#8 的原文注释（`aggregator.py` L922-932）——本项目「映射不可行」的第一手证据**：
> ```
> P0-1（2026-09-02 审核）：原实现只用 dict.get + 别名循环，实测 0902
> 15 个确认板块仅命中 5 个（33%）——资金流表有 476 个板块且命名口径与
> Node 2 不同（"旅游" vs "旅游概念"、"橡胶" vs "橡胶制品"）。
> 改为复用统一匹配入口 _match_sector，子串门槛放宽到 2 字后匹配率 33%→80%。
>   2. "narrow"（如"化工"→"磷化工"）与 generic 同等降级（实测 11/15 通用名会误配）
> ```
> **匹配率提到 80% 的代价是 73%（11/15）误配**，于是加 A6 护栏强制 neutral，
> 于是消息面/资金面双双归零 —— 这就是 §3.3 的七步死循环。

#### 第二组：T5 事件→板块对齐层（影响面比预估大 3 倍）

| # | 符号 | 位置 | 处置 | 退役理由 |
|---|---|---|---|---|
| 10 | `_group_events_by_sector` | def@L2101；**3 个调用点**：L216(`run`) / L1657(`_check_reservoir_promotion`) / L1778(`_build_ambush_channel`) | **删** | 见下方「⚠️ 时序修订」 |
| 11 | `_lookup_sector_events` | def@L2125；**5 个调用点**：L1304(`_llm_refine_sectors`) / L1418(`_deterministic_catalyst_promotion`) / L1663+L1682(`_check_reservoir_promotion`) / L1811(`_build_ambush_channel`) | **删** | 同上 |

> ### ⚠️ 时序修订（本节写作过程中新核出的事实，**推翻 §5.4 已写入的结论**）
>
> §5.4 写的是「Agent 调用必须前移到 **L193** 之前」。**这是错的。**
>
> T5 映射不止服务 miniGOT，它同时是**埋伏通道 G2 闸门**和**蓄水池升格条件 2**的判据：
>
> | 链路 | 代码 | 性质 |
> |---|---|---|
> | 埋伏通道 G2（合格催化剂） | L1811 `sector_events = self._lookup_sector_events(...)` → L1814 `_run_ambush_gates(name, sector_events, ...)` | **准入闸门**，决定入正式池/蓄水池 |
> | 蓄水池升格条件 2 | L1682 → L1683-1687 `qualified_events = [e for e in sector_events if e.confidence >= CATALYST_CONFIDENCE_GATE and e.direction != "negative"]` → L1688 `if not qualified_events: ...` | **准入闸门**，决定能否重走流水线 |
> | miniGOT 精炼 + 确定性提升 | L1304 / L1418 | 标签调整 |
>
> 前两条位于 `_build_ambush_channel`（**L188**）内部，**早于** `_build_sector_intel`（L193）。
> 故 **glue 必须前移到 L188 之前**，即 `regime` 计算（L173-179）之后、埋伏通道之前。
>
> **由此产生的循环依赖及其唯一解**：
> - Segment A 候选集的第 4 个来源是 `ambush_pool`（L601-615「池外补入」），而 `ambush_pool` 是 L188 的**输出**；
> - glue 在 L188 之前 ⇒ **拿不到 `ambush_pool`**；
> - 唯一解：glue 只喂 Node 2 的原始三路候选（`active_mainline` 组+成员 / `ambush` / `cooling_down`），
>   池外补入项走既有 fallback（`_resolve_pool_sector_detail` L674-704 的 `strength_lookup` 合成路径已存在），
>   其家族归属由 `_build_frame_group` 兜底、`glue_family` 留 `null`。
> - **0903 实测代价 = 0**：候选 24 = mainline-group 4 + mainline-member 7 + ambush 8 + cooling 5，
>   恰好等于 `confirmed_sectors=24`，说明当日池外补入 **0 个**。补入路径是 2026-08 后为「升格后已移出
>   `sector.ambush`」新加的兜底，非主流量。
> - **不把 `_build_sector_intel` 改成 async**（维持 §5.4 结论）：改为抽出 `_collect_sector_candidates()`
>   供 glue 与 Segment A 共用，glue 结果作为参数传入。

#### 第三组：手写映射表与其消费点

| # | 符号 | 位置 | 处置 | 退役理由 |
|---|---|---|---|---|
| 12 | `board_aliases.SECTOR_NAME_CANONICAL` 在 **aggregator 内**的消费点 | L2109 / L2138 / L2867 | **停**（消费点随 #7/#10/#11 消失） | ⚠️ **文件本体不可删**，见 #13 |
| 13 | `src/got/board_aliases.py` 本体 | 50 行，**21 条**映射 | **留** | 被 **6 个文件**消费（见下方引用块）；删除会破坏 `event_id` 稳定性 |
| 14 | `CONCEPT_UNIVERSE` | `src/got/nodes/sector_analysis.py`；被 `catalyst_calendar.py` import | **留** | Node 2 侧的概念全集，是 glue 家族受控词表的**输入源**而非退役对象 |
| 15 | `data/capital_flow_aliases.json` / `data/wencai_board_aliases.json` | 数据文件 | **停**（aggregator 侧不再读） | 需先确认 `stock_screener.py` / Node 2 是否仍读，**不可直接删文件** |

> **#13 的核查结果 —— `board_aliases.py` 是全局基础设施，不是 Node 6 私有映射表**：
>
> | 消费方 | 用途 | glue 后是否仍需要 |
> |---|---|---|
> | `src/got/catalyst_calendar.py` | **写入侧**：`event_id=_make_event_id(source, event_type, _normalize_sector_name(sector), event_date)`、`sector=_normalize_sector_name(sector)` | **必须保留**。删除会导致 `event_id` 漂移，历史事件无法对账 |
> | `src/got/catalyst_realization.py` | 催化剂兑现匹配 | 保留 |
> | `src/got/nodes/portfolio_decision.py` | Node 7 `confirmed_map[normalize_sector_name(...)]` | 保留（属 OPEN-1 射程） |
> | `src/services/stock_screener.py` | 问财选股 | 保留 |
> | `src/got/nodes/sector_analysis.py` | Node 2 | 保留 |
> | `src/got/nodes/aggregator.py` | **4 处** | **随 #7/#10/#11 消失** |
>
> **顺带核出 `board_aliases.py` 的两个固有缺陷（作为「手写映射不可行」的补充证据）**：
> 1. **覆盖率 9.7%**：21 条映射 vs `frame_group_lookup` 的 217 个板块 vs `affected_sectors` 的 53 个标签；
> 2. **反向表多对一 collapse bug**：`_SECTOR_NAME_REVERSE = {v: k for k, v in SECTOR_NAME_CANONICAL.items()}`
>    —— `旅游概念`/`旅游及景区`/`旅游综合` 三个 key 同映射到 `旅游`，dict comprehension 后向覆盖，
>    最终 `_SECTOR_NAME_REVERSE["旅游"] == "旅游综合"`。
>    故 `sector_name_variants("旅游")` 返回 `{"旅游", "旅游综合"}`，**丢失 `旅游概念` 与 `旅游及景区`**。
>    而 0903 的 `active_mainline` 第一名正是 `旅游`（tier=A，composite 99.7）。

#### 第四组：LLM 权限与准入阈值

| # | 符号 | 位置 | 处置 | 退役理由 |
|---|---|---|---|---|
| 16 | `SectorIntel.family_resonance` | `schemas.py`；0903 实测 **24/24 全 `None`** | **删** | D7。⚠️ **`MainlineEndurance.family_resonance` 是另一个字段，必须保留**（L3001/L3091/L3124/L3645 活代码） |
| 17 | `_llm_refine_sectors` 的 `adjustments` 分支 + `_classify_all_sectors` 的 `score >= 50` 与 `_is_hard_exclude` 的 `composite < 40` | L1355-1382（adjustments）/ L1207（`score >= 50`）/ L1244（`composite < 40`） | **删 adjustments 分支**；**两个阈值改由 D10 处置** | ① adjustments 与 glue 改同一字段 ⇒ 两个 Agent 入口打架；② `score>=50` / `composite<40` 是**按旧 composite 语义**校准的绝对阈值，而 0903 实测 composite 与 classification **已经脱钩**（旅游景点 99.1→caution、汽车整车 35.7→actionable，因埋伏绕过 TOP-N），阈值早已半失效 |

> ### ✅ 接线后重判（阶段 2 退役执行时，2026-09-26）——部分「删」项修正为「保留」
>
> 本清单写于 0903，未预见阶段 1 接线后 glue 事件供给链路的新增消费者。执行退役时逐个精确 grep，**发现下列符号仍有活消费者，【删】作废、改为【保留】**：
>
> | # | 符号 | 原处置 | 重判 | 新消费者（接线后出现） |
> |---|---|---|---|---|
> | #6 | `_GENERIC_ROOTS` | 删 | **保留** | `_match_sector` 保留（见 #7），其 generic 判定仍依赖本词根表 |
> | #7 | `_match_sector` | 删 | **保留** | `_lookup_flow_map` 仍调它做板块名→资金流表归一化匹配（吸筹确认闸门 `_check_accumulation_gate` 链路；初次登记时误写为 `_resolve_pool_sector_detail`，0926 审核已更正） |
> | — | `_parse_sector_level_flows` + `_CAPITAL_CHANNEL_TERMS` | （§2/§5 旁及） | **保留** | `_detect_family_capital_mismatch` 仍消费（家族级资金错位比对，非三件套） |
> | #10 | `_group_events_by_sector` | 删 | **保留** | 接线后成为 glue 事件供给基础设施：`_glue_aware_events_map` legacy 回退 + 埋伏 G2 + 蓄水池升格仍用 |
> | #11 | `_lookup_sector_events` | 删 | **保留** | 同上，埋伏/蓄水池路径活代码 |
> | — | `_extract_hot_sectors` | 删 | **保留** | 埋伏/蓄水池路径仍消费（仅断掉 `_compute_sector_scores` 内的调用） |
> | #16 | `SectorIntel.family_resonance` | 删 | **已删** ✅ | 全仓无读取方，随阶段 2 删除（`MainlineEndurance.family_resonance` 仍按 P4 保留） |
>
> **确认按原计划删除的项**：#1/#2/#3 三件套、#4 `_NEWS_*` 7 常量、#5 `_UNRELIABLE_MATCH_TYPES`、#8 `_lookup_capital_flow`、#9 `_CAPITAL_DIVERGENT_*`/`_CAPITAL_ALIGNED_MIN_YI`/`_divergent_threshold`，以及 `_parse_capital_flows`/`_extract_news_keywords`（审计确认均真孤儿）。`_compute_sector_scores` 精简为 tech+lifecycle，漏命中/降级板块改 **neutral 中性垫底**（负责人裁决放弃逐板块兜底）。D-P1 `capital_divergence` 诊断置 `None`（schema 字段保留）。

### 6.2 必须保留项（防误删清单，5 项）

| # | 符号 | 位置 | 保留理由 |
|---|---|---|---|
| P1 | `FAMILY_KEYWORDS` + `_FAMILY_NAMES` + `_build_frame_group` | `src/got/nodes/sector_analysis.py` | **D3+D1 的兜底路径**：glue 给出 `family=null`（分不出来）时由它兜底。13 个家族名即受控词表 |
| P2 | `strength_lookup` / `overheat_lookup` / `frame_group_lookup` | Node 2 输出（`schemas.py`），217/110/217 条 | glue 缺席时的降级数据源（F1）；`overheat_lookup` 还服务 Segment F 准入闸门 |
| P3 | `exhausted_mainline` | `sector.json`，64,744 B / 48.0% / 63 条 | **不喂 Agent**（注意力污染），但**必须留在归档**：`_resolve_pool_sector_detail`(L690) 与 `_synthesize_mainline_endurance` 的 `detail_lookup`(L2956-2960) 都要遍历它 |
| P4 | `MainlineEndurance.family_resonance` + `>= 0.7` 支撑信号判据 | L2996-3003 / L2932 / L3091 / L3124 / L3645 | 活代码。**但公式须改 E2**（§7.1）：`1 - std_dev/50.0` 在 0903 已零区分度 |
| P5 | `_classify_all_sectors` 的其余 5 条护栏 | L1140-1237 | `_is_hard_exclude` 的 lifecycle 分支、`_wave_coeff` 排序、TOP-N=12、`OVERHEAT_ADMISSION_LIMIT=0.3`、组拥挤拦截 `hot_count>=3`、组成员继承 —— **3 周校准成果，D5 只替换强度不换准入** |

### 6.3 `remote_integration_plan.md` 联动修订（7 条）

| # | 目标位置 | 修订内容 |
|---|---|---|
| R1 | §14.3「反面教材」栏 | 当前仅 1 项。追加本文 §6.1 的 17 项，并**新增一列「教训类型」**（映射不可行 / 护栏互相拆台 / 手调常量 / LLM 权限残缺） |
| R2 | §14.2 **B3**（工具结果无大小上限，优先级「中」） | 原文只讲 `serialize_tool_result` 截断。**须补「筛选 ≠ 截断」的区分**：截断是无损性未知的暴力裁剪，筛选是按语义边界取舍。0903 实测：全喂 sector+news = 208,560 B，爆预算 1.7-2.3 倍；而状态机候选只占 sector.json 的 **23.4%**（31,600 B）——**该做的从来不是截断，是筛选**。优先级建议由「中」提到「高」 |
| R3 | §14.2 **B1**（超时默认关闭） | 原文事实已过时或需复核：`_call_llm_async`(L3826) 走 `asyncio.to_thread(adapter.call_text, ...)`，**未传 `timeout`**。glue 单次 `max_tokens≈5000`，无超时则挂死整条流水线。列为 glue 的 **P0 阻塞项** |
| R4 | §14.1 **A1**（`AgentMemory` 校准分母伪造）+ **A2**（记忆回填缺失） | **从 glue 的前置依赖中移除**。核查结论：glue 走 `call_text` 单次调用、不走 `build_agent_executor()`，因此不触及 `AgentMemory`（A1/A2 只影响 multi 架构的 `BaseAgent`）。A1/A2 仍是 §15 T1/T1-H 的前置，但**不阻塞 glue** |
| R5 | §14.1 **A4**（`call_type` 用量归因不统一） | **原文描述有误，须改写**。原文：「GOT 24 处 `call_text` 各自随意传 `call_type` 字符串」。核查事实：`call_text` 签名（`llm_adapter.py` L486-494）**根本没有 `call_type` 参数**；`persist_llm_usage` 在 `src/` 下仅被 `analyzer.py`(3)/`chat_context.py`(2)/`runner.py`(1) 调用，**`src/got/` 下 0 处**。故真实缺陷是「**GOT 全部 LLM 调用完全不落用量库**」，而非「命名不统一」。修法应改为：在 `_call_llm_async` 内补 `persist_llm_usage(response.usage, model, call_type="got_node6_{stage}")` |
| R6 | §14.3「可直接复用」栏 | 追加 `storage.record_llm_usage` / `get_llm_usage_summary`（L3316/L3337，已支持 `by_call_type` 聚合，L3363-3391）—— §8 影子期指标的**度量基础设施**，依赖 R5 先落地 |
| R7 | §15.6 验收标准末条 | 原文「成本：审查层每日 LLM 调用总数 ≤ 20」。**限定语「审查层」须明确**：它约束 T1/T1-H，不约束 Node 6。且该指标**当前无法度量**（见 R5）。glue 的成本红线在本文 §5.4 单独核算 |

## 7. 连带修复

> D5（强度替换）不是孤立改动。composite 的语义变了，下游所有**按旧语义校准的常量与公式**都跟着失效。
> 本节是必须与 D5 **同批落地**的修复；漏一项就会出现「新分旧尺」的隐性错配。

### 7.1 E2：`family_resonance` 公式自适应归一化（D8）

#### 现状（L2996-3003）

```python
strengths = [m.composite_score for m in members]
family_resonance = max(0.0, 1.0 - std_dev / 50.0)   # 50 分为满分标准差
# L3091: if family_resonance >= 0.7: support_signals.append(f"高共振（{family_resonance:.2f}）")
```

**分母 50.0 是写死的绝对量**，含义是「成员强度标准差达 50 分则共振归零」。

#### 0903 实测：零区分度

| 家族 | 成员 composite | std_dev | 现公式 | `>=0.7` 触发 |
|---|---|---|---|---|
| 银行 | 100.0 / 98.7 | 0.65 | **0.987** | ✓ |
| 知识产权 | 90.8 / 88.5 / 87.5 | 1.38 | **0.972** | ✓ |
| 旅游 | 99.7 / 99.1 / 86.9 | 5.90 | **0.882** | ✓ |
| 短剧游戏 | 100.0 / 81.1 / 78.7 | 9.53 | **0.809** | ✓ |

**4/4 全部触发「高共振」支撑信号**，该判据在 0903 完全无信息量。
在 D5 之下会更糟：Agent 给的 strength 是语义强度分，同家族成员天然接近（共享同一叙事），
std_dev 会系统性变小，`family_resonance` 向 1.0 坍缩 —— **从「零区分度」恶化到「恒为真」**。

#### 修法比较（三个候选分母，全部用 0903 实算）

| 方案 | 分母 | 银行 | 知识产权 | 旅游 | 短剧游戏 | `>=0.7` 命中 | 判定 |
|---|---|---|---|---|---|---|---|
| 现状 | 50.0（写死） | 0.987 | 0.972 | 0.882 | 0.809 | **4/4** | 零区分度 |
| **E2a** | 全体 24 候选 pstdev = **31.42** | 0.979 | 0.956 | 0.812 | 0.697 | **3/4** | 改善有限 → **否决** |
| **E2c**【推荐】 | **主线子集** 11 成员 pstdev = **7.62** | **0.915** | **0.819** | **0.226** | **0.000** | **2/4** | 全谱展开，门槛天然有效 |

**E2a 为何不够**：全体候选含 ambush（7.9-52.4）与 cooling（54.9-74.8），这些低位板块把整体 pstdev
撑到 31.42，**稀释了主线梯队内部的差异** —— 分母越大，所有家族的 resonance 都被推向 1.0。

**E2c 为何正确**：`_synthesize_mainline_endurance` 评估的对象本来就是 `active_mainline` 家族（L2946），
其成员 composite 天然聚在高位（0903 主线 11 成员 mean=91.91，range 78.7-100.0）。
用**主线子集自身的 pstdev** 作分母，度量的才是「主线梯队内部的一致性」—— 这正是 `family_resonance` 的语义本意。

**E2c 的语义验证（与人工判断逐条对照）**：

| 家族 | E2c | 人工语义判断 | 一致 |
|---|---|---|---|
| 银行（银行 100.0 / 保险 98.7） | 0.915 | 双双顶级，真共振 | ✓ |
| 知识产权（90.8 / 88.5 / 87.5） | 0.819 | 三成员紧密，真共振 | ✓ |
| 旅游（99.7 / 99.1 / **86.9**） | 0.226 | 酒店餐饮掉队 13 分，梯队分化 | ✓ |
| 短剧游戏（100.0 / **81.1** / **78.7**） | 0.000 | 掉队 19-21 分，明显分化 | ✓ |

**4/4 一致**。`>=0.7` 门槛无需重调，自动把真共振（银行/知识产权）与伪共振（旅游/短剧游戏）分开。

#### 落地公式（含小样本保护）

```python
# E2c：分母 = 当日主线子集（role == "mainline"）composite 的 pstdev
mainline_scores = [i.composite_score for i in sector_intels if i.sector_role == "mainline"]
sd_ref = pstdev(mainline_scores) if len(mainline_scores) >= 2 else 0.0
# 小样本保护：主线成员 < 4 或 sd_ref 过小时设 floor，防止分母→0 导致 resonance 爆到 1.0
if len(mainline_scores) < 4 or sd_ref < 5.0:
    sd_ref = max(sd_ref, 5.0)
family_resonance = clip(1.0 - std_dev / sd_ref, 0.0, 1.0)   # 替换 L3001
# L3003 单成员分支 family_resonance = 1.0 保持不变
```

**0903 验算**：主线成员 11 个（≥4），`sd_ref = 7.62 > 5.0`，取 7.62 —— 与上表一致。

### 7.2 F1：降级契约（**不能退回现状**）

> ⚠️ **关键设计约束**：§6.1 把 `_cross_check_capital` / `_cross_check_news` / `_cross_check_sentiment`
> 三件套全删了，所以 **F1 无路可退到「现状规则合成」**。F1 必须是一个**新的、更诚实的降级态**，
> 而不是旧路径的备份。这一点如果实施时忽略，会在删完三件套后发现降级分支引用了不存在的函数。

| # | 字段 | glue 在场 | **F1 降级值** | 说明 |
|---|---|---|---|---|
| F1-1 | `composite_score` | Agent `strength` × 100 | **`= tech_score`**（Node 2 尺子，裸技术分） | 三件套已删，唯一还可信的分就是 Node 2 的 `strength_score`。**不加任何跨维修正** |
| F1-2 | `capital_check` | Agent `capital_note` 语义 + 规则侧金额直读 | **`"unknown"`** | ⚠️ **不是 `"neutral"`**。`neutral` 在旧语义里是「验证通过但无信号」，会白送分；`unknown` 明确表示「无判据」 |
| F1-3 | `news_check` | 同上 | **`"unknown"`** | 同上。0903 现状是 24/24 neutral，F1 必须与现状**可区分**，否则影子期无法度量降级率 |
| F1-4 | `sentiment_check` | 同上 | **`"unknown"`** | 同上 |
| F1-5 | `frame_group` | `glue.families` | **`frame_group_lookup.get(name)` → `_build_frame_group(name)`** | 既有兜底链（L849-850）不变 |
| F1-6 | `classification` | Segment F 6 条护栏 | **同左，完全不变** | D5 只换强度不换准入。但注意 F1-1 使 composite 退回 tech_score，**TOP-N 排序会变** —— 这是可接受的，因为准入本来就是规则侧的职责 |
| F1-7 | `llm_refinement_used` | `True` | **`False`** | 🔴 **L299 当前硬编码 `True`**，是原方案 §5.2 指出的「说谎字段」（全项目无代码读取）。必须改为按实际结果赋值 |
| F1-8 | `data_quality.missing_fields` | `[]` | **`["agent_semantic_glue"]`** | 🔴 **L3282 当前硬编码 `[]` 恒空**（原方案 §9.2 已指出）。必须打通，否则下游无法感知降级 |

#### F1 的 7 种触发场景

| 场景 | 触发条件 | 动作 |
|---|---|---|
| S1 调用失败 | `_call_llm_async` 4 次重试后返回 `""`（L3839-3855） | 全量 F1，记 `warning` |
| S2 JSON 解析失败 | `_parse_json` raise（L3858-3863，`re.search` + `json.loads`） | 全量 F1，记 `warning` |
| S3 覆盖不全 | glue 返回的 `sectors` 少于候选集 | **逐板块降级**：缺失项走 F1，命中项用 glue |
| S4 家族名越界 | `family` 不在 13 个受控词表内 | 该板块 `frame_group` 走兜底链，记 `warning`，**不影响 strength** |
| S5 强度越界 | `strength` 不在 `[0, 100]` | `clip` 后使用，记 `warning` |
| S6 开关关闭 | `AGENT_SEMANTIC_GLUE_ENABLED=false` | 全量 F1（= 影子期的对照组） |
| S7 超时 | `call_text` 无 `timeout`（见 §6.3 R3） | **必须先修 R3**，否则 S7 退化为挂死整条流水线，不是降级 |

### 7.3 可复现性机制（4 项，影子期的前提）

| # | 机制 | 现状 | 要求 |
|---|---|---|---|
| C1 | `temperature=0` | Node 6 现有 4 个调用点分别是 0.3 / 0.2 / 0.3 / 0.2（L1335 / L2072 / L3418 / L3743） | glue **必须传 0**。归属与强度是判定任务不是创作任务；非 0 温度下影子期指标无法归因（分不清是模型波动还是逻辑变化） |
| C2 | 当日结果落盘 | `_run_cache`（L170 / L2276-2289）是**单次 `run()` 内的内存缓存**，进程退出即失 | glue 结果须写 `data/got_daily/{date}/agent_glue.json`。理由：① 重跑当日流水线不重复烧 token；② 影子期需要 glue 输出与规则输出的**同日对照存档**；③ 事后可复现某日的归属判断 |
| C3 | `call_type` 归因 | 🔴 **完全缺失**：`_call_llm_async` 不调 `persist_llm_usage`，`src/got/` 下 0 处（见 §6.3 R5） | 传 `call_type="got_node6_semantic_glue"`。**这是 §8 全部指标的度量前提** —— 没有它，「glue 成功率」「token 消耗」只能靠 grep 日志 |
| C4 | 上下文切片固定 | 候选集在 `_build_sector_intel` 内按 4 个来源顺序 append，末尾 `results.sort(key=composite, reverse=True)`（L670） | 喂 glue 的顺序必须**确定性**：按 `(role 优先级, Node 2 原始顺序)` 固定，**不按 composite 排序**（composite 此时尚未由 glue 给出，形成循环）。否则同一日两次运行的 prompt 不同，C1 白做 |

### 7.4 原方案（`grilling_0903_solutions.md`）条目处置

#### 自动作废（4 条）—— 被 D1-D10 覆盖，无需单独实施

| 原方案 | 标题 | 作废理由 |
|---|---|---|
| **§3.4** | 方案：连续函数替代离散四态（tanh） | `_cross_check_capital` **整个退役**（§6.1 #1），aligned/divergent/neutral 三态不再存在，A6 单向利多随之消失。给一个要删的函数做整形手术无意义 |
| **§2.4 方案 A** | 催化剂注入 `composite_score` | ① 它要给 `_compute_sector_scores` 加 `catalyst_events_map` 参数并按板块查事件，而查事件依赖 `_lookup_sector_events`（§6.1 #11 **退役**）；② composite 已由 Agent 接管（D5），再往里注入催化剂 = **双重计入** |
| **§6.4** | 软门槛 + 连续催化力 | ① L695 同样是「在 composite_score 中注入催化剂」，与 §2.4 方案 A 同源同废；② 降 `CATALYST_CONFIDENCE_GATE=0.7` 不是瓶颈 —— 0903 实测 107 条 events 中 **≥0.7 有 69 条（64.5%）**，真正的瓶颈是**归属**（带 `affected_sectors` 仅 36/107=33.6%，精确命中候选仅 3 个）。归属为空时降门槛只会放进更多**归属不明**的事件 |
| **§8.4** | LLM 辅助板块归类 + 语义嵌入兜底 | 见下方逐条拆解。**被 D3+D1 完全取代** |

**§8.4 的五条具体作废理由**（原方案 L863-918）：

1. **Step 1 扩 `FAMILY_KEYWORDS`（改成 dict + 新增 7 个家族）直接违反 D2**（接线层禁映射）——
   用户原话「映射试了一个月了错漏太多」。扩表就是加映射。
2. **Step 2 的 `if not unknown or len(unknown) > 20: return {}` 在 0903 会直接触发** ——
   实测「其他」家族 **95 个（43.8%）**，远超 20 ⇒ **整个 LLM 归类被跳过，方案自我瘫痪**。
3. **Step 2 只归类落在「其他」的板块，对已知家族无纠错权** ——
   而 0903 实测 `航空 → 军工` 这类归属本身就是可疑的（`航空` 的候选强度 52.4，`军工` 家族 8 成员）。
   D3+D1 允许 Agent 覆盖**全部候选**的归属，不限「其他」。
4. **Step 3 的 SQLite 缓存表 `sector_family_cache` 跨日复用与 D1 冲突** ——
   家族归属是语义判断，随市场叙事漂移（「知识产权」在 IP 经济热时属新消费，在版权政策期属政策主题）。
   缓存会把旧判断锁死。D1 = 每日重算。
5. **§8.5 的验证指标基线数字错误** —— 原文「其他家族成员数应从 ~64 降至 < 15」，
   0903 实测是 **95 个 / 217 = 43.8%**，不是 ~64。

**D3+D1 相对 §8.4 的改进**：作用域从「全量 217 板块中落其他的 95 个」收窄到「**当日候选 24 个中落其他的 11 个**」，
既避开 20 上限，又把 token 花在真正影响决策的板块上。

#### 挂起（3 条）—— 不作废，但移出本轮射程

| 原方案 | 标题 | 挂起去向 |
|---|---|---|
| **§4.4** | 两阶段优化器（卖出→买入解耦） | **OPEN-1**（用户明示「仓位先不管，后续处理」） |
| **§7.4** | 情绪面直接注入仓位公式 | **OPEN-1**（同属仓位链路）+ **OPEN-2**（情绪面 `contrarian_signal` 展示态） |
| **§5.4** | LLM 输出结构化控制参数 | **部分采纳**：`sector_overrides` 的思路即 D5 的 `strength`；但「`reasoning` → `position_limit` 反馈」部分挂 **OPEN-1** |

#### 保留待办（3 条）—— 与 glue 无耦合，可独立排期

| 原方案 | 标题 | 去向 |
|---|---|---|
| **§9.3** | 字段级完整性检查 | **P0-B**。§7.2 F1-8 只打通 `agent_semantic_glue` 一个字段，原方案的完整字段级检查仍需单独做 → **OPEN-5** |
| **§10.3** | exhausted 判定与当日涨幅矛盾 | **OPEN-3**。注：原方案依据的 `one_day_drop` 判据**已于 2026-09-02 P0-3 移除**，须按现行 4 条判据重新评估 |
| **§11.3** | Node 1 指数覆盖不完整 | **OPEN-4**（`INDEX_CONFIG` zip 静默截断） |

#### 原方案 §12 路线图与附录论文索引的处置（本轮补录）

> 上述 10 条只处置了各问题的**解决方案（§X.4）**。原方案还有两块结构性内容需明确去向，
> 否则对照两份文件会找不到它们的落点。

**G1 — §12.1 优先级矩阵 + §12.2 实施路线图：整体作废，由本文 §7.4 + §10 取代。**

| 原方案 | 原文内容 | 处置 |
|---|---|---|
| §12.1 优先级矩阵 | 问题 1/2/3=P0、4/5/6=P1、7/8/9=P2、10=P3（按「改动行数」排序） | **作废**。排序依据是「规则改动量」，而 D5 用 Agent 一次性替换问题 1/2/6 的规则，行数指标失去意义。新优先级见本文 §10：P0-A 接线（阻塞项 A1 回放 harness + A2 超时/用量落库）→ P0-B 数据完整性 → P1 调参 |
| §12.2 实施路线图 | Week1 P0 三连 / Week2 P1 三连 / Week3 P2 收尾 | **作废**。原路线把问题 1/2/5/6 当作独立逐日改动，实际它们同为「跨维校验空转」一个根因（§3），须由 glue **同批**替换，不能拆到不同周 |
| §12.3 验证框架 | `--replay-date` 回放 + `tests/got/` 单测 | **不作废但已证伪**：见 §2.1#10（两入口均不存在）+ §8.3（基础设施缺失）；真实替代见 §10 A1（回放 harness）+ §8.1（六条可计数指标） |

**G2 — 附录论文索引（36 篇）：分两类处置，不整体搬运。**

| 类别 | 代表引文 | 处置 |
|---|---|---|
| **佐证 D5 的 Agent 路线（保留）** | 问题 1 #1「Leveraging LLMs for Top-Down Sector Allocation」(arXiv 2503.09647)、#2「Cross-Sector Market Regime Forecasting with LLM」、#3「FinRipple: Aligning LLMs with Financial Market」(ACL 2025) | **反而支持本文**：这些工作的核心正是「LLM prompt 整合新闻+宏观做板块对齐」，与 D5「Agent 定强度」同路。原方案却把它们挂在「改规则匹配」的 §2.4 下，属**引文与方案错配** |
| **随作废方案失效（不搬运）** | 问题 1 #4 FinBERT+Zero-Shot、#5 FinGPT `event_sector_map` 字典注入、#6 Qlib 日历事件因子 | 这些是「预建映射表/因子字典」思路，与 D2「接线层禁映射」直接冲突，随 §2.4/§6.4 作废 |
| **支撑 §6.2 保留护栏（隐性沿用）** | 问题 3 #10 Kelly+VIX、`_classify_all_sectors` 注释内的 Bergemann/Black-Litterman/Ben-David、仓位链路的 Moreira & Muir | 已在保留代码的注释中，本文 §6.2 P5「3 周校准成果」即指这批学术锚点，无需重复列出 |

**G3 — `remote_integration_plan.md` §13「不整合的模块」：范围确认，非遗漏。**

glue 是 Node 6 内部改造（`aggregator.py` + 一次 `call_text`），**不触及 §13 任一模块**（Futu OpenD / GenerationBackend / Web 前端 / 多市场 / Decision Signal 生命周期 / Codex-OpenCode / 远端 `disagreement.py` 整体）。其中「远端 `disagreement.py` 仅吸收冲突分类学与 `RiskOverridePlan` 设计」已由现存 T3 `_arbitrate_contradictions` 承接，glue 不新增依赖。

## 8. 影子期与验收红线

> 本节回答「怎么算接对了」。沿用 `remote_integration_plan.md` §15.3 的「影子→转正」范式
> （T5 本身就是这么转正的，然后 48h 后 0/24），但针对 T5 的失败教训加严了两处：
> **① 指标必须可计数**（T5 影子期只看了「无异常」，而「全部 neutral」恰恰看起来就是「无异常」）；
> **② 必须有反向指标**（防止「接了但什么也没改」被当成成功）。

### 8.1 六条可计数指标

> **⚠️ 口径修正（2026-09-11 C 层修复）**：M1/M6 原表基线取自规则侧字段，但影子期 glue「接受但不使用」
> （`aggregator.py` 的 `status` 字段标记），规则侧字段不因 glue 而变 ⇒ M1 恒 3.1%、M6 恒 65.6%，
> 红线永不达成（死锁）。C 层修正：M1/M6 改为 **glue 侧**度量。详见 `got_layer_c_plan_0911.md` §1。
>
> **⚠️ 二次修正（2026-09-11 复审）**：上一版修正**把字段路径与数值都写错了** ——
> ① `agent_glue.json` 的 `relations[]` 实际键为 `[event_id, board_code, strength, direction, propagation_path, confidence]`，
> **既无 `news_check` 也无 `family`**；glue 侧的正确路径是 **`strengths[].news_read`** 与 **`families[].family`**。
> ② 上一版填的 `3.1%` / `65.6%` 是**规则侧恒值**，却标成「glue 侧」，**同一行内自相矛盾**；
> glue 侧实测为 **34.4%** / **96.9%**。
> ③ 「M1'/M6' 的命名与 C-P6/C-P7 的最终定义如何对齐」**已于 2026-09-14 落地**：M1 主口径取 glue 侧
> `strengths[].news_read`（= 本表 M1'）；M6 主口径取「其他」回收率（90.9%）。
> ⚠️ **它不是本表的 M6'**：M6' 量的是 glue 家族覆盖率（96.9%，分母为全部 **32** 个候选），
> 而回收率的分母是原「其他」板块（**11** 个）——**两者不是同一个量**。落地后 M6' 降为观察项。

| # | 指标 | 0903 基线 | 转正红线 | 数据源 | 当前可度量 |
|---|---|---|---|---|---|
| M1' | **glue 消息面非中性率**：glue `strengths[]` 中 `news_read` 非 `low`/`unknown` 的候选占比 | **11/32 = 34.4%**（0910 实测，glue 侧）<br>规则侧对照 = 1/32 = 3.1% | **≥ 60%** | `agent_glue.json` **`strengths[].news_read`**；规则侧对照取 `unified.json` `confirmed_sectors[].news_check` | ✅ |
| M2 | **事件归属命中率**：至少关联到 1 条 catalyst event 的候选占比 | **3/24 = 12.5%**（精确同名：AIGC概念/汽车整车/短剧游戏） | **≥ 70%** | `agent_glue.json` `relations` × `news.json` `events` | ⚠️ 需 ground truth |
| M3 | **误配率**：人工抽查 glue 归属中「该事件与该板块无实质传导关系」的占比 | 旧机制 **11/15 = 73%**（L931 自述） | **≤ 15%** | 人工抽查（每日 24 候选全量，≤10 分钟） | ⚠️ 人工 |
| M4 | **排序差异度**：见下方三条相关系数（⚠️ 前提：规则侧 tech/composite 未退化，否则 ρ 无诊断力） | — | 见下表 | 同日产出新旧两份 composite | ✅（影子期双跑） |
| M5 | **F1 降级率**：触发 §7.2 S1-S5 任一场景的交易日占比 | —（无 glue） | **≤ 10%**（10 日中至多 1 日） | 依赖 C3 `call_type` 落库 | 🔴 **当前不可** |
| **M6** | **「其他」回收率**（C-P7 主口径）：原「其他」板块中 glue 给出真实家族名的占比，「无归属」**计为未回收** | **10/11 = 90.9%**（0910 实测） | **≥ 80%** | 集合取自 `unified.json` `confirmed_sectors[].frame_group == "其他"`，命中判据取 `agent_glue.json` `families[].family` | ✅ |
| M6' | **glue 家族覆盖率**（**2026-09-14 起降为观察项**）：glue `families[]` 中 `family` 非「无归属」的占比 | **31/32 = 96.9%**（0910 实测，glue 侧）<br>规则侧对照 = 21/32 = 65.6% | —（观察项，不参与转正判定） | `agent_glue.json` **`families[].family`**；规则侧对照取 `unified.json` `confirmed_sectors[].frame_group` | ✅ |

#### M4 拆成三条相关系数（单一 Spearman 无诊断力）

| 子指标 | 红线 | 理由 |
|---|---|---|
| **M4-a** `ρ(glue.strength, tech_score)` | **≥ 0.6** | Agent 不应推翻 Node 2 的**技术面事实**（涨跌幅/量能/均线是硬数据）。低于 0.6 意味着 Agent 在用叙事覆盖数据，这是越权 |
| **M4-b** `ρ(glue.strength, 旧 composite)` | **< 1.0** | 🔴 **反向指标**。若 ρ=1.0，说明 Agent 只是重述了规则输出，**接线零价值**。T5 影子期的致命教训就是只验「无异常」不验「有改变」 |
| **M4-c** `ρ(glue.strength, 旧 composite)` | **≥ 0.3** | 下限。低于 0.3 说明 Agent 在乱打分（与技术面、与旧口径均无关联），属于失控而非创新 |

**M4-b/M4-c 合起来是「必须改变，但不能乱改」的双向约束**，这是本文相对 T5 影子期的核心加严。

> **为何 M2/M3 无法完全自动化**：归属正确性的 ground truth 是「SK 海力士 HBM 利多是否传导到光刻机」这类
> **市场共识判断**，不存在可查询的真值表。这正是 D2（接线层禁映射）的同一逻辑：
> 既然规则无法判定，那它也无法被规则验证 —— **只能人工抽查**。故 M3 必须设计成 ≤10 分钟/日的轻量流程，
> 否则影子期会因成本过高而被跳过（这是 T5 影子期「只看日志无异常」的真实成因）。

> ⚠️ **执行注记（2026-10-06，不改本节原设计）**：M3 的人工流程已按 OPEN-C2 钉成代码
> `scripts/build_m3_review_package.py`（按日分层抽 10：回收 5 + 冲突 3 + 随机对照 2，离线只读、不代判）。
> 负责人于 2026-10-06 裁决 **M3 直接以分层终审读数 2.8% 结案转正，放弃本表「每日候选全量」口径的复跑前置**
> ——依据是分层对难点超采（样本 72.2% vs 总体 55.4%）使读数**偏高**，拿偏高读数作验收属保守方向；
> 按层外推回全量口径 ≈2.2%，两读数同处 15% 红线内。结案后 stratified 降为日常预警（破线需人工复核、
> 不自动回滚）。读数表、机制发现与残留风险见 [`../verification.md`](../verification.md) §6 与
> [`../plans/architecture_optimization_implementation_20260929.md`](../plans/architecture_optimization_implementation_20260929.md) §11.8。

### 8.2 转正条件与开关命名

| 项 | 约定 |
|---|---|
| **影子期长度** | **≥ 10 个交易日**（不是 T5 的「2 周」—— 2 周含周末，实际只有 10 日，且 T5 转正后 48h 就暴露 0/24，说明 2 周不够） |
| **双达标** | M1/M4/M6（自动）**全部达标** 且 M2/M3（人工）**连续 5 日达标** 且 M5 ≤ 10% |
| **一票否决** | 任一日出现「glue 成功但 `classification` 全量翻转」（actionable 数变化 > 50%）→ 暂停影子期排查 |
| **开关命名** | `AGENT_SEMANTIC_GLUE_ENABLED`（默认 `false`）+ `AGENT_SEMANTIC_GLUE_SHADOW`（默认 `true`），与 `SECTOR_ALIGN_SHADOW`（`catalyst_config.py` L159-163）同构 |
| **回滚路径** | `AGENT_SEMANTIC_GLUE_SHADOW=true` → glue 照跑、结果只归档不生效；`AGENT_SEMANTIC_GLUE_ENABLED=false` → 完全走 F1 |
| **影子期双跑成本** | 每日额外 1 次 LLM 调用（`max_tokens≈5000`）。**规则侧 composite 必须同时保留计算**才能算 M4-b/M4-c —— 意味着影子期内 `_cross_check_*` 三件套**不能删**，只能断开对 `classification` 的影响 |

> ⚠️ **影子期与 §6.1 退役的时序矛盾（实施时必须注意）**：
> M4-b/M4-c 需要「旧 composite」作对照，而旧 composite 依赖 `_cross_check_*` 三件套。
> 故正确顺序是：**先接线（三件套保留但输出只归档不生效）→ 影子期 10 日 → 转正 → 才删三件套**。
> 这与 D1「先接线后调参」一致，但把「退役」推到了转正之后。**不可在影子期前删除 §6.1 的 17 项。**

### 8.3 验证基础设施缺失（3 项，全部是 §10 的前置）

| # | 缺失项 | 核查事实 | 影响 |
|---|---|---|---|
| **V1** | **无 GOT 日期回放入口** | `main.py` `parse_arguments()`（L267-442）共 **24 个参数**，有 `--got`（跑当日 6 节点）、`--backtest` / `--backtest-code` / `--backtest-days` / `--backtest-force`（**个股维度**的历史结果评估），**无任何 `--replay-date` / `--got-date` / `--as-of` 类参数** | 无法用 0903 数据重跑 Node 6。M4 的「同日双跑」无从实施 |
| **V2** | **无 Context 反序列化器** | `src/got/archive.py`（782 行）只有 `load_phase2_decision(run_date)`（L753，L774 `json.loads(decision_path.read_text())`），**只读 Phase 2 的 `decision.json`**。写入侧 `archive_daily_run(run_date=...)`（L58）支持指定日期，但**无对应的 Node 1-5 contexts loader** | 即使写了回放入口，也无法把 `market.json` / `sector.json` / `capital.json` / `news.json` / `sentiment.json` 还原成 pydantic Context 对象喂给 `AggregatorNode.run()` |
| **V3** | **Node 6 测试覆盖极薄** | `tests/` 下 203 个 `.py`，**只有 1 个**与 Node 6 相关：`tests/test_node6_regime_state.py`（仅覆盖跨日状态机）。`tests/` 下**无 `got/` 子目录**（只有 `fixtures/` 与 `services/`，后者 3 个文件） | `_classify_all_sectors` 的 6 条护栏、`_build_sector_intel` 的 4 路候选收集、`_synthesize_mainline_endurance` **全部无测试**。改这些函数没有回归网 |

**V1+V2 合起来 = 本项目当前无法对 GOT 做任何离线验证**。这是比 glue 本身更基础的缺口：
没有回放能力，§8.1 的六条指标只能靠**等实盘每天跑一次**积累，10 个交易日 = 2 周日历时间，
且期间无法重跑修正 —— 一旦某日 glue 出错，那天的影子数据就永久缺失。

**故 §10 把 V1+V2（回放 harness）列为 P0-A 的第一项，且是 A5（开影子期）的硬阻塞。**

## 9. 挂起项（OPEN）

> 本节记录 grilling 过程中发现、但**用户明示不在本轮射程**的问题。每项附已核查的事实，
> 供后续开专项时直接接手，**不要求本轮实施**。

### OPEN-1：仓位链路（用户明示「仓位先不管，后续处理」）

#### 已验证的 4 条事实（本轮复核）

| # | 事实 | 代码位置 | 后果 |
|---|---|---|---|
| **W1** | **仓位超限无减仓路径**：`position_gap <= 0`（即 `new_market_value > target_position_value`，仓位已达标或已超限）时，代码**不减仓，反而继续买入** `min(sell_proceeds * 0.8, available_cash)` | `portfolio_decision.py` L2422-2430（L2429 注释自述为「高切低轮动」） | 卖出**仅由个股 `action_recommendation` 驱动**（L2360-2362 `if action == "sell"` / L2387 `elif action == "reduce"`），**无「组合层仓位超限」驱动的减仓**。若全部持仓都是 hold，超限状态会持续 |
| **W2** | **原方案 §4.4「两阶段优化器」的结构已经存在** | L2346「Step 1: 生成卖出建议并计算释放资金」→ L2415「Step 2: 计算买入预算」→ L2437「Step 3: 生成买入建议」 | ✅ **好消息**：OPEN-1 不需要重构，只需给 **Step 1 补一个卖出来源**（仓位超限强制减仓）。实施成本远低于原方案预估 |
| **W3** | **`total_equity` 与 `accounts[0]` 口径不一致**：`total_equity = portfolio.get("total_equity")` 是**全账户合计**，而 `positions = accounts[0].get("positions", [])` **只取第一个账户** | L178+L180（`_assess_risk`）、L294-295+L304（`_estimate_current_drawdown`）、L2340+L2336（`_allocate`）、L383、L1959 —— **共 5 处** | 多账户时 `current_position = total_market_value / total_equity` 的分子分母口径错配，风险判定偏松；`_allocate` 用全账户 equity 算 `target_position_value`（L2344）但只操作 `accounts[0]` 的持仓 → **超额买入** |
| **W4** | **从 `reasoning` 字符串里解析板块名**：`sector_limits.get(score.reasoning.split('[')[0].strip() if '[' in score.reasoning else ...)` | L2467-2468 | 脆弱耦合：`reasoning` 格式一变（而 §6.1 #17 正要改 `_llm_refine_sectors` 写入 `reasoning` 的逻辑），**埋伏仓的 0.55 预算系数与 5% 止损会静默失效** → 降为主线仓的 1.0 / 7% |

> 🔴 **公开撤销一条错误断言**（遵循 `remote_integration_plan.md` §5 的事实核查约定）：
> grilling 过程中我曾断言「`base_position` 是死参数（L2463）」。**复核结果：错。**
> `base_position` 仅出现于 L327（形参）/ L338（docstring）/ L358（`scaled = base_position * multiplier`），
> 属 `_apply_drawdown_scaling`（def@L324），且该函数**被 L184 正常调用**（`_assess_risk` 内）。
> L2463 实际是 `logger.warning(f"[Phase 2] 无法获取 {score.symbol} 价格，跳过")`，与 `base_position` 无关。
> **该断言作废，不列入 OPEN-1。**

#### OPEN-1b：`REFINE_MAX_SECTORS` 混合口径（连带后果 ④）

| 项 | 事实 |
|---|---|
| 配置 | `catalyst_config.py` L169-170：`REFINE_TOP_N = 10`（基础片，composite 降序前 N）、`REFINE_MAX_SECTORS = 20`（视野上限 = 基础片 + 埋伏候选追加片） |
| 0903 实测 | 当日 `confirmed_sectors = 24`，**> 20** ⇒ **至少 4 个板块从未进入 LLM 视野** |
| 混合口径风险 | 仓位公式 `_compute_position_from_regime`（L231）按**全部 24 个** `sector_intels` 统计，而 LLM 只看过其中 **20 个** ⇒ 同一份决策里两个不同口径的集合被当成同一个用 |
| D5 之后的变化 | glue 的视野 = **全部候选**（§5.2），`REFINE_MAX_SECTORS` 对 glue **不适用** → **混合口径自动消失**。但 `_llm_refine_sectors` 的 narrative 分支仍用该上限，需确认是否同步扩充 |
| 处置 | **归入 OPEN-1**（因为它影响的是仓位公式的输入口径）。glue 接线后需**重新核算**仓位公式在「24 个新语义 composite」下的行为 |

### OPEN-2：情绪面信号只到展示层

| 项 | 事实 |
|---|---|
| 生产侧 | `aggregator.py` L2548 `contrarian_alert = getattr(stage_state, "contrarian_alert", False)`；L2577 触发条件 `if contrarian_alert and momentum_dir == "cooling" and acceleration is not None ...`；L2578 `components = {"contrarian_signal": 0.75, "capital_withdrawal": clip(-acceleration, 0.0, 1.0)}`；L2587 `"contrarian_alert": True` |
| schema | `schemas.py` L523（文档）/ L532 `contrarian_alert: bool` / L541 `trading_signal: str ∈ {contrarian_sell, trend_following, neutral, ...}` / **L560 `contrarian_signal: bool`** |
| 消费侧 | `archive.py` **L445** `if stage_state.contrarian_alert:` —— 仅用于**归档渲染**；`aggregator.py` L3907-3908 / L3926 `contrarian_alert_today` —— 仅写**监控 JSON** |
| 缺陷 | `contrarian_signal` / `trading_signal` **不进入仓位公式也不进入准入判定**，是纯展示态。原方案 §7.4「情绪面直接注入仓位公式」属 **OPEN-1** 射程 |
| 与 glue 的关系 | **无直接耦合**。glue 只消费 `sentiment` 的 `hot_sectors`（`_extract_hot_sectors` L2798）；`contrarian_*` 系列在 Node 6 内部，不经过 miniGOT |

### OPEN-3：exhausted 判定与当日涨幅

| 项 | 事实 |
|---|---|
| 现行判据 | `sector_analysis.py` **L2265** 注释：`BROKEN = OR(5日涨幅≤0, 跌破MA20, 过热exhausted且20日高位<0.60, 单日崩≥20, ...)`；**L2280** `or (overheat_level == "exhausted" and high20_pos < 0.60)` |
| 原方案依据已失效 | 原方案 §10.2 引的 `one_day_drop` 判据**已于 2026-09-02 P0-3 移除** ⇒ §10.3「新增当日反弹缓冲」**需按现行 4 条判据重新评估**，不可直接实施 |
| 0903 实测 | `exhausted_mainline` **63 条 / 64,744 B / 占 sector.json 48.0%** —— 是归档里最大的单一字段，但**不喂 Agent**（§5.2）、必须保留在归档（§6.2 P3） |
| 与 glue 的关系 | **间接**：`_synthesize_mainline_endurance` 的 `detail_lookup`（L2956-2960）会遍历 `exhausted_mainline`，而续航评估受 §7.1 E2 影响 |

### OPEN-4：`INDEX_CONFIG` 权重配对三重缺陷（**比原方案 §11 严重，且是 §11.3 的实施前置**）

```python
# market_analysis.py L42-47
"tech_growth": {
    "indices": ["399006.SZ", "000688.SH"],              # key 空间：指数代码
    "weights": {"chinext": 0.6, "star50": 0.4},         # key 空间：别名 ← 与上面不同！
    "aggregation": "weighted_average_with_consistency",
}
# L316-318: scores 按 indices 顺序 append
# L342-345
weights = config.get("weights", {})
weight_values = list(weights.values())
weighted_scores = [s * w for s, w in zip(scores, weight_values)]
base_score = sum(weighted_scores) / sum(weight_values)      # ← 分母是全部权重之和
```

| # | 缺陷 | 场景推演 | 后果 |
|---|---|---|---|
| **X1** | **`zip` 静默截断 + 分母不归一** | 科创50（`000688.SH`）获取失败 → `scores` 只剩 1 个 → `zip([s], [0.6, 0.4])` 只产出 1 对 → `base_score = (s × 0.6) / (0.6 + 0.4) = s × 0.6` | **创业板指技术分被静默压低到 60%**，而 `tech_growth` 正是 `_compute_market_regime` 的 ADX/regime 来源（`aggregator.py` L334-360）⇒ **直接影响 regime 判定与 `risk_budget`**。无任何告警（L109-110 只在**全部**失败时 raise） |
| **X2** | 🔴 **原方案 §11.3 的修法会直接踩坑** | §11.3 把 `indices` 扩成 3 个（加 `000905.SH` 中证500）但**不改 `weights`** → `zip([s1,s2,s3], [0.6,0.4])` 只产出 **2 对** | **中证500 被静默丢弃，原方案修法完全无效**，且无任何报错提示「你加的指数没用上」。故 **X1 是 §11.3 的硬前置** |
| **X3** | **两套 key 空间无校验，靠人工维护顺序** | `indices` 用指数代码、`weights` 用别名（`chinext`/`star50`），**无任何代码校验对应关系**。若有人将 `weights` 写成 `{"star50": 0.4, "chinext": 0.6}`（顺序调换） | **创业板指拿 0.4、科创50 拿 0.6**，静默错配，无告警 |
| **X4** | **降级到单指数时「一致性」恒为真** | L348-349 `signs = [...for s in scores]`；`same_direction = all(s == signs[0] for s in signs) and signs[0] != 0`。当 `scores` 只有 1 个时 `all()` **恒 True** | `consistency_discount = 1.0`、`consistency_label = "aligned"` —— **单指数时「跨指数一致性」退化为无意义的恒真**，这是原方案 §11.2「`consistency: "aligned"` 基于 2 个指数」的更糟版本 |

**修法方向**（待 OPEN-4 专项）：`weights` 的 key 改为**指数代码**（与 `indices` 同一 key 空间），
配对改为 `weights.get(code)` 而非 `zip`，分母改为**实际参与配对的权重之和**，
并在 `len(scores) != len(indices)` 时显式告警 + 降级 `consistency_label = "degraded"`。

### OPEN-5：数据完整性自检（对应原方案 §9.3，列为 P0-B）

| # | 事实 | 位置 |
|---|---|---|
| **Y1** | `missing_fields=[]` **硬编码恒空** | `aggregator.py` **L3282**（`_build_data_quality` L3258-3284）。该函数只填 `available_fields`（L3267 固定 5 个字符串）与 `fetch_failed_fields`（L3270-3278 按 `status == "failed"`） |
| **Y2** | 0903 实例：`sector.json` 的 `crowded_families` **len=0**、`notes` **len=2 B**（空数组） | 而 `_classify_all_sectors` 的组拥挤拦截（L1180 附近）**刻意不用** `crowded_families`（代码注释自述：「其家族含 `[其他]` 这类百员杂物家族」），改为自算 `hot_count` ⇒ **Node 2 产出的该字段实际无消费者** |
| **Y3** | 0903 实例：`lifecycle_state` **13/13 全 null** | 而 `_is_hard_exclude`（L1240-1250）**依赖 `detail.lifecycle.phase`** 判 `late_stage`/`mature_exhausted` ⇒ 该分支在 0903 **全量空转** |
| **Y4** | §7.2 F1-8 只打通 `agent_semantic_glue` **一个字段** | 原方案 §9.3「字段级完整性检查」仍需单独做。两者不重叠：F1-8 是「glue 缺席要可见」，OPEN-5 是「上游数据缺席要可见」 |
| **优先级** | **P0-B**。Y3 尤其重要：`_is_hard_exclude` 的 lifecycle 分支是 §6.2 P5 保留的 5 条护栏之一，若 `lifecycle` 恒 null，**这条护栏实际不存在**，而无人知晓 |

## 10. 统一实施路线图（合并 glue 与 `remote_integration_plan.md`）

> 本节把 glue（Node 6 纵向重构）与 `remote_integration_plan.md`（远端模块横向整合）合并为**单一路线图**。
> 合并的必要性：两份文档的顺序存在**交叉**（同改 `aggregator.py` / `market_analysis.py`）与**共享基底**
> （超时 / call_type / 回放 / temperature 是双方前置）。各按各的路线图推进会撞车。
> **优先级总原则**：涉及**代码现状**的判断以 glue 为准（09-03 最新、逐条核查真实代码）；涉及**远端模块范围/规格**以 remote 为准（glue 不涉及新模块）。

### 10.1 跨文档冲突裁定

**A 类｜事实性矛盾（glue 优先，需修订 remote）**

| # | 冲突点 | remote 原文 | glue 核查（权威） | 裁定 | 阻塞 |
|---|---|---|---|---|---|
| **A-1** | call_type 用量归因 | §14.1 A4「24 处 `call_text` 各自随意传 `call_type`，命名不统一」 | §6.3 R5：`call_text`（`llm_adapter.py` L486-494）**无 `call_type` 形参**；`src/got/` 下 `persist_llm_usage` **0 处调用** → GOT 用量**完全不落库** | 按 glue 修订 remote §14.1 A4（问题从「命名不统一」改为「完全不落库」，修法从「统一命名」改为「适配器层加落库」） | 🔴 是（Phase 0） |
| **A-2** | 编排器超时 | §14.2 B1「`agent_orchestrator_timeout_s` 默认 0」 | §6.3 R3 + §2.2：该值 **config 默认 600 非 0**，但 single 架构不读它；**真问题是 `call_text` 无 `timeout` 参数**，`_call_llm_async` 经 `asyncio.to_thread(call_text)` 未传超时 | 按 glue 修订 remote §14.2 B1（重定问题与修法） | 🔴 是（Phase 0） |

**B 类｜范围误标（remote 把 glue 不需要的前置当通用前置）**

| # | 冲突点 | 裁定 | 修订 |
|---|---|---|---|
| **B-1** | §14.1 A1/A2 标为「GOT 对接**前置**」 | glue R4：glue 走**单次 `call_text`** 不触及 `AgentMemory`；A1（校准分母）/A2（记忆回填）仅是 remote **§8 分歧摘要（multi 架构）** 的前置，**不阻塞 glue** | remote §14.1 A1/A2 注明适用边界：「仅 §8 multi 架构前置，Node 6 glue 不需要」 |
| **B-2** | §15.6「审查层每日 LLM 调用 ≤ 20」 | glue R7：该限定语约束 **T1/T1-H**，**不约束 Node 6**；glue 成本另计（§5.4，单次全量调用） | remote §15.6 明确限定语「审查层」= T1/T1-H |

**C 类｜共享基底（两文档都需要 → 合并为 Phase 0，只做一次）**

| 地基项 | glue 出处 | remote 出处 | 共同受益 |
|---|---|---|---|
| 回放 harness | §8.3 V1+V2 / §10.4 A1 | §12.3（`--replay-date` 已证伪） | glue 影子 M4；remote §8 前后对比 |
| call_type 落库 | §6.3 R5/R6 / §7.3 C3 / A2① | §14.1 A4 | glue M5；remote §8 审议、§15.6 预算 |
| 超时传参 | §6.3 R3 / F1-S7 / A2② | §14.2 B1 | glue F1 降级；remote §15.2 审查层 |
| temperature 传参 | §7.3 C1（glue 要 =0） | §14.2 B2（审查层要 =0.2） | 适配器支持形参后各取所需 |

> 裁定：**必须最先做**。这是 glue A5（影子）与 remote §8/§15 的**共同硬阻塞**。
> remote §11 把 §14.1 A 类排到 Week 4 是**最大排期错误**，须提前到 Phase 0。

**D 类｜潜在耦合（同文件/同下游，协调顺序即可，非矛盾）**

| # | 耦合 | 协调 |
|---|---|---|
| **D-1** | remote §8 分歧摘要 vs glue **同改 `aggregator.py`**（glue 保留 `_arbitrate_contradictions` 仅加 call_type；§8 重构矛盾检测） | glue 先 → §8 后，且 §8 须保留 glue 埋的 call_type |
| **D-2** | remote §5 MarketThemeContext vs glue Segment E（**两处调 confidence**） | 划清层级：MarketTheme = 全局 regime 层，glue = 板块层；§5 第一步仅接口预留，不冲突 |
| **D-3** | remote §7 AlphaSift vs glue D10（glue 改 `actionable` 判定 → **移动 AlphaSift 归零率基线**） | glue A6 转正后，重标 AlphaSift 影子基线 |
| **D-4** | remote §6 Intelligence vs glue 事件侧 | 无冲突：Intelligence 保持 `NewsContext` 格式；glue 语义归属不依赖 `affected_sectors`（鲁棒） |
| **D-5** | remote §3 Tencent vs glue regime | **正向协同**：修 Node 1 指数降级 → 改善 `tech_growth` → 改善 glue regime 输入 |
| **D-6** | remote §4 指数注册表 vs glue OPEN-4（**同改 `market_analysis.py` `INDEX_CONFIG`**） | 合并：§4 实施时一并修 OPEN-4 X1-X4，否则加中证500 踩 X2 静默丢弃 |

**E 类｜确认无冲突**
- **canonical_id**（remote §4/§9）针对**指数/股票**；glue `board_code` 是**板块 TDX 码** → 不同实体，无重叠。
- **禁映射 scope**：glue D2 约束 **Node 6 事件-板块接线层**；remote §4 `IndexRegistry` 别名是**数据层身份解析** → 不同 scope；glue §6.1 #13 保留 `board_aliases.py` 供 event_id 对账，不违反 D2。
- **问财 NL**（glue D3）与 remote 数据源策略一致（都确认东财 push2 单股/板块排名已被封）。
- **影子→转正范式**：glue §8 明确沿用 remote §15.3，是有意复用非重复。

### 10.2 `remote_integration_plan.md` 模块整合地图（摘要 + 引用原文）

> 下表只保留路线图所需的**摘要与关系**；各模块的**实现细节**（Tencent 字段规格、AlphaSift 10 策略、Intelligence API、指数注册表结构等）见 remote 对应章节，本文不复制。

| remote 模块 | 摘要 | 与 glue 关系 | 落入 Phase | 细节 |
|---|---|---|---|---|
| §3 Tencent 指数 fallback | 新增 `TencentFetcher` 接入源序，修 Node 1 指数降级 | 协同（D-5）：改善 glue regime 输入 | Phase 2 | remote §3 |
| §4 指数注册表 canonical_id | `IndexEntry`/`parse_analysis_target`，消歧 `000001` | **合并修 OPEN-4**（D-6） | Phase 2 | remote §4 |
| §5 MarketThemeContext | Node 2 题材阶段 + 龙头角色；两步走 | 划清 confidence 层级（D-2） | Phase 4 | remote §5 |
| §6 Intelligence 情报 | 替代 news_feeds 5 源，SQLite 2 表，Node 4 消费 | 无冲突（D-4），glue 鲁棒 | Phase 4（可提前） | remote §6 |
| §7 AlphaSift 选股 | YAML 策略引擎替代 5 轮漏斗，保留 P5+T1 | glue 下游；转正后重标基线（D-3） | Phase 4 | remote §7 |
| §8 分歧摘要 + 审议 | `SkillAggregator` 基座 + 11 类冲突分类 + `RiskOverridePlan` | **同改 aggregator.py**（D-1）；依赖 Phase 0 | Phase 3 | remote §8 |
| §9 canonical_id 双写 | `stock_daily` 加列，只写不读 | 无冲突 | Phase 5 | remote §9 |
| §10 TickFlow | 第二 P0 数据源，待 API Key | 无冲突 | Phase 5 | remote §10 |
| §14.1 A 类修复 | A1/A2 AgentMemory、A3 单代理记忆、A4 call_type | **A4→Phase 0**；A1/A2→§8 前置（Phase 3，非 glue 前置，B-1） | Phase 0 / 3 | remote §14.1 |
| §14.2 B 类修复 | B1 超时、B2 temperature、B3 工具结果上限… | **B1/B2→Phase 0**；B3 补「筛选≠截断」（glue R2） | Phase 0 | remote §14.2 |
| §15 T1/T1-H 审查层 | 个股终审修复 + 持仓审查，共用 review profile 基座 | 共享 Phase 0（超时/temp/call_type）；**预算独立**（B-2） | Phase 3 | remote §15 |

### 10.3 合并后的 Phase 0-5 路线图

```
Phase 0  共同地基（最先，唯一入口）
  ├─ call_type 落库（适配器层加形参 + persist_llm_usage）   [glue A2① / remote §14.1 A4]
  ├─ 超时传参（call_text 加 timeout，_call_llm_async 透传）  [glue A2② / remote §14.2 B1]
  ├─ temperature 传参（call_text 加形参，各调用点自取）      [glue §7.3 C1 / remote §14.2 B2]
  └─ 回放 harness（load_daily_contexts + --got-replay）      [glue A1 / remote §12.3]
        │
        ├────────────► Phase 1  glue 接线（改 aggregator.py）
        │                  A3 抽候选 → A4 _agent_semantic_glue → A5 影子≥10日 → A6 转正+退役17项
        │                  并行支：P0-B 数据完整性（B1/B2/B3）
        │
        └────────────► Phase 2  remote P0 数据源（data_provider/ + market_analysis.py，与 Phase 1 文件不重叠）
                           §3 Tencent fallback ∥ §4 指数注册表（合并修 OPEN-4 X1-X4）
                                    │
   Phase 1 的 A6 转正 ───────────┴──► Phase 3  remote §8 分歧摘要（同改 aggregator.py，须排在 A6 后）
                                        + §15 T1/T1-H 审查层（共享 Phase 0 基座，预算独立）
                                        + §14.1 A1/A2（§8 multi 架构前置）
                                              │
                                              └──► Phase 4  remote P1/P2
                                                     §5 MarketThemeContext ∥ §6 Intelligence ∥ §7 AlphaSift
                                                     （§7 须用 glue 转正后的归零率基线，D-3）
                                                          │
                                                          └──► Phase 5  长尾
                                                                 glue P1 调参（转正+影子验收后）
                                                                 + remote §9 canonical_id 双写 + §10 TickFlow（待 Key）
                                                                 + OPEN-1 家族仓位
```

**排序要点**
- **必须最先**：Phase 0 四项地基。其中 **call_type 落库 + 超时传参是双重硬阻塞**（无它则 glue M5 不可度量、F1 降级失效、remote §15.6 预算无数据源）。
- **前后依赖**：glue `A3→A4→A5→A6` 严格串行；remote §8 依赖 Phase 0 且**必须排在 glue A6 之后**（同改 aggregator.py）；remote §7 AlphaSift 依赖 glue 转正（基线重标）。
- **可并行**：Phase 1（glue，`aggregator.py`）与 Phase 2（remote P0，`data_provider/` + `market_analysis.py`）**文件不重叠**（唯一交点 §4×OPEN-4 已合并处理）；glue P0-B 与 A3/A4 并行；§6 Intelligence 相对独立可提前。
- **必须等影子/验收后**：glue A6 的 **17 项退役**须影子 ≥10 交易日双达标后；glue P1 调参、AlphaSift 转正、T1 否决权生效，均须各自影子验收通过后。

### 10.4 glue 详细步骤（P0-A / P0-B / P1，落入上述 Phase 0 / 1 / 5）

> 映射：**A1 / A2 = Phase 0 共享地基**；**A3-A6 = Phase 1**；**P0-B = Phase 1 并行支**；**P1 = Phase 5**。
> 依赖关系：**A1 与 A2 是 A5 的硬阻塞**（无回放能力则影子期只能靠实盘逐日积累；
> 无 `call_type` 落库则 M5 不可度量）。**A6（退役）必须在转正之后**（§8.2 的时序矛盾）。
> remote 模块的详细步骤见其原文对应章节，本文不复制。

### P0-A：接线（本轮射程）

| 步 | 动作 | 文件 | 验收 | 阻塞 |
|---|---|---|---|---|
| **A1** | **建 GOT 回放 harness**（补 V1+V2）：① 新增 `load_daily_contexts(run_date) -> (Market, Sector, Capital, News, Sentiment)`，把 5 个 json 反序列化为 pydantic Context；② `main.py` 新增 `--got-replay YYYYMMDD`，只跑 Node 6（+可选 Node 7），**不重跑 Node 1-5**、**不写归档**（写到 `data/got_daily/{date}/_replay/`） | `src/got/archive.py`（新增 loader）、`main.py`（L286-442 argparse）、可选 `scripts/replay_got.py` | 用 0903 归档回放，输出的 `confirmed_sectors` 与 `unified.json` 存档**逐字段一致**（24 条、composite/classification/frame_group 全等） | 🔴 **阻塞 A5** |
| **A2** | **补可观测性基座**（§6.3 R3/R5 + §7.3 C3）：① `_call_llm_async`（L3826-3855）增加 `call_type` 形参并调 `persist_llm_usage(response.usage, model, call_type=...)`；② 给 `call_text` 传 `timeout`；③ 现有 4 个调用点分别传 `got_node6_refine` / `got_node6_nominate` / `got_node6_arbitrate` / `got_node6_override` | `src/got/nodes/aggregator.py`、`src/got/catalyst_config.py`（超时常量） | `get_llm_usage_summary` 能按 `by_call_type` 查出 Node 6 的 4 类调用（L3363-3391）；断网时 glue 在超时后返回 `""` 而不是挂死 | 🔴 **阻塞 A5**（M5 无数据源） |
| **A3** | **抽出候选收集**：把 `_build_sector_intel`（L533-616）的 Segment A 抽为 `_collect_sector_candidates(sector, ambush_pool=None) -> (candidates, member_to_group)`。**不改 async**。同时补 `tests/test_node6_candidates.py`（补 V3） | `src/got/nodes/aggregator.py`、`tests/` | 抽出后 `_build_sector_intel` 输出与抽出前**逐字段一致**；新增测试覆盖 4 路候选收集 + 0903 基线（24 条） | 阻塞 A4 |
| **A4** | **实现 `_agent_semantic_glue`**：① 按 §5.2 组装输入（只喂候选 + events 去 4 个冗余字段 + `major_events`）；② `temperature=0`、`max_tokens≈5000`、`call_type="got_node6_semantic_glue"`；③ 按 §5.3 解析输出，校验受控词表 / `[0,100]` 边界；④ 结果落 `agent_glue.json`（C2）；⑤ **调用位置：L188 之前**（§6.1 「时序修订」），结果作为参数传给 `_build_ambush_channel` 与 `_build_sector_intel`；⑥ 实现 §7.2 F1 全部 8 行降级契约（含 F1-7 `llm_refinement_used` 去硬编码、F1-8 `missing_fields` 打通） | `src/got/nodes/aggregator.py`、`src/got/schemas.py`（glue 输出结构）、`src/got/catalyst_config.py`（两个开关） | 回放 0903：`agent_glue.json` 生成且字节 ≤ 90K；**关开关时输出与 A3 基线一致**（证明 F1 不改变现有行为） | 阻塞 A5 |
| **A5** | **开影子期**：`AGENT_SEMANTIC_GLUE_ENABLED=true` + `AGENT_SEMANTIC_GLUE_SHADOW=true`。glue 照跑、结果只归档不生效；**三件套保留计算**（供 M4-b/M4-c 对照）。每日人工抽查 M3（≤10 分钟） | 配置 + 每日归档 | ≥ **10 个交易日**，按 §8.2 双达标；任一日触发一票否决则暂停排查 | 阻塞 A6 |
| **A6** | **转正 + 退役**：① `AGENT_SEMANTIC_GLUE_SHADOW=false`；② **才**执行 §6.1 的 17 项退役；③ 同步改 4 个调用点（§5.4）：L216 退役、L219 删 `adjustments` 分支、L224 改查 `glue.relations`、L274 回填 `frame_group`；④ 落地 §7.1 E2c 公式；⑤ 执行 §6.3 的 7 条 `remote_integration_plan.md` 修订 | `src/got/nodes/aggregator.py`、`docs/remote_integration_plan.md` | 全量测试通过；回放 0903 的 M1/M6 达红线；`_cross_check_*` 三件套无残留引用 | — |

> ⚠️ **A1 的验收为何是「逐字段一致」而不是「大致相同」**：
> Node 6 内部有跳日状态机（`_apply_regime_state_machine` L179，读前日归档）与 EMA 基线回写（L314-316）。
> 回放时必须能**控制这两个副作用**，否则回放会污染真实状态。建议回放路径默认设
> `DISABLE_REGIME_STATE_MACHINE=true`（L176 已有该开关）并跳过 `_save_context`（L311）/ `_update_monitoring`（L319）。

### P0-B：数据完整性（对应 OPEN-5，可与 P0-A 并行）

| 步 | 动作 | 验收 |
|---|---|---|
| **B1** | 打通 `_build_data_quality`（L3258-3284）的 `missing_fields`：按字段级检查上游 5 个 Context 的关键子字段（至少覆盖 `sector.crowded_families` / `sector.active_mainline[].members[].lifecycle` / `news.catalyst_calendar.events` / `capital.sector_flow_all`） | 0903 回放能自动报出 Y2（`crowded_families` 空）与 Y3（`lifecycle` 13/13 null） |
| **B2** | 处理 Y3：`_is_hard_exclude`（L1240-1250）的 lifecycle 分支在 `lifecycle` 全 null 时**等于不存在** → 要么修上游让 `lifecycle` 有值，要么在 `missing_fields` 里显式标记「护栏 P5-1 本日空转」 | 护栏空转**可见**（不要求修好，要求不静默） |
| **B3** | 处理 Y2：`crowded_families` 无消费者 → 要么 Node 2 停产（省 2 B），要么修正其家族口径后给 `_classify_all_sectors` 用 | 二择一，不留「产了不用」的字段 |

### P1：调参（**转正之后**，对应 D1「先接线后调参」）

| 项 | 说明 |
|---|---|
| **P1-1** | `_classify_all_sectors` 的 `score >= 50`（L1207）与 `_is_hard_exclude` 的 `composite < 40`（L1244）：两个绝对阈值是按**旧 composite 语义**校准的。glue 转正后 composite = Agent strength × 100，**分布会变**，必须用影子期 10 日数据重新定分位（建议改为当日分位数而非绝对值） |
| **P1-2** | `OVERHEAT_ADMISSION_LIMIT = 0.3`（L125）、`top_n = 12`（L646）、`_wave_coeff`（L99-114）：准入护栏本身**不动**（D5），但其输入分布变了，需重新验证 0903 类场景下的 actionable 数量是否合理 |
| **P1-3** | `CATALYST_CONFIDENCE_GATE = 0.7`（`catalyst_config.py` L147，注释自述 `TODO: 待校准 (当前样本量: 0)`）：glue 提供归属后，**样本量才第一次不为 0**，此时才能真校准 |
| **P1-4** | 仓位链路（OPEN-1）：在 glue 转正、composite 语义稳定之后才开专项。W2 已证明只需给 `_allocate` Step 1 补一个卖出来源 |

---

## 附录 A：0903 实测数据基线

> 全部数据由本轮 Python 探针从 `data/got_daily/20260903/` 归档直接核出，**非估算**。
> 后续实施完成后，用同一探针重跑并对比本表，即可量化改进幅度。

### A.1 `sector.json`（紧凑 JSON = **134,931 B**；磁盘 192,842 B 是缩进后大小）

| 字段 | 字节 | 占比 | 条数 | 喂 Agent |
|---|---|---|---|---|
| `exhausted_mainline` | 64,744 | **48.0%** | 63 | ❌（注意力污染） |
| `active_mainline` | 11,686 | 8.7% | 4 组 / 11 成员 | ✅ |
| `theme_families` | 8,870 | 6.6% | 32 | ❌ |
| `near_miss_ambush` | 8,801 | 6.5% | 9 | ❌ |
| `ambush` | 7,643 | 5.7% | 8 | ✅ |
| `industry_top` | 6,600 | 4.9% | 10 | ❌ |
| `frame_group_lookup` | 5,542 | 4.1% | 217 | ⚠️（只喂候选部分） |
| `cooling_down` | 5,150 | 3.8% | 5 | ✅ |
| `top_by_score` | 4,654 | 3.4% | 5 | ❌ |
| `strength_lookup` | 4,399 | 3.3% | 217 | ⚠️（同上） |
| `overheat_lookup` | 2,247 | 1.7% | 110 | ⚠️（同上） |
| `defensive_rotation` | 1,946 | 1.4% | 2 | ❌ |
| `reasoning` | 1,791 | 1.3% | str | ❌ |
| `data_quality` | 487 | 0.4% | 8 | ❌ |
| `crowded_families` / `notes` | 2 / 2 | 0.0% | **len=0（空）** | — |
| **候选集合计** | **31,600** | **23.4%** | **24** | ✅ |

**`active_mainline` 4 组结构**：`旅游`（tier=A，members=3：旅游/旅游景点/酒店餐饮）、
`银行`（tier=A，members=2：银行/保险）、`短剧游戏`（tier=S，members=3：短剧游戏/AIGC概念/华为鸿蒙）、
`知识产权`（tier=A，members=3：知识产权/C2M概念/网红经济）。

**候选集核算**（与 `_build_sector_intel` L549-615 一致）：
mainline-group 4 / 11,678 B + mainline-member 7 / 7,155 B + ambush 8 / 7,627 B + cooling 5 / 5,140 B
= **count 24 / 31,600 B / 23.4%** —— 精确等于 `confirmed_sectors = 24`，证明当日池外补入 **0 个**（§6.1 时序修订的关键依据）。

### A.2 `news.json`（**73,629 B**）

| 字段 | 字节 | 占比 | 条数 |
|---|---|---|---|
| `catalyst_calendar` | 62,797 | **85.3%** | **dict，7 个 key** |
| └ `events` | 62,612 | — | **107** |
| `major_events` | 7,511 | 10.2% | 13 |
| `reasoning` | 1,093 | 1.5% | str |
| `key_risks` | 949 | 1.3% | 5 |
| `concept_suggestions` | 753 | 1.0% | 3 |
| `data_quality` | 326 | 0.4% | 8 |

**`catalyst_calendar` 的 7 个 key**：`status` / `target_date` / `window_days` / **`events`** /
`last_full_refresh` / `refresh_mode` / `total_trading_days_since_refresh`。
⚠️ 它是 **dict 不是 list**（grilling 过程中我曾误作 list 处理，导致 `KeyError: 0`）。

**events 单条 avg 585 B，15 字段**，其中 **4 个冗余字段**（占 ~15% 字节）：

| 冗余字段 | 冗余原因 |
|---|---|
| `consensus_expectation` | = `title` 加前缀（样例：`全球事件传导: SK 海力士/三星财报及 HBM 指引`） |
| `category_tag` | = `direction`（样例两者均为 `drift`） |
| `is_global` | = `event_type` 的布尔投影 |
| `created_at` | **107/107 全 `None`** |

**events 统计**：带 `affected_sectors` 的 **36/107 = 33.6%**；`confidence ≥ 0.7` 有 **69 条（64.5%）**
（分布：1.0×3 / 0.8×49 / 0.75×3 / 0.7×14 / 0.65×1 / 0.6×32 / 0.55×1 / 0.5×4）；
`direction`：positive 43 / drift 29 / binary 15 / negative 12 / fade 8。

**`affected_sectors` 标签全集 = 53 个**（高频项）：
AI应用 5 / AIGC概念 4 / 多模态AI 4 / AI芯片 4 / 新能源车 4 / 半导体 3 / 汽车整车 3 / 消费电子 3 /
AI手机 3 / 医疗器械 3 / 算力概念 3 / 智能电网 3 / 特高压 3 …（尾部均为 1-2 次）。

### A.3 0/24 的机制证明（§3.4 的数据基础）

**53 个事件标签 vs 24 个候选，精确同名命中仅 3 个**：`AIGC概念` / `汽车整车` / `短剧游戏`。

| 候选 | 相关事件标签 | 子串关系 | 为何未命中 |
|---|---|---|---|
| `航空` | `航天航空` | ✅ 真子串 | 被 `_match_sector` 的 **`min_substr_len=3`** 主动拒绝（`航空` 仅 2 字） |
| `光刻机` | `半导体设备` | ❌ | 无任何字面重叠 —— **传导关系**，任何子串/别名/embedding 方案都救不了 |
| `华为鸿蒙` | `华为手机` | 部分（`华为`） | 同上，需知道鸿蒙是华为生态 |
| `OLED概念` / `玻璃基板` | `折叠屏` / `消费电子` | ❌ | 同上，需知道折叠屏用 OLED 与玻璃基板 |
| `第三代半` | `半导体` / `AI芯片` | 部分 | 同上，需知道第三代半导体是化合物半导体 |
| `数字水印` / `知识产权` | `AI应用` / `多模态AI` | ❌ | 同上，需知道 AIGC 的版权溯源需求 |

**结论**：6 组中只有 1 组（`航空`）是字面匹配能解决的，**其余 5 组均需市场知识**。
这就是 D2（接线层禁映射）的定量依据。

### A.4 `unified.json` `confirmed_sectors`（24 条完整）

| 板块 | classification | composite | role | frame_group |
|---|---|---|---|---|
| 银行 | actionable | 100.0 | mainline | 金融 |
| 短剧游戏 | actionable | 100.0 | mainline | AI软件 |
| 旅游 | actionable | 99.7 | mainline | 消费 |
| **旅游景点** | **caution** | **99.1** | mainline | 消费 |
| 保险 | actionable | 98.7 | mainline | 金融 |
| 知识产权 | actionable | 90.8 | mainline | **其他** |
| C2M概念 | actionable | 88.5 | mainline | **其他** |
| 网红经济 | actionable | 87.5 | mainline | **其他** |
| 酒店餐饮 | actionable | 86.9 | mainline | 消费 |
| AIGC概念 | actionable | 81.1 | mainline | AI软件 |
| 华为鸿蒙 | actionable | 78.7 | mainline | AI软件 |
| 石油贸易 | actionable | 74.8 | cooling | **其他** |
| 热泵概念 | actionable | 68.6 | cooling | **其他** |
| 装配式建 | actionable | 64.2 | cooling | **其他** |
| 数字水印 | actionable | 64.2 | cooling | **其他** |
| 物业管理 | actionable | 54.9 | cooling | **其他** |
| 航空 | caution | 52.4 | ambush | 军工 |
| **汽车整车** | **actionable** | **35.7** | ambush | **其他** |
| 第三代半 | caution | 23.4 | ambush | **其他** |
| 玻璃基板 | caution | 23.4 | ambush | AI硬件 |
| OLED概念 | caution | 16.4 | ambush | AI软件 |
| 光刻机 | caution | 16.2 | ambush | AI硬件 |
| 建材 | caution | 15.6 | ambush | **其他** |
| 减肥药 | caution | 7.9 | ambush | 医药 |

**三个关键读数**：
1. 🔴 **composite 与 classification 已脱钩**：旅游景点 **99.1 → caution**、汽车整车 **35.7 → actionable**。
   原因是埋伏角色绕过 TOP-N（L651-655 直接映射）。⇒ **B3（筛选 ≠ 截断）从「推荐」升级为「必选」**，
   且 §6.1 #17 的两个绝对阈值已半失效。
2. **落「其他」家族的候选 11 个**（知识产权/C2M概念/网红经济/石油贸易/热泵概念/装配式建/数字水印/物业管理/汽车整车/第三代半/建材），
   **11/24 = 45.8%** ⇒ M6 基线 54.2%。
3. **`family_resonance` 24/24 全 `None`** ⇒ §6.1 #16 可删；而 `MainlineEndurance.family_resonance` 是另一个字段（§6.2 P4）。

**`news_check` 全部 neutral（0/24）** ⇒ M1 基线 0%。

### A.5 `frame_group_lookup` 分组质量（217 板块 → 13 家族）

| 家族 | 成员数 | | 家族 | 成员数 |
|---|---|---|---|---|
| **其他** | **95（43.8%）** | | 农业 | 13 |
| AI软件 | 18 | | 医药 | 11 |
| AI硬件 | 18 | | 军工 | 8 |
| 周期资源 | 17 | | 消费 | 7 |
| 新能源 | 16 | | 智能驾驶 | 5 |
| 金融 | 4 | | 机器人 | 4 |
| 生态环保 | 1 | | | |

**抽样验证**：银行→金融 ✓、保险→金融 ✓、旅游/旅游景点/酒店餐饮→消费 ✓、
短剧游戏/AIGC概念/华为鸿蒙→AI软件 ✓；
但 知识产权/C2M概念/网红经济/汽车整车/第三代半/装配式建/石油贸易→**其他** ✗，
且 **`全国性银` 不在表内**（TDX 8 字节 GBK → 4 汉字硬截断的产物，§3.1）。

### A.6 token 预算核算（vs `agent_deep_research_budget = 30000`）

| 方案 | 字节 | 估算 token | 判定 |
|---|---|---|---|
| 全喂 `sector.json` + `news.json` | 208,560 | **52–70K** | 🔴 **爆预算 1.7–2.3 倍** |
| 只喂候选 + events 去冗余 + `major_events` | ~85,000 | **21–28K** | ✅ **可行**（§5.2 选定） |
| 再按 `confidence ≥ 0.7` 筛 events（69/107） | ~55,000 | **14–19K** | ✅ 宽裕，但会丢掉 0.6 档的 32 条（含潜在传导链） |

### A.7 `MainlineEndurance.family_resonance` 0903 实算（§7.1 的数据基础）

全体 24 候选：mean = 63.70，pstdev = **31.42**，range = 92.1（7.9 ~ 100.0）。
主线子集 11 成员：mean = 91.91，pstdev = **7.62**，range = 21.3（78.7 ~ 100.0）。

| 家族 | 成员 composite | std_dev | 现公式（/50） | E2a（/31.42） | **E2c（/7.62）** |
|---|---|---|---|---|---|
| 银行 | 100.0 / 98.7 | 0.65 | 0.987 | 0.979 | **0.915** |
| 知识产权 | 90.8 / 88.5 / 87.5 | 1.38 | 0.972 | 0.956 | **0.819** |
| 旅游 | 99.7 / 99.1 / 86.9 | 5.90 | 0.882 | 0.812 | **0.226** |
| 短剧游戏 | 100.0 / 81.1 / 78.7 | 9.53 | 0.809 | 0.697 | **0.000** |
| **`>=0.7` 命中** | | | **4/4** | **3/4** | **2/4** |

---

## 附录 B：本文事实核查约定

遵循 `remote_integration_plan.md` §5 的约定：本文所有行号、字节数、条数均为 **2026-09-03 当日快照**，
且均由本轮只读核查得出。若实施时代码已变动，**以代码为准，不以本文为准**。

§2.2 记录了 grilling 过程中我方 **8 条错误推荐的自我纠正**，§6.1 与 §9 分别追加了第 9、10 条：

| # | 错误断言 | 纠正 |
|---|---|---|
| 9 | 「Agent 调用必须前移到 **L193** 之前」（§5.4 已写入） | **错**。T5 映射同时服务埋伏通道 G2 闸门（L1811）与蓄水池升格条件 2（L1682），两者在 `_build_ambush_channel`（**L188**）内 ⇒ 必须前移到 **L188 之前**，并因此产生 `ambush_pool` 循环依赖（唯一解见 §6.1） |
| 10 | 「`base_position` 是死参数（L2463）」 | **错**。`base_position` 属 `_apply_drawdown_scaling`（L324），**被 L184 正常调用**；L2463 实际是价格获取失败的 warning。**断言作废** |

另需纠正一条对 `remote_integration_plan.md` 的引用：其 §15.6「每日 LLM 调用 ≤ 20」的**限定语是「审查层」**
（T1/T1-H），不约束 Node 6；且该指标当前**无法度量**（§6.3 R5）。

---

## 11. 实施进度追踪

> 更新日期：2026-09-04

### 11.1 已完成

| 步 | 内容 | 完成日期 | 验收结果 |
|---|---|---|---|
| **Phase 0-A2** | call_type 落库（4 调用点 `persist_llm_usage`）+ 超时传参 + temperature 传参 | 2026-09-03 | ✅ `get_llm_usage_summary(by_call_type)` 可查 4 类调用 |
| **Phase 0-A2 附加** | F1-7 `llm_refinement_used` 去硬编码 + F1-8 `missing_fields` 字段级自检 | 2026-09-03 | ✅ `lifecycle_unavailable` / `catalyst_events_empty` 自动检出 |
| **Phase 0-A1** | GOT 回放 harness（`load_daily_contexts` + `--got-replay` + `replay_mode`） | 2026-09-03 | ✅ 0903 回放逐字段一致（24 confirmed / 16 actionable） |
| **Phase 1-A3** | 抽出 `_collect_sector_candidates`（Segment A → 独立同步方法） | 2026-09-03 | ✅ 13 测试通过 + 0903 逐字段一致 |
| **Phase 1-A4** | 实现 `_agent_semantic_glue`（Schema + 主方法 + F1 降级 + 输入瘦身 + 校验 S3-S5 + 落盘 + 时序接线） | 2026-09-03 | ✅ 17 测试通过 + 0903 开关关闭时逐字段一致 |
| **A5 基础设施** | `[SHADOW]` 日志 + `_log_shadow_comparison`（glue vs tech_score diff） | 2026-09-03 | ✅ 影子对照日志就绪 |
| **P0-B B1** | `_build_data_quality` 新增 3 字段级检查（`crowded_families_empty` / `capital_sector_flow_empty` / `hot_stocks_sectors_empty`） | 2026-09-03 | ✅ 0903 自动检出 Y2 + Y4 |
| **P0-B B2** | 护栏空转日志（`missing_fields` 非空 → WARNING） | 2026-09-03 | ✅ 日志可见 |
| **Phase 2 §3** | TencentFetcher（腾讯免费指数 K 线源，P1 优先级，fallback 链） | 2026-09-03 | ✅ 5 个 Node 1 指数全部获取成功，20 测试通过 |
| **Phase 2 §4** | 指数注册表（`data/got_index_registry.json` 29 指数）+ `parse_analysis_target()` 消歧（000001 双身份） + `matches_index_code()` 跨格式匹配 | 2026-09-03 | ✅ 26 测试通过，`market_profile.py` 裸码改后缀 |
| **Phase 3a §15** | T1/T1-H 审查层：① `build_review_executor`（轻量 Agent，3 工具 + max_steps=4 + 审查专用系统提示词）② T1 改用 `try_parse_json` 四策略解析（替代脆弱正则 `\{[^}]+\}`）③ T1-H 持仓审查（Step 2.5 插入，按优先级选择目标，Agent 只有降档权）④ 影子模式开关 | 2026-09-04 | ✅ 22 测试通过，解决 D1/D2/D3 三处缺陷 |
| **Phase 3c §14.1** | A1/A2 AgentMemory 修复：① `avg_confidence` 从硬编码 0.6 → 从 AnalysisHistory.raw_result 计算真实均值（无数据时降级 0.5）② `get_stock_history` 动态计算 `outcome_5d/20d/was_correct`（惰性回填，读取时通过 DataFetcherManager 获取 K 线计算） | 2026-09-04 | ✅ 为 §8 multi 架构前置就绪 |
| **Phase 4a §6** | Intelligence 情报系统：① `intelligence_sources` + `intelligence_items` 两张 SQLite 表 ② `IntelligenceService` 服务（~350 行，支持 NewsNow/RSS 源 + scope 查询 + retention 管理）③ Node 4 `_fetch_intelligence` + `_merge_with_intelligence` 集成 ④ 4 个内建 NewsNow 模板（财联社/雪球/微博/百度） | 2026-09-04 | ✅ 17 测试通过，与 news_feeds 并行采集 |
| **Phase 4b §9** | canonical_id 双写：① `StockDaily` 新增 `canonical_id` 列 ② `generate_canonical_id()` 函数（指数感知：IndexRegistry 优先 + 代码模式推断）③ `save_daily_data` 写入路径集成 ④ SQLite upsert 同步更新 | 2026-09-04 | ✅ 只写不读，`000001` 正确解析为 `sh000001`（上证指数） |
| **Phase 4c §5 Step 1** | MarketThemeContext 模型定义：① `MarketThemeContext` / `MarketThemeItem` / `ThemeBreadth` 等 Pydantic 模型 ② `unknown()` 默认构造（不参与评分）③ `get_market_theme_context()` 全局访问 | 2026-09-04 | ✅ 接口预留，等待 A6 后实施 Step 2（mootdx 板块排名接入） |

### 11.2 进行中 / 待办

| 步 | 内容 | 阻塞 | 预计 |
|---|---|---|---|
| **A5 影子期运营** | `.env` 设 `AGENT_SEMANTIC_GLUE_ENABLED=true` + `AGENT_SEMANTIC_GLUE_SHADOW=true`，每日跑 `--got` 后查看 `[SHADOW]` 日志 + `agent_glue.json` 归档 | 无（代码就绪） | ≥10 交易日 |
| **T1/T1-H 影子期运营** | `.env` 设 `AGENT_STOCK_REVIEW_ENABLED=true` + `AGENT_STOCK_REVIEW_SHADOW=true` + `AGENT_HOLDING_REVIEW_ENABLED=true` + `AGENT_HOLDING_REVIEW_SHADOW=true`，每日查看 `[SHADOW][T1终审]` 和 `[SHADOW][T1-H]` 日志 | 无（代码就绪） | ≥10 交易日 |
| **P0-B B3** | `crowded_families` 处置（Node 2 停产 vs 修正口径） | 需与 Node 2 协商 | 独立决策 |

### 11.3 后续路线图

| Phase | 内容 | 前置 | 文件范围 |
|---|---|---|---|
| **A6 转正+退役** | ① `AGENT_SEMANTIC_GLUE_SHADOW=false` ② 退役 §6.1 的 17 项 ③ 改 4 个调用点 ④ E2 公式 ⑤ 修订 `remote_integration_plan.md` | A5 ≥10 日双达标 | `aggregator.py` / `docs/remote_integration_plan.md` |
| ~~**Phase 2**~~ | ~~remote P0 数据源（§3 Tencent fallback ∥ §4 指数注册表）~~ | ~~Phase 0~~ | ✅ **2026-09-03 已完成** |
| ~~**Phase 3a**~~ | ~~remote §15 T1/T1-H 审查层（个股终审 + 持仓审查）~~ | ~~Phase 0~~ | ✅ **2026-09-04 已完成**（`factory.py` / `stock_screener.py` / `portfolio_decision.py`） |
| ~~**Phase 3c**~~ | ~~remote §14.1 A1/A2（Agent Memory 校准分母 + outcome 回填）~~ | ~~Phase 0~~ | ✅ **2026-09-04 已完成**（`memory.py`） |
| **Phase 3b** | remote §8 分歧摘要 + SkillAggregator + RiskOverridePlan | **A6 之后**（同改 `aggregator.py` 聚合核心） | `aggregator.py` 聚合核心 |
| ~~**Phase 4a**~~ | ~~remote §6 Intelligence 情报系统~~ | ~~无（独立模块）~~ | ✅ **2026-09-04 已完成**（`intelligence_service.py` + Node 4 集成 + 2 SQLite 表） |
| ~~**Phase 4c**~~ | ~~remote §5 MarketThemeContext Step 1（模型定义）~~ | ~~无（Step 2 等 A6）~~ | ✅ **2026-09-04 已完成**（`market_theme.py` 模型 + unknown 默认构造） |
| **Phase 4b** | remote §5 MarketThemeContext Step 2（mootdx 板块排名接入） | **A6 之后**（aggregator.py 接入） | `market_theme.py` + `aggregator.py` |
| **Phase 4d** | remote §7 AlphaSift 选股引擎 | **A6 之后**（归零率基线重标，D-3） | `screening/` 子包 + `screening_adapter.py` |
| ~~**Phase 5a**~~ | ~~remote §9 canonical_id 双写~~ | ~~无（独立数据层）~~ | ✅ **2026-09-04 已完成**（`StockDaily.canonical_id` + `generate_canonical_id`） |
| **Phase 5b** | remote §10 TickFlow 数据源 | 需申请 API Key | `tickflow_fetcher.py` |
| **Phase 5c** | glue P1 调参 + OPEN-1 家族仓位 | A6 转正 | — |

### 11.4 开发建议（下一步优先级）

> 更新日期：2026-09-04（Phase 4a/4b/4c 完成后）

**推荐路径：A5 + T1/T1-H 双影子运营 → A6 转正 → §8 分歧摘要 + §5 Step 2**

理由：
1. **Phase 0/1/2/3a/3c/4a/4b/4c 全部完成**：glue 接线 + 远端 P0 数据源 + 审查层 + AgentMemory 修复 + Intelligence 情报 + canonical_id 双写 + MarketThemeContext 模型均已就绪
2. **A5 + T1/T1-H 影子运营可同步进行**：每日设环境变量跑 `--got` 和选股，人工抽查 ≤10 分钟
3. **§8 分歧摘要 + §5 Step 2 必须等 A6**：同改 `aggregator.py` 聚合核心，与 glue 调用链同区段
4. **§7 AlphaSift 也需等 A6**：glue 转正后重标归零率基线（D-3）

**影子期环境变量清单**：
```bash
# glue 影子
AGENT_SEMANTIC_GLUE_ENABLED=true
AGENT_SEMANTIC_GLUE_SHADOW=true

# T1 候选股审查影子
AGENT_STOCK_REVIEW_ENABLED=true
AGENT_STOCK_REVIEW_SHADOW=true

# T1-H 持仓审查影子
AGENT_HOLDING_REVIEW_ENABLED=true
AGENT_HOLDING_REVIEW_SHADOW=true
```

**具体开发步骤**：
1. 同步开启 A5 + T1/T1-H 影子期：`.env` 加上述 6 行，每日跑完检查 `[SHADOW]` 日志
2. A5 达标（≥10 交易日 + 双达标）后执行 A6 转正 + 17 项退役
3. A6 完成后启动 §8 分歧摘要（Phase 3b）

### 11.5 测试覆盖汇总

| 测试文件 | 测试数 | 覆盖内容 |
|---|---|---|
| `tests/test_node6_candidates.py` | 13 | Segment A 候选收集（4 路 + 去重 + 0903 基线） |
| `tests/test_node6_regime_state.py` | 16 | 状态机 + EMA 基线 + regime schema |
| `tests/test_node6_glue.py` | 17 | 语义粘合层（F1 降级 / 去冗余 / S3-S5 校验 / 调度） |
| `tests/test_tencent_fetcher.py` | 20 | TencentFetcher（symbol 转换 / K 线解析 / 集成） |
| `tests/test_index_registry.py` | 26 | 指数注册表（查找 / 消歧 / 跨格式匹配 / 冲突） |
| `tests/test_phase3_glue.py` | 22 | §15 T1/T1-H 审查层（review profile / JSON 解析 / 降档惩罚 / 优先级选择）+ §14.1 A1/A2（avg_confidence 计算 / outcome 字段） |
| `tests/test_intelligence_service.py` | 17 | §6 Intelligence（ORM / 安全工具 / NewsNow 模板 / 服务初始化 / 便捷函数） |
| `tests/test_phase4_parallel.py` | 8 | §5 MarketThemeContext 模型 + §9 canonical_id 生成（指数感知 / 代码模式） |
| **合计** | **139** | 全部通过 |

