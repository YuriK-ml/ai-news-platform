from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from ai_news_platform.llm.openai_responses_client import OpenAITextResponse
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
from ai_news_platform.models.article import Article
from ai_news_platform.models.source import Source


class CountingBatchClient:
    def __init__(self) -> None:
        self.requested_model = "gpt-5-mini"
        self.calls = 0

    def create_text_response_full(self, *, instructions: str, input_text: str) -> OpenAITextResponse:
        self.calls += 1

        ids = []
        for line in input_text.splitlines():
            line = line.strip()
            if line.startswith("Article-ID:"):
                ids.append(line.split(":", 1)[1].strip())

        blocks = []
        for aid in ids:
            blocks.append(
                f"""
### RESULT
Article-ID: {aid}
Status: REJECT
Reason:
not directly related to FIFA World Cup 2026
### END RESULT
""".strip()
            )

        return OpenAITextResponse(
            output_text="\n\n".join(blocks).strip(),
            model="gpt-5-mini",
            status="completed",
            incomplete_reason=None,
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cached_tokens=0,
            reasoning_tokens=0,
        )


def _make_settings(*, batch_size: int) -> Settings:
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
            batch_size=batch_size,
            reasoning_effort="minimal",
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


def test_runner_uses_batch_size_10_to_chunk_requests(tmp_path, monkeypatch) -> None:
    settings = _make_settings(batch_size=10)
    runner = PipelineRunner(settings)

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )

    client = CountingBatchClient()
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: client)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path, count=11)
    try:
        runner._process_llm_stage(conn, settings.config.domains)
        assert client.calls == 2
    finally:
        conn.close()

