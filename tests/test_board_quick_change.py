# -*- coding: utf-8 -*-
"""
get_board_quick_change 回归测试

测试目标：锁定 B-lite 实施的数学逻辑，防止未来修改引入 off-by-one 错误。

场景覆盖：
1. 慢涨板块（水运原型）——验证正例
2. 暴跌后反弹（半导体原型）——验证否决例
3. 暴跌发生在窗口边缘——验证窗口边界
4. 数据不足的降级——验证守卫逻辑
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from data_provider.mootdx_fetcher import MootdxFetcher


def make_kline_df(closes: list[float], volumes: list[float] = None) -> pd.DataFrame:
    """构造测试用 K 线 DataFrame。

    Args:
        closes: 收盘价序列
        volumes: 成交量序列（默认全为 1000，避免被 vol=0 过滤）
    """
    n = len(closes)
    if volumes is None:
        volumes = [1000.0] * n
    return pd.DataFrame({
        'close': closes,
        'vol': volumes,
        'open': closes,  # 简化：开盘=收盘
        'high': closes,
        'low': closes,
    })


class TestGetBoardQuickChange:
    """get_board_quick_change 回归测试套件。"""

    def test_slow_rise_sector(self):
        """场景 1：慢涨板块（水运原型）——验证正例。

        构造连续小涨的收盘价序列，验证：
        - change_1d / change_3d / change_5d 数值精确等于手工计算值
        - has_crash == False

        这是抓 off-by-one 的核心：如果 closes[-6] 被改成 closes[-5]，
        5日值就会对不上，测试立刻红。
        """
        # 构造 11 根 K 线：前 5 天平稳，后 5 天每天涨 2%
        # 收盘价：100, 100, 100, 100, 100, 102, 104.04, 106.12, 108.24, 110.41, 112.62
        closes = [100.0] * 5 + [
            100 * 1.02**i for i in range(1, 7)  # 102, 104.04, 106.12, 108.24, 110.41, 112.62
        ]
        df = make_kline_df(closes)

        # 手工计算正确答案
        # change_1d = (112.62 - 110.41) / 110.41 = 2.00%
        expected_1d = (closes[-1] - closes[-2]) / closes[-2] * 100
        # change_3d = (112.62 - 106.12) / 106.12 = 6.12%
        expected_3d = (closes[-1] - closes[-4]) / closes[-4] * 100
        # change_5d = (112.62 - 102) / 102 = 10.41%
        expected_5d = (closes[-1] - closes[-6]) / closes[-6] * 100

        fetcher = MootdxFetcher()

        # Mock _mootdx_session 返回假 client
        mock_client = MagicMock()
        mock_client.index_bars.return_value = df

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client

            result = fetcher.get_board_quick_change(['880461'])

        assert '880461' in result
        data = result['880461']

        # 验证数值精确匹配（容忍浮点误差 0.01%）
        assert abs(data['change_1d'] - expected_1d) < 0.01, \
            f"change_1d 期望 {expected_1d:.2f}%, 实际 {data['change_1d']:.2f}%"
        assert abs(data['change_3d'] - expected_3d) < 0.01, \
            f"change_3d 期望 {expected_3d:.2f}%, 实际 {data['change_3d']:.2f}%"
        assert abs(data['change_5d'] - expected_5d) < 0.01, \
            f"change_5d 期望 {expected_5d:.2f}%, 实际 {data['change_5d']:.2f}%"

        # 验证无暴跌
        assert data['has_crash'] is False, "慢涨板块不应触发 has_crash"

    def test_crash_then_rebound(self):
        """场景 2：暴跌后反弹（半导体原型）——验证否决例。

        构造"前 3 天有一天跌 7%、后 2 天反弹"的序列。
        此时 3日、5日累计可能都是正的——验证 has_crash == True 这条护栏
        能把这种"假慢涨"拦下来。

        这是护栏逻辑的锁死测试：将来有人把 -5.0 阈值改成 -8.0 或删掉这条检查，
        测试立刻红。
        """
        # 构造 11 根 K 线：第 8 天（倒数第 4 天）暴跌 7%，后 3 天反弹
        # 收盘价：100, 100, 100, 100, 100, 100, 93, 96, 99, 102, 105
        closes = [100.0] * 6 + [93.0, 96.0, 99.0, 102.0, 105.0]
        df = make_kline_df(closes)

        fetcher = MootdxFetcher()

        mock_client = MagicMock()
        mock_client.index_bars.return_value = df

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client

            result = fetcher.get_board_quick_change(['880491'])

        assert '880491' in result
        data = result['880491']

        # 验证 has_crash == True（第 8 天跌了 7%，在 5 日窗口内）
        assert data['has_crash'] is True, \
            "暴跌后反弹板块应触发 has_crash，即使累计涨幅为正"

        # 验证累计涨幅确实为正（这是"假慢涨"的陷阱）
        assert data['change_3d'] > 0, "3日累计应为正（反弹）"
        assert data['change_5d'] > 0, "5日累计应为正（反弹）"

    def test_crash_at_window_boundary(self):
        """场景 3：暴跌发生在窗口边缘——验证窗口边界。

        构造两个对照序列：
        - 序列 A：暴跌发生在第 6 天前（窗口外）→ has_crash 应为 False
        - 序列 B：暴跌发生在恰好第 5 天（窗口内最早一天）→ has_crash 应为 True

        这两个对照把 range(max(1, n-5), n) 的边界锁死——
        将来有人把 n-5 改成 n-4 或 n-6，必有一个对照挂掉。
        """
        fetcher = MootdxFetcher()

        # 序列 A：暴跌在第 6 天（倒数第 6 天，刚好在窗口外）
        # 11 根 K 线：第 6 天跌 6%（在 5 日窗口外）
        closes_a = [100.0] * 5 + [94.0] + [100.0] * 5  # 第 6 天跌 6%
        df_a = make_kline_df(closes_a)

        mock_client_a = MagicMock()
        mock_client_a.index_bars.return_value = df_a

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client_a
            result_a = fetcher.get_board_quick_change(['test_a'])

        assert 'test_a' in result_a
        # 第 6 天（closes[-6]）在 5 日窗口外（窗口是 closes[-5] 到 closes[-1]）
        # 所以 has_crash 应为 False
        assert result_a['test_a']['has_crash'] is False, \
            "暴跌在第 6 天（窗口外）不应触发 has_crash"

        # 序列 B：暴跌在第 5 天（倒数第 5 天，刚好在窗口内最早一天）
        # 11 根 K 线：第 7 天跌 6%（在 5 日窗口内最早一天）
        closes_b = [100.0] * 6 + [94.0] + [100.0] * 4  # 第 7 天跌 6%
        df_b = make_kline_df(closes_b)

        mock_client_b = MagicMock()
        mock_client_b.index_bars.return_value = df_b

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client_b
            result_b = fetcher.get_board_quick_change(['test_b'])

        assert 'test_b' in result_b
        # 第 7 天（closes[-5]）在 5 日窗口内（窗口是 closes[-5] 到 closes[-1]）
        # 所以 has_crash 应为 True
        assert result_b['test_b']['has_crash'] is True, \
            "暴跌在第 5 天（窗口内）应触发 has_crash"

    def test_insufficient_data(self):
        """场景 4：数据不足的降级——验证守卫逻辑。

        验证：
        - 只有 4 根 K 线（len < 6）→ 整个板块被丢弃（返回中不含该 code）
        - 中间有 vol=0 的停牌 bar → 过滤后仍够 6 根则正常计算、不够则丢弃

        防止未来有人把 len(df) < 6 守卫误改成 len(df) < 2（旧代码的值），
        导致短数据板块算出错误的 5 日涨幅混入通道。
        """
        fetcher = MootdxFetcher()

        # 子场景 4a：只有 4 根 K 线 → 应被丢弃
        closes_short = [100.0, 101.0, 102.0, 103.0]
        df_short = make_kline_df(closes_short)

        mock_client_short = MagicMock()
        mock_client_short.index_bars.return_value = df_short

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client_short
            result = fetcher.get_board_quick_change(['short_board'])

        assert 'short_board' not in result, \
            "只有 4 根 K 线的板块应被丢弃，不应出现在结果中"

        # 子场景 4b：11 根 K 线但中间有 6 根 vol=0 → 过滤后只剩 5 根 → 应被丢弃
        closes_with_halt = [100.0] * 5 + [101.0] * 6
        volumes_with_halt = [1000.0] * 5 + [0.0] * 6  # 后 6 根停牌
        df_halt = make_kline_df(closes_with_halt, volumes_with_halt)

        mock_client_halt = MagicMock()
        mock_client_halt.index_bars.return_value = df_halt

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client_halt
            result = fetcher.get_board_quick_change(['halt_board'])

        assert 'halt_board' not in result, \
            "过滤 vol=0 后不足 6 根的板块应被丢弃"

        # 子场景 4c：11 根 K 线但有 5 根 vol=0 → 过滤后剩 6 根 → 应正常计算
        closes_partial_halt = [100.0] * 5 + [101.0] * 6
        volumes_partial_halt = [1000.0] * 5 + [0.0] * 5 + [1000.0]  # 5 根停牌，1 根正常
        df_partial = make_kline_df(closes_partial_halt, volumes_partial_halt)

        mock_client_partial = MagicMock()
        mock_client_partial.index_bars.return_value = df_partial

        with patch.object(fetcher, '_mootdx_session') as mock_session:
            mock_session.return_value.__enter__.return_value = mock_client_partial
            result = fetcher.get_board_quick_change(['partial_halt_board'])

        assert 'partial_halt_board' in result, \
            "过滤 vol=0 后仍够 6 根的板块应正常计算"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
