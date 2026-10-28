# -*- coding: utf-8 -*-
"""P0-2 正文级精读单测。

覆盖：
1. `_sample_long_text`：≤limit 原样；超长含头/中/尾三段且总长受控
2. `fetch_article_text`：trafilatura 优先、缺失降级剥标签、抓取异常 → ""
3. `_format_news_for_prompt`：开关关 → 逐字节同现状（snippet 150 字、无「中略」）；
   开关开 → 正文段入 prompt 且超长采样
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.news_fulltext import fetch_article_text
from src.got.nodes.news_analysis import NewsAnalysisNode, _sample_long_text

MODULE = "src.got.nodes.news_analysis"


class TestSampleLongText(unittest.TestCase):
    def test_short_text_unchanged(self):
        self.assertEqual(_sample_long_text("短正文", 1200), "短正文")

    def test_long_text_keeps_head_middle_tail(self):
        text = "头" * 400 + "中" * 400 + "尾" * 400
        out = _sample_long_text(text, 300)
        self.assertIn("头", out)
        self.assertIn("中", out)
        self.assertTrue(out.endswith("尾"))
        self.assertEqual(out.count("…（中略）"), 2)
        self.assertLessEqual(len(out), 300 + 30, "采样总长不得超过 limit + 省略号开销")
        self.assertLess(len(out), len(text))

    def test_zero_limit_is_passthrough(self):
        self.assertEqual(_sample_long_text("abc", 0), "abc")


class TestFetchArticleText(unittest.TestCase):
    def _resp(self, html):
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status.return_value = None
        return resp

    def test_latin1_default_redecoded(self):
        """无 charset 的 text/* 响应 requests 缺省 ISO-8859-1 → 按 apparent_encoding 重解（防乱码）。"""

        class _Resp:
            def __init__(self):
                self._raw = "<p>统计局发布数据</p>".encode("utf-8")
                self.encoding = "ISO-8859-1"
                self.apparent_encoding = "utf-8"

            @property
            def text(self):
                return self._raw.decode(self.encoding, errors="replace")

            def raise_for_status(self):
                return None

        with patch("src.got.news_fulltext.requests.get", return_value=_Resp()), \
             patch("src.got.news_fulltext._extract_with_trafilatura", return_value=""):
            out = fetch_article_text("https://example.com/a")
        self.assertIn("统计局发布数据", out)

    def test_prefers_trafilatura(self):
        html = "<html><body><nav>导航</nav><p>正文内容</p></body></html>"
        with patch("src.got.news_fulltext.requests.get", return_value=self._resp(html)), \
             patch("src.got.news_fulltext._extract_with_trafilatura", return_value="干净正文"):
            self.assertEqual(fetch_article_text("https://example.com/a"), "干净正文")

    def test_falls_back_to_tag_strip(self):
        html = "<html><script>var x=1;</script><body><p>正文内容</p></body></html>"
        with patch("src.got.news_fulltext.requests.get", return_value=self._resp(html)), \
             patch("src.got.news_fulltext._extract_with_trafilatura", return_value=""):
            out = fetch_article_text("https://example.com/a")
        self.assertIn("正文内容", out)
        self.assertNotIn("var x=1", out)

    def test_network_error_returns_empty(self):
        with patch("src.got.news_fulltext.requests.get", side_effect=RuntimeError("boom")):
            self.assertEqual(fetch_article_text("https://example.com/a"), "")

    def test_empty_url_returns_empty(self):
        self.assertEqual(fetch_article_text(""), "")


class TestEnrichFulltext(unittest.IsolatedAsyncioTestCase):
    async def test_fills_short_snippet_with_article_text(self):
        target = {"title": "短摘要", "snippet": "短", "url": "https://example.com/a"}
        skipped_url = {"title": "无链接", "snippet": "短", "url": ""}
        long_snippet = {"title": "已有摘要", "snippet": "长" * 300, "url": "https://example.com/b"}

        with patch(
            "src.got.news_fulltext.fetch_article_text", return_value="抓取到的完整正文",
        ), patch(f"{MODULE}.GOT_NEWS_FULLTEXT_ENABLED", True):
            out = await NewsAnalysisNode._enrich_fulltext([target, skipped_url, long_snippet])

        self.assertEqual(target["snippet"], "抓取到的完整正文")
        self.assertEqual(target["snippet_raw"], "短", "原摘要留存供影子对照")
        self.assertEqual(skipped_url["snippet"], "短")
        self.assertEqual(long_snippet["snippet"], "长" * 300, "snippet 已够长不抓取")
        self.assertEqual(len(out), 3)

    async def test_fetch_failure_keeps_snippet(self):
        item = {"title": "短摘要", "snippet": "短", "url": "https://example.com/a"}
        with patch("src.got.news_fulltext.fetch_article_text", return_value=""):
            await NewsAnalysisNode._enrich_fulltext([item])
        self.assertEqual(item["snippet"], "短")
        self.assertNotIn("snippet_raw", item)


class TestFormatNewsFulltext(unittest.TestCase):
    def _items(self):
        return [{
            "title": "产业政策落地",
            "snippet": "头" * 400 + "中" * 400 + "尾" * 400,
            "source": "sina",
            "source_type": "sina",
            "url": "https://example.com/a",
            "published_date": "2026-10-28 09:00:00",
        }]

    def test_disabled_is_byte_identical_to_legacy(self):
        items = self._items()
        with patch(f"{MODULE}.GOT_NEWS_FULLTEXT_ENABLED", False):
            off, _ = NewsAnalysisNode._format_news_for_prompt(items, gated=True)
            explicit, _ = NewsAnalysisNode._format_news_for_prompt(items, gated=True, fulltext=False)
        self.assertEqual(off, explicit)
        self.assertNotIn("（中略）", off)
        self.assertIn(items[0]["snippet"][:150], off)
        self.assertNotIn(items[0]["snippet"][:151], off)

    def test_enabled_samples_long_body(self):
        items = self._items()
        with patch(f"{MODULE}.GOT_NEWS_FULLTEXT_ENABLED", True), \
             patch(f"{MODULE}.GOT_NEWS_FULLTEXT_MAX_CHARS", 300):
            on, _ = NewsAnalysisNode._format_news_for_prompt(items, gated=True, fulltext=True)
        self.assertIn("（中略）", on)
        self.assertIn("尾" * 10, on)


if __name__ == "__main__":
    unittest.main()
