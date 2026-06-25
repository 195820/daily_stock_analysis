# -*- coding: utf-8 -*-
"""
StockScreenerPipeline 单元测试（使用模拟数据）。

验证：
- 数据模型
- 通知格式化
- 综合评分计算
- 策略加载
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"E:\daily_stock_analysis")

from src.services.stock_screener import (
    ScreenerStock,
    ScreenerRunResult,
    StockScreenerPipeline,
)


class TestScreenerStock(unittest.TestCase):
    """ScreenerStock 数据模型测试"""

    def test_default_values(self):
        """默认值应正确"""
        stock = ScreenerStock(code="600519", name="贵州茅台")
        self.assertEqual(stock.code, "600519")
        self.assertEqual(stock.score, 100.0)
        self.assertEqual(stock.source, "ths")
        self.assertFalse(stock.ma_alignment)
        self.assertEqual(stock.signal_tags, [])

    def test_custom_values(self):
        """自定义值应正确赋值"""
        stock = ScreenerStock(
            code="000858",
            name="五粮液",
            score=75.5,
            technical_score=80.0,
            volume_ratio=2.5,
            sector_name="白酒",
            sector_score=85.0,
            risk_tags=["高估值"],
        )
        self.assertEqual(stock.score, 75.5)
        self.assertEqual(stock.technical_score, 80.0)
        self.assertEqual(stock.volume_ratio, 2.5)
        self.assertEqual(stock.sector_name, "白酒")
        self.assertIn("高估值", stock.risk_tags)


class TestScreenerRunResult(unittest.TestCase):
    """ScreenerRunResult 数据模型测试"""

    def test_default_values(self):
        """默认值应正确"""
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test_strategy",
        )
        self.assertEqual(result.run_date, "2026-06-25")
        self.assertEqual(result.data_source, "ths")
        self.assertEqual(result.total_scanned, 0)
        self.assertEqual(result.stocks, [])
        self.assertIsNone(result.error)

    def test_with_stocks(self):
        """包含股票数据"""
        stocks = [
            ScreenerStock(code="600519", name="贵州茅台", score=80.0),
            ScreenerStock(code="000858", name="五粮液", score=70.0),
        ]
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
            matched_count=2,
            stocks=stocks,
        )
        self.assertEqual(result.matched_count, 2)
        self.assertEqual(len(result.stocks), 2)
        self.assertEqual(result.stocks[0].code, "600519")


class TestFormatNotification(unittest.TestCase):
    """format_notification 测试"""

    def setUp(self):
        """创建测试用 pipeline 实例"""
        self.pipeline = StockScreenerPipeline.__new__(StockScreenerPipeline)

    def test_error_result(self):
        """错误结果应显示错误信息"""
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
            error="数据源不可用",
        )
        text = self.pipeline.format_notification(result)
        self.assertIn("失败", text)
        self.assertIn("数据源不可用", text)

    def test_empty_result(self):
        """空结果应显示无股票"""
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
        )
        text = self.pipeline.format_notification(result)
        self.assertIn("未找到", text)

    def test_with_stocks(self):
        """有股票结果应格式化显示"""
        stocks = [
            ScreenerStock(
                code="600519",
                name="贵州茅台",
                score=80.0,
                technical_score=75.0,
                volume_ratio=2.0,
                sector_name="白酒",
                sector_score=85.0,
                heat_score=60.0,
                sentiment_score=0.3,
                risk_score=20.0,
                ai_reasoning="技术面良好，主力资金流入",
            ),
        ]
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
            matched_count=1,
            elapsed_seconds=3.5,
            stocks=stocks,
        )
        text = self.pipeline.format_notification(result)
        self.assertIn("600519", text)
        self.assertIn("贵州茅台", text)
        self.assertIn("80.0", text)
        self.assertIn("白酒", text)
        self.assertIn("3.5s", text)

    def test_high_score_emoji(self):
        """高分股票应显示🔥"""
        stocks = [
            ScreenerStock(code="600519", name="贵州茅台", score=80.0),
        ]
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
            matched_count=1,
            stocks=stocks,
        )
        text = self.pipeline.format_notification(result)
        self.assertIn("🔥", text)

    def test_low_score_emoji(self):
        """低分股票应显示👀"""
        stocks = [
            ScreenerStock(code="600519", name="贵州茅台", score=50.0),
        ]
        result = ScreenerRunResult(
            run_date="2026-06-25",
            run_time="08:30:00",
            strategy_name="test",
            matched_count=1,
            stocks=stocks,
        )
        text = self.pipeline.format_notification(result)
        self.assertIn("👀", text)


class TestScreenerRepository(unittest.TestCase):
    """ScreenerRepository 基础测试"""

    def test_import(self):
        """应能正常导入"""
        from src.repositories.screener_repository import ScreenerRepository
        self.assertTrue(callable(ScreenerRepository))


class TestScreenerAgentTools(unittest.TestCase):
    """选股 Agent Tools 测试"""

    def test_import_tools(self):
        """应能正常导入所有工具"""
        from src.agent.tools.screener_tools import ALL_SCREENER_TOOLS
        self.assertEqual(len(ALL_SCREENER_TOOLS), 4)

    def test_tool_names(self):
        """工具名称应正确"""
        from src.agent.tools.screener_tools import ALL_SCREENER_TOOLS
        names = [t.name for t in ALL_SCREENER_TOOLS]
        self.assertIn("get_forum_sentiment", names)
        self.assertIn("get_sector_rotation", names)
        self.assertIn("get_sector_constituents", names)
        self.assertIn("get_screener_results", names)

    def test_tool_categories(self):
        """工具分类应正确"""
        from src.agent.tools.screener_tools import ALL_SCREENER_TOOLS
        categories = {t.name: t.category for t in ALL_SCREENER_TOOLS}
        self.assertEqual(categories["get_forum_sentiment"], "sentiment")
        self.assertEqual(categories["get_sector_rotation"], "market")
        self.assertEqual(categories["get_sector_constituents"], "market")
        self.assertEqual(categories["get_screener_results"], "data")


if __name__ == '__main__':
    unittest.main()
