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
import logging
import time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from src.got.board_filter import is_noise_board

logger = logging.getLogger(__name__)


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
# 便捷函数 + Step 2 排名构建器（方案 A：mootdx 自算）
# =============================================================================

_default_context: Optional[MarketThemeContext] = None

# 进程内 TTL 缓存（成功 60s / 失败 30s，沿用 §5.2 远端口径）
_CACHE_TTL_OK_SECONDS = 60.0
_CACHE_TTL_FAIL_SECONDS = 30.0
_theme_cache: Dict[str, object] = {"context": None, "expires_at": 0.0}

_TOP_N = 10          # leading_concepts / leading_industries / lagging_themes 各取条数
_ACTIVE_N = 15       # active_themes（accelerating ∪ warming）上限


def _classify_phase(change_1d: float, change_3d: float, change_5d: float) -> str:
    """题材阶段判定（写死规则，避免执行期拍脑袋）。

    数据源只提供 change_1d/3d/5d 三时框（mootdx 11 根 K 线聚合），无独立 11 日字段，
    故以三时框同向性代替「加速」判据：
    - accelerating：三时框全部 >0（近-中-长同向向上 = 主升加速）
    - warming：5 日 >0 但未全面加速（中期升温，短期动能不齐）
    - cooling：5 日 <=0（走弱/退潮；5 日负而 3 日正为典型退潮）
    无效数据（板块缺失/取数失败）不经此函数——由 build_theme_context 的
    unknown() 兜底路径整体承担（审查修复 #3：原穷尽分支后的 unknown 返回不可达，删除）。
    """
    if change_5d > 0 and change_3d > 0 and change_1d > 0:
        return "accelerating"
    if change_5d > 0:
        return "warming"
    return "cooling"


def _strength_score(change_5d: float, all_changes: List[float]) -> int:
    """把 5 日动量映射为 0-100 强度分（在全体板块中的百分位）。"""
    if not all_changes:
        return 0
    rank = sum(1 for c in all_changes if c <= change_5d)
    return max(0, min(100, int(rank / len(all_changes) * 100)))


def _build_from_quick(
    code_name: Dict[str, str],
    quick: Dict[str, Dict[str, float]],
) -> List[Dict[str, object]]:
    """把单域（概念或行业）的 quick_change 转为排序后的条目列表（含 phase/strength）。"""
    valid = [(code, data) for code, data in quick.items() if code in code_name]
    all_c5 = [d["change_5d"] for _, d in valid]
    rows: List[Dict[str, object]] = []
    for code, data in valid:
        c1, c3, c5 = data["change_1d"], data["change_3d"], data["change_5d"]
        rows.append({
            "code": code,
            "name": code_name[code],
            "change_pct": float(c1),          # 当日框（对齐 §2.1 口径）
            "change_5d": float(c5),
            "phase": _classify_phase(c1, c3, c5),
            "strength_score": _strength_score(c5, all_c5),
        })
    rows.sort(key=lambda r: r["change_5d"], reverse=True)
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows


def _cache_set(context: MarketThemeContext, *, ok: bool) -> None:
    _theme_cache["context"] = context
    ttl = _CACHE_TTL_OK_SECONDS if ok else _CACHE_TTL_FAIL_SECONDS
    _theme_cache["expires_at"] = time.monotonic() + ttl
    global _default_context
    _default_context = context


def build_theme_context(fetcher_manager, *, use_cache: bool = True) -> MarketThemeContext:
    """用 mootdx 独立板块排名构造 MarketThemeContext（方案 A，§5 远端整合 Step 2）。

    取数链：get_all_concept_boards + get_all_industry_boards（code+name）
    → get_board_quick_change(all_codes)（一次批量拿 11 日多时框动量，禁止逐板 kline）。
    任何一环取数失败 → 回退 MarketThemeContext.unknown()（现状即 unknown，失败=零行为）。

    Args:
        fetcher_manager: DataFetcherManager（或实现三方法的等价对象）。
        use_cache: 是否走进程内 TTL 缓存（测试传 False 强制重算）。
    """
    now = time.monotonic()
    cached = _theme_cache.get("context")
    if use_cache and isinstance(cached, MarketThemeContext) and now < float(_theme_cache["expires_at"]):
        return cached  # type: ignore[return-value]

    try:
        concepts = fetcher_manager.get_all_concept_boards() or []
        industries = fetcher_manager.get_all_industry_boards() or []
        # 源头净化概念域：剔除统计指数/行情事件/财务标签等非可交易噪声板块
        # （昨ST连板/昨日涨停等影子线污染根因，缺陷①）。行业域不动。
        # 过滤后概念可能为空 → status 自然翻 partial（既有逻辑覆盖）。
        concept_map = {
            b["code"]: b["name"]
            for b in concepts
            if b.get("code") and b.get("name") and not is_noise_board(b["name"])
        }
        industry_map = {b["code"]: b["name"] for b in industries if b.get("code") and b.get("name")}
        all_codes = list(concept_map.keys() | industry_map.keys())
        if not all_codes:
            logger.warning("[MarketTheme] 板块清单为空，回退 unknown")
            ctx = MarketThemeContext.unknown()
            ctx.data_quality.source = "mootdx_quick_change"
            _cache_set(ctx, ok=False)
            return ctx

        quick = fetcher_manager.get_board_quick_change(all_codes) or {}
        if not quick:
            logger.warning("[MarketTheme] 快筛全部失败，回退 unknown")
            ctx = MarketThemeContext.unknown()
            ctx.data_quality.source = "mootdx_quick_change"
            _cache_set(ctx, ok=False)
            return ctx

        concept_rows = _build_from_quick(concept_map, quick)
        industry_rows = _build_from_quick(industry_map, quick)

        leading_concepts = [
            RankedThemeItem(name=r["name"], code=r["code"], rank=r["rank"], change_pct=r["change_pct"])
            for r in concept_rows[:_TOP_N]
        ]
        leading_industries = [
            RankedThemeItem(name=r["name"], code=r["code"], rank=r["rank"], change_pct=r["change_pct"])
            for r in industry_rows[:_TOP_N]
        ]

        # 落后题材：概念域倒序取底 N（rank 仍从全域名次来）
        lagging_rows = [r for r in concept_rows if r["change_5d"] < 0][-_TOP_N:]
        lagging_themes = [
            RankedThemeItem(name=r["name"], code=r["code"], rank=r["rank"], change_pct=r["change_pct"])
            for r in lagging_rows
        ]

        # 活跃题材：accelerating ∪ warming 前 _ACTIVE_N（概念域优先，按 5 日动量已排序）
        active_items: List[MarketThemeItem] = []
        for r in concept_rows:
            if r["phase"] in ("accelerating", "warming"):
                active_items.append(MarketThemeItem(
                    name=r["name"], code=r["code"], rank=r["rank"], change_pct=r["change_pct"],
                    phase=r["phase"], strength_score=r["strength_score"],
                    reason=f"5d={r['change_5d']:+.1f}% 当日={r['change_pct']:+.1f}% → {r['phase']}",
                ))
            if len(active_items) >= _ACTIVE_N:
                break

        breadth = ThemeBreadth(
            warming_count=sum(1 for r in concept_rows if r["phase"] == "warming"),
            accelerating_count=sum(1 for r in concept_rows if r["phase"] == "accelerating"),
            cooling_count=sum(1 for r in concept_rows if r["phase"] == "cooling"),
            unknown_count=sum(1 for r in concept_rows if r["phase"] == "unknown"),
            total_count=len(concept_rows),
        )

        # 名称匹配覆盖率（供影子期评估 mootdx 板块名 vs GOT 现有板块名错配率）
        coverage = (len(quick) / len(all_codes)) if all_codes else 0.0
        status = "ok" if active_items else "partial"
        ctx = MarketThemeContext(
            status=status,
            active_themes=active_items,
            leading_industries=leading_industries,
            leading_concepts=leading_concepts,
            lagging_themes=lagging_themes,
            theme_breadth=breadth,
            data_quality=MarketStructureDataQuality(
                status=status,
                source="mootdx_quick_change",
                missing_fields=[] if status == "ok" else ["active_themes"],
            ),
        )
        ctx_name_coverage = round(coverage, 3)
        _cache_set(ctx, ok=True)
        logger.info(
            "[MarketTheme] 构造完成 status=%s active=%d 快筛覆盖=%.1f%%",
            status, len(active_items), coverage * 100,
        )
        # 覆盖率挂到 data_quality（模型无专字段，附加到 source 备注，供归档展示）
        ctx.data_quality.source = f"mootdx_quick_change(name_match_coverage~{ctx_name_coverage})"
        return ctx
    except Exception as exc:  # noqa: BLE001 - 取数任何异常都降级为 unknown（零行为）
        logger.warning("[MarketTheme] 构造异常，回退 unknown: %s", exc)
        ctx = MarketThemeContext.unknown()
        ctx.data_quality.source = "mootdx_quick_change"
        _cache_set(ctx, ok=False)
        return ctx


def get_market_theme_context() -> MarketThemeContext:
    """获取最近一次构造的市场结构上下文（未构造则返回 unknown 默认值）。

    Step 2 接入后由 aggregator 调 build_theme_context 填充 _default_context；
    本函数只读，不触发取数（避免消费点隐式发起网络请求）。
    """
    global _default_context
    if _default_context is None:
        _default_context = MarketThemeContext.unknown()
    return _default_context


def reset_market_theme_context():
    """重置全局上下文与 TTL 缓存（测试用）。"""
    global _default_context
    _default_context = None
    _theme_cache["context"] = None
    _theme_cache["expires_at"] = 0.0
