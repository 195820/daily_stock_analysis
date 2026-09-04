# -*- coding: utf-8 -*-
"""
Phase 1-A3 单元测试：_collect_sector_candidates 候选收集。

覆盖：
1. 4 路来源收集（主线组+成员 / ambush / cooling / pool 补入）
2. 去重逻辑（同名板块不重复入候选）
3. member_to_group 映射正确性
4. 池外补入的 detail 解析（命中 Node 2 vs strength_lookup 合成）
5. 空输入边界
6. 0903 基线回放（24 条）
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.schemas import (
    AmbushCandidate,
    MainlineGroup,
    SectorContext,
    SectorDetail,
)
from src.got.nodes.aggregator import AggregatorNode


def _make_sector(
    mainline=None,
    ambush=None,
    cooling=None,
    near_miss=None,
    top_by_score=None,
    exhausted=None,
    strength_lookup=None,
):
    """构造最小 SectorContext 用于测试。"""
    return SectorContext(
        active_mainline=mainline or [],
        ambush=ambush or [],
        cooling_down=cooling or [],
        near_miss_ambush=near_miss or [],
        top_by_score=top_by_score or [],
        exhausted_mainline=exhausted or [],
        strength_lookup=strength_lookup or {},
    )


def _make_detail(name, score=50.0, state="active_mainline"):
    return SectorDetail(code=f"TEST_{name}", name=name, state=state, strength_score=score)


class TestCollectSectorCandidates(unittest.TestCase):
    """测试 _collect_sector_candidates 4 路来源。"""

    def setUp(self):
        self.node = AggregatorNode()

    # ── 1. 主线板块 ──

    def test_mainline_group_and_members(self):
        """主线组名 + 成员展开，member_to_group 正确。"""
        member_a = _make_detail("AI软件", 90.0)
        member_b = _make_detail("AIGC", 80.0)
        group = MainlineGroup(
            group_name="AI软件",
            group_score=90.0,
            tier="A",
            mainline_quality="strong",
            members=[member_a, member_b],
        )
        sector = _make_sector(mainline=[group])

        candidates, member_to_group = self.node._collect_sector_candidates(sector)

        names = [c[0] for c in candidates]
        # 组名 + AIGC 成员（AI软件 已作为组名入候选，成员展开时去重）
        self.assertIn("AI软件", names)
        self.assertIn("AIGC", names)
        self.assertEqual(len(candidates), 2)
        # member_to_group 只含非组名成员
        self.assertEqual(member_to_group, {"AIGC": "AI软件"})
        # role_info 正确
        for _, _, role_info in candidates:
            self.assertEqual(role_info["role"], "mainline")
            self.assertEqual(role_info["tier"], "A")

    def test_mainline_best_member_as_group_representative(self):
        """组名候选的 detail 是最强成员。"""
        member_weak = _make_detail("消费A", 40.0)
        member_strong = _make_detail("消费B", 85.0)
        group = MainlineGroup(
            group_name="消费A",
            group_score=85.0,
            tier="B",
            mainline_quality="weak",
            members=[member_weak, member_strong],
        )
        sector = _make_sector(mainline=[group])

        candidates, _ = self.node._collect_sector_candidates(sector)

        # 组名 "消费A" 的 detail 应该是最强成员（消费B）
        group_candidate = [c for c in candidates if c[0] == "消费A"][0]
        self.assertEqual(group_candidate[1].name, "消费B")
        self.assertEqual(group_candidate[1].strength_score, 85.0)

    # ── 2. 埋伏板块 ──

    def test_ambush_sectors(self):
        """埋伏板块独立收集。"""
        ambush_detail = _make_detail("量子计算", 35.0, state="ambush")
        sector = _make_sector(ambush=[ambush_detail])

        candidates, member_to_group = self.node._collect_sector_candidates(sector)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "量子计算")
        self.assertEqual(candidates[0][2]["role"], "ambush")
        self.assertEqual(member_to_group, {})

    # ── 3. 高位降温 ──

    def test_cooling_down_sectors(self):
        """降温板块独立收集。"""
        cooling_detail = _make_detail("旅游", 70.0, state="high_position_cooling")
        sector = _make_sector(cooling=[cooling_detail])

        candidates, _ = self.node._collect_sector_candidates(sector)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "旅游")
        self.assertEqual(candidates[0][2]["role"], "cooling")
        self.assertEqual(candidates[0][2]["quality"], "weak")

    # ── 4. 池外补入 ──

    def test_pool_fallback_sector_with_node2_detail(self):
        """池外补入板块命中 Node 2 的 near_miss_ambush。"""
        near_miss_detail = _make_detail("储能", 45.0, state="ambush")
        sector = _make_sector(near_miss=[near_miss_detail])
        pool = [AmbushCandidate(sector_name="储能", sector_code="T_储能", catalyst_score=1.2, category_tag="drift")]

        candidates, _ = self.node._collect_sector_candidates(sector, ambush_pool=pool)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "储能")
        self.assertEqual(candidates[0][2]["role"], "ambush")
        # detail 来自 near_miss_ambush
        self.assertIsNotNone(candidates[0][1])

    def test_pool_fallback_sector_with_strength_lookup(self):
        """池外补入板块无 Node 2 detail，走 strength_lookup 合成。"""
        sector = _make_sector(strength_lookup={"锂电": 60.0})
        pool = [AmbushCandidate(sector_name="锂电", sector_code="T_锂电", catalyst_score=0.8, category_tag="fade")]

        candidates, _ = self.node._collect_sector_candidates(sector, ambush_pool=pool)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0][0], "锂电")
        # detail 由 _resolve_pool_sector_detail 合成
        detail = candidates[0][1]
        self.assertIsNotNone(detail)
        self.assertEqual(detail.strength_score, 60.0)

    def test_pool_fallback_sector_no_data(self):
        """池外补入板块无任何数据源，detail 为 None。"""
        sector = _make_sector()
        pool = [AmbushCandidate(sector_name="未知板块", sector_code="", catalyst_score=0.5, category_tag="binary")]

        candidates, _ = self.node._collect_sector_candidates(sector, ambush_pool=pool)

        self.assertEqual(len(candidates), 1)
        self.assertIsNone(candidates[0][1])

    # ── 5. 去重逻辑 ──

    def test_dedup_across_sources(self):
        """同名板块在不同来源只出现一次（主线优先）。"""
        mainline_detail = _make_detail("机器人", 88.0)
        group = MainlineGroup(
            group_name="机器人",
            group_score=88.0,
            tier="S",
            mainline_quality="strong",
            members=[mainline_detail],
        )
        ambush_detail = _make_detail("机器人", 55.0, state="ambush")
        sector = _make_sector(mainline=[group], ambush=[ambush_detail])

        candidates, _ = self.node._collect_sector_candidates(sector)

        names = [c[0] for c in candidates]
        self.assertEqual(names.count("机器人"), 1)
        # 主线优先：role 是 mainline，不是 ambush
        robot_candidate = [c for c in candidates if c[0] == "机器人"][0]
        self.assertEqual(robot_candidate[2]["role"], "mainline")

    def test_dedup_pool_vs_node2(self):
        """池外补入板块已在 Node 2 ambush 名单时不重复。"""
        ambush_detail = _make_detail("光伏", 40.0, state="ambush")
        sector = _make_sector(ambush=[ambush_detail])
        pool = [AmbushCandidate(sector_name="光伏", sector_code="T_光伏", catalyst_score=1.0, category_tag="drift")]

        candidates, _ = self.node._collect_sector_candidates(sector, ambush_pool=pool)

        names = [c[0] for c in candidates]
        self.assertEqual(names.count("光伏"), 1)

    # ── 6. 空输入边界 ──

    def test_empty_sector(self):
        """全空 SectorContext 返回空列表。"""
        sector = _make_sector()

        candidates, member_to_group = self.node._collect_sector_candidates(sector)

        self.assertEqual(candidates, [])
        self.assertEqual(member_to_group, {})

    def test_empty_pool_ignored(self):
        """ambush_pool=None 与 ambush_pool=[] 等价。"""
        ambush_detail = _make_detail("风电", 38.0, state="ambush")
        sector = _make_sector(ambush=[ambush_detail])

        c1, _ = self.node._collect_sector_candidates(sector, ambush_pool=None)
        c2, _ = self.node._collect_sector_candidates(sector, ambush_pool=[])

        self.assertEqual(len(c1), len(c2))
        self.assertEqual(c1[0][0], c2[0][0])

    # ── 7. 多路混合 ──

    def test_mixed_sources(self):
        """4 路来源同时存在，优先级正确。"""
        # 主线
        ml_member = _make_detail("半导体", 85.0)
        group = MainlineGroup(
            group_name="半导体",
            group_score=85.0,
            tier="A",
            mainline_quality="strong",
            members=[ml_member],
        )
        # 埋伏
        ambush_d = _make_detail("光刻机", 42.0, state="ambush")
        # 降温
        cool_d = _make_detail("旅游", 70.0, state="high_position_cooling")
        # 池外
        sector = _make_sector(
            mainline=[group],
            ambush=[ambush_d],
            cooling=[cool_d],
            strength_lookup={"第三代半": 55.0},
        )
        pool = [
            AmbushCandidate(sector_name="第三代半", sector_code="T_第三代半", catalyst_score=1.5, category_tag="drift"),
        ]

        candidates, m2g = self.node._collect_sector_candidates(sector, ambush_pool=pool)

        names = [c[0] for c in candidates]
        self.assertEqual(len(names), 4)
        self.assertIn("半导体", names)
        self.assertIn("光刻机", names)
        self.assertIn("旅游", names)
        self.assertIn("第三代半", names)
        self.assertEqual(m2g, {})  # 半导体是唯一成员，即组名本身


class TestCollectCandidatesReplay0903(unittest.TestCase):
    """0903 基线回放：确认候选收集数量与归档一致。"""

    def test_0903_candidate_count(self):
        """0903 归档数据应有 24 个候选板块。"""
        from datetime import date
        from src.got.archive import load_daily_contexts

        contexts = load_daily_contexts(date(2026, 9, 3))
        if contexts is None:
            self.skipTest("0903 归档数据不可用")

        sector = contexts.get("sector")
        if sector is None:
            self.skipTest("sector context 不可用")

        node = AggregatorNode()

        # 模拟 ambush_pool（从 unified context 的 ambush_pool 字段获取）
        unified = contexts.get("unified")
        ambush_pool = getattr(unified, "ambush_pool", []) if unified else []

        candidates, member_to_group = node._collect_sector_candidates(sector, ambush_pool)

        # 0903 基线：24 个 confirmed_sectors
        self.assertEqual(len(candidates), 24, f"0903 基线应为 24 条候选，实际 {len(candidates)}")
        # member_to_group 非空（主线有多成员组）
        self.assertGreater(len(member_to_group), 0)


if __name__ == "__main__":
    unittest.main()
