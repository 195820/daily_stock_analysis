# -*- coding: utf-8 -*-
"""ntfy 附件路径：头部编码 + 单条消息（结论 + 附件）回归。

2026-10-27 两次真机事故：
1. 带 emoji/中文的标题直接塞 `Filename`/`Title` 头 → requests 按 latin-1 编码 → `UnicodeEncodeError`
   → 30 KB 正文退化成 9 段纯文本。
2. 附件 PUT 之后又 POST 一条摘要 → 用户侧看到两条气泡（一条空标题附件 + 一条摘要）。
"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from src.notification_sender.ntfy_sender import (
    NtfySender,
    attachment_filename,
)

TITLE = "📊 GOT 每日决策 — 2026-10-27"


class _Resp:
    status_code = 200

    def json(self):
        return {"attachment": {"url": "https://ntfy.sh/file/SMOKE"}}


def test_attachment_filename_is_latin1_safe() -> None:
    name = attachment_filename(TITLE)

    name.encode("latin-1")  # 不抛 UnicodeEncodeError 即达标
    assert name == "GOT-2026-10-27.md"


def test_attachment_filename_fallback_when_title_all_non_ascii() -> None:
    assert attachment_filename("📊 每日决策") == "got-daily-report.md"


def _sender(monkeypatch, captured: dict, *, with_attachment: bool = True):
    def fake_put(url, params=None, data=None, headers=None, timeout=None, verify=True):
        for key, value in headers.items():
            key.encode("latin-1")
            value.encode("latin-1")  # 复刻 http.client 的头部编码
        captured["put"] = {"url": url, "params": params, "headers": headers, "data": data}
        return _Resp() if with_attachment else SimpleNamespace(status_code=500, text="")

    def fake_post(server_url, json=None, headers=None, timeout=None, verify=True):
        captured.setdefault("posts", []).append(json)
        return _Resp()

    monkeypatch.setattr(requests, "put", fake_put)
    monkeypatch.setattr(requests, "post", fake_post)
    return NtfySender(
        SimpleNamespace(
            ntfy_url="https://ntfy.sh/dsa-test",
            ntfy_token=None,
            webhook_verify_ssl=True,
        )
    )


def test_long_message_is_single_message_with_summary(monkeypatch) -> None:
    captured = {}
    sender = _sender(monkeypatch, captured)
    content = TITLE + "\n\n# GOT 运行推理汇总\n" + "影子段读数\n" * 2000
    summary = "📊 GOT 每日决策 — 2026-10-27\n操作总结: 卖出 1 只"

    assert sender.send_to_ntfy(content, title=TITLE, summary=summary) is True

    put = captured["put"]
    assert put["data"] == content.encode("utf-8")  # 附件是全量正文
    assert put["params"]["message"].startswith(summary)  # 消息正文 = 结论
    assert "见附件" in put["params"]["message"]
    assert put["params"]["title"] == TITLE  # 标题走 query，UTF-8 安全
    assert put["headers"]["Filename"] == "GOT-2026-10-27.md"
    assert "posts" not in captured  # 不再额外 POST 摘要气泡


def test_long_message_falls_back_to_chunks_when_upload_fails(monkeypatch) -> None:
    captured = {}
    sender = _sender(monkeypatch, captured, with_attachment=False)
    content = TITLE + "\n\n" + "影子段读数\n" * 2000

    assert sender.send_to_ntfy(content, title=TITLE, summary="结论") is True

    posts = captured["posts"]
    assert len(posts) > 1
    assert posts[0]["title"].endswith(f"(1/{len(posts)})")
