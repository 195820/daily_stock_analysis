"""Tests for family_embed V2 scoring structure (§7 #12 WI).

验证打分结构修正：
- V2 开关关闭时：保持旧逻辑（纯 cosine）
- V2 开关开启时：两-term 打分 = cos*(1-W) + keyword*W
- 语义兜底：关键词覆盖率 ≥0.8 → bypass embedding
"""
import pytest
from src.got.family_embed import (
    FALLBACK_FAMILIES,
    compute_keyword_overlap,
    check_keyword_bypass,
    select_family,
)
from src.got.family_keywords import FAMILY_KEYWORDS


class TestKeywordOverlap:
    """关键词覆盖率计算测试"""

    def test_exact_match(self):
        """板块名完全匹配关键词"""
        board_name = "混合现实"
        keywords = ["混合现实", "MR"]
        overlap = compute_keyword_overlap(board_name, keywords)
        assert overlap == 1.0, f"期望 1.0，实际 {overlap}"

    def test_partial_match(self):
        """板块名部分匹配关键词"""
        board_name = "虚拟现实"
        keywords = ["混合现实", "MR"]
        overlap = compute_keyword_overlap(board_name, keywords)
        # "虚拟" 2字 + "现实" 2字 = 4字中匹配 2字 = 0.5
        assert 0.4 < overlap < 0.6, f"期望 ~0.5，实际 {overlap}"

    def test_no_match(self):
        """板块名不匹配任何关键词"""
        board_name = "英伟达概"
        keywords = ["芯片", "GPU"]
        overlap = compute_keyword_overlap(board_name, keywords)
        assert overlap == 0.0, f"期望 0.0，实际 {overlap}"

    def test_empty_keywords(self):
        """空关键词列表"""
        board_name = "混合现实"
        keywords = []
        overlap = compute_keyword_overlap(board_name, keywords)
        assert overlap == 0.0


class TestKeywordBypass:
    """语义兜底测试"""

    def test_high_coverage_bypass(self):
        """高覆盖率触发 bypass"""
        board_name = "混合现实"
        keywords = ["混合现实", "MR"]
        assert check_keyword_bypass(board_name, keywords) is True

    def test_low_coverage_no_bypass(self):
        """低覆盖率不触发 bypass"""
        board_name = "英伟达概"
        keywords = ["芯片", "GPU"]
        assert check_keyword_bypass(board_name, keywords) is False

    def test_threshold_boundary(self):
        """阈值边界测试（0.8）"""
        # 5字板块名，关键词命中4字 = 4/5 = 0.8 ≥ 0.8 → bypass（字符集口径，≥ 判定含等号）
        board_name = "虚拟现实板"
        keywords = ["虚拟现实"]  # 匹配 "虚" "拟" "现" "实" = 4字
        assert compute_keyword_overlap(board_name, keywords) == 0.8
        assert check_keyword_bypass(board_name, keywords) is True

    def test_just_above_threshold(self):
        """刚好超过阈值"""
        # 5字板块名，匹配5字 = 1.0 ≥ 0.8 → bypass
        board_name = "虚拟现实板"
        keywords = ["虚拟现实板"]  # 匹配全部 5字
        assert check_keyword_bypass(board_name, keywords) is True


class TestSelectFamily:
    """select_family 阈值判定测试"""

    def test_no_candidates(self):
        """无候选家族"""
        result = select_family([], min_score=0.5, min_margin=0.05)
        assert result["fallback"] is False
        assert result["reason"] == "no_candidate"

    def test_low_score_reject(self):
        """分数不足被拒"""
        scores = [("AI硬件", 0.4), ("机器人", 0.3)]
        result = select_family(scores, min_score=0.5, min_margin=0.05)
        assert result["fallback"] is False
        assert result["reason"] == "low_score"

    def test_ambiguous_reject(self):
        """margin 不足被拒"""
        scores = [("AI硬件", 0.6), ("机器人", 0.56)]  # margin = 0.04 < 0.05
        result = select_family(scores, min_score=0.5, min_margin=0.05)
        assert result["fallback"] is False
        assert result["reason"] == "ambiguous"

    def test_accept(self):
        """满足条件接受"""
        scores = [("AI硬件", 0.6), ("机器人", 0.4)]  # margin = 0.2 ≥ 0.05
        result = select_family(scores, min_score=0.5, min_margin=0.05)
        assert result["fallback"] is True
        assert result["reason"] == "ok"
        assert result["family"] == "AI硬件"
