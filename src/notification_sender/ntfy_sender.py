# -*- coding: utf-8 -*-
"""ntfy notification sender."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import quote, unquote, urlparse, urlunparse

import requests

from src.config import Config


logger = logging.getLogger(__name__)

# ntfy 服务端默认 message-size-limit=4096（自建改大也会让 FCM/APNS 失效），留余量
MAX_MESSAGE_BYTES = 3800
# 超长正文以附件形式上传时保留的摘要长度（字符，非字节）
SUMMARY_CHARS = 1200
# 机器产出标记：双向监听端据此跳过自己发的日报（同 topic 对话模式必需）
MACHINE_TAG = "robot"
# 附件消息的正文走 query 参数（URL 编码），压到 2 KB 内避免长 URL 被中间代理拒绝
QUERY_MESSAGE_MAX_BYTES = 2000


def _clip_utf8(text: str, limit: int) -> str:
    """按 UTF-8 字节裁剪，不切断多字节字符。"""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore")


def attachment_filename(title: str, fallback: str = "got-daily-report") -> str:
    """标题 → HTTP 头可用的 ASCII 文件名（emoji/中文在 header 里只能 latin-1）。"""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", title or "").strip("-._")
    return f"{stem[:60] or fallback}.md"


def resolve_ntfy_endpoint(ntfy_url: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split NTFY_URL into server root and topic from the final path segment."""
    raw_url = (ntfy_url or "").strip().rstrip("/")
    if not raw_url:
        return None, None

    parsed = urlparse(raw_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None, None

    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if not path_segments:
        return None, None

    topic = unquote(path_segments[-1]).strip()
    if not topic:
        return None, None

    root_path = "/".join(path_segments[:-1])
    server_url = urlunparse(
        parsed._replace(
            path=f"/{root_path}" if root_path else "",
            params="",
            query="",
            fragment="",
        )
    ).rstrip("/")

    return server_url, topic


def split_message(content: str, limit: int = MAX_MESSAGE_BYTES) -> List[str]:
    """Split ``content`` into pieces whose UTF-8 size stays under ``limit`` bytes.

    Prefers line boundaries; a single over-long line is hard-split on characters.
    """
    chunks: List[str] = []
    current: List[str] = []
    current_bytes = 0

    for line in content.splitlines(keepends=True):
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > limit:
            if current:
                chunks.append("".join(current))
                current, current_bytes = [], 0
            buf: List[str] = []
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


class NtfySender:
    """Send Markdown text notifications through the ntfy JSON publish API."""

    def __init__(self, config: Config):
        self._ntfy_url = getattr(config, "ntfy_url", None)
        self._ntfy_token = getattr(config, "ntfy_token", None)
        self._webhook_verify_ssl = getattr(config, "webhook_verify_ssl", True)

    def _is_ntfy_configured(self) -> bool:
        return bool(self._ntfy_url)

    def _resolve_ntfy_endpoint(self) -> Tuple[Optional[str], Optional[str]]:
        return resolve_ntfy_endpoint(self._ntfy_url)

    def _auth_headers(self) -> dict:
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "daily_stock_analysis",
        }
        token = (self._ntfy_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _post_message(
        self,
        server_url: str,
        payload: dict,
        timeout_seconds: Optional[float],
    ) -> Optional[requests.Response]:
        try:
            response = requests.post(
                server_url,
                json=payload,
                headers=self._auth_headers(),
                timeout=timeout_seconds or 10,
                verify=self._webhook_verify_ssl,
            )
        except requests.exceptions.Timeout:
            logger.error("发送 ntfy 消息失败: 请求超时")
            return None
        except requests.exceptions.RequestException as exc:
            logger.error("发送 ntfy 消息失败: 网络请求异常")
            logger.debug("ntfy 请求异常类型: %s", type(exc).__name__)
            return None
        except Exception as exc:
            logger.error("发送 ntfy 消息失败: 未知异常")
            logger.debug("ntfy 未知异常类型: %s", type(exc).__name__)
            return None

        if 200 <= response.status_code < 300:
            return response
        logger.error("ntfy 请求失败: HTTP %s", response.status_code)
        logger.debug("ntfy 响应内容: %s", response.text)
        return None

    def _upload_attachment(
        self,
        server_url: str,
        topic: str,
        content: str,
        title: str,
        message: str,
        timeout_seconds: Optional[float],
    ) -> Optional[str]:
        """Upload ``content`` as a file attachment and return its public URL.

        附件与正文在同一次 PUT 里发布（``title``/``message`` 走 query 参数，UTF-8 安全），
        得到「一条消息 = 结论 + 附件」，不再额外 POST 摘要消息。
        """
        # HTTP 头只能 latin-1：标题含 emoji/中文时 requests 直接抛 UnicodeEncodeError
        # （2026-10-27 实测长消息整条退化成十几段纯文本）。文件名压成 ASCII，
        # 标题/正文走 query 参数（URL 编码），附件侧不设 Title/Message 头。
        headers = {
            "Content-Type": "text/markdown; charset=utf-8",
            "User-Agent": "daily_stock_analysis",
            "Filename": attachment_filename(title),
        }
        token = (self._ntfy_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = requests.put(
                f"{server_url}/{quote(topic, safe='')}",
                params={"title": title, "message": message},
                data=content.encode("utf-8"),
                headers=headers,
                timeout=timeout_seconds or 30,
                verify=self._webhook_verify_ssl,
            )
        except Exception as exc:
            logger.warning("ntfy 附件上传失败: %s", type(exc).__name__)
            return None

        if not 200 <= response.status_code < 300:
            logger.warning("ntfy 附件上传失败: HTTP %s", response.status_code)
            return None

        try:
            attachment = (response.json() or {}).get("attachment") or {}
        except ValueError:
            return None
        return attachment.get("url")

    def send_to_ntfy(
        self,
        content: str,
        title: Optional[str] = None,
        *,
        summary: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """Publish a notification to ntfy using a JSON body with UTF-8 text.

        ntfy 单条消息默认上限 4 KB；超出时把全文当附件上传，**同一次 PUT 里**带上
        ``title``/``message``（query 参数），得到「一条消息 = 结论 + 附件」——
        旧实现先 PUT 附件、再 POST 摘要，用户侧会看到两条气泡。
        ``summary`` 未给时回落到正文前 ``SUMMARY_CHARS`` 字符；附件上传失败才退回分段。
        """
        if not self._is_ntfy_configured():
            logger.warning("ntfy URL 未配置，跳过推送")
            return False

        server_url, topic = self._resolve_ntfy_endpoint()
        if not server_url or not topic:
            logger.error("NTFY_URL 必须是包含 topic path 的完整 endpoint，例如 https://ntfy.sh/my-topic")
            return False

        if title is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
            title = f"📈 股票分析报告 - {date_str}"

        if len(content.encode("utf-8")) <= MAX_MESSAGE_BYTES:
            payload = {
                "topic": topic,
                "title": title,
                "message": content,
                "markdown": True,
                "tags": [MACHINE_TAG],
            }
            if self._post_message(server_url, payload, timeout_seconds) is not None:
                logger.info("ntfy 消息发送成功")
                return True
            return False

        message = (summary if summary else content[:SUMMARY_CHARS]).strip()
        message = _clip_utf8(message, QUERY_MESSAGE_MAX_BYTES)
        message = f"{message}\n\n……（全文 {len(content)} 字符，见附件）"

        attach_url = self._upload_attachment(
            server_url, topic, content, title, message, timeout_seconds
        )
        if attach_url:
            logger.info("ntfy 长消息已按附件发送成功（单条消息）")
            return True

        chunks = split_message(content)
        logger.info("ntfy 长消息改分段发送: %d 段", len(chunks))
        ok = True
        for index, chunk in enumerate(chunks, start=1):
            payload = {
                "topic": topic,
                "title": f"{title} ({index}/{len(chunks)})",
                "message": chunk,
                "markdown": True,
                "tags": [MACHINE_TAG],
            }
            if self._post_message(server_url, payload, timeout_seconds) is None:
                ok = False
        return ok
