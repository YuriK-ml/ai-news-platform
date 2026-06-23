from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog


@dataclass(frozen=True)
class OpenAIResponsesConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60
    max_output_tokens: int = 4000
    reasoning_effort: str = "minimal"


@dataclass(frozen=True)
class OpenAITextResponse:
    output_text: str
    model: str
    status: str | None
    incomplete_reason: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    reasoning_tokens: int


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
        self._logger = structlog.get_logger(__name__)

    @property
    def requested_model(self) -> str:
        return self._config.model

    @classmethod
    def from_env(cls, *, reasoning_effort: str = "minimal") -> "OpenAIResponsesClient":
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
        model = os.getenv("OPENAI_MODEL", "gpt-5.2")
        return cls(
            OpenAIResponsesConfig(
                api_key=api_key,
                base_url=base_url,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        )

    def create_text_response(self, *, instructions: str, input_text: str) -> str:
        return self.create_text_response_full(instructions=instructions, input_text=input_text).output_text

    def create_text_response_full(self, *, instructions: str, input_text: str) -> OpenAITextResponse:
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
            "reasoning": {"effort": self._config.reasoning_effort},
        }

        with httpx.Client(timeout=self._config.timeout_seconds, headers=headers) as client:
            resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                err = _extract_openai_error(resp)
                self._logger.error(
                    "openai_response_error",
                    http_status=resp.status_code,
                    requested_model=self._config.model,
                    error_type=err.get("type"),
                    error_code=err.get("code"),
                    error_message=err.get("message"),
                )
            resp.raise_for_status()
            data = resp.json()

        return _parse_text_response(data, fallback_model=self._config.model)


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


def _extract_openai_error(resp: httpx.Response) -> dict[str, str | None]:
    """
    Extract safe error details from OpenAI-style error payload.
    """

    try:
        data = resp.json()
    except Exception:
        return {"type": None, "code": None, "message": None}

    if not isinstance(data, dict):
        return {"type": None, "code": None, "message": None}

    err = data.get("error")
    if not isinstance(err, dict):
        return {"type": None, "code": None, "message": None}

    et = err.get("type") if isinstance(err.get("type"), str) else None
    ec = err.get("code") if isinstance(err.get("code"), (str, int)) else None
    em = err.get("message") if isinstance(err.get("message"), str) else None
    return {"type": et, "code": str(ec) if ec is not None else None, "message": em}


def _parse_text_response(response_json: dict[str, Any], *, fallback_model: str) -> OpenAITextResponse:
    output_text = _extract_output_text(response_json)
    model = response_json.get("model")
    if not isinstance(model, str) or not model.strip():
        model = fallback_model

    status = response_json.get("status")
    if not isinstance(status, str) or not status.strip():
        status = None

    incomplete_reason: str | None = None
    incomplete_details = response_json.get("incomplete_details")
    if isinstance(incomplete_details, dict):
        reason = incomplete_details.get("reason")
        if isinstance(reason, str) and reason.strip():
            incomplete_reason = reason

    usage = response_json.get("usage")
    if not isinstance(usage, dict):
        return OpenAITextResponse(
            output_text=output_text,
            model=model,
            status=status,
            incomplete_reason=incomplete_reason,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            reasoning_tokens=0,
        )

    input_tokens = usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else 0
    output_tokens = usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else 0
    total_tokens = usage.get("total_tokens") if isinstance(usage.get("total_tokens"), int) else (input_tokens + output_tokens)

    cached_tokens = 0
    input_details = usage.get("input_tokens_details")
    if isinstance(input_details, dict) and isinstance(input_details.get("cached_tokens"), int):
        cached_tokens = input_details.get("cached_tokens") or 0

    reasoning_tokens = 0
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict) and isinstance(output_details.get("reasoning_tokens"), int):
        reasoning_tokens = output_details.get("reasoning_tokens") or 0

    return OpenAITextResponse(
        output_text=output_text,
        model=model,
        status=status,
        incomplete_reason=incomplete_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
    )
