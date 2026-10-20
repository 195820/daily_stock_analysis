# -*- coding: utf-8 -*-
"""联网检索证据校验：判断一段检索文本是否支持「该股票属于该板块」。

存在的理由（2026-10-20，601778 反例驱动）
--------------------------------------------
板块身份校验原用 efinance 板块列表跨口径比对，结构性误杀（THS 概念在东财没有对应板块）。
改用「检索 + LLM 判定」后，**证据护栏必须比"引用串在文本里存在"更严**：

    601778 晶科科技的检索结果里唯一出现「军贸」的片段，讲的是另一只票
    —— 晶品特装(688084) 智能化编组核心供应商；军贸+内装双轮驱动 ——

只做 span 存在性校验会把这条当成本票的证据。因此这里要求：

1. **板块名与该股票标识出现在同一 segment（句子/片段）**；
2. segment 内出现**其他 6 位代码**时一律判否（同句混谈多票 ⇒ 归属不可归因）；
3. segment 内只有板块名、没有标的标识时，仅当同时满足才接受：
   - segment 含指代词（该股 / 该公司 / 该标的），且
   - 结果标题里含该股票名称或代码（指代对象的绑定来源），且
   - segment 内没有其他 6 位代码（见规则 2）。

纯函数、零 IO、零 LLM：供 `_verify_candidate_sectors` 消费，也便于用真实检索串单测。
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import urlparse

# 6 位 A 股代码（允许 SH/SZ/BJ 前后缀）
_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
# 片段切分：中文句末标点 + 分号 + 换行 + 竖线（搜索摘要常用）
_SEGMENT_SPLIT_RE = re.compile(r"[。！？；;\n|]+")
# 指代词：仅在"标题已绑定标的"时才允许把归属挂到标题的票上
_ANAPHORS = ("该股", "该公司", "该标的", "这家公司")
# T3：指名式概念列表标签（「核心概念 白酒概念」「所属概念：…」）
_CONCEPT_LIST_RE = re.compile(r"(?:核心概念|所属概念|所属板块|概念板块|关联概念|关联板块)[：:\s、]*$")
# T3 段长上限：列表段通常很短，长段多半是综述文，归属不足以归因
_CONCEPT_LIST_MAX_LEN = 120

# 来源白名单（用户 2026-10-20 选定「财经站白名单」）：
# 护栏只保证「同段绑定」，但营销稿会把蹭概念写成事实 → 只信财经站与公告源。
EVIDENCE_SOURCE_ALLOWLIST = (
    "stockstar.com",   # 证券之星
    "eastmoney.com",   # 东方财富
    "10jqka.com.cn",   # 同花顺
    "cfi.cn",          # 中财网
    "cninfo.com.cn",   # 巨潮资讯（公告）
    "hx168.com.cn",    # 华西证券 F10（概念列表页）
    "dabanke.com",     # 打板客（题材/概念列表）
    "sina.com.cn",     # 新浪财经
    "qq.com",          # 腾讯新闻财经
    "163.com",         # 网易财经
    "hexun.com",       # 和讯
    "jrj.com.cn",      # 金融界
)


def is_allowed_source(url: Optional[str]) -> bool:
    """URL 是否属于白名单来源（hostname 精确/后缀匹配，防 `evil.com/?x=stockstar.com`）。"""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower().strip(".")
    if not host:
        return False
    return any(host == domain or host.endswith("." + domain)
               for domain in EVIDENCE_SOURCE_ALLOWLIST)


def _codes_in(text: str) -> set[str]:
    return set(_CODE_RE.findall(text or ""))


def _segments(text: str) -> list[str]:
    return [seg.strip() for seg in _SEGMENT_SPLIT_RE.split(text or "") if seg.strip()]


def _board_hit(segment: str, boards: Iterable[str]) -> Optional[str]:
    for board in boards:
        if board and board in segment:
            return board
    return None


def evidence_supports_membership(
    *,
    title: str,
    text: str,
    stock_name: str,
    code: str,
    boards: Iterable[str],
) -> Optional[str]:
    """返回被接受的**原文片段**（供 LLM/归档引用）；无合格证据返回 None。

    Args:
        title: 检索结果标题（指代绑定的依据）
        text: 检索结果摘要/正文
        stock_name: 股票名称（如 "江顺科技"）
        code: 6 位代码（如 "001400"）
        boards: 待判板块名候选（原标签 + resolved 名 + 别名变体）

    Returns:
        命中的 segment 原文；None 表示"这段检索不支持该归属"。
    """
    code = (code or "").strip()
    stock_name = (stock_name or "").strip()
    boards = [b.strip() for b in boards if b and b.strip()]
    if not code or not boards:
        return None

    title_bound = bool(stock_name and stock_name in (title or "")) or code in (title or "")
    # 标题作为一个 segment 参与判定（真相往往就写在标题里）
    segments = ([title] if title else []) + _segments(text)

    for seg in segments:
        board = _board_hit(seg, boards)
        if not board:
            continue

        seg_codes = _codes_in(seg)
        if seg_codes and code not in seg_codes:
            # 同句混入其他票的代码 → 归属无法归因（601778 × 晶品特装 688084 即此类）
            continue

        has_ident = (code in seg) or (stock_name and stock_name in seg)
        if has_ident:
            return seg

        # T2 无直接标的标识：仅当"指代词 + 标题已绑定标的"时接受
        if title_bound and any(a in seg for a in _ANAPHORS):
            return seg

        # T3 指名式概念列表：「核心概念 白酒概念」这类 F10/概念页句式。
        # 条件：标题已绑定本票 + 段内无其它 6 位代码 + 段长 ≤120 + 板块名前 ≤16 字出现列表标签。
        if title_bound and not seg_codes and len(seg) <= _CONCEPT_LIST_MAX_LEN:
            prefix = seg[: seg.find(board)]
            if _CONCEPT_LIST_RE.search(prefix[-16:]):
                return seg

    return None
