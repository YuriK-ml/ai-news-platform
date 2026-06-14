from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ai_news_platform.models.source import Source
from ai_news_platform.storage.db import utc_now_iso


@dataclass(frozen=True)
class FetchUpdate:
    ok: bool
    http_status: int | None = None
    error_message: str | None = None
    last_item_external_id: str | None = None
    last_item_published_at: str | None = None


class SourceRepository:
    def upsert(self, conn: sqlite3.Connection, source: Source, *, enabled: bool = True) -> None:
        now = utc_now_iso()
        config_json = json.dumps(source.config or {}, default=str)
        conn.execute(
            """
            INSERT INTO sources (
              id, domain_id, type, name, url, enabled, config_json,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              domain_id=excluded.domain_id,
              type=excluded.type,
              name=excluded.name,
              url=excluded.url,
              enabled=excluded.enabled,
              config_json=excluded.config_json,
              updated_at=excluded.updated_at
            """,
            (
                source.id,
                source.domain_id,
                source.type,
                source.name,
                source.url,
                1 if enabled else 0,
                config_json,
                now,
                now,
            ),
        )

    def record_fetch(self, conn: sqlite3.Connection, source_id: str, update: FetchUpdate) -> None:
        now = utc_now_iso()
        if update.ok:
            conn.execute(
                """
                UPDATE sources SET
                  last_fetch_at=?,
                  last_success_at=?,
                  consecutive_error_count=0,
                  last_http_status=?,
                  last_error_message=NULL,
                  last_item_external_id=?,
                  last_item_published_at=?,
                  updated_at=?
                WHERE id=?
                """,
                (
                    now,
                    now,
                    update.http_status,
                    update.last_item_external_id,
                    update.last_item_published_at,
                    now,
                    source_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE sources SET
                  last_fetch_at=?,
                  last_error_at=?,
                  consecutive_error_count=consecutive_error_count + 1,
                  total_error_count=total_error_count + 1,
                  last_http_status=?,
                  last_error_message=?,
                  updated_at=?
                WHERE id=?
                """,
                (now, now, update.http_status, update.error_message, now, source_id),
            )
