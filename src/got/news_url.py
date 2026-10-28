# -*- coding: utf-8 -*-
"""新闻 URL 归一化（P1-1：URL 指纹去重）。

采集层的 URL 此前完全不参与去重：`news_feeds._deduplicate` 只按标题相似度 0.45，
同一 URL 换标题即重复入库。本模块把 URL 归一化成稳定指纹（去追踪参数/去 fragment/
统一 host 大小写与 www 前缀/去尾斜杠），供采集层去重与 `node4_raw_news.url_canonical`
落库共用。

归一化失败（空串/非 http(s)/无法解析）返回 ""：调用方跳过 URL 判据、回退标题判据，
不改变原有去重行为。
"""

from typing import List, Tuple
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# 已知追踪/动态参数（业界通用清单，命中即删）
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "share_token", "from", "is_remote", "refer", "ch", "t",
    "timestamp", "req_id", "position", "type", "catid", "itemid", "ft",
})

# 前缀命中的动态参数（utm_ 之外的平台私有约定）
_DROP_PREFIX = ("utm_", "bd_", "weibo_")

_HTTP_SCHEMES = ("http", "https")


def _keep_param(key: str) -> bool:
    """保留非追踪参数（精确清单 + 前缀黑名单，单条谓词避免重复过滤）。"""
    return key not in _TRACKING_PARAMS and not key.startswith(_DROP_PREFIX)


def canonicalize_url(url: str) -> str:
    """归一化 URL：小写 scheme/host → 去追踪参数 → 去 fragment → 去尾斜杠。

    空/无法解析/非 http(s) → 返回 ""（调用方跳过 URL 判据，回退标题判据）。
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in _HTTP_SCHEMES or not parts.netloc:
        return ""

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    kept: List[Tuple[str, str]] = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if _keep_param(k)
    ]

    # 中文路径原样保留（urlsplit 不 decode）
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    return urlunsplit((
        parts.scheme.lower(),
        host,
        path,
        "&".join(f"{k}={v}" for k, v in kept),
        "",
    ))
