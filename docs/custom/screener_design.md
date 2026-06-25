# 个性化选股系统 — 详细设计文档

> 基于 Daily Stock Analysis (DSA) 框架的个性化改造，实现：论坛舆情监控 → 板块形态分析 → 策略选股 → 智能挑选 → 自动推送

---

## 1. 系统总览

### 1.1 架构定位

```
┌─────────────────────────────────────────────────────────────────┐
│                        StockScreenerPipeline                     │
│                     （新增：选股流水线编排）                       │
├──────────┬──────────────┬──────────────┬────────────────────────┤
│          │              │              │                        │
▼          ▼              ▼              ▼                        ▼
Forum      SectorRotation StrategyFilter AI Scoring          Notification
Sentiment  Service        Service        (复用LLM)           (复用推送)
Service
(新增)      (新增)         (复用+扩展)    (复用)              (复用+扩展)
│          │              │              │                        │
▼          ▼              ▼              ▼                        ▼
NGA大时代   EastMoney     strategies/   GeminiAnalyzer        企微/飞书/
(fid=706)   API+缓存      *.yaml        + Agent Tools         Telegram
```

### 1.2 数据流

```
每日定时触发 (Scheduler)
    │
    ├─① ForumSentimentService.fetch_hot_stocks()
    │     → 热门股票列表 + 情绪评分 + 讨论热度
    │
    ├─② SectorRotationService.get_rotation_analysis()
    │     → 强势板块排名 + 板块趋势 + 龙头股
    │
    ├─③ StrategyFilterService.filter(candidates, strategies)
    │     → 按自定义策略过滤，保留符合条件的股票
    │
    ├─④ StockScreenerPipeline.score(filtered, sector_data, sentiment_data)
    │     → AI 综合评分（多维度交叉）
    │
    ├─⑤ 生成选股报告 → 推送通知
    │
    └─⑥ 持久化到 SQLite (screener_results 表)
```

---

## 2. 模块详细设计

---

### 2.1 ForumSentimentService — A股论坛舆情采集服务

**文件**: `src/services/forum_sentiment.py`

#### 2.1.1 职责

从指定的 A 股论坛/社区采集热门股票讨论数据，输出结构化的舆情评分。

#### 2.1.2 数据源

| 数据源 | 采集方式 | 数据内容 | 优先级 |
|--------|---------|---------|--------|
| NGA 大时代 (bbs.nga.cn/fid=706) | HTTP API (`thread.php` + `read.php`, `__output=8`) | 帖子标题、回复数、帖子内容、高楼帖 | P0 |

> **设计说明**: 初期仅接入 NGA 大时代板块作为唯一数据源。NGA 大时代是中文互联网高质量的股票讨论社区，帖子深度好、高楼帖（万级回复）多，适合做情绪分析和热门股提取。
>
> **认证方式**: NGA 需要登录 Cookie 认证（`ngaPassportUid` + `ngaPassportCid`），配置在 `.env` 中。
>
> **编码注意**: NGA API 返回 GBK 编码的 JSON，需 `resp.encoding = 'gbk'` + `json.loads(text, strict=False)` 处理控制字符。
>
> **API 端点**:
> - 帖子列表: `GET https://bbs.nga.cn/thread.php?fid=706&page={N}&__output=8`
> - 帖子内容: `GET https://bbs.nga.cn/read.php?tid={TID}&page={N}&__output=8`
>
> **高楼帖策略**: 对回复数 > 500 的活跃高楼帖，额外抓取最新几页内容，提取近期讨论的股票和情绪变化。
>
> **未来扩展**: 可追加东方财富股吧、淘股吧等数据源，沿用 DataFetcherManager 的优先级降级模式。

#### 2.1.3 核心接口

```python
@dataclass
class ForumHotStock:
    """论坛热门股票"""
    code: str                    # 股票代码 (如 "600519")
    name: str                    # 股票名称 (如 "贵州茅台")
    source: str                  # 来源: "nga_dashidai"
    heat_score: float            # 热度评分 0-100 (讨论量/浏览量归一化)
    sentiment_score: float       # 情绪评分 -1.0 ~ 1.0 (负面~正面)
    mention_count: int           # 讨论/提及次数
    trend: str                   # 趋势: "rising" | "falling" | "stable"
    top_posts: List[Dict]        # 热门帖子 [{title, url, replies, likes}]
    fetched_at: datetime         # 采集时间

@dataclass
class ForumSentimentResult:
    """论坛舆情采集结果"""
    hot_stocks: List[ForumHotStock]        # 热门股票列表
    market_sentiment: float                # 市场整体情绪 -1.0 ~ 1.0
    hot_topics: List[Dict]                 # 热门话题 [{topic, heat, stock_codes}]
    source_status: Dict[str, str]          # 各数据源状态 {"nga_dashidai": "ok", ...}
    fetched_at: datetime

class ForumSentimentService:
    def __init__(self, config: Config):
        """
        初始化，根据 config 中的开关决定启用哪些数据源
        """

    @property
    def is_available(self) -> bool:
        """至少一个数据源可用"""

    def fetch_hot_stocks(
        self,
        *,
        sources: Optional[List[str]] = None,   # 指定源，None=全部
        top_n: int = 50,                        # 返回 Top N
        cache_ttl: int = 1800,                  # 缓存秒数 (默认30分钟)
    ) -> ForumSentimentResult:
        """
        主入口：采集论坛热门股票
        - 并发请求多个数据源
        - 合并去重（按股票代码）
        - 计算综合热度/情绪评分
        - 结果缓存
        """

    def get_stock_sentiment(self, stock_code: str) -> Optional[Dict]:
        """
        查询单只股票的舆情摘要（供 Agent Tool 调用）
        返回: {heat_score, sentiment_score, mention_count, top_posts, trend}
        """

    # --- 内部数据源采集器 ---
    def _fetch_nga_thread_list(self, pages: int = 3) -> List[Dict]:
        """
        NGA 大时代帖子列表:
        - API: GET https://bbs.nga.cn/thread.php?fid=706&page={N}&__output=8
        - 编码: GBK，需 json.loads(text, strict=False)
        - 认证: Cookie (ngaPassportUid + ngaPassportCid)
        - 解析: 遍历 data.__T，提取 tid, subject, replies, author, postdate, recommend
        - 默认抓取前 3 页（约 105 条帖子）
        - 返回: [{tid, subject, replies, author, postdate, recommend}, ...]
        """

    def _fetch_nga_thread_content(self, tid: int, pages: int = 1) -> Dict:
        """
        NGA 帖子内容（用于高楼帖深度分析）:
        - API: GET https://bbs.nga.cn/read.php?tid={tid}&page={N}&__output=8
        - 解析: data.__R[0] 为主楼，data.__R[1..N] 为回复
        - 返回: {subject, content (主楼), replies: [{author, content, postdate}, ...]}
        """

    def _identify_high_rise_threads(self, threads: List[Dict], threshold: int = 500) -> List[Dict]:
        """
        识别高楼帖（回复数 > threshold），对它们做额外深度抓取:
        - 抓最新 2 页回复（捕捉近期讨论热点）
        - 与帖子列表数据合并
        """

    def _extract_stock_codes(self, text: str) -> List[str]:
        """
        从帖子标题/内容中提取股票代码:
        - 正则匹配 6 位数字代码 (如 600519, 002594, 300750)
        - 匹配常见股票名称 (如 贵州茅台, 比亚迪, 宁德时代)
        - 名称→代码映射（复用 src/data/stock_mapping）
        """
```

#### 2.1.4 情绪分析策略

```
情绪评分计算 (sentiment_score):
  ├── 基础方法: 关键词规则引擎 (零依赖，优先使用)
  │     ├── 正面词库: 涨停、利好、突破、放量、龙头、加仓、看多...
  │     ├── 负面词库: 跌停、利空、暴跌、减持、亏损、退市、割肉...
  │     └── 评分 = (正面词频 - 负面词频) / 总词频 → [-1, 1]
  │
  └── 增强方法 (可选): 调用已配置的 LLM 批量评估
        └── 当 config.forum_sentiment_use_llm = true 时启用
```

#### 2.1.5 缓存策略

```python
# 内存缓存 (进程内)
_cache: Dict[str, Tuple[ForumSentimentResult, float]]  # key → (result, timestamp)

# 持久化缓存 (SQLite)
# 表: forum_sentiment_cache
# 字段: source, result_json, fetched_at, expires_at
# TTL: 可配置，默认 30 分钟
```

---

### 2.2 SectorRotationService — 板块轮动分析服务

**文件**: `src/services/sector_rotation.py`

#### 2.2.1 职责

追踪板块涨跌趋势，识别板块轮动信号，输出强势板块排名和形态评分。

#### 2.2.2 数据依赖

| 数据 | 来源 | 状态 |
|------|------|------|
| 概念板块排名 | EastMoney push2 API + akshare 降级 | ✅ Phase 2 实现 |
| 行业板块排名 | EastMoney push2 API + akshare 降级 | ✅ Phase 2 实现 |
| 板块成分股 | EastMoney push2 API（按涨幅排序） | ✅ Phase 2 实现 |
| 板块历史K线 | EastMoney push2his API + akshare 降级 | ✅ Phase 2 实现 |
| 板块资金流向 | 基于成交额变化估算 | ✅ Phase 2 实现 |

#### 2.2.3 核心接口

```python
@dataclass
class SectorStrength:
    """板块强度数据"""
    name: str                        # 板块名称 (如 "人工智能")
    board_type: str                  # "concept" | "industry"
    change_pct_today: float          # 今日涨跌幅 %
    change_pct_3d: float             # 近3日累计涨跌 %
    change_pct_5d: float             # 近5日累计涨跌 %
    strength_score: float            # 综合强度评分 0-100
    rotation_phase: str              # 轮动阶段: "emerging" | "accelerating" | "peaking" | "declining"
    leader_stocks: List[Dict]        # 龙头股 [{code, name, change_pct, role}]
    capital_flow: Optional[float]    # 资金净流入 (万元)
    stock_count: int                 # 板块成分股数量
    up_ratio: float                  # 板块内上涨比例 0-1

@dataclass
class SectorRotationResult:
    """板块轮动分析结果"""
    strong_sectors: List[SectorStrength]     # 强势板块排名
    emerging_sectors: List[SectorStrength]   # 新晋崛起板块
    declining_sectors: List[SectorStrength]  # 衰退板块
    rotation_signals: List[Dict]             # 轮动信号 [{from_sector, to_sector, signal}]
    market_theme: str                        # 市场主线概括
    fetched_at: datetime

class SectorRotationService:
    def __init__(self, config: Config, fetcher_manager: DataFetcherManager):
        """
        依赖 DataFetcherManager 获取板块数据
        """

    def get_rotation_analysis(
        self,
        *,
        top_n: int = 20,
        lookback_days: int = 5,
        cache_ttl: int = 3600,          # 默认1小时缓存
    ) -> SectorRotationResult:
        """
        主入口：板块轮动分析
        1. 获取当日概念/行业板块排名
        2. 获取近 N 日板块涨跌数据 (新增: _fetch_sector_history)
        3. 计算板块强度评分 (多维度加权)
        4. 判断轮动阶段
        5. 识别龙头股
        6. 生成轮动信号
        """

    def get_sector_stocks(self, sector_name: str, board_type: str = "concept") -> List[Dict]:
        """
        获取板块成分股列表
        复用 DsaEastMoneyHotspotProvider
        """

    def get_stock_sector_membership(self, stock_code: str) -> List[str]:
        """
        查询股票所属板块
        复用 DataFetcherManager.get_belong_boards()
        """

    # --- 内部方法 ---
    def _fetch_sector_history(self, sector_name: str, days: int) -> List[Dict]:
        """
        获取板块历史涨跌数据
        API: EastMoney 板块K线接口
        http://push2his.eastmoney.com/api/qt/stock/kline/get
        参数: secid=90.BK0xxx (概念) / 90.BK1xxx (行业)
        """

    def _calculate_strength_score(self, sector: Dict, history: List[Dict]) -> float:
        """
        强度评分算法:
          score = w1 * 今日涨幅排名分
                + w2 * 3日趋势分
                + w3 * 5日趋势分
                + w4 * 资金流向分
                + w5 * 板块内上涨比例分
          默认权重: w1=0.25, w2=0.20, w3=0.15, w4=0.25, w5=0.15
        """

    def _detect_rotation_phase(self, history: List[Dict]) -> str:
        """
        轮动阶段判断:
          emerging:     近5日从跌转涨，涨幅逐步放大
          accelerating: 连续上涨，涨幅加速
          peaking:      涨幅开始收窄，高位放量
          declining:    连续下跌或涨幅转负
        """
```

#### 2.2.4 强度评分权重（可配置）

```python
# 环境变量可覆盖默认权重
SECTOR_WEIGHT_TODAY=0.25
SECTOR_WEIGHT_3D=0.20
SECTOR_WEIGHT_5D=0.15
SECTOR_WEIGHT_CAPITAL_FLOW=0.25
SECTOR_WEIGHT_UP_RATIO=0.15
```

---

### 2.3 StrategyFilterService — 策略选股过滤服务

**文件**: `src/services/strategy_filter.py`

#### 2.3.1 职责

将候选股票池与自定义 YAML 策略进行匹配，筛选出符合策略条件的股票。

#### 2.3.2 与现有策略系统的关系

```
现有机制 (个股分析时):
  strategies/*.yaml → SkillManager → 注入到 LLM prompt → AI 分析单只股票

新增机制 (选股筛选时):
  strategies/*.yaml → StrategyFilterService → 量化条件匹配 → 过滤候选股票
```

> **关键区别**: 现有策略是给 LLM 的分析指引（软性），新增的过滤服务是量化条件匹配（硬性）。两者互补——先硬筛缩小范围，再用 LLM 精评。

#### 2.3.3 核心接口

```python
@dataclass
class StrategyCondition:
    """从 YAML 策略解析出的量化筛选条件"""
    field: str              # 如 "ma_alignment", "volume_ratio", "change_pct"
    operator: str           # "gt", "lt", "eq", "between", "cross_above"
    value: Any              # 阈值
    weight: float           # 条件权重 (用于评分)

@dataclass
class FilteredStock:
    """通过策略筛选的股票"""
    code: str
    name: str
    matched_strategies: List[str]           # 匹配的策略名称列表
    strategy_scores: Dict[str, float]       # 各策略匹配度评分
    total_score: float                      # 综合策略评分
    key_indicators: Dict[str, Any]          # 关键指标快照
    match_details: List[Dict]               # 匹配明细

class StrategyFilterService:
    def __init__(self, config: Config, fetcher_manager: DataFetcherManager):
        """
        复用现有 SkillManager 加载 YAML 策略
        解析策略中的量化条件
        """

    def filter(
        self,
        candidate_codes: List[str],
        *,
        strategy_names: Optional[List[str]] = None,   # 指定策略，None=全部screener类策略
        top_n: int = 30,
    ) -> List[FilteredStock]:
        """
        主入口: 候选股票 → 策略匹配 → 过滤排序
        1. 批量获取候选股票的行情数据 (并发)
        2. 计算技术指标 (MA/MACD/RSI/量比等)
        3. 逐策略匹配条件
        4. 计算综合评分
        5. 按评分降序排列，返回 Top N
        """

    # --- 内置量化条件引擎 ---
    def _check_ma_alignment(self, df: pd.DataFrame) -> bool:
        """均线多头排列: MA5 > MA10 > MA20 > MA60"""

    def _check_volume_breakout(self, df: pd.DataFrame, threshold: float = 2.0) -> bool:
        """放量突破: 当日成交量 > N日均量 * threshold"""

    def _check_price_position(self, df: pd.DataFrame) -> str:
        """价格位置: "near_high" | "near_low" | "middle" """

    def _check_macd_golden_cross(self, df: pd.DataFrame) -> bool:
        """MACD金叉"""

    def _check_rsi(self, df: pd.DataFrame, low: float = 30, high: float = 70) -> str:
        """RSI区间: "oversold" | "neutral" | "overbought" """
```

#### 2.3.4 策略 YAML 扩展（screener 类策略）

在现有 YAML schema 基础上，新增 `screener_conditions` 字段：

```yaml
# strategies/custom/forum_hot_screener.yaml
name: forum_hot_screener
display_name: 论坛热股精选
description: 从论坛热门股中筛选技术面良好的标的
category: screener                          # 新类别: screener
core_rules: []
required_tools:
  - get_daily_history
  - analyze_trend
aliases: [论坛选股, 热股筛选]
default_active: true
default_priority: 10
market_regimes: [sector_hot, trending_up]

# ===== 新增: 量化筛选条件 =====
screener_conditions:
  - field: volume_ratio
    operator: gt
    value: 1.5
    weight: 0.2
    description: "量比 > 1.5 (有资金关注)"

  - field: ma_alignment
    operator: eq
    value: true
    weight: 0.3
    description: "均线多头排列"

  - field: change_pct_5d
    operator: between
    value: [0, 15]
    weight: 0.2
    description: "近5日涨幅 0~15% (温和上涨)"

  - field: rsi_14
    operator: between
    value: [40, 70]
    weight: 0.15
    description: "RSI处于合理区间"

  - field: turnover_rate
    operator: gt
    value: 3.0
    weight: 0.15
    description: "换手率 > 3% (活跃度)"

# 分析指引 (原有字段，给 LLM 用)
instructions: |
  **论坛热股精选策略**
  关注论坛讨论度高且技术面良好的股票...
```

---

### 2.4 StockScreenerPipeline — 选股流水线（核心编排）

**文件**: `src/services/stock_screener.py`

#### 2.4.1 职责

编排上述三个服务，完成每日选股全流程，生成报告并推送。

#### 2.4.2 核心接口

```python
@dataclass
class ScreenerPick:
    """最终选股结果"""
    code: str
    name: str
    composite_score: float             # 综合评分 0-100
    sector: str                        # 所属最强板块
    sector_strength: float             # 板块强度分
    forum_heat: float                  # 论坛热度分
    strategy_score: float              # 策略匹配分
    ai_reasoning: str                  # AI 推荐理由
    risk_tags: List[str]               # 风险标签
    recommendation: str                # "strong_buy" | "buy" | "watch"

@dataclass
class ScreenerReport:
    """选股报告"""
    date: str
    picks: List[ScreenerPick]                  # 精选股票 (Top 5-10)
    hot_sectors: List[Dict]                    # 推荐板块
    market_summary: str                        # 市场概况
    strategy_summary: str                      # 策略执行摘要
    forum_highlights: List[Dict]               # 论坛亮点
    model_used: str                            # 使用的 AI 模型
    generated_at: datetime

class StockScreenerPipeline:
    def __init__(self, config: Config):
        self.config = config
        self.fetcher_manager = DataFetcherManager()
        self.forum_service = ForumSentimentService(config)
        self.sector_service = SectorRotationService(config, self.fetcher_manager)
        self.strategy_filter = StrategyFilterService(config, self.fetcher_manager)
        self.analyzer = GeminiAnalyzer(config=config)    # 复用 LLM 分析
        self.notifier = NotificationService()            # 复用通知
        self.db = DatabaseManager.get_instance()

    async def run(self, *, send_notification: bool = True) -> ScreenerReport:
        """
        主流程 (可作为 Scheduler 的定时任务调用)

        Step 1: 论坛舆情采集
          └→ forum_service.fetch_hot_stocks(top_n=50)
          └→ 输出: 50只热门股票 + 情绪评分

        Step 2: 板块轮动分析
          └→ sector_service.get_rotation_analysis(top_n=20)
          └→ 输出: 20个强势板块 + 龙头股

        Step 3: 候选池合并
          └→ 论坛热股 ∪ 板块龙头股 → 去重 → 候选池 (约60-80只)

        Step 4: 策略量化筛选
          └→ strategy_filter.filter(candidates, top_n=30)
          └→ 输出: 通过策略条件的30只 + 策略评分

        Step 5: AI 综合评分
          └→ 构建 prompt: 策略评分 + 板块强度 + 论坛情绪 + 技术面
          └→ LLM 评分 + 生成推荐理由
          └→ 输出: Top 5-10 精选

        Step 6: 报告生成 + 推送
          └→ _generate_report()
          └→ notifier.send() (复用现有推送渠道)

        Step 7: 持久化
          └→ save to SQLite (screener_results 表)
        """

    def _merge_candidates(
        self,
        forum_result: ForumSentimentResult,
        sector_result: SectorRotationResult,
    ) -> List[Dict]:
        """
        合并候选池:
        - 论坛热股 (权重: heat_score)
        - 板块龙头 (权重: sector_strength * stock_role_weight)
        - 去重规则: 同一只股票取最高分
        - 附加信息: 所属板块、论坛情绪
        """

    def _ai_scoring(
        self,
        filtered_stocks: List[FilteredStock],
        sector_result: SectorRotationResult,
        forum_result: ForumSentimentResult,
    ) -> List[ScreenerPick]:
        """
        AI 综合评分 (复用 GeminiAnalyzer 的 LLM 调用能力)

        Prompt 构建:
          你是专业的 A 股选股分析师。以下是经过量化策略初筛后的候选股票，
          请结合以下维度进行综合评分 (0-100):

          1. 板块强度 (25%): 所属板块的趋势强度和轮动阶段
          2. 舆情热度 (15%): 论坛讨论热度和情绪倾向
          3. 技术形态 (30%): 均线、量价、MACD 等
          4. 策略匹配 (20%): 自定义策略的匹配程度
          5. 风险因素 (10%): 减持、高估值、监管等负面因素

          请输出 Top 10 精选，每只给出:
          - 综合评分
          - 推荐理由 (50字内)
          - 风险标签
          - 操作建议 (strong_buy / buy / watch)
        """

    def _generate_report(self, picks: List[ScreenerPick], ...) -> ScreenerReport:
        """
        生成选股报告
        复用 NotificationService 的模板渲染能力
        """
```

#### 2.4.3 综合评分权重（可配置）

```python
# 环境变量配置
SCREENER_WEIGHT_SECTOR=0.25       # 板块强度权重
SCREENER_WEIGHT_FORUM=0.15        # 论坛舆情权重
SCREENER_WEIGHT_TECHNICAL=0.30    # 技术形态权重
SCREENER_WEIGHT_STRATEGY=0.20     # 策略匹配权重
SCREENER_WEIGHT_RISK=0.10         # 风险因子权重

SCREENER_TOP_N=10                 # 最终推荐股票数
SCREENER_MIN_SCORE=60             # 最低推荐分数阈值
```

---

### 2.5 新增 Agent Tools

**文件**: `src/agent/tools/screener_tools.py`

供 Agent 模式调用的新工具，使 AI 可以在分析过程中主动查询舆情和板块数据。

```python
# Tool 1: 查询论坛舆情
@tool(name="get_forum_sentiment", category="sentiment",
      description="查询股票在雪球/股吧/淘股吧的论坛舆情数据")
def get_forum_sentiment(stock_code: str) -> dict:
    """
    返回:
    {
        "code": "600519",
        "heat_score": 85.2,
        "sentiment_score": 0.72,
        "mention_count": 1234,
        "trend": "rising",
        "top_posts": [...],
        "source": "xueqiu + eastmoney_guba"
    }
    """

# Tool 2: 查询板块轮动
@tool(name="get_sector_rotation", category="market",
      description="获取当前板块轮动分析，包括强势板块、崛起板块和衰退板块")
def get_sector_rotation(top_n: int = 10) -> dict:
    """
    返回:
    {
        "strong_sectors": [{"name": "人工智能", "strength_score": 88, "rotation_phase": "accelerating", ...}],
        "emerging_sectors": [...],
        "rotation_signals": [...]
    }
    """

# Tool 3: 板块成分股查询
@tool(name="get_sector_constituents", category="market",
      description="查询指定板块的成分股列表")
def get_sector_constituents(sector_name: str, board_type: str = "concept") -> dict:
    """复用 DsaEastMoneyHotspotProvider"""
```

---

### 2.6 通知报告扩展

#### 2.6.1 新增报告类型

在 `src/notification.py` 中新增 `generate_screener_report()` 方法：

```python
def generate_screener_report(self, report: ScreenerReport) -> str:
    """
    选股报告模板

    输出格式:
    ═══════════════════════════════════════
    📊 每日智能选股报告 | 2026-06-13
    ═══════════════════════════════════════

    🏆 今日市场主线: 人工智能 + 新能源

    ━━━ 精选推荐 (Top 5) ━━━

    1. 🟢 600519 贵州茅台  ★ 92分
       板块: 白酒 (强度 85/100 ↑)
       论坛热度: 88 | 策略匹配: 95
       💡 理由: 均线多头+板块龙头+论坛看多情绪一致
       ⚠️ 风险: 估值偏高

    2. 🟢 002594 比亚迪    ★ 88分
       ...

    ━━━ 强势板块 ━━━
    1. 人工智能 (↑↑ 加速期) 涨幅 +3.2%
    2. 新能源  (↑  崛起期) 涨幅 +2.1%
    ...

    ━━━ 论坛热点 ━━━
    🔥 NGA 大时代讨论度最高: 贵州茅台 (1234条回复)
    📈 情绪最正面: 比亚迪 (0.85)
    ...

    ⏰ 生成时间: 2026-06-13 18:00
    🤖 模型: gemini-2.5-pro
    """
```

#### 2.6.2 Jinja2 模板

新增 `templates/report_screener.j2`，支持自定义报告格式。

---

## 3. 配置扩展

### 3.1 新增环境变量（`.env`）

```bash
# ===== 论坛舆情服务 (NGA 大时代) =====
FORUM_SENTIMENT_ENABLED=true
FORUM_SENTIMENT_NGA_UID=                                # NGA 登录 Cookie: ngaPassportUid
FORUM_SENTIMENT_NGA_CID=                                # NGA 登录 Cookie: ngaPassportCid
FORUM_SENTIMENT_NGA_FID=706                             # NGA 版面 ID（大时代=706）
FORUM_SENTIMENT_PAGES=3                                 # 帖子列表抓取页数
FORUM_SENTIMENT_HIGH_RISE_THRESHOLD=500                 # 高楼帖回复数阈值
FORUM_SENTIMENT_HIGH_RISE_EXTRA_PAGES=2                 # 高楼帖额外抓取页数
FORUM_SENTIMENT_CACHE_TTL=1800                          # 缓存秒数
FORUM_SENTIMENT_USE_LLM=false                           # 是否用LLM做情绪分析
FORUM_SENTIMENT_TOP_N=50                                # 采集热门股数量

# ===== 板块轮动服务 =====
SECTOR_ROTATION_ENABLED=true
SECTOR_ROTATION_LOOKBACK_DAYS=5                           # 回溯天数
SECTOR_ROTATION_TOP_N=20                                  # 分析板块数
SECTOR_WEIGHT_TODAY=0.25                                  # 强度评分权重
SECTOR_WEIGHT_3D=0.20
SECTOR_WEIGHT_5D=0.15
SECTOR_WEIGHT_CAPITAL_FLOW=0.25
SECTOR_WEIGHT_UP_RATIO=0.15

# ===== 选股流水线 =====
SCREENER_ENABLED=true
SCREENER_SCHEDULE_TIME=08:30                              # 每日选股时间 (盘前)
SCREENER_TOP_N=10                                         # 最终推荐数量
SCREENER_MIN_SCORE=60                                     # 最低推荐分
SCREENER_WEIGHT_SECTOR=0.25
SCREENER_WEIGHT_FORUM=0.15
SCREENER_WEIGHT_TECHNICAL=0.30
SCREENER_WEIGHT_STRATEGY=0.20
SCREENER_WEIGHT_RISK=0.10
SCREENER_CUSTOM_STRATEGY_DIR=                             # 自定义策略目录 (覆盖 AGENT_SKILL_DIR)
```

### 3.2 Config 类扩展

在 `src/config.py` 的 `Config` dataclass 和 `_load_from_env()` 中添加对应字段，模式与现有 `social_sentiment_*`、`alphasift_*` 一致。

---

## 4. 数据库扩展

### 4.1 新增表

```sql
-- 选股结果表
CREATE TABLE screener_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,                    -- 选股日期
    stock_code TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    composite_score REAL,                      -- 综合评分
    sector TEXT,                               -- 所属板块
    sector_strength REAL,                      -- 板块强度
    forum_heat REAL,                           -- 论坛热度
    strategy_score REAL,                       -- 策略评分
    ai_reasoning TEXT,                         -- AI 推荐理由
    recommendation TEXT,                       -- 操作建议
    risk_tags TEXT,                            -- 风险标签 (JSON数组)
    matched_strategies TEXT,                   -- 匹配策略 (JSON数组)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_date, stock_code)
);

-- 论坛舆情缓存表
CREATE TABLE forum_sentiment_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,                      -- 数据源
    stock_code TEXT,                           -- 股票代码 (可为空=整体市场情绪)
    result_json TEXT NOT NULL,                 -- JSON 序列化结果
    heat_score REAL,
    sentiment_score REAL,
    fetched_at TIMESTAMP,
    expires_at TIMESTAMP
);

-- 板块历史数据表
CREATE TABLE sector_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_name TEXT NOT NULL,
    board_type TEXT NOT NULL,                  -- "concept" | "industry"
    trade_date TEXT NOT NULL,
    change_pct REAL,
    capital_flow REAL,
    up_count INTEGER,
    down_count INTEGER,
    UNIQUE(sector_name, trade_date)
);
```

---

## 5. 调度集成

### 5.1 main.py 改造

在现有 `--schedule` 模式的 `background_tasks` 列表中追加选股任务：

```python
# main.py 约 line 1270
if config.screener_enabled:
    from src.services.stock_screener import StockScreenerPipeline
    screener = StockScreenerPipeline(config)

    def screener_task():
        """每日选股任务"""
        try:
            report = asyncio.run(screener.run(send_notification=True))
            logger.info(f"选股完成: 推荐 {len(report.picks)} 只股票")
        except Exception as e:
            logger.error(f"选股失败: {e}")

    background_tasks.append({
        "task": screener_task,
        "interval_seconds": 86400,          # 每日一次
        "run_immediately": False,
        "name": "daily_stock_screener",
    })
```

### 5.2 独立运行模式

```bash
# 独立运行选股 (不依赖 --schedule)
python main.py --screener

# 选股 + 指定策略
python main.py --screener --strategies forum_hot_screener,volume_breakout

# 选股 dry-run (不推送)
python main.py --screener --dry-run
```

---

## 6. 文件清单

### 6.1 新增文件

| 文件路径 | 职责 | 状态 |
|---------|------|------|
| `src/services/forum_sentiment.py` | NGA 大时代舆情采集服务 | ✅ Phase 1 完成 |
| `src/services/sector_rotation.py` | 板块轮动分析服务 | ✅ Phase 2 完成 |
| `src/services/strategy_filter.py` | 策略量化筛选服务 | ✅ Phase 3 完成 |
| `src/services/ths_screener.py` | 同花顺问财选股服务 | ✅ Phase 3 完成 |
| `src/services/dde_fetcher.py` | 东方财富 DDE 数据获取 | ✅ Phase 3 完成 |
| `src/services/stock_screener.py` | 选股流水线编排 | ✅ Phase 3 完成 |
| `src/repositories/dde_repository.py` | DDE 数据访问层 | ✅ Phase 3 完成 |
| `src/agent/tools/screener_tools.py` | Agent 选股工具集 | ⬜ Phase 5 |
| `src/repositories/screener_repository.py` | 选股结果数据访问层 | ⬜ Phase 4 |
| `templates/report_screener.j2` | 选股报告 Jinja2 模板 | ⬜ Phase 4 |
| `strategies/custom/dde_low_screener.yaml` | DDE 低位选股策略 | ✅ Phase 3 完成 |
| `strategies/custom/forum_hot_screener.yaml` | 示例：论坛热股筛选策略 | ⬜ Phase 4 |
| `strategies/custom/sector_leader.yaml` | 示例：板块龙头策略 | ⬜ Phase 4 |
| `tests/test_sector_rotation.py` | 板块轮动测试（20 用例） | ✅ Phase 2 完成 |
| `tests/services/test_forum_sentiment.py` | 舆情服务测试 | ⬜ Phase 5 |
| `tests/services/test_stock_screener.py` | 选股流水线测试 | ⬜ Phase 5 |

### 6.2 修改文件

| 文件路径 | 改动内容 | 状态 |
|---------|---------|------|
| `src/config.py` | 新增 ~30 个配置字段（`forum_sentiment_*` + `sector_rotation_*` + `sector_weight_*` + `screener_*`） | ✅ Phase 1+2 完成 |
| `.env.example` | 新增论坛舆情 + 板块轮动 + 选股流水线配置说明 | ✅ Phase 1+2 完成 |
| `src/storage.py` | 新增 `ScreenerResultRecord` + `ForumSentimentCacheRecord` + `SectorHistoryRecord` + `StockDdeRecord` | ✅ Phase 1+2+3 完成 |
| `main.py` | 新增 `--screener` CLI 参数 + 调度集成 | ✅ Phase 3 完成 |
| `src/notification.py` | 新增 `generate_screener_report()` 方法 | ⬜ Phase 4 |
| `api/v1/router.py` | 新增选股 API 端点 (可选) | ⬜ Phase 5 |

---

## 7. 实施路线

### Phase 1: 基础能力 ✅ 完成 (2026-06-13)

1. ✅ **Config 扩展** — `src/config.py` 新增 20 个字段（`forum_sentiment_*` + `screener_*`）
2. ✅ **ForumSentimentService** — `src/services/forum_sentiment.py`，NGA 大时代单源
   - NGA 帖子列表抓取（多页，GBK 编码，Cookie 认证）
   - 高楼帖识别 + 最新回复深度抓取
   - 股票代码提取（AkShare 全量 A 股名称映射 + 6 位代码正则）
   - 关键词规则引擎情绪分析
   - 内存缓存 + TTL
3. ✅ **数据库建表** — `src/storage.py` 新增 `ScreenerResultRecord` + `ForumSentimentCacheRecord`
4. ✅ **.env 配置** — `.env.example` + `.env` 写入 NGA Cookie

### Phase 2: 板块分析 ✅ 完成 (2026-06-13)

5. ✅ **SectorRotationService** — `src/services/sector_rotation.py`
   - EastMoney HTTP 直连（`trust_env=False` 绕过代理）获取板块列表 + K线历史 + 成分股
   - akshare 降级路径（`stock_board_concept_hist_em` / `stock_board_industry_hist_em`）
   - 5 维加权强度评分（今日涨幅 / 3日趋势 / 5日趋势 / 资金流向 / 上涨比例）
   - 轮动阶段判断（emerging / accelerating / peaking / declining）
   - 龙头股识别（板块内涨幅 Top 5）
   - 轮动信号 + 市场主线自动生成
   - 内存 TTL 缓存（默认 1 小时）
6. ✅ **sector_history 建表** — `src/storage.py` 新增 `SectorHistoryRecord` ORM 模型
7. ✅ **Config 扩展** — `src/config.py` 新增 9 个 `sector_rotation_*` / `sector_weight_*` 字段
8. ✅ **单元测试** — `tests/test_sector_rotation.py` 20 个用例全部通过（模拟数据验证）

### Phase 3: 策略筛选 ✅ 完成 (2026-06-14)

8. ✅ **StrategyFilterService** — `src/services/strategy_filter.py`，量化条件引擎
9. ✅ **YAML schema 扩展** — screener_conditions 字段（dde_low_screener.yaml）
10. ✅ **编写自定义策略** — DDE 低位选股策略
11. ✅ **THSScreenerService** — `src/services/ths_screener.py`，同花顺问财选股（推荐数据源）
12. ✅ **DdeFetcher** — `src/services/dde_fetcher.py`，东方财富 DDE 数据获取
13. ✅ **DDE 数据表** — `src/storage.py` 新增 `StockDdeRecord`
14. ✅ **依赖安装** — playwright + chromium

#### Phase 3 新增数据源

| 数据源 | 说明 | 优先级 |
|--------|------|--------|
| **同花顺问财（推荐）** | Playwright 模拟浏览器，直接使用同花顺 DDE 计算 | P0 |
| 东方财富 | Playwright 获取全市场资金流，自算 DDE（大单净流入/流通市值） | P1 |

> **设计说明**: 同花顺问财是首选数据源，因为：
> 1. DDE 计算与同花顺 APP 完全一致，无差异
> 2. 支持自然语言查询，灵活扩展
> 3. 结果可直接与用户在手机上看到的一致

### Phase 4: 编排 + 推送（约 2-3 天）⬜

11. **StockScreenerPipeline** — 串联全流程
12. **AI 评分 prompt** — 构建综合评分 prompt
13. **报告模板** — report_screener.j2 + generate_screener_report()
14. **调度集成** — main.py --screener + 定时任务

### Phase 5: 增强 + 测试（约 2-3 天）⬜

15. **Agent Tools** — screener_tools.py 注册
16. **更多论坛源** — 东方财富股吧、淘股吧（可选扩展）
17. **API 端点** — 选股结果查询接口
18. **集成测试** — 端到端选股流程
19. **文档** — 使用指南、策略编写指南

---

## 8. 风险和注意事项

| 风险 | 应对 |
|------|------|
| NGA API 变更/Cookie 失效 | Cookie 过期提示 + 请求频率控制 + 未来可扩展更多源 |
| 舆情数据噪音大 | 多层过滤（垃圾帖/广告）+ 情绪阈值过滤 + 可选 LLM 精筛 |
| 选股结果质量不稳定 | 回测验证机制 + 最低分阈值 + 人工审核模式 (`--dry-run`) |
| 板块数据延迟 | 缓存 TTL 控制 + 标记数据时效 |
| LLM 评分成本 | 可配置关闭 AI 评分（纯量化模式）+ 使用低成本模型 |
| 合规风险 | 仅供个人研究，不做投资建议推送 |
