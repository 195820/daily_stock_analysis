# Fix: 持仓板块漂移误卖修复 - 提交总结

## Commit 信息

**Commit**: `cc3c9f2`  
**Branch**: `fix/sector-drift-grace`  
**Date**: 2026-09-18

## 改动统计

```
 src/got/nodes/portfolio_decision.py            | 599 ++++++++++++++++++++++++-
 src/got/schemas.py                             |   9 +
 src/storage.py                                 |  33 ++
 tests/test_portfolio_decision_lot_rejection.py | 148 +++++-
 tests/test_reverse_lookup.py                   |  54 +++
 5 files changed, 820 insertions(+), 23 deletions(-)
```

## 核心功能

### Fix 1: 家族中继匹配（问题 1+2 修复）

**文件**: `src/got/schemas.py`, `src/got/nodes/portfolio_decision.py`

- 新增字段：
  - `sector_match_type`: direct / relay / thesis / fallback / none
  - `sector_drift_grace`: 家族宽限标记

- 新增方法：
  - `_build_family_anchor_map()`: 构建家族→锚点映射
  - `_family_relay_match()`: 家族中继匹配逻辑
  
- 关键修复：
  - **问题 1**: strength ×0.85 打折，避免虚高（92→78）
  - **问题 2**: 过热降权用 `relay_anchor_sector` 查惩罚，不可逃逸

### Fix 2: 买入论点快照

**文件**: `src/storage.py`, `src/got/nodes/portfolio_decision.py`

- 新表：`holding_thesis_snapshots`
  - symbol, sector_name, family_name, entry_date, entry_price
  - source (got_buy/drift_watch), drift_watch_since, drift_family
  
- 新增方法：
  - `_write_thesis_snapshots()`: 买入时写入
  - `_load_holding_theses()`: 读取 open 记录
  - `_close_thesis_snapshots()`: 卖出时关闭
  - `_find_stock_sector_with_thesis()`: fallback 路径消费论点

- 强度打折：×0.75（比当日 boards 中继再低一档）

### Fix 3: 卖出宽限

**文件**: `src/got/nodes/portfolio_decision.py`

- 核心方法：
  - `_drift_action_with_grace()`: Invesco 非对称门槛判定
  - `_apply_drift_watch()`: 漂移观察持久化

- 判定逻辑：
  - 家族锚点 >=60 → hold + 置观察
  - 家族锚点 [40,60)：首次 reduce，跨日 sell
  - 家族锚点 <40 或无家族 → sell（不宽限）

## 单测覆盖

### test_reverse_lookup.py（新增 3 个用例）

1. `test_family_relay_match_semiconductor`: 家族中继验证
   - 预期：strength≈78.2 (92×0.85), is_relay=True
   
2. `test_family_relay_skipped_when_direct_match`: 直接命中优先
   - 预期：不走中继，is_relay=False
   
3. `test_family_relay_no_anchor_returns_fallback`: 无锚点降级
   - 预期：返回 fallback

### test_portfolio_decision_lot_rejection.py（新增 3 个用例）

1. `test_drift_action_grace_hold`: 家族强 → hold
2. `test_drift_action_grace_reduce_then_sell`: 家族弱 → reduce 后 sell
3. `test_drift_action_deep_break_sells`: 深度破位 → sell 不宽限

**测试结果**: 16/16 PASSED ✓

## 修复根因

**问题股票**: 688598 金博股份

**原因为何被误卖**：
1. 买入时按"半导体"家族评分（当时是主线，composite~92）
2. 持仓分析时 `_reverse_lookup_stock_sector` 只做板块名级匹配
3. 今日 confirmed_sectors 无"半导体"板块名（只有先进封装 92/存储芯片 90 等成员）
4. 家族关系完全丢失，只剩石墨烯命中（fallback 路径取 boards 中强度最高者 63.7）
5. 纯规则匹配，落入 `sector_role not in ("mainline","ambush")` → sell

**修复方案**：
- Fix 1: 家族中继匹配（板块名→normalize→frame_group_lookup→家族名→锚点）
- Fix 2: 论点快照（买入时记录家族锚点，漂移时可回溯）
- Fix 3: 卖出宽限（Invesco 非对称门槛，保护强家族中的漂移股）

## 预期效果

运行 `python main.py --got` 后，`data/got_daily/<当日>/portfolio_decision.json` 中 688598：

```json
{
  "symbol": "688598",
  "sector_name": "半导体",           // 家族中继命中（而非石墨烯）
  "sector_match_type": "relay",      // Fix 1 生效
  "family_name": "AI 硬件",           // 家族名
  "composite_score": ~65-75,         // 综合评分合理
  "dimension_scores": {
    "sector": ~70-80                 // 板块维度 ≈78×过热系数（而非 33）
  },
  "action_recommendation": "hold",   // 不再是 sell
  "sector_drift_grace": true         // Fix 3 生效（若触发宽限）
}
```

## 回滚方案

如需回滚此 commit：

```bash
git revert cc3c9f2
```

注意：
- 会删除 `holding_thesis_snapshots` 表结构
- 恢复原有板块匹配逻辑
- 移除所有宽限相关代码

## 后续建议

1. **生产验证**: 运行完整流程生成新数据，核对 688598 输出
2. **监控指标**: 
   - `sector_match_type` 分布（relay 占比）
   - `sector_drift_grace` 触发频率
   - 漂移股实际表现 vs 误卖股对比
3. **参数调优**: 
   - strength 折扣系数 0.85/0.75 可根据回测调整
   - 宽限门槛 60/40 可参考历史数据优化
4. **扩展场景**: 
   - 考虑多家族归属的加权处理
   - 论点快照冷启动期的特殊逻辑

---

**Author**: Developer  
**Reviewers**: TBD  
**Status**: Ready for Review & Merge
