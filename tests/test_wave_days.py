"""波次计数器单元测试（v3.5 S2）。

测试 _calc_wave_days 的波次判定逻辑：
① 单日闪断不重置（种业型：强度未回撤）
② 深回撤 ≥2 日重置（复合铜箔型）
③ neutral 退出无条件重置
④ 单日深 V 不重置但落观察日志
⑤ 波峰强度滚动更新
"""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch
from src.got.nodes.sector_analysis import SectorAnalysisNode
from src.storage import Node2SectorTrendRecord


class TestWaveDaysCalculation:
    """波次计数器测试。"""

    @pytest.fixture
    def node(self):
        """创建 SectorAnalysisNode 实例。"""
        return SectorAnalysisNode(use_mock=True)

    @pytest.fixture
    def mock_session(self):
        """创建 mock session。"""
        session = MagicMock()
        session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(return_value=None)
        session.query.return_value.filter.return_value.all = MagicMock(return_value=[])
        return session

    def test_single_day_flash_no_reset(self, node, mock_session):
        """测试 ①：单日闪断不重置（种业型：强度未回撤）。"""
        # 构造历史：前日是 active_mainline，wave_days=5，强度 80
        # 昨日是 exhausted（单日闪断），强度 78（回撤 <10 分）
        # 今日是 active_mainline
        prev_record = Node2SectorTrendRecord(
            sector_code="880123",
            sector_name="种业",
            tracking_date=date.today() - timedelta(days=2),
            state="active_mainline",
            strength_score=80.0,
            wave_days=5,
            wave_start_date=date.today() - timedelta(days=7),
            wave_peak_strength=82.0,
        )

        gap_record = Node2SectorTrendRecord(
            sector_code="880123",
            sector_name="种业",
            tracking_date=date.today() - timedelta(days=1),
            state="exhausted_mainline",
            strength_score=78.0,
            wave_days=5,
            wave_start_date=date.today() - timedelta(days=7),
            wave_peak_strength=82.0,
        )

        # Mock 查询
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            side_effect=[gap_record, prev_record]
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880123",
            "active_mainline",
            81.0,
            date.today(),
        )

        # 验证：单日闪断，回撤 <10 分，应冻结继承（同波延续）
        assert wave_days == 5, f"Expected wave_days=5 (frozen), got {wave_days}"
        assert wave_start == prev_record.wave_start_date
        assert wave_peak == 82.0  # 继承前波峰值

    def test_deep_drawdown_reset(self, node, mock_session):
        """测试 ②：深回撤 ≥2 日重置（复合铜箔型）。"""
        # 构造历史：前日前是 active_mainline，wave_days=4，峰值 85
        # 前日是 exhausted，强度 70（回撤 15 分 >10 分）
        # 昨日是 exhausted（间隙 ≥2 日）
        # 今日是 active_mainline
        prev_record = Node2SectorTrendRecord(
            sector_code="880456",
            sector_name="复合铜箔",
            tracking_date=date.today() - timedelta(days=3),
            state="active_mainline",
            strength_score=80.0,
            wave_days=4,
            wave_start_date=date.today() - timedelta(days=7),
            wave_peak_strength=85.0,
        )

        gap_record1 = Node2SectorTrendRecord(
            sector_code="880456",
            sector_name="复合铜箔",
            tracking_date=date.today() - timedelta(days=2),
            state="exhausted_mainline",
            strength_score=70.0,  # 回撤 15 分
            wave_days=4,
            wave_start_date=date.today() - timedelta(days=7),
            wave_peak_strength=85.0,
        )

        gap_record2 = Node2SectorTrendRecord(
            sector_code="880456",
            sector_name="复合铜箔",
            tracking_date=date.today() - timedelta(days=1),
            state="exhausted_mainline",
            strength_score=68.0,
            wave_days=4,
            wave_start_date=date.today() - timedelta(days=7),
            wave_peak_strength=85.0,
        )

        # Mock 查询：第一次取最近记录（gap_record2），第二次取出框前锚点（prev_record）；
        # 间隙记录查询返回 [gap_record1, gap_record2]（2 交易日）
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            side_effect=[gap_record2, prev_record]
        )
        mock_session.query.return_value.filter.return_value.all = MagicMock(
            return_value=[gap_record1, gap_record2]
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880456",
            "active_mainline",
            75.0,
            date.today(),
        )

        # 验证：间隙 ≥2 日，回撤 ≥10 分，应重置
        assert wave_days == 1, f"Expected wave_days=1 (reset), got {wave_days}"
        assert wave_start == date.today()
        assert wave_peak == 75.0

    def test_neutral_exit_unconditional_reset(self, node, mock_session):
        """测试 ③：neutral 退出无条件重置。"""
        # 构造历史：前日是 active_mainline，wave_days=6
        # 昨日是 neutral（无条件重置）
        # 今日是 active_mainline
        prev_record = Node2SectorTrendRecord(
            sector_code="880789",
            sector_name="测试板块",
            tracking_date=date.today() - timedelta(days=2),
            state="active_mainline",
            strength_score=75.0,
            wave_days=6,
            wave_start_date=date.today() - timedelta(days=8),
            wave_peak_strength=80.0,
        )

        gap_record = Node2SectorTrendRecord(
            sector_code="880789",
            sector_name="测试板块",
            tracking_date=date.today() - timedelta(days=1),
            state="neutral",
            strength_score=60.0,
            wave_days=6,
            wave_start_date=date.today() - timedelta(days=8),
            wave_peak_strength=80.0,
        )

        # Mock 查询
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            return_value=gap_record
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880789",
            "active_mainline",
            70.0,
            date.today(),
        )

        # 验证：neutral 退出，无条件重置
        assert wave_days == 1, f"Expected wave_days=1 (reset after neutral), got {wave_days}"
        assert wave_start == date.today()
        assert wave_peak == 70.0

    def test_single_day_deep_v_no_reset(self, node, mock_session):
        """测试 ④：单日深 V 不重置但落观察日志。

        夹具修正（2026-09-01 审核）：原构造为 2 日间隙（T-2/T-1 各一条），与用例名"单日深 V"、
        注释"间隙仅 1 日"矛盾；而 2 日间隙 + 回撤 ≥10 分按结论 B 应重置（复合铜箔型）。
        现改为真正的单日深 V：T-2 active → T-1 单日深跌（回撤 20 分）→ 今日回归，
        对应粮食概念 09-01 型（100→78→100），读法 A：单日深 V 冻结不重置。
        """
        # 构造历史：前日前是 active_mainline，wave_days=3，峰值 80，平台强度 75
        # 前日（T-1）单日 exhausted，强度 60（深 V，自峰值回撤 20 分）
        # 今日是 active_mainline（强度回升但未破位）
        prev_record = Node2SectorTrendRecord(
            sector_code="880999",
            sector_name="深V测试",
            tracking_date=date.today() - timedelta(days=2),
            state="active_mainline",
            strength_score=75.0,
            wave_days=3,
            wave_start_date=date.today() - timedelta(days=5),
            wave_peak_strength=80.0,
        )

        gap_record1 = Node2SectorTrendRecord(
            sector_code="880999",
            sector_name="深V测试",
            tracking_date=date.today() - timedelta(days=1),
            state="exhausted_mainline",
            strength_score=60.0,  # 单日深 V 回撤（自峰值 80 回撤 20 分）
            wave_days=3,
            wave_start_date=date.today() - timedelta(days=5),
            wave_peak_strength=80.0,
        )

        # Mock 查询：第一次取最近记录（gap_record1），第二次取锚点（prev_record）
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            side_effect=[gap_record1, prev_record]
        )
        mock_session.query.return_value.filter.return_value.all = MagicMock(
            return_value=[gap_record1]
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880999",
            "active_mainline",
            70.0,
            date.today(),
        )

        # 验证：单日深 V（间隙 1 交易日 <2）→ 冻结继承，不重置（读法 A）
        assert wave_days == 3, f"Expected wave_days=3 (frozen, single day deep V), got {wave_days}"
        assert wave_start == prev_record.wave_start_date
        assert wave_peak == 80.0  # 继承前波峰值

    def test_peak_strength_rolling_update(self, node, mock_session):
        """测试 ⑤：波峰强度滚动更新。"""
        # 构造历史：前日是 active_mainline，wave_days=2，峰值 75
        # 今日是 active_mainline，强度 80（新高）
        prev_record = Node2SectorTrendRecord(
            sector_code="880111",
            sector_name="滚动更新测试",
            tracking_date=date.today() - timedelta(days=1),
            state="active_mainline",
            strength_score=75.0,
            wave_days=2,
            wave_start_date=date.today() - timedelta(days=3),
            wave_peak_strength=75.0,
        )

        # Mock 查询
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            return_value=prev_record
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880111",
            "active_mainline",
            80.0,  # 新高
            date.today(),
        )

        # 验证：波峰更新为 max(75, 80) = 80
        assert wave_days == 3, f"Expected wave_days=3, got {wave_days}"
        assert wave_start == prev_record.wave_start_date
        assert wave_peak == 80.0, f"Expected wave_peak=80.0 (updated), got {wave_peak}"

    def test_first_record_reset(self, node, mock_session):
        """测试无前记录时重置。"""
        # Mock 查询返回 None（无前记录）
        mock_session.query.return_value.filter.return_value.order_by.return_value.first = MagicMock(
            return_value=None
        )

        # 计算今日 wave_days
        wave_days, wave_start, wave_peak = node._calc_wave_days(
            mock_session,
            "880222",
            "active_mainline",
            70.0,
            date.today(),
        )

        # 验证：无前记录，重置
        assert wave_days == 1
        assert wave_start == date.today()
        assert wave_peak == 70.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
