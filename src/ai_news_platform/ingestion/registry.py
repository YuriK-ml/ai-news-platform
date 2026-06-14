from __future__ import annotations

from ai_news_platform.ingestion.base import IngestionConnector


class ConnectorRegistry:
    """
    Maps source types (e.g., 'rss', 'website') to connectors.
    """

    def __init__(self) -> None:
        self._by_type: dict[str, IngestionConnector] = {}

    def register(self, connector: IngestionConnector) -> None:
        self._by_type[connector.type] = connector

    def get(self, source_type: str) -> IngestionConnector:
        try:
            return self._by_type[source_type]
        except KeyError as exc:
            raise KeyError(f"No connector registered for type={source_type!r}") from exc
