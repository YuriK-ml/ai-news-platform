from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ai_news_platform.ingestion.base import RawIngestedItem
from ai_news_platform.models.article import Article


def normalize_item(item: RawIngestedItem) -> Article:
    payload = _as_dict(item.payload)

    canonical_url = (payload.get("link") or "").strip()
    if not canonical_url:
        raise ValueError(f"Missing link/canonical_url for source={item.source.id}")

    title = _first_str(payload.get("title"))
    content = _extract_content(payload)

    # Stable, deterministic id based on (domain_id, canonical_url).
    article_id = hashlib.sha256(f"{item.source.domain_id}|{canonical_url}".encode("utf-8")).hexdigest()

    metadata: dict[str, Any] = {
        "external_id": item.external_id,
        "published_at": payload.get("published") or payload.get("published_parsed"),
        "updated_at": payload.get("updated") or payload.get("updated_parsed"),
        "author": payload.get("author"),
        "tags": payload.get("tags"),
    }

    return Article(
        id=article_id,
        domain_id=item.source.domain_id,
        source_id=item.source.id,
        canonical_url=canonical_url,
        title=title,
        content=content,
        metadata=metadata,
    )


def _as_dict(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    raise TypeError(f"Unsupported payload type: {type(payload)!r}")


def _first_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _extract_content(payload: dict[str, Any]) -> str | None:
    if isinstance(payload.get("content"), list) and payload["content"]:
        first = payload["content"][0]
        if isinstance(first, dict) and isinstance(first.get("value"), str):
            return first["value"]
        return str(first)
    if isinstance(payload.get("summary"), str):
        return payload["summary"]
    if isinstance(payload.get("description"), str):
        return payload["description"]
    return None
