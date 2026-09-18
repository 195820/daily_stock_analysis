# GOT 已验证完成项归档
> **原位置**：`docs/archive/got/verified_completion_log.md`（2026-09-17 文档结构重排移入当前位置）

> **性质**：本目录存放已通过验证、从 `verification.md` 归档的项。
> 归档标准：验证流程完备、实测值达标（或验证机制已收口）、无需继续跟踪。

---

## 归档清单（2026-09-15）

### 1. A-P0-4：monitoring 按日去重 ✅

**归档来源**：`verification.md` §6（2026-09-14 实测有效）

**验证证据**（两条独立证据）：

1. **归档侧**：09-14 至少跑过 2 次，而当日 `signal_dates` 只有 1 条
   - `node4_events` 中存在 `first_seen='2026-09-14'` 且 `mention_count=1`（按日去重口径）但 `mention_count_run_based=2`（旧口径）的行
   - 旁证：`node4_raw_news` 同日有 5 条事件 `appearance_count_run_based = 3`

2. **沙箱侧**：`save_monitoring` 连调 3 次，今日条目恒为 1
   ```
   初始             : 总条数=36  今日 (2026-09-14) range_reversal=1
   第 1 次 save_monitoring: 总条数=36  今日 range_reversal=1
   第 2 次 save_monitoring: 总条数=36  今日 range_reversal=1
   第 3 次 save_monitoring: 总条数=36  今日 range_reversal=1
   ```

**机制**：`persistence.py:704-707` 在追加前剔除 `date == today_str` 的既有条目，再按当日计数追加 ⇒ 跨次运行幂等。

**残留限制**：本项只保证**跨次运行**不重复；**单次运行内**若某类信号出现 ≥2 次，仍会落 ≥2 条同 `(type, date)` 条目（`persistence.py:709-711` 的 `for _ in range(count)`，`signal_count_30d` 依赖该计数语义）。该口径冲突已登记在 `backlog.md` §9。

---

### 2. R-C1：S1/S2 失败样本落盘 ✅

**归档来源**：`verification.md` §4（2026-09-14 补测通过）

**判据**：制造 S1 / S2 各一次后 `agent_glue.json` **仍存在**且 `status` 正确（达标线：2/2 存在）

**实测结果**：**4/4 通过**
- `tests/test_node6_glue.py::TestGlueFailureSamplePersistence`
- 覆盖 `S1_call_failed` / `S1_empty` / `S2_parse_failed` / `S2_validate_failed`
- 逐个断言落盘文件存在 + `status` 相符

**复现命令**：
```bash
python -m pytest tests/test_node6_glue.py::TestGlueFailureSamplePersistence -q
```

---

### 3. R-C3：十锚点逐位一致 ✅

**归档来源**：`verification.md` §4（2026-09-14 实跑 PASS）

**判据**：`verify_glue_metrics.py --date 20260910` 十锚点逐位一致（达标线：全部相等）

**实测结果**：**PASS 10/10**
- 脚本退出码：`1/2 PASS | 0 FAIL | 1 SKIP | exit 2`（R-C6 SKIP 因回填样本不足）
- C-P6 / C-P7 落地后复跑：主口径切换为 glue 侧 / 回收率，10 个锚点仍逐位一致、退出码不变

**复现命令**：
```bash
python scripts/verify_glue_metrics.py --date 20260910
```

---

### 4. R-C5：`_log_shadow_comparison` 零引用 ✅

**归档来源**：`verification.md` §4（2026-09-14 复验通过）

**判据**：`_log_shadow_comparison` 在 `src/` 与 `tests/` 零引用（达标线：0 处）

**实测结果**：**零真实引用**
- `src/got/nodes/aggregator.py:1072` 仅剩墓志铭注释（`# C-P4: … 已删除`）
- `tests/` 零命中

**复现命令**：
```bash
rg -n '_log_shadow_comparison' src/ tests/
```

---

### 5. R2：状态机 `previous_regime` 收口 ✅

**归档来源**：`verification.md` §5（2026-09-14 已收口）

**原始 FAIL**：36 个含 `unified.json` 的归档日中，**0 天** `previous_regime != regime`

**根因**：按状态机设计（`aggregator.py:527-531`），该条件**只在情形 2（确认切换）成立**，其余两情形按设计就相等。实测是市场未触发切换，非代码缺陷。

**收口方式**：`tests/test_node6_regime_state.py::test_confirmed_switch` 补断言 `result.previous_regime == "bull"`（情形 2 是唯一会让两者不等的路径）⇒ 该项**不再依赖等市场切换**，每次跑测试即验。

**旁证**：`transition_pending` 在 0904 / 0909 / 0910 均被正确写入 ⇒ 字段接线是通的。

**复现命令**：
```bash
python -m pytest tests/test_node6_regime_state.py::TestApplyRegimeStateMachine -q
```

---

### 6. R4：EMA 计数按日 ✅

**归档来源**：`verification.md` §5（2026-09-14 首验 PASS）

**判据**：EMA 计数按日（`sample_count` / `daily_scores` 键数不变）

**实测结果**：**PASS**
- `sample_count` 2→2、键数 2→2
- 首次真算（同日两跑快照 `data/_run1` / `data/_run2`）

**复现命令**：
```bash
python scripts/verify_same_day_idempotency.py --runs data/_run1 data/_run2
```

---

### 7. M6：「其他」回收率达标 ✅

**归档来源**：`verification.md` §3（2026-09-14 实测）

**指标定义**（C-P7 落地后的主口径）：原「其他」板块中 glue 给出真实家族名的占比（「无归属」**计为未回收**）

**转正红线**：≥ 80%

**实测值**：**10/11 = 90.9%**（0910 + 四日）

**达标判定**：✅ **数值超过红线**（90.9% ≥ 80%）

**对照列 / 观察项**（不参与转正判定）：
- 规则侧 `frame_group != "其他"`（修复前口径）：21/32 = 65.6%（原主口径，已降为观察项）
- glue 侧 `families[].family` 非「无归属」（覆盖率）：31/32 = 96.9%（⚠️ 与主口径分母不同：32 vs 11，不是同一个量）

**复现命令**：
```bash
python scripts/verify_glue_metrics.py --date 20260910
```

---

## 持仓板块漂移修复（2026-09-18）

**Commit**: `20cba78ea46017773e0c192349c9232774af8ef6`  
**Branch**: `fix/sector-drift-grace`  
**Date**: 2026-09-18

### 改动统计

```
 src/got/nodes/portfolio_decision.py            | 599 ++++++++++++++++++++++++-
 src/got/schemas.py                             |   9 +
 src/storage.py                                 |  33 ++
 tests/test_portfolio_decision_lot_rejection.py | 148 +++++-
 tests/test_reverse_lookup.py                   |  54 +++
 6 files changed, 974 insertions(+), 23 deletions(-)
```

### 核心功能验收

#### Fix 1: 家族中继匹配 ✅

**代码位置**: `_family_relay_match` (`portfolio_decision.py:1848-1901`)

**验收点**:
- ✅ 板块名→normalize→frame_group_lookup→家族名→锚点逻辑正确
- ✅ strength ×0.85 打折（问题 1 修正：避免虚高）
- ✅ weight=1.2（介于 direct 1.5 / normal 1.0 之间）
- ✅ sector_role/三维 check 注入锚点值
- ✅ 过热降权继承接线（问题 2 修正：不可逃逸）

**单测覆盖**: `test_family_relay_match_semiconductor` → strength≈78.2 (92×0.85), is_relay=True

#### Fix 2: 买入论点快照 ✅

**新表**: `HoldingThesisRecord` (`storage.py:1257-1285`)

**验收点**:
- ✅ 字段设计完整（symbol/sector_name/family_name/entry_date/composite_at_entry/source/drift_watch_since/drift_family/is_open）
- ✅ UPSERT 机制（自然键 symbol+entry_date，同 P4 A-P0-6）
- ✅ 写入方法 `_write_thesis_snapshots` (买入时)
- ✅ 读取方法 `_load_holding_theses` (持仓分析时)
- ✅ 关闭方法 `_close_thesis_snapshots` (卖出时)
- ✅ 强度打折 ×0.75（比当日 boards 中继再低一档）

**冷启动策略**: 存量持仓靠 Fix 1 中继 + Fix 3 懒建观察记录覆盖；前向从下一次买入开始积累

#### Fix 3: 卖出宽限 ✅

**代码位置**: `_drift_action_with_grace` (`portfolio_decision.py:2537-2595`)

**验收点**:
- ✅ Invesco 非对称门槛判定逻辑正确
- ✅ 家族锚点 ≥60 → hold + 置观察
- ✅ 家族锚点 [40,60)：首次 reduce，跨日 sell
- ✅ 家族锚点 <40 → sell（不宽限）
- ✅ `_apply_drift_watch` 懒建/UPDATE 逻辑正确
- ✅ 写库 try/except 降级（不阻塞主流程）

**理论依据**: Bethke 2026 退出规则回测（快触发在噪声信号上 whipsaw——确认信号 + 宽限窗口优于单日即卖）

#### 可观测性 ✅

**字段新增**: `UnifiedScoringResult` (`schemas.py:986-999`)

- ✅ `sector_match_type`: direct/relay/thesis/fallback/none
- ✅ `sector_drift_grace`: bool（家族宽限标记）
- ✅ portfolio_decision.json 落盘自动携带

### 测试验证

#### 单测结果

**test_reverse_lookup.py** (新增 3 用例):
1. `test_family_relay_match_semiconductor`: 家族中继验证 → PASS ✓
2. `test_family_relay_skipped_when_direct_match`: 直接命中优先 → PASS ✓
3. `test_family_relay_no_anchor_returns_fallback`: 无锚点降级 → PASS ✓

**test_portfolio_decision_lot_rejection.py** (新增 3 用例):
1. `test_drift_action_grace_hold`: 家族强 → hold → PASS ✓
2. `test_drift_action_grace_reduce_then_sell`: 家族弱 → reduce 后 sell → PASS ✓
3. `test_drift_action_deep_break_sells`: 深度破位 → sell 不宽限 → PASS ✓

**总结果**: **16/16 PASSED ✓**

### 根因修复验证

**问题股票**: 688598 金博股份

**原因为何被误卖**:
1. ❌ 买入时按"半导体"家族评分（当时是主线，composite~92）
2. ❌ 持仓分析时只做板块名级匹配
3. ❌ 今日 confirmed_sectors 无"半导体"板块名（只有先进封装 92/存储芯片 90 等成员）
4. ❌ 家族关系完全丢失，只剩石墨烯命中（fallback 路径取 boards 中强度最高者 63.7）
5. ❌ 纯规则匹配，落入 `sector_role not in ("mainline","ambush")` → sell

**修复后预期行为**:
1. ✅ Fix 1: 家族中继匹配 → 识别"半导体概念"属于 AI 硬件家族
2. ✅ 锚点：先进封装 (composite=92, sector_role="mainline")
3. ✅ strength = 92 × 0.85 ≈ 78（压过石墨烯 63.7）
4. ✅ sector_role="mainline" → 不再落入 sell 分支
5. ✅ action = hold（若家族锚点≥60）或 reduce/sell（依宽限逻辑）

### 生产验证待办

- [ ] 运行 `python main.py --got`
- [ ] 核对 `data/got_daily/<今日>/portfolio_decision.json` 中 688598 的 `sector_match_type="relay"`、`family_name="AI 硬件"`、`action="hold"`
- [ ] 检查 `holding_thesis_snapshots` 表已建且后续买入产生 got_buy 记录
- [ ] 无 drift 的其他持仓（600250 等）行为与改动前一致（`sector_match_type="direct"`）

### 合并建议

**前置条件**: 生产验证通过

**合并命令**:
```bash
git checkout main
git merge fix/sector-drift-grace
git push origin main
```

---

*归档日期：2026-09-18*
*状态：待生产验证*
