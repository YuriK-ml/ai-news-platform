from __future__ import annotations

import os
from dataclasses import dataclass
from time import perf_counter

import httpx
import structlog

from ai_news_platform.ingestion.registry import ConnectorRegistry
from ai_news_platform.ingestion.rss.connector import RssConnector
from ai_news_platform.llm.input_builder import build_article_input
from ai_news_platform.llm.openai_responses_client import OpenAIResponsesClient
from ai_news_platform.llm.output_parser import parse_llm_output, validate_telegram_html
from ai_news_platform.llm.prompt_loader import PromptLoader
from ai_news_platform.models.article import Article
from ai_news_platform.models.source import Source
from ai_news_platform.normalization.normalizer import normalize_item
from ai_news_platform.publishing.telegram.client import TelegramClient
from ai_news_platform.publishing.telegram.publisher import TelegramPublisher
from ai_news_platform.settings.models import DomainConfig, Settings, SourceConfig
from ai_news_platform.storage.db import Database, utc_now_iso
from ai_news_platform.storage.repositories.article_events import (
    ArticleEvent,
    ArticleEventsRepository,
)
from ai_news_platform.storage.repositories.articles import ArticleRepository, is_within_max_age
from ai_news_platform.storage.repositories.sources import FetchUpdate, SourceRepository


@dataclass(frozen=True)
class IngestSummary:
    sources_processed: int
    fetched_articles: int
    inserted_articles: int
    duplicate_articles: int
    failed_sources: int
    failed_source_ids: list[str]
    execution_time_seconds: float
    published_articles: int
    publication_failures: int
    skipped_old_articles: int


@dataclass(frozen=True)
class PublishStats:
    published_articles: int
    publication_failures: int
    skipped_old_articles: int


class PipelineRunner:
    """
    Runner:

    RSS -> Normalize -> SQLite -> Single LLM Stage -> READY/REJECTED -> Telegram -> PUBLISHED
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = structlog.get_logger(__name__)

        self.connectors = ConnectorRegistry()
        self.connectors.register(RssConnector())

        db_path = os.getenv(self.settings.config.storage.database_path_env, "./data/app.sqlite3")
        self.db = Database(path=db_path)
        self.sources_repo = SourceRepository()
        self.articles_repo = ArticleRepository()
        self.article_events_repo = ArticleEventsRepository()

    def ingest_rss(self, *, domain_ids: list[str] | None = None) -> IngestSummary:
        start = perf_counter()
        selected = self._select_domains(domain_ids)

        with self.db.connect() as conn:
            self.db.initialize_schema(conn)

            inserted = 0
            duplicates = 0
            fetched_articles = 0
            sources_processed = 0
            failed_source_ids: list[str] = []

            domain_llm_enabled = {
                d.id: bool(self.settings.config.llm.enabled and d.llm and d.llm.enabled) for d in selected
            }

            for domain in selected:
                domain_logger = self.logger.bind(domain_id=domain.id)
                domain_logger.info("domain_ingest_start", source_count=len(domain.sources))

                for source_cfg in domain.sources:
                    if source_cfg.type != "rss" or not source_cfg.enabled:
                        continue

                    source = _to_source(domain_id=domain.id, source_cfg=source_cfg)
                    sources_processed += 1
                    self.sources_repo.upsert(conn, source, enabled=source_cfg.enabled)

                    source_inserted = 0
                    source_duplicates = 0
                    source_fetched = 0
                    try:
                        connector = self.connectors.get(source.type)
                        items = connector.fetch(source)
                        source_fetched = len(items)
                        fetched_articles += source_fetched

                        self.sources_repo.record_fetch(
                            conn,
                            source.id,
                            FetchUpdate(
                                ok=True,
                                http_status=200,
                                last_item_external_id=items[0].external_id if items else None,
                            ),
                        )

                        for raw_item in items:
                            article = normalize_item(raw_item)
                            outcome = self.articles_repo.insert_normalized(
                                conn,
                                article=article,
                                source_item_external_id=raw_item.external_id,
                                raw_json=raw_item.payload if isinstance(raw_item.payload, dict) else None,
                            )
                            if outcome.inserted:
                                inserted += 1
                                source_inserted += 1
                                self.article_events_repo.append(
                                    conn, ArticleEvent(article_id=outcome.article_id, event_type="INGESTED")
                                )

                                # Avoid NEW accumulation when LLM is disabled for this domain.
                                if not domain_llm_enabled.get(domain.id, False):
                                    self.articles_repo.mark_rejected(
                                        conn, article_id=outcome.article_id, explanation="llm_disabled"
                                    )
                                    self.article_events_repo.append(
                                        conn,
                                        ArticleEvent(
                                            article_id=outcome.article_id,
                                            event_type="LLM_PROCESSED",
                                            details={"decision": "REJECT", "explanation": "llm_disabled"},
                                        ),
                                    )
                            else:
                                duplicates += 1
                                source_duplicates += 1

                        domain_logger.info(
                            "source_ingest_success",
                            source_id=source.id,
                            fetched_articles=source_fetched,
                            inserted_articles=source_inserted,
                            duplicate_articles=source_duplicates,
                        )

                    except Exception as exc:
                        domain_logger.exception("source_ingest_error", source_id=source.id, error=str(exc))
                        http_status = None
                        if isinstance(exc, httpx.HTTPStatusError):
                            http_status = exc.response.status_code
                        self.sources_repo.record_fetch(
                            conn,
                            source.id,
                            FetchUpdate(ok=False, http_status=http_status, error_message=str(exc)),
                        )
                        failed_source_ids.append(source.id)
                        domain_logger.warning(
                            "source_ingest_failed",
                            source_id=source.id,
                            fetched_articles=source_fetched,
                            inserted_articles=source_inserted,
                            duplicate_articles=source_duplicates,
                        )

                domain_logger.info("domain_ingest_end")

            self._process_llm_stage(conn, selected)
            publish_stats = self._publish_ready_articles(conn, selected)

            conn.commit()

        duration = perf_counter() - start
        self.logger.info(
            "ingest_complete",
            sources_processed=sources_processed,
            fetched_articles=fetched_articles,
            inserted_articles=inserted,
            duplicate_articles=duplicates,
            failed_sources=len(failed_source_ids),
            published_articles=publish_stats.published_articles,
            publication_failures=publish_stats.publication_failures,
            skipped_old_articles=publish_stats.skipped_old_articles,
            execution_time_seconds=round(duration, 3),
        )

        return IngestSummary(
            sources_processed=sources_processed,
            fetched_articles=fetched_articles,
            inserted_articles=inserted,
            duplicate_articles=duplicates,
            failed_sources=len(failed_source_ids),
            failed_source_ids=failed_source_ids,
            execution_time_seconds=duration,
            published_articles=publish_stats.published_articles,
            publication_failures=publish_stats.publication_failures,
            skipped_old_articles=publish_stats.skipped_old_articles,
        )

    def _process_llm_stage(self, conn, domains: list[DomainConfig]) -> None:
        if not self.settings.config.llm.enabled:
            return

        enabled_domains = [d for d in domains if d.llm and d.llm.enabled]
        if not enabled_domains:
            return

        client = OpenAIResponsesClient.from_env()
        prompt_loader = PromptLoader(self.settings)

        limit = int(self.settings.config.llm.max_articles_per_run)
        domain_ids = [d.id for d in enabled_domains]
        candidates = self.articles_repo.list_llm_candidates(conn, domain_ids=domain_ids, limit=limit)

        self.logger.info("llm_stage_start", candidate_count=len(candidates), max_articles_per_run=limit)
        domain_by_id = {d.id: d for d in enabled_domains}

        for row in candidates:
            article_id = row["id"]
            domain_id = row["domain_id"]
            domain = domain_by_id.get(domain_id)
            if not domain:
                continue

            try:
                prompt = prompt_loader.load_domain_pipeline_prompt(domain=domain)
                input_text = build_article_input(
                    source_name=row["source_name"],
                    canonical_url=row["canonical_url"],
                    published_at=row["published_at"],
                    title=row["title"],
                    content=row["content"],
                )
                output_text = client.create_text_response(instructions=prompt, input_text=input_text)
                parsed = parse_llm_output(output_text)

                if parsed.decision == "REJECT":
                    self.articles_repo.mark_rejected(
                        conn,
                        article_id=article_id,
                        explanation=parsed.rejection_explanation,
                    )
                    self.article_events_repo.append(
                        conn,
                        ArticleEvent(
                            article_id=article_id,
                            event_type="LLM_PROCESSED",
                            details={"decision": "REJECT", "explanation": parsed.rejection_explanation},
                        ),
                    )
                    continue

                validate_telegram_html(parsed.final_text or "")
                final_language = domain.llm.target_language or self.settings.config.llm.default_target_language
                self.articles_repo.mark_ready(
                    conn,
                    article_id=article_id,
                    final_language=final_language,
                    final_title=parsed.final_title or "-",
                    final_text=parsed.final_text or "-",
                )
                self.article_events_repo.append(
                    conn,
                    ArticleEvent(
                        article_id=article_id,
                        event_type="LLM_PROCESSED",
                        details={"decision": "READY_TO_PUBLISH", "final_language": final_language},
                    ),
                )
            except Exception as exc:
                self.articles_repo.mark_error(conn, article_id=article_id, stage="LLM", message=str(exc))
                self.article_events_repo.append(
                    conn, ArticleEvent(article_id=article_id, event_type="ERROR", error_message=str(exc))
                )
                self.logger.exception("llm_error", article_id=article_id, domain_id=domain_id, error=str(exc))

        self.logger.info("llm_stage_end", processed_count=len(candidates))

    def _publish_ready_articles(self, conn, domains: list[DomainConfig]) -> PublishStats:
        if self.settings.config.publishing.default_publisher != "telegram":
            return PublishStats(published_articles=0, publication_failures=0, skipped_old_articles=0)

        env_key = self.settings.config.publishing.telegram.bot_token_env
        token = os.getenv(env_key, "")
        if not token:
            raise ValueError(f"Missing Telegram bot token env var: {env_key}")

        publisher = TelegramPublisher(
            client=TelegramClient(bot_token=token),
            parse_mode=self.settings.config.publishing.telegram.parse_mode,
            disable_web_page_preview=self.settings.config.publishing.telegram.disable_web_page_preview,
        )

        max_per_run = int(self.settings.config.publishing.max_articles_per_run)
        max_age_hours = int(self.settings.config.ingestion.max_article_age_hours)

        channel_map = {d.id: d.telegram.channel_id for d in domains if d.telegram and d.telegram.channel_id}
        domain_ids = list(channel_map.keys())

        candidates = self.articles_repo.list_ready_for_publication(conn, domain_ids=domain_ids, limit=max_per_run * 5)

        published = 0
        failures = 0
        skipped_old = 0

        for row in candidates:
            if published >= max_per_run:
                self.logger.info("publish_limit_reached", max_articles_per_run=max_per_run)
                break

            domain_id = row["domain_id"]
            channel_id = channel_map.get(domain_id)
            if not channel_id:
                continue

            if not is_within_max_age(published_at=row["published_at"], max_age_hours=max_age_hours):
                self.articles_repo.mark_skipped_old(
                    conn,
                    article_id=row["id"],
                    reason=f"older_than_{max_age_hours}_hours_or_unparseable",
                )
                skipped_old += 1
                continue

            article = Article(
                id=row["id"],
                domain_id=row["domain_id"],
                source_id=row["source_id"],
                canonical_url=row["canonical_url"],
                title=row["final_title"],
                content=row["final_text"],
                metadata=None,
            )

            try:
                message_id = publisher.publish(
                    article=article,
                    channel_id=channel_id,
                    source_name=row["source_name"],
                    published_at=row["published_at"],
                )
                self.articles_repo.mark_published(
                    conn,
                    article_id=article.id,
                    telegram_message_id=message_id,
                    telegram_channel=channel_id,
                    telegram_published_at=utc_now_iso(),
                )
                self.article_events_repo.append(
                    conn,
                    ArticleEvent(
                        article_id=article.id,
                        event_type="PUBLISHED",
                        details={"telegram_message_id": message_id, "telegram_channel": channel_id},
                    ),
                )
                published += 1
            except Exception as exc:
                failures += 1
                self.articles_repo.mark_publish_failed(conn, article_id=article.id, error=str(exc))
                self.articles_repo.record_last_error(conn, article_id=article.id, stage="PUBLISH", message=str(exc))
                self.article_events_repo.append(
                    conn, ArticleEvent(article_id=article.id, event_type="ERROR", error_message=str(exc))
                )
                self.logger.exception("publish_failed", domain_id=domain_id, article_id=article.id, error=str(exc))

        return PublishStats(published_articles=published, publication_failures=failures, skipped_old_articles=skipped_old)

    def _select_domains(self, domain_ids: list[str] | None) -> list[DomainConfig]:
        if not domain_ids:
            return list(self.settings.config.domains)
        wanted = set(domain_ids)
        selected = [d for d in self.settings.config.domains if d.id in wanted]
        missing = wanted - {d.id for d in selected}
        if missing:
            raise ValueError(f"Unknown domain ids in config: {sorted(missing)}")
        return selected


def _to_source(*, domain_id: str, source_cfg: SourceConfig) -> Source:
    return Source(
        id=source_cfg.id,
        domain_id=domain_id,
        type=source_cfg.type,
        name=source_cfg.name,
        url=source_cfg.url,
        config=source_cfg.config or {},
    )
