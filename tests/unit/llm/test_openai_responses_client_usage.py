from __future__ import annotations

from ai_news_platform.llm.openai_responses_client import (
    OpenAIResponsesClient,
    OpenAIResponsesConfig,
    _parse_text_response,
)


def _make_response_json(*, status: str = "completed", include_usage: bool = True) -> dict:
    data: dict = {
        "model": "gpt-5.2",
        "status": status,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            }
        ],
    }
    if include_usage:
        data["usage"] = {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 7},
            "output_tokens_details": {"reasoning_tokens": 11},
        }
    return data


def test_parse_completed_response_with_usage() -> None:
    resp = _parse_text_response(_make_response_json(), fallback_model="fallback")
    assert resp.output_text == "Hello"
    assert resp.model == "gpt-5.2"
    assert resp.status == "completed"
    assert resp.incomplete_reason is None
    assert resp.input_tokens == 100
    assert resp.output_tokens == 50
    assert resp.total_tokens == 150
    assert resp.cached_tokens == 7
    assert resp.reasoning_tokens == 11


def test_parse_incomplete_response_with_reason() -> None:
    data = _make_response_json(status="incomplete", include_usage=True)
    data["incomplete_details"] = {"reason": "max_output_tokens"}
    resp = _parse_text_response(data, fallback_model="fallback")
    assert resp.status == "incomplete"
    assert resp.incomplete_reason == "max_output_tokens"


def test_parse_response_without_usage_is_zeroed() -> None:
    resp = _parse_text_response(_make_response_json(include_usage=False), fallback_model="fallback")
    assert resp.input_tokens == 0
    assert resp.output_tokens == 0
    assert resp.total_tokens == 0
    assert resp.cached_tokens == 0
    assert resp.reasoning_tokens == 0


def test_create_text_response_preserves_old_contract(monkeypatch) -> None:
    class DummyResp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return _make_response_json()

    class DummyClient:
        def __init__(self, *args, **kwargs) -> None:
            self.last_payload = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, json: dict):
            self.last_payload = json
            return DummyResp()

    import ai_news_platform.llm.openai_responses_client as mod

    dummy = DummyClient()
    monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: dummy)

    client = OpenAIResponsesClient(
        OpenAIResponsesConfig(
            api_key="k",
            base_url="https://api.openai.com",
            model="gpt-5.2",
            reasoning_effort="minimal",
        )
    )
    text = client.create_text_response(instructions="I", input_text="X")
    assert text == "Hello"

    full = client.create_text_response_full(instructions="I", input_text="X")
    assert full.output_text == "Hello"
    assert isinstance(dummy.last_payload["max_output_tokens"], int)
    assert dummy.last_payload["reasoning"]["effort"] == "minimal"
