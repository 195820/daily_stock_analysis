# -*- coding: utf-8 -*-
"""
===================================
NGA 大时代论坛舆情采集服务
===================================

从 NGA 玩家社区大时代板块 (fid=706) 采集股票讨论数据，输出：
- 热门股票列表（按讨论频率排序）
- 情绪评分（关键词规则引擎）
- 热门话题
- 高楼帖深度分析

依赖:
- NGA 登录 Cookie (ngaPassportUid + ngaPassportCid)
- NGA API 返回 GBK 编码 JSON，需特殊处理
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Config

logger = logging.getLogger(__name__)

# ============================================================
# 数据模型
# ============================================================

@dataclass
class ForumHotStock:
    """论坛热门股票"""
    code: str                     # 股票代码 (如 "600519")
    name: str                     # 股票名称 (如 "贵州茅台")
    source: str = "nga_dashidai"  # 来源标识
    heat_score: float = 0.0       # 热度评分 0-100 (讨论量/回复数归一化)
    sentiment_score: float = 0.0  # 情绪评分 -1.0 ~ 1.0 (负面~正面)
    mention_count: int = 0        # 帖子/回复中提及次数
    trend: str = "stable"         # "rising" | "falling" | "stable"
    top_posts: List[Dict] = field(default_factory=list)  # [{title, tid, replies, url}]
    fetched_at: Optional[datetime] = None


@dataclass
class ForumHotSector:
    """论坛热门板块"""
    name: str                     # 板块名称 (如 "稀土")
    mention_count: int = 0        # 帖子/回复中提及次数
    sentiment_score: float = 0.0  # 情绪评分 -1.0 ~ 1.0
    heat_score: float = 0.0       # 热度评分 0-100
    top_posts: List[Dict] = field(default_factory=list)  # [{title, tid, replies}]


@dataclass
class ForumSentimentResult:
    """论坛舆情采集结果"""
    hot_stocks: List[ForumHotStock] = field(default_factory=list)
    hot_sectors: List[ForumHotSector] = field(default_factory=list)  # 板块维度
    market_sentiment: float = 0.0            # 市场整体情绪 -1.0 ~ 1.0
    hot_topics: List[Dict] = field(default_factory=list)  # [{topic, heat, stock_codes}]
    source_status: Dict[str, str] = field(default_factory=dict)
    thread_count: int = 0                    # 扫描的帖子总数
    fetched_at: Optional[datetime] = None


# ============================================================
# 情绪关键词库
# ============================================================

_POSITIVE_WORDS = frozenset({
    "涨停", "利好", "突破", "放量", "龙头", "加仓", "看多", "看涨",
    "大涨", "暴涨", "新高", "反弹", "启动", "主升", "翻倍", "金叉",
    "多头", "强势", "爆发", "拉升", "封板", "牛股", "起飞", "底部放量",
    "资金流入", "主力吸筹", "量价齐升", "强势板块", "超预期",
    "建仓", "抄底", "满仓", "看好", "牛", "稳", "强",
})

_NEGATIVE_WORDS = frozenset({
    "跌停", "利空", "暴跌", "减持", "亏损", "退市", "割肉", "看空",
    "大跌", "崩盘", "新低", "破位", "套牢", "出逃", "死叉",
    "空头", "弱势", "跳水", "砸盘", "熊市", "割韭菜", "爆雷",
    "资金流出", "主力出货", "量价背离", "高估值", "泡沫",
    "清仓", "空仓", "跑路", "看跌", "菜", "亏", "垃圾",
})

# 板块关键词（用于提取帖子中的板块讨论）
# 优先级：资源 > 科技 > 消费 > 金融 > 制造
SECTOR_KEYWORDS = [
    # 资源/有色
    "稀土", "钼", "钨", "小金属", "有色", "锂", "铜", "铝", "黄金", "白银",
    "钴", "镍", "锌", "锡", "锑", "镁", "钛", "铌", "钽", "铟", "锗",
    # 新能源
    "锂电", "锂电池", "储能", "光伏", "风电", "氢能", "新能源车", "充电桩",
    # 科技
    "AI", "人工智能", "芯片", "半导体", "光模块", "算力", "数据中心",
    "量子", "卫星", "通信", "5G", "6G", "光纤", "MLCC", "电容",
    # 消费/医药
    "白酒", "食品", "医药", "医疗", "生物", "疫苗", "创新药",
    # 制造/军工
    "军工", "航天", "航空", "船舶", "汽车", "机器人",
    # 其他
    "金融", "券商", "银行", "保险", "地产", "基建", "钢铁", "煤炭",
]


# ============================================================
# NGA HTTP 客户端
# ============================================================

class _NgaClient:
    """NGA API HTTP 客户端，处理 GBK 编码和 Cookie 认证。

    支持自动续期：Cookie 失效时用用户名密码重新登录。
    """

    BASE_URL = "https://bbs.nga.cn"
    _REQUEST_TIMEOUT = 15
    _RELOGIN_MAX_RETRIES = 2

    def __init__(
        self,
        uid: str,
        cid: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._username = username
        self._password = password
        self._uid = uid
        self._cid = cid
        self._session = self._build_session(uid, cid)

    def _build_session(self, uid: str, cid: str) -> requests.Session:
        """构建带 Cookie 和重试策略的 Session。"""
        session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Referer": f"{self.BASE_URL}/",
        })
        session.cookies.set("ngaPassportUid", uid, domain=".nga.cn")
        session.cookies.set("ngaPassportCid", cid, domain=".nga.cn")
        return session

    @property
    def can_relogin(self) -> bool:
        return bool(self._username and self._password)

    def relogin(self) -> bool:
        """用用户名密码重新登录，刷新 Cookie。返回是否成功。"""
        if not self.can_relogin:
            logger.warning("[NGA] 无法续期：未配置用户名/密码")
            return False

        for attempt in range(1, self._RELOGIN_MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    f"{self.BASE_URL}/accounts/login",
                    data={
                        "name": self._username,
                        "password": self._password,
                        "cid": "nginx",
                    },
                    headers={"User-Agent": self._session.headers["User-Agent"]},
                    timeout=self._REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    # NGA 登录成功后 Set-Cookie 里带 ngaPassportUid/Cid
                    for cookie in resp.cookies:
                        if cookie.name in ("ngaPassportUid", "ngaPassportCid"):
                            self._session.cookies.set(
                                cookie.name, cookie.value, domain=".nga.cn",
                            )
                    # 验证新 Cookie 有效
                    new_uid = self._session.cookies.get("ngaPassportUid", domain=".nga.cn")
                    if new_uid:
                        self._uid = new_uid
                        logger.info("[NGA] 自动续期成功 (attempt %d)", attempt)
                        return True
                logger.warning("[NGA] 登录返回 status=%d (attempt %d)", resp.status_code, attempt)
            except Exception as exc:
                logger.warning("[NGA] 登录异常: %s (attempt %d)", exc, attempt)
            time.sleep(1)

        logger.error("[NGA] 自动续期失败，需手动更新 Cookie")
        return False

    def fetch_thread_list(self, stid: int, page: int = 1) -> Dict[str, Any]:
        """获取帖子合集的帖子列表。返回解析后的 JSON dict。"""
        url = f"{self.BASE_URL}/thread.php"
        params = {"stid": stid, "page": page, "__output": 8}
        return self._get_json(url, params)

    def fetch_thread_content(self, tid: int, page: int = 1) -> Dict[str, Any]:
        """获取帖子内容（主楼 + 回复）。返回解析后的 JSON dict。"""
        url = f"{self.BASE_URL}/read.php"
        params = {"tid": tid, "page": page, "__output": 8}
        return self._get_json(url, params)

    def _get_json(self, url: str, params: Dict) -> Dict[str, Any]:
        """GET 请求 → GBK 解码 → JSON 解析。认证失败时自动续期重试。"""
        resp = self._session.get(url, params=params, timeout=self._REQUEST_TIMEOUT)
        resp.encoding = "gbk"

        # 检测认证失败 → 自动续期
        if resp.status_code in (401, 403) or self._is_auth_error(resp):
            if self.can_relogin and self.relogin():
                resp = self._session.get(url, params=params, timeout=self._REQUEST_TIMEOUT)
                resp.encoding = "gbk"

        if resp.status_code != 200:
            raise RuntimeError(f"NGA HTTP {resp.status_code}: {url}")
        # strict=False 容忍帖子内容中的控制字符
        data = json.loads(resp.text, strict=False)
        if "error" in data and "data" not in data:
            err = data["error"]
            err_msg = err.get("1", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"NGA API error: {err_msg[:200]}")
        return data

    @staticmethod
    def _is_auth_error(resp: requests.Response) -> bool:
        """检测 NGA 返回的 JSON 中是否包含认证错误。"""
        try:
            text = resp.text[:500]
            return "登录" in text or "cookie" in text.lower() or "-1001" in text
        except Exception:
            return False


# ============================================================
# 股票代码提取
# ============================================================

# A 股代码正则：6 位数字，前面可能有 SH/SZ 前缀
_CODE_RE = re.compile(r'\b(?:SH|SZ|sh|sz)?(\d{6})\b')

# 延迟加载的名称→代码反向映射
_name_to_code_cache: Optional[Dict[str, str]] = None
_name_to_code_lock = threading.Lock()
_akshare_name_cache: Optional[Dict[str, str]] = None
_akshare_name_cache_ts: float = 0.0
_AKSHARE_NAME_CACHE_TTL = 3600  # 1小时


def _get_name_to_code_map() -> Dict[str, str]:
    """构建名称→代码的反向映射（懒加载，线程安全）。

    优先使用 AkShare 全量 A 股名称（覆盖广），
    回退到 STOCK_NAME_MAP（~90 只热门股）。
    """
    global _name_to_code_cache, _akshare_name_cache, _akshare_name_cache_ts
    if _name_to_code_cache is not None:
        return _name_to_code_cache
    with _name_to_code_lock:
        if _name_to_code_cache is not None:
            return _name_to_code_cache

        # 尝试 AkShare 全量数据
        try:
            from src.services.name_to_code_resolver import _get_akshare_name_to_code
            akshare_map = _get_akshare_name_to_code()
            if akshare_map and len(akshare_map) > 50:
                _name_to_code_cache = akshare_map
                logger.info(f"ForumSentiment: AkShare 名称映射加载 {len(akshare_map)} 条")
                return _name_to_code_cache
        except Exception as e:
            logger.debug(f"ForumSentiment: AkShare 名称加载失败: {e}")

        # 回退到 STOCK_NAME_MAP
        from src.data.stock_mapping import STOCK_NAME_MAP
        name_to_code: Dict[str, str] = {}
        for code, name in STOCK_NAME_MAP.items():
            if code.isdigit() and len(code) == 6:
                name_to_code[name] = code
        _name_to_code_cache = name_to_code
        return name_to_code


def extract_stock_references(text: str) -> List[Tuple[str, str]]:
    """
    从文本中提取股票代码引用。

    返回: [(code, name), ...]  去重后的列表
    - 匹配 6 位数字代码 → 查名称映射
    - 匹配已知股票名称 → 查代码
    """
    results: Dict[str, str] = {}  # code → name

    # 1. 正则匹配 6 位数字代码
    name_map = _get_name_to_code_map()
    code_to_name = {v: k for k, v in name_map.items()}

    for m in _CODE_RE.finditer(text):
        code = m.group(1)
        if code in code_to_name:
            results[code] = code_to_name[code]
        elif code not in results:
            results[code] = code  # 未知代码，用代码本身

    # 2. 匹配已知股票名称
    for name, code in name_map.items():
        if name in text and len(name) >= 2:
            results[code] = name

    return [(code, name) for code, name in results.items()]


def analyze_sentiment(text: str) -> float:
    """
    关键词规则引擎情绪分析。

    返回: -1.0 (极度负面) ~ 1.0 (极度正面)
    """
    pos_count = sum(1 for w in _POSITIVE_WORDS if w in text)
    neg_count = sum(1 for w in _NEGATIVE_WORDS if w in text)
    total = pos_count + neg_count
    if total == 0:
        return 0.0
    return (pos_count - neg_count) / total


# ============================================================
# ForumSentimentService
# ============================================================

class ForumSentimentService:
    """
    NGA 大时代论坛舆情采集服务。

    Usage::

        svc = ForumSentimentService(config)
        if svc.is_available:
            result = svc.fetch_hot_stocks()
    """

    _SOURCE_NAME = "nga_dashidai"

    def __init__(self, config: Config):
        self._config = config
        self._client: Optional[_NgaClient] = None
        self._cache: Dict[str, Tuple[ForumSentimentResult, float]] = {}
        self._cache_lock = threading.RLock()

        if config.forum_sentiment_nga_uid and config.forum_sentiment_nga_cid:
            self._client = _NgaClient(
                uid=config.forum_sentiment_nga_uid,
                cid=config.forum_sentiment_nga_cid,
                username=config.forum_sentiment_nga_username,
                password=config.forum_sentiment_nga_password,
            )

    @property
    def is_available(self) -> bool:
        """NGA 客户端已配置（有 Cookie）。"""
        return self._client is not None

    def fetch_hot_stocks(
        self,
        *,
        top_n: Optional[int] = None,
        cache_ttl: Optional[int] = None,
    ) -> ForumSentimentResult:
        """
        主入口：采集 NGA 大时代论坛热门股票。

        流程:
        1. 抓取帖子列表（多页）
        2. 识别高楼帖，额外抓取最新回复
        3. 从标题和内容中提取股票代码
        4. 计算每只股票的热度和情绪
        5. 排序返回 Top N
        """
        if not self.is_available:
            logger.warning("ForumSentimentService 不可用: 缺少 NGA Cookie 配置")
            return ForumSentimentResult(
                source_status={self._SOURCE_NAME: "unconfigured"},
                fetched_at=datetime.now(),
            )

        top_n = top_n or self._config.forum_sentiment_top_n
        cache_ttl = cache_ttl or self._config.forum_sentiment_cache_ttl

        # 检查内存缓存
        cache_key = f"hot_stocks_{top_n}"
        cached = self._get_cache(cache_key, cache_ttl)
        if cached is not None:
            return cached

        try:
            result = self._do_fetch(top_n)
            result.source_status[self._SOURCE_NAME] = "ok"
        except Exception as e:
            logger.error(f"NGA 舆情采集失败: {e}")
            result = ForumSentimentResult(
                source_status={self._SOURCE_NAME: f"error: {e}"},
                fetched_at=datetime.now(),
            )

        self._set_cache(cache_key, result)
        return result

    def get_stock_sentiment(self, stock_code: str) -> Optional[Dict]:
        """查询单只股票的舆情摘要（供 Agent Tool 调用）。"""
        result = self.fetch_hot_stocks()
        for stock in result.hot_stocks:
            if stock.code == stock_code:
                return {
                    "code": stock.code,
                    "name": stock.name,
                    "heat_score": stock.heat_score,
                    "sentiment_score": stock.sentiment_score,
                    "mention_count": stock.mention_count,
                    "trend": stock.trend,
                    "top_posts": stock.top_posts,
                    "source": stock.source,
                }
        return None

    # ============================================================
    # 内部实现
    # ============================================================

    def _do_fetch(self, top_n: int) -> ForumSentimentResult:
        """执行实际的采集和分析流程。"""
        cfg = self._config
        stid = cfg.forum_sentiment_nga_stid
        pages = cfg.forum_sentiment_pages
        high_rise_threshold = cfg.forum_sentiment_high_rise_threshold
        high_rise_extra = cfg.forum_sentiment_high_rise_extra_pages

        # Step 1: 抓取帖子列表
        all_threads: List[Dict] = []
        for page in range(1, pages + 1):
            try:
                data = self._client.fetch_thread_list(stid, page)
                thread_dict = data.get("data", {}).get("__T", {})
                for _idx, t in thread_dict.items():
                    if isinstance(t, dict) and t.get("tid"):
                        all_threads.append(t)
            except Exception as e:
                logger.warning(f"NGA 帖子列表第 {page} 页获取失败: {e}")
                break
            time.sleep(0.3)  # 请求间隔

        if not all_threads:
            return ForumSentimentResult(
                source_status={self._SOURCE_NAME: "empty"},
                fetched_at=datetime.now(),
            )

        logger.info(f"NGA 采集到 {len(all_threads)} 条帖子")

        # Step 2: 识别高楼帖，抓取额外内容
        high_rise_threads = [
            t for t in all_threads
            if t.get("replies", 0) >= high_rise_threshold
        ]
        high_rise_contents: Dict[int, List[Dict]] = {}

        for t in high_rise_threads[:10]:  # 最多处理 10 个高楼帖
            tid = t["tid"]
            total_replies = t.get("replies", 0)
            # 计算最新页码
            last_page = max(1, (total_replies + 19) // 20)
            pages_to_fetch = range(
                max(1, last_page - high_rise_extra + 1),
                last_page + 1,
            )
            replies = []
            for p in pages_to_fetch:
                try:
                    data = self._client.fetch_thread_content(tid, p)
                    r_data = data.get("data", {}).get("__R", {})
                    for _pid, r in r_data.items():
                        if isinstance(r, dict) and r.get("content"):
                            replies.append(r)
                except Exception as e:
                    logger.warning(f"高楼帖 tid={tid} 第 {p} 页获取失败: {e}")
                time.sleep(0.3)
            high_rise_contents[tid] = replies

        # Step 3: 从标题和内容中提取股票代码和板块关键词
        # stock_code → {names: set, mentions: int, sentiment_sum: float, posts: list}
        stock_data: Dict[str, Dict] = {}
        # sector_name → {mentions: int, sentiment_sum: float, sentiment_count: int, posts: list}
        sector_data: Dict[str, Dict] = {}
        all_sentiments: List[float] = []

        def extract_sector_refs(text: str) -> List[str]:
            """从文本中提取板块关键词"""
            matched = []
            for kw in SECTOR_KEYWORDS:
                if kw in text:
                    matched.append(kw)
            return matched

        def add_sector_ref(sector: str, sentiment: float, tid: int, subject: str, replies: int):
            if sector not in sector_data:
                sector_data[sector] = {
                    "mentions": 0,
                    "sentiment_sum": 0.0,
                    "sentiment_count": 0,
                    "posts": [],
                }
            sd = sector_data[sector]
            sd["mentions"] += 1
            sd["sentiment_sum"] += sentiment
            sd["sentiment_count"] += 1
            if len(sd["posts"]) < 3:
                sd["posts"].append({
                    "title": subject[:80],
                    "tid": tid,
                    "replies": replies,
                })

        for t in all_threads:
            subject = t.get("subject", "")
            tid = t.get("tid", 0)
            replies_count = t.get("replies", 0)

            # 分析标题
            refs = extract_stock_references(subject)
            title_sentiment = analyze_sentiment(subject)
            all_sentiments.append(title_sentiment)

            # 提取板块关键词
            sector_refs = extract_sector_refs(subject)
            for sector in sector_refs:
                add_sector_ref(sector, title_sentiment, tid, subject, replies_count)

            for code, name in refs:
                if code not in stock_data:
                    stock_data[code] = {
                        "name": name,
                        "mentions": 0,
                        "sentiment_sum": 0.0,
                        "sentiment_count": 0,
                        "posts": [],
                        "total_heat": 0,
                    }
                sd = stock_data[code]
                sd["mentions"] += 1
                sd["sentiment_sum"] += title_sentiment
                sd["sentiment_count"] += 1
                sd["total_heat"] += replies_count
                if len(sd["posts"]) < 3:
                    sd["posts"].append({
                        "title": subject[:80],
                        "tid": tid,
                        "replies": replies_count,
                        "url": f"https://bbs.nga.cn/read.php?tid={tid}",
                    })

            # 分析高楼帖回复内容
            if tid in high_rise_contents:
                for r in high_rise_contents[tid]:
                    content = r.get("content", "")
                    r_refs = extract_stock_references(content)
                    r_sentiment = analyze_sentiment(content)
                    # 提取板块关键词
                    r_sector_refs = extract_sector_refs(content)
                    for sector in r_sector_refs:
                        add_sector_ref(sector, r_sentiment, tid, subject, replies_count)
                    for code, name in r_refs:
                        if code not in stock_data:
                            stock_data[code] = {
                                "name": name,
                                "mentions": 0,
                                "sentiment_sum": 0.0,
                                "sentiment_count": 0,
                                "posts": [],
                                "total_heat": 0,
                            }
                        sd = stock_data[code]
                        sd["mentions"] += 1
                        sd["sentiment_sum"] += r_sentiment
                        sd["sentiment_count"] += 1

        # Step 4: 计算热度和情绪评分，排序
        if not stock_data:
            return ForumSentimentResult(
                hot_stocks=[],
                market_sentiment=sum(all_sentiments) / len(all_sentiments) if all_sentiments else 0.0,
                thread_count=len(all_threads),
                source_status={self._SOURCE_NAME: "ok"},
                fetched_at=datetime.now(),
            )

        max_mentions = max(sd["mentions"] for sd in stock_data.values()) or 1
        max_heat = max(sd["total_heat"] for sd in stock_data.values()) or 1

        hot_stocks: List[ForumHotStock] = []
        for code, sd in stock_data.items():
            heat = (sd["mentions"] / max_mentions * 50 +
                    sd["total_heat"] / max_heat * 50)
            avg_sentiment = (sd["sentiment_sum"] / sd["sentiment_count"]
                           if sd["sentiment_count"] > 0 else 0.0)
            hot_stocks.append(ForumHotStock(
                code=code,
                name=sd["name"],
                heat_score=round(heat, 1),
                sentiment_score=round(avg_sentiment, 2),
                mention_count=sd["mentions"],
                trend="rising" if avg_sentiment > 0.2 else ("falling" if avg_sentiment < -0.2 else "stable"),
                top_posts=sd["posts"],
                fetched_at=datetime.now(),
            ))

        hot_stocks.sort(key=lambda s: (s.heat_score + abs(s.sentiment_score) * 30), reverse=True)
        hot_stocks = hot_stocks[:top_n]

        # Step 4.5: 生成热门板块列表
        hot_sectors: List[ForumHotSector] = []
        if sector_data:
            max_sector_mentions = max(sd["mentions"] for sd in sector_data.values()) or 1
            for name, sd in sector_data.items():
                heat = sd["mentions"] / max_sector_mentions * 100
                avg_sentiment = (sd["sentiment_sum"] / sd["sentiment_count"]
                               if sd["sentiment_count"] > 0 else 0.0)
                hot_sectors.append(ForumHotSector(
                    name=name,
                    mention_count=sd["mentions"],
                    sentiment_score=round(avg_sentiment, 2),
                    heat_score=round(heat, 1),
                    top_posts=sd["posts"],
                ))
            hot_sectors.sort(key=lambda s: (s.heat_score + abs(s.sentiment_score) * 30), reverse=True)
            hot_sectors = hot_sectors[:top_n]
            logger.info(f"NGA 板块提取: {len(hot_sectors)} 个热门板块, "
                       f"Top3: {', '.join(s.name for s in hot_sectors[:3])}")

        # Step 5: 生成热门话题
        hot_topics = self._generate_hot_topics(stock_data, all_threads)

        market_sentiment = (
            sum(all_sentiments) / len(all_sentiments) if all_sentiments else 0.0
        )

        return ForumSentimentResult(
            hot_stocks=hot_stocks,
            hot_sectors=hot_sectors,
            market_sentiment=round(market_sentiment, 2),
            hot_topics=hot_topics,
            thread_count=len(all_threads),
            source_status={self._SOURCE_NAME: "ok"},
            fetched_at=datetime.now(),
        )

    def _generate_hot_topics(
        self,
        stock_data: Dict[str, Dict],
        threads: List[Dict],
    ) -> List[Dict]:
        """生成热门话题摘要。"""
        # 按提及次数排序取 Top 5 股票
        sorted_stocks = sorted(
            stock_data.items(),
            key=lambda x: x[1]["mentions"],
            reverse=True,
        )[:5]

        topics = []
        for code, sd in sorted_stocks:
            topics.append({
                "topic": f"{sd['name']}({code})",
                "stock_codes": [code],
                "heat": sd["mentions"],
                "avg_sentiment": round(
                    sd["sentiment_sum"] / sd["sentiment_count"]
                    if sd["sentiment_count"] > 0 else 0.0,
                    2,
                ),
            })
        return topics

    # ============================================================
    # 缓存
    # ============================================================

    def _get_cache(self, key: str, ttl: int) -> Optional[ForumSentimentResult]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            result, ts = entry
            if time.time() - ts > ttl:
                del self._cache[key]
                return None
            return result

    def _set_cache(self, key: str, result: ForumSentimentResult) -> None:
        with self._cache_lock:
            self._cache[key] = (result, time.time())
