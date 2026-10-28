# -*- coding: utf-8 -*-
"""P1-1：URL 归一化去重单测。

覆盖：
1. `canonicalize_url` 边界：追踪参数删除、www 归一、尾斜杠、非法 scheme、空输入、中文路径保留
2. `news_feeds._deduplicate`：同 canonical URL 不同标题 → 只留 1 条；
   不同 URL 相似标题仍走标题判据（行为不变）
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.got.news_feeds import _deduplicate
from src.got.news_url import canonicalize_url


class TestCanonicalizeUrl(unittest.TestCase):
    def test_tracking_params_dropped(self):
        self.assertEqual(
            canonicalize_url("https://a.com/x?utm_source=weibo&spm=abc"),
            "https://a.com/x",
        )

    def test_www_normalized(self):
        self.assertEqual(
            canonicalize_url("https://a.com/x?utm_source=weibo"),
            canonicalize_url("https://www.a.com/x"),
        )

    def test_empty_and_invalid(self):
        self.assertEqual(canonicalize_url(""), "")
        self.assertEqual(canonicalize_url(None), "")  # type: ignore[arg-type]
        self.assertEqual(canonicalize_url("javascript:alert(1)"), "")
        self.assertEqual(canonicalize_url("not a url"), "")
        self.assertEqual(canonicalize_url("ftp://a.com/x"), "")

    def test_trailing_slash_and_fragment(self):
        self.assertEqual(
            canonicalize_url("https://a.com/x/"),
            canonicalize_url("https://a.com/x#frag"),
        )

    def test_meaningful_params_kept(self):
        out = canonicalize_url("https://a.com/x?id=42&utm_medium=pc&weibo_uid=9")
        self.assertEqual(out, "https://a.com/x?id=42")

    def test_cjk_path_preserved(self):
        out = canonicalize_url("https://a.com/新闻/详情?utm_campaign=1")
        self.assertEqual(out, "https://a.com/新闻/详情")

    def test_scheme_and_host_lowercased(self):
        self.assertEqual(
            canonicalize_url("HTTPS://A.COM/X"), "https://a.com/X",
        )


class TestDeduplicateUrlFingerprint(unittest.TestCase):
    def test_same_canonical_url_different_titles_deduped(self):
        items = [
            {"title": "央行宣布降准0.5个百分点", "url": "https://a.com/x?utm_source=weibo"},
            {"title": "完全不同的另一条标题内容", "url": "https://www.a.com/x"},
        ]
        self.assertEqual(len(_deduplicate(items)), 1)

    def test_different_urls_keep_title_rule(self):
        items = [
            {"title": "央行宣布降准0.5个百分点", "url": "https://a.com/x"},
            {"title": "与上一条毫无关系的标题", "url": "https://b.com/y"},
        ]
        self.assertEqual(len(_deduplicate(items)), 2)

    def test_similar_titles_still_deduped_without_urls(self):
        items = [
            {"title": "央行宣布降准0.5个百分点", "url": ""},
            {"title": "央行宣布降准0.5个百分点!", "url": ""},
        ]
        self.assertEqual(len(_deduplicate(items)), 1)

    def test_url_match_merges_platform_metadata(self):
        items = [
            {"title": "微博热议某产业政策", "url": "https://a.com/x", "platform": "weibo", "rank": 3},
            {"title": "产业政策登上百度热搜", "url": "https://a.com/x?utm_source=baidu",
             "platform": "baidu", "rank": 7},
        ]
        out = _deduplicate(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["platforms"], ["baidu", "weibo"])
        self.assertEqual(out[0]["rank"], 3)

    def test_title_match_merges_platform_metadata(self):
        items = [
            {"title": "某产业政策落地", "url": "https://a.com/x", "platform": "weibo", "rank": 5, "heat": 10},
            {"title": "某产业政策落地", "url": "https://b.com/y", "platform": "zhihu", "rank": 2, "heat": 99},
        ]
        out = _deduplicate(items)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["platforms"], ["weibo", "zhihu"])
        self.assertEqual(out[0]["rank"], 2)
        self.assertEqual(out[0]["heat"], 99)


    def test_title_merge_registers_url_owner(self):
        """标题判重吞掉带 URL 的条目后，同 URL 换标题再来一条仍应判重（P1-1 全路径）。"""
        items = [
            {"title": "央行宣布降准0.5个百分点", "url": ""},
            {"title": "央行宣布降准0.5个百分点!", "url": "https://a.com/x"},
            {"title": "完全不同的另一条标题内容", "url": "https://www.a.com/x?utm_source=weibo"},
        ]
        self.assertEqual(len(_deduplicate(items)), 1)


if __name__ == "__main__":
    unittest.main()
