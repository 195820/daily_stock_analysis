# GOT 运行模型修复方案：同日重跑幂等与末版基线（A 层）

> **定位**：本轮只修 **A 层（运行模型）** —— 即「同一天跑多次时，跨日状态被推进多次」与
> 「跨日状态读取把**当天**归档当作前一日」两类缺陷。**B–E 层（同一概念多处计算 / 影子验收设计 /
> 与 0902 报告对账 / 其他具体缺陷）不在本轮射程**，见 §7 范围外清单。
>
> **遵循既有工程约定**（与 `agent_glue_plan_0903.md` / `intraday_gate_plan_0907.md` 同构）：
> 事实核查先行（§2）、**被推翻的错误结论公开撤销**（§2.3）、已确认决策链（§4）、
> **可计数的强制验收红线**（§6）、范围外与挂起项显式列出（§7）。
>
> **状态：待实施（用户已确认范围 A′）**。创建日期：2026-09-11。
> 事实核查基准：HEAD `a803e09` + 工作区快照（2026-09-11 09:05 之后）。
>
> **修订台账**：[`got_revision_ledger_0911.md`](got_revision_ledger_0911.md) ——
> 本文的「2026-09-11 修订」注已汇总在该台账（16 条外部质询 + 13 处同源传播 + 11 处复核漏网 + 3 处自曝）。
>
> **实施审核（2026-09-11）**：[`got_impl_review_0911.md`](got_impl_review_0911.md) —— 本轮实施的审核结论（**2 个 P0** / 8 个 P1 / 红线达标总表 / 承诺脚本缺失清单）。

---

## 0. 结论摘要

GOT 自称「日频」系统，但**没有"一天一次"的运行约束**，且其跨日状态机**从归档目录回读"前一日"时
不排除当天**。两条叠加的后果是：同一天运行 N 次，跨日状态被推进 N 次（或读到当天早轮的中间态），
**归档里存的那一版并不是"用前一日状态算出来的那一版"**。

0910 是实证案例（§1）：4 次启动 / 2 次成功归档，最终归档的 `market_regime` 被自引用**从 `bull` 翻成了 `transition`**。

**修复目标（一句话）**：

> 同一天任意次运行，得到**同一份**结果；最后一次运行即为基准。

达成路径：**读侧排除当天 + 写侧按日幂等**。二者必须同时做——只做读侧，
EMA 基线与 Node 2 的派生计数器仍按运行次数推进，"末版为准"不成立。

**范围（用户已确认 A′，§4）**：9 个读侧入口 + 6 项写侧 + 归档原子替换 + 归档侧快照标签。
**不做**：进程级互斥锁（→ OPEN-1）、任何阈值/判据变更。

---

## 1. 触发事件：0910 事故链（实测）

| 运行 | 时段 | 结局 | 是否归档 | 写入的跨日状态 |
|---|---|---|---|---|
| run#1 | 11:14:40 → 11:47:59 | **Node 2 拿到 0/172 板块**（mootdx 板池故障）→ 降级空结果，`actionable=0`、`正式池=0` | ✅ | `market_regime={transition, dwell=1, prev=bull}`；EMA `samples 123→124` |
| run#2 | 14:12:02 → 14:32:10 | **崩溃 exit 1**：`'SectorContext' object has no attribute 'status'` | ❌ | — |
| run#3 | 17:01:10 → ？ | 无归档行（与 run#4 时间重叠，疑被中断） | ❌ | — |
| run#4 | 17:51:35 → 17:58:56 | 正常：`active_mainline=4`、`actionable=17`、`正式池=1` | ✅ | **读到 run#1 的当日归档** → `{transition, dwell=2, prev=transition, pending=bear}`；EMA `samples 124→125` |

**决定性日志**（`logs/stock_analysis_20260910.log`）：

```
11:47:59 | [Node 6] Regime 切换确认: bull → transition      ← run#1，正确读到 0909(bull)
17:58:36 | [Node 6] Regime 首次出现: bear，待次日确认        ← run#4，读到的是 run#1 自己写的 0910
```

**偏差量化**：若 run#4 读到 0909（`bull, dwell 6, pending=transition`），代入状态机情形 3 得
`{bull, dwell=7, pending=bear}`；实际落盘 `{transition, dwell=2, pending=bear}`。
即 **raw regime 从 `bear` 被"修正"成了 `transition`**：

| 下游 | 正确链路 | 0910 实际 |
|---|---|---|
| `REGIME_MULTIPLIER`（`portfolio_decision.py:215-258`） | `bull = 1.0` → 板块上限 `0.28` | `transition = 0.65` → 板块上限 **`0.182`**（11 个板块） |
| `constraint_reasoning` 文本 | "市场状态=bull" | "市场状态=transition" |
| `final_position_limit` | 不变 | 不变（被 `min(position, risk_budget)` 压死，见 B 层 B3 —— [`got_layer_b_plan_0911.md`](got_layer_b_plan_0911.md) §2.3） |

`★ Insight ─────────────────────────────────────`
**同一个 bug 放过了仓位、却污染了 regime 与板块上限。** `_compute_position_from_regime` 的输出被
`min(·, regime.risk_budget)` 截断，31 个归档日中 23 天两者严格相等、`actionable≥6` 时 100% 相等
—— 所以 regime 错了，仓位上限却"恰好"没错。这类"错误被下游一个巧合掩盖"的形态，
是**只看最终仓位数字无法发现本 bug** 的原因，必须以状态字段本身验收（§6 R2）。
`─────────────────────────────────────────────────`

---

## 2. 事实核查

> 约定：本轮所有断言均由只读核查得出，代码引用为 **HEAD `a803e09` 行号**；若实施时代码已变动，**以代码为准**。

### 2.1 读侧自引用：9 个入口（已证实）

**核心机制**：

```python
# src/got/persistence.py:176-184  —— 不排除当天
def _list_archive_dirs(reverse: bool = True) -> list[Path]:
    return sorted(
        (d for d in _ARCHIVE_DIR.iterdir()
         if d.is_dir() and not d.name.endswith(_SKIP_DIR_SUFFIXES)),
        reverse=reverse,
    )

# src/got/persistence.py:335-350  —— 取倒序第一个含 regime 的目录，命中即 return
for d in date_dirs:
    data = _load_unified_json(d)
    ...
    if regime is None: continue
    return {...}          # ← 当天目录排在 0909 之前 ⇒ 永不到达 0909
```

写盘时机（`src/got/orchestrator.py:95-121` → `src/got/archive.py:105,118-122`）：
**unified.json 在 Node 6 之后写**。故同一次进程内，Node 6 读到的"当天目录"必然来自**当天更早的成功运行**。
日志侧面证据：run#1 回读日志为 `regime 历史回读: 20 日, 从 20260813 到 20260909`（当天目录尚不存在）；
run#4 为 `从 20260814 到 20260910`（当天目录已在）。

| # | 入口 | 位置 | 读什么 | 自引用 |
|---|---|---|---|---|
| D1 | `load_capital_flow_series(5)` | `persistence.py:228` | capital.json 板块资金流/DDE 5 日序列 | 是 |
| D2 | `load_previous_regime_state()` | `persistence.py:335` | unified.json `market_regime` | **是（本案）** |
| D3 | `load_regime_state_history(20)` | `persistence.py:368` | unified.json regime 序列 | 是（`transition_count` 源） |
| D4 | `load_previous_stage_state()` | `persistence.py:421` | sentiment.json `stage_state` | 是 |
| D5 | `load_sentiment_score_history(20)` | `persistence.py:456` | sentiment.json score 序列 | 是 |
| D6 | `load_regime_history(20)` | `persistence.py:490` | unified/market.json regime | 是 |
| D7 | `_load_previous_reservoir()` | `aggregator.py:2037-2071` | 最近目录 `ambush_reservoir` | 是（**绕过 `_list_archive_dirs`**） |
| D8 | `_compute_sentiment_momentum()` | `sentiment_analysis.py:923-951` | 最近 10 天 sentiment score（`sorted(iterdir)[:10]`） | 是（**绕过**） |
| D9 | `_load_reservoir_sector_names()` | `catalyst_calendar.py:1051-1075` | 最近一份 unified.json `ambush_reservoir` | 是（**绕过**） |

消费点：`aggregator.py:513/516`、`sentiment_analysis.py:993/1134/161`、`capital_analysis.py:507-508/622-623`、
`aggregator.py:2805/2810/4498-4500`、`catalyst_calendar.py:1060-1072`。

**排除的替代假设**（§2.3 列出被推翻项）：
- `--got-replay` 不推进状态（`aggregator.py:193-194` 显式跳过状态机；结果只写 `_replay/`）；
- `load_previous_context()` / `list_saved_contexts()`（`persistence.py:43-64,86-104`）**无调用者**（死代码），
  `data/aggregator_contexts/` 不是 regime 的第二状态源；
- 无脚本回填 regime/dwell 到 unified.json（`scripts/backfill_state_field.py` 只写 Node 2 SQLite `state` 列）。

### 2.2 写侧「按运行次数推进」：6 项（已证实）

| # | 状态 | 机制 | 0910 实证 |
|---|---|---|---|
| W1 | `ema_baseline.json` | `aggregator.py:4397-4409` 读-改-写、**无日期键**；每次运行 α 平滑 + `sample_count+1` | `11:47:59 → score=0.269/samples=124`；`17:58:56 → score=0.188/samples=125`。终值 **0.188 ≠ 单次应有的 0.269** |
| W2 | `monitoring.json` 日期窗口 | `persistence.py:596-614` `signal_dates`/`contrarian_alert_dates` 按**条** append，却按 30 **日**窗口消费 | `signal_dates` 中 `range_reversal` 有 **2 条 `2026-09-10`**（09-04 有 3 条）→ `signal_count_30d=35` 虚高，持续 30 天 |
| W3 | `node4_events.mention_count` | `news_analysis.py:691-704` `+= 1`；而历史加载（`:846-849`）按 `last_seen >= today-7` 取，**含当天早轮刚写的行** | 事件 `0228c8d9`（四部门发布会）`first_seen == last_seen == 2026-09-10`，`mention_count=3`，`sentiment_trajectory=[0.0,0.0,1.0]` |
| W4 | `node4_raw_news.appearance_count` | `news_analysis.py:800-803` `+= 1` | `last_seen_date=2026-09-10` 且 `appearance_count>=2` 的行 **127 条**；多行 `first==last==09-10` 且 `count=3` |
| W5 | `catalyst_refresh.trading_days_since_refresh` | `catalyst_calendar.py:218-222` incremental 时 `+= 1` | `last_full_refresh=09-10, since=2`（不重复触发全量，仅计数虚高；无决策消费者） |
| W6 | `ambush_position_snapshots` | `portfolio_decision.py:1929` `session.merge(record)` **未带主键** → 无法按唯一键 `(symbol, entry_date)` 命中 → 第二同日运行 INSERT 撞唯一约束 → **整批回滚**（且被 `:1939-1940` 的 `except` 静默吞掉） | 表内 0 行，**证据不足**，但失败方向是**丢数据** |

### 2.3 被推翻的结论（公开撤销，防后人重踩）

| # | 曾被主张 | 核查结论 | 依据 |
|---|---|---|---|
| 1 | 「0910 的 `wave_days` 与 `wave_start_date` 不一致 = 缺陷」 | ❌ **错。是"冻结继承"设计语义** | `decide_wave_days`（`sector_analysis.py:109-160`）的降温冻结路径 `return max(prev_wd,1), prev_start`（`:125`）与出框回归浅回撤冻结路径（`:149,160`）**有意继承旧 `wave_start_date` 但保留很小的 `wave_days`**。逐板块复算全部命中分支：粮食概念锚点 09-07、缺口期强度回撤 0.8 < 10 → 冻结 `(wd=1, start=09-07)`；林业 09-08→09-09 冻结 `(1, 09-04)` 后 09-10 累到 2；供销社平台回撤 9.7 < 10（差 0.3 分未触发重置）→ 冻结 `(4, 08-25)` 后累到 6 |
| 2 | 「0910 的 `mainline_days` 被多跑膨胀」 | ❌ **错。0910 的 14/10/12 是正确行链计数** | 0910 只有 17:51 一次有效 Node 2 写（run#1 是 `0/172 板块` → `_save_sector_trends` 在 `sector_analysis.py:4102 if not sectors: return` 早退）。DB 复核：粮食概念截止 09-10 连续 `is_mainline=1` 行 = 14、林业 = 10、供销社 = 12 |
| 3 | 「每次运行推进一次跨日状态」 | ⚠️ **表述过宽**。准确说法：**每次成功归档推进一次**（推进次数 = 当日成功归档次数 − 1） | 0910 全天仅 2 次归档，故只推进 1 次 |
| 4 | 「`previous_regime ≡ regime`（11/11 零反例）是自引用的**充分证据**」 | ⚠️ **只是旁证**。它是状态机**结构性产物**：情形 1/3 都写 `previous_regime = prev_regime`，而这两个分支恰有 `new_regime == prev_regime`；只有情形 2（切换确认）才会不等 | `aggregator.py:564-570`；11 天实测零反例 |

**撤销后仍成立的核心命题**：D2 自引用确实发生（run#4 的 `prev_state=(transition, dwell=1, pending=None)`
**不可能来自 0909**，唯一可达来源是 0910 run#1），且 W1–W6 确实按运行次数推进。

### 2.4 已确认同日幂等、**不得误伤**的状态源（可作修复模板）

| 状态 | 幂等机制 | 位置 |
|---|---|---|
| `catalyst_events` | 确定性 `event_id = MD5(source\|type\|sector\|date)` + UPSERT | `catalyst_calendar.py:67-70,1432-1461` |
| `catalyst_realizations` | 注册前查重 + 兑现前过滤 `is_realized==False` | `catalyst_realization.py:189-198,69-80` |
| `wave_calibration.jsonl` | 显式先删当日再重写 | `sector_analysis.py:4309-4340`（数据 0 重复） |
| `node2_sector_trends` 行 | PK `(sector_code, tracking_date)`，query-then-update | `sector_analysis.py:4149-4152` |
| `dwell_days` / `wave_days` / `wave_start_date` / `exit_pressure_days` | 读取带 `tracking_date < today` 过滤 | `sector_analysis.py:4084 / 4431 / 4222` |
| `screener_results` / `data/got_screener/*.json` | 按日 upsert / 文件覆盖 | `repositories/screener_repository.py`；`stock_screener.py:2784-2791` |
| `agent_glue.json` / `_replay/` | 每次覆盖；replay 跳过全部跨日写 | `aggregator.py:193-194,350-362,1084-1088` |

---

## 3. 根因

**两条独立机制，缺一不可**：

1. **没有"一次日频运行"的定义**（A1）。系统可以在任意时点、任意次数运行
   （0907–0909 各 1 次、0903 11 次、0910 4 次），却没有"这一版是否构成当日基准"的概念，
   也没有"同日重跑应覆盖而非追加"的统一约定。
2. **跨日状态以"归档目录列表"为唯一记忆载体，且列表不排除当天**（A2）。
   读侧（§2.1）与写侧（§2.2）都建立在"归档目录 = 历史日"这一前提上，
   而该前提在「当天已有成功归档」时**不成立**。

`min(·, risk_budget)` 之类的下游巧合会掩盖 1 的后果，使问题在"最终仓位数字"上不可见（§1 Insight）。

---

## 4. 已确认决策（本轮不再重议）

| # | 决策 | 用户原话/确认 |
|---|---|---|
| D-1 | **末版为准**：正常节奏 13:30（盘内），测试期随时跑，后续可能追加/临时跑一版，**以当日最后一版数据为基准** | 「这个需要按照最后一版数据作为基准」 |
| D-2 | **A2 需修复**：跨日状态机把当日归档读作前一日是缺陷，**确证后修复** | 「确证后是需要修复的」 |
| D-3 | **快照标签只落归档侧**（`meta.json`），不进节点 JSON、不进 SQLite；本轮**不改任何阈值/判据** | 「b 只落在归档侧就行」 |
| D-4 | **范围 = A′**：9 读侧 + 6 写侧 + 归档原子替换 + 快照标签；**不做进程级互斥锁** | 「A」 |
| D-5 | **A-P0-3 的 EMA 幂等取法 = 按日键 + 序列重算**（否决「同日不增计数」的补丁式改法） | 「按1吧」 |

**设计推论（由 D-1 + D-4 导出，实施时据此）**：读侧只读"前一日及更早"、写侧按日幂等，
则 `f(前一日状态, 当日数据)` 在同一天内是**同一个函数**，多次运行**天然幂等**；
写侧覆盖式落盘使**最后一次运行即为状态终值**。→ 无需额外引入"当日只记最后一次"的调度逻辑。

---

## 5. 修复清单

### 5.1 必须改（P0）

#### A-P0-1 统一归档目录列举入口（消除 3 处绕过 + 加排除当日）

- **位置**：`persistence.py:176-184`（新增参数）；`aggregator.py:2037-2071`（D7）；
  `sentiment_analysis.py:923-951`（D8）；`catalyst_calendar.py:1051-1075`（D9）
- **改法**：
  1. `_list_archive_dirs(reverse=True, before: Optional[date] = None)` —— `before` 非空时只返回
     目录名日期 `< before` 的目录。默认 `None` = 现有行为（不动其他调用方的语义）。
  2. 导出为模块级公共函数（现为下划线私有），供 D7/D8/D9 复用，**删掉三处各自的 `iterdir()`/`sorted()`**。
- **理由**：DRY；三处绕过是"同一个语义散落四处"的典型（用户规则「复用优先」）。
  只改 `persistence.py` 会漏掉 D7–D9，而 D7/D9 直接决定 `ambush_reservoir` 与 T5 板块域。

#### A-P0-2 六个 loader 排除当天

- **位置**：`persistence.py:228 / 335 / 368 / 421 / 456 / 490`，统一传入 `before=date.today()`
- **改法**：在六个 loader 内部调用 `_list_archive_dirs(reverse=True, before=date.today())`。
  签名不变（调用方无需改），语义变为"只回读历史日"。
- **理由**：这六个 loader 服务的是**跨日记忆与历史序列**，当天不构成历史。
  对 D3（`transition_count`）尤其必要——"近 20 交易日确认切换次数"不应把当天早轮值计入。
- **⚠️ 跨层耦合（C7）**：本项会改 `transition_count` 的**读取口径**，而 C 层 C7 的
  ≈2026-09-25 时间炸弹倒计时是按旧口径推的 ⇒ **本项落地后须重算 C 层倒计时**，
  否则 OPEN-C1 专项会按错日期排期。见 §6.4 第 3 行与
  [`got_layer_c_plan_0911.md`](got_layer_c_plan_0911.md) §2.7 / OPEN-C1。

#### A-P0-3 EMA 基线改为按日幂等

- **位置**：`aggregator.py:4382-4413`（更新逻辑）+ `persistence.py:111-160`（读写）
- **改法**：`ema_baseline.json` 增加 `daily_scores: {"YYYY-MM-DD": score, ...}`（保留最近 N=60 个交易日），
  `regime_score` / `score_std` / `sample_count` 由该序列**每次重算**（纯函数）：
  - 若 `daily_scores` 中已有当天键 → **覆盖**该键的值（末版为准）；
  - `sample_count = len(daily_scores)`；
  - 冷启动兼容：文件无 `daily_scores` 时视为空序列，用现有 `regime_score/score_std` 作种子迁移一次。
- **理由**：这是唯一**跨日累积且直接进入判据**的状态（喂 `_adaptive_temporal_check` 的 z-score 阈值，
  `aggregator.py:4244-4282`）。"同日不增计数"这类补丁式改法需要用 Welford 反演当日贡献，易错；
  改为"按日键 + 序列重算"是纯函数，**天然幂等**且可离线复算。
- **已确认（决策 D-5）**：取「按日键 `daily_scores` + 序列重算」；
  **否决**「同日不增计数」的补丁式改法——后者要求在 Welford 累积量上反演当日贡献，
  且反演错误会被随后的正常日掩盖（与 §1 的「错误被下游巧合掩盖」同型）。
- **附带收益**：`sample_count = len(daily_scores)` 之后，0910 的
  `samples 123→124→125` 这类"按运行次数累加"的痕迹在数据上直接消失，
  且 §6.2 的 R4（同日两跑 `sample_count` 与 `daily_scores` 键数不变）可离线复算，无需实跑。

#### A-P0-4 `monitoring.json` 日期窗口按日去重

- **位置**：`persistence.py:596-614`
- **改法**：`signal_dates` / `contrarian_alert_dates` 在 append 前先剔除 `date == today_str` 的既有条目
  （与 `cutoff_30d` 的清理同一处，合并为一次列表推导）。
- **理由**：二者按 30 **日**窗口消费（`signal_count_30d`、`contrarian_alert_count_30d`），
  同日重复 append 会使其虚高并**持续 30 天**，且 `all(v==0)` 的"从未触发"告警会被掩盖。

#### A-P0-5 Node 2 三处派生计数器补 `tracking_date < today` 过滤

- **位置**：`sector_analysis.py:4351-4353`（`_calc_strength_trend`）、`:4376-4378`（`_calc_mainline_days`）、
  `:4393-4395`（`_calc_ambush_days`）
- **改法**：三处 query 各加 `.filter(Node2SectorTrendRecord.tracking_date < date.today())`，
  与已正确实现的 `dwell_days`（`:4084`）、`wave_days`（`:4431`）、`exit_pressure_days`（`:4222`）对齐。
  `_calc_mainline_days` / `_calc_ambush_days` 末尾的 `return days + 1`（代表今天）**保持不变**。
- **理由**：零语义变更、一行一个。0910 因只有一次有效 Node 2 写而**未暴露**，
  但只要某天 Node 2 成功跑两次即非幂等：`strength_trajectory` 会把当天分数重复追加、
  `mainline_days` / `ambush_days` 会多计 1。
- **附带（建议并入）**：缺失板块补写路径 `mainline_days = prev.mainline_days + 1`（`:4263-4266`）
  读的是**已存值**，会把先前的多跑膨胀跨日传播；建议改为与主路径一致的"按行计数"。

#### A-P0-6 `ambush_position_snapshots` 改自然键 upsert

- **位置**：`portfolio_decision.py:1916-1937`
- **改法**：写前按 `(symbol, entry_date)` 查询：命中则更新 `entry_price` 等字段，未命中才 `session.add`。
  同时**收窄 `:1939-1940` 的 `except Exception`** —— 至少把"唯一约束冲突"单独 `logger.error` 并
  在返回值里标记失败，避免"丢数据"被静默吞掉（用户规则「严禁吞没异常」）。
- **理由**：当前 `session.merge()` 未带主键 → 无法命中自然键 → 第二同日运行 INSERT 撞唯一约束 →
  **整批回滚**；失败方向是丢数据，风险不对称。

#### A-P0-7 Node 4 两处计数按日去重（**先影子对比**）

- **位置**：`news_analysis.py:691-704`（`mention_count`）、`:800-803`（`appearance_count`）
- **改法**：`+1` 的条件收紧为"跨日才计"—— 仅当 `db_event.last_seen < today`（或 `last_seen_date < today`）
  时 `+1`；同日重复只刷新 `last_seen` / 合并来源 / 追加轨迹。
  为可比性，**旧口径值保留到新增的 `mention_count_run_based` / `appearance_count_run_based` 字段**。
- **理由（为何单独放最后）**：这是**口径变更**，不是纯 bug 修复。生命周期阈值按"跨日离散出现次数"设计
  （`news_analysis.py:878-881` `emerging: ==1 / fermenting: >=3 / peak: >=10`），
  改了会让今天的 `mention_count=3` 变成 1 → **改变明天起的事件生命周期判定**，需回标阈值。
  故先双写、跑一天对比事件状态，再切主字段（§6 R3 / OPEN-3）。

#### A-P0-8 归档写入改原子替换

- **位置**：`archive.py:92-105`（"当天重跑清旧档"）+ `:118-134`（写文件）
- **改法**：先写旁路目录 `archive_dir.with_suffix(".tmp")`，写完后再替换：
  `os.replace`/`shutil.move` 对**目录**不适用非空目标（Windows 语义），故按"目标改名 `.old` → tmp 改名目标 → 删 `.old`"两步 rename 实现（窗口从"整个写入期"缩短到"两次 rename 之间"）。
  `_replay/` 的保留语义（`a803e09` 新增）在 tmp 方案下改为"替换前先把它搬进 tmp"。
  ⚠️ **`agent_glue.json` 不适用保留语义** —— 它是当日**影子结论**，按 D-1「末版为准」必须随本轮**覆盖**
  （详见 B 层 [`got_layer_b_plan_0911.md`](got_layer_b_plan_0911.md) §7.2 OPEN-B5）。
- **理由**：当前"先删光目标目录、再逐个写"在**两个进程时间重叠**时，A 进程会删掉 B 进程正在写的文件。
  0910 的 run#3/run#4 确有重叠。原子替换是局部、低风险的收敛；**进程级互斥锁不做**（→ OPEN-1）。

##### A-P0-8-a 影子样本的落地路径（实施前必读，含一处会丢数据的坑）

**实际调用链**（已核实，与最初表述有差异）：

```
Node 6 完成 → aggregator._save_glue_result 直接写 data/got_daily/{today}/agent_glue.json
              （aggregator.py:1086，路径就是归档目录本身，不是旁路）
           → archive_daily_run 清理+重写（archive.py:94-105 的 preserved_files 放过上面那个文件）
```

`preserved_files = {"agent_glue.json"}` 存在的唯一理由是"别把同一轮刚写的删掉"。

**触发条件收窄（修正本文原表述）**：原文说该集合"会保留当天更早一轮的影子输出"——**过宽**。
只有当本轮 glue 走了 `aggregator.py:960-1038` 的 **6 条不落盘 `return`** 之一
（`:962 / :967 / :970 / :988 / :993 / :1003`，见 C 层 C-P1）时，
目标目录里才会留着上一轮的。0910 实测同轮（glue `17:58:50` / unified `17:58:56`，`+6s`），
**未观察到污染**。方向对，条件比原文窄。

**⚠️ 实施陷阱**：tmp 方案下 tmp 目录里**没有** `agent_glue.json`（aggregator 写的是目标目录），
直接 swap 会让归档里**影子样本彻底消失**。唯一正确解法是"替换前把目标目录的 glue 搬进 tmp"——
而这样做的正确性**恰恰依赖 C 层 C-P1 已落地**（降级路径也落盘），否则降级轮搬进来的正是上一轮的，
即 A-P0-8 要修的那个 bug。

⇒ **落地约束（与 C 层强耦合，必须同批）**：

| # | 约束 | 原因 |
|---|---|---|
| 1 | **C-P1 先于或同批于 A-P0-8** | 否则 `target→tmp` 的搬运会把上一轮 glue 带进来 |
| 2 | **C-P2 同批** | `_save_glue_result` 用 `date.today()`（`:1085`）⇒ 跨日回放的样本落进今天的 `_replay/`；不修则 A-P0-8 的 `_replay/` 搬运搬错日期 |
| 3 | **`_replay/` 搬运语义保留** | C-P11 的回填样本（0901–0903）只存在于 `_replay/`，是本层唯一会丢的数据 |

对应 C 层位置：[`got_layer_c_plan_0911.md`](got_layer_c_plan_0911.md) §5.1 C-P1 / C-P2。

#### A-P0-9 归档侧快照标签

- **位置**：`archive.py:_build_meta`（`meta.json`）
- **改法**：新增两个字段，**只落 `meta.json`**：
  - `snapshot_time`：本次实际取数完成时刻（`datetime.now().isoformat()`）；
  - `bar_complete`：当日日线是否已走完（`datetime.now().time() >= time(15, 0)` 且为交易日）。
- **理由（D-3）**：你的正常节奏是 13:30 盘内、`bar_complete` 恒为 false，标签的价值在于**标记例外**
  ——把 0910 `17:51` 这类收盘后补跑版单独标出，便于将来做 P6 兑现追踪（PEAD 需对齐真实收盘收益）
  时按 `bar_complete` 分组定位偏差源。**不进节点 JSON**：节点输出已因 `data_quality.timestamp` 每次不同而不可比，
  再加运行态字段只会更糟；**不进 SQLite**：`data/got_daily/*/meta.json` 本身就是按日清单，加表等于多一处同步副本。

---

## 6. 验收红线（可计数）

### 6.1 六条

| # | 指标 | 达标线 | 数据源 | 可度量 |
|---|---|---|---|---|
| **R1** | **同日两跑状态一致性**：同一天连跑两次，**状态字段**逐字节一致 | 100% 一致 | 见 6.2 字段清单 | ✅ |
| **R2** | **自引用修复的可观测标志**：出现 `previous_regime != regime` 的归档日 | **≥ 1 天**（下一次真实 regime 切换日） | `unified.json` `market_regime` | ✅ |
| **R3** | **事件计数不翻倍**：同日两跑后 `mention_count` / `appearance_count` 相对单跑不增 | 不增 | DB `node4_events` / `node4_raw_news` | ✅ |
| **R4** | **EMA 计数按日**：同日两跑后 `sample_count` 不变、`daily_scores` 键数不变 | 不变 | `ema_baseline.json` | ✅ |
| **R5** | **信号日期无同日重复**：`signal_dates` 中同 `(type, date)` 至多 1 条 | 0 重复 | `monitoring.json` | ✅ |
| **R6** | **状态源清单逐项复测**：§2.2 W1–W6 + §2.1 D1–D9 各跑一次同日两跑验证 | 全部幂等 | §6.2 脚本 | ✅ |

### 6.2 R1 的字段清单（**不是全文件 diff**）

⚠️ **不能对整文件做逐字节比较**：`data_quality.timestamp`（每节点）、`meta.run_timestamp`、
`meta.duration_seconds`、`meta.snapshot_time` 天然随运行变化；`reasoning` / `reasoning.md` /
`hot_topics` 等 LLM 派生字段也非确定（→ OPEN-2）。R1 **只比状态字段**：

```
unified.json:        market_regime.{regime, dwell_days, previous_regime, transition_pending, transition_count}
                     ambush_reservoir[].{sector_name, reason, entered_date, days_in_reservoir}
sentiment.json:      stage_state.{current_stage, dwell_days, previous_stage, stage_transitions}
                     sentiment_momentum
market.json:         （无跨日字段）
sector.json:         每板块 mainline_days / ambush_days / wave_days / wave_start_date
                     / strength_trajectory / dwell_days / exit_pressure_days
ema_baseline.json:   regime_score, score_std, sample_count, daily_scores 键集合
monitoring.json:     cross_node.signal_dates, node5.contrarian_alert_dates
DB node4_events:     mention_count（+ 影子字段 mention_count_run_based）
DB node4_raw_news:   appearance_count（+ 影子字段）
DB ambush_position_snapshots: 行数不变
```

### 6.3 验收手具

**原文写"当前项目无 GOT 离线回放"——已失效，此处修正**（C 层 §2.8 C8 实测）：
V1（`--got-replay`，`main.py:359-365`）与 V2（`load_daily_contexts`，`main.py:1324-1343`）
**在 HEAD `a803e09` 上都已存在**，0903 §8.3 的"硬阻塞"结论已过期。
但**回放不能替代同日两跑**：`replay_mode` 显式跳过跨日状态写（`aggregator.py:193-194`），
重建的是规则侧，**不含状态推进**——而 R1–R6 验的正是状态推进。

故手具分两个：

| 手具 | 覆盖 | 命令 |
|---|---|---|
| `scripts/verify_same_day_idempotency.py`（新增） | R1–R6：同日实跑两次，按 §6.2 清单提取状态字段并 diff，输出 A-P0-1…A-P0-9 逐项 PASS/FAIL | `python main.py --got` ×2 |
| `scripts/verify_glue_metrics.py`（C 层 C-P5 新增） | 影子侧 M1–M6；回填期与实盘期同一脚本 | `--date / --all` |

`--got-replay` **只用于恢复影子样本**（C 层 C-P11），不用于算本层红线（C 层 §7.2 同款结论）。

### 6.4 跨层批次耦合（实施排期约束）

按层切分实施计划，会**天然切碎按文件路径耦合的改动**。当前已确证 3 组必须同批：

| 组 | 涉及项 | 耦合点 | 单独落地的后果 |
|---|---|---|---|
| **A-P0-8 × C-P1** | 归档原子替换 × 降级路径落盘 | 同一个文件 `data/got_daily/{date}/agent_glue.json`；`preserved_files` 的正确性依赖"本轮一定写过" | tmp 方案下影子样本**彻底消失**（tmp 里没有 glue） |
| **A-P0-8 × C-P2** | 同上 × `_save_glue_result` 日期基准 | 回放样本落进 `{today}/_replay/`；A-P0-8 会整体搬走 `_replay/` | C-P11 回填样本落错日期目录，**R-C4「连续 7 天」不成立** |
| **A-D-2（A-P0-2 之 D3）× C7** | `load_regime_state_history` 排除当天 × `transition_count` 时间炸弹 | `transition_count` 的读取口径 | OPEN-C1 的倒计时按旧口径推得，**专项会按错日期排期** |

**落地顺序（建议）**：C-P1 → A-P0-8 → C-P2 同批；A-P0-2（D3）落地后立刻重算 C 层倒计时。
其余各组（C-P1…C-P5 与 D-P1…D-P5 同源可观测性）与影子样本无耦合，可独立推进。

---

## 7. 范围外 / 挂起项

### 7.1 范围外（本轮不做，但已核查，另立专项）

| 层 | 项 | 证据位置 |
|---|---|---|
| B | **B 层（概念一致性）已独立成文** → [`got_layer_b_plan_0911.md`](got_layer_b_plan_0911.md)：含 B1–B7 事实核查、5 条已确认处置（D-B1/B2/B3/B4/B6）、防误修清单与 9 条可计数红线。⚠️ **本表原 B1–B6 行的表述已在 B 层核查中被推翻 5 条**（Node6 加减幅度、`final_position_limit` 统计、`航空` 对照、B4/B6 定性、B1 定性），且原编号**跳过 B5**；以新文档为准 | `docs/got_layer_b_plan_0911.md` |
| C | **C 层（影子验收设计）已独立成文** → [`got_layer_c_plan_0911.md`](got_layer_c_plan_0911.md)：含 C1–C8 事实核查、7 条已确认处置（D-C1…D-C7）、10 条公开撤销、8 项防误修清单、7 条可计数红线。⚠️ **本表原 C 行三条表述已被修订/升格**（「M1/M6 结构上不可改善」实为**转正门禁自锁**；「`strengths` 无 `kind` 字段」应为**无口径声明字段 `basis`**；「`range_reversal` 11/11 零区分度」病因更深——`transition_count` 跨方法学断点 + ≈2026-09-25 的时间炸弹）；且**六条转正红线实测 0/6 达标**（2 条门禁自锁 + 2 条不达标 + 1 条不可度量 + 1 条未测），以新文档为准 | `docs/got_layer_c_plan_0911.md` |
| D | **D 层（与 0902 报告对账）已独立成文** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md)：15 项实测，**生效 3 / 症状自行消失 1 / 不修 1 / 未修 10**；**紧急项（立即+1周内 6 项）完成率 1/6**。⚠️ **本表原 D 行只覆盖 5 项，且 P0-1 病因表述偏了** —— 主因不是 `min(分位, −80)`（它只在平静日起作用），而是**阈值总体（全市场 ~400 板块）≠ 判定总体（确认池 ~15）**；另漏 P0-5/P1-5/P2-1/P2-3 未修与 P2-2 症状自行消失。最重单项：`associated_sectors` 恒空同时是 **B1 根因链起点**与**埋伏闸门 G2 注意力子闸 / G3 的死因**（G2 的已发酵子闸仍活，见 D 层 §2.3 修订表） | `docs/got_layer_d_plan_0911.md` |
| E | **E 层（其他具体缺陷）已收口（2026-09-11）** → [`got_layer_d_plan_0911.md`](got_layer_d_plan_0911.md) **附录 C**（决策 E-1：不独立成文，只产出对照表）。12 项全部实测：**4 项被 A/C/D 层吸收或推翻**（E1→D §2.3、E7→A 层推翻、E9→C 层 C7、E8→例证修正）、**7 项分属三种形态**（E2/E3/E4/E5/E6/E10/E11）、**E12 不处置**（4+7+1=12）。⚠️ **E3 比原注重得多**：`sector_exposure_limits` 被反向当角色分类器用，而那个分类器因 `reasoning.split('[')[0]` 解析恒失败 → **26/26 笔买入全走主线分支**（该分支在**修复解析前**不可达；**修好解析后可达**，故 D-P11 属行为变更项而非纯可观测性项）；上限绑定率仅 **1/26 天**。⚠️ **E11 是生产端+消费端双死链**。E 层不解散则必然累积成「其他」——它就是审计报告自己的「其他」家族 | `docs/got_layer_d_plan_0911.md` 附录 C |

### 7.2 OPEN

| # | 项 | 待决 | 建议默认 |
|---|---|---|---|
| **OPEN-1** | **进程级互斥**：0910 run#3/run#4 时间重叠，两者日志交错落在同一份 `ema_baseline.json` 与同批 SQLite 行上 | 引入文件锁/DB 锁，还是约定"正常节奏单跑"？ | **本轮不做**（正常 13:30 单跑不触发）；仅在 A-P0-8 用原子替换降低一半风险 |
| **OPEN-2** | **可复现性**：`meta.git_commit` 不代表运行时代码（0910 的 `a803e09` 修复当时以未提交形式存在于工作区），且 LLM 输出非确定 | 是否落 `git_dirty`/工作区哈希？LLM 是否固定 seed/temperature=0 并落 `prompt_hash`？ | 落 `git_dirty` + 关键字段的 `prompt_hash`（低成本、高收益） |
| **OPEN-3** | **`mention_count` 口径切换**：按日去重后事件生命周期判定会变 | 切换前需几天影子对比？阈值是否回标？ | 双写一天，比对 `emerging/fermenting/peak` 分布无位移后切换 |
| **OPEN-4** | **15:00 不是"日线走完"的唯一判据**：`bar_complete` 用 15:00 墙钟；是否还要考虑数据源延迟（K 线落库时刻） | 用墙钟还是用实际 bar 的时间戳？ | 先用墙钟（简单、与你的 13:30 节奏配合），后续如需精确再改为 bar 时间戳 |

---

## 附录 A：0910 状态污染一览（修复前基线）

| 状态 | 修复前（0910） | 单次运行应有值 |
|---|---|---|
| `market_regime.regime` | `transition` | `bull` |
| `market_regime.dwell_days` | `2` | `7` |
| `market_regime.previous_regime` | `transition` | `bull` |
| 板块敞口上限（11 个板块） | `0.182` | `0.28` |
| `ema_baseline.regime_score` | `0.188` | `0.269` |
| `ema_baseline.sample_count` | `125` | `124` |
| `monitoring.cross_node.signal_count_30d` | `35`（含 2 条同日 `range_reversal`） | 少 1 条 |
| `node4_events.0228c8d9.mention_count` | `3` | `1` |
| `node4_raw_news` 同日 `appearance_count>=2` 行数 | `127` | 显著更少 |

## 附录 B：本文事实核查约定

沿用 `agent_glue_plan_0903.md` 附录 B：所有代码断言带文件路径 + 行号，均为 **HEAD `a803e09` +
2026-09-11 工作区快照**的只读核查结果；若实施时代码已变动，**以代码为准，不以本文为准**。
§2.3 记录本轮被推翻的 4 条错误主张，§7.1 记录已核查但不在本轮射程的 B–E 层问题，
供后续开专项时直接接手。
