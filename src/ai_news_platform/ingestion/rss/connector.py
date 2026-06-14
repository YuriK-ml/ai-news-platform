from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
import structlog

from ai_news_platform.ingestion.base import IngestionConnector, RawIngestedItem
from ai_news_platform.models.source import Source


class RssConnector:
    type = "rss"

    def fetch(self, source: Source) -> list[RawIngestedItem]:
        if not source.url:
            raise ValueError(f"RSS source {source.id!r} is missing url")

        logger = structlog.get_logger(__name__).bind(source_id=source.id, source_type=source.type)
        logger.info("rss_fetch_start", url=source.url)

        timeout_seconds = float(source.config.get("timeout_seconds", 30))
        headers = {
            "User-Agent": source.config.get(
                "user_agent",
                "ai-news-platform/0.1 (+https://example.invalid; contact=devnull@example.invalid)",
            )
        }

        with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
            response = client.get(source.url)
            response.raise_for_status()

        parsed = feedparser.parse(response.text)
        if getattr(parsed, "bozo", 0):
            # feedparser bozo means parse issues; still may have entries
            logger.warning("rss_parse_warning", bozo_exception=str(getattr(parsed, "bozo_exception", "")))

        items: list[RawIngestedItem] = []
        for entry in parsed.entries or []:
            entry_dict = _entry_to_jsonable(entry)
            external_id = (
                entry_dict.get("id")
                or entry_dict.get("guid")
                or entry_dict.get("link")
                or entry_dict.get("title")
            )
            items.append(RawIngestedItem(source=source, external_id=external_id, payload=entry_dict))

        logger.info("rss_fetch_success", item_count=len(items))
        return items


def _entry_to_jsonable(entry: Any) -> dict[str, Any]:
    """
    Convert feedparser entries into a JSON-serializable dict with a stable subset of fields.
    """

    # feedparser entries are dict-like; converting to dict keeps many values JSONable.
    data = dict(entry)

    for key in ("published_parsed", "updated_parsed"):
        if key in data and data[key]:
            data[key] = _time_struct_to_iso(data[key])

    # Ensure nested structures are JSONable by a round-trip; trim if necessary later.
    json.dumps(data, default=str)
    return data


def _time_struct_to_iso(ts: Any) -> str:
    try:
        dt = datetime(*ts[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return str(ts)
