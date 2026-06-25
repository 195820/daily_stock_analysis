# -*- coding: utf-8 -*-
"""
ForumSentimentService 单元测试（使用模拟数据）。

验证：
- 情绪分析算法
- 股票引用提取
- 论坛数据模型
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"E:\daily_stock_analysis")

from src.services.forum_sentiment import (
    ForumSentimentService,
    ForumHotStock,
    ForumSentimentResult,
    analyze_sentiment,
    extract_stock_references,
)


class TestAnalyzeSentiment(unittest.TestCase):
    """情绪分析函数测试"""

    def test_positive_sentiment(self):
        """正面情绪文本应返回正值"""
        text = "这个股票太牛了，暴涨涨停，主力强势拉升"
        score = analyze_sentiment(text)
        self.assertGreater(score, 0.0)

    def test_negative_sentiment(self):
        """负面情绪文本应返回负值"""
        text = "跌停暴跌，割肉跑了，套牢亏惨"
        score = analyze_sentiment(text)
        self.assertLess(score, 0.0)

    def test_neutral_sentiment(self):
        """中性文本应返回接近 0"""
        text = "今天天气不错"
        score = analyze_sentiment(text)
        self.assertAlmostEqual(score, 0.0, delta=0.3)

    def test_mixed_sentiment(self):
        """混合情绪应返回中间值"""
        text = "虽然涨了但是量能不足，谨慎追高"
        score = analyze_sentiment(text)
        self.assertGreater(score, -1.0)
        self.assertLess(score, 1.0)

    def test_empty_text(self):
        """空文本应返回 0"""
        score = analyze_sentiment("")
        self.assertEqual(score, 0.0)


class TestExtractStockReferences(unittest.TestCase):
    """股票引用提取测试"""

    def test_code_in_text(self):
        """应提取文本中的股票代码"""
        text = "看看 600519 贵州茅台这只票"
        refs = extract_stock_references(text)
        codes = [r[0] for r in refs]
        self.assertIn("600519", codes)

    def test_no_codes(self):
        """无代码文本应返回空列表"""
        text = "今天天气真好"
        refs = extract_stock_references(text)
        self.assertEqual(len(refs), 0)

    def test_returns_tuples(self):
        """返回 (code, name) 元组列表"""
        text = "600519 贵州茅台"
        refs = extract_stock_references(text)
        self.assertIsInstance(refs, list)
        if refs:
            self.assertIsInstance(refs[0], tuple)
            self.assertEqual(len(refs[0]), 2)


class TestForumHotStock(unittest.TestCase):
    """ForumHotStock 数据模型测试"""

    def test_default_values(self):
        """默认值应正确"""
        stock = ForumHotStock(code="600519", name="贵州茅台")
        self.assertEqual(stock.code, "600519")
        self.assertEqual(stock.name, "贵州茅台")
        self.assertEqual(stock.source, "nga_dashidai")
        self.assertEqual(stock.heat_score, 0.0)
        self.assertEqual(stock.sentiment_score, 0.0)
        self.assertEqual(stock.mention_count, 0)
        self.assertEqual(stock.trend, "stable")

    def test_custom_values(self):
        """自定义值应正确赋值"""
        stock = ForumHotStock(
            code="000858",
            name="五粮液",
            heat_score=75.0,
            sentiment_score=0.5,
            mention_count=20,
            trend="rising",
        )
        self.assertEqual(stock.heat_score, 75.0)
        self.assertEqual(stock.sentiment_score, 0.5)
        self.assertEqual(stock.mention_count, 20)
        self.assertEqual(stock.trend, "rising")


class TestForumSentimentResult(unittest.TestCase):
    """ForumSentimentResult 数据模型测试"""

    def test_default_values(self):
        """默认值应正确"""
        result = ForumSentimentResult()
        self.assertEqual(result.hot_stocks, [])
        self.assertEqual(result.hot_sectors, [])
        self.assertEqual(result.market_sentiment, 0.0)
        self.assertEqual(result.thread_count, 0)

    def test_with_data(self):
        """包含数据"""
        stocks = [ForumHotStock(code="600519", name="茅台", heat_score=80.0)]
        result = ForumSentimentResult(
            hot_stocks=stocks,
            market_sentiment=0.3,
            thread_count=100,
        )
        self.assertEqual(len(result.hot_stocks), 1)
        self.assertEqual(result.market_sentiment, 0.3)
        self.assertEqual(result.thread_count, 100)


if __name__ == '__main__':
    unittest.main()
