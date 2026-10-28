# -*- coding: utf-8 -*-
"""
通达信 2026-09 新版行情协议适配补丁（tdxpy / mootdx）

背景
----
2026-09 起，通达信公开行情服务器（TCP 7709）拒绝老客户端：
- 握手只认 0c02(init) + 0c03(tdxlevel 42B 认证)；tdxpy 0.2.7 的三段 setup
  里 0c03 是 44B 老载荷，认证不过 → 行情类命令（K线/报价/分时）静默回空，
  而 get_security_count / get_security_list / get_finance_info 仍正常。
- K线命令 0x010c 的版本标识由 0x01016408 改为 0x01007B18
  （其余字段与老 pytdx 完全一致，响应格式不变）。

实测（2026-10-23，117.34.114.15:7709）：
- 打补丁前：`get_security_bars` / `get_index_bars` 全回 None（全池 70+ 节点一致）
- 打补丁后：个股 600519、板块指数 880597 均返回当日 bar（含 up_count/down_count）

实现
----
只做两件事，全部在运行时 monkeypatch，不改 site-packages：
1. TdxHq_API.setup 换成 0c02 + 0c03(42B) 两段握手；
2. GetSecurityBarsCmd / GetIndexBarsCmd 发出的包把版本标识（字节 2:6）改写为
   0x01007B18 —— 响应解析沿用 tdxpy 原实现。

幂等；对未安装 tdxpy / mootdx 的环境静默跳过。
"""

import logging
import struct
from typing import Tuple

logger = logging.getLogger(__name__)

# 抓包得到的新版握手载荷（2026-09-14 逆向，与 tdxdata 0.1.1 一致）
_INIT_PAYLOAD = bytes.fromhex("0c0218940001030003000d0001")
_AUTH_PAYLOAD = bytes.fromhex(
    "0c031899000120002000db0f7464786c6576656c"
    "000000295cf740110000000000000000000000000005"
)
# 老库值（存档对照）：0x01016408
_BARS_VERSION_FLAG = 0x01007B18
# <H I H H H H 6s ...> 包结构里版本标识占据的字节区间
_BARS_VERSION_SLICE = slice(2, 6)

_applied = False


def _build_setup_patch(tdx_hq_api, base_parser) -> None:
    class _RawHandshake(base_parser):
        """把抓包载荷原样发出，不做响应解析。"""

        payload: bytes = b""

        def setup(self):  # noqa: D401 - tdxpy 约定
            self.send_pkg = bytearray(self.payload)

    init_cmd = type("_InitCmd", (_RawHandshake,), {"payload": _INIT_PAYLOAD})
    auth_cmd = type("_AuthCmd", (_RawHandshake,), {"payload": _AUTH_PAYLOAD})

    def _setup(self):
        init_cmd(self.client).call_api()
        auth_cmd(self.client).call_api()

    tdx_hq_api.setup = _setup


def _patch_bars_set_params(cls) -> None:
    original = cls.setParams

    def set_params(self, category, market, code, start, count):
        original(self, category, market, code, start, count)
        # tdxpy 用 struct.pack 产出 bytes，需先转 bytearray 再改写版本标识
        pkg = bytearray(self.send_pkg)
        pkg[_BARS_VERSION_SLICE] = struct.pack("<I", _BARS_VERSION_FLAG)
        self.send_pkg = pkg

    set_params.__wrapped__ = original  # 便于排查/回滚
    cls.setParams = set_params


def apply_tdx_protocol_patch() -> Tuple[bool, str]:
    """给 tdxpy 打新版协议补丁（幂等）。

    Returns:
        (applied_now, reason)：已应用过返回 (False, "already")，
        环境不满足返回 (False, 原因)，本次应用成功返回 (True, "ok")。
    """
    global _applied
    if _applied:
        return False, "already"

    try:
        from tdxpy.hq import TdxHq_API
        from tdxpy.parser.base import BaseParser
        from tdxpy.parser.std.get_index_bars import GetIndexBarsCmd
        from tdxpy.parser.std.get_security_bars import GetSecurityBarsCmd
    except Exception as exc:  # 未安装 tdxpy → mootdx 也用不了，交给上层降级
        logger.debug("tdx 协议补丁跳过（tdxpy 不可用）: %s", exc)
        return False, f"tdxpy unavailable: {exc}"

    try:
        _build_setup_patch(TdxHq_API, BaseParser)
        _patch_bars_set_params(GetSecurityBarsCmd)
        _patch_bars_set_params(GetIndexBarsCmd)
    except Exception as exc:
        logger.warning("tdx 协议补丁应用失败（保持老协议）: %s", exc)
        return False, f"patch failed: {exc}"

    _applied = True
    logger.info(
        "tdx 协议补丁已应用：0c02+0c03 握手，K线版本标识 0x%08X", _BARS_VERSION_FLAG
    )
    return True, "ok"
