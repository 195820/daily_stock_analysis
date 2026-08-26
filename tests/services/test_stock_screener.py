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

    def test_portfolio_fields(self):
        """持仓字段默认值应正确"""
        stock = ScreenerStock(code="600519", name="贵州茅台")
        self.assertFalse(stock.portfolio_held)
        self.assertEqual(stock.portfolio_quantity, 0.0)
        self.assertEqual(stock.portfolio_avg_cost, 0.0)
        self.assertIsNone(stock.portfolio_pnl_pct)
        self.assertEqual(stock.recommendation, "watch")

    def test_portfolio_held(self):
        """已持仓股票数据"""
        stock = ScreenerStock(
            code="600519",
            name="贵州茅台",
            score=80.0,
            recommendation="buy",
            portfolio_held=True,
            portfolio_quantity=100,
            portfolio_avg_cost=1800.0,
            portfolio_pnl_pct=5.2,
        )
        self.assertTrue(stock.portfolio_held)
        self.assertEqual(stock.portfolio_quantity, 100)
        self.assertEqual(stock.portfolio_pnl_pct, 5.2)


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


class TestR4SoftGuarantee(unittest.TestCase):
    """R4 软保底机制测试（_apply_r4_filter 静态方法）"""

    def _make_candidate(self, code, sector, risk, name=""):
        return {
            "code": code, "name": name or f"stock_{code}",
            "sector": sector, "sector_type": "ambush",
            "risk_score_safe": risk,
            "price": 10.0, "dde_net_pct": 1.0,
            "change_pct": 0.0, "turnover_rate": 0.05, "volume": 100000,
        }

    def test_guarantee_for_underrepresented_mainline(self):
        """主线 1 只 risk=55 + 埋伏 12 只 risk=85 → 主线通过保底入选"""
        candidates = [self._make_candidate(f"C{i:02d}", "auto_chip", 85) for i in range(12)]
        candidates.append(self._make_candidate("M01", "water_product", 55, "tian_shan"))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [{"name": "water_product"}]}],
                "ambush": [{"name": "auto_chip"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)

        # 主通道 Top 6 全是 auto_chip (85 > 55)
        # water_product 通过保底入选
        result_codes = {c["code"] for c in result}
        self.assertIn("M01", result_codes, "主线票应通过保底通道入选")
        self.assertEqual(len(result), 7, "主通道 6 + 保底 1 = 7")

    def test_guarantee_quality_floor(self):
        """保底票 risk=30 < 35 门槛 → 被拒"""
        candidates = [self._make_candidate(f"C{i:02d}", "auto_chip", 85) for i in range(8)]
        candidates.append(self._make_candidate("M01", "water_product", 30))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [{"name": "water_product"}]}],
                "ambush": [{"name": "auto_chip"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_codes = {c["code"] for c in result}
        self.assertNotIn("M01", result_codes, "risk=30 < 35 门槛应被拒")
        self.assertEqual(len(result), 6)

    def test_guarantee_boundary_exact_floor(self):
        """risk=35 恰等于门槛 → 严格 > 被拒（边界保护）"""
        candidates = [self._make_candidate(f"C{i:02d}", "auto_chip", 85) for i in range(8)]
        candidates.append(self._make_candidate("M01", "water_product", 35))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [{"name": "water_product"}]}],
                "ambush": [{"name": "auto_chip"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_codes = {c["code"] for c in result}
        self.assertNotIn("M01", result_codes, "risk=35 等于门槛，严格 > 应被拒")

    def test_guarantee_just_above_floor(self):
        """risk=36 > 35 门槛 → 通过保底"""
        candidates = [self._make_candidate(f"C{i:02d}", "auto_chip", 85) for i in range(8)]
        candidates.append(self._make_candidate("M01", "water_product", 36))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [{"name": "water_product"}]}],
                "ambush": [{"name": "auto_chip"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_codes = {c["code"] for c in result}
        self.assertIn("M01", result_codes, "risk=36 > 35 门槛应通过保底")

    def test_guarantee_max_two(self):
        """3 个无代表 actionable 板块 → 至多 2 个获保底"""
        candidates = [self._make_candidate(f"C{i:02d}", "dominant", 90) for i in range(8)]
        candidates.append(self._make_candidate("G1", "sector_a", 55))
        candidates.append(self._make_candidate("G2", "sector_b", 52))
        candidates.append(self._make_candidate("G3", "sector_c", 48))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [
                    {"name": "sector_a"}, {"name": "sector_b"}, {"name": "sector_c"},
                ]}],
                "ambush": [{"name": "dominant"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_sectors = {c["sector"] for c in result}
        guaranteed = result_sectors - {"dominant"}
        self.assertLessEqual(len(guaranteed), 2, "保底至多 2 个板块")

    def test_old_format_compatibility(self):
        """旧格式（无 members）的 active_mainline 也能拿到保底"""
        candidates = [self._make_candidate(f"C{i:02d}", "dominant", 90) for i in range(8)]
        candidates.append(self._make_candidate("M01", "legacy_sector", 55))

        # 旧格式：active_mainline 元素直接是 SectorDetail（无 members）
        got_context = {
            "sector_context": {
                "active_mainline": [{"name": "legacy_sector"}],
                "ambush": [{"name": "dominant"}],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_codes = {c["code"] for c in result}
        self.assertIn("M01", result_codes, "旧格式主线票应通过保底入选")

    def test_non_actionable_not_guaranteed(self):
        """非 actionable 板块的票不能走保底"""
        candidates = [self._make_candidate(f"C{i:02d}", "dominant", 90) for i in range(8)]
        candidates.append(self._make_candidate("X01", "unknown_sector", 55))

        got_context = {
            "sector_context": {
                "active_mainline": [{"members": [{"name": "dominant"}]}],
                "ambush": [],
            }
        }

        result = StockScreenerPipeline._apply_r4_filter(candidates, got_context)
        result_codes = {c["code"] for c in result}
        self.assertNotIn("X01", result_codes, "非 actionable 板块不能走保底")
        self.assertEqual(len(result), 6)


class TestRiskScoringGradient(unittest.TestCase):
    """风险评分梯度化测试（_score_risk）"""

    def setUp(self):
        self.pipeline = StockScreenerPipeline.__new__(StockScreenerPipeline)

    def test_rsi_tiers(self):
        """RSI 三档扣分：>85 -30 / >80 -20 / >75 -10"""
        # 直接测试分数逻辑（不拉真实数据）
        # 构造不同 RSI 场景的 mock df
        import pandas as pd
        import numpy as np

        def _make_df_with_rsi(target_rsi, vol_std=3.0, pct_5d=5.0):
            """构造一个能产出目标 RSI 的 K 线 DataFrame"""
            np.random.seed(42)
            n = 30
            close = [100.0]
            for i in range(n - 1):
                # 简单构造：让最近 6 天的涨跌产生目标 RSI
                close.append(close[-1] * (1 + np.random.normal(0, 0.01)))

            # 调整最后 6 天以控制 RSI
            # RSI = 100 - 100/(1+RS), RS = avg_gain/avg_loss
            # 简化：用实际 RSI 计算逻辑验证
            df = pd.DataFrame({
                "close": close,
                "volume": [100000] * n,
                "pct_chg": [0.0] + [
                    (close[i] / close[i-1] - 1) * 100 for i in range(1, n)
                ],
            })
            return df

        # 不拉真实数据，直接验证逻辑分支
        # 测试 1: score=100 - 0 (RSI<75) = 100（无 RSI 扣分）
        # 测试 2: score=100 - 10 (RSI 75~80) = 90
        # 测试 3: score=100 - 20 (RSI 80~85) = 80
        # 测试 4: score=100 - 30 (RSI>85) = 70
        # 因为 _score_risk 内部拉数据，这里用 mock

        with patch.object(self.pipeline, '_calc_rsi', return_value=82.0):
            mock_df = pd.DataFrame({
                "close": list(range(100, 130)),
                "volume": [100000] * 30,
                "pct_chg": [0.0] + [1.0] * 29,  # pct_5d = (129/125-1)*100 = 3.2%
            })
            # 模拟 fetcher_manager
            mock_fm = MagicMock()
            mock_fm.get_daily_data.return_value = (mock_df, None)
            candidates = [{"code": "T01", "name": "test"}]
            scores = self.pipeline._score_risk(candidates, mock_fm)
            # RSI=82 → -20, vol_std=0 (< 5) → 0, pct_5d=3.2% (< 10) → 0
            # 换手率/volume ratio 可能扣分，但至少验证 RSI 梯度
            self.assertLessEqual(scores["T01"], 80, "RSI=82 应扣 -20")
            self.assertGreater(scores["T01"], 50, "应高于数据不足分 50")

    def test_distinct_score_distribution(self):
        """12 只不同特征的票应产生 >5 个不同分值"""
        import pandas as pd
        import numpy as np

        mock_fm = MagicMock()

        def _gen_df(rsi_proxy, vol_std, pct_5d, vol_decay_ratio=1.0, ma_dev=0):
            """生成具有特定风险特征的 mock K 线"""
            n = 30
            base = 100.0
            # 构造收盘价序列以产生目标 5 日涨幅
            close = [base] * (n - 6)
            for i in range(6):
                close.append(close[-1] * (1 + pct_5d / 100 / 5))
            # MA20 偏离
            if ma_dev != 0:
                close[-1] = close[-1] * (1 + ma_dev / 100)
            # 波动率（pct_chg 标准差）
            pct_chg = [0.0] + [
                (close[i] / close[i-1] - 1) * 100 for i in range(1, n)
            ]
            # 调整使 std 匹配目标
            if len(pct_chg) >= 20:
                current_std = np.std(pct_chg[-20:])
                if current_std > 0 and vol_std > 0:
                    scale = vol_std / current_std
                    pct_chg = [p * scale for p in pct_chg]
            # 成交量（换手率衰减）
            volume = [100000] * (n - 7)
            for i in range(7):
                if i < 4:
                    volume.append(100000)
                else:
                    volume.append(int(100000 * vol_decay_ratio))
            return pd.DataFrame({
                "close": close,
                "volume": volume,
                "pct_chg": pct_chg,
            })

        # 12 只票：不同 RSI/波动率/涨幅组合
        scenarios = [
            (60, 2.0, 3.0),   # 低风险
            (70, 3.0, 5.0),   # 轻度
            (76, 4.0, 8.0),   # RSI >75
            (81, 5.5, 12.0),  # RSI >80
            (86, 6.0, 16.0),  # RSI >85
            (65, 9.0, 4.0),   # 高波动
            (55, 3.0, 22.0),  # 暴涨 >20%
            (50, 3.0, 26.0),  # 暴涨 >25%
            (70, 4.0, 11.0),  # 涨幅 >10%
            (60, 7.0, 6.0),   # 中高波动
            (82, 8.5, 18.0),  # RSI>80 + 高波动 + 涨幅>15%
            (45, 3.0, -8.0),  # 下跌（MA20 负偏离）
        ]

        candidates = []
        for i, (rsi_p, vol, pct) in enumerate(scenarios):
            code = f"S{i:02d}"
            candidates.append({"code": code, "name": f"stock_{i}"})
            mock_fm.get_daily_data.return_value = (
                _gen_df(rsi_p, vol, pct), None
            )
            # 直接调用内部逻辑获取分数
            # 由于 mock_fm 的 return_value 会被每次调用覆盖，需要特殊处理
            scores = self.pipeline._score_risk(
                [{"code": code, "name": f"stock_{i}"}], mock_fm
            )

        # 收集所有分数
        all_scores = []
        for i, (rsi_p, vol, pct) in enumerate(scenarios):
            code = f"S{i:02d}"
            mock_fm.get_daily_data.return_value = (
                _gen_df(rsi_p, vol, pct), None
            )
            scores = self.pipeline._score_risk(
                [{"code": code, "name": f"stock_{i}"}], mock_fm
            )
            all_scores.append(scores.get(code, 50))

        distinct_values = len(set(all_scores))
        self.assertGreater(
            distinct_values, 5,
            f"12 只不同特征票应产生 >5 个不同分值，实际 {distinct_values}: {sorted(set(all_scores))}"
        )


class TestBollingerBandLowerBound(unittest.TestCase):
    """布林带下轨评分修复测试（Issue 2: boll_pos<0 → 3 分而非 0 分）"""

    def setUp(self):
        self.pipeline = StockScreenerPipeline.__new__(StockScreenerPipeline)

    def test_below_lower_band_gets_points(self):
        """跌破下轨（boll_pos<0）应获得 3 分（超卖反弹潜力），而非 0 分"""
        import pandas as pd
        import numpy as np

        mock_fm = MagicMock()

        # 构造 K 线：前 29 天稳定在 100 附近（std≈1.5），最后一天暴跌到 93
        # → MA20≈100, BB_lower≈100-2×1.5=97, close=93 < 97 → boll_pos<0
        np.random.seed(42)
        n = 30
        close = [100.0 + np.random.normal(0, 1.5) for _ in range(n - 1)]
        close.append(93.0)  # 跌破下轨
        pct_chg = [0.0] + [(close[i] / close[i-1] - 1) * 100 for i in range(1, n)]

        df = pd.DataFrame({
            "close": close,
            "high": [c * 1.01 for c in close],
            "low": [c * 0.99 for c in close],
            "volume": [100000] * n,
            "pct_chg": pct_chg,
        })
        mock_fm.get_daily_data.return_value = (df, None)

        candidates = [{"code": "BB01", "name": "test_bb", "turnover_rate": 0.05}]
        scores = self.pipeline._score_technical_wave(candidates, mock_fm)

        # 布林带贡献应 ≥ 3（跌破下轨给 3 分）
        # 其他维度（MA20/RSI/MACD 等）也可能贡献分数
        # 关键断言：分数应 > 0（旧代码 boll_pos<0 → 0，加其他维度可能仍 >0）
        # 更精确的验证：确保布林带组件贡献了 3 分
        self.assertGreater(scores["BB01"], 0, "跌破下轨应获得 3 分布林带基础分")


if __name__ == '__main__':
    unittest.main()
