# -*- coding: utf-8 -*-
"""
Intelligence 情报服务（§6 远端整合）

与 news_feeds 的区别：
- news_feeds: 硬编码 5 源推送型，滚动窗口无持久化
- IntelligenceService: 可配置源（NewsNow/RSS），持久化到 SQLite，支持 scope 查询

核心功能：
1. 多源采集（NewsNow 热榜 / RSS 订阅）
2. 持久化到 SQLite（intelligence_items 表）
3. scope 查询（按 symbol/market/sector 过滤）
4. retention 管理（自动清理过期条目）

数据流：
    IntelligenceService.fetch_enabled_sources() → 持久化到 SQLite
                                                    ↓
    Node 4 消费: IntelligenceService.list_items(scope_type="market", market="cn", days=1)
                                                    ↓
                                     与 news_feeds 合并去重 → LLM 事件提取 → NewsContext
"""

import hashlib
import ipaddress
import json
import logging
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.storage import (
    DatabaseManager,
    IntelligenceSourceRecord,
    IntelligenceItemRecord,
    utc_naive_now,
)

logger = logging.getLogger(__name__)

# =============================================================================
# 常量
# =============================================================================

# NewsNow API
NEWSNOW_API = "https://newsnow.busiyi.world/api/s"
NEWSNOW_TIMEOUT = 10

# RSS 解析
RSS_TIMEOUT = 12
RSS_MAX_ITEMS = 30

# 通用
DEFAULT_TIMEOUT = 10
MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # 2MB
_CHUNK_SIZE = 8192

# 内存缓存（成功 60s，失败 30s）
_CACHE_TTL_SUCCESS = 60
_CACHE_TTL_FAILURE = 30
_cache_lock = threading.Lock()
_cache: Dict[str, Tuple[float, Any]] = {}

# 并发保护
_fetch_semaphore = threading.BoundedSemaphore(2)

# 全局单例锁（双重检查，防竞态）
_session_lock = threading.Lock()
_service_lock = threading.Lock()

# =============================================================================
# NewsNow 模板（A 股相关，均已在远端验证 — 见 docs/got/archive/remote_integration_plan.md §6.2）
# =============================================================================

NEWSNOW_TEMPLATES = {
    "cls-hot": {"name": "财联社热门", "scope_type": "market", "scope_value": "cn"},
    "xueqiu-hotstock": {"name": "雪球热门股票", "scope_type": "market", "scope_value": "cn"},
    "weibo": {"name": "微博热搜", "scope_type": "market", "scope_value": "cn"},
    "baidu": {"name": "百度热搜", "scope_type": "market", "scope_value": "cn"},
}


# =============================================================================
# 安全工具
# =============================================================================

def _is_private_ip(ip_str: str) -> bool:
    """判断 IP 是否为私有/回环/链路本地/保留/组播/未指定地址。

    使用标准库 ipaddress 而非手写正则，覆盖 IPv4/IPv6 全部内网段，
    避免正则遗漏（如 169.254.0.0/16 云元数据、100.64.0.0/10 CGNAT）。
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析 → 保守视为不安全
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _is_safe_url(url: str) -> bool:
    """检查 URL 是否安全（SSRF 防护）。

    - 仅允许 http/https 协议
    - 解析主机名的**所有** IP（域名走 DNS），逐个拒绝内网地址，
      防止域名指向 127.0.0.1 / 169.254.169.254 等绕过字面量正则
    - DNS 解析失败视为不安全（拒绝）
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning("[Intelligence] 拒绝非 HTTP(S) 协议 URL: %s", url)
            return False
        hostname = parsed.hostname or ""
        if not hostname:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addrinfos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            logger.warning("[Intelligence] DNS 解析失败，拒绝 URL: %s (%s)", url, exc)
            return False
        for info in addrinfos:
            ip_str = info[4][0]
            if _is_private_ip(ip_str):
                logger.warning(
                    "[Intelligence] 拒绝解析到内网 IP 的 URL: %s -> %s", url, ip_str
                )
                return False
        return True
    except Exception:
        return False


def _make_item_id(title: str, source_id: str, published_at: Optional[datetime]) -> str:
    """生成情报条目唯一 ID（title + source + 发布日期）。"""
    date_str = published_at.strftime("%Y%m%d") if published_at else "unknown"
    raw = f"{title}|{source_id}|{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def _to_utc_naive(dt: datetime) -> datetime:
    """将 aware datetime 归一化为 UTC-naive；naive 视为已是 UTC-naive。"""
    if dt.tzinfo is not None and dt.utcoffset() is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.replace(tzinfo=None)


def _parse_pubdate(raw: Optional[str]) -> Optional[datetime]:
    """解析 RSS/Atom 发布时间（RFC 822 / ISO 8601），失败返回 None。"""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # RFC 822（RSS <pubDate>）
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return _to_utc_naive(dt)
    except (TypeError, ValueError):
        pass
    # ISO 8601（Atom <published>/<updated>）
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return _to_utc_naive(dt)
    except (TypeError, ValueError):
        return None


# =============================================================================
# 内存缓存（W4：成功 60s / 失败 30s，避免批量分析时重复打源站）
# =============================================================================

def _cache_get(key: str) -> Optional[List[Dict[str, Any]]]:
    """读取未过期缓存；命中返回条目列表，未命中/过期返回 None。"""
    with _cache_lock:
        entry = _cache.get(key)
        if not entry:
            return None
        expires_at, value = entry
        if time.time() >= expires_at:
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: List[Dict[str, Any]], success: bool) -> None:
    """写入缓存：成功 60s，失败 30s（失败短 TTL 便于快速重试）。"""
    ttl = _CACHE_TTL_SUCCESS if success else _CACHE_TTL_FAILURE
    with _cache_lock:
        _cache[key] = (time.time() + ttl, value)


def _cache_clear() -> None:
    """清空缓存（测试/手动刷新用）。"""
    with _cache_lock:
        _cache.clear()


# =============================================================================
# HTTP 会话
# =============================================================================

def _build_session() -> requests.Session:
    """构建带重试的 HTTP 会话"""
    session = requests.Session()
    retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # 禁用代理（安全）
    session.trust_env = False
    return session


_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """获取全局 HTTP 会话（线程安全，双重检查锁）"""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = _build_session()
    return _session


def _read_body_limited(resp: requests.Response, max_bytes: int = MAX_CONTENT_LENGTH) -> bytes:
    """流式读取响应体，超过 max_bytes 立即中止（W3：防止大响应打爆内存）。

    需配合 requests 的 stream=True 使用；调用方负责 resp.close()。
    """
    chunks: List[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
        if not chunk:
            continue
        if total + len(chunk) > max_bytes:
            remain = max_bytes - total
            if remain > 0:
                chunks.append(chunk[:remain])
            logger.warning("[Intelligence] 响应超过 %d 字节限制，已截断", max_bytes)
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


# =============================================================================
# NewsNow 采集
# =============================================================================

def _fetch_newsnow_template(template_id: str) -> List[Dict[str, Any]]:
    """从 NewsNow 采集指定模板的热榜数据。"""
    cache_key = f"newsnow:{template_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not _is_safe_url(NEWSNOW_API):
        _cache_set(cache_key, [], success=False)
        return []

    params = {"id": template_id}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://newsnow.busiyi.world/",
    }

    try:
        with _fetch_semaphore:
            resp = _get_session().get(
                NEWSNOW_API,
                params=params,
                headers=headers,
                timeout=NEWSNOW_TIMEOUT,
                stream=True,
            )
            try:
                resp.raise_for_status()
                content = _read_body_limited(resp)
            finally:
                resp.close()

        data = json.loads(content)

        # C3：NewsNow 响应为 {"items": [...]}（dict），此前误用 isinstance(data, list)
        # 恒为 False 导致本通道永远返回空。此处以 dict.items 为主，兼容裸 list。
        if isinstance(data, dict):
            raw_items = data.get("items", [])
        elif isinstance(data, list):
            raw_items = data
        else:
            raw_items = []

        items: List[Dict[str, Any]] = []
        for item in raw_items[:20]:  # 限制条数
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            items.append({
                "title": title,
                "url": item.get("url", "") or "",
                "summary": item.get("desc", "") or "",
                # W5：尽力解析 NewsNow 发布时间（缺失时 _persist_items 回退为 now）
                "published_at": _parse_pubdate(item.get("pubDate")),
                "extra": {
                    "hot_score": item.get("hot", 0) or 0,
                    "template_id": template_id,
                },
            })

        _cache_set(cache_key, items, success=True)
        return items

    except Exception as e:
        logger.warning("[Intelligence] NewsNow %s 采集失败: %s", template_id, e)
        _cache_set(cache_key, [], success=False)
        return []


# =============================================================================
# RSS 采集
# =============================================================================

def _fetch_rss(url: str) -> List[Dict[str, Any]]:
    """从 RSS 源采集数据"""
    cache_key = f"rss:{url}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not _is_safe_url(url):
        _cache_set(cache_key, [], success=False)
        return []

    try:
        with _fetch_semaphore:
            resp = _get_session().get(
                url,
                timeout=RSS_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0"},
                stream=True,
            )
            try:
                resp.raise_for_status()
                content = _read_body_limited(resp)
            finally:
                resp.close()

        # 简易 RSS 解析（不依赖 feedparser）
        items: List[Dict[str, Any]] = []
        # 提取 <item> 或 <entry> 块
        item_pattern = re.compile(r"<(?:item|entry)[^>]*>(.*?)</(?:item|entry)>", re.DOTALL)
        title_pattern = re.compile(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.DOTALL)
        link_pattern = re.compile(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", re.DOTALL)
        desc_pattern = re.compile(r"<(?:description|summary)[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:description|summary)>", re.DOTALL)
        pubdate_pattern = re.compile(r"<(?:pubDate|published|updated)[^>]*>(.*?)</(?:pubDate|published|updated)>", re.DOTALL)

        text_content = content.decode("utf-8", errors="ignore")
        for match in item_pattern.finditer(text_content):
            block = match.group(1)

            title_match = title_pattern.search(block)
            title = title_match.group(1).strip() if title_match else ""
            if not title:
                continue

            link_match = link_pattern.search(block)
            link = link_match.group(1).strip() if link_match else ""

            desc_match = desc_pattern.search(block)
            desc = desc_match.group(1).strip() if desc_match else ""
            # 去除 HTML 标签
            desc = re.sub(r"<[^>]+>", "", desc)[:200]

            # W5：解析发布时间（此前 pubdate_pattern 定义却从未使用）
            pubdate_match = pubdate_pattern.search(block)
            published_at = _parse_pubdate(pubdate_match.group(1)) if pubdate_match else None

            items.append({
                "title": title,
                "url": link,
                "summary": desc,
                "published_at": published_at,
                "extra": {"source_url": url},
            })

            if len(items) >= RSS_MAX_ITEMS:
                break

        _cache_set(cache_key, items, success=True)
        return items

    except Exception as e:
        logger.warning("[Intelligence] RSS %s 采集失败: %s", url, e)
        _cache_set(cache_key, [], success=False)
        return []


# =============================================================================
# IntelligenceService 主类
# =============================================================================

class IntelligenceService:
    """Intelligence 情报服务

    功能：
    1. 从配置的源采集情报
    2. 持久化到 SQLite
    3. 提供 scope 查询 API
    4. retention 管理
    """

    def __init__(self, db: Optional[DatabaseManager] = None):
        self._db = db

    def _get_db(self) -> DatabaseManager:
        """获取数据库管理器"""
        if self._db is None:
            from src.storage import get_db
            self._db = get_db()
        return self._db

    # -------------------------------------------------------------------------
    # 源管理
    # -------------------------------------------------------------------------

    def register_newsnow_source(self, template_id: str, enabled: bool = True) -> str:
        """注册 NewsNow 源（显式调用会按 enabled 覆盖既有状态）"""
        if template_id not in NEWSNOW_TEMPLATES:
            raise ValueError(f"未知 NewsNow 模板: {template_id}")

        template = NEWSNOW_TEMPLATES[template_id]
        source_id = f"newsnow-{template_id}"

        db = self._get_db()
        with db.session_scope() as session:
            existing = session.query(IntelligenceSourceRecord).filter_by(source_id=source_id).first()
            if existing:
                existing.enabled = enabled
                existing.updated_at = utc_naive_now()
            else:
                record = IntelligenceSourceRecord(
                    source_id=source_id,
                    source_type="newsnow",
                    template_id=template_id,
                    scope_type=template["scope_type"],
                    scope_value=template["scope_value"],
                    market="cn",
                    enabled=enabled,
                    name=template["name"],
                )
                session.add(record)

        logger.info("[Intelligence] 注册 NewsNow 源: %s (enabled=%s)", source_id, enabled)
        return source_id

    def register_rss_source(
        self,
        url: str,
        name: str,
        scope_type: Optional[str] = None,
        scope_value: Optional[str] = None,
        market: str = "cn",
        enabled: bool = True,
    ) -> str:
        """注册 RSS 源"""
        if not _is_safe_url(url):
            raise ValueError(f"不安全的 URL: {url}")

        source_id = f"rss-{hashlib.md5(url.encode()).hexdigest()[:12]}"

        db = self._get_db()
        with db.session_scope() as session:
            existing = session.query(IntelligenceSourceRecord).filter_by(source_id=source_id).first()
            if existing:
                existing.url = url
                existing.name = name
                existing.enabled = enabled
                existing.updated_at = utc_naive_now()
            else:
                record = IntelligenceSourceRecord(
                    source_id=source_id,
                    source_type="rss",
                    url=url,
                    name=name,
                    scope_type=scope_type,
                    scope_value=scope_value,
                    market=market,
                    enabled=enabled,
                )
                session.add(record)

        logger.info("[Intelligence] 注册 RSS 源: %s (%s)", source_id, name)
        return source_id

    def list_sources(
        self,
        enabled_only: bool = False,
        source_type: Optional[str] = None,
        market: Optional[str] = None,
        scope_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """列出情报源配置（§6.2 list_sources）。

        Args:
            enabled_only: 仅返回启用的源
            source_type: 按类型过滤（newsnow / rss）
            market: 按市场过滤（如 "cn"）
            scope_type: 按 scope 过滤（symbol / market / sector）

        Returns:
            {"sources": [...], "total": int}
        """
        db = self._get_db()
        with db.session_scope() as session:
            q = session.query(IntelligenceSourceRecord)
            if enabled_only:
                q = q.filter(IntelligenceSourceRecord.enabled == True)  # noqa: E712
            if source_type:
                q = q.filter(IntelligenceSourceRecord.source_type == source_type)
            if market:
                q = q.filter(IntelligenceSourceRecord.market == market)
            if scope_type:
                q = q.filter(IntelligenceSourceRecord.scope_type == scope_type)
            records = q.order_by(IntelligenceSourceRecord.source_id).all()
            sources = [
                {
                    "source_id": r.source_id,
                    "source_type": r.source_type,
                    "template_id": r.template_id,
                    "url": r.url,
                    "scope_type": r.scope_type,
                    "scope_value": r.scope_value,
                    "market": r.market,
                    "enabled": r.enabled,
                    "name": r.name,
                }
                for r in records
            ]
            return {"sources": sources, "total": len(sources)}

    def fetch_enabled_sources(self) -> Dict[str, int]:
        """采集所有启用的源，返回 {source_id: item_count}"""
        db = self._get_db()
        sources_to_fetch = []

        with db.session_scope() as session:
            enabled_sources = session.query(IntelligenceSourceRecord).filter_by(enabled=True).all()
            for src in enabled_sources:
                sources_to_fetch.append({
                    "source_id": src.source_id,
                    "source_type": src.source_type,
                    "template_id": src.template_id,
                    "url": src.url,
                    "scope_type": src.scope_type,
                    "scope_value": src.scope_value,
                    "market": src.market,
                })

        stats: Dict[str, int] = {}
        for src in sources_to_fetch:
            items: List[Dict[str, Any]] = []
            if src["source_type"] == "newsnow" and src["template_id"]:
                items = _fetch_newsnow_template(src["template_id"])
            elif src["source_type"] == "rss" and src["url"]:
                items = _fetch_rss(src["url"])

            if items:
                self._persist_items(src["source_id"], items, src)
            stats[src["source_id"]] = len(items)

        logger.info("[Intelligence] 采集完成: %s", stats)
        return stats

    def _persist_items(
        self,
        source_id: str,
        items: List[Dict[str, Any]],
        source_meta: Dict[str, Any],
    ) -> int:
        """持久化情报条目到 SQLite（批量去重，避免 N+1 查询）。返回新增条数。"""
        if not items:
            return 0
        db = self._get_db()

        # 预计算 item_id + published_at（W5：使用真实发布时间，缺失回退 now）
        prepared: List[Tuple[str, datetime, Dict[str, Any]]] = []
        for item in items:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            published_at = item.get("published_at") or utc_naive_now()
            item_id = _make_item_id(title, source_id, published_at)
            prepared.append((item_id, published_at, item))

        if not prepared:
            return 0

        candidate_ids = [pid for pid, _, _ in prepared]
        inserted = 0
        with db.session_scope() as session:
            # 一次性查出已存在的 item_id（替代逐条 query，消除 N+1）
            existing_ids = {
                row[0]
                for row in session.query(IntelligenceItemRecord.item_id)
                .filter(IntelligenceItemRecord.item_id.in_(candidate_ids))
                .all()
            }
            for item_id, published_at, item in prepared:
                if item_id in existing_ids:
                    continue  # 去重
                record = IntelligenceItemRecord(
                    item_id=item_id,
                    source_id=source_id,
                    title=(item.get("title") or "").strip(),
                    summary=item.get("summary", "") or "",
                    url=item.get("url", "") or "",
                    published_at=published_at,
                    scope_type=source_meta.get("scope_type"),
                    scope_value=source_meta.get("scope_value"),
                    market=source_meta.get("market", "cn"),
                )
                if item.get("extra"):
                    record.set_extra_data(item["extra"])
                session.add(record)
                existing_ids.add(item_id)  # 防同批内重复
                inserted += 1

        return inserted

    # -------------------------------------------------------------------------
    # 查询 API（GOT 消费入口）
    # -------------------------------------------------------------------------

    def list_items(
        self,
        scope_type: Optional[str] = None,
        scope_value: Optional[str] = None,
        market: Optional[str] = None,
        query: Optional[str] = None,
        days: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """查询情报条目

        Args:
            scope_type: 范围类型（symbol / market / sector）
            scope_value: 范围值（如 "600519.SH" / "cn" / "半导体"）
            market: 市场过滤（如 "cn"）
            query: 关键词搜索（标题/摘要）
            days: 时间范围（天）
            limit: 返回条数上限

        Returns:
            {"items": [...], "total": int}
        """
        db = self._get_db()
        cutoff = utc_naive_now() - timedelta(days=days)

        with db.session_scope() as session:
            q = session.query(IntelligenceItemRecord).filter(
                IntelligenceItemRecord.published_at >= cutoff
            )

            if scope_type:
                q = q.filter(IntelligenceItemRecord.scope_type == scope_type)
            if scope_value:
                q = q.filter(IntelligenceItemRecord.scope_value == scope_value)
            if market:
                q = q.filter(IntelligenceItemRecord.market == market)
            if query:
                pattern = f"%{query}%"
                q = q.filter(
                    (IntelligenceItemRecord.title.ilike(pattern))
                    | (IntelligenceItemRecord.summary.ilike(pattern))
                )

            q = q.order_by(IntelligenceItemRecord.published_at.desc()).limit(limit)
            records = q.all()

            items = []
            for r in records:
                items.append({
                    "item_id": r.item_id,
                    "source_id": r.source_id,
                    "title": r.title,
                    "summary": r.summary,
                    "url": r.url,
                    "published_at": r.published_at.isoformat() if r.published_at else None,
                    "scope_type": r.scope_type,
                    "scope_value": r.scope_value,
                    "market": r.market,
                })

            return {"items": items, "total": len(items)}

    # -------------------------------------------------------------------------
    # retention 管理
    # -------------------------------------------------------------------------

    def cleanup_old_items(self, retention_days: int = 7) -> int:
        """清理过期情报条目

        Args:
            retention_days: 保留天数

        Returns:
            删除的条目数
        """
        db = self._get_db()
        cutoff = utc_naive_now() - timedelta(days=retention_days)

        with db.session_scope() as session:
            count = session.query(IntelligenceItemRecord).filter(
                IntelligenceItemRecord.published_at < cutoff
            ).delete()

        logger.info("[Intelligence] 清理 %d 条过期情报（>%d 天）", count, retention_days)
        return count


# =============================================================================
# 便捷函数（供 Node 4 调用）
# =============================================================================

_service_instance: Optional[IntelligenceService] = None


def get_intelligence_service() -> IntelligenceService:
    """获取全局 IntelligenceService 实例（线程安全，双重检查锁）"""
    global _service_instance
    if _service_instance is None:
        with _service_lock:
            if _service_instance is None:
                _service_instance = IntelligenceService()
    return _service_instance


def fetch_intelligence_items(
    days: int = 1,
    market: Optional[str] = "cn",
    scope_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """采集并返回 Intelligence 情报（供 Node 4 集成）。

    - W6：即使本次采集无新增，也返回窗口内已持久化的条目（不再 total==0 短路）。
    - W9：默认按 market="cn" 过滤（Node 4 关注 A 股市场级情报）。
    - C4：输出字段与 news_feeds 对齐（snippet / published_date），避免下游
      _format_news_for_prompt 读到空摘要。

    Returns:
        情报条目列表，格式与 news_feeds 输出兼容。
    """
    service = get_intelligence_service()

    # 采集（内部有 60s/30s 缓存，批量分析时不会重复打源站）
    stats = service.fetch_enabled_sources()
    total = sum(stats.values())

    # W9：按 market/scope 过滤查询
    result = service.list_items(days=days, market=market, scope_type=scope_type, limit=100)
    items = result.get("items", [])

    # 转换为 news_feeds 兼容格式
    merged: List[Dict[str, Any]] = []
    for item in items:
        title = item.get("title", "") or ""
        merged.append({
            "title": title,
            "snippet": item.get("summary") or title[:150],  # C4：对齐 news_feeds 字段名
            "source": f"intelligence_{item.get('source_id', 'unknown')}",
            "source_type": "intelligence",
            "url": item.get("url", "") or "",
            "published_date": item.get("published_at", "") or "",  # C4：对齐 news_feeds 字段名
        })

    logger.info("[Intelligence] 返回 %d 条情报（本次采集 %d 条）", len(merged), total)
    return merged


def initialize_builtin_sources() -> int:
    """初始化内建 A 股情报源（幂等）。

    W1：仅创建**缺失**的源，不覆盖已存在源的 enabled 状态
        （避免用户手动禁用后被每次 Node 4 运行重新启用）。

    Returns:
        本次新注册的源数量。
    """
    service = get_intelligence_service()
    try:
        existing_ids = {
            s["source_id"] for s in service.list_sources().get("sources", [])
        }
    except Exception as e:
        logger.warning("[Intelligence] 读取现有源失败，跳过初始化: %s", e)
        return 0

    registered = 0
    for template_id in NEWSNOW_TEMPLATES:
        source_id = f"newsnow-{template_id}"
        if source_id in existing_ids:
            continue  # 已存在 → 跳过，保留用户的 enabled 设置
        try:
            service.register_newsnow_source(template_id, enabled=True)
            registered += 1
        except Exception as e:
            logger.warning("[Intelligence] 注册 %s 失败: %s", template_id, e)

    if registered:
        logger.info(
            "[Intelligence] 内建源初始化：新注册 %d 个（已存在 %d 个跳过）",
            registered, len(existing_ids),
        )
    return registered
