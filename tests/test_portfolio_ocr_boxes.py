# -*- coding: utf-8 -*-
"""持仓截图版面解析的回归测试。

坐标取自 2026-10-24 在 ECS 上对合成截图跑 rapidocr 的真实输出（21 个框）。
核心断言：带「代码」列时数量/成本不能被代码列污染。
"""

import pytest

from src.services.portfolio_ocr_import import PortfolioOcrImportService


def _box(text, x0, y0, x1=None, y1=None):
    return {
        "text": text,
        "score": 0.99,
        "x0": x0,
        "y0": y0,
        "x1": x1 if x1 is not None else x0 + 60,
        "y1": y1 if y1 is not None else y0 + 30,
    }


# 版面对应：名称 | 代码 | 数量 | 成本 | 现价（列 x 递增，行 y 递增）
ROWS = [
    ("平安银行", "000001", "1000", "11.20", "11.35"),
    ("贵州茅台", "600519", "100", "1580.00", "1602.50"),
    ("宁德时代", "300750", "300", "210.40", "198.70"),
]
COL_X = (20, 230, 400, 560, 730)


def build_boxes():
    boxes = []
    y = 70
    for row in ROWS:
        for cell, x in zip(row, COL_X):
            boxes.append(_box(cell, x, y))
        y += 60
    return boxes


class TestParseHoldingsFromBoxes:
    @staticmethod
    def _parse():
        svc = PortfolioOcrImportService()
        svc._backend = "rapidocr"  # 避免测试环境探测后端
        return svc.parse_holdings_from_boxes(build_boxes())

    def test_quantity_not_taken_from_code_column(self):
        items = self._parse()
        assert [(i.name, i.quantity) for i in items] == [
            ("平安银行", 1000),
            ("贵州茅台", 100),
            ("宁德时代", 300),
        ]

    def test_prices_follow_column_order(self):
        by_name = {i.name: i for i in self._parse()}
        assert by_name["平安银行"].cost_price == pytest.approx(11.20)
        assert by_name["平安银行"].current_price == pytest.approx(11.35)
        assert by_name["贵州茅台"].cost_price == pytest.approx(1580.00)
        assert by_name["贵州茅台"].current_price == pytest.approx(1602.50)

    def test_symbol_is_bare_code_matching_storage(self):
        """持仓表存裸码（`record_trade` → `canonical_stock_code` 只做 upper），解析也存裸码。"""
        by_name = {i.name: i for i in self._parse()}
        assert by_name["平安银行"].symbol == "000001"
        assert by_name["贵州茅台"].symbol == "600519"
        assert by_name["宁德时代"].symbol == "300750"

    def test_header_row_is_not_a_holding(self):
        boxes = build_boxes() + [
            _box("股票名称", COL_X[0], 10),
            _box("代码", COL_X[1], 10),
            _box("持仓", COL_X[2], 10),
            _box("成本", COL_X[3], 10),
            _box("现价", COL_X[4], 10),
        ]
        svc = PortfolioOcrImportService()
        svc._backend = "rapidocr"
        assert len(svc.parse_holdings_from_boxes(boxes)) == 3

    def test_legacy_text_parser_would_corrupt_code_column(self):
        """锁定「为什么默认走 boxes」：纯文本正则会拿代码当数量。"""
        svc = PortfolioOcrImportService()
        text = "\n".join(" ".join(row) for row in ROWS)
        legacy = svc.parse_holdings_from_ocr(text)
        wrong = [(i.name, i.quantity, i.cost_price) for i in legacy]
        assert ("平安银行", 1, 1000.0) in wrong


class TestRowSplit:
    def test_same_row_boxes_clustered(self):
        boxes = [_box("平安银行", 20, 100), _box("000001", 230, 104), _box("1000", 400, 98)]
        rows = PortfolioOcrImportService._row_split(boxes)
        assert len(rows) == 1
        assert [b["text"] for b in rows[0]] == ["平安银行", "000001", "1000"]

    def test_adjacent_rows_not_merged(self):
        boxes = [_box("平安银行", 20, 100), _box("贵州茅台", 20, 160)]
        assert len(PortfolioOcrImportService._row_split(boxes)) == 2


class TestComputeSyncTradesSymbolFormats:
    """持仓表键是裸码、匹配函数给 SH 前缀，两侧必须归一化后再比。"""

    @staticmethod
    def _svc():
        svc = PortfolioOcrImportService()
        svc._backend = "rapidocr"
        return svc

    def test_prefixed_holding_matches_bare_system_key(self):
        from src.services.portfolio_ocr_import import HoldingItem

        holdings = [HoldingItem(name="贵州茅台", quantity=100, cost_price=1580.0,
                                current_price=1602.5, symbol="SH600519")]
        system = {"600519": {"quantity": 100, "last_price": 1602.5}}
        assert self._svc().compute_sync_trades(holdings, system) == []

    def test_suffix_form_matches_bare_system_key(self):
        from src.services.portfolio_ocr_import import HoldingItem

        holdings = [HoldingItem(name="平安银行", quantity=1000, cost_price=11.2,
                                current_price=11.35, symbol="000001.SZ")]
        system = {"000001": {"quantity": 1000, "last_price": 11.35}}
        assert self._svc().compute_sync_trades(holdings, system) == []

    def test_quantity_diff_still_emitted_across_formats(self):
        from src.services.portfolio_ocr_import import HoldingItem

        holdings = [HoldingItem(name="贵州茅台", quantity=100, cost_price=1580.0,
                                current_price=1602.5, symbol="SH600519")]
        system = {"600519": {"quantity": 300, "last_price": 1602.5}}
        trades = self._svc().compute_sync_trades(holdings, system)
        assert len(trades) == 1
        assert trades[0]["side"] == "sell"
        assert trades[0]["quantity"] == 200.0

    def test_symbol_key_variants(self):
        svc = PortfolioOcrImportService
        for raw in ("600519", "SH600519", "sh600519", "600519.SH", "SH.600519"):
            assert svc._symbol_key(raw) == "600519", raw
        assert svc._symbol_key("BJ920748") == "920748"
        assert svc._symbol_key("HK00700") == "HK00700"

    def test_disappeared_position_without_price_and_cost_is_skipped(self):
        """既无现价也无成本时无法定价，record_trade 会 raise，只能留待人工。"""
        trades = self._svc().compute_sync_trades([], {"600519": {"quantity": 100, "last_price": 0}})
        assert trades == []

    def test_disappeared_position_falls_back_to_cost_price(self):
        """截图里已清空的票：缺现价时按成本价清仓，否则会永久留在系统持仓里。"""
        trades = self._svc().compute_sync_trades(
            [], {"600519": {"quantity": 100, "last_price": 0, "avg_cost": 1580.0}}
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "sell"
        assert trades[0]["quantity"] == 100.0
        assert trades[0]["price"] == 1580.0
        assert "缺现价" in trades[0]["note"]

    def test_disappeared_position_sells_all_at_last_price(self):
        """旧票卖出后不再出现在截图里 → 生成清仓卖出（价格取系统最新价）。"""
        trades = self._svc().compute_sync_trades(
            [], {"002929": {"quantity": 1300, "last_price": 56.96, "avg_cost": 54.945}}
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "sell"
        assert trades[0]["quantity"] == 1300.0
        assert trades[0]["price"] == 56.96
        assert trades[0]["note"] == "OCR同步清仓-002929"

