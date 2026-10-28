# -*- coding: utf-8 -*-
"""问财初筛的行业过滤 + `sector_query_board` 写回（2026-10-20 backlog §7 #18/#19）

钉死两条契约：

1. **#18**：「军贸概念」在 `concept_industry_map` 里有行业关键词表 → 问财语义漂移应答
   （绿电/消费票）在入池前被行业过滤掉，军工行业票保留。缺该行时过滤整段是 no-op，
   漂移应答原样入池（15 只里 11 只绿电/消费的生产事故根因）。
2. **#19**：`_get_sector_constituents_filtered` 返回的每只 dict 带 `sector_query_board`
   = 本次**实际命中查询**的板块名（`resolve_with_kind` / 别名改写产物）。Node 7 板块校验
   靠它用真实板块实名检索，而不是 Node 6 标签（「液冷服务」→「液冷服务器」错位即误伤根因）。

用打桩的 `THSScreenerService` + fake resolver 直接跑过滤函数，不触真实问财。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.stock_screener import StockScreenerPipeline


def _ths_stock(code, name, industry, dde=1.2):
    return SimpleNamespace(
        code=code, name=name, price=10.0, dde_net_pct=dde, dde_5d_sum=dde,
        change_pct=2.0, turnover_rate=3.0, volume=1_000_000, industry=industry,
    )


class FakeTHSService:
    """固定返回预置成分股（无论 query 内容）。"""

    def __init__(self, stocks):
        self.stocks = stocks
        self.queries = []

    def query(self, query):
        self.queries.append(query)
        return SimpleNamespace(stocks=list(self.stocks), total_count=len(self.stocks))


class FakeResolver:
    def __init__(self, resolved_name):
        self.resolved_name = resolved_name
        self.confirms = []

    def is_failed_today(self, sector_name):
        return False

    def resolve_with_kind(self, sector_name):
        return self.resolved_name, "concept"

    def confirm(self, sector_name, board_name, kind=None, source=None):
        self.confirms.append((sector_name, board_name, source))

    def aggressive_match(self, board_name):
        return None


class TestIndustryFilterAndQueryBoard(unittest.TestCase):

    def _run(self, sector_name, stocks, resolved_name):
        pipeline = object.__new__(StockScreenerPipeline)
        pipeline.config = None
        service = FakeTHSService(stocks)
        resolver = FakeResolver(resolved_name)
        with patch("src.services.ths_screener.THSScreenerService", return_value=service), \
             patch("src.services.board_name_resolver.get_board_name_resolver",
                   return_value=resolver):
            result = pipeline._get_sector_constituents_filtered(
                sector_name, "active_mainline"
            )
        return result, service

    def test_junmao_industry_filter_drops_green_power_stock(self):
        """军贸概念：光伏/电力行业票被过滤，船舶/航空行业票保留。"""
        stocks = [
            _ths_stock("601778", "晶科科技", "光伏发电"),
            _ths_stock("600032", "浙江新能", "电力"),
            _ths_stock("300589", "江龙船艇", "船舶制造"),
            _ths_stock("600435", "北方导航", "航空装备"),
            _ths_stock("600764", "中国海防", "船舶制造"),
        ]
        result, _ = self._run("军贸概念", stocks, "军贸概念")

        codes = [s["code"] for s in result]
        self.assertNotIn("601778", codes)      # 绿电漂移票
        self.assertNotIn("600032", codes)
        self.assertEqual(codes, ["300589", "600435", "600764"])

    def test_query_board_carries_resolved_name_not_tag(self):
        """液冷服务 → 别名改写为液冷服务器：写回 sector_query_board 供 Node 7 检索。"""
        stocks = [
            _ths_stock("001400", "江顺科技", "电子"),
            _ths_stock("002076", "星光股份", "电子"),
        ]
        result, service = self._run("液冷服务", stocks, "液冷服务器")

        self.assertTrue(service.queries)
        self.assertIn("液冷服务器", service.queries[0])      # 问财确实按实名查
        self.assertEqual(
            [s["sector_query_board"] for s in result], ["液冷服务器", "液冷服务器"]
        )


if __name__ == "__main__":
    unittest.main()
