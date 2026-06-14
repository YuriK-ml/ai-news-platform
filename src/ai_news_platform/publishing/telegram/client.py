from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: int


class TelegramClient:
    """
    Minimal Telegram Bot API client wrapper.
    """

    def __init__(self, *, bot_token: str, base_url: str = "https://api.telegram.org") -> None:
        if not bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is missing/empty")
        self._bot_token = bot_token
        self._base_url = base_url.rstrip("/")

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        parse_mode: str | None = "HTML",
        disable_web_page_preview: bool = False,
        timeout_seconds: float = 30,
    ) -> TelegramSendResult:
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode and parse_mode != "plain":
            payload["parse_mode"] = parse_mode

        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data!r}")

        message_id = int(data["result"]["message_id"])
        return TelegramSendResult(message_id=message_id)
