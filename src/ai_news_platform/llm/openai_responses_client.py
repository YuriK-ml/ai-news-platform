from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60
    max_output_tokens: int = 900


class OpenAIResponsesClient:
    """
    Minimal OpenAI-compatible Responses API client.

    Uses POST {base_url}/v1/responses with:
    - `instructions`: system/developer prompt text
    - `input`: user content (article template)
    """

    def __init__(self, config: OpenAIResponsesConfig) -> None:
        if not config.api_key:
            raise ValueError("OPENAI_API_KEY is missing/empty")
        if not config.base_url:
            raise ValueError("OPENAI_BASE_URL is missing/empty")
        if not config.model:
            raise ValueError("OPENAI_MODEL is missing/empty")
        self._config = config

    @classmethod
    def from_env(cls) -> "OpenAIResponsesClient":
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
        model = os.getenv("OPENAI_MODEL", "gpt-5.2")
        return cls(OpenAIResponsesConfig(api_key=api_key, base_url=base_url, model=model))

    def create_text_response(self, *, instructions: str, input_text: str) -> str:
        url = _responses_url(self._config.base_url)
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._config.model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": self._config.max_output_tokens,
            "text": {"format": {"type": "text"}},
        }

        with httpx.Client(timeout=self._config.timeout_seconds, headers=headers) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        return _extract_output_text(data)


def _responses_url(base_url: str) -> str:
    """
    Build the Responses API URL.

    If `base_url` already includes `/v1`, do not append it again.
    Examples:
    - https://api.openai.com        -> https://api.openai.com/v1/responses
    - https://api.openai.com/v1     -> https://api.openai.com/v1/responses
    - https://proxy.example.com/v1  -> https://proxy.example.com/v1/responses
    """

    base = (base_url or "").rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/responses"
    return f"{base}/v1/responses"


def _extract_output_text(response_json: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response_json.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
    out = "\n".join(parts).strip()
    if not out:
        raise ValueError("Responses API returned no output_text")
    return out
