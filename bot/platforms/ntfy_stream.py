# -*- coding: utf-8 -*-
"""
===================================
ntfy 双向通道（订阅端）
===================================

ntfy 只有推送 sender，没有接收端。本模块补上订阅侧：

    NTFY_URL       → 出站 topic（手机订阅它收报告）
    NTFY_CMD_URL   → 入站 topic（手机往它发指令，本进程订阅）

协议：`GET <server>/<topic>/json` 是 NDJSON 长连接，每行一个事件
（`open` / `message` / `keepalive`，keepalive 默认 45s）。收到 `message`
转成 `BotMessage` 交给既有 `CommandDispatcher`，回复再发回出站 topic。

回声防护：本进程发出的消息带 `bot` tag，订阅端见到该 tag 直接跳过，
因此 cmd 与 out 同 topic 也不会自激。断线用 `since=<最后一条 id>` 续传，
不丢指令、不重放旧指令。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse, urlunparse

import requests

from bot.models import BotMessage, BotResponse, ChatType

logger = logging.getLogger(__name__)

NTFY_STREAM_AVAILABLE = True

# 自己发出的消息一律带该 tag，订阅端据此跳过（防回声）
ECHO_TAG = "bot"
# 服务端 message-size-limit 默认 4096：回复按 3800 字节分段
MAX_MESSAGE_BYTES = 3800
# (connect, read)；read 必须大于 keepalive 间隔（默认 45s）
STREAM_TIMEOUT: Tuple[float, float] = (10, 90)
# 断线重连退避
RECONNECT_MIN_SECONDS = 3
RECONNECT_MAX_SECONDS = 60


def resolve_ntfy_endpoint(ntfy_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split an ntfy topic URL into server root and topic (final path segment)."""
    raw_url = (ntfy_url or "").strip().rstrip("/")
    if not raw_url:
        return None, None

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None, None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None, None

    topic = unquote(segments[-1]).strip()
    if not topic:
        return None, None

    root_path = "/".join(segments[:-1])
    server_url = urlunparse(
        parsed._replace(
            path=f"/{root_path}" if root_path else "",
            params="",
            query="",
            fragment="",
        )
    ).rstrip("/")
    return server_url, topic


def split_message(content: str, limit: int = MAX_MESSAGE_BYTES) -> list:
    """Split text into UTF-8 chunks under ``limit`` bytes, preferring line breaks."""
    chunks = []
    current = []
    current_bytes = 0

    for line in content.splitlines(keepends=True):
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > limit:
            if current:
                chunks.append("".join(current))
                current, current_bytes = [], 0
            buf = []
            buf_bytes = 0
            for char in line:
                char_bytes = len(char.encode("utf-8"))
                if buf_bytes + char_bytes > limit:
                    chunks.append("".join(buf))
                    buf, buf_bytes = [], 0
                buf.append(char)
                buf_bytes += char_bytes
            if buf:
                current, current_bytes = buf, buf_bytes
            continue
        if current_bytes + line_bytes > limit:
            chunks.append("".join(current))
            current, current_bytes = [], 0
        current.append(line)
        current_bytes += line_bytes

    if current:
        chunks.append("".join(current))
    return chunks or [content]


class NtfyStreamClient:
    """Subscribe to an ntfy topic and feed inbound text into the bot dispatcher."""

    def __init__(
        self,
        out_url: str,
        cmd_url: str,
        *,
        token: Optional[str] = None,
        verify_ssl: bool = True,
        dispatcher_factory: Optional[Callable[[], Any]] = None,
        session: Optional[requests.Session] = None,
    ):
        self._out_server, self._out_topic = resolve_ntfy_endpoint(out_url)
        self._cmd_server, self._cmd_topic = resolve_ntfy_endpoint(cmd_url)
        if not all([self._out_server, self._out_topic, self._cmd_server, self._cmd_topic]):
            raise ValueError("ntfy 出站/入站 URL 必须都是 <server>/<topic> 完整 endpoint")

        self._token = (token or "").strip()
        self._verify_ssl = verify_ssl
        self._dispatcher_factory = dispatcher_factory
        self._session = session or requests.Session()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_id: Optional[str] = None
        self._active_response: Optional[Any] = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Blocking consume loop (reconnects with backoff until stopped)."""
        backoff = RECONNECT_MIN_SECONDS
        while not self._stop_event.is_set():
            try:
                self._consume_once()
                backoff = RECONNECT_MIN_SECONDS
            except Exception as exc:  # 网络抖动/服务端重启都走这里
                if self._stop_event.is_set():
                    break
                logger.warning("[Ntfy] 订阅中断: %s；%ss 后重连", type(exc).__name__, backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SECONDS)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.start, name="NtfyStream", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        self._stop_event.set()
        # 关掉活动响应，让阻塞中的 iter_lines 立刻抛错退出（否则要等到读超时）
        response = self._active_response
        if response is not None:
            self._active_response = None
            try:
                response.close()
            except Exception:  # pragma: no cover - 关流失败不影响退出
                pass
        if wait and self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    # ------------------------------------------------------------------ internals
    def _headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "daily_stock_analysis"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _subscribe_url(self) -> str:
        base = f"{self._cmd_server}/{self._cmd_topic}/json"
        # 首连只看最近 15 分钟，避免把历史指令当新指令重放
        return f"{base}?since={self._last_id}" if self._last_id else f"{base}?since=15m"

    def _consume_once(self) -> None:
        logger.info("[Ntfy] 订阅 %s/%s", self._cmd_server, self._cmd_topic)
        with self._session.get(
            self._subscribe_url(),
            headers=self._headers(),
            stream=True,
            timeout=STREAM_TIMEOUT,
            verify=self._verify_ssl,
        ) as response:
            self._active_response = response
            try:
                response.raise_for_status()
                for raw in response.iter_lines(decode_unicode=False):
                    if self._stop_event.is_set():
                        return
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    self._handle_event(event)
            finally:
                self._active_response = None

    def _handle_event(self, event: Dict[str, Any]) -> None:
        if event.get("event") != "message":
            return

        tags = event.get("tags") or []
        if ECHO_TAG in tags:
            return

        text = (event.get("message") or "").strip()
        if not text:
            return

        message_id = str(event.get("id") or "")
        if message_id:
            self._last_id = message_id

        bot_message = BotMessage(
            platform="ntfy",
            message_id=message_id,
            user_id=f"ntfy:{event.get('topic') or self._cmd_topic}",
            user_name=(event.get("title") or "ntfy"),
            chat_id=str(event.get("topic") or self._cmd_topic),
            chat_type=ChatType.PRIVATE,
            content=text,
            raw_content=text,
            mentioned=True,  # ntfy 是 1:1 通道，无需 @
            timestamp=datetime.now(),
            raw_data=event,
        )

        response = self._dispatch(bot_message)
        reply = (response.text or "").strip() if response else ""
        if reply:
            self.publish(reply, markdown=bool(getattr(response, "markdown", False)))

    def _dispatch(self, message: BotMessage) -> Optional[BotResponse]:
        try:
            factory = self._dispatcher_factory
            if factory is None:
                from bot.dispatcher import get_dispatcher

                factory = get_dispatcher
            return factory().dispatch(message)
        except Exception as exc:
            logger.error("[Ntfy] 指令分发失败: %s", exc)
            return BotResponse.error_response(f"命令执行失败: {str(exc)[:100]}")

    def publish(self, content: str, *, markdown: bool = False, title: Optional[str] = None) -> bool:
        """Publish a reply to the outbound topic, chunked to fit ntfy's 4 KB limit."""
        chunks = split_message(content)
        ok = True
        for index, chunk in enumerate(chunks, start=1):
            payload = {
                "topic": self._out_topic,
                "message": chunk,
                "tags": [ECHO_TAG],
                "markdown": markdown,
            }
            if title:
                payload["title"] = title if len(chunks) == 1 else f"{title} ({index}/{len(chunks)})"
            try:
                response = self._session.post(
                    self._out_server,
                    json=payload,
                    headers={**self._headers(), "Content-Type": "application/json; charset=utf-8"},
                    timeout=15,
                    verify=self._verify_ssl,
                )
            except Exception as exc:
                logger.warning("[Ntfy] 回复发送失败: %s", type(exc).__name__)
                ok = False
                continue
            if not 200 <= response.status_code < 300:
                logger.warning("[Ntfy] 回复发送失败: HTTP %s", response.status_code)
                ok = False
        return ok


_client: Optional[NtfyStreamClient] = None
_client_lock = threading.Lock()


def get_ntfy_stream_client() -> Optional[NtfyStreamClient]:
    """Return the process-wide client, creating it from config on first use."""
    global _client
    with _client_lock:
        if _client is None:
            from src.config import get_config

            config = get_config()
            out_url = getattr(config, "ntfy_url", None)
            cmd_url = getattr(config, "ntfy_cmd_url", None)
            if not out_url or not cmd_url:
                logger.warning("[Ntfy] NTFY_URL / NTFY_CMD_URL 未同时配置，无法启动订阅")
                return None
            try:
                _client = NtfyStreamClient(
                    out_url,
                    cmd_url,
                    token=getattr(config, "ntfy_token", None),
                    verify_ssl=getattr(config, "webhook_verify_ssl", True),
                )
            except ValueError as exc:
                logger.error("[Ntfy] 配置无效: %s", exc)
                return None
        return _client


def start_ntfy_stream_background() -> bool:
    """Start the ntfy command subscriber in a daemon thread."""
    client = get_ntfy_stream_client()
    if client is None:
        return False
    client.start_background()
    return client.is_running()
