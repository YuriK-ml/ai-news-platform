from __future__ import annotations

from typing import Protocol

from ai_news_platform.models.article import Article


class Publisher(Protocol):
    """
    Output channel abstraction.

    Telegram is the first implementation; future publishers can be added.
    """

    type: str

    def publish(self, article: Article) -> str:
        raise NotImplementedError

