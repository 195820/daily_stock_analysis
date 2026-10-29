# -*- coding: utf-8 -*-
"""
=============================================
Node 4 新闻采集模块（v2：5 源推送型）
=============================================

数据源（均为推送型，无需搜索 query）：

  S1 新浪文章流 — feed.sina.com.cn（21+ 小时归档，覆盖全天）
  S2 财联社电报 — cls.cn/v1（签名认证，A 股核心快讯，~1 小时窗口）
  S3 华尔街见闻 — api-one.wallstcn.com（全球宏观，免签）
  S4 东财快讯   — np-listapi.eastmoney.com（国内媒体聚合，免签）
  S5 NewsNow    — newsnow.busiyi.world（8 平台热榜：微博/百度/知乎/澎湃/头条/财联社/抖音/B站）

设计参考：pre-market-brief 项目 + TrendRadar（60k stars）

已知限制：
  - 5 源全是滚动窗口，中热度消息（如厄尔尼诺形成）滚出后永久丢失
  - 需持久化层（SQLite）解决事件生命周期追踪问题

反爬策略：
  - CLS: 专用限流器（0.8s 间隔 + 抖动）+ UA 轮换 + 签名
  - 其他源: 通用 UA + Referer + 独立超时
  - 所有源独立 try/except，单源失败不阻塞
"""

import hashlib
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.got.news_url import canonicalize_url

logger = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================

TITLE_SIMILARITY_THRESHOLD = 0.45
MAX_MERGED_NEWS = 150

# CLS 限流参数
CLS_MIN_INTERVAL = 0.8
CLS_JITTER_MIN = 0.1
CLS_JITTER_MAX = 0.4
CLS_TIMEOUT = 15

# 通用超时
DEFAULT_TIMEOUT = 12

# =============================================================================
# UA 池
# =============================================================================

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0",
]

# =============================================================================
# 新闻权重计算（参考 pre-market-brief）
# =============================================================================

# 来源权重（0-30 分）
_SOURCE_WEIGHTS = {
    "sina": 22,          # 归档流，覆盖面广
    "cls": 20,           # A 股核心，但高频低价值条目多
    "wallstreetcn": 25,  # 国际视野，信息密度高
    "eastmoney": 20,     # 散户关注
    "newsnow": 26,       # 跨平台热榜，多源交叉验证
    "nga_forum": 16,     # 散户论坛帖子，信息密度低但有早期信号
    "ths_hot": 28,       # 同花顺热榜，投资者关注焦点，高价值
    "cctv": 28,          # 权威
    "caixin": 24,        # 专业
}


def _calculate_weight(item: Dict) -> float:
    """信息源质量分（非事件价值）：来源 40% + 时效 35% + 多源确认 25%。

    2026-10-22 删除「热度」项：实测该把一条个股涨停播报（22.4）排在产业级开源
    发布/全球气候异常（各 11.6）之前 1.9 倍——它度量媒体热度，不度量事件对
    A 股的影响深度。事件价值只由 LLM 全量初筛判定（见 news_analysis
    `_shortlist_titles`），本分数仅用于展示顺序与 NGA 保底，不决定 LLM 能否看到。
    """
    source_score = _SOURCE_WEIGHTS.get(item.get("source_type", "other"), 10)

    time_score = 10  # 解析不出时间 → 默认中等
    pub_date = item.get("published_date", "")
    if pub_date:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                news_time = datetime.strptime(pub_date[:19], fmt)
            except ValueError:
                continue
            hours_diff = (datetime.now() - news_time).total_seconds() / 3600
            if hours_diff <= 1:
                time_score = 25
            elif hours_diff <= 4:
                time_score = 20
            elif hours_diff <= 8:
                time_score = 15
            elif hours_diff <= 16:
                time_score = 10
            else:
                time_score = 5
            break

    count_score = min(item.get("source_count", 1) * 10, 30)

    return source_score * 0.40 + time_score * 0.35 + count_score * 0.25


# =============================================================================
# S1: 新浪文章流（21+ 小时归档）
# =============================================================================

_SINA_FEEDS = [
    {"pageid": "153", "lid": "2516", "name": "财经频道", "num": 50},
    {"pageid": "153", "lid": "2517", "name": "股票频道", "num": 50},
    {"pageid": "153", "lid": "2515", "name": "国内财经", "num": 30},
    {"pageid": "153", "lid": "2518", "name": "国际财经", "num": 30},
]


def fetch_sina_news() -> List[Dict[str, Any]]:
    """
    新浪文章归档流（feed.sina.com.cn）。

    4 个栏目并发采集，每个 30-50 条，覆盖 21+ 小时。
    这是覆盖面最广的单一数据源。
    """
    results: List[Dict] = []

    for feed in _SINA_FEEDS:
        try:
            resp = requests.get(
                "https://feed.sina.com.cn/api/roll/get",
                params={
                    "pageid": feed["pageid"],
                    "lid": feed["lid"],
                    "num": feed["num"],
                    "page": 1,
                    "versionNumber": "1.2.4",
                    "encode": "utf-8",
                },
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                timeout=DEFAULT_TIMEOUT,
            )
            data = resp.json()
            status = data.get("result", {}).get("status", {})
            if isinstance(status, dict) and status.get("code") != 0:
                continue

            items = data.get("result", {}).get("data", [])
            for item in items:
                title = (item.get("title") or "").strip()
                if not title or len(title) < 8:
                    continue

                ctime = item.get("ctime", "")
                pub_date = ""
                if ctime and str(ctime).isdigit():
                    pub_date = datetime.fromtimestamp(
                        int(ctime)
                    ).strftime("%Y-%m-%d %H:%M:%S")

                results.append({
                    "title": title,
                    "snippet": title[:150],
                    "source": "sina",
                    "source_type": "sina",
                    "url": item.get("url", ""),
                    "published_date": pub_date,
                })
        except Exception as exc:
            logger.warning("[news_feeds] 新浪 %s 失败: %s", feed["name"], exc)

    logger.info("[news_feeds] S1 新浪文章流: %d 条", len(results))
    return results


# =============================================================================
# S2: 财联社电报（签名认证）
# =============================================================================

_CLS_URL = "https://www.cls.cn/v1/roll/get_roll_list"
_CLS_PARAMS = {"app": "CailianpressWeb", "os": "web", "sv": "8.7.9"}

_cls_lock = threading.Lock()
_cls_last_time = 0.0
_cls_session: Optional[requests.Session] = None
_cls_session_lock = threading.Lock()


def _cls_sign(params: Dict[str, str]) -> str:
    """CLS 签名: MD5(SHA1(sorted_query_string))"""
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sha1 = hashlib.sha1(qs.encode("utf-8")).hexdigest()
    return hashlib.md5(sha1.encode("utf-8")).hexdigest()


def _cls_get(url: str, params: Dict) -> requests.Response:
    """CLS HTTP GET（限流 + 签名）。"""
    global _cls_last_time

    with _cls_lock:
        now = time.time()
        elapsed = now - _cls_last_time
        min_wait = CLS_MIN_INTERVAL + random.uniform(CLS_JITTER_MIN, CLS_JITTER_MAX)
        if elapsed < min_wait:
            time.sleep(min_wait - elapsed)
        _cls_last_time = time.time()

    global _cls_session
    if _cls_session is None:
        with _cls_session_lock:
            if _cls_session is None:
                _cls_session = requests.Session()
                retry = Retry(
                    total=3, backoff_factor=1,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET"],
                )
                adapter = HTTPAdapter(max_retries=retry, pool_maxsize=3)
                _cls_session.mount("https://", adapter)

    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Referer": "https://www.cls.cn/telegraph",
    }
    resp = _cls_session.get(url, params=params, timeout=CLS_TIMEOUT, headers=headers)
    resp.raise_for_status()
    return resp


def fetch_cls_telegraph(limit: int = 60) -> List[Dict[str, Any]]:
    """
    财联社电报（签名认证，分页拉取）。

    覆盖范围约 1-3 小时（CLS 是高频快讯流）。
    """
    all_items: List[Dict] = []
    last_time = 0

    for page in range(10):
        if len(all_items) >= limit:
            break

        params = {
            **_CLS_PARAMS,
            "rn": str(min(20, limit - len(all_items))),
            "last_time": str(last_time),
        }
        params["sign"] = _cls_sign(params)

        try:
            resp = _cls_get(_CLS_URL, params)
            data = resp.json()
        except Exception as exc:
            logger.warning("[news_feeds] CLS 第 %d 页失败: %s", page + 1, exc)
            break

        if data.get("errno") != 0:
            logger.warning("[news_feeds] CLS errno=%s", data.get("errno"))
            break

        roll = data.get("data", {}).get("roll_data", [])
        if not roll:
            break

        for item in roll:
            title = (item.get("title") or "").strip()
            content = (item.get("content") or "").strip()
            if not title and content:
                title = content[:60].replace("\n", " ")
            if not title:
                continue

            ctime = item.get("ctime", 0)
            pub_date = ""
            if ctime:
                pub_date = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")

            all_items.append({
                "title": title,
                # P0-2：正文不截断（CLS API 本就返回全文；旧版截 150 字让细读只见摘要尾巴）
                "snippet": content if content else title[:150],
                "source": "cls",
                "source_type": "cls",
                "url": "",
                "published_date": pub_date,
                "level": item.get("level", ""),
            })

        last_time = roll[-1].get("ctime", 0)
        if not last_time:
            break

    logger.info("[news_feeds] S2 CLS 电报: %d 条", len(all_items))
    return all_items[:limit]


# =============================================================================
# S3: 华尔街见闻（免签）
# =============================================================================


def fetch_wallstreetcn_news(limit: int = 30) -> List[Dict[str, Any]]:
    """华尔街见闻实时快讯（免签）。"""
    try:
        resp = requests.get(
            "https://api-one.wallstcn.com/apiv1/content/lives",
            params={"channel": "global-channel", "limit": limit},
            headers={
                "User-Agent": random.choice(_USER_AGENTS),
                "Referer": "https://wallstreetcn.com/",
                "Origin": "https://wallstreetcn.com",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        logger.warning("[news_feeds] 华尔街见闻失败: %s", exc)
        return []

    items = data.get("data", {}).get("items", [])
    results = []
    for item in items:
        text = (item.get("content_text") or "").strip()
        if not text:
            continue

        display_time = item.get("display_time", 0)
        pub_date = ""
        if display_time:
            pub_date = datetime.fromtimestamp(display_time).strftime("%Y-%m-%d %H:%M:%S")

        is_important = item.get("is_important", False)
        uri = item.get("uri", "")

        results.append({
            "title": text[:100],
            # P0-2：正文不截断（快讯流无标题字段，title 本就是截断产物；snippet 保留全文）
            "snippet": text,
            "source": "wallstreetcn",
            "source_type": "wallstreetcn",
            "url": f"https://wallstreetcn.com/articles/{uri}" if uri else "",
            "published_date": pub_date,
            "level": "A" if is_important else "",
        })

    logger.info("[news_feeds] S3 华尔街见闻: %d 条", len(results))
    return results


# =============================================================================
# S4: 东财快讯（免签）
# =============================================================================


def fetch_eastmoney_kuaixun(limit: int = 30) -> List[Dict[str, Any]]:
    """东财快讯（np-listapi，免签）。"""
    try:
        resp = requests.get(
            "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns",
            params={
                "client": "web",
                "biz": "web_home_channel",
                "column": "350,35,466,467",
                "order": 1,
                "needInteractData": 0,
                "page_index": 1,
                "page_size": limit,
                "req_trace": str(uuid.uuid4()),
            },
            headers={"User-Agent": random.choice(_USER_AGENTS)},
            timeout=DEFAULT_TIMEOUT,
        )
        data = resp.json()
    except Exception as exc:
        logger.warning("[news_feeds] 东财快讯失败: %s", exc)
        return []

    items = data.get("data", {}).get("list", [])
    results = []
    for item in items:
        title = (item.get("title") or "").strip()
        if not title:
            continue

        results.append({
            "title": title,
            "snippet": (item.get("summary") or title)[:150],
            "source": item.get("mediaName", "eastmoney"),
            "source_type": "eastmoney",
            "url": item.get("uniqueUrl", ""),
            "published_date": item.get("showTime", ""),
        })

    logger.info("[news_feeds] S4 东财快讯: %d 条", len(results))
    return results


# =============================================================================
# S5: NewsNow 国内热榜（免费公开 API，覆盖微博/百度/知乎/澎湃/头条等）
# =============================================================================

_NEWSNOW_API = "https://newsnow.busiyi.world/api/s"
_NEWSNOW_PLATFORMS = [
    ("weibo", "微博"),
    ("baidu", "百度热搜"),
    ("zhihu", "知乎"),
    ("thepaper", "澎湃新闻"),
    ("toutiao", "今日头条"),
    ("cls-hot", "财联社热门"),
    ("douyin", "抖音"),
    ("bilibili-hot-search", "B站热搜"),
]


def fetch_newsnow_hot() -> List[Dict[str, Any]]:
    """
    NewsNow 国内热榜聚合（免费公开 API）。

    覆盖微博/百度/知乎/澎湃/头条/财联社/华尔街见闻/抖音/B站。
    返回跨平台热榜条目，source_count 反映被几个平台同时报道。
    """
    results: List[Dict] = []

    for pid, name in _NEWSNOW_PLATFORMS:
        try:
            resp = requests.get(
                _NEWSNOW_API,
                params={"id": pid, "latest": ""},
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                timeout=DEFAULT_TIMEOUT,
            )
            data = resp.json()
            items = data.get("items", [])

            for idx, item in enumerate(items, 1):
                title = (item.get("title") or "").strip()
                if not title:
                    continue

                url = item.get("url", "")
                results.append({
                    "title": title,
                    "snippet": title[:150],
                    "source": name,
                    "source_type": "newsnow",
                    "url": url,
                    "published_date": "",
                    "platform": pid,
                    # P0-1：API item 无 rank 字段（实测只有 id/title/url/mobileUrl/extra），
                    # 响应数组顺序即榜单顺序 → 序号补名次
                    "rank": idx,
                })
        except Exception as exc:
            logger.warning("[news_feeds] NewsNow %s 失败: %s", name, exc)

    logger.info("[news_feeds] S5 NewsNow 热榜: %d 条（%d 平台）", len(results), len(_NEWSNOW_PLATFORMS))
    return results


# =============================================================================
# 合并 + 去重 + 排序
# =============================================================================


def _platform_set(item: Dict) -> set:
    """条目已知平台集合：合并列表 `platforms` 优先，回退单值 `platform`。"""
    merged = item.get("platforms")
    if merged:
        return set(merged)
    single = item.get("platform")
    return {single} if single else set()


def _merge_attention_meta(keep: Dict, drop: Dict) -> None:
    """被去重吞掉的条目，把热榜元数据（platform/rank/heat）并入保留条目。

    P0-1：平台扩散（微博/百度/知乎同题）此前在 `_deduplicate` 里被整条丢弃，
    `platform` 只在首条保留。这里只做**观测聚合**：不写 `source_count`（平台扩散
    ≠ 独立媒体确认，`source_count` 口径由 `_count_cross_source` 独有）。
    """
    platforms = _platform_set(keep) | _platform_set(drop)
    if platforms:
        keep["platforms"] = sorted(platforms)

    ranks = [
        r for r in (keep.get("rank"), drop.get("rank"))
        if isinstance(r, int) and r > 0
    ]
    if ranks:
        keep["rank"] = min(ranks)  # 数值更小 = 榜单更靠前

    heats = [
        h for h in (keep.get("heat"), drop.get("heat"))
        if isinstance(h, (int, float)) and h
    ]
    if heats:
        keep["heat"] = max(heats)


def _deduplicate(news_items: List[Dict]) -> List[Dict]:
    """按 URL 指纹 + title 相似度去重（P1-1：URL 先判，换标题同链接不再重复入库）。"""
    if not news_items:
        return []

    unique: List[Dict] = []
    seen_urls: dict[str, Dict] = {}
    for item in news_items:
        title = item.get("title", "")
        if not title:
            continue
        cand = canonicalize_url(item.get("url", "") or "")
        if cand:
            owner = seen_urls.get(cand)
            if owner is not None:
                _merge_attention_meta(owner, item)
                continue
        is_dup = False
        for existing in unique:
            ratio = SequenceMatcher(None, title, existing["title"]).ratio()
            if ratio >= TITLE_SIMILARITY_THRESHOLD:
                is_dup = True
                _merge_attention_meta(existing, item)
                # 本条被标题判重吞掉，但它携带的 URL 归属保留条目：
                # 否则同一 URL 换标题再来一条时 URL 判据看不到 owner，会重复入库
                if cand:
                    seen_urls.setdefault(cand, existing)
                break
        if is_dup:
            continue
        unique.append(item)
        if cand:
            seen_urls[cand] = item

    if len(unique) < len(news_items):
        logger.info(
            "[news_feeds] 去重: %d → %d 条", len(news_items), len(unique),
        )
    return unique


def _count_cross_source(
    news_items: List[Dict],
    sina: List[Dict], wallstreetcn: List[Dict],
    cls_news: List[Dict], eastmoney: List[Dict],
    newsnow: List[Dict], nga_forum: Optional[List[Dict]] = None,
    ths_hot: Optional[List[Dict]] = None,
) -> None:
    """跨源标记 source_count。"""
    nga_forum = nga_forum or []
    ths_hot = ths_hot or []
    sources_by_type = {
        "sina": sina,
        "wallstreetcn": wallstreetcn,
        "cls": cls_news,
        "eastmoney": eastmoney,
        "newsnow": newsnow,
        "nga_forum": nga_forum,
        "ths_hot": ths_hot,
    }

    for item in news_items:
        title = item.get("title", "")
        matched = set()
        for src_type, src_list in sources_by_type.items():
            for other in src_list:
                ratio = SequenceMatcher(None, title, other.get("title", "")).ratio()
                if ratio >= TITLE_SIMILARITY_THRESHOLD:
                    matched.add(src_type)
                    break
        item["source_count"] = max(1, len(matched))


def _merge_all(
    sina: List[Dict],
    cls_news: List[Dict],
    wallstreetcn: List[Dict],
    eastmoney: List[Dict],
    newsnow: List[Dict],
    nga_forum: Optional[List[Dict]] = None,
    ths_hot: Optional[List[Dict]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """合并核心：返回 (截断后的 LLM 输入, 截断前的去重全量池)。

    `MAX_MERGED_NEWS` 的 weight 截断只服务 LLM 输入预算。热榜条目（NewsNow/同花顺）
    没有发布时间 → `_calculate_weight` 的时效项取默认中值，结构性地排在有时间戳的
    滚动新闻之后，多数交易日在截断线外被整源丢弃（实测 2026-10-28：newsnow 203 条、
    ths_hot 15 条，merged 150 条中 0 条）。**落库/观测必须用未截断的池**，
    否则 platform/rank/snapshot_count 永远到不了 node4_raw_news。
    """
    nga_forum = nga_forum or []
    ths_hot = ths_hot or []
    all_news = sina + cls_news + wallstreetcn + eastmoney + newsnow + nga_forum + ths_hot
    if not all_news:
        return [], []

    deduped = _deduplicate(all_news)
    _count_cross_source(deduped, sina, wallstreetcn, cls_news, eastmoney, newsnow, nga_forum, ths_hot)

    # 计算权重并排序
    for item in deduped:
        item["weight"] = _calculate_weight(item)
    deduped.sort(key=lambda x: x.get("weight", 0), reverse=True)
    pool = deduped

    # NGA 保底：确保至少 5 条论坛帖子进入合并结果（散户视角不应被专业媒体完全淹没）
    NGA_MIN_ITEMS = 5
    if nga_forum and len(deduped) > MAX_MERGED_NEWS:
        nga_in = sum(1 for d in deduped[:MAX_MERGED_NEWS] if d.get("source_type") == "nga_forum")
        if nga_in < NGA_MIN_ITEMS:
            nga_items_sorted = sorted(
                [d for d in deduped if d.get("source_type") == "nga_forum"],
                key=lambda x: x.get("weight", 0), reverse=True,
            )
            bottom = MAX_MERGED_NEWS - 1
            for nga_item in nga_items_sorted[:NGA_MIN_ITEMS]:
                if nga_item in deduped[:MAX_MERGED_NEWS]:
                    continue
                # 替换底部最低权重的非 NGA 条目
                while bottom >= 0 and deduped[bottom].get("source_type") == "nga_forum":
                    bottom -= 1
                if bottom >= 0:
                    deduped[bottom] = nga_item
                    bottom -= 1

    if len(deduped) > MAX_MERGED_NEWS:
        deduped = deduped[:MAX_MERGED_NEWS]

    active = sum(1 for s in [sina, cls_news, wallstreetcn, eastmoney, newsnow, nga_forum] if s)
    logger.info(
        "[news_feeds] 合并: %d 条（%d 源活跃），权重 %.0f~%.0f；未截断池 %d 条",
        len(deduped), active,
        deduped[-1].get("weight", 0) if deduped else 0,
        deduped[0].get("weight", 0) if deduped else 0,
        len(pool),
    )
    return deduped, pool


def merge_news(
    sina: List[Dict],
    cls_news: List[Dict],
    wallstreetcn: List[Dict],
    eastmoney: List[Dict],
    newsnow: List[Dict],
    nga_forum: Optional[List[Dict]] = None,
    ths_hot: Optional[List[Dict]] = None,
) -> List[Dict]:
    """7 源合并 + 去重 + 跨源标记 + 权重排序（截断到 MAX_MERGED_NEWS，LLM 输入口径）。"""
    merged, _ = _merge_all(
        sina, cls_news, wallstreetcn, eastmoney, newsnow, nga_forum, ths_hot,
    )
    return merged


# =============================================================================
# 粗筛池降噪：娱乐/社会热榜不进 LLM 泛读（2026-10-29）
# =============================================================================
# 微博/百度/头条/抖音/B站/知乎热榜与 A 股基本面无关，却因小时快照与多次在榜获得
# 高 appearance_count/rank（旧 weight 路径的结构性误判）。本过滤器只作用于 **LLM
# 粗筛入口与补读分析**：`_persist_raw_news(raw_pool)` 照旧落库（attention 轨迹与
# 概念发现/风险总结的 `raw_news` 均不改），DB 行不删不改。
# 保留：同花顺热榜（投资者关注）、财联社热门、澎湃新闻、财经媒体与 `intelligence_rss-*`。
_CARRIED_POOL_EXCLUDED_SOURCES = frozenset({
    "微博", "百度热搜", "今日头条", "抖音", "B站热搜", "知乎",
})
_CARRIED_POOL_EXCLUDED_INTEL_SOURCES = frozenset({
    "intelligence_newsnow-baidu",
    "intelligence_newsnow-weibo",
    "intelligence_newsnow-toutiao",
    "intelligence_newsnow-douyin",
    "intelligence_newsnow-bilibili-hot-search",
    "intelligence_newsnow-zhihu",
})


def filter_carried_pool(items: List[Dict]) -> List[Dict]:
    """返回剔除娱乐/社会热榜后的**新列表**（不改条目字段、不删除 DB 行）。

    只按 `source` 精确匹配（NewsNow 平台名与 Intelligence `intelligence_newsnow-<platform>`
    源名），不做标题语义判断：规则是「哪些平台不该参与粗筛」，不是价值判断。
    """
    kept: List[Dict] = []
    dropped = 0
    for item in items:
        source = (item.get("source") or "").strip()
        if source in _CARRIED_POOL_EXCLUDED_SOURCES or source in _CARRIED_POOL_EXCLUDED_INTEL_SOURCES:
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        logger.info(
            "[news_feeds] 粗筛池降噪: 剔除娱乐热榜 %d 条（保留 %d 条）", dropped, len(kept),
        )
    return kept


def _collect_all_sources(
    skip_sources: Iterable[str] = (),
) -> Tuple[Dict[str, List[Dict]], Dict[str, int]]:
    """串行采集 7 源（避免并发触发反爬），返回 {源名: 条目} 与各源条数。"""
    sources = {
        "sina": fetch_sina_news,
        "cls": lambda: fetch_cls_telegraph(120),
        "wallstreetcn": lambda: fetch_wallstreetcn_news(30),
        "eastmoney": lambda: fetch_eastmoney_kuaixun(30),
        "newsnow": fetch_newsnow_hot,
        "nga_forum": fetch_nga_forum_news,
        "ths_hot": fetch_ths_hot_topics,
    }
    skipped = set(skip_sources)
    if skipped:
        unknown = skipped - set(sources)
        if unknown:
            logger.warning("[news_feeds] 未知跳过源（忽略）: %s", sorted(unknown))
        sources = {k: v for k, v in sources.items() if k not in skipped}

    results: Dict[str, List[Dict]] = {}
    stats: Dict[str, int] = {}

    for name, fetcher in sources.items():
        try:
            items = fetcher()
            results[name] = items
            stats[name] = len(items)
        except Exception as exc:
            logger.error("[news_feeds] %s 采集异常: %s", name, exc)
            results[name] = []
            stats[name] = 0

    return results, stats


def fetch_all_news_pool(
    skip_sources: Iterable[str] = (),
) -> Tuple[List[Dict], List[Dict], Dict[str, int]]:
    """同 `fetch_all_news`，额外返回**截断前**的去重全量池（落库/观测口径）。

    Returns:
        (merged_news, raw_pool, source_stats)
        - merged_news：截断到 MAX_MERGED_NEWS 的 LLM 输入（与 `fetch_all_news` 逐字节一致）
        - raw_pool：去重 + 权重排序后的全量池（不截断），供 `_persist_raw_news` 落库；
          热榜条目（无发布时间）只会出现在这里，不在 merged_news 里
    """
    results, stats = _collect_all_sources(skip_sources)
    merged, pool = _merge_all(
        results.get("sina", []),
        results.get("cls", []),
        results.get("wallstreetcn", []),
        results.get("eastmoney", []),
        results.get("newsnow", []),
        results.get("nga_forum", []),
        results.get("ths_hot", []),
    )
    return merged, pool, stats


def fetch_all_news(skip_sources: Iterable[str] = ()) -> Tuple[List[Dict], Dict[str, int]]:
    """
    串行采集 7 源新闻，返回合并结果 + 各源统计。

    Args:
        skip_sources: 跳过的源名（`sina`/`cls`/`wallstreetcn`/`eastmoney`/`newsnow`/
            `nga_forum`/`ths_hot`）。快照采集按小时跑，跳过论坛/热榜这类非时效源，
            避免高频抓取触发反爬（它们仍由每日 GOT 采集）。

    Returns:
        (merged_news, source_stats)
        source_stats = {"sina": N, "cls": N, "wallstreetcn": N, "eastmoney": N, "newsnow": N, "nga_forum": N, "ths_hot": N}
        （被跳过的源不出现在 stats 中，与「该源返回空」区分开）
    """
    merged, _, stats = fetch_all_news_pool(skip_sources)
    return merged, stats



# =============================================================================
# S6: NGA 论坛帖子（散户讨论，早期信号源）
# =============================================================================

def fetch_nga_forum_news() -> List[Dict[str, Any]]:
    """
    NGA 大时代论坛帖子标题 → 新闻条目。

    复用 ForumSentimentService 的采集管道，将帖子标题转为统一新闻格式。
    散户讨论的信息密度低于专业媒体，但能捕捉早期传闻和情绪转向。
    """
    try:
        from src.config import Config
        from src.services.forum_sentiment import ForumSentimentService

        config = Config.get_instance()
        if not config.forum_sentiment_enabled:
            return []

        service = ForumSentimentService(config)
        if not service.is_available:
            return []

        result = service.fetch_hot_stocks()
        items: List[Dict] = []

        # 将热门股的关联帖子转为新闻条目
        for stock in result.hot_stocks[:30]:
            for post in stock.top_posts:
                title = (post.get("title") or "").strip()
                if not title or len(title) < 6:
                    continue
                tid = post.get("tid", 0)
                items.append({
                    "title": title,
                    "snippet": title[:150],
                    "source": "NGA论坛",
                    "source_type": "nga_forum",
                    "url": f"https://bbs.nga.cn/read.php?tid={tid}" if tid else "",
                    "published_date": "",
                })

        # 去重（同一帖子可能被多只热门股引用）
        seen_titles = set()
        deduped = []
        for item in items:
            if item["title"] not in seen_titles:
                seen_titles.add(item["title"])
                deduped.append(item)

        logger.info("[news_feeds] S6 NGA 论坛: %d 条（%d 帖子）", len(deduped), result.thread_count)
        return deduped

    except Exception as exc:
        logger.warning("[news_feeds] NGA 论坛采集失败: %s", exc)
        return []


# =============================================================================
# S7: 同花顺热榜话题（投资者关注焦点，直接反映市场热点）
# =============================================================================

def fetch_ths_hot_topics() -> List[Dict[str, Any]]:
    """
    同花顺热榜热门话题 → 新闻条目。

    这是投资者关注焦点的直接反映，例如"美国拟禁止中国光模块进口"类话题。
    每个话题包含标题、描述、相关股票代码和热度值。

    Returns:
        新闻条目列表，格式与 S1-S5 一致
    """
    try:
        from src.services.ths_hot_list import fetch_hot_topics

        topics = fetch_hot_topics(page_size=30)
        if not topics:
            logger.warning("[news_feeds] S7 同花顺热榜: 0 条")
            return []

        items = []
        for topic in topics:
            title = topic.get("title", "").strip()
            if not title:
                continue

            try:
                rank = int(topic.get("order", 0) or 0)
            except (TypeError, ValueError):
                rank = 0

            # 构建描述（包含相关股票信息）
            description = topic.get("description", "")
            related_stocks = topic.get("related_stocks", [])
            if related_stocks:
                stock_names = [s.get("name", "") for s in related_stocks[:3] if s.get("name")]
                if stock_names:
                    description = f"{description} 关联股票: {', '.join(stock_names)}"

            # 构建 URL（如果有）
            jump_url = topic.get("jump_url", "")
            if jump_url and not jump_url.startswith("http"):
                jump_url = f"https:{jump_url}" if jump_url.startswith("//") else ""

            items.append({
                "title": title,
                "snippet": description[:200] if description else title[:150],
                "source": "同花顺热榜",
                "source_type": "ths_hot",
                "url": jump_url,
                "published_date": "",  # 热榜无明确时间戳
                "heat": topic.get("heat", 0),
                "related_stocks": related_stocks,
                # P0-1：热榜名次（API `order` 字段，0=未知）+ 平台标记供落库/attention 观测
                "platform": "ths",
                "rank": rank or None,
            })

        logger.info("[news_feeds] S7 同花顺热榜: %d 条", len(items))
        return items

    except Exception as exc:
        logger.warning("[news_feeds] 同花顺热榜采集失败: %s", exc)
        return []
