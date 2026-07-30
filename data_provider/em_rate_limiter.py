# -*- coding: utf-8 -*-
"""
=============================================
EmRateLimiter - 东财 HTTP 请求统一限流器
=============================================

设计来源：a-stock-data 的 em_get() 模式
核心理念：所有东财 HTTP 请求必须经过统一限流入口，
         避免分散限流导致触发反爬（RemoteDisconnected / 429）。

限流策略：
1. 串行化：threading.Lock 保证同一时刻只有一个请求在执行
2. 最小间隔：EM_MIN_INTERVAL=1.0s + 随机 0.1-0.5s 抖动
3. 连接池：HTTPAdapter(maxsize=5) 复用 TCP 连接
4. 自动重试：urllib3.Retry(total=3, 指数退避, 429/5xx 强制重试）
5. UA 随机化：5 个浏览器 UA 轮换

使用方式：
    from data_provider.em_rate_limiter import em_get

    resp = em_get("https://push2.eastmoney.com/api/qt/clist/get",
                  params={"pn": 1, "pz": 5, ...})
    data = resp.json()
"""

import logging
import random
import threading
import time
from typing import Optional, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# === 限流参数 ===
EM_MIN_INTERVAL = 1.0        # 最小请求间隔（秒）
EM_JITTER_MIN = 0.1          # 抖动下限
EM_JITTER_MAX = 0.5          # 抖动上限
EM_TIMEOUT = 15              # 请求超时（秒）

# === UA 池 ===
_EM_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

# === 全局状态 ===
_em_lock = threading.Lock()
_em_last_request_time = 0.0
_em_session: Optional[requests.Session] = None
_em_session_lock = threading.Lock()


def _get_session() -> requests.Session:
    """
    获取全局共享的 requests Session（线程安全，懒初始化）

    配置：
    - HTTPAdapter 连接池（maxsize=5）
    - urllib3.Retry（3 次指数退避，429/5xx 强制重试）
    - 默认 Referer（东财页面来源）
    """
    global _em_session
    if _em_session is not None:
        return _em_session

    with _em_session_lock:
        if _em_session is not None:
            return _em_session

        session = requests.Session()

        # 重试策略：3 次指数退避，仅对 429/5xx 强制重试
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,           # 1s, 2s, 4s
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=5,
            pool_maxsize=5,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # 默认 headers
        session.headers.update({
            "Referer": "https://quote.eastmoney.com/",
            "Accept": "application/json, text/plain, */*",
        })

        # 注意：不走 trust_env=False，保留系统代理（东财直连会被反爬封锁）

        _em_session = session
        return _em_session


def _throttle() -> None:
    """
    串行限流：确保请求间隔 ≥ EM_MIN_INTERVAL + 随机抖动

    线程安全：使用 Lock 保证同一时刻只有一个请求在执行
    """
    global _em_last_request_time

    with _em_lock:
        now = time.time()
        elapsed = now - _em_last_request_time
        jitter = random.uniform(EM_JITTER_MIN, EM_JITTER_MAX)
        min_wait = EM_MIN_INTERVAL + jitter

        if elapsed < min_wait:
            sleep_time = min_wait - elapsed
            logger.debug(f"东财限流：等待 {sleep_time:.2f}s (间隔={elapsed:.2f}s, 目标={min_wait:.2f}s)")
            time.sleep(sleep_time)

        _em_last_request_time = time.time()


def em_get(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = EM_TIMEOUT,
    use_ua: bool = True,
    **kwargs,
) -> requests.Response:
    """
    东财 HTTP GET 统一入口（带限流 + 重试 + UA 随机化）

    所有对 eastmoney.com 域名的请求应通过此函数，
    而非直接使用 requests.get()。

    Args:
        url: 请求 URL
        params: URL 参数
        timeout: 超时时间（秒）
        use_ua: 是否随机 User-Agent（默认 True）
        **kwargs: 透传给 requests.Session.get()

    Returns:
        requests.Response

    Raises:
        requests.RequestException: 重试耗尽后仍失败
    """
    _throttle()

    session = _get_session()

    headers = kwargs.pop("headers", {})
    if use_ua and "User-Agent" not in headers:
        headers["User-Agent"] = random.choice(_EM_USER_AGENTS)

    logger.debug(f"em_get: {url} params={params}")
    resp = session.get(url, params=params, timeout=timeout, headers=headers, **kwargs)
    resp.raise_for_status()

    return resp


def em_post(
    url: str,
    data: Optional[Dict[str, Any]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    timeout: int = EM_TIMEOUT,
    use_ua: bool = True,
    **kwargs,
) -> requests.Response:
    """
    东财 HTTP POST 统一入口（带限流）

    用法同 em_get，但支持 POST body。
    """
    _throttle()

    session = _get_session()

    headers = kwargs.pop("headers", {})
    if use_ua and "User-Agent" not in headers:
        headers["User-Agent"] = random.choice(_EM_USER_AGENTS)

    logger.debug(f"em_post: {url}")
    resp = session.post(url, data=data, json=json_data, timeout=timeout, headers=headers, **kwargs)
    resp.raise_for_status()

    return resp


def reset_session() -> None:
    """重置 Session（用于测试或连接异常时强制重建）"""
    global _em_session
    with _em_session_lock:
        if _em_session is not None:
            _em_session.close()
            _em_session = None
        logger.info("东财 Session 已重置")
