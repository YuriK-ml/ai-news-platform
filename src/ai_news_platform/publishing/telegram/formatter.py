from __future__ import annotations

import html
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def format_article_message(
    *,
    title: str | None,
    source_name: str,
    published_at: str | None,
    url: str,
    parse_mode: str = "HTML",
) -> str:
    """
    Simple message format:

    Title

    Source

    Published date

    URL
    """

    safe_title = (title or "").strip() or url
    safe_published = _format_published(published_at) if published_at else "-"

    if parse_mode == "HTML":
        safe_title = html.escape(safe_title)
        safe_source = html.escape(source_name)
        safe_published = html.escape(safe_published)
        safe_url = html.escape(url)
    else:
        safe_source = source_name
        safe_url = url

    return f"{safe_title}\n\n{safe_source}\n\n{safe_published}\n\n{safe_url}"


def _format_published(value: str) -> str:
    value = value.strip()
    dt = _parse_datetime(value)
    if dt is None:
        return value
    # Display in UTC to keep logs and messaging consistent.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_datetime(value: str) -> datetime | None:
    # ISO 8601
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    # RFC822-ish (common in RSS)
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None
