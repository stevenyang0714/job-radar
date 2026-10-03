"""Telegram 推播。"""

import time

import httpx

MAX_LENGTH = 4000  # Telegram 單則上限 4096 字元，留一點餘裕


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None, retries: int = 3) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=20)
        self._retries = retries

    def send(self, text: str) -> None:
        """發送 HTML 格式訊息；太長會依行拆成多則。"""
        for chunk in split_message(text):
            self._send_one(chunk)

    def _send_one(self, text: str) -> None:
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        error = "unknown"
        for attempt in range(1, self._retries + 1):
            wait = 2**attempt
            try:
                resp = self._client.post(self._url, json=payload)
                data = resp.json()
            except (httpx.TransportError, ValueError) as e:
                error = type(e).__name__  # 不顯示 httpx 的錯誤訊息：裡面的網址含 bot token
            else:
                if data.get("ok"):
                    return
                error = data.get("description") or f"HTTP {resp.status_code}"
                if resp.status_code != 429 and resp.status_code < 500:
                    raise TelegramError(error)
                wait = (data.get("parameters") or {}).get("retry_after") or wait
            if attempt < self._retries:
                time.sleep(wait)
        raise TelegramError(error)


def split_message(text: str, limit: int = MAX_LENGTH) -> list[str]:
    """依行拆分，每則不超過 limit；單行過長才硬切。"""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
