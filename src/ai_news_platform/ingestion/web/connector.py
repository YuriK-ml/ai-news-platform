from __future__ import annotations

from ai_news_platform.ingestion.base import RawIngestedItem
from ai_news_platform.models.source import Source


class WebsiteConnector:
    type = "website"

    def fetch(self, source: Source) -> list[RawIngestedItem]:
        raise NotImplementedError("Website ingestion not implemented yet.")

