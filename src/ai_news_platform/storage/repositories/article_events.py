from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from ai_news_platform.storage.db import utc_now_iso


EventType = Literal["INGESTED", "LLM_PROCESSED", "PUBLISHED", "ERROR"]


@dataclass(frozen=True)
class ArticleEvent:
    article_id: str
    event_type: EventType
    details: dict[str, Any] | None = None
    error_message: str | None = None


class ArticleEventsRepository:
    """
    Lightweight audit trail (append-only).
    """

    def append(self, conn: sqlite3.Connection, event: ArticleEvent) -> None:
        conn.execute(
            """
            INSERT INTO article_events (article_id, event_type, created_at, details_json, error_message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.article_id,
                event.event_type,
                utc_now_iso(),
                json.dumps(event.details or {}, default=str) if event.details is not None else None,
                event.error_message,
            ),
        )

