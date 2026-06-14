from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ai_news_platform.models.article import Article


LLMDecision = Literal["REJECT", "READY_TO_PUBLISH"]


@dataclass(frozen=True)
class LLMStageResult:
    decision: LLMDecision
    reason: str | None = None
    final_title: str | None = None
    final_text: str | None = None
    final_language: str | None = None


class LLMStageService(Protocol):
    """
    Single-step editorial LLM stage.

    Implementation will be added later (no provider integration in current scope).
    """

    def process_article(self, *, domain_id: str, prompt: str, article: Article) -> LLMStageResult: ...

