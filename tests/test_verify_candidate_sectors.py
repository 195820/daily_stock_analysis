# -*- coding: utf-8 -*-
"""候选股板块交叉验证测试（_verify_candidate_sectors）

覆盖 2026-09-01 升级后的验证链路：
- L1 别名归一化匹配（"券商" ↔ "券商概念"），命中时不触发 LLM
- L2 LLM 仲裁：命中保留 / 未命中清空
- LLM 不可用时降级为信任问财（保留标签）
- efinance 获取失败时保留标签
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.got.nodes.portfolio_decision import PortfolioDecisionNode


def _stock(code="600030", name="中信证券", sector="券商"):
    return SimpleNamespace(code=code, name=name, sector=sector, sector_name=sector)


class TestVerifyCandidateSectors(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.node = PortfolioDecisionNode()
        self.intel_map = {"券商": {}, "参股券商": {}}

    async def _run(self, stocks, boards_by_code, llm_content=None, llm_raises=False):
        """运行验证；返回 (结果列表, LLM 是否被调用)"""
        def get_belong_boards(code):
            return boards_by_code.get(code, [])

        mock_manager = MagicMock()
        mock_manager.get_belong_boards.side_effect = get_belong_boards

        mock_adapter = MagicMock()
        if llm_raises:
            mock_adapter.call_text.side_effect = RuntimeError("llm down")
        else:
            mock_response = SimpleNamespace(content=llm_content)
            mock_adapter.call_text.return_value = mock_response

        with patch("data_provider.base.DataFetcherManager", return_value=mock_manager), \
             patch("src.agent.llm_adapter.LLMToolAdapter", return_value=mock_adapter):
            result = await self.node._verify_candidate_sectors(stocks, self.intel_map)
        return result, mock_adapter.call_text.called

    async def test_alias_match_keeps_tag_without_llm(self):
        """问财标签'券商' + efinance 返回'券商概念' → L1 别名命中，保留标签且不调 LLM"""
        stock = _stock()
        result, llm_called = await self._run(
            [stock],
            {"600030": [{"name": "券商概念"}, {"name": "沪股通"}]},
        )
        self.assertEqual(result[0].sector, "券商")
        self.assertFalse(llm_called)

    async def test_exact_match_keeps_tag(self):
        stock = _stock()
        result, llm_called = await self._run(
            [stock],
            {"600030": [{"name": "券商"}]},
        )
        self.assertEqual(result[0].sector, "券商")
        self.assertFalse(llm_called)

    async def test_llm_arbitration_hit_keeps_tag(self):
        """L1 未命中 → LLM 仲裁判定同一概念 → 保留标签"""
        stock = _stock(sector="参股券商")
        llm_json = '[{"index": 0, "matched_board": "参股券商概念"}]'
        result, llm_called = await self._run(
            [stock],
            {"600030": [{"name": "参股券商概念"}, {"name": "互联网金融"}]},
            llm_content=llm_json,
        )
        self.assertTrue(llm_called)
        self.assertEqual(result[0].sector, "参股券商")

    async def test_llm_arbitration_miss_clears_tag(self):
        """L1 未命中 + LLM 判不匹配（含防蹭概念场景）→ 清空标签"""
        stock = _stock(code="002552", name="利君股份", sector="券商")
        llm_json = '[{"index": 0, "matched_board": null}]'
        result, llm_called = await self._run(
            [stock],
            {"002552": [{"name": "机械设备"}, {"name": "专精特新"}]},
            llm_content=llm_json,
        )
        self.assertTrue(llm_called)
        self.assertEqual(result[0].sector, "")
        self.assertEqual(result[0].sector_name, "")

    async def test_llm_hallucinated_board_name_is_discarded(self):
        """LLM 返回的 matched_board 不在实际板块列表中 → 视为未命中并清空"""
        stock = _stock()
        llm_json = '[{"index": 0, "matched_board": "券商"}]'  # 列表中没有"券商"
        result, _ = await self._run(
            [stock],
            {"600030": [{"name": "机械设备"}]},
            llm_content=llm_json,
        )
        self.assertEqual(result[0].sector, "")

    async def test_llm_failure_falls_back_to_keep_tag(self):
        """LLM 异常 → 降级为信任问财，保留标签"""
        stock = _stock()
        result, _ = await self._run(
            [stock],
            {"600030": [{"name": "机械设备"}]},
            llm_raises=True,
        )
        self.assertEqual(result[0].sector, "券商")

    async def test_efinance_empty_keeps_tag(self):
        """efinance 获取失败（空列表）→ 保留问财标签"""
        stock = _stock()
        result, llm_called = await self._run([stock], {})
        self.assertEqual(result[0].sector, "券商")
        self.assertFalse(llm_called)

    async def test_unknown_sector_skips_verification(self):
        """标签不在 sector_intel_map 中 → 跳过验证"""
        stock = _stock(sector="未追踪板块")
        result, llm_called = await self._run(
            [stock],
            {"600030": [{"name": "其他"}]},
        )
        self.assertEqual(result[0].sector, "未追踪板块")
        self.assertFalse(llm_called)

    async def test_more_than_15_finalists_skip_all(self):
        stocks = [_stock(code=str(600000 + i), name=f"S{i}") for i in range(16)]
        with patch("data_provider.base.DataFetcherManager") as mock_mgr:
            result = await self.node._verify_candidate_sectors(stocks, self.intel_map)
        self.assertEqual(len(result), 16)
        mock_mgr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
