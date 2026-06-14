from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Database:
    """
    SQLite database access layer.
    """

    def __init__(self, *, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize_schema(self, conn: sqlite3.Connection) -> None:
        schema_path = Path(__file__).parent / "schema" / "schema.sql"
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        self._ensure_article_publish_columns(conn)

    def _ensure_article_publish_columns(self, conn: sqlite3.Connection) -> None:
        """
        Best-effort schema evolution for local development.

        SQLite `CREATE TABLE IF NOT EXISTS` does not add new columns to existing tables.
        """

        existing = {row["name"] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
        desired: list[tuple[str, str]] = [
            ("publication_status", "TEXT"),
            ("publication_error", "TEXT"),
            ("telegram_message_id", "INTEGER"),
            ("telegram_channel", "TEXT"),
            ("telegram_published_at", "TEXT"),
            ("editorial_status", "TEXT"),
            ("status_updated_at", "TEXT"),
            ("rejection_reason", "TEXT"),
            ("rejected_at", "TEXT"),
            ("llm_processed_at", "TEXT"),
            ("final_language", "TEXT"),
            ("final_title", "TEXT"),
            ("final_text", "TEXT"),
            ("last_error_stage", "TEXT"),
            ("last_error_message", "TEXT"),
            ("last_error_at", "TEXT"),
        ]
        for name, col_type in desired:
            if name not in existing:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {col_type}")

        # Best-effort backfill for newly added editorial columns.
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE articles
            SET editorial_status = COALESCE(editorial_status, 'NEW'),
                status_updated_at = COALESCE(status_updated_at, ?)
            WHERE editorial_status IS NULL OR status_updated_at IS NULL
            """,
            (now,),
        )
        conn.execute(
            """
            UPDATE articles
            SET editorial_status = 'PUBLISHED',
                status_updated_at = COALESCE(status_updated_at, ?)
            WHERE (publication_status='published' OR telegram_message_id IS NOT NULL OR telegram_published_at IS NOT NULL)
            """,
            (now,),
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
