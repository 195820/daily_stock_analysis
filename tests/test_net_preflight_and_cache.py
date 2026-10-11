# -*- coding: utf-8 -*-
"""批次 E 单测：E1 网络预检（观测）+ E3 所属板块当日缓存（提速、结果零差异）。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from data_provider.base import DataFetcherManager
from src.got import net_preflight as np


# ── E1 net_preflight ──
class _Resp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_preflight_all_ok():
    with patch("src.got.net_preflight.urllib.request.urlopen", return_value=_Resp(200)):
        res = np.run_net_preflight()
    assert res["all_ok"] is True
    assert res["ok_count"] == res["total_count"] == len(np.PROBE_TARGETS)
    assert res["domains"]


def test_preflight_handles_errors():
    with patch("src.got.net_preflight.urllib.request.urlopen", side_effect=OSError("down")):
        res = np.run_net_preflight()
    assert res["all_ok"] is False
    assert res["ok_count"] == 0
    assert all(d["ok"] is False for d in res["domains"])


def test_preflight_5xx_not_ok():
    with patch("src.got.net_preflight.urllib.request.urlopen", return_value=_Resp(503)):
        res = np.run_net_preflight()
    assert res["all_ok"] is False


# ── E3 get_belong_boards 当日缓存 ──
class _FakeFetcher:
    name = "Fake"

    def __init__(self, raw):
        self._raw = raw
        self.calls = 0

    def get_belong_board(self, code):
        self.calls += 1
        return self._raw


def _mgr(fake):
    m = object.__new__(DataFetcherManager)
    m._fetchers = [fake]
    m._ensure_concurrency_guards()
    return m


def test_belong_boards_cached_same_day():
    fake = _FakeFetcher([{"板块名称": "半导体"}])
    m = _mgr(fake)
    norm = staticmethod(lambda raw: [{"板块名称": "半导体"}])
    with patch.object(DataFetcherManager, "_normalize_belong_boards", norm):
        r1 = m.get_belong_boards("600000.SH")
        r2 = m.get_belong_boards("600000.SH")
    assert r1 == r2 and fake.calls == 1  # 第二次走当日缓存，不重取


def test_belong_boards_cache_stale_next_day():
    fake = _FakeFetcher([{"板块名称": "半导体"}])
    m = _mgr(fake)
    norm = staticmethod(lambda raw: [{"板块名称": "半导体"}])
    with patch.object(DataFetcherManager, "_normalize_belong_boards", norm):
        m.get_belong_boards("600000.SH")
        # 缓存键为归一后代码；直接把唯一键的日期改成昨天 → 触发重取
        with m._belong_boards_cache_lock:
            keys = list(m._belong_boards_cache.keys())
            assert len(keys) == 1
            _k, _v = m._belong_boards_cache[keys[0]]
            m._belong_boards_cache[keys[0]] = ("2000-01-01", _v)
        m.get_belong_boards("600000.SH")
    assert fake.calls == 2  # 跨日失效重取


def test_belong_boards_empty_not_cached():
    fake = _FakeFetcher([{"板块名称": "半导体"}])
    m = _mgr(fake)
    norm = staticmethod(lambda raw: [])  # 空结果不缓存（保留重试语义）
    with patch.object(DataFetcherManager, "_normalize_belong_boards", norm):
        m.get_belong_boards("600000.SH")
        m.get_belong_boards("600000.SH")
    assert fake.calls == 2
    assert "600000.SH" not in m._belong_boards_cache
