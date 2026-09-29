"""
缺陷③C 单测：newsnow-xueqiu 低保真单维备份适配器（Node 5）

覆盖：
- _fetch_xueqiu_newsnow_fallback：url 正则取代码、科技成长过滤、hot_score 作
  attention 代理、降序取 top N、异常/空源降级为空。
- _fetch_xueqiu 接线：akshare 三榜全空才触发备份；主源有数据时绝不触碰备份。
"""

import asyncio
from unittest.mock import patch

import pandas as pd

from src.got.nodes.sentiment_analysis import SentimentAnalysisNode


def _item(name, url, hot):
    return {"title": name, "url": url, "extra": {"hot_score": hot}}


# =============================================================================
# 备份适配器：提取/过滤/排序
# =============================================================================

class TestNewsnowFallbackExtraction:
    def test_extracts_codes_and_filters(self):
        node = SentimentAnalysisNode()
        items = [
            _item("中芯半导体", "https://xueqiu.com/S/SH688981", 200),  # 半导体 ✓
            _item("某某稀土", "https://xueqiu.com/S/SZ000831", 900),      # 稀土 ✓
            _item("无关名称", "https://xueqiu.com/S/SH600000", 5000),      # 无关键词 → 剔除
            _item("软件股份", "https://xueqiu.com/statuses/x", 400),       # 无 /S/ 代码 → 剔除
            _item("白酒龙头", "https://xueqiu.com/S/BJ830799", 100),      # 白酒 ✓ BJ 前缀
        ]
        with patch(
            "src.got.intelligence_service._fetch_newsnow_template", return_value=items
        ):
            out = asyncio.run(node._fetch_xueqiu_newsnow_fallback())

        # 按 attention 降序：900/200/100
        assert [r["stock_code"] for r in out] == ["000831", "688981", "830799"]
        assert all(r["dimension"] == "tweet" for r in out)
        assert all(r["price"] == 0.0 for r in out)
        assert [r["attention"] for r in out] == [900, 200, 100]

    def test_empty_source_returns_empty(self):
        node = SentimentAnalysisNode()
        with patch(
            "src.got.intelligence_service._fetch_newsnow_template", return_value=[]
        ):
            assert asyncio.run(node._fetch_xueqiu_newsnow_fallback()) == []

    def test_exception_degrades_to_empty(self):
        node = SentimentAnalysisNode()
        with patch(
            "src.got.intelligence_service._fetch_newsnow_template",
            side_effect=RuntimeError("boom"),
        ):
            assert asyncio.run(node._fetch_xueqiu_newsnow_fallback()) == []

    def test_bad_hot_score_falls_back_to_zero(self):
        node = SentimentAnalysisNode()
        items = [_item("某某稀土", "https://xueqiu.com/S/SZ000831", "N/A")]
        with patch(
            "src.got.intelligence_service._fetch_newsnow_template", return_value=items
        ):
            out = asyncio.run(node._fetch_xueqiu_newsnow_fallback())
        assert out[0]["attention"] == 0


# =============================================================================
# _fetch_xueqiu 接线：仅主源全崩时兜底
# =============================================================================

class TestFetchXueqiuWiring:
    def test_fallback_when_akshare_all_empty(self):
        node = SentimentAnalysisNode()
        empty = pd.DataFrame()
        sample = [{
            "stock_code": "600519", "stock_name": "白酒龙头",
            "attention": 88, "price": 0.0, "dimension": "tweet",
        }]

        async def _fake_fallback(self):
            return sample

        with patch("akshare.stock_hot_follow_xq", return_value=empty), \
             patch("akshare.stock_hot_deal_xq", return_value=empty), \
             patch("akshare.stock_hot_tweet_xq", return_value=empty), \
             patch.object(
                 SentimentAnalysisNode, "_fetch_xueqiu_newsnow_fallback", _fake_fallback
             ):
            out = asyncio.run(node._fetch_xueqiu())

        assert out["total_count"] == 1
        assert out["tweet"] == sample
        assert out["fallback_source"] == "newsnow-xueqiu"

    def test_no_fallback_when_akshare_has_data(self):
        node = SentimentAnalysisNode()
        df = pd.DataFrame([{
            "股票代码": "SH600519", "股票简称": "白酒龙头",
            "关注": 123, "最新价": 10.0,
        }])
        called = {"n": 0}

        async def _fake_fallback(self):
            called["n"] += 1
            return []

        with patch("akshare.stock_hot_follow_xq", return_value=df), \
             patch("akshare.stock_hot_deal_xq", return_value=pd.DataFrame()), \
             patch("akshare.stock_hot_tweet_xq", return_value=pd.DataFrame()), \
             patch.object(
                 SentimentAnalysisNode, "_fetch_xueqiu_newsnow_fallback", _fake_fallback
             ):
            out = asyncio.run(node._fetch_xueqiu())

        assert out["total_count"] == 1
        assert "fallback_source" not in out
        assert called["n"] == 0  # 主源有数据 → 绝不触碰备份
