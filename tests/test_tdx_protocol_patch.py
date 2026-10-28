# -*- coding: utf-8 -*-
"""TDX 2026-09 新版行情协议补丁的离线回归测试（不联网）。"""

import struct

from tdxpy.hq import TdxHq_API
from tdxpy.parser.base import BaseParser
from tdxpy.parser.std.get_index_bars import GetIndexBarsCmd
from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd

from data_provider.tdx_protocol import _BARS_VERSION_FLAG, apply_tdx_protocol_patch


class _FakeClient:
    """最小 socket 替身：记录上行包，下行返回空帧。"""

    def __init__(self):
        self.sent = []
        self.send_pkg_num = 0
        self.send_pkg_bytes = 0
        self.last_api_send_bytes = 0
        self.first_pkg_send_time = None
        self.recv_pkg_num = 0
        self.recv_pkg_bytes = 0

    def send(self, pkg):
        self.sent.append(bytes(pkg))
        return len(pkg)

    def recv(self, n):
        if n == 16:
            return struct.pack("<IIIHH", 0, 0, 0, 0, 0)
        return b""


def test_bars_requests_carry_new_version_flag():
    apply_tdx_protocol_patch()
    for cmd_cls in (GetSecurityBarsCmd, GetIndexBarsCmd):
        cmd = cmd_cls(_FakeClient())
        cmd.setParams(9, 1, "880597", 0, 5)
        version = struct.unpack("<I", bytes(cmd.send_pkg)[2:6])[0]
        assert version == _BARS_VERSION_FLAG == 0x01007B18
        # 命令码与请求长度保持不变，只改版本标识
        assert struct.unpack("<H", bytes(cmd.send_pkg)[:2])[0] == 0x010C
        assert len(cmd.send_pkg) == 38


def test_setup_sends_new_handshake_payloads(monkeypatch):
    apply_tdx_protocol_patch()

    sent = []

    def _record(self):
        self.setup()
        sent.append(bytes(self.send_pkg))

    monkeypatch.setattr(BaseParser, "_call_api", _record)

    api = TdxHq_API(heartbeat=False)
    api.client = _FakeClient()

    api.setup()

    assert [p.hex() for p in sent] == [
        "0c0218940001030003000d0001",
        "0c031899000120002000db0f7464786c6576656c"
        "000000295cf740110000000000000000000000000005",
    ]
    assert len(sent[1]) == 42
