from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
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
        retry_attempts = _as_int(source.config.get("retry_attempts"))
        if retry_attempts is None or retry_attempts < 1:
            retry_attempts = 1
        headers = {
            "User-Agent": source.config.get(
                "user_agent",
                "ai-news-platform/0.1 (+https://example.invalid; contact=devnull@example.invalid)",
            )
        }

        # Опциональные RSS-настройки для "тяжёлых" лент (например, AI-домен).
        # Если поля не заданы, поведение остаётся прежним: берём все entry в порядке RSS.
        rss_max_age_hours = _as_int(source.config.get("rss_max_age_hours"))
        rss_sort_by_published_desc = _as_bool(source.config.get("rss_sort_by_published_desc"))
        rss_max_items = _as_int(source.config.get("rss_max_items"))
        rss_drop_if_no_date = _as_bool(source.config.get("rss_drop_if_no_date"))

        last_exc: Exception | None = None
        for attempt in range(1, retry_attempts + 1):
            try:
                with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
                    response = client.get(source.url)
                    response.raise_for_status()
                last_exc = None
                break
            except (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as exc:
                last_exc = exc
                logger.warning(
                    "rss_fetch_attempt_failed",
                    attempt=attempt,
                    retry_attempts=retry_attempts,
                    error=str(exc),
                )
                if attempt < retry_attempts:
                    time.sleep(2)
                    continue
                break

        if last_exc is not None:
            raise last_exc

        parsed = feedparser.parse(response.text)
        if getattr(parsed, "bozo", 0):
            # feedparser bozo means parse issues; still may have entries
            logger.warning("rss_parse_warning", bozo_exception=str(getattr(parsed, "bozo_exception", "")))

        # Конвертируем entry в JSON-able dict и при необходимости фильтруем/сортируем/ограничиваем.
        candidates: list[tuple[dict[str, Any], datetime | None]] = []
        now_utc = datetime.now(timezone.utc)
        threshold = None
        if rss_max_age_hours is not None and rss_max_age_hours > 0:
            threshold = now_utc - timedelta(hours=rss_max_age_hours)

        for entry in parsed.entries or []:
            entry_dict = _entry_to_jsonable(entry)
            published_dt = _extract_entry_datetime(entry_dict)

            if published_dt is None and rss_drop_if_no_date:
                continue
            if threshold is not None and published_dt is not None and published_dt < threshold:
                continue

            candidates.append((entry_dict, published_dt))

        if rss_sort_by_published_desc:
            # Без даты отправляем в конец списка.
            candidates.sort(key=lambda x: x[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        if rss_max_items is not None:
            # Если лимит задан — применяем после фильтрации и сортировки.
            if rss_max_items <= 0:
                candidates = []
            else:
                candidates = candidates[:rss_max_items]

        items: list[RawIngestedItem] = []
        for entry_dict, _dt in candidates:
            external_id = entry_dict.get("id") or entry_dict.get("guid") or entry_dict.get("link") or entry_dict.get("title")
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


def _extract_entry_datetime(entry: dict[str, Any]) -> datetime | None:
    """
    Извлечь дату публикации для фильтра/сортировки.
    По требованиям: published_parsed, fallback updated_parsed.
    """

    value = entry.get("published_parsed") or entry.get("published") or entry.get("updated_parsed") or entry.get("updated")
    if not value:
        return None
    dt = _parse_datetime(value)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # ISO8601
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        # RFC2822 / другие форматы
        try:
            return parsedate_to_datetime(s)
        except Exception:
            return None
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _as_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)
