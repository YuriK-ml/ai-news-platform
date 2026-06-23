from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import structlog

from ai_news_platform.models.article import Article, ArticleLifecycleState
from ai_news_platform.storage.db import utc_now_iso


@dataclass(frozen=True)
class InsertOutcome:
    inserted: bool
    article_id: str


class ArticleRepository:
    """
    Article persistence and lifecycle state management.

    Deduplication is enforced by SQLite unique constraint on (domain_id, canonical_url).
    """

    def insert_normalized(
        self,
        conn: sqlite3.Connection,
        *,
        article: Article,
        source_item_external_id: str | None,
        raw_json: dict | None,
    ) -> InsertOutcome:
        logger = structlog.get_logger(__name__).bind(domain_id=article.domain_id, source_id=article.source_id)
        now = utc_now_iso()

        raw_json_text = json.dumps(raw_json or {}, default=str) if raw_json is not None else None
        normalized_json_text = json.dumps(
            {
                "id": article.id,
                "domain_id": article.domain_id,
                "source_id": article.source_id,
                "canonical_url": article.canonical_url,
                "title": article.title,
                "content": article.content,
                "metadata": article.metadata or {},
            },
            default=str,
        )

        cursor = conn.execute(
            """
            INSERT INTO articles (
              id, domain_id, source_id, canonical_url, source_item_external_id,
              title, content, published_at,
              lifecycle_state,
              raw_json, normalized_json,
              publication_status,
              editorial_status,
              status_updated_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain_id, canonical_url) DO NOTHING
            """,
            (
                article.id,
                article.domain_id,
                article.source_id,
                article.canonical_url,
                source_item_external_id,
                article.title,
                article.content,
                (article.metadata or {}).get("published_at"),
                ArticleLifecycleState.NORMALIZED.value,
                raw_json_text,
                normalized_json_text,
                "unpublished",
                "NEW",
                now,
                now,
                now,
            ),
        )
        inserted = cursor.rowcount == 1
        if inserted:
            logger.info("article_inserted", article_id=article.id, url=article.canonical_url)
        else:
            logger.info("article_duplicate", article_id=article.id, url=article.canonical_url)
        return InsertOutcome(inserted=inserted, article_id=article.id)

    def get_publish_candidates_by_ids(
        self, conn: sqlite3.Connection, *, article_ids: list[str]
    ) -> list[sqlite3.Row]:
        if not article_ids:
            return []
        placeholders = ",".join(["?"] * len(article_ids))
        rows = conn.execute(
            f"""
            SELECT
              a.id,
              a.domain_id,
              a.source_id,
              a.canonical_url,
              a.title,
              a.published_at,
              a.publication_status,
              a.telegram_message_id,
              a.telegram_channel,
              a.telegram_published_at,
              s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.id IN ({placeholders})
            """,
            tuple(article_ids),
        ).fetchall()
        return rows

    def list_llm_candidates(self, conn: sqlite3.Connection, *, domain_ids: list[str], limit: int) -> list[sqlite3.Row]:
        if limit <= 0 or not domain_ids:
            return []
        placeholders = ",".join(["?"] * len(domain_ids))
        rows = conn.execute(
            f"""
            SELECT
              a.id,
              a.domain_id,
              a.source_id,
              a.canonical_url,
              a.title,
              a.content,
              a.published_at,
              a.editorial_status,
              s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.domain_id IN ({placeholders})
              AND (a.editorial_status IS NULL OR a.editorial_status='NEW')
            ORDER BY a.created_at ASC
            LIMIT ?
            """,
            tuple(domain_ids) + (limit,),
        ).fetchall()
        return rows

    def list_ready_for_publication(
        self, conn: sqlite3.Connection, *, domain_ids: list[str], limit: int
    ) -> list[sqlite3.Row]:
        if limit <= 0 or not domain_ids:
            return []
        placeholders = ",".join(["?"] * len(domain_ids))
        rows = conn.execute(
            f"""
            SELECT
              a.id,
              a.domain_id,
              a.source_id,
              a.canonical_url,
              a.published_at,
              a.final_title,
              a.final_text,
              a.final_language,
              a.telegram_message_id,
              a.telegram_channel,
              a.telegram_published_at,
              a.publication_status,
              s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.domain_id IN ({placeholders})
              AND a.editorial_status='READY'
              AND (a.publication_status IS NULL OR a.publication_status IN ('unpublished','failed'))
              AND a.telegram_message_id IS NULL
              AND a.telegram_published_at IS NULL
            ORDER BY a.llm_processed_at ASC
            LIMIT ?
            """,
            tuple(domain_ids) + (limit,),
        ).fetchall()
        return rows

    def get_next_ready_unpublished_for_publication(
        self, conn: sqlite3.Connection, *, domain_ids: list[str]
    ) -> sqlite3.Row | None:
        """
        Select exactly one article ready to publish.
        """

        rows = self.list_ready_for_publication(conn, domain_ids=domain_ids, limit=50)
        for row in rows:
            status = row["publication_status"]
            if status is None or status == "unpublished":
                return row
        return None

    def list_unpublished_candidates(
        self, conn: sqlite3.Connection, *, domain_ids: list[str], limit: int
    ) -> list[sqlite3.Row]:
        if limit <= 0:
            return []
        placeholders = ",".join(["?"] * len(domain_ids))
        rows = conn.execute(
            f"""
            SELECT
              a.id,
              a.domain_id,
              a.source_id,
              a.canonical_url,
              a.title,
              a.published_at,
              a.publication_status,
              a.telegram_message_id,
              a.telegram_channel,
              a.telegram_published_at,
              s.name AS source_name
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE a.domain_id IN ({placeholders})
              AND (a.publication_status IS NULL OR a.publication_status='unpublished' OR a.publication_status='failed')
              AND a.telegram_message_id IS NULL
              AND a.telegram_published_at IS NULL
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            tuple(domain_ids) + (limit,),
        ).fetchall()
        return rows

    def mark_rejected(
        self, conn: sqlite3.Connection, *, article_id: str, explanation: str | None
    ) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              editorial_status='REJECTED',
              status_updated_at=?,
              rejection_reason=?,
              rejected_at=?,
              updated_at=?
            WHERE id=?
            """,
            (now, explanation, now, now, article_id),
        )

    def mark_ready(
        self,
        conn: sqlite3.Connection,
        *,
        article_id: str,
        final_language: str,
        final_title: str,
        final_text: str,
    ) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              editorial_status='READY',
              status_updated_at=?,
              llm_processed_at=?,
              final_language=?,
              final_title=?,
              final_text=?,
              updated_at=?
            WHERE id=?
            """,
            (now, now, final_language, final_title, final_text, now, article_id),
        )

    def mark_error(self, conn: sqlite3.Connection, *, article_id: str, stage: str, message: str) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              editorial_status='ERROR',
              status_updated_at=?,
              last_error_stage=?,
              last_error_message=?,
              last_error_at=?,
              updated_at=?
            WHERE id=?
            """,
            (now, stage, message, now, now, article_id),
        )

    def record_last_error(self, conn: sqlite3.Connection, *, article_id: str, stage: str, message: str) -> None:
        """
        Record last error fields without changing editorial status.
        """

        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              last_error_stage=?,
              last_error_message=?,
              last_error_at=?,
              updated_at=?
            WHERE id=?
            """,
            (stage, message, now, now, article_id),
        )

    def mark_skipped_old(self, conn: sqlite3.Connection, *, article_id: str, reason: str) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              publication_status='skipped_old',
              publication_error=?,
              updated_at=?
            WHERE id=?
            """,
            (reason, now, article_id),
        )

    def mark_publish_failed(self, conn: sqlite3.Connection, *, article_id: str, error: str) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              publication_status='failed',
              publication_error=?,
              updated_at=?
            WHERE id=?
            """,
            (error, now, article_id),
        )

    def mark_published(
        self,
        conn: sqlite3.Connection,
        *,
        article_id: str,
        telegram_message_id: int,
        telegram_channel: str,
        telegram_published_at: str,
    ) -> None:
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles SET
              editorial_status='PUBLISHED',
              status_updated_at=?,
              publication_status='published',
              publication_error=NULL,
              telegram_message_id=?,
              telegram_channel=?,
              telegram_published_at=?,
              lifecycle_state=?,
              updated_at=?
            WHERE id=?
            """,
            (
                now,
                telegram_message_id,
                telegram_channel,
                telegram_published_at,
                ArticleLifecycleState.PUBLISHED.value,
                now,
                article_id,
            ),
        )


def is_within_max_age(*, published_at: str | None, max_age_hours: int) -> bool:
    if not published_at:
        return False
    dt = _parse_datetime(published_at)
    if dt is None:
        return False
    threshold = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    dt_utc = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(timezone.utc) >= threshold


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return parsedate_to_datetime(value)
    except Exception:
        return None
