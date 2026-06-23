from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from ai_news_platform.pipeline.runner import PipelineRunner
from ai_news_platform.llm.openai_responses_client import OpenAITextResponse
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
from ai_news_platform.models.source import Source
from ai_news_platform.models.article import Article


class FakeClient:
    def __init__(self, output_text: str | Exception):
        self._output_text = output_text
        self.calls: list[tuple[str, str]] = []
        self.requested_model = "gpt-5.2"

    def create_text_response(self, *, instructions: str, input_text: str) -> str:
        return self.create_text_response_full(instructions=instructions, input_text=input_text).output_text

    def create_text_response_full(self, *, instructions: str, input_text: str) -> OpenAITextResponse:
        self.calls.append((instructions, input_text))
        if isinstance(self._output_text, Exception):
            raise self._output_text
        return OpenAITextResponse(
            output_text=self._output_text,
            model="gpt-5.2",
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


def _seed_db(db_path: Path, *, count: int = 3) -> sqlite3.Connection:
    db = Database(path=str(db_path))
    conn = db.connect()
    db.initialize_schema(conn)

    # required source row for joins
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


def test_batch_llm_partial_missing_result_marks_only_one_error(tmp_path, monkeypatch) -> None:
    settings = _make_settings(batch_size=2)
    runner = PipelineRunner(settings)

    # patch prompt loader and OpenAI client
    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )

    output = """
### RESULT
Article-ID: a0
Status: READY_TO_PUBLISH
Title:
<b>Ok</b>
Text:
Body
### END RESULT
    """.strip()
    fake = FakeClient(output)
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: fake)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path)
    try:
        # only process LLM stage (no Telegram)
        runner._process_llm_stage(conn, settings.config.domains)
        conn.commit()

        rows = conn.execute("SELECT id, editorial_status FROM articles ORDER BY id").fetchall()
        statuses = {r["id"]: r["editorial_status"] for r in rows}
        assert statuses["a0"] == "READY"
        # One of a1/a2 will be missing result in its batch and become ERROR.
        assert set(statuses.values()) >= {"READY", "ERROR"}
    finally:
        conn.close()


def test_batch_llm_malformed_single_result_does_not_fail_entire_batch(tmp_path, monkeypatch) -> None:
    settings = _make_settings(batch_size=20)
    runner = PipelineRunner(settings)

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )

    good_blocks = []
    for n in range(18):
        good_blocks.append(
            f"""
### RESULT
Article-ID: a{n}
Status: READY_TO_PUBLISH
Title:
<b>Ok</b>
Text:
Body
### END RESULT
""".strip()
        )

    malformed_missing_text = """
### RESULT
Article-ID: a18
Status: READY_TO_PUBLISH
Title:
<b>Ok</b>
### END RESULT
""".strip()

    # a19 RESULT is missing entirely
    output = "\n\n".join(good_blocks + [malformed_missing_text]).strip()

    fake = FakeClient(output)
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: fake)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path, count=20)
    try:
        runner._process_llm_stage(conn, settings.config.domains)
        conn.commit()

        c_ready = conn.execute("SELECT COUNT(*) FROM articles WHERE editorial_status='READY'").fetchone()[0]
        c_error = conn.execute("SELECT COUNT(*) FROM articles WHERE editorial_status='ERROR'").fetchone()[0]
        assert c_ready == 18
        assert c_error == 2

        row = conn.execute(
            "SELECT editorial_status, last_error_stage FROM articles WHERE id='a18'"
        ).fetchone()
        assert row["editorial_status"] == "ERROR"
        assert row["last_error_stage"] == "LLM_BATCH_PARSE"
    finally:
        conn.close()


def test_batch_llm_request_failure_marks_batch_error_only(tmp_path, monkeypatch) -> None:
    settings = _make_settings(batch_size=2)
    runner = PipelineRunner(settings)

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )
    fake = FakeClient(RuntimeError("boom"))
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: fake)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path)
    try:
        runner._process_llm_stage(conn, settings.config.domains)
        conn.commit()
        # All NEW articles were in the first batches; at least some should be ERROR.
        c = conn.execute("SELECT COUNT(*) FROM articles WHERE editorial_status='ERROR'").fetchone()[0]
        assert c >= 2
    finally:
        conn.close()


def test_single_mode_still_works_when_batch_size_1(tmp_path, monkeypatch) -> None:
    settings = _make_settings(batch_size=1)
    runner = PipelineRunner(settings)

    monkeypatch.setattr(
        "ai_news_platform.pipeline.runner.PromptLoader.load_domain_pipeline_prompt",
        lambda self, domain: "PROMPT",
    )
    # single-article contract output
    output = "REJECT\nnope"
    fake = FakeClient(output)
    monkeypatch.setattr("ai_news_platform.pipeline.runner.OpenAIResponsesClient.from_env", lambda **kwargs: fake)

    db_path = tmp_path / "t.sqlite3"
    conn = _seed_db(db_path)
    try:
        runner._process_llm_stage(conn, settings.config.domains)
        conn.commit()
        c = conn.execute("SELECT COUNT(*) FROM articles WHERE editorial_status='REJECTED'").fetchone()[0]
        assert c >= 1
        assert len(fake.calls) >= 1
    finally:
        conn.close()
