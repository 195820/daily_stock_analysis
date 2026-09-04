# -*- coding: utf-8 -*-
"""
§5 MarketThemeContext + §9 canonical_id 双写测试

覆盖：
- MarketThemeContext 模型定义
- generate_canonical_id 函数
"""

import pytest


# =============================================================================
# §5 MarketThemeContext 测试
# =============================================================================

class TestMarketThemeContext:
    """MarketThemeContext 模型测试"""

    def test_unknown_default(self):
        """验证 unknown 默认构造"""
        from src.got.market_theme import MarketThemeContext
        ctx = MarketThemeContext.unknown()
        assert ctx.status == "unknown"
        assert ctx.is_available is False
        assert len(ctx.active_themes) == 0

    def test_model_fields(self):
        """验证模型字段"""
        from src.got.market_theme import MarketThemeContext, MarketThemeItem, RankedThemeItem
        ctx = MarketThemeContext(
            status="ok",
            active_themes=[
                MarketThemeItem(name="AI", phase="accelerating", strength_score=85),
            ],
            leading_industries=[
                RankedThemeItem(name="半导体", rank=1, change_pct=2.5),
            ],
        )
        assert ctx.status == "ok"
        assert ctx.is_available is True
        assert len(ctx.active_themes) == 1
        assert ctx.active_themes[0].phase == "accelerating"

    def test_get_market_theme_context_singleton(self):
        """验证全局实例"""
        from src.got.market_theme import get_market_theme_context, reset_market_theme_context
        reset_market_theme_context()
        ctx1 = get_market_theme_context()
        ctx2 = get_market_theme_context()
        assert ctx1 is ctx2
        assert ctx1.status == "unknown"


# =============================================================================
# §9 canonical_id 测试
# =============================================================================

class TestCanonicalId:
    """canonical_id 生成函数测试"""

    def test_shanghai_stock(self):
        """上交所股票（6xxxxx → sh）"""
        from src.storage import generate_canonical_id
        assert generate_canonical_id("600519") == "sh600519"  # 贵州茅台
        assert generate_canonical_id("601318") == "sh601318"  # 中国平安

    def test_shenzhen_stock(self):
        """深交所股票（0xxxxx/3xxxxx → sz）"""
        from src.storage import generate_canonical_id
        assert generate_canonical_id("000002") == "sz000002"  # 万科 A
        assert generate_canonical_id("300750") == "sz300750"  # 宁德时代

    def test_beijing_stock(self):
        """北交所股票（8xxxxx/4xxxxx → bj）"""
        from src.storage import generate_canonical_id
        assert generate_canonical_id("830799") == "bj830799"
        assert generate_canonical_id("430047") == "bj430047"

    def test_bare_code_is_stock_not_index(self):
        """C2：裸码 000001 → 股票 sz000001（平安银行），不误标为上证指数 sh000001。

        与 index_registry.parse_analysis_target 的「裸码→股票」消歧规则一致：
        stock_daily 存的是股票日线，裸码必须按股票解释（此前误查 IndexRegistry
        会把平安银行 000001 标成上证指数 sh000001）。无论注册表是否可用，
        裸码都回退到代码模式推断 → sz000001，结果确定。
        """
        from src.storage import generate_canonical_id
        assert generate_canonical_id("000001") == "sz000001"  # 平安银行（股票）
        assert generate_canonical_id("000300") == "sz000300"  # 裸码按股票，非沪深300指数

    def test_explicit_index_identity(self):
        """C2：显式指数身份（后缀/前缀/中文名）→ 指数 canonical_id"""
        from src.storage import generate_canonical_id
        assert generate_canonical_id("000001.SH") == "sh000001"   # 后缀 → 上证指数
        assert generate_canonical_id("sh000001") == "sh000001"    # 前缀 → 上证指数
        assert generate_canonical_id("上证指数") == "sh000001"      # 中文名 → 上证指数

    def test_empty_invalid(self):
        """空/无效输入"""
        from src.storage import generate_canonical_id
        assert generate_canonical_id("") == ""
        assert generate_canonical_id(None) == ""
        assert generate_canonical_id("abc") == ""
        assert generate_canonical_id("12345") == ""  # 5 位数不够
