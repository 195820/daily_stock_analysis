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
   初始             : 总条数=36  今日(2026-09-14) range_reversal=1
   第1次 save_monitoring: 总条数=36  今日 range_reversal=1
   第2次 save_monitoring: 总条数=36  今日 range_reversal=1
   第3次 save_monitoring: 总条数=36  今日 range_reversal=1
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

## 归档说明

1. **归档日期**：2026-09-15
2. **归档原因**：上述项验证流程完备、实测值达标（或验证机制已收口）、无需在待验证文档中继续跟踪
3. **待验证文档**：`docs/verification.md` 已移除上述项，仅保留真正待验证的内容
4. **配套文档**：
   - [`verification.md`](../verification.md) — 待验证 / 影子期清单
   - [`backlog.md`](../backlog.md) — 待实施清单
   - [`README.md`](./README.md) — 归档索引

---

*归档日期：2026-09-15*
