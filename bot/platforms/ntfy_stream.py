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
import tempfile
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import unquote, urlparse, urlunparse

import requests

from bot.models import BotMessage, BotResponse, ChatType

logger = logging.getLogger(__name__)

NTFY_STREAM_AVAILABLE = True

# 机器产出的消息（本进程回帖 + NtfySender 日报）统一打该 tag，订阅端据此跳过。
# 同 topic 对话模式（NTFY_CMD_URL == NTFY_URL）下，这是唯一防自激/防"日报被当指令"的闸门。
ECHO_TAG = "robot"
# 兼容早期已发出的 "bot" 标记
LEGACY_ECHO_TAGS = ("bot",)
# 同 topic 对话模式的兜底：未打标记又明显是整段机器文本（超长/多行）不进 dispatch；
# 短自由文本仍交给 dispatcher 的 NL 路由，但不把它当作"在叫我"（不触发问候）
BULK_TEXT_CHARS = 200
BULK_TEXT_LINES = 10
# 手机发图更新持仓：只收图片附件，超过上限直接忽略
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
ATTACHMENT_TIMEOUT = 30
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
        # 首连不重放历史：从本进程启动时刻起收，避免把旧指令（可能带 LLM 调用）再执行一遍
        self._started_at = int(time.time())

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
        # 首连从进程启动时刻起（不重放历史指令）；此后用最后一条消息 id 精确续传
        marker = self._last_id or str(self._started_at)
        return f"{base}?since={marker}"

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
        if ECHO_TAG in tags or any(tag in tags for tag in LEGACY_ECHO_TAGS):
            return

        # 任何见过的消息都推进游标，断线重连不会重放（含被下面规则跳过的）
        message_id = str(event.get("id") or "")
        if message_id:
            self._last_id = message_id

        image_path = self._download_image(event.get("attachment") or {})

        text = (event.get("message") or "").strip()
        if not text and not image_path:
            return
        caption = text
        if image_path and not text.startswith("/"):
            # ntfy 对纯附件会自动填 "You received a file: xxx"，不能据此走自由文本分支。
            # 图片 + 非命令文本 ⇒ 一律按「发图更新持仓」处理，原文本留作 caption。
            text = "/portfolio"

        bot_message = BotMessage(
            platform="ntfy",
            message_id=message_id,
            user_id=f"ntfy:{event.get('topic') or self._cmd_topic}",
            user_name=(event.get("title") or "ntfy"),
            chat_id=str(event.get("topic") or self._cmd_topic),
            chat_type=ChatType.PRIVATE,
            content=text,
            raw_content=text,
            mentioned=True,
            timestamp=datetime.now(),
            raw_data=(
                {**event, "image_path": image_path, "caption": caption}
                if image_path else event
            ),
        )

        # 与 dispatcher 同一套解析：命中 "/cmd" 或中文命令词表才算"在叫机器人"。
        # 未命中时：整段机器文本（无标记的第三方推送）直接丢弃；
        # 短自由文本保留 NL 路由，但 mentioned=False，避免每条闲聊都回一次问候。
        command, _ = bot_message.get_command_and_args("/")
        if command is None:
            if len(text) > BULK_TEXT_CHARS or text.count("\n") > BULK_TEXT_LINES:
                logger.info(
                    "[Ntfy] 跳过疑似机器文本（%d 字符 / %d 行），未进入 dispatch",
                    len(text), text.count("\n"),
                )
                return
            bot_message.mentioned = False

        try:
            response = self._dispatch(bot_message)
            reply = (response.text or "").strip() if response else ""
            if reply:
                self.publish(reply, markdown=bool(getattr(response, "markdown", False)))
        finally:
            if image_path:
                Path(image_path).unlink(missing_ok=True)

    def _download_image(self, attachment: Dict[str, Any]) -> Optional[str]:
        """下载图片附件到临时文件；非图片/超限/失败一律返回 None。"""
        url = (attachment or {}).get("url")
        if not url:
            return None
        mime = str(attachment.get("type") or "")
        if mime and not mime.startswith("image/"):
            logger.info("[Ntfy] 忽略非图片附件: %s", mime)
            return None
        size = attachment.get("size") or 0
        if size and size > MAX_ATTACHMENT_BYTES:
            logger.warning("[Ntfy] 附件过大(%.1f MB)，已忽略", size / 1024 / 1024)
            return None

        suffix = Path(str(attachment.get("name") or "image")).suffix or ".png"
        try:
            response = self._session.get(
                url, timeout=ATTACHMENT_TIMEOUT, verify=self._verify_ssl
            )
            response.raise_for_status()
            data = response.content
        except Exception as exc:
            logger.warning("[Ntfy] 附件下载失败: %s", type(exc).__name__)
            return None

        if len(data) > MAX_ATTACHMENT_BYTES:
            logger.warning("[Ntfy] 附件下载后超限，已丢弃")
            return None

        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            handle.write(data)
            handle.flush()
        finally:
            handle.close()
        logger.info("[Ntfy] 收到图片附件 %s (%d KB)", suffix, len(data) // 1024)
        return handle.name

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
