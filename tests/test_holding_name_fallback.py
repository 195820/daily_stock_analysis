# -*- coding: utf-8 -*-
"""P1-1 小尾巴单测（backlog §2）：持仓名缺失/等于代码时的消费侧兜底。

覆盖 PortfolioDecisionNode._resolve_holding_name：
- 正常名直接透传；
- 空串 / name==symbol → 走离线索引兜底（monkeypatch，不耦合数据文件内容）；
- 索引不可用 → 回退原行为（返回 symbol，不新增异常路径）。
name 无计算副作用（仅展示/日志/归档），故仅测显示回退。
"""
from __future__ import annotations

import src.data.stock_index_loader as stock_index_loader
from src.got.nodes.portfolio_decision import PortfolioDecisionNode


def _fake_index(mapping):
    return lambda code: mapping.get(code)


def test_meaningful_name_passthrough(monkeypatch):
    # 正常名不应触发兜底（即便索引可用）
    monkeypatch.setattr(stock_index_loader, "get_index_stock_name", _fake_index({"600519": "坏数据"}))
    assert PortfolioDecisionNode._resolve_holding_name("600519", "贵州茅台") == "贵州茅台"


def test_empty_name_falls_back_to_index(monkeypatch):
    monkeypatch.setattr(stock_index_loader, "get_index_stock_name", _fake_index({"600250": "南京商旅"}))
    assert PortfolioDecisionNode._resolve_holding_name("600250", "") == "南京商旅"


def test_name_equals_symbol_falls_back(monkeypatch):
    monkeypatch.setattr(stock_index_loader, "get_index_stock_name", _fake_index({"605566": "福莱蒽特"}))
    # 上游把 name 直接塞成代码：应识别 name==symbol 并兜底
    assert PortfolioDecisionNode._resolve_holding_name("605566", "605566") == "福莱蒽特"


def test_index_unavailable_returns_symbol(monkeypatch):
    # 索引查不到该码（返回 None）→ 回退原行为返回 symbol
    monkeypatch.setattr(stock_index_loader, "get_index_stock_name", _fake_index({}))
    assert PortfolioDecisionNode._resolve_holding_name("999999", "") == "999999"


def test_non_string_name_is_safe(monkeypatch):
    monkeypatch.setattr(stock_index_loader, "get_index_stock_name", _fake_index({"600000": "浦发银行"}))
    assert PortfolioDecisionNode._resolve_holding_name("600000", None) == "浦发银行"
    assert PortfolioDecisionNode._resolve_holding_name("600000", 123) == "浦发银行"
