from __future__ import annotations

from ai_news_platform.logging.redaction import redact_secrets


def test_redact_telegram_bot_url_does_not_leak_token() -> None:
    token = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    msg = f'HTTP Request: POST https://api.telegram.org/bot{token}/sendMessage "HTTP/1.1 200 OK"'
    out = redact_secrets(msg)
    assert token not in out
    assert "https://api.telegram.org/bot***/sendMessage" in out

