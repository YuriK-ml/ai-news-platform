from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import httpx

from ai_news_platform.pipeline.runner import PipelineRunner
from ai_news_platform.settings.models import (
    AppConfig,
    DomainConfig,
    DomainLLMConfig,
    IngestionConfig,
    LLMConfig,
    LLMModelPricing,
    LLMPricingConfig,
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
from ai_news_platform.models.source import Source
from ai_news_platform.models.article import Article


class AlwaysFailClient:
    requested_model = "gpt-5-mini"

    def create_text_response_full(self, *, instructions: str, input_text: str):
        raise httpx.HTTPStatusError(
            "forbidden",
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            response=httpx.Response(403, request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
        )


def _make_settings() -> Settings:
    root = RootConfig(
        app=AppConfig(service_name="ai-news-platform"),
        logging=LoggingConfig(level="INFO"),
        storage=StorageConfig(database_path_env="DATABASE_PATH", database_path_default="./data/app.sqlite3"),
        ingestion=IngestionConfig(max_article_age_hours=24),
        publishing=PublishingConfig(
            max_articles_per_run=2,
            default_publisher="telegram",
            telegram=TelegramPublishingConfig(bot_token_env="TELEGRAM_BOT_TOKEN"),
        ),
        llm=LLMConfig(
            enabled=True,
            prompts_dir="prompts",
            default_target_language="ru",
            max_articles_per_run=100,
            batch_size=20,
            pricing=LLMPricingConfig(
                models={
                    "gpt-5-mini": LLMModelPricing(
                        input_price_per_million_usd=Decimal("0.25"),
                        output_price_per_million_usd=Decimal("2.0"),
                    )
                }
            ),
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


def _seed_db(db_path: Path, *, count: int) -> sqlite3.Connection:
    db = Database(path=str(db_path))
    conn = db.connect()
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
    for n in range(count):
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

    conn.commit()
    return conn


def test_llm_stats_counts_attempts_when_all_batches_fail(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    runner = PipelineRunner(settings)

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )
    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env",
        lambda **kwargs: AlwaysFailClient(),
    )

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path, count=100)
    try:
        stats = runner._process_llm_stage(conn, settings.config.domains)
        assert stats is not None
        assert stats.attempted_request_count == 5
        assert stats.failed_request_count == 5
        assert stats.successful_request_count == 0
        assert stats.attempted_article_count == 100
        assert stats.article_count == 0
    finally:
        conn.close()
