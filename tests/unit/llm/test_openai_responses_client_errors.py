from __future__ import annotations

import json

import httpx

from ai_news_platform.llm.openai_responses_client import OpenAIResponsesClient, OpenAIResponsesConfig


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def error(self, event: str, **kw) -> None:
        self.events.append((event, kw))


def test_openai_response_error_403_logged_safely(monkeypatch) -> None:
    cap = CapturingLogger()
    import ai_news_platform.llm.openai_responses_client as mod

    monkeypatch.setattr(mod.structlog, "get_logger", lambda name=None: cap)

    error_body = {
        "error": {
            "message": "You do not have access to this model",
            "type": "insufficient_quota",
            "code": "model_not_found",
        }
    }

    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    resp = httpx.Response(403, request=req, content=json.dumps(error_body).encode("utf-8"))

    class DummyClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict):
            return resp

    monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: DummyClient())

    client = OpenAIResponsesClient(
        OpenAIResponsesConfig(api_key="sk-test-secret", base_url="https://api.openai.com", model="gpt-5-mini")
    )

    try:
        client.create_text_response_full(instructions="SECRET_INSTRUCTIONS", input_text="SECRET_ARTICLE")
    except httpx.HTTPStatusError:
        pass

    assert cap.events, "Expected openai_response_error log event"
    event, payload = cap.events[0]
    assert event == "openai_response_error"
    assert payload["http_status"] == 403
    assert payload["requested_model"] == "gpt-5-mini"
    assert payload["error_type"] == "insufficient_quota"
    assert payload["error_code"] == "model_not_found"
    assert "access" in (payload["error_message"] or "").lower()

    # Ensure we do not log secrets or input content.
    for v in payload.values():
        if isinstance(v, str):
            assert "sk-test-secret" not in v
            assert "SECRET_INSTRUCTIONS" not in v
            assert "SECRET_ARTICLE" not in v

