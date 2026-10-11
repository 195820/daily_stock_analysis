# -*- coding: utf-8 -*-
"""D0（竞品补强批次 D 前置）：个股当日涨幅批量取数单测。

覆盖 `TencentFetcher.get_stock_quick_change`（单次多 symbol HTTP 的解析契约）：
- 正常多标的解析（change_pct / price / name，返回键沿用入参 code）
- 短 payload（字段不足）跳过、不崩
- HTTP 失败降级为空 dict（不抛，供 news_pulse 诚实缺源）
- 空入参短路
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from data_provider.tencent_fetcher import TencentFetcher


def _tencent_line(name: str, code: str, price: str, change_pct: str) -> str:
    """构造一条腾讯行情 payload（~ 分隔，长度覆盖到字段 32）。"""
    fields = ["1"] * 50
    fields[1] = name
    fields[2] = code
    fields[3] = price
    fields[32] = change_pct
    return "~".join(fields)


def _fake_resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = text.encode("gbk", errors="ignore")
    return resp


def test_batch_parse_multi_symbol():
    payload = (
        f'v_sh600000="{_tencent_line("浦发银行", "600000", "7.10", "-1.23")}";'
        f'v_sz000001="{_tencent_line("平安银行", "000001", "11.20", "8.50")}";'
    )
    with patch("data_provider.tencent_fetcher.urllib.request.urlopen",
               return_value=_fake_resp(payload)):
        result = TencentFetcher().get_stock_quick_change(["600000.SH", "000001.SZ"])

    assert set(result) == {"600000.SH", "000001.SZ"}
    assert result["600000.SH"]["change_pct"] == -1.23
    assert result["600000.SH"]["name"] == "浦发银行"
    assert result["600000.SH"]["price"] == 7.10
    assert result["000001.SZ"]["change_pct"] == 8.50


def test_short_payload_skipped_no_crash():
    payload = (
        f'v_sh600000="1~浦发银行~600000~7.10";'  # 字段不足 33，须跳过
        f'v_sz000001="{_tencent_line("平安银行", "000001", "11.20", "8.50")}";'
    )
    with patch("data_provider.tencent_fetcher.urllib.request.urlopen",
               return_value=_fake_resp(payload)):
        result = TencentFetcher().get_stock_quick_change(["600000.SH", "000001.SZ"])

    assert "600000.SH" not in result
    assert result["000001.SZ"]["change_pct"] == 8.50


def test_http_failure_degrades_to_empty():
    with patch("data_provider.tencent_fetcher.urllib.request.urlopen",
               side_effect=OSError("network down")):
        result = TencentFetcher().get_stock_quick_change(["600000.SH"])
    assert result == {}


def test_empty_codes_short_circuit():
    # 空入参不发 HTTP
    with patch("data_provider.tencent_fetcher.urllib.request.urlopen") as m:
        assert TencentFetcher().get_stock_quick_change([]) == {}
        m.assert_not_called()


def test_unmappable_code_excluded():
    with patch("data_provider.tencent_fetcher.urllib.request.urlopen") as m:
        # 非 6 位 A 股代码无法转 symbol → 短路返回 {}，不发请求
        assert TencentFetcher().get_stock_quick_change(["AAPL"]) == {}
        m.assert_not_called()
