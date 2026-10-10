# Ultra Review 审查报告：批次5 落地 + P1 小尾巴

> 状态（2026-10-10 文档整理补）：**已收口——全部 Critical/Warning/Suggestion 修复均已落地**（见 [`backlog.md`](../backlog.md) §5 尾注与 [`docs/CHANGELOG.md`](../../CHANGELOG.md) 2026-09-29 收尾条目）。本文件仅作审查留痕，**不再更新、不得当作待办清单**；原位置为仓库根目录（未入库），现收入 GOT 归档区。

> **审查日期**: 2026-09-28
> **审查对象**: `git diff 3d71945^..HEAD`（两个提交，15 文件，+1340/-17）
> - `3d71945` GOT 批次5（后续排期）落地：5b 探测 / 4b 板块排名 / 3b 分歧摘要 / 4d 前置（全部影子态）
> - `ae432ab` fix(got): 补 backlog §2 两个小尾巴（P1-1 持仓名兜底 / P1-3 核实已落地）
>
> **审查方法**: ultra-review 三视角（Completeness / Correctness / Impact）并行子代理 + 主代理串行补齐与交叉验证
> **视角覆盖**: Completeness ✅ 子代理完整交付 · Correctness ✅ 子代理中止后主代理串行补齐（逐函数审读 + symtable/运行时复现 + 34 例新测试实跑）· Impact ✅ 主代理串行补齐（shadow 门控隔离、契约漂移、热路径开销、全局状态）

---

## 结论速览

| 严重度 | 数量 | 编号 |
|---|---|---|
| 🔴 Critical (MUST FIX) | 1 | #1 |
| 🟡 Warnings (SHOULD FIX) | 2 | #2 #3 |
| 🔵 Suggestions (CONSIDER) | 3 | #4 #5 #6 |

**一句话结论**: 批次 5 的 3b/4b 影子观测因 `aggregator.py` 缺模块级 `import os` 而从未真正执行过——每次生产 `python main.py --got` 跑到 Node 6 聚合即 NameError 崩溃；且现有测试结构（全静态方法级）无法捕获此类整函数级回归。

---

## 🔴 Critical (MUST FIX)

### #1 `aggregator.run()` 每次必崩：裸 `os.getenv` 无模块级 `import os`，3b/4b 影子观测从未执行

**位置**: `src/got/nodes/aggregator.py#L326`、`src/got/nodes/aggregator.py#L397`

**Problem**: 批次 5 在 `run()` 内新增的三态开关读取：

```python
# L326
_dg_shadow_on = os.getenv("GOT_DISAGREEMENT_SHADOW", "1") == "1"
# L397
_mt_shadow_on = os.getenv("GOT_MARKET_THEME_SHADOW", "1") == "1"
```

两处均执行于 `try` 块**之外**，且默认值 `"1"` = 影子默认开 = **每次生产运行必然走到**。但该文件**没有模块级 `import os`**：

- 顶部 import 块（L15-89）无 `os`
- L215/L345 的 `import os as _os` 是函数局部绑定且别名是 `_os` 不是 `os`
- L349 的绑定位于 L326 之后，且同样不提供 `os` 名字

**证据**（主代理独立复现，非子代理单方声明）:

1. `symtable` 静态分析：`run()` 内 `os` 按 global 解析（`is_global=True, is_local=False`），而模块 globals 无 `os`（`hasattr(aggregator_module, 'os') == False`）→ 运行时 NameError **确定性崩溃**
2. 运行时复现（conda env，stub 后调 `run(replay_mode=True)`）：`NAMEERROR REPRODUCED: name 'os' is not defined`
3. 对照组：`portfolio_decision.py` L20 有模块级 `import os`，仅 aggregator 侧坏

**测试为何没抓到**（逃逸根因）: 25 个新测试全部只调静态/辅助方法（`_apply_regime_state_machine`、`_build_theme_shadow_dict`、`_theme_confidence_factor` 等），**全仓库无任何测试走 `aggregator.run()` 主路径** → "node6 域 86 passed" 为真，但从未覆盖崩溃点。

**后果**:
- 每次 `python main.py --got` 在 Node 6 聚合即崩，GOT 全流程不可用
- commit 核心交付物（`conflict_summary_shadow` / `market_theme_shadow` / 归档"市场结构"段）实为死代码
- "零决策改动、回归 PASS"的验收声明对该路径不成立

**Fix**（一行）:

```python
# src/got/nodes/aggregator.py 顶部 import 块（L15-21 处）
import os
```

---

## 🟡 Warnings (SHOULD FIX)

### #2 影子路径零 `run()` 级测试，回归网存在结构性盲区

**位置**: `tests/test_disagreement.py`、`tests/test_market_theme_step2.py`

**Problem**: 发现 #1 能合入并通过全部域测试，证明现有测试结构上无法捕获"整函数级"回归。同样的盲区会让下一个 `run()` 内的改动再次无声逃逸。

**Fix**: 补一个 `run()` 级冒烟测试（构造 5 个最小 context + `replay_mode=True`），断言能产出 `UnifiedContext` 且 `conflict_summary_shadow` 非空。一次断言堵住此类逃逸。

### #3 `_classify_phase` 的 `unknown` 分支不可达，`unknown_count` 恒为 0

**位置**: `src/got/market_theme.py#L119-L136`（`_classify_phase`）

**Problem**: 分支逻辑为 `三时框全>0→accelerating`、`c5>0→warming`、`c5<=0→cooling`，穷尽了所有输入 → 末尾 `return "unknown"` 是死代码。`ThemeBreadth.unknown_count` 永远为 0；"unknown: 无有效数据"的语义实际由空列表路径（`total_count=0`）承担。文档字符串承诺的四态只有三态可达。

**Fix**: 删除死分支。"无数据"语义已由 `MarketThemeContext.unknown()` 兜底路径（`build_theme_context` 空 quick/异常分支）正确承担。

---

## 🔵 Suggestions (CONSIDER)

### #4 funnel_stats 缺 Round 2 出口

**位置**: `src/services/stock_screener.py#L1543-L1653`

**Problem**: commit 声称"5 轮漏斗归零率观测"，但 trace 键为 `r0_in/r0/r1/r1_5/r3/r4/final_out`，无 `r2`（资金面 Top 20，L1616-1644 无赋值）。归零率画像缺一轮，D-3 基线重标时口径不完整。

**Fix**: Round 2 收口处补 `funnel_trace["r2"] = len(candidates)`。

### #5 4b 影子取数落在主流程关键路径，每日常态开销

**位置**: `src/got/orchestrator.py#L100-L111`

**Problem**: Step 2.5 在影子默认开（`GOT_MARKET_THEME_SHADOW` 默认 `"1"`）下对全部概念+行业板块逐板拉 11 根 K 线（`get_board_quick_change`，mootdx TCP，板内并发 20），`await` 串行于两路完成之后、Node 6 之前。TTL 缓存 60s 不跨进程存活，每日运行全额付费。属设计意图（"每日真跑落观测"），但已 ~25 分钟的运行会再增加数十秒量级，且失败仅 warning 不留耗时指标。

**Fix**: monitoring.json 里记一条 4b 取数耗时，影子期顺便评估成本；无需改默认行为。

### #6 测试计数与账本不符（良性）

**位置**: `tests/test_disagreement.py`

**Problem**: 实际 14 例，commit 与 backlog 写 13 例。多 1 例属超交付，仅账本数字需对齐。

**Fix**: backlog §5 对应行 13→14。

---

## 审查中排除的误报（审计留痕）

| 声称 | 判定 | 依据 |
|---|---|---|
| 4b 接线断裂：orchestrator 丢弃 `build_theme_context` 返回值导致 shadow 恒 None | **不成立** | 接线走模块级全局变量：`_cache_set` → `global _default_context`（market_theme.py L165-166），aggregator 经 `get_market_theme_context()` 只读消费（该函数注释明说"只读，不触发取数"）。链路完整，唯一阻断是 #1 的 NameError |
| P1-1 兜底污染持仓计算 | **不成立** | `_resolve_holding_name` 仅用于展示/归档字段，两处消费点（L567/L598）均无计算副作用；异常全部吞掉回退 symbol，符合"兜底不引入新异常路径" |
| 3b 影子改 LLM 仲裁输入 | **不成立** | `decision_path_hint` 仅在 `GOT_DISAGREEMENT_ENABLED=1`（默认 `"0"`）时注入 prompt；shadow 走空串分支，prompt 逐字节不变 |
| schemas.py 引入循环导入 | **不成立** | `market_theme` 只依赖 pydantic，不反向 import schemas |
| `build_opinions` 字段名错配 | **不成立** | 逐一核对 schema：`cross_index_consistency`(L674)、`fear_greed`(L679)、`institutional_score`(L677)、`active_mainline`(L378)、`classification="actionable"`(L754) 全部存在且类型匹配 |
| 共识分制往返丢信号 | **不成立** | `normalize_decision_signal` 把 strong_sell 归一为 sell（输入侧收敛），`_SCORE_TO_SIGNAL` 输出侧允许 strong_sell（细化）；输入归一/输出细化，无信号丢失 |

---

## 其余核验结论（无发现）

- **影子隔离目标达成**（除 #1 外）：4b `overall_confidence` 仅 enabled 态修改；3b veto 仅 enabled 态触发 `_ro_veto_buy`，与 `over_limit_forced` 分支并列、旧路径逐字节不变
- **34 个新测试实跑全过**（4.82s，`pytest tests/test_disagreement.py tests/test_market_theme_step2.py tests/test_holding_name_fallback.py tests/test_got_funnel_stats.py`）
- **异常降级路径完整**：funnel_stats / risk_override_shadow / 4b 构造失败均 try-except 降级，不阻塞主流程
- **归档兼容**：archive"市场结构"段仅在 `market_theme_shadow` 非 None 时渲染，不破坏既有 reasoning.md 消费方
- **纯新增契约**：`ScreenerRunResult.funnel_stats` 为新增字段，输出 dict 附加键无下游破坏
- **TickFlow 探测 / 79.push2 取证**：一次性外部脚本产物，仅以 docs 落 verification.md §7/§7.1，无可核验代码（按 docs-only 交付接受）

---

## Summary of Changes

- **审查对象**: 批次 5 影子态落地（5b/4b/3b/4d）+ P1-1/P1-3 小尾巴，15 文件
- **1 Critical**: `aggregator.py` 缺顶层 `import os`，默认影子开关使 `run()` 每次必崩——3b/4b 影子观测从未真正执行，"回归 PASS"声明未覆盖主路径
- **逃逸根因**: 25 个新测试全为静态方法级，仓库无 `run()` 级测试 → 补冒烟测试为第一优先修复
- **排除 1 项外部误报**（4b 接线断裂——实为全局变量接线，链路完整）；新模块（disagreement/market_theme）逐函数审读未发现逻辑错误
- **修复顺序建议**: 一行加 `import os`（#1）→ `run()` 冒烟测试（#2）→ 删 `_classify_phase` 死分支（#3）→ 可选补 r2 trace（#4）+ 账本对齐（#6）
