# -*- coding: utf-8 -*-
"""「一只票横跨两行、无代码列」券商版面的解析测试（中信证券真实截图骨架）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.portfolio_ocr_import import PortfolioOcrImportService


def _boxes(rows, row_height=30, row_gap=40):
    """把二维文本表转成 OCR 框（x 按列序，y 按行序）。"""
    out = []
    for row_index, cells in enumerate(rows):
        y0 = row_index * row_gap
        for col_index, text in enumerate(cells):
            if not text:
                continue
            x0 = col_index * 250
            out.append({
                "text": text,
                "score": 0.95,
                "x0": x0,
                "y0": y0,
                "x1": x0 + 200,
                "y1": y0 + row_height,
            })
    return out


class TestTwoRowLayout(unittest.TestCase):
    def setUp(self):
        self.svc = PortfolioOcrImportService()
        # 名称→代码走网络，测试固定为已知结果
        self.svc._resolve_symbol = lambda name: {"润建股份": "002929", "天奇股份": "002009"}.get(name)

    def test_number_variants_handle_dot_thousands_and_three_decimals(self):
        variants = PortfolioOcrImportService._number_variants
        self.assertIn(68484.0, variants("68.484.00"))
        self.assertIn(-2942.86, variants("-2.942.86"))
        self.assertIn(54.945, variants("54.945"))     # 价格（三位小数）
        self.assertIn(54945.0, variants("54.945"))    # 也可能被当作千分位
        self.assertIn(4.12, variants("4.120%"))
        self.assertIn(1300.0, variants("1300"))

    def test_citic_two_row_layout_parses_holding(self):
        boxes = _boxes([
            ["市值", "盈亏", "持仓/可用←", "成本/现价←"],
            ["润建股份", "-2.942.86", "1300", "54.945"],
            ["68.484.00", "4.120%", "1300", "52.680"],
        ])

        holdings = self.svc.parse_holdings_from_boxes(boxes)

        self.assertEqual(len(holdings), 1)
        item = holdings[0]
        self.assertEqual(item.name, "润建股份")
        self.assertEqual(item.quantity, 1300)
        self.assertAlmostEqual(item.cost_price, 54.945, places=3)
        self.assertAlmostEqual(item.current_price, 52.68, places=3)
        self.assertEqual(item.symbol, "002929")

    def test_two_row_layout_without_duplicated_quantity(self):
        """T+1 买入当日「可用」为空 → 下行缺数量列，仍须按市值兜底解析。"""
        boxes = _boxes([
            ["市值", "盈亏", "持仓/可用←", "成本/现价←"],
            ["天奇股份", "-72.63", "2000", "17.816"],
            ["35,560.00", "-0.200%", "17.780"],
        ])

        holdings = self.svc.parse_holdings_from_boxes(boxes)

        self.assertEqual(len(holdings), 1)
        item = holdings[0]
        self.assertEqual(item.name, "天奇股份")
        self.assertEqual(item.symbol, "002009")
        self.assertEqual(item.quantity, 2000)
        self.assertAlmostEqual(item.cost_price, 17.816, places=3)
        self.assertAlmostEqual(item.current_price, 17.78, places=3)

    def test_inconsistent_numbers_are_rejected(self):
        boxes = _boxes([
            ["市值", "盈亏", "持仓/可用←", "成本/现价←"],
            ["某只股票", "-999999.00", "1300", "54.945"],
            ["68.484.00", "4.120%", "1300", "52.680"],
        ])

        # 盈亏与 数量×(现价-成本) 相差巨大 → 宁可不识别，也不能写入错数据
        self.assertEqual(self.svc.parse_holdings_from_boxes(boxes), [])

    def test_multi_holding_pages(self):
        boxes = _boxes([
            ["市值", "盈亏", "持仓/可用←", "成本/现价←"],
            ["润建股份", "-2.942.86", "1300", "54.945"],
            ["68.484.00", "4.120%", "1300", "52.680"],
            ["平安银行", "150.00", "1000", "11.200"],
            ["11.350.00", "1.339%", "1000", "11.350"],
        ])

        holdings = self.svc.parse_holdings_from_boxes(boxes)

        self.assertEqual([h.name for h in holdings], ["润建股份", "平安银行"])
        self.assertEqual(holdings[1].quantity, 1000)
        self.assertAlmostEqual(holdings[1].cost_price, 11.2, places=3)
        self.assertAlmostEqual(holdings[1].current_price, 11.35, places=3)


if __name__ == "__main__":
    unittest.main()
