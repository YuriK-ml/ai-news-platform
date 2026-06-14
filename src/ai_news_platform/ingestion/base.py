from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_news_platform.models.source import Source


@dataclass(frozen=True)
class RawIngestedItem:
    """
    Connector output before normalization.
    """

    source: Source
    external_id: str | None
    payload: object


class IngestionConnector(Protocol):
    type: str

    def fetch(self, source: Source) -> list[RawIngestedItem]:
        raise NotImplementedError

