# GOT 0911 实施审核报告

> **审核对象**：2026-09-11 工作区的**未提交实施改动**（13 个 tracked 文件，+730/−330，新增 1 个模块）。
> 对照物：[A 运行模型](got_run_model_plan_0911.md) / [B 概念一致性](got_layer_b_plan_0911.md) /
> [C 影子验收](got_layer_c_plan_0911.md) / [D 与 0902 对账](got_layer_d_plan_0911.md) 四份修复方案的
> **修复清单（§5）**与**验收红线（§6）**；修订历史见[修订台账](got_revision_ledger_0911.md)。
>
> **基准**：HEAD `a803e09` + 2026-09-11 工作区快照。代码最后变动 20:28，审核在 21:0x–21:1x 完成。
>
> **本文只审核，不改代码**。

---

## 0. 结论摘要

| 类别 | 数量 | 说明 |
|---|---|---|
| **P0** | **2** | 零买入日 Phase 2 静默丢失；A-P0-7 新增列无 DB 迁移 ⇒ 现有库事件持久化**全废** |
| **P1** | 8 | 含 1 条**红线 FAIL 且症状恶化**（`航运概念` 掉进「其他」）、1 条未实现（D-P13）、文档未同步 |
| **P2 / 卫生** | 6 | 含新增**裸 `except` 无日志**、游离产物、实施总结与真实改动严重不符 |
| **红线达标** | A 4/9、B 3/9、C 4/13、D 4/14 | 详见 §6 |

> ⚠️ **本文已有第二轮**：修复轮（2026-09-11 夜）之后的重审结论见文末
> **[第二轮复审](#第二轮复审修复轮之后2026-09-11-夜)** —— 第一轮 13 条中 **11 条已修**，
> 但**修复轮引入了新的 P0**（`_allocate` 引用未定义变量 `max_limit`/`role_threshold` ⇒ 任何有买单的日子 Phase 2 必崩）、
> **4 个验收脚本全部掉包了红线定义**、`0903.md` 的口径修正写错（新 P1）。
> **§0–§10 记录的是修复轮之前的状态，红线达标表已过期，勿单独引用。**
>
> ✅ **全修轮（2026-09-11 夜）已完成**：R2.2 的 P0 已修并**运行时验证**；`cooling` 角色错配、D-P4 分数平价、
> `archive.py` 中断残留、卫生与接线类 7 项、**4 个验收脚本的真算改造**均已落地；
> **3 个脚本被 `.gitignore` 吞掉**的问题已修（§R2.12）。
> ⚠️ 脚本跑通后浮出 4 条**真实 FAIL**，其中 **R2 这条红线本身无效**（把市场事件当验收条件）—— 见 §R2.11。
> 仍待决策 3 项 + 全量测试，见 **§R2.13**。

**最刺眼的一行**：四份文档把「**可计数的强制验收红线**」写进每一份的工程约定，而承诺的
**4 个验收脚本一个都不存在**（§5）—— 不是「没跑」，是**判据的载体从未被建出来**。

**另一条元层判断**：实施者的 `E3_fixes_summary.md` 声明「修改文件：`schemas.py` + `portfolio_decision.py`」、
只记录 D-P11/12/13 三项，**实际改了 13 个 tracked 文件 + 1 个新模块**。
⇒ 「实施完成」这个**声明的对照物本身不存在**，无人能拿那份总结去核对。
这正是四份文档反复批判的「**自述 ≠ 验收**」，这次出现在实施侧。

---

## 1. 审核方法与证据分级

本文把每条结论的**证据级别**标出来，避免把"报告说的"当成"已经验过"：

| 标记 | 含义 |
|---|---|
| **★** | **独立实测 / 读码确认**（本次审核亲手验证，可复现） |
| ○ | 来自四路审核 agent 的报告，**未逐一复核**（行号与结论可信，但引用前建议复验） |

**环境偏差（★）**：实际执行环境是 **Python 3.10.20**，而项目要求 **3.12.13**
（`CLAUDE.md` 的 Conda 段）。本次测试结论受此影响，**不能等同于 3.12 下的结论**。

---

## 2. 测试结论（★）

| 项 | 结果 |
|---|---|
| 全量 | **22 failed / 3628 passed / 2 skipped**（11:49） |
| **GOT 相关 14 个测试文件单跑** | **249 passed / 0 failed**（7.51s） |
| 22 个失败的归属 | 均在非 GOT 模块：`test_system_config_service` 7、`test_main_schedule_mode` 5、`test_static_assets_consistency` 3、`test_stock_index_loader` 1、`test_yfinance_fundamental_adapter` 1 |
| ⚠️ 截断 | 后台输出文件只保留了 **17/22** 条 FAILED 明细；剩余 5 条未见。**但 GOT 子集单跑 249/249 全绿**，故可判定失败不涉及 GOT |
| 收集失败 | `tests/test_discord_platform.py` 收集报错（**不在本次改动的 13 个文件内**，与 GOT 无关） |

### ⚠️ 「全绿」是假的安全感

GOT 子集 249/249 全绿，但 **P0-1（零买入日崩溃）没有被任何用例覆盖** ——
绿色只说明「被测的路径没坏」，而这次的 bug 恰好长在**零买入日**这条没人写用例的路径上，
而它占历史 26 天里的 **13 天（约 50%）**。

### ★ 测试污染生产归档（实测）

```
data/got_daily/20260911/            目录创建 20:26
data/got_daily/20260911/agent_glue.json          mtime 21:11   ← 本次单跑 GOT 子集的时间
data/got_daily/20260911/_replay/agent_glue.json  mtime 21:11
```

`tests/test_node6_glue.py` 等直接调用 `_agent_semantic_glue` 且**未 mock `_save_glue_result`**，
而 C-P1 新增的落盘路径写的就是**生产归档目录**（不是 tmp、不是测试目录）。
⇒ **跑一次测试就会往当天归档写夹具数据**；在「末版为准」语义下会覆盖真实影子样本。

---

## 3. P0（2 条）

### P0-1 ★ 零买入日 Phase 2 整体崩溃（静默丢失全部产出）

**位置**：`src/got/nodes/portfolio_decision.py:137`、`:1911`、`:1995`

```python
:137    ambush_buys, ambush_failed = self._write_ambush_buy_snapshots(...)   # 解包 2 元组
:1911       if not buy_allocations:  return []                              # 只返回 1 个值 ❌
:1995       return written, failed_symbols                                  # 正常路径才是 2 元组
```

**现象**：零买入日 → `ValueError: not enough values to unpack (expected 2, got 0)`。

**后果**：调用点在 `run()` 内**无 try**，异常上抛到 `src/got/orchestrator.py:156-158`：

```python
except Exception as exc:
    logger.warning("[GOT] Phase 2 失败（已忽略）: %s", exc)
```

⇒ **当天 `portfolio_decision.json` / `trading_recommendations.md` 全部产出静默丢失**，终端仅一行 warning。
历史 26 天里 **13 天 `buy_budget = 0`** ⇒ 约 **50% 的交易日命中**。

**根因**：典型的「只改了成功路径」。`A-P0-6` 把该函数返回值从单值改成 2 元组，**漏改提前返回**。
函数签名至今仍写 `-> List[Dict[str, Any]]`（实际返回元组），docstring 仍写「返回成功写入的快照列表」。

**建议改法**：`:1911` 改 `return [], []`；签名改为 `-> tuple[list[dict], list[str]]`；同步修正 docstring。

---

### P0-2 ★ A-P0-7 新增列无 DB 迁移 ⇒ 现有生产库事件持久化全废

**位置**：`src/storage.py:1022`（`node4_events.mention_count_run_based`）、`:1347`（`node4_raw_news.appearance_count_run_based`）

**实测（本次用实库 PRAGMA 确认）**：

```
node4_events    -> mention_count_run_based 存在? False   (共 14 列)
node4_raw_news  -> appearance_count_run_based 存在? False (共 11 列)
```

`src/storage.py` 共 5 个迁移函数
（`_migrate_node2_sector_trends` / `_migrate_stock_daily_canonical_id` / `_migrate_catalyst_realizations` /
`_migrate_catalyst_events` / `_migrate_deactivate_ambush_frames`）—— **没有任何 node4 迁移**。
而 `Base.metadata.create_all` **只建新表、不补旧表列**（项目自己在 `_migrate_node2_sector_trends`
的 docstring 里已写明这个坑）。

**后果**：ORM 对 `Node4EventRecord` / `Node4RawNewsRecord` 的 SELECT/INSERT 立即
`OperationalError: no such column`；而 `news_analysis.py` 的 `except Exception as e: logger.error(...)`
只记日志 ⇒ **事件持久化、跨日去重、`lifecycle_state` 更新静默全废**，且**不会让主流程报错**。

**建议改法**：补 `_migrate_node4_run_based_columns()`（`ALTER TABLE ... ADD COLUMN`，幂等，
用 `PRAGMA table_info` 判断存在性），并注册到 `initialize()` 的迁移调用链。

---

## 4. P1（8 条）

| # | 问题 | 位置 | 证据 |
|---|---|---|---|
| **P1-1** | **A-P0-1 未达成**：文档点名的 **3 处绕过原样存在**（各自 `iterdir()` 自排序、不排除当天、不走统一入口）；`_list_archive_dirs` 仍是**私有**，未导出公共函数 | `aggregator.py:2149-2152`、`sentiment_analysis.py:943`、`catalyst_calendar.py:1060-1062` | ★ 读码 |
| **P1-2** | **R-B4 FAIL 且症状恶化**：`航运概念` 从「误归军工」变成**掉进「其他」**；`光刻机` 同样落「其他」。`family_keywords.py` 的 docstring 写「移除 `航运`（航运概念**归入周期资源**）」，**但只删未补** —— 周期资源族里没有 `"航运"`。同时把「其他」占比推高，**正面违反 B 层 §2.8 防误修立场** | `src/got/family_keywords.py` | ★ 实测 `_build_frame_group` |
| **P1-3** | **D-P13 未实现**：文档要求「改读 `sector_role`」，代码里只有 `sector_role = None` + `pass` + TODO。实施者自己的总结亦承认 | `portfolio_decision.py:2858-2863` | ★ 读码 |
| **P1-4** | **D-P11 只完成解析层**：分支语义仍是「用 limit 数值反推角色」，不是读 `sector_role` ⇒ 把**恒假**换成了**另一个错误分类器**（limit 值受 confidence 影响，同样会误判） | `portfolio_decision.py` | ★ 读码 |
| **P1-5** | **文档未同步**：`agent_cot_design.md`（CLAUDE.md 自己指定的「详细设计文档」）仍写「L1': 埋伏通道(**4 闸门**+蓄水池）」`:53`、「**4 闸门流水线**」`:505` —— D-P5 正是要求卸掉这个说法 | `agent_cot_design.md` | ★ `git status` 未改动 |
| **P1-6** | **新增裸 `except Exception:` 无日志**，且 `import TradingCalendar` —— **该类在仓库中不存在**（`src/core/trading_calendar.py` 只有模块级 `is_market_open`）⇒ 恒抛 ImportError、恒走 `return run_date.weekday() < 5` ⇒ `bar_complete` 退化为「15:00 后且工作日」，**节假日被误标为已完成** | `archive.py:625-631` | ★ 读码 |
| **P1-7** | ○ **`.tmp` / `.old` 目录会击穿 `_list_archive_dirs` 的 `before` 过滤**：`_SKIP_DIR_SUFFIXES` 不含这两个后缀，且无 `isdigit()` 守卫 ⇒ `datetime.strptime("20260911.tmp", "%Y%m%d")` 抛 `ValueError`。归档写入期间并发跑 Node 6，或异常残留，都会让**之后每次 GOT 运行的所有 loader 崩溃** | `archive.py:93-94`、`persistence.py:227-233` | ○ 审核报告 |
| **P1-8** | ○ **C 层指标定义未落地**：C-P6（M1 改 glue 侧）/ C-P7（M6 改回收率）/ C-P8（M4 系数与前提）均未见落地；`0903.md` §8.1 仍是旧口径 ⇒ **R-C3 无法达标** | `docs/agent_glue_plan_0903.md` | ○ 审核报告 |

---

## 5. 承诺的验收脚本：一个都不存在（★）

四份文档的 §6 都以「**可计数的强制验收红线**」为核心承诺。实测：

```
scripts/verify_glue_metrics.py         -> 【不存在】   （C-P5，R-C3 / R-C6 的唯一手具）
scripts/check_audit_claims.py          -> 【不存在】   （D-P9，R-D1 / R-D2）
scripts/verify_layer_b_consistency.py  -> 【不存在】   （B 层 §6.3，R-B3 / R-B8 / R-B9）
scripts/verify_same_day_idempotency.py -> 【不存在】   （A 层 §6.3，R1–R6）
scripts/ 近 24h 新增文件: 无
```

⇒ **39 条红线（A 9 + B 9 + C 7 + D 2、含 R-D1 的四个子计数）里，没有任何一条能被机械执行。**

> **这不是"没跑"，是判据的载体从未被建出来。**
> 把验收条件写进文档是**承诺**，写进 `scripts/` 才是**能力**。
> 文档里写着「R-B2：单测断言」并不会让那个断言存在。

---

## 6. 红线达标总表

| 层 | 达标 | 未达标 / 部分 / 无证据 |
|---|---|---|
| **A**（9 项） | A-P0-2 ✓ A-P0-3 ✓ A-P0-4 ✓ A-P0-8 ✓（**用了正解**：搬 glue + `_replay/` 进 tmp，非 naive swap） | **A-P0-1 ✗**（3 处绕过）、**A-P0-7 ✗**（无迁移，P0-2）、A-P0-6 ⚠️（解包 bug，P0-1）、A-P0-9 ⚠️（P1-6）、A-P0-5 部分 |
| **B**（9 项） | R-B2 ✓（集合相等已成立）R-B5 ✓（形参已删，白名单活代码未被误删）R-B6 ✓ | **R-B1 ✗（2/4 口径标注）**、**R-B4 ✗**（P1-2）、**R-B7 ✗**（glue prompt 未标单位）；R-B3 / R-B8 / R-B9 **无证据**（脚本不存在） |
| **C**（13 项） | C-P1 ✓ C-P2 ✓（`main.py:1344` 已传 `run_date=replay_date`）C-P4 ✓ C-P10 ✓ | **C-P5 ✗**（脚本不存在）、C-P6/C-P7/C-P8 ✗、C-P3 ⚠️（replay 分支新增落盘副作用，违约）；R-C3 / R-C6 **结构性不可达** |
| **D**（14 项） | D-P6 ✓ D-P7 ✓ D-P8 ✓ D-P14 ✓ | **D-P13 ✗**（P1-3）、D-P9/D-P10 ✗（脚本不存在）；D-P1（缺分位落盘）、D-P2（`skipped_reason` 从未赋值）、D-P3（常量双源）、D-P5（留痕未落地）、D-P11（仅解析层）、D-P12（绑定次数未落盘）**部分**；R-D1 / R-D2 **无法验收** |

**未达标集中规律**：**凡是"需要新写一个东西"的（脚本、迁移、留痕）全部缺失；
凡是"改一行现有代码"的（删形参、改文案、搬 glue）基本都做了。**

---

## 7. 与文档声明不符之处

| # | 声明 | 实际 | 证据 |
|---|---|---|---|
| 1 | `E3_fixes_summary.md`：**「修改文件：`schemas.py` + `portfolio_decision.py`」** | 实际 **13 个 tracked 文件 + 1 个新模块**（`family_keywords.py`），+730/−330 | ★ `git diff --stat` |
| 2 | `E3_fixes_summary.md` 只记 D-P11/12/13 三项 | 实际横扫 A/B/C/D 四层 | ★ |
| 3 | `E3_fixes_summary.md` §5「**结论：逻辑正确，ambush 分支可达**」 | 该节自身推导混乱：先用 `max_limit = 0.40`（理论上限）算阈值 0.20，又用实测 0.182 当 mainline limit，**两者不同源**。D 层文档实测阈值是 **0.091**（`0.182 × 0.5`）。结论碰巧对，推导错 | ★ 读码 |
| 4 | 文档称 A 层「**零行为变更**」（除 A-P0-8） | 工作区夹带大量 B/C/D 层的行为变更项（含 D-P4 隐藏变更：`sentiment_check` `low`→`missing` 使 `−5` 消失、composite 系统性 +5） | ○ 审核报告 |
| 5 | D-P5 要求改 `CLAUDE.md` 的「埋伏检测」行 | 该行实际写的是「6 维加权评分（非 AND 闸门）」，**根本没有「4 层闸门」** ⇒ 文档**位置指错**；真正的载体在 `agent_cot_design.md:505` | ★ |
| 6 | 文档 §5.2 要求 `光刻机` → 「设备子集」 | **受控词表里不存在「设备」族** ⇒ 该期望**文档自身不可实现**，实测落「其他」 | ★ |

---

## 8. P2 / 卫生问题

| # | 问题 | 位置 |
|---|---|---|
| 1 | **游离调试残片**：`_temp_check.bin`（500 B，实为 `aggregator.py` 里 `_check_anti_top_buy` 的代码片段，扩展名伪装成二进制）。**从未提交，不该在仓库根目录** | 仓库根 |
| 2 | **实施总结放错位置**：`E3_fixes_summary.md` 在仓库根而非 `docs/` | 仓库根 |
| 3 | ○ **D-P3 常量双源 + 新增死配置**：`archive.py` 仍硬编码 `(2, 6)` 且留着过期 TODO（"待 catalyst_config.py 新增 AMBUSH_POOL_TARGET_RANGE 后改为导入"—— 而该常量**已存在**）；`AMBUSH_POOL_MIN_DAYS` 零消费者 | ★ diff + ○ 审核报告 |
| 4 | ○ **P4 快照路径仍是同一解析 bug**：`:1933` 仍用 `_extract_sector_from_reasoning` 解析候选 reasoning（无 `板块[`）⇒ 恒 `""` ⇒ `continue`。DB 实测 `ambush_position_snapshots` **0 行** | ○ 审核报告 |
| 5 | ○ **P4 异常策略收紧 + 死返回**：新增 `raise` 使原先可降级的快照写失败改为中断 Phase 2；`failed_symbols` 非空必然 `raise` ⇒ `:1995` 的非空分支与调用侧 `if ambush_failed` 是**不可达死代码** | ○ 审核报告 |
| 6 | ○ **D-P2 漏斗把死闸记成"已评估"**：`skipped_reason` 初始化后**从未赋值**；"全通过"时对 G2/G3 一律 `evaluated += 1` ⇒ 掩盖死闸，与 D-P5 目标相反 | ○ 审核报告 |

---

## 9. 建议处置顺序

1. **P0-1**（一行：`return [], []` + 签名/docstring）—— 影响 50% 交易日，改动最小
2. **P0-2**（补 node4 迁移）—— 现有库必崩，且被 `except` 掩盖
3. **P1-2**（`"航运"` 补进周期资源族）—— 红线 FAIL + 症状恶化，一行
4. **P1-1 / P1-3 / P1-4**（A-P0-1 三处绕过、D-P13、D-P11 语义层）—— 属"重构量较大"类，需排期
5. **§5 的四个验收脚本** —— 不做则全部红线继续不可执行；建议**与修复同批**，否则又是一轮"自述 ≠ 验收"
6. **P1-5 / §7 / §8**（文档同步、卫生清理）—— 随提交一并处理
7. `_temp_check.bin` 属**删除操作**，需你确认后再动

---

## 10. 本报告未覆盖的

- 22 个测试失败中 **5 条未见明细**（后台输出被截断）；已确认的 17 条均与 GOT 无关
- ○ 标记的 11 条来自审核 agent，**未逐一复核**；引用前建议按行号复验
- 未做**归档回放**（R-A/B/C/D 的零行为变更红线本应由回放证明）—— 对应脚本不存在，无法进行
- 未评估 D-P11 修复后的**实际分支分布变化量**（需在 26 个归档日上回放；本报告只确认"可达")


---

# 第二轮复审（修复轮之后，2026-09-11 夜）

> 修复轮改动：`git status` 由 13 个 tracked 文件增至 **16**（新增 `agent_cot_design.md`、`docs/agent_glue_plan_0903.md`、
> `src/got/catalyst_calendar.py`），合计 **+868/−366**；新增 `scripts/check_audit_claims.py`；
> 承诺的 4 个脚本**全部建出**。代码最后变动 22:42，本轮复审在 23:2x 完成。

## R2.1 第一轮报告的问题：13 条中 **11 条已修**（★ 逐条实测）

| 第一轮 # | 状态 | 证据 |
|---|---|---|
| **P0-1** 零买入日解包崩溃 | ✅ **已修** | `portfolio_decision.py:1911` → `return [], []`；签名改为 `-> tuple[List[Dict[str, Any]], List[str]]` |
| **P0-2** node4 新增列无迁移 | ✅ **已修** | `storage.py` 新增 `_migrate_node4_run_based_columns()` 并注册于 `:1525`；**实库 PRAGMA 确认两列已存在** |
| **P1-1** A-P0-1 三处绕过 | ✅ **已修** | 三个 `iterdir()` 全消；`_list_archive_dirs` 增 `before` 参数 + `ValueError` 守卫；7 个 loader 统一调用 |
| **P1-2** `航运概念` 落「其他」 | ✅ **已修** | 实测 `_build_frame_group('航运概念') == '周期资源'` |
| **P1-3** D-P13 未实现 | ✅ **已修** | 改读 `sector_role_map`（`portfolio_decision.py:2838-2842, 2858`）；0910 数据确认 **16 个 ambush 板块在 map 内** |
| **P1-4** D-P11 只做解析层 | ✅ **已修** | 分支不再由 limit 反推，改由 `sector_role` 决定 |
| **P1-5** `agent_cot_design.md` 未同步 | ✅ **已修** | `:53` / `:505` 已改为「4 层闸门（其中 G2 注意力闸 / G3 因无数据源未启用）」 |
| **P1-6** `_is_bar_complete` 类不存在 + 吞异常 | ✅ **已修** | 改用**存在的**模块级 `is_market_open`；`except` 现为 `logger.debug(...)`（**⚠️ debug 级偏弱，见 R2.4**） |
| **P1-7** `.tmp` / `.old` 击穿过滤 | ✅ **已修** | `_SKIP_DIR_SUFFIXES` 已含 `".tmp", ".old"`（`persistence.py:212`） |
| **§5** 4 个验收脚本不存在 | ⚠️ **脚本已建，但见 R2.2 —— 问题升级而非解决** | 4 个文件均在 `scripts/` |
| **§8-1/§8-2** 卫生 | ✅ **已修** | `_temp_check.bin` 已删；`E3_fixes_summary.md` 已移入 `docs/` |

**GOT 测试**：改动后重跑 14 个 GOT 测试文件 → **249 passed / 0 failed**（8.13s）。

## R2.2 🔴🔴 新 P0（对抗复核抓出，**本报告第一轮遗漏**）→ ✅ **已修**：`_allocate` 引用未定义变量

**位置**：`src/got/nodes/portfolio_decision.py:2876-2877`

```python
sector_limit_detail = {
    "sector_name": score.sector_name,
    "max_limit": max_limit,            # ← 未定义
    "role_threshold": role_threshold,  # ← 未定义
    "stock_limit": sector_limit,
    "branch": branch,
}
```

**AST 扫描确认（★）**：

```
【未绑定】_allocate() 读取了 max_limit，但函数内无赋值 -> NameError
【未绑定】_allocate() 读取了 role_threshold，但函数内无赋值 -> NameError
```

`grep` 全文件：`max_limit` 仅 `:2876` 一处、`role_threshold` 仅 `:2877` 一处 —— **两者都只有「读」，没有「写」**。

**后果**：该 dict 位于 `if quantity > 0:` 之内 ⇒ **任何有买单的交易日**进入 `_allocate` 即抛
`NameError: name 'max_limit' is not defined`。异常路径与第一轮 P0-1 **完全相同**
（`run()` 内无 try → `orchestrator.py:156` 只记 warning）⇒ **当天 Phase 2 全部产出静默丢失**。
历史 26 天里 13 天有买入 ⇒ 约 **50% 的交易日**。

**性质**：这是**修复 P0-1 / D-P11 时引入的回归** —— 同一函数内，改掉了旧的 `return []` 解包 bug，
同时新造了一个必崩的未定义引用。**它与第一轮的 P0-1 形状完全一样（Phase 2 静默丢失）。**

> ⚠️ **本报告第一轮遗漏了它**：作者当时已打印出含 `"max_limit": max_limit` 的代码块，
> 却把它归为"字段名暗示因果"的 **P2 命名问题**，**未核对变量是否已定义**。
> 由对抗复核 agent 抓出。**教训：看到「把变量记进 dict」时，必须回查该变量在哪赋值。**

**建议改法**：删掉 `max_limit` / `role_threshold` 两个键（分支已由 `sector_role` 决定，二者不再是判据），
或补回其定义并与 `sector_role_map` 的口径对齐；`branch` 另建议扩为三值（`mainline`/`ambush`/`cooling`）。

### ✅ 修复记录（2026-09-11 夜，本报告作者实施）

**改动（3 处）**：

| 文件 | 改动 |
|---|---|
| `src/got/nodes/portfolio_decision.py:2874-2880` | dict 删除未定义的 `max_limit` / `role_threshold`，**改为记录真正的判据 `sector_role`**；`stock_limit` 保留并注明"仅参考值" |
| `src/got/nodes/portfolio_decision.py:2833-2835` | 修正失效注释：原写"回退到 `sector_exposure_limits` 反推"，实际 fallback 是 `"mainline"`，反推逻辑已随 D-P13 移除 |
| `src/got/schemas.py:1024` | `sector_limit_detail` 的 description 同步为 `{sector_name, sector_role, stock_limit, branch}` |

**验证（★ 三层，非推断）**：

1. **类级扫描**：用 stdlib `symtable` 写未定义名扫描器（区分局部/闭包/全局）扫全仓 `src/` + `scripts/` ——
   `max_limit` / `role_threshold` **已从结果中消失**；其余 33 条为误报（`__file__` 由解释器注入、`feishu_doc.py` 条件导入）。**该类缺陷在仓内不再存在。**
2. **导入检查**：`import src.got.nodes.portfolio_decision, src.got.schemas` → OK。
3. **运行时矩阵验证**（★ 决定性证据，直接调用 `_allocate` 走 `quantity > 0` 分支）：

| 板块 | `sector_role` | 落盘 `branch` | 止损价 | 仓位 |
|---|---|---|---|---|
| MLCC概念 | ambush | ambush | **9.50**（=10.0×0.95，−5%） | 440,000（×0.55） |
| 粮食概念 | mainline | mainline | 9.30（−7%） | 800,000 |
| 旅游景点 | **cooling** | **mainline** | **9.30** | **800,000** |
| None | mainline | mainline | 9.30 | 800,000 |

⇒ **无 `NameError`**；埋伏分支经 `sector_role` 正确生效（×0.55 预算 + −5% 止损）；
且**新增的 `sector_role` 字段立刻暴露了 cooling 的错配**（`branch=mainline` 而 `sector_role=cooling`）——
这正是把它加进留痕的价值：**它让缺陷可见，而不是掩盖**。

**回归**：GOT 14 个测试文件 **249 passed / 0 failed**。

> ⚠️ **该修复无测试覆盖**：`grep -rn "_allocate" tests/` **为空** —— 与第一轮 P0-1 同一个盲区。
> 本条的验证**完全依靠**上述运行时矩阵（手工构造上下文），**不是**测试套件发现的。
> 建议为 `_allocate` 补三条用例（ambush / mainline / cooling），否则下一次同类回归仍不会被 CI 拦住。


## R2.3 🔴 新发现 P0 级：4 个脚本**全部掉包了红线定义**

**4/4 脚本存在、可执行、打印 PASS；0/4 验证自己名字所指的那条红线。** 脚本自己写明了这一点：

| 脚本 | 自述（原文） | 实际检查的东西 | 其红线在文档里的定义 |
|---|---|---|---|
| `verify_same_day_idempotency.py` | 「通过 AST + 文本模式匹配检查代码约束，**不运行 GOT**」 | 源码里有无 `before=date.today()` | A 层 §6.3：「**同日实跑两次**，按 §6.2 清单提取状态字段并 diff」 |
| `verify_glue_metrics.py` | —（docstring 自行改写 R-C3/R-C6） | 「`return` 语句数 = 8」「status 字面量齐 8 种」 | C 层 R-C3：`--date 20260910` 输出与文档 **M1=3.1% / M1′=34.4% / M2=28.1% / M4-a=0.591P,0.419S / M4-b/c=0.646P,0.500S / M6=65.6% / M6′=96.9% / 回收率 90.9%** 逐位一致 |
| `verify_layer_b_consistency.py` | 「本脚本用 AST 静态分析验证 import 路径与定义位置，**不执行业务代码**」 | `CONTROLLED_FAMILIES` 的 import 路径、家族数 = 14 | B 层 R-B3：「同一期归档**回放**，`crowded_families` 非空天数与家族拥挤降权触发次数变化**有界**」 |
| `check_audit_claims.py` | 「验证…修复项是否落地」 | grep `sector_role_map 出现: True`、`sentiment_check 返回 missing` | D 层 R-D1：`unverified` 条数 = **0**（基线 10）；R-D2：「附录 A 与脚本输出**逐位一致**」 |

**为什么这比「脚本不存在」严重**：

- 文件缺失是**可见的**；一个打印 `[PASS] R-C3` 的脚本是**不可见的假绿灯**。
- 四份文档的核心论点之一是「**代码存在 ≠ 分支可达**」。现在这个形态出现在**验证层自身**：
  脚本的存在被当成了红线的达标。
- 脚本 docstring 里**直接改写了红线定义**（`verify_glue_metrics.py` 把 R-C3 改写成「必须有 8 个 return 路径」）
  ⇒ 读者若只读脚本，会得到与文档**不同**的判据。

**建议**：每个脚本的 docstring 顶部**原文引用**其红线在文档中的定义，再写实现；
二者不一致时以文档为准。且 R-C1…R-C7 / R-B3/B8/B9 / R-D1/D2 / R1…R6 中，
**凡定义含「回放」「diff」「逐位一致」「算出某指标」的，静态分析在原理上不可能达成** ——
这几条必须真的跑数据（0910 的 `unified.json` + `agent_glue.json` 都在 `data/got_daily/` 里）。

## R2.4 🔴 新 P1：`docs/agent_glue_plan_0903.md` 的口径修正**三重错误**

修复轮在该文档 §8.1 新增两行：

```
| M1' | **glue 消息面非中性率**：glue relations 中 `news_check != "neutral"` 的候选占比 | 实测 3.1%（规则侧恒值） | ≥ 60% | `agent_glue.json` `relations[].news_check`（glue 侧，非 `unified.json`） |
| M6' | **glue 家族覆盖率**：glue relations 中 `family != "其他"` 的占比 | 实测 65.6%（规则侧恒值） | ≥ 80% | `agent_glue.json` `relations[].family`（glue 侧，非 `unified.json` `frame_group`） |
```

| # | 错误 | 实测证据 |
|---|---|---|
| 1 | **字段路径不存在** | `agent_glue.json` 实测：`relations[]` 的键为 `[event_id, board_code, strength, direction, propagation_path, confidence]` —— **无 `news_check`、无 `family`**。正确路径是 `strengths[].news_read`（键：`board_code, strength, confidence, evidence, capital_read, news_read, sentiment_read`）与 `families[].family`（键：`board_code, family`） |
| 2 | **数值是规则侧的** | 3.1%（M1）/ 65.6%（M6）**正是规则侧恒值**，而该行标签写「glue 侧」⇒ **同一行内自相矛盾** |
| 3 | **正确值未采用** | C 层 §1 实测：M1′（glue 侧）= **34.4%**；M6′ = **96.9%**；C-P7 的「其他」回收率 = **90.9%** |

**这是同一错误形态的第 4 次复发**：「M1 / M1′ 跨口径混用」在
① D 层 §1 与 R-C3 的锚点互换、② C 层 §0 的计数、③ 修订台账 §1 的 #3、④ 本处，各出现一次。

## R2.5 🟡 新 P2

| # | 问题 | 位置 |
|---|---|---|
| 1 | **`cooling` 角色落到 `else` 分支**：拿到主线仓位与 −7% 止损，与 `ROLE_MULTIPLIER = {"mainline":1.0, "ambush":0.55, "cooling":0.3}` 的设计意图**相反**；且 `branch` 只记 `mainline`/`ambush` 两值 ⇒ **留痕把 cooling 记成 mainline**（0910 有 5 个 cooling 板块）。<br>⚠️ **2026-09-11 运行时证实**：cooling 板块实测 `branch=mainline`、止损 **9.30**、仓位 **800,000** —— 与 mainline **完全相同**，`ROLE_MULTIPLIER["cooling"]=0.3` 从未生效。**未修**：属行为变更（会改变仓位与止损），需你决定 | `portfolio_decision.py:2863-2868` |
| 2 | **注释与实现不符**：注释写「回退到 `sector_exposure_limits` 反推（当 `sector_role` 不可用时）」，但代码 fallback 是 `"mainline"`，**没有反推逻辑** | `portfolio_decision.py:2837-2838` |
| 3 | `sector_limit_detail` 仍落 `role_threshold` / `stock_limit`，但分支**已不由它们决定** ⇒ 字段名暗示因果，实际是无关留痕 | `portfolio_decision.py:2876-2878` |
| 4 | `_is_bar_complete` 的降级日志用 `logger.debug` —— 生产环境基本不可见（降级本身影响 `bar_complete` 语义） | `archive.py:679` |
| 5 | ○ 待复核：`sector_role_map.get(..., "mainline")` 的 fallback 方向是「放大风险」（主线全额仓位 + 更宽止损），而非保守侧 | `portfolio_decision.py:2858` |

## R2.6 测试结论（全量）

| 项 | 结果 |
|---|---|
| 全量 | **22 failed / 3628 passed / 2 skipped**（与第一轮**同一批**） |
| **GOT 相关失败** | **0**（grep 筛查 `node*/got/glue/regime/phase/sector_state/ambush/catalyst` = 0 命中） |
| 失败分布 | `test_main_schedule_mode` **9**、`test_system_config_service` 7、`test_static_assets_consistency` 3、`test_yfinance_fundamental_adapter` 1、`test_stock_index_loader` 1、`test_fetcher_source_optimization` 1 = 22 |
| **失败根因（★ 单跑定位）** | **不是断言失败**，而是 pytest 自身 capture 机制的 `ValueError: I/O operation on closed file`（`_pytest/capture.py:591`）。该测试文件 **0 次触及 replay 分支**，而 `main.py` 的唯一改动 `run_date=replay_date` 位于 replay 分支内 ⇒ **与本次实施无关**；诱因是测试自己 `handler.close()`（`:56` / `:1516` / `:1563`）关掉了 pytest 捕获的流 |
| 环境 | 实跑 **Python 3.10.20**（项目要求 3.12.13）；`tests/test_discord_platform.py` 收集失败（不在改动范围内） |

## R2.7 对抗复核的结论（已并入）

> 该 agent 的任务是**证伪「修复轮已解决全部问题」**。以下为其判定；标 ○ 者未逐一复验。

### (A) 第一轮 §4 P1-8 与 §8 的 3/4/5/6 项

| 项 | 判定 | 证据 |
|---|---|---|
| **P1-8** C-P6 / C-P7 / C-P8 | ○ **部分修（三子项均未按 C 层定义落地）** | `0903.md` 只把 M1/M6 改名为 M1'/M6' 并换了数据源写法，但：**字段路径错**（`relations[].news_check` / `relations[].family` 在 `agent_glue.json` 里不存在）、**没有规则侧并列对照列**（C-P6 要求）、**M6 未改成「其他」回收率**（C-P7 要求，锚点 90.9%）、**M4 子表仍写 `≥0.6` 且无系数列**（C-P8 要求增列 Spearman + 标「待标定」） |
| **§8-3** D-P3 常量双源 | ○ **部分修** | `archive.py:473-475` 已改 `from src.got.catalyst_config import AMBUSH_POOL_TARGET_RANGE`，不再硬编码 `(2,6)`，过期 TODO 已删；**但 `AMBUSH_POOL_MIN_DAYS` 仍零消费者** |
| **§8-4** P4 快照解析 bug | ○ **已修** | `portfolio_decision.py:1932-1934` 改为 `scoring.sector_name`。副作用：`_extract_sector_from_reasoning` 已无生产调用者，**成死代码** |
| **§8-5** P4 异常收紧 + 死返回 | ○ **已修**（**且第一轮报告的该项描述已不符当前代码**） | 现 `except` 只 `logger.warning` + 收集 `failed_symbols`，无 `raise`；`failed_symbols` 被调用侧 `:141 if ambush_failed` 消费，非死代码。遗留小瑕疵：异常分支把所有未写入的 buy_allocations 都记为 failed，**非 ambush 板块（本就被 `continue` 跳过）会被误报** |
| **§8-6** D-P2 死闸记 `evaluated` | ○ **部分修** | 新增 `structurally_dead` / `structurally_dead_subgate` 标注（`aggregator.py:2413-2414`），但「全通过」分支**仍对 G2/G3 `evaluated += 1`**；**`skipped_reason` 全仓 0 命中 —— 该字段在当前代码里根本不存在**（第一轮报告的该项描述失焦） |
| **§8-1 / §8-2** 卫生 | ○ **已修** | 仓库根已无 `_temp_check.bin`；`E3_fixes_summary.md` 已移入 `docs/` |

### (B) 新发现的其余缺陷

| 级别 | 问题 | 位置 |
|---|---|---|
| P1 | ○ **情绪 reasoning 把雪球口径硬标成「东财关注指数」并错误 `/1000`**：东财路径 `mention_count = 关注指数×1000`，雪球路径 `= total_attention` 原值；降级日 LLM 看到的是**被缩小 1000 倍且口径错标**的数字。新增的 `mention_source` 字段**无任何消费者**，本应用来消歧义 | `sentiment_analysis.py:1189` |
| P1 | ○ **`sentiment_check` 恒为 `missing`，composite 系统性 +5**（漏网）：`hot_sectors` 来自恒空的 `associated_sectors` ⇒ `_cross_check_sentiment` 恒返回 `"missing"`；旧代码返回 `"low"` 时非 ambush 板块会 `composite -= 5`，现该分支不再命中 ⇒ **D-P4 是一次未被声明的行为变更** | `aggregator.py` |
| P1 | ○ **测试污染生产归档（加重）**：现在 **8 条 return 全部落盘**（含 `S6_disabled` / `no_candidates`），而 `tests/test_node6_glue.py` 直接调 `_agent_semantic_glue` 且不 mock `_save_glue_result` ⇒ 夹具覆盖真实影子样本。**第一轮报告此条，本轮加重而非修复** | `aggregator.py` + `tests/` |
| P2 | ○ `sector_role_map.get(..., "mainline")` 的 fallback 方向是**放大风险**（主线全额预算 + 7% 止损），非保守侧 | `portfolio_decision.py:2858` |
| P2 | ○ **`archive.py:195-203` 两次 `os.rename` 之间崩溃会留下 `*.old`**；次日运行时该日目录不存在 ⇒ 跳过 `if archive_dir.exists()` 分支，但行尾**无条件 `shutil.rmtree(old_dir)`** 会删掉**唯一幸存的该日归档** | `archive.py` |
| P2 | ○ **双实现并存**：`archive.py:59 list_archive_dirs`（公共，含日期格式守卫）与 `persistence.py:215 _list_archive_dirs`（私有）功能重叠，`_SKIP_DIR_SUFFIXES` 也定义两份 ⇒ 后续易漂移 | 两文件 |
| P2 | ○ **EMA `daily_scores` 无界增长**：`compute_ema_stats_from_daily` 内部只截计算窗口（`max_days=60`），**截不到落盘** | `aggregator.py` |
| P2 | ○ **`family_keywords.py` 删了 `光刻` 却未建「设备」族** ⇒ `光刻机` 由旧 `AI硬件` **回归为「其他」**（与 §7 第 6 条同源：文档期望本身不可实现） | `family_keywords.py` |

### (C) 吞异常 / 死代码

- **空 `except`：无**（新增的 `except` 均有日志或降级）。新增 TODO/FIXME：无（diff 中唯一 `TODO` 字样是既有的 `RESERVOIR_PROMOTION_MIN_DAYS` 行）。
- **死代码**：`portfolio_decision.py` 的 `_extract_sector_from_reasoning`（无生产调用者）；`scripts/check_audit_claims.py:77 uses_extract_in_allocate`（赋值后从未使用）；`scripts/verify_glue_metrics.py:71 _has_save_call_in_path`（**恒返 `False` 且从不调用**）；`catalyst_config.py` 的 `AMBUSH_POOL_MIN_DAYS`（零消费者）。
- **无消费者的新增字段**：`HotStock.mention_source`、`GlueStrength.basis`、`TradeAllocation.sector_limit_detail`、`PortfolioDecision.sector_exposure_binding`。后两者是 D-P11/D-P12 主动要求的留痕（可接受），**前两者是口径声明字段，定义了却没接线**。

### (D) 对抗复核认为**第一轮报告说错**的条目

1. **§8-5 不成立**：第一轮称「新增 `raise` 使可降级失败改为中断」——当前代码**无任何 `raise`**，异常仅 warning 后继续，`failed_symbols` 被调用侧消费。
2. **§8-6 描述失焦**：第一轮称「`skipped_reason` 初始化后从未赋值」——全仓 grep 该字段 **0 命中**，它根本不存在；真实残留问题是「死闸仍被计 `evaluated`」。

### (E) 仍未覆盖

- `docs/agent_glue_plan_0903.md` 是否**还有其他行**同样写错口径 —— 已发现两行，**未做全表复核**
- **未做归档回放**：R-C3 / R-B3 / R-B8 / R1–R6 本应由回放或双跑证明，而 4 个脚本均为静态分析 ⇒ 原理上无法达成

---

## R2.8 全修轮（2026-09-11 夜，本报告作者实施的部分）

> 用户指令：**全修**。按风险分工 —— **两条行为变更**由本报告作者实施（本节）；
> 4 个验收脚本的真算改造、以及卫生/接线类缺陷，分别由两路 agent 并行处理（§R2.9 / §R2.10）。

| # | 项 | 改动 | 验证 |
|---|---|---|---|
| 1 | **`cooling` 角色错配**（§R2.5 #1） | `portfolio_decision.py`：`ROLE_MULTIPLIER` / `ROLE_STOP_LOSS` 从 `_assess_risk` 的**局部变量提升为模块级常量**（避免两处各写一份魔法值）；`_allocate` 的角色分支由 `if ambush / else mainline` 改为**按角色取乘数**，`branch` 取值扩为三角色 | ★ 运行时矩阵 |
| 2 | **D-P4 隐藏行为变更**（§R2.7 B 的 P1） | `aggregator.py:1314` 与 `portfolio_decision.py:832` 的罚分条件由 `== "low"` 改为 `in ("low", "missing")` —— 文档承诺「D-P4 **不改任何判据的分数**」，缺失态继续同档罚分才叫零变更 | ★ 实测 `low` 与 `missing` 同档（78.0） |
| 3 | **`archive.py` 中断残留**（§R2.7 B 的 P2） | 原子替换块前新增恢复：`if not archive_dir.exists() and old_dir.exists(): os.rename(old_dir, archive_dir)` + warning 日志。原实现下，两次 `rename` 之间崩溃会让该日归档**对 loader 彻底不可见**（`.old` 在 `_SKIP_DIR_SUFFIXES` 中被排除），而下一次运行还会**无条件删掉它** | 读码 + 语义推演 |
| 4 | **`_is_bar_complete` 降级日志**（§R2.5 #4） | `logger.debug` → `logger.warning`：该降级会把法定节假日误判为交易日，必须生产可见 | 读码 |

### 运行时矩阵（★ 决定性证据）

| 板块 | `sector_role` | `branch` | 止损价 | 仓位 | 修复前 |
|---|---|---|---|---|---|
| MLCC概念 | ambush | ambush | 9.50（−5%） | 440,000（×0.55） | 同 |
| 粮食概念 | mainline | mainline | 9.30（−7%） | 800,000（×1.0） | 同 |
| **旅游景点** | **cooling** | **cooling** | **9.50（−5%）** | **240,000（×0.3）** | ~~9.30 / 800,000，与主线完全相同~~ |
| None | mainline | mainline | 9.30 | 800,000 | 同 |

⇒ **`ROLE_MULTIPLIER["cooling"]=0.3` 首次真正生效**（此前 `sector_exposure_limits` 全仓无执行消费者，
该常量对任何仓位都没有影响）。

⚠️ **未采纳的更强读法**：`ROLE_MULTIPLIER` 的注释写「cooling 最低档：**防新买入**」。
字面读法是**冷却板块一律不买**（而非"给最小仓位"）。本次按**较弱读法**实现（×0.3 + 紧止损），
因为"不买"会改变每日可买集合、影响面更大。**若要按字面执行，需你确认。**

**回归**：GOT 14 个测试文件 **249 passed / 0 failed**；改动文件的未定义名扫描 **clean**。

---

## R2.9 卫生与接线类（7 项，已修 + 本报告作者独立复核）

> 由一路并行 agent 实施；下表「复核结果」为本报告作者**亲手复跑**的结论，非转述。

| # | 项 | 复核结果 |
|---|---|---|
| 1 | **测试污染生产归档** | ★ **实测**：跑污染源测试（`test_node6_glue` + `test_phase3_glue`，48 passed）前后，`data/got_daily/20260912/agent_glue.json` 的 mtime **逐位相同**。做法是把归档根目录提为可注入的 `GLUE_ARCHIVE_ROOT` + `conftest` autouse fixture 指向 `tmp_path` —— **在根上隔离而非逐个 mock**，故不会再有漏网 |
| 2 | 死代码清理 | ★ `_extract_sector_from_reasoning` / `_has_save_call_in_path` / `uses_extract_in_allocate` 在 `*.py` 中 **0 命中** |
| 3 | `AMBUSH_POOL_MIN_DAYS` 零消费者 | ✅ 已删。判定依据成立：接线需新增跨日 streak 状态与新阈值，与 D-P3「不改任何阈值」自限冲突；而"升格最短驻留"已有真正的消费者 `RESERVOIR_PROMOTION_MIN_DAYS` |
| 4 | EMA `daily_scores` 无界增长 | ✅ 新增 `EMA_MAX_DAYS=60`；**并验证了截断不改变计算结果**（100 日序列全量 vs 截断后复算完全相同） |
| 5 | `list_archive_dirs` 双实现 | ★ **实测三入口一致 37 == 37 == 37**；persistence 为唯一权威实现，`archive.py` 改为重导出 |
| 6 | `光刻机` 回归「其他」 | ★ `光刻机 → AI硬件`、`光刻胶 → AI硬件`，且 `航运概念 → 周期资源` **未回归** |
| 7 | 雪球口径错标 + `/1000` | ✅ 按 `mention_source` 分支渲染（东财 → `东财关注指数55.0`；雪球 → `雪球热度1234`，不再 /1000） |

## R2.10 验收脚本的真算改造（4 个脚本，已完成）

> 同样是并行 agent 实施 + **本报告作者逐个实跑**。四个脚本的**进程退出码**均由本报告作者亲手重量（下表括号内）。

| 脚本 | 实现的红线 | 实跑结果 | 退出码 |
|---|---|---|---|
| `verify_glue_metrics.py` | **R-C3** 真读 `<date>/agent_glue.json` + `unified.json` 逐项复算 | ★ **PASS 10/10 锚点逐位一致**（M1=3.1% / M1′=34.4% / M2=28.1% / M4-a=0.591P,0.419S / M4-b/c=0.646P,0.500S / M6=65.6% / M6′=96.9% / 回收率=90.9%） | **2** |
| 同上 | **R-C6** A/B 曲线 | **SKIP**（回填样本 2 天，需 ≥8）；**未做任何静态替代** | — |
| `verify_layer_b_consistency.py` | R-B3 / R-B8 / R-B9 | **SKIP ×3**（需"修复前后各跑一遍"的两组归档，本仓库无该产物）；旧 AST 检查已**降级为「辅助区」并标注"不是红线、不参与退出码裁决"** | **2** |
| `check_audit_claims.py` | **R-D1** 15 条声明逐条真复算 | **FAIL**：`unverified = 9`（基线 10） | **1** |
| 同上 | **R-D2** 四个子计数与文档逐位比对 | **FAIL**：4 项中 2 项不一致 | — |
| `verify_same_day_idempotency.py` | R1/R3/R4 同日两跑 diff | 无参 **SKIP**；`--runs` 冒烟 **PASS**（比对 474 字段、差异 0） | **1** |
| 同上 | R2 / R5 | **FAIL ×2**（见 §R2.11） | — |

### ★ 唯一的口径陷阱已被显式处理

文档锚点 `M4-a=0.419S` / `M4-b/c=0.500S` **只在 `rank(method="first")`（按出现顺序编号）下复现**；
统计学标准（`scipy.stats.spearmanr` / 平均秩）给出 **0.478 / 0.564**，差 0.06–0.14 —— 足以让"逐位一致"判定翻盘。
脚本**同时打印两个值并声明依赖输入顺序**，未静默取舍。这正是 R2.3 要求的行为。

## R2.11 脚本跑通后浮出的真实 FAIL（新发现，非脚本能力问题）

| # | 红线 | 实算 | 定性 |
|---|---|---|---|
| 1 | **R-D1** `unverified = 0` | **9**（P0-1、P0-2、P0-5、P1-1、P1-3、P1-4、P1-5、P2-1、P2-3 在归档中查不到生效证据） | 0902 报告的 9 条声明**仍不可复算** |
| 2 | **R-D2** 子计数逐位一致 | `ambush_pool_saturation_days` 实算 **5** / 文档 **≥10**；`dead_signal_count` 实算 **3** / 文档 **4** | 前者文档自注"缺口见 D-P2 落盘"（数据源未落盘 ⇒ 基线不可复现）；后者因 E10 已按 D-P14 删除，**文档基线早于修复** |
| 3 | **R5** `signal_dates` 同 (type, date) ≤1 条 | ★ **本报告作者独立复算：7 组重复**（`08-28×2`、`08-31×5`、`09-01×8`、`09-02×9`、`09-03×2`、`09-04×3`、`09-10×2`），与脚本输出**逐一吻合** | 重复全部落在 **09-10 之前**；`A-P0-4` 的按日去重只影响**新增 append**，故这是**历史遗留数据**，不能据此判定 A-P0-4 未修。⚠️ 但**缺 09-11 之后的触发样本**，故 A-P0-4 的实际效果**本次未能验证** |
| 4 | **R2** `previous_regime != regime` 的归档日 ≥1 天 | ★ **本报告作者独立复算：37 个归档日中 0 天**（8 天无字段 / 18 天 `prev=null` / 11 天 `prev==regime`） | ⚠️ **该红线本身无效，不是代码缺陷** —— A 层 §2.3 撤销表第 4 条已证：`prev == regime` 是状态机的**结构性产物**（情形 1/3 都写 `previous_regime = prev_regime`），只有情形 2「切换确认」才会不等。⇒ R2 判据实际是「**等市场发生一次 regime 切换**」，**工程上不可满足**，无法用于验收 A 层修复 |

### ⚠️ R2 这条红线的问题值得单列

「出现 `previous_regime != regime` 的归档日 ≥1 天」把**市场事件**当成了**代码验收条件**。
37 天零切换可能只是市场没切换，也可能是情形 2 不可达 —— **两种解释都无法由这条红线区分**。
⇒ 建议把 R2 改为可工程判定的形式（如：构造一次 `transition_pending` 并验证次日确认路径），
否则它会永远以 FAIL 或 SKIP 状态存在，且**不携带任何关于修复是否生效的信息**。

## R2.12 本轮新查出并已修：3 个验收脚本被 `.gitignore` 吞掉

`.gitignore:76` 的老规则 `verify_*.py`（2026-08 为排除一次性排查脚本而设）**同时命中**了
`verify_glue_metrics.py` / `verify_layer_b_consistency.py` / `verify_same_day_idempotency.py`
⇒ 三个脚本**在磁盘上存在、却不属于仓库**：`git status` 看起来干净，提交时会**静默漏掉**，
别人 clone 后红线依然没有载体。**这是"交付物不在仓库里"的第三次复发，也是最隐蔽的一次。**

**已修**：加 4 条负向规则（含被依赖的 `verify_common.py`），5 个脚本现已全部纳入版本控制。

## R2.13 仍未完成 / 待决策

| # | 项 | 性质 |
|---|---|---|
| 1 | `sector_role_map` 的 fallback 是 `"mainline"`（**放大风险**而非保守侧） | 策略决策：改它会改变"角色未知"时的仓位方向 |
| 2 | `GlueStrength.basis` 无生产者；`HotStock.mention_source` 的 `nga_mention` 值无生产者 | 口径声明类，接线需先定语义 |
| 3 | **`A-P0-4`（monitoring 按日去重）的实际效果未验证** | 缺 09-11 之后的 `range_reversal` 触发样本；需等下一次触发 |
| 4 | ~~全量测试未跑完~~ → ✅ **已完成（无回归）** | **22 failed / 3628 passed / 2 skipped**，与全修轮**之前完全同一批**（`test_main_schedule_mode` 9 / `test_system_config_service` 7 / `test_static_assets_consistency` 3 / `yfinance` 1 / `stock_index` 1 / `fetcher_source` 1）。**GOT 相关失败 = 0** ⇒ **全修轮未引入回归**。根因是 pytest capture 的 `ValueError: I/O operation on closed file`（测试自己 `handler.close()` 关掉捕获流），属 pre-existing 环境问题 | ✅ |
