# -*- coding: utf-8 -*-
"""
MarketThemeContext 模型定义（§5 远端整合 Step 1）

为 Node 2 板块分析补充题材阶段（warming / accelerating / cooling）和龙头股角色维度，
替代当前纯截面百分位排名。

当前状态：Step 1 接口预留
- 定义 MarketThemeContext 模型
- 提供 unknown 默认构造（不参与评分）
- 不接入 aggregator.py（等待 A6 转正后实施 Step 2）

数据源问题：
- MarketHotspotService 依赖东财 push2 板块排名接口（已被封）
- 方案 A：用 mootdx TCP 板块 K 线数据自行计算排名（待实施）
- 方案 B：从 Node 2 已有数据构造（零信息增益，已否决）
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class RankedThemeItem(BaseModel):
    """排名型题材条目。"""
    name: str
    code: str = ""
    rank: int = 0
    change_pct: float = 0.0  # 涨跌幅 (%)


class MarketThemeItem(RankedThemeItem):
    """题材条目（含阶段 + 强度）。"""
    phase: str = "unknown"  # warming / accelerating / cooling / unknown
    strength_score: Optional[int] = None  # 0-100 强度分
    reason: Optional[str] = None  # 阶段判定理由


class ThemeBreadth(BaseModel):
    """题材宽度统计。"""
    warming_count: int = 0
    accelerating_count: int = 0
    cooling_count: int = 0
    unknown_count: int = 0
    total_count: int = 0


class MarketStructureDataQuality(BaseModel):
    """数据结构质量标记。"""
    status: str = "unknown"  # ok / partial / unknown
    source: str = ""  # 数据来源标识
    missing_fields: List[str] = Field(default_factory=list)


class MarketThemeContext(BaseModel):
    """市场结构上下文（§5 远端整合）。

    为 Node 2 板块分析补充题材阶段和龙头角色维度。

    当前实现：
    - status="unknown" 时不参与 Node 6 评分（接口预留）
    - 待 Step 2 接入 mootdx 独立板块排名后产生真实信号
    """
    status: str = "unknown"  # ok / partial / unknown
    active_themes: List[MarketThemeItem] = Field(default_factory=list)  # 活跃题材（含阶段+强度）
    leading_industries: List[RankedThemeItem] = Field(default_factory=list)  # 行业 Top
    leading_concepts: List[RankedThemeItem] = Field(default_factory=list)  # 概念 Top
    lagging_themes: List[RankedThemeItem] = Field(default_factory=list)  # 落后题材
    theme_breadth: ThemeBreadth = Field(default_factory=ThemeBreadth)  # 宽度统计
    data_quality: MarketStructureDataQuality = Field(default_factory=MarketStructureDataQuality)  # 数据质量

    @classmethod
    def unknown(cls) -> "MarketThemeContext":
        """构造 unknown 状态的默认实例（不参与评分）。"""
        return cls(
            status="unknown",
            data_quality=MarketStructureDataQuality(
                status="unknown",
                source="not_implemented",
                missing_fields=["active_themes", "leading_industries", "leading_concepts"],
            ),
        )

    @property
    def is_available(self) -> bool:
        """数据是否可用（参与评分）。"""
        return self.status == "ok" and len(self.active_themes) > 0


# =============================================================================
# 便捷函数
# =============================================================================

_default_context: Optional[MarketThemeContext] = None


def get_market_theme_context() -> MarketThemeContext:
    """获取市场结构上下文（当前返回 unknown 默认值）。

    Step 2 实施后将接入 mootdx 板块排名数据。
    """
    global _default_context
    if _default_context is None:
        _default_context = MarketThemeContext.unknown()
    return _default_context


def reset_market_theme_context():
    """重置全局上下文（测试用）。"""
    global _default_context
    _default_context = None
