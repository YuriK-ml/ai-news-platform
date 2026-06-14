from __future__ import annotations

from ai_news_platform.publishing.base import Publisher


class PublisherRouter:
    """
    Routes publication to the correct publisher implementation and channel(s) per domain.
    """

    def __init__(self) -> None:
        self._by_type: dict[str, Publisher] = {}

    def register(self, publisher: Publisher) -> None:
        raise NotImplementedError("Publisher registration not implemented yet.")

    def get(self, publisher_type: str) -> Publisher:
        raise NotImplementedError("Publisher lookup not implemented yet.")

