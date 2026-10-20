# -*- coding: utf-8 -*-
"""SearchService.search_generic：单 provider 切片语义（2026-10-20）。

为什么不是"首个非空 provider 失败转移就停"：板块身份校验要以**护栏通过**为停止条件，
Bocha 对 601778 会返回「成功但全是算力项目页」（0 证据）。因此 search_generic 只负责
一次查询 + 一段 provider 切片，把"换下一个 provider"的决定权留给调用方。
"""

from unittest.mock import MagicMock

from src.search_service import SearchResponse, SearchService
from src.search_service import SearchResult


def _service(providers):
    """绕过真实配置：只塞 _providers（is_available 仅读该属性）。"""
    svc = SearchService.__new__(SearchService)
    svc._providers = providers
    return svc


def _provider(name: str, results=None, success: bool = True):
    p = MagicMock()
    p.name = name
    p.is_available = True
    p.search.return_value = SearchResponse(
        query="q", results=results or [], provider=name, success=success,
        error_message=None if results else "无结果",
    )
    return p


def _result(url: str) -> SearchResult:
    return SearchResult(title="t", snippet="s", url=url, source="stockstar")


def test_offset_zero_only_calls_first_provider() -> None:
    first = _provider("Bocha", [_result("https://a.stockstar.com/x")])
    second = _provider("Tavily", [_result("https://b.eastmoney.com/y")])

    resp = _service([first, second]).search_generic(
        "q", provider_offset=0, max_providers=1
    )

    assert resp.provider == "Bocha"
    first.search.assert_called_once()
    second.search.assert_not_called()


def test_offset_one_reaches_second_provider() -> None:
    first = _provider("Bocha", [])
    second = _provider("Tavily", [_result("https://b.eastmoney.com/y")])

    resp = _service([first, second]).search_generic(
        "q", provider_offset=1, max_providers=1
    )

    assert resp.provider == "Tavily"
    assert len(resp.results) == 1
    first.search.assert_not_called()


def test_all_empty_returns_unsuccessful() -> None:
    resp = _service([_provider("Bocha", []), _provider("Tavily", [])]).search_generic(
        "q", max_providers=2
    )

    assert resp.success is False
    assert resp.results == []
    assert "Bocha" in (resp.error_message or "")


def test_no_providers_returns_not_configured() -> None:
    resp = _service([]).search_generic("q")

    assert resp.provider == "None"
    assert resp.success is False
    assert resp.error_message == "未配置搜索能力"
