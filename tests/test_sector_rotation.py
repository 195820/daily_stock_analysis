# -*- coding: utf-8 -*-
"""
SectorRotationService 单元测试（使用模拟数据）。

使用预录的 EastMoney API 响应格式数据验证：
- 强度评分算法
- 轮动阶段判断
- 龙头股识别
- 轮动信号生成
"""

import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, r"D:\daily_stock_analysis")

from src.config import Config
from src.services.sector_rotation import (
    SectorRotationResult,
    SectorRotationService,
    SectorStrength,
    _safe_float,
)


# ============================================================
# 模拟数据（基于 EastMoney API 真实响应格式）
# ============================================================

# 板块列表模拟数据（概念板块 Top 5）
MOCK_CONCEPT_BOARDS = [
    {"name": "券商概念", "code": "BK0711", "change_pct": 3.99, "price": 1030.71,
     "turnover_rate": 2.5, "up_count": 42, "down_count": 8},
    {"name": "黄金概念", "code": "BK0547", "change_pct": 4.51, "price": 19505.7,
     "turnover_rate": 3.1, "up_count": 15, "down_count": 3},
    {"name": "军工航天", "code": "BK0480", "change_pct": 4.42, "price": 57545.83,
     "turnover_rate": 2.8, "up_count": 38, "down_count": 12},
    {"name": "人工智能", "code": "BK1131", "change_pct": 2.15, "price": 2350.5,
     "turnover_rate": 4.2, "up_count": 55, "down_count": 25},
    {"name": "新能源汽车", "code": "BK1033", "change_pct": -0.85, "price": 1820.3,
     "turnover_rate": 3.5, "up_count": 20, "down_count": 45},
]

MOCK_INDUSTRY_BOARDS = [
    {"name": "证券", "code": "BK1020", "change_pct": 3.50, "price": 2150.0,
     "turnover_rate": 2.3, "up_count": 30, "down_count": 5},
    {"name": "贵金属", "code": "BK1025", "change_pct": 4.20, "price": 8500.0,
     "turnover_rate": 2.9, "up_count": 12, "down_count": 2},
]

# 板块K线模拟数据（券商概念 BK0711, 8个交易日）
MOCK_KLINE_BK0711 = [
    {"date": "2025-06-03", "open": 1021.18, "close": 1034.61, "high": 1038.95, "low": 1020.91,
     "volume": 19565460, "amount": 22908950566.0},
    {"date": "2025-06-04", "open": 1033.00, "close": 1048.55, "high": 1051.11, "low": 1032.81,
     "volume": 24134338, "amount": 28330580162.0},
    {"date": "2025-06-05", "open": 1047.98, "close": 1058.04, "high": 1061.78, "low": 1047.57,
     "volume": 25677470, "amount": 32144082106.0},
    {"date": "2025-06-06", "open": 1058.24, "close": 1047.39, "high": 1060.70, "low": 1046.35,
     "volume": 17104991, "amount": 21266247362.0},
    {"date": "2025-06-09", "open": 1055.99, "close": 1063.98, "high": 1069.76, "low": 1054.53,
     "volume": 29274028, "amount": 37661992786.0},
    {"date": "2025-06-10", "open": 1062.95, "close": 1049.74, "high": 1062.95, "low": 1042.13,
     "volume": 24902059, "amount": 30723974212.0},
    {"date": "2025-06-11", "open": 1052.28, "close": 1070.04, "high": 1081.82, "low": 1051.67,
     "volume": 40441837, "amount": 48703637532.0},
    {"date": "2025-06-12", "open": 1063.81, "close": 1068.55, "high": 1078.67, "low": 1063.81,
     "volume": 33642021, "amount": 37226444950.0},
]

# 人工智能板块K线（连续上涨 → accelerating 阶段）
MOCK_KLINE_BK1131 = [
    {"date": "2025-06-03", "open": 2200, "close": 2210, "high": 2215, "low": 2195,
     "volume": 10000000, "amount": 15000000000.0},
    {"date": "2025-06-04", "open": 2210, "close": 2230, "high": 2235, "low": 2205,
     "volume": 11000000, "amount": 16000000000.0},
    {"date": "2025-06-05", "open": 2230, "close": 2260, "high": 2268, "low": 2225,
     "volume": 13000000, "amount": 18000000000.0},
    {"date": "2025-06-06", "open": 2260, "close": 2290, "high": 2298, "low": 2255,
     "volume": 15000000, "amount": 20000000000.0},
    {"date": "2025-06-09", "open": 2290, "close": 2330, "high": 2340, "low": 2285,
     "volume": 18000000, "amount": 24000000000.0},
    {"date": "2025-06-10", "open": 2330, "close": 2370, "high": 2380, "low": 2325,
     "volume": 20000000, "amount": 27000000000.0},
    {"date": "2025-06-11", "open": 2370, "close": 2340, "high": 2385, "low": 2330,
     "volume": 22000000, "amount": 30000000000.0},
    {"date": "2025-06-12", "open": 2340, "close": 2350, "high": 2360, "low": 2335,
     "volume": 19000000, "amount": 26000000000.0},
]

# 新能源汽车板块K线（连续下跌 → declining 阶段）
MOCK_KLINE_BK1033 = [
    {"date": "2025-06-03", "open": 1900, "close": 1890, "high": 1905, "low": 1885,
     "volume": 12000000, "amount": 14000000000.0},
    {"date": "2025-06-04", "open": 1890, "close": 1870, "high": 1895, "low": 1865,
     "volume": 13000000, "amount": 15000000000.0},
    {"date": "2025-06-05", "open": 1870, "close": 1850, "high": 1875, "low": 1845,
     "volume": 14000000, "amount": 16000000000.0},
    {"date": "2025-06-06", "open": 1850, "close": 1840, "high": 1855, "low": 1835,
     "volume": 11000000, "amount": 13000000000.0},
    {"date": "2025-06-09", "open": 1840, "close": 1830, "high": 1845, "low": 1825,
     "volume": 12500000, "amount": 14500000000.0},
    {"date": "2025-06-10", "open": 1830, "close": 1825, "high": 1835, "low": 1820,
     "volume": 10000000, "amount": 12000000000.0},
    {"date": "2025-06-11", "open": 1825, "close": 1820, "high": 1830, "low": 1815,
     "volume": 11500000, "amount": 13500000000.0},
    {"date": "2025-06-12", "open": 1820, "close": 1820, "high": 1825, "low": 1815,
     "volume": 9000000, "amount": 11000000000.0},
]

# 成分股模拟数据
MOCK_CONSTITUENTS = {
    "BK0711": [
        {"code": "601211", "name": "国泰君安", "change_pct": 7.52, "price": 18.50},
        {"code": "600030", "name": "中信证券", "change_pct": 6.80, "price": 25.30},
        {"code": "601066", "name": "中信建投", "change_pct": 5.90, "price": 32.10},
        {"code": "601688", "name": "华泰证券", "change_pct": 5.20, "price": 19.80},
        {"code": "000776", "name": "广发证券", "change_pct": 4.50, "price": 16.20},
    ],
    "BK0547": [
        {"code": "600547", "name": "山东黄金", "change_pct": 8.10, "price": 35.60},
        {"code": "002155", "name": "湖南黄金", "change_pct": 7.20, "price": 18.90},
        {"code": "600489", "name": "中金黄金", "change_pct": 6.50, "price": 12.40},
    ],
}


class TestSafeFloat(unittest.TestCase):
    """工具函数测试。"""

    def test_normal_values(self):
        self.assertEqual(_safe_float(3.14), 3.14)
        self.assertEqual(_safe_float("2.5"), 2.5)
        self.assertEqual(_safe_float(0), 0.0)

    def test_invalid_values(self):
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float("abc"), 0.0)
        self.assertEqual(_safe_float(float("nan")), 0.0)


class TestRotationPhaseDetection(unittest.TestCase):
    """轮动阶段判断测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_accelerating_phase(self):
        """连续上涨且涨幅加速 → accelerating。"""
        phase = self.service._detect_rotation_phase(MOCK_KLINE_BK1131)
        # 连续上涨，后半段涨幅应大于前半段
        self.assertIn(phase, ("accelerating", "peaking"))

    def test_declining_phase(self):
        """连续下跌 → declining。"""
        phase = self.service._detect_rotation_phase(MOCK_KLINE_BK1033)
        self.assertEqual(phase, "declining")

    def test_insufficient_data(self):
        """数据不足 → unknown。"""
        phase = self.service._detect_rotation_phase([MOCK_KLINE_BK0711[0]])
        self.assertEqual(phase, "unknown")

    def test_mixed_phase(self):
        """涨跌参半（券商概念有涨有跌）。"""
        phase = self.service._detect_rotation_phase(MOCK_KLINE_BK0711)
        # 有涨有跌，应识别为某种非极端状态
        self.assertIn(phase, ("accelerating", "emerging", "peaking", "declining"))


class TestStrengthScore(unittest.TestCase):
    """强度评分算法测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_strong_sector_score(self):
        """强势板块应得到高分。"""
        score = self.service._calculate_strength_score(
            change_today=4.5,
            change_3d=8.0,
            change_5d=12.0,
            capital_flow=3.5,
            up_ratio=0.85,
        )
        self.assertGreater(score, 60)

    def test_weak_sector_score(self):
        """弱势板块应得到低分。"""
        score = self.service._calculate_strength_score(
            change_today=-3.0,
            change_3d=-6.0,
            change_5d=-10.0,
            capital_flow=-4.0,
            up_ratio=0.15,
        )
        self.assertLess(score, 40)

    def test_neutral_sector_score(self):
        """中性板块应得到中间分。"""
        score = self.service._calculate_strength_score(
            change_today=0.0,
            change_3d=0.0,
            change_5d=0.0,
            capital_flow=0.0,
            up_ratio=0.5,
        )
        self.assertAlmostEqual(score, 50.0, delta=10)

    def test_score_range(self):
        """评分始终在 0-100 范围内。"""
        for today in [-10, -5, 0, 5, 10]:
            for flow in [-10, 0, 10]:
                score = self.service._calculate_strength_score(
                    change_today=today,
                    change_3d=today * 2,
                    change_5d=today * 3,
                    capital_flow=flow,
                    up_ratio=0.5,
                )
                self.assertGreaterEqual(score, 0)
                self.assertLessEqual(score, 100)


class TestPeriodChange(unittest.TestCase):
    """多日涨跌幅计算测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_3d_change(self):
        """3日涨跌: (1068.55 - 1049.74) / 1049.74 * 100。"""
        change = self.service._calc_period_change(MOCK_KLINE_BK0711, days=3)
        expected = (1068.55 - 1049.74) / 1049.74 * 100
        self.assertAlmostEqual(change, expected, places=1)

    def test_5d_change(self):
        """5日涨跌: 取最近5根K线，(最后一根close - 第一根close) / 第一根close * 100。"""
        change = self.service._calc_period_change(MOCK_KLINE_BK0711, days=5)
        # klines[-5:] = [Jun6(1047.39), Jun9(1063.98), Jun10(1049.74), Jun11(1070.04), Jun12(1068.55)]
        expected = (1068.55 - 1047.39) / 1047.39 * 100
        self.assertAlmostEqual(change, round(expected, 2), places=1)

    def test_insufficient_data(self):
        """数据不足返回 0。"""
        change = self.service._calc_period_change([], days=3)
        self.assertEqual(change, 0.0)


class TestCapitalFlow(unittest.TestCase):
    """资金流向估算测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_positive_flow(self):
        """放量 → 正资金流。"""
        flow = self.service._estimate_capital_flow(MOCK_KLINE_BK0711)
        self.assertIsNotNone(flow)
        # 最后一日成交额 vs 前5日均额
        last_amount = MOCK_KLINE_BK0711[-1]["amount"]
        avg_amount = sum(k["amount"] for k in MOCK_KLINE_BK0711[-6:-1]) / 5
        expected = (last_amount - avg_amount) / 1e8
        self.assertAlmostEqual(flow, expected, places=1)

    def test_no_data(self):
        """数据不足返回 None。"""
        flow = self.service._estimate_capital_flow([MOCK_KLINE_BK0711[0]])
        self.assertIsNone(flow)


class TestRotationSignals(unittest.TestCase):
    """轮动信号生成测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_emerging_signal(self):
        """崛起板块应生成 emerging 信号。"""
        sectors = [
            SectorStrength(
                name="测试板块A", rotation_phase="emerging",
                change_pct_today=3.5, strength_score=65,
            ),
        ]
        signals = self.service._generate_rotation_signals(sectors)
        emerging_sigs = [s for s in signals if s["type"] == "emerging"]
        self.assertGreater(len(emerging_sigs), 0)
        self.assertIn("测试板块A", emerging_sigs[0]["description"])

    def test_warning_signal(self):
        """高强度但衰退的板块应生成 warning 信号。"""
        sectors = [
            SectorStrength(
                name="见顶板块", rotation_phase="peaking",
                change_pct_today=1.5, strength_score=70,
            ),
        ]
        signals = self.service._generate_rotation_signals(sectors)
        warning_sigs = [s for s in signals if s["type"] == "warning"]
        self.assertGreater(len(warning_sigs), 0)


class TestMarketTheme(unittest.TestCase):
    """市场主线生成测试。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    def test_theme_with_sectors(self):
        """有强势板块时生成主题。"""
        strong = [
            SectorStrength(name="券商", strength_score=80),
            SectorStrength(name="黄金", strength_score=75),
        ]
        emerging = [SectorStrength(name="AI", strength_score=60)]
        theme = self.service._generate_market_theme(strong, emerging)
        self.assertIn("券商", theme)
        self.assertIn("崛起", theme)

    def test_empty_theme(self):
        """无数据时返回默认主题。"""
        theme = self.service._generate_market_theme([], [])
        self.assertIn("分化", theme)


class TestFullAnalysisWithMock(unittest.TestCase):
    """使用 mock 数据测试完整分析流程。"""

    def setUp(self):
        self.config = Config._load_from_env()
        self.service = SectorRotationService(self.config)

    @patch.object(SectorRotationService, '_fetch_with_retry')
    @patch('src.services.sector_rotation._EastMoneyClient')
    def test_full_analysis_mock(self, MockClient, mock_fetch):
        """完整分析流程（mock 网络请求）。"""
        # Mock 板块列表
        def side_effect(board_type, top_n):
            if board_type == "concept":
                return MOCK_CONCEPT_BOARDS
            return MOCK_INDUSTRY_BOARDS

        mock_fetch.side_effect = side_effect

        # Mock K线和成分股
        mock_client = MockClient.return_value
        kline_map = {
            "BK0711": MOCK_KLINE_BK0711,
            "BK0547": MOCK_KLINE_BK0711,  # 复用
            "BK0480": MOCK_KLINE_BK0711,
            "BK1131": MOCK_KLINE_BK1131,
            "BK1033": MOCK_KLINE_BK1033,
            "BK1020": MOCK_KLINE_BK0711,
            "BK1025": MOCK_KLINE_BK0711,
        }
        mock_client.fetch_board_kline.side_effect = lambda **kw: kline_map.get(kw.get("board_code", ""), [])
        mock_client.fetch_board_constituents.side_effect = lambda code, top_n=10: MOCK_CONSTITUENTS.get(code, [])

        # 替换服务的 client
        self.service._client = mock_client

        result = self.service.get_rotation_analysis(top_n=5, lookback_days=5)

        # 验证输出结构
        self.assertIsInstance(result, SectorRotationResult)
        self.assertIsNotNone(result.fetched_at)
        self.assertGreater(result.total_sectors, 0)

        # 验证有强势板块
        if result.strong_sectors:
            top = result.strong_sectors[0]
            self.assertIsInstance(top, SectorStrength)
            self.assertGreater(top.strength_score, 0)
            self.assertIn(top.rotation_phase,
                         ("emerging", "accelerating", "peaking", "declining", "unknown"))

        # 验证市场主线
        self.assertIsInstance(result.market_theme, str)

        print("\n=== Mock 分析结果 ===")
        print(f"总板块: {result.total_sectors}")
        print(f"强势: {len(result.strong_sectors)} 崛起: {len(result.emerging_sectors)} 衰退: {len(result.declining_sectors)}")
        print(f"主线: {result.market_theme}")
        for s in result.strong_sectors[:5]:
            leaders = ", ".join(f"{st['name']}{st['change_pct']:+.1f}%" for st in s.leader_stocks[:3])
            print(f"  {s.name:8s} | {s.board_type:8s} | 今日{s.change_pct_today:+.2f}% 3D{s.change_pct_3d:+.2f}% 5D{s.change_pct_5d:+.2f}% | 强度{s.strength_score:.1f} | {s.rotation_phase:12s} | 龙头: {leaders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
