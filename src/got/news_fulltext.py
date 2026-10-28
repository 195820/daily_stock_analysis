# -*- coding: utf-8 -*-
"""单页正文抽取（P0-2 短名单全文精读，可选依赖，失败静默降级）。

调用点只有 `NewsAnalysisNode._enrich_fulltext`（`GOT_NEWS_FULLTEXT_ENABLED=true`）：
阶段 2 细读前对 snippet 过短、url 非空的条目抓一次正文。

依赖策略：`trafilatura` 可用则用它的正文抽取（去导航/广告干净）；不可用或抽取为空
→ requests + 标签剥离兜底（同 `intelligence_service._fetch_rss` 的手法）。任一环节
失败都返回 ""，调用方保持原 snippet——精读是增强，不是硬依赖。
"""

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 单页正文上限（字符）：够覆盖 1200 字的 prompt 采样窗口，又不至于把整本专题页塞进内存
MAX_FULLTEXT_CHARS = 8000
_TEXT_TIMEOUT = 10

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    """剥标签兜底抽取：去脚本/样式块 → 去标签 → 压缩空白。"""
    text = _SCRIPT_STYLE.sub(" ", html or "")
    text = _TAG.sub(" ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]{2,8};", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_with_trafilatura(html: str) -> str:
    """trafilatura 抽取（可选依赖；缺失/失败 → ""）。"""
    try:
        import trafilatura
    except ImportError:
        return ""
    try:
        return (trafilatura.extract(html) or "").strip()
    except Exception as exc:  # noqa: BLE001 — 抽取库异常一律降级
        logger.debug("[news_fulltext] trafilatura 抽取失败: %s", exc)
        return ""


def fetch_article_text(url: str, timeout: int = _TEXT_TIMEOUT) -> str:
    """抓单页正文。trafilatura 可用则用 extract；不可用则 requests+正则剥标签。

    任一失败 → 返回 ""（调用方回退 snippet）。上限 MAX_FULLTEXT_CHARS 字。
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        resp.raise_for_status()
        # 编码兜底：无 charset 的 text/* 响应 requests 缺省按 ISO-8859-1 解码 →
        # 中文站整篇变乱码（实测新浪页 8000 字全 mojibake）。按 apparent_encoding 重解。
        if (resp.encoding or "").lower() in ("iso-8859-1", "ascii"):
            resp.encoding = resp.apparent_encoding or "utf-8"
        html: Optional[str] = resp.text
    except Exception as exc:  # noqa: BLE001 — 网络/反爬失败一律降级
        logger.debug("[news_fulltext] 抓取失败 %s: %s", url, exc)
        return ""
    if not html:
        return ""

    text = _extract_with_trafilatura(html)
    if not text:
        text = _strip_tags(html)
    return text[:MAX_FULLTEXT_CHARS]
