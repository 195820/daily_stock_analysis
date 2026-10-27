# -*- coding: utf-8 -*-
"""ntfy 双向通道与长消息推送的回归测试（全部离线，不触网）。"""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.models import BotResponse
from bot.platforms import ntfy_stream
from src.notification_sender.ntfy_sender import (
    MAX_MESSAGE_BYTES,
    NtfySender,
    resolve_ntfy_endpoint as sender_resolve,
    split_message,
)


def _sender(**overrides):
    config = SimpleNamespace(
        ntfy_url="https://ntfy.sh/dsa-out-topic",
        ntfy_token="tk_test",
        webhook_verify_ssl=True,
        **overrides,
    )
    return NtfySender(config)


class TestNtfySenderLongMessage(unittest.TestCase):
    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_short_message_posts_single_message_without_attachment(self, mock_post):
        mock_post.return_value = mock.Mock(status_code=200)

        self.assertTrue(_sender().send_to_ntfy("短消息", title="标题"))

        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["message"], "短消息")
        self.assertNotIn("attach", payload)

    @mock.patch("src.notification_sender.ntfy_sender.requests.put")
    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_long_message_uploads_attachment_then_posts_summary(self, mock_post, mock_put):
        mock_put.return_value = mock.Mock(
            status_code=200,
            json=lambda: {"attachment": {"url": "https://ntfy.sh/file/report.md"}},
        )
        mock_post.return_value = mock.Mock(status_code=200)

        content = "行内容\n" * 8000  # 约 28 KB，与 GOT reasoning.md 同量级
        self.assertGreater(len(content.encode("utf-8")), MAX_MESSAGE_BYTES)
        self.assertTrue(_sender().send_to_ntfy(content, title="GOT 报告"))

        self.assertEqual(mock_put.call_count, 1)
        upload_url = mock_put.call_args.args[0]
        self.assertEqual(upload_url, "https://ntfy.sh/dsa-out-topic")
        self.assertEqual(mock_put.call_args.kwargs["data"], content.encode("utf-8"))
        self.assertIn("Filename", mock_put.call_args.kwargs["headers"])

        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["attach"], "https://ntfy.sh/file/report.md")
        self.assertIn("见附件", payload["message"])
        self.assertLessEqual(len(payload["message"].encode("utf-8")), MAX_MESSAGE_BYTES)

    @mock.patch("src.notification_sender.ntfy_sender.requests.put")
    @mock.patch("src.notification_sender.ntfy_sender.requests.post")
    def test_upload_failure_falls_back_to_chunks(self, mock_post, mock_put):
        mock_put.return_value = mock.Mock(status_code=500, json=lambda: {})
        mock_post.return_value = mock.Mock(status_code=200)

        content = "".join(f"第{i}行：一些较长的正文内容用于分段\n" for i in range(400))
        self.assertTrue(_sender().send_to_ntfy(content, title="长文本"))

        self.assertGreater(mock_post.call_count, 1)
        for call in mock_post.call_args_list:
            chunk = call.kwargs["json"]["message"]
            self.assertLessEqual(len(chunk.encode("utf-8")), MAX_MESSAGE_BYTES)
        joined = "".join(call.kwargs["json"]["message"] for call in mock_post.call_args_list)
        self.assertEqual(joined, content)

    def test_split_message_handles_cjk_and_over_long_line(self):
        text = "中文" * 3000  # 单行 18000 字节，无换行可切
        chunks = split_message(text)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.encode("utf-8")), MAX_MESSAGE_BYTES)
        self.assertEqual("".join(chunks), text)


class _FakeStreamResponse:
    def __init__(self, events):
        self._lines = [json.dumps(event).encode("utf-8") for event in events]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line
        # 模拟流结束（真实环境是长连接）


class _FakeSession:
    def __init__(self, events, post_status=200):
        self.events = events
        self.post_status = post_status
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append({"url": url, **kwargs})
        return _FakeStreamResponse(self.events)

    def post(self, url, json=None, headers=None, timeout=None, verify=None):
        self.post_calls.append({"url": url, "json": json, "headers": headers})
        return SimpleNamespace(status_code=self.post_status)


class _FakeDispatcher:
    def __init__(self, reply):
        self.reply = reply
        self.messages = []

    def dispatch(self, message):
        self.messages.append(message)
        return BotResponse.text_response(self.reply)


class TestNtfyStreamClient(unittest.TestCase):
    def _client(self, session, dispatcher=None, **kwargs):
        return ntfy_stream.NtfyStreamClient(
            "https://ntfy.sh/dsa-out-topic",
            "https://ntfy.sh/dsa-cmd-topic",
            session=session,
            dispatcher_factory=(lambda: dispatcher) if dispatcher else None,
            **kwargs,
        )

    def test_endpoint_parser_matches_sender(self):
        for url in (
            "https://ntfy.sh/topic",
            "https://ntfy.sh/",
            "https://host:8443/prefix/deep/topic",
            "http://127.0.0.1:8080/t",
            "",
            "ftp://ntfy.sh/topic",
        ):
            self.assertEqual(ntfy_stream.resolve_ntfy_endpoint(url), sender_resolve(url), url)

    def test_ignores_keepalive_open_and_echo_tag(self):
        session = _FakeSession(
            [
                {"event": "open", "topic": "dsa-cmd-topic"},
                {"event": "keepalive", "topic": "dsa-cmd-topic"},
                {"event": "message", "id": "1", "message": "自己发的", "tags": ["bot"]},
                {"event": "message", "id": "2", "message": "   "},
            ]
        )
        dispatcher = _FakeDispatcher("reply")
        client = self._client(session, dispatcher)

        client._consume_once()

        self.assertEqual(dispatcher.messages, [])
        self.assertEqual(session.post_calls, [])
        self.assertIsNone(client._last_id)

    def test_dispatches_message_and_replies_to_out_topic_with_echo_tag(self):
        session = _FakeSession(
            [
                {"event": "message", "id": "m1", "topic": "dsa-cmd-topic", "message": "/status"},
                {"event": "keepalive", "topic": "dsa-cmd-topic"},
            ]
        )
        dispatcher = _FakeDispatcher("状态正常")
        client = self._client(session, dispatcher)

        client._consume_once()

        self.assertEqual(len(dispatcher.messages), 1)
        bot_message = dispatcher.messages[0]
        self.assertEqual(bot_message.content, "/status")
        self.assertEqual(bot_message.platform, "ntfy")

        self.assertEqual(len(session.post_calls), 1)
        payload = session.post_calls[0]["json"]
        self.assertEqual(session.post_calls[0]["url"], "https://ntfy.sh")
        self.assertEqual(payload["topic"], "dsa-out-topic")
        self.assertEqual(payload["message"], "状态正常")
        self.assertEqual(payload["tags"], [ntfy_stream.ECHO_TAG])
        self.assertEqual(client._last_id, "m1")

    def test_resume_uses_last_message_id(self):
        session = _FakeSession(
            [{"event": "message", "id": "m9", "topic": "dsa-cmd-topic", "message": "/help"}]
        )
        client = self._client(session, _FakeDispatcher("ok"))

        client._consume_once()
        client._consume_once()

        # 首连锚定进程启动时间戳，绝不重放历史指令
        self.assertIn(f"since={client._started_at}", session.get_calls[0]["url"])
        self.assertIn("since=m9", session.get_calls[1]["url"])

    def test_long_reply_is_chunked_under_byte_limit(self):
        session = _FakeSession([], post_status=200)
        client = self._client(session, _FakeDispatcher(""))
        long_reply = "报告正文" * 3000

        self.assertTrue(client.publish(long_reply, markdown=True))

        self.assertGreater(len(session.post_calls), 1)
        for call in session.post_calls:
            self.assertLessEqual(len(call["json"]["message"].encode("utf-8")), MAX_MESSAGE_BYTES)
            self.assertEqual(call["json"]["tags"], [ntfy_stream.ECHO_TAG])
        joined = "".join(call["json"]["message"] for call in session.post_calls)
        self.assertEqual(joined, long_reply)

    def test_background_start_requires_both_topics(self):
        with mock.patch.object(ntfy_stream, "_client", None), mock.patch(
            "src.config.get_config",
            return_value=SimpleNamespace(
                ntfy_url="https://ntfy.sh/out", ntfy_cmd_url=None, ntfy_token=None,
                webhook_verify_ssl=True,
            ),
        ):
            self.assertFalse(ntfy_stream.start_ntfy_stream_background())

    def test_invalid_endpoint_raises(self):
        with self.assertRaises(ValueError):
            ntfy_stream.NtfyStreamClient("not-a-url", "https://ntfy.sh/cmd", session=_FakeSession([]))

    def test_stop_closes_active_stream_and_reports_stopped(self):
        closed = []
        client = self._client(_FakeSession([]))
        client._active_response = SimpleNamespace(close=lambda: closed.append(True))

        client.stop(wait=True)

        self.assertEqual(closed, [True], "stop() 必须关闭活动响应以解除读流阻塞")
        self.assertIsNone(client._active_response)
        self.assertFalse(client.is_running())

    def test_background_thread_exits_after_stop(self):
        session = _FakeSession(
            [{"event": "message", "id": "z1", "topic": "dsa-cmd-topic", "message": "/help"}]
        )
        client = self._client(session, _FakeDispatcher("ok"))

        client.start_background()
        self.assertTrue(client.is_running())
        client.stop(wait=True)

        self.assertFalse(client.is_running())


if __name__ == "__main__":
    unittest.main()
