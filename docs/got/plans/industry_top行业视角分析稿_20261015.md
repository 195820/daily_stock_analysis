# industry_top 行业视角分析稿

> **状态**：分析已落稿，待拍板  
> **日期**：2026-10-15  
> **背景**：backlog §7 #15「`industry_top` 行业视角如何进决策面（待分析）」  
> **产出锚点**：`sector_analysis.py:3800` `_fetch_and_score_industry_boards`（mootdx 881xxx + 4 维百分位 → Top10）  
> **消费锚点**：仅生产者自身文案 + `scripts/calibrate_optimal_stop.py:116` GATE-2 扫表（被主线判据过滤，零贡献）

---

## 1. 实测统计（56 归档日样本）

### 1.1 基础统计

| 指标 | 数值 |
|------|------|
| 总归档日 | 56 |
| 有 industry_top 数据 | 46 / 56（82%） |
| industry_top 数量 | 恒为 10（设计值） |
| state 取值集合 | **{neutral}**（恒值） |
| tier 取值集合 | **{None}**（恒空） |

### 1.2 strength_score 分布

| 分桶 | 计数 | 占比 |
|------|------|------|
| 98-100 | 68 | 14.8% |
| 95-97 | 119 | 25.9% |
| 90-94 | 197 | 42.8% |
| 85-89 | 73 | 15.9% |
| <85 | 3 | 0.7% |
| **合计** | **460** | **100%** |

- **均值**：93.74
- **中位数**：93.90
- **范围**：84.40 - 100.00

**结论**：score 集中在 90-97 区间（68.7%），区分度有限；极端高分（≥98）占比 14.8%，可作为「强势行业」信号。

### 1.3 与主线板块重叠率

#### 口径一：与 `confirmed_sectors`（role==mainline）重叠

| 匹配方式 | 均值 | 最小值 | 最大值 |
|----------|------|--------|--------|
| 精确匹配 | 6.3% | 0% | 30% |
| 子串匹配 | 12.2% | 0% | 40% |

#### 口径二：与 `active_mainline` 重叠

| 匹配方式 | 均值 | 最小值 | 最大值 |
|----------|------|--------|--------|
| 精确匹配 | 6.7% | 0% | 30% |
| 子串匹配 | 12.6% | 0% | 40% |

**重叠率解读**：
- 平均仅 6-13% 的 industry_top 板块与主线板块重叠
- 说明 **industry_top（881xxx 行业宇宙）与 active_mainline/confirmed_sectors（880xxx 概念宇宙）是两套几乎独立的板块体系**
- 重叠例：黄金、铜、种植业、林业、旅游、煤炭开采（资源/周期类行业易与概念主线交叉）
- 非重叠例：industry_top 高频词「黄酒/诊断服务/水产捕捞/租赁经纪」vs confirmed_mainline 高频词「PCB概念/CPO概念/合成生物/短剧游戏」

### 1.4 高频板块对照

**industry_top Top 15**（按出现频次）：
```
黄酒(14), 其他种植(11), 诊断服务(11), 水产捕捞(11), 租赁经纪(9), 
教育出版(8), 医疗研发(7), 氮肥(7), 视频音频(6), 白银(6), 
线下药店(6), 光纤光缆(6), 种植业(6), 林业(6), 生活用纸(6)
```

**confirmed_mainline Top 15**（按出现频次）：
```
PCB概念(12), CPO概念(10), 银行(9), 合成生物(8), 医药商业(8), 
红黄酒(8), 黄金概念(7), CXO概念(7), 复合铜箔(6), 短剧游戏(6), 
创新药(6), 5G概念(6), 玻璃基板(6), 旅游(6), 黄金(5)
```

**观察**：
- industry_top 偏向传统行业（农业/资源/消费）
- confirmed_mainline 偏向科技/概念主题（半导体/新能源/医药创新）
- 两套体系的板块命名逻辑不同：industry_top 用通达信行业分类（881xxx），confirmed_mainline 用概念主题（880xxx）

---

## 2. 两个实测约束的处理

### 2.1 约束一：state 恒 neutral / tier 恒空

**问题**：industry_top 无生命周期语义（state 恒 neutral、tier 恒空），无法直接喂下游做「主线确认/退出」判断。

**可用字段**：
1. **strength_score**（4 维百分位加权）：区分度有限（90-97 集中），但 ≥98 可作为「强势行业」信号
2. **name**：板块名称，用于重叠率计算
3. **indicators**（4 维百分位）：
   - `price_momentum`（价格动量）
   - `turnover_expansion`（量能扩张）
   - `relative_strength`（相对强度）
   - `breadth_health`（宽度健康度）
4. **today_change**：当日涨跌幅（实测多为 null）

**不可用字段**：
- `state`：恒 neutral，无信息量
- `tier`：恒空，无信息量
- `lifecycle` / `launch_signal` / `mainline_quality`：实测为 null
- `rrg` / `momentum_state`：实测为 null

**结论**：industry_top 只能提供「行业强度排名」信号，无法提供「生命周期/主线确认」信号。

### 2.2 约束二：GATE-2 名义扫描零贡献

**问题**：`scripts/calibrate_optimal_stop.py:116` 将 industry_top 列入 `sector_lists` 扫表名单，但被主线判据（`mainline_states = {"active_mainline"} | {"exhausted_mainude" if include_exhausted else set()}`）过滤，实际零贡献。

**处置建议**：
- **选项 A（推荐）**：从 `sector_lists` 中移除 `industry_top`，减少无效扫描
- **选项 B（保守）**：保留现状，零运行时成本（仅字典查找），但增加代码理解负担

**理由**：industry_top 与主线板块重叠率仅 6-13%，即使纳入扫描也不会改变最优止损标定结果（主线判据已过滤掉 90%+ 的 industry_top 板块）。

---

## 3. 落点分析：Node 6 续航检查交叉验证

### 3.1 设计意图（文献对照分析二口径）

文献对照分析稿（`regime与数据流断路_文献对照分析_20261011.md` 分析二）建议：
> `industry_top` → Node 6 续航检查：`active_mainline` 与 `industry_top` 重叠率交叉验证（家族续航评估既有输入）

**设计逻辑**：
- 主线板块（880xxx 概念）的续航能力可通过「是否有对应行业板块（881xxx）同步走强」交叉验证
- 例：「PCB概念」主线若伴随「电子元器件」行业走强 ⇒ 续航置信度提升
- 反之：「PCB概念」主线但无对应行业板块支撑 ⇒ 纯概念炒作，续航存疑

### 3.2 实测重叠率是否足以支撑信号

**问题**：重叠率均值仅 6-13%，是否足以作为交叉验证信号？

**分析**：
- **阈值建议**：重叠率 ≥ 20% 视为「行业支撑」，< 20% 视为「无行业支撑」
- **实测达标率**：46 个归档日中，仅 9 日（19.6%）重叠率 ≥ 20%（子串口径）
- **信号稀缺性**：大部分主线板块无对应行业板块同步走强，「行业支撑」是稀缺信号

**结论**：
- 重叠率 **足以支撑交叉验证信号**，但信号稀缺（仅 20% 的主线板块有行业支撑）
- 建议作为「续航置信度加成项」而非「主线确认/否决项」
- 阈值建议：重叠率 ≥ 20% ⇒ 续航评分 +0.1（乘法因子 1.1）

### 3.3 信号价值评估

**正面**：
- 提供「概念 vs 行业」跨宇宙交叉验证，填补现有信号盲区
- 零额外数据成本（industry_top 已落盘 54 日零缺失）
- 零决策改动（仅作为 Node 6 上下文展示）

**负面**：
- 信号稀缺（仅 20% 达标），大部分时间无信息量
- 两套板块体系命名逻辑不同，子串匹配可能误判（例：「光伏硅料」vs「光伏」）
- state/tier 恒值约束下，无法提供生命周期信号

---

## 4. 接线建议：影子先行

### 4.1 推荐方案：影子模式零决策改动

**开关设计**：`GOT_INDUSTRY_CROSS_SHADOW`（默认 true，影子模式）

**接线点**：Node 6 聚合（`aggregator.py`）

**产物**：`data/got_daily/YYYYMMDD/unified.json` 新增字段 `industry_cross_check`：
```json
{
  "industry_cross_check": {
    "overlap_rate_exact": 0.063,
    "overlap_rate_substr": 0.122,
    "overlap_count": 1,
    "overlap_names": ["黄金"],
    "cross_check_verdict": "no_industry_support",
    "confidence_adjustment": 0.0
  }
}
```

**字段说明**：
- `overlap_rate_exact`：精确匹配重叠率
- `overlap_rate_substr`：子串匹配重叠率
- `overlap_count`：重叠板块数量
- `overlap_names`：重叠板块名称列表
- `cross_check_verdict`：
  - `"industry_support"`：重叠率 ≥ 20%，有行业支撑
  - `"no_industry_support"`：重叠率 < 20%，无行业支撑
- `confidence_adjustment`：续航置信度调整值（当前恒 0.0，零决策改动）

### 4.2 影子观测指标

**归档位置**：`data/got_daily/YYYYMMDD/reasoning.md` 末尾「🧪 影子模式观测」段新增子段：

```markdown
### 🧪 industry_top 交叉验证（影子）

- 重叠率（精确/子串）：6.3% / 12.2%
- 重叠板块：黄金
- 裁决：no_industry_support
- 续航置信度调整：0.0（零决策改动）
```

**评估周期**：≥10 个交易日（仓库规则）

**晋升门槛**：
- 负责人抽审 ≥5 例「industry_support」裁决，确认重叠板块确实为主线提供行业支撑
- 重叠率 ≥ 20% 的归档日，主线板块 T+5 超额收益显著高于重叠率 < 20% 的归档日（需回放验证）

**回退门槛**：
- 重叠板块与主线板块语义无关（误判率 > 30%）
- 「industry_support」裁决对主线续航预测无显著区分度

### 4.3 保守选项：不接线

**理由**：
1. 信号稀缺（仅 20% 达标），大部分时间无信息量
2. state/tier 恒值约束下，信息维度有限（仅 score/name）
3. 两套板块体系命名逻辑不同，子串匹配可靠性存疑
4. 现有 Node 6 续航检查已消费 `active_mainline` 的 `strength_score` / `mainline_days` / `capital_check` 等维度，industry_top 提供的边际信息有限

**建议**：
- 若负责人认为「概念 vs 行业」交叉验证价值有限，可选择不接线
- 保留 industry_top 落盘（已零缺失 54 日），未来有新想法时可随时回放分析

---

## 5. GATE-2 处置建议

### 5.1 现状

`scripts/calibrate_optimal_stop.py:116` 将 industry_top 列入 `sector_lists`：
```python
sector_lists = ("active_mainline", "exhausted_mainline", "ambush",
                "near_miss_ambush", "defensive_rotation", "cooling_down",
                "top_by_score", "industry_top")
```

但被主线判据过滤：
```python
mainline_states = {"active_mainline"} | ({"exhausted_mainline"} if include_exhausted else set())
```

industry_top 不在 `mainline_states` 中，实际零贡献。

### 5.2 处置建议

**推荐**：从 `sector_lists` 中移除 `industry_top`

**理由**：
1. 零贡献：被主线判据过滤，不参与最优止损标定
2. 代码清晰度：移除后 `sector_lists` 语义更明确（仅包含主线相关板块列表）
3. 零运行时成本：移除后减少一次字典查找（可忽略）

**实施**：
```python
sector_lists = ("active_mainline", "exhausted_mainline", "ambush",
                "near_miss_ambush", "defensive_rotation", "cooling_down",
                "top_by_score")  # 移除 industry_top
```

**回归测试**：`tests/test_optimal_stop_priors.py:52` 造样本逻辑需同步检查（若测试依赖 industry_top 字段，需移除或替换）

---

## 6. 总结与建议

### 6.1 核心发现

1. **industry_top 与主线板块是两套独立体系**：重叠率仅 6-13%，命名逻辑不同（行业 vs 概念）
2. **state/tier 恒值约束**：无法提供生命周期信号，仅能提供强度排名（score）
3. **信号稀缺但有价值**：重叠率 ≥ 20% 的归档日仅占 20%，但可作为「行业支撑」交叉验证信号
4. **GATE-2 零贡献**：industry_top 被主线判据过滤，建议从扫表名单移除

### 6.2 接线建议

**推荐方案**：影子先行（`GOT_INDUSTRY_CROSS_SHADOW=true`）

- **接线点**：Node 6 续航检查
- **产物**：`unified.json` 新增 `industry_cross_check` 字段
- **阈值**：重叠率 ≥ 20% ⇒ `cross_check_verdict="industry_support"`
- **决策影响**：零（`confidence_adjustment` 恒 0.0）
- **评估周期**：≥10 个交易日

**保守选项**：不接线

- 信号稀缺，边际信息有限
- 保留 industry_top 落盘，未来有新想法时可随时回放

### 6.3 GATE-2 处置

**推荐**：从 `sector_lists` 移除 `industry_top`

- 零贡献，移除后代码更清晰
- 同步检查 `tests/test_optimal_stop_priors.py` 测试逻辑

### 6.4 待负责人裁决

1. **是否接线**：影子先行 vs 不接线
2. **阈值确认**：重叠率 ≥ 20% 是否合理
3. **GATE-2 处置**：是否从 `sector_lists` 移除 `industry_top`
4. **若接线，WI 归属**：归负责人实施（backlog #15 已注记「接线 WI 归负责人裁决」）

---

## 附录：实测数据样本

### A.1 重叠例（前 20 例）

| 日期 | industry_top 名称 | confirmed_mainline 名称 | 匹配方式 |
|------|-------------------|-------------------------|----------|
| 20260807 | 铜 | 铜 | 子串 |
| 20260807 | 黄金 | 黄金 | 子串 |
| 20260810 | 黄金 | 黄金 | 精确 |
| 20260812 | 光伏硅料 | 光伏 | 子串 |
| 20260812 | 黄金 | 黄金 | 子串 |
| 20260812 | 光伏逆变 | 光伏 | 子串 |
| 20260821 | 黄金 | 黄金 | 精确 |
| 20260824 | 焦炭加工 | 焦炭加工 | 精确 |
| 20260824 | 黄金 | 黄金 | 精确 |
| 20260826 | 黄金 | 黄金 | 精确 |
| 20260827 | 种植业 | 种植业 | 精确 |
| 20260831 | 煤炭开采 | 煤炭开采 | 精确 |
| 20260831 | 煤炭 | 煤炭开采 | 子串 |
| 20260901 | 种植业 | 种植业 | 精确 |
| 20260901 | 林业 | 林业 | 精确 |
| 20260902 | 旅游 | 旅游 | 精确 |
| 20260903 | 餐饮 | 酒店餐饮 | 子串 |
| 20260903 | 旅游 | 旅游 | 精确 |
| 20260903 | 旅游 | 旅游景点 | 子串 |
| 20260903 | 旅游景区 | 旅游 | 子串 |

### A.2 industry_top indicators 结构示例

```json
{
  "name": "其他出版",
  "strength_score": 99.0,
  "indicators": {
    "price_momentum": 0.998,
    "turnover_expansion": 0.981,
    "relative_strength": 0.983,
    "breadth_health": 1.0
  },
  "state": "neutral",
  "tier": null,
  "rrg": null,
  "momentum_state": null
}
```

**说明**：4 维百分位均在 0.98+ 区间，说明该行业板块在截面排名中处于顶尖位置。
