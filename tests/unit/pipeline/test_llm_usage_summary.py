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
from ai_news_platform.models.source import Source
from ai_news_platform.models.article import Article


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def info(self, event: str, **kw) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw) -> None:
        self.events.append((event, kw))

    def exception(self, event: str, **kw) -> None:
        self.events.append((event, kw))


class BatchFakeClient:
    def __init__(self) -> None:
        self.calls = 0
        self.requested_model = "gpt-5.2"

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
Status: READY_TO_PUBLISH
Title:
<b>T</b>
Text:
Body
### END RESULT
""".strip()
            )
        output_text = "\n\n".join(blocks).strip()

        return OpenAITextResponse(
            output_text=output_text,
            model="gpt-5.2",
            status="completed",
            incomplete_reason=None,
            input_tokens=1000,
            output_tokens=500,
            total_tokens=1500,
            cached_tokens=100,
            reasoning_tokens=0,
        )

    def create_text_response(self, *, instructions: str, input_text: str) -> str:
        return self.create_text_response_full(instructions=instructions, input_text=input_text).output_text


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
                    "gpt-5.2": LLMModelPricing(
                        input_price_per_million_usd=Decimal("1.0"),
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


def test_llm_stage_usage_summary_sums_two_batches(tmp_path, monkeypatch) -> None:
    settings = _make_settings()
    runner = PipelineRunner(settings)
    runner.logger = CapturingLogger()

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )
    fake = BatchFakeClient()
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: fake)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path, count=40)
    try:
        runner._process_llm_stage(conn, settings.config.domains)
        conn.commit()

        batch_usage = [e for e in runner.logger.events if e[0] == "llm_batch_usage"]
        assert len(batch_usage) == 2

        summary = [e for e in runner.logger.events if e[0] == "llm_stage_usage_summary"]
        assert len(summary) == 1
        payload = summary[0][1]
        assert payload["request_count"] == 2
        assert payload["attempted_request_count"] == 2
        assert payload["successful_request_count"] == 2
        assert payload["failed_request_count"] == 0
        assert payload["attempted_article_count"] == 40
        assert payload["article_count"] == 40
        assert payload["completed_response_count"] == 2
        assert payload["incomplete_response_count"] == 0
        assert payload["input_tokens"] == 2000
        assert payload["output_tokens"] == 1000
        assert payload["total_tokens"] == 3000
        assert payload["cached_tokens"] == 200
        assert payload["reasoning_tokens"] == 0
        # cost = (2000 * 1 + 1000 * 2) / 1e6 = 0.004000
        assert payload["total_cost_usd"] == "0.004000"
        assert payload["total_cost_cents"] == 0
    finally:
        conn.close()
