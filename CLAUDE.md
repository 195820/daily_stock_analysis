AGENTS.md

# 开发环境启动配置

## Conda 环境

- **环境名**: `dsa`
- **Python**: 3.12.13
- **路径**: `D:\Users\195820\anaconda3\envs\dsa`
- **创建日期**: 2026-06-15

## 启动命令

```powershell
# 激活环境
conda activate dsa

# 运行项目
python main.py                          # 常规分析
python main.py --dry-run                # 仅获取数据不分析
python main.py --debug                  # 调试模式
python main.py --schedule               # 定时模式

# 直接调用环境内 python（不激活时）
D:\Users\195820\anaconda3\envs\dsa\python.exe main.py
```

## 已安装依赖

- `requirements.txt` 全部依赖（efinance, akshare, tushare, litellm, fastapi, alphasift 等）
- 开发工具: pytest, pytest-asyncio, black, isort, ruff, mypy

## 注意事项

- `imgkit` 需要系统安装 `wkhtmltopdf`（图片报告功能）
- 首次运行前需配置 `.env`（LLM API Key、通知渠道、NGA Cookie 等）
- 数据库自动建表于 `./data/stock_analysis.db`

## 东方财富反爬配置

东方财富接口有反爬限制，需在 `.env` 中启用以下配置：

```bash
# 启用东财反爬补丁（NID授权 + 随机UA + 请求延迟）
ENABLE_EASTMONEY_PATCH=true
```

**作用范围**：
- `src/services/technical_indicator.py` — 技术指标获取
- `src/services/sector_rotation.py` — 板块轮动分析（使用 `trust_env=False` 绕过系统代理）
- `data_provider/` — K线数据获取

**反爬机制说明**：
- NID授权令牌：通过 `src/patches/eastmoney_patch.py` 获取
- 请求延迟：自动添加随机延迟避免触发限流
- 随机UA：每次请求使用不同的User-Agent

**常见问题**：
- 若K线数据获取失败（`RemoteDisconnected`），检查是否启用 `ENABLE_EASTMONEY_PATCH`
- 实时行情建议优先使用腾讯/新浪数据源（`realtime_source_priority=tencent,akshare_sina`）

---

# 个性化改造规划

> 详细设计文档: `docs/custom/screener_design.md`
> 创建日期: 2026-06-13

## 改造目标

在现有 DSA 框架基础上，新增 **每日智能选股系统**，实现：
1. 从 NGA 大时代板块采集热门股票讨论和舆情
2. 分析板块轮动形态，识别强势板块和龙头股
3. 用自定义 YAML 策略量化筛选候选股票
4. AI 综合评分，输出 Top 10 精选推荐
5. 自动推送到 configured 通知渠道

## 新增模块

| 模块 | 文件路径 | 职责 |
|------|---------|------|
| ForumSentimentService | `src/services/forum_sentiment.py` | NGA 大时代舆情采集 |
| SectorRotationService | `src/services/sector_rotation.py` | 板块轮动分析 + 强度评分 |
| StrategyFilterService | `src/services/strategy_filter.py` | 策略量化条件筛选引擎 |
| StockScreenerPipeline | `src/services/stock_screener.py` | 选股流水线编排 |
| TechnicalIndicatorService | `src/services/technical_indicator.py` | 短线技术指标采集与评分 |
| THSScreenerService | `src/services/ths_screener.py` | 同花顺问财选股服务 |
| DdeFetcher | `src/services/dde_fetcher.py` | 东方财富 DDE 数据获取 |
| DdeRepository | `src/repositories/dde_repository.py` | DDE 数据访问层 |
| Screener Tools | `src/agent/tools/screener_tools.py` | Agent 选股工具集 |
| Screener Repository | `src/repositories/screener_repository.py` | 选股结果数据访问 |

## 新增配置项（.env）

```bash
# 论坛舆情 (NGA 大时代)
FORUM_SENTIMENT_ENABLED=true
FORUM_SENTIMENT_NGA_UID=                    # ngaPassportUid
FORUM_SENTIMENT_NGA_CID=                    # ngaPassportCid
FORUM_SENTIMENT_NGA_FID=706                 # 大时代版面 ID
FORUM_SENTIMENT_CACHE_TTL=1800

# 板块轮动
SECTOR_ROTATION_ENABLED=true
SECTOR_ROTATION_LOOKBACK_DAYS=5

# 选股流水线
SCREENER_ENABLED=true
SCREENER_SCHEDULE_TIME=08:30
SCREENER_TOP_N=10
SCREENER_MIN_SCORE=60
```

## 自定义策略

策略文件放在 `strategies/custom/` 目录，YAML schema 在现有基础上新增 `screener_conditions` 字段：

```yaml
name: forum_hot_screener
category: screener          # 新类别
screener_conditions:
  - field: volume_ratio
    operator: gt
    value: 1.5
    weight: 0.2
  - field: ma_alignment
    operator: eq
    value: true
    weight: 0.3
```

## 新增 CLI 入口

```bash
python main.py --screener                    # 独立运行选股
python main.py --screener --dry-run          # 选股但不推送
python main.py --schedule                    # 定时模式自动包含选股任务
```

## 实施阶段

1. **Phase 1** — ✅ Config 扩展 + ForumSentimentService（NGA 大时代）+ 建表（2026-06-13 完成）
2. **Phase 2** — ✅ SectorRotationService + 板块历史数据（2026-06-13 完成）
3. **Phase 3** — ✅ StrategyFilterService + DDE 数据层 + 同花顺问财集成（2026-06-14 完成）
4. **Phase 4** — ✅ TechnicalIndicatorService + 风险评估 + 五维整合 + AI评分 + 便捷脚本（2026-06-16 完成）
5. **Phase 5** — ⬜ Agent Tools + 更多论坛源 + API 端点 + 测试

## Phase 3 新增内容（2026-06-14）

### 数据源

| 数据源 | 说明 | DDE 计算方式 |
|--------|------|-------------|
| **同花顺问财（推荐）** | 直接获取同花顺筛选结果 | 同花顺官方计算 |
| 东方财富 | 自己计算 DDE | 大单净流入金额 / 流通市值 |

### 新增文件

- `src/services/ths_screener.py` - 同花顺问财选股服务（Playwright 模拟浏览器）
- `src/services/dde_fetcher.py` - 东方财富 DDE 数据获取
- `src/repositories/dde_repository.py` - DDE 数据访问层
- `src/storage.py` - 新增 `StockDdeRecord` 数据表
- `strategies/custom/dde_low_screener.yaml` - DDE 低位选股策略

### 策略条件

```yaml
# DDE 低位选股策略 (dde_low_screener.yaml)
screener_conditions:
  - field: dde_big_order    # DDE大单净量 >= 0.6%
    operator: gte
    value: 0.6
  - field: low_ratio        # 最低价比率 ∈ [1.0, 1.02)
    operator: between
    value: [1.0, 1.02]
  - field: dde_range_2d     # 近2日区间DDE >= 0.6%
    operator: gte
    value: 0.6
```

### 新增依赖

- `playwright` - 用于模拟浏览器访问同花顺问财

## 关键设计决策

- **双层筛选**: 量化硬筛（缩小范围）→ LLM 软评（精选打分）
- **综合评分权重**: 技术30% + 板块25% + 策略20% + 舆情15% + 风险10%
- **零侵入**: 完全复用现有通知/调度/LLM/数据层，不改核心 pipeline
- **NGA 优先**: 舆情数据源以 NGA 大时代为核心，未来可扩展更多论坛源

## Phase 4 新增内容（2026-06-15）

### 短线技术指标服务

新增 `src/services/technical_indicator.py`，专注短线交易场景：

| 指标 | 权重 | 说明 |
|------|------|------|
| DDE大单净量 | 25% | 主力动向，复用已有 DdeFetcher |
| 量比 | 20% | 当日放量程度（vs 5日均量） |
| 换手率 | 15% | 活跃度（量比估算） |
| RSI(6) | 15% | 快周期超买超卖，50-70 为强势区 |
| MA5/MA10/MA20 排列 | 10% | 短期趋势，多头排列加分 |
| 布林带位置 | 5% | 中上轨最优 |
| 突破5日高点 | 10% | 创新高信号 |

### 评分验证结果

用同花顺问财筛选的 91 只股票测试：

| 排名 | 代码 | 评分 | 特征 |
|------|------|------|------|
| 1 | 002935 天奥电子 | 72.2 | 量比2.57 + RSI78 + 多头排列 |
| 2 | 300377 赢时胜 | 70.9 | 涨8.2% + 量比4.11 + 放量上攻 |
| 3 | 600497 驰宏锌锗 | 69.3 | 涨9.1% + 量比2.84 + 多头突破 |
| - | 603335 迪生力 | 39.1 | 涨停但量比0.07（缩量一字板）→ 低分合理 |

### 流水线整合

- `StockScreenerPipeline._enrich_technical_scores()` — 选股后自动补充技术评分
- 综合评分 = 策略×w + 技术×w + 板块×w + 舆情×w + 风险×w（五维加权，LLM 优先）
- `format_notification()` — 显示技术评分 + 信号标签（主力强攻/放量上攻/多头排列等）

### 技术实现要点

- 使用 `eastmoney_patch()` 反爬（NID 授权 + 随机 UA + 延迟）
- HTTPS 直连，`trust_env=False` 绕过系统代理（与 sector_rotation 一致）
- 从 K 线数据计算所有指标，不依赖实时行情接口

### 待完成

- [x] AI 综合评分（LLM 点评，五维数据整合）
- [x] 板块面整合（个股所属板块 → 板块强度分）
- [x] 舆情面整合（NGA 热度/情绪 → 评分）
- [x] 风险评估服务（估值分位、波动率）

### 五维综合评分（2026-06-16 完成）

新增模块：
- `src/services/risk_assessment.py` — 风险评估服务（估值/波动率/超买）
- `_enrich_sector_scores()` — 板块面整合（DataFetcherManager.get_belong_boards）
- `_enrich_sentiment_scores()` — 舆情面整合（ForumSentimentService）
- `_enrich_risk_scores()` — 风险整合
- `_ai_scoring()` — LLM AI 综合评分（降级到加权公式）
- `_compute_composite_scores()` — 五维加权综合评分

综合评分公式（LLM 不可用时降级）：
```
score = 策略×w_strategy + 技术×w_technical + 板块×w_sector + 舆情×w_forum + (100-风险)×w_risk
```

新增 `scripts/run_screener.py` 便捷脚本：
```bash
python scripts/run_screener.py                  # 默认运行
python scripts/run_screener.py --notify          # 推送通知
python scripts/run_screener.py --source eastmoney  # 东方财富数据源
```