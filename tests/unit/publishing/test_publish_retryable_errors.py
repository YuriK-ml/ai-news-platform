from __future__ import annotations

import httpx
import sqlite3
from pathlib import Path

from ai_news_platform.models.article import Article
from ai_news_platform.models.source import Source
from ai_news_platform.pipeline.runner import PipelineRunner
from ai_news_platform.settings.models import (
    AppConfig,
    DomainConfig,
    DomainLLMConfig,
    IngestionConfig,
    LLMConfig,
    LoggingConfig,
    PublishingConfig,
    RootConfig,
    Settings,
    StorageConfig,
    TelegramDomainConfig,
    TelegramPublishingConfig,
)
from ai_news_platform.storage.db import Database
from ai_news_platform.storage.repositories.articles import ArticleRepository
from ai_news_platform.storage.repositories.sources import SourceRepository


def _make_settings() -> Settings:
    root = RootConfig(
        app=AppConfig(service_name="ai-news-platform"),
        logging=LoggingConfig(level="INFO"),
        storage=StorageConfig(database_path_env="DATABASE_PATH", database_path_default="./data/app.sqlite3"),
        ingestion=IngestionConfig(max_article_age_hours=24),
        publishing=PublishingConfig(
            max_articles_per_run=2,
            interval_seconds=60,
            default_publisher="telegram",
            telegram=TelegramPublishingConfig(bot_token_env="TELEGRAM_BOT_TOKEN"),
        ),
        llm=LLMConfig(
            enabled=True,
            prompts_dir="prompts",
            default_target_language="ru",
            max_articles_per_run=100,
            batch_size=10,
        ),
        domains=[
            DomainConfig(
                id="football",
                name="Football",
                telegram=TelegramDomainConfig(channel_id="@x"),
                llm=DomainLLMConfig(enabled=True, target_language="ru", pipeline_prompt="football_pipeline.txt"),
                sources=[],
            )
        ],
    )
    return Settings(raw={}, config=root)


def _seed_ready_articles(db_path: Path) -> None:
    db = Database(path=str(db_path))
    with db.connect() as conn:
        db.initialize_schema(conn)

        src = Source(
            id="bbc_sport_football",
            domain_id="football",
            type="rss",
            name="BBC Sport Football",
            url="https://example.com/rss",
            config={},
        )
        SourceRepository().upsert(conn, src)

        repo = ArticleRepository()
        for n in range(2):
            a = Article(
                id=f"a{n}",
                domain_id="football",
                source_id="bbc_sport_football",
                canonical_url=f"https://example.com/{n}",
                title=f"t{n}",
                content=f"c{n}",
                metadata={"published_at": "2026-01-01T00:00:00Z"},
            )
            repo.insert_normalized(conn, article=a, source_item_external_id=f"x{n}", raw_json={})
            repo.mark_ready(
                conn,
                article_id=f"a{n}",
                final_language="ru",
                final_title="<b>T</b>",
                final_text="Body\n\nSource: BBC\nOriginal: https://example.com",
            )

        # Ensure deterministic ordering: a0 earlier than a1.
        conn.execute("UPDATE articles SET llm_processed_at='2026-01-01T00:00:00Z' WHERE id='a0'")
        conn.execute("UPDATE articles SET llm_processed_at='2026-01-02T00:00:00Z' WHERE id='a1'")
        conn.commit()


def _get_statuses(db_path: Path) -> dict[str, tuple[str, str]]:
    with Database(path=str(db_path)).connect() as conn:
        rows = conn.execute(
            "SELECT id, editorial_status, publication_status FROM articles ORDER BY id"
        ).fetchall()
        return {r["id"]: (r["editorial_status"], r["publication_status"]) for r in rows}


def test_connecterror_does_not_mark_error(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    db_path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    _seed_ready_articles(db_path)
    runner = PipelineRunner(settings)

    def _raise(self, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", "https://api.telegram.org"))

    monkeypatch.setattr("ai_news_platform.publishing.telegram.client.TelegramClient.send_message", _raise)

    ok = runner.publish_once(domain_ids=["football"])
    assert ok is False

    statuses = _get_statuses(db_path)
    assert statuses["a0"] == ("READY", "unpublished")
    assert statuses["a1"][0] == "READY"


def test_connecttimeout_does_not_mark_error(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    db_path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    _seed_ready_articles(db_path)
    runner = PipelineRunner(settings)

    def _raise(self, **kwargs):
        raise httpx.ConnectTimeout("boom", request=httpx.Request("POST", "https://api.telegram.org"))

    monkeypatch.setattr("ai_news_platform.publishing.telegram.client.TelegramClient.send_message", _raise)

    ok = runner.publish_once(domain_ids=["football"])
    assert ok is False

    statuses = _get_statuses(db_path)
    assert statuses["a0"] == ("READY", "unpublished")


def test_readtimeout_does_not_mark_error(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    db_path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    _seed_ready_articles(db_path)
    runner = PipelineRunner(settings)

    def _raise(self, **kwargs):
        raise httpx.ReadTimeout("boom", request=httpx.Request("POST", "https://api.telegram.org"))

    monkeypatch.setattr("ai_news_platform.publishing.telegram.client.TelegramClient.send_message", _raise)

    ok = runner.publish_once(domain_ids=["football"])
    assert ok is False

    statuses = _get_statuses(db_path)
    assert statuses["a0"] == ("READY", "unpublished")


def test_permanent_error_still_marks_failed(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    db_path = tmp_path / "t.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABCDEF")
    _seed_ready_articles(db_path)
    runner = PipelineRunner(settings)

    def _raise(self, **kwargs):
        raise RuntimeError("permanent")

    monkeypatch.setattr("ai_news_platform.publishing.telegram.client.TelegramClient.send_message", _raise)

    ok = runner.publish_once(domain_ids=["football"])
    assert ok is False

    statuses = _get_statuses(db_path)
    assert statuses["a0"] == ("ERROR", "failed")
    assert statuses["a1"][0] == "READY"

