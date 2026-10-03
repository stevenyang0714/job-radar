from datetime import date

import httpx
import pytest

from jobradar.messages import format_job
from jobradar.notify import Telegram, TelegramError, split_message

TOKEN = "123456:SECRET-TOKEN"


def _telegram(handler):
    return Telegram(TOKEN, "42", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_split_message_keeps_lines_together():
    text = "\n".join(f"line {i:02d}" for i in range(10))  # 每行 7 字元
    chunks = split_message(text, limit=20)
    assert all(len(c) <= 20 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_message_hard_splits_very_long_lines():
    assert split_message("x" * 45, limit=20) == ["x" * 20, "x" * 20, "x" * 5]


def test_send_posts_html_message():
    def handler(request):
        assert request.url.path == f"/bot{TOKEN}/sendMessage"
        assert request.read().decode().count('"parse_mode":"HTML"') == 1
        return httpx.Response(200, json={"ok": True})

    _telegram(handler).send("<b>hi</b>")


def test_bad_request_raises_without_leaking_token():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    with pytest.raises(TelegramError) as exc:
        _telegram(handler).send("hi")
    assert "chat not found" in str(exc.value)
    assert "SECRET" not in str(exc.value)


def test_rate_limit_waits_and_retries(monkeypatch):
    waits = []
    monkeypatch.setattr("jobradar.notify.time.sleep", waits.append)
    responses = iter(
        [
            httpx.Response(429, json={"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 5}}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    _telegram(lambda request: next(responses)).send("hi")

    assert waits == [5]


def test_connection_error_message_hides_url(monkeypatch):
    monkeypatch.setattr("jobradar.notify.time.sleep", lambda _: None)

    def handler(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TelegramError) as exc:
        _telegram(handler).send("hi")
    assert str(exc.value) == "ConnectError"


def test_format_job_labels_and_escaping():
    today = date(2026, 10, 3)
    new = format_job(kind="new", title="R&D <Lead>", company="Acme", url="https://x/?a=1&b=2", posted_date=date(2026, 10, 1), today=today, score=82)
    assert new.startswith("🆕 <b>新刊登</b>（2 天前）｜<b>82 分</b>")
    assert "R&amp;D &lt;Lead&gt;" in new
    assert 'href="https://x/?a=1&amp;b=2"' in new

    old = format_job(kind="long_open", title="Risk", company="Acme", url="https://x", posted_date=date(2025, 6, 1), today=today, applicants=3)
    assert old.startswith("⏳ <b>已開 16 個月未關</b>（可能是常年招募）")
    assert "應徵 3 人" in old and "💰" not in old
