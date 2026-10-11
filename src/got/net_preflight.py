# -*- coding: utf-8 -*-
"""批次 E1（竞品经验补强计划 2026-10-06）：轻量网络预检（一期仅观测）。

对 3-4 个关键数据域各发**一次短超时 GET**（计划：不用 HEAD——东财/同花顺类接口常拒 HEAD，
假阳性会把正常日打成 lite），总耗时受 `PROBE_TIMEOUT_S × max_workers` 约束（目标 <3s）。
结果落 `meta.json` 新增 `net_preflight` 字段（观测用），**不据此降级/跳采集**——lite 自动降级
按计划 §E1 属"改决策面"最高风险项，延后到 lite/full 历史差异读数产出后再评估。

一期纪律：`GOT_NET_PREFLIGHT=0` 可关；缺省开但纯观测（零决策影响）。
"""

from __future__ import annotations

import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# 关键数据域（GET 探活，返回码 <500 视为可达；403/404 也算"域名活着"）
PROBE_TARGETS: List[Dict[str, str]] = [
    {"name": "eastmoney_push2", "url": "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&fs=m:1+t:2"},
    {"name": "tencent_qt", "url": "https://qt.gtimg.cn/q=sh000001"},
    {"name": "sina_roll", "url": "https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&num=1"},
    {"name": "xueqiu_status", "url": "https://xueqiu.com/statuses/hot/listV2.json"},
]

PROBE_TIMEOUT_S = 2.5
_TOTAL_BUDGET_S = 4.0


def _probe(target: Dict[str, str]) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(
            target["url"], headers={"User-Agent": "Mozilla/5.0"}, method="GET"
        )
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            status = resp.status
        return {"name": target["name"], "ok": status < 500, "status": status,
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001 - 探测失败只记不抛
        return {"name": target["name"], "ok": False, "status": None,
                "error": type(exc).__name__,
                "latency_ms": int((time.monotonic() - t0) * 1000)}


def run_net_preflight() -> Dict[str, Any]:
    """并发探测关键数据域，返回观测摘要（总耗时超预算的域记 timeout，不阻塞）。"""
    t0 = time.monotonic()
    results: List[Dict[str, Any]] = []
    try:
        with ThreadPoolExecutor(max_workers=len(PROBE_TARGETS)) as ex:
            futures = {ex.submit(_probe, tgt): tgt for tgt in PROBE_TARGETS}
            for fut, tgt in futures.items():
                try:
                    results.append(fut.result(timeout=_TOTAL_BUDGET_S))
                except FutTimeout:
                    results.append({"name": tgt["name"], "ok": False, "status": None,
                                    "error": "probe_timeout"})
    except Exception as exc:  # noqa: BLE001
        logger.debug("[net_preflight] 预检整体异常（降级为观测空）: %s", exc)
        return {"error": type(exc).__name__, "domains": []}

    ok_count = sum(1 for r in results if r.get("ok"))
    return {
        "all_ok": ok_count == len(results) and bool(results),
        "ok_count": ok_count,
        "total_count": len(results),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "domains": results,
    }
