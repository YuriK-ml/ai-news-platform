from __future__ import annotations

import os
from dataclasses import dataclass
from time import perf_counter
from time import sleep

import httpx
import structlog

from ai_news_platform.ingestion.registry import ConnectorRegistry
from ai_news_platform.ingestion.rss.connector import RssConnector
from ai_news_platform.llm.input_builder import build_article_input, build_batch_input
from ai_news_platform.llm.openai_responses_client import OpenAIResponsesClient
from ai_news_platform.llm.output_parser import (
    parse_batch_output,
    parse_llm_output,
    validate_telegram_html,
)
from ai_news_platform.llm.prompt_loader import PromptLoader
from ai_news_platform.llm.usage_stats import LLMRunUsageStats, format_usd, resolve_pricing_for_model, usd_to_cents
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
    llm_usage_stats: LLMRunUsageStats | None = None


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

            llm_usage_stats = self._process_llm_stage(conn, selected)
            publish_stats = PublishStats(published_articles=0, publication_failures=0, skipped_old_articles=0)

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
            llm_usage_stats=llm_usage_stats,
        )

    def publish_once(self, *, domain_ids: list[str] | None = None) -> bool:
        """
        Publish exactly one READY article (unpublished) and exit.
        """

        selected = self._select_domains(domain_ids)
        self.logger.info("publish_worker_start", mode="once", domain_count=len(selected))
        with self.db.connect() as conn:
            self.db.initialize_schema(conn)
            published = self._publish_one_ready_article(conn, selected)
            conn.commit()
        return published

    def publish_worker(self, *, domain_ids: list[str] | None = None) -> None:
        """
        Publisher worker: publish one READY article per iteration, then sleep.
        """

        selected = self._select_domains(domain_ids)
        interval = int(self.settings.config.publishing.interval_seconds)
        if interval < 1:
            interval = 1

        self.logger.info(
            "publish_worker_start",
            mode="worker",
            domain_count=len(selected),
            interval_seconds=interval,
        )

        while True:
            self.logger.info("publish_worker_iteration")
            with self.db.connect() as conn:
                self.db.initialize_schema(conn)
                self._publish_one_ready_article(conn, selected)
                conn.commit()

            self.logger.info("publish_worker_sleep", interval_seconds=interval)
            sleep(interval)

    def _process_llm_stage(self, conn, domains: list[DomainConfig]) -> LLMRunUsageStats | None:
        if not self.settings.config.llm.enabled:
            return None

        enabled_domains = [d for d in domains if d.llm and d.llm.enabled]
        if not enabled_domains:
            return None

        client = OpenAIResponsesClient.from_env(reasoning_effort=self.settings.config.llm.reasoning_effort)
        prompt_loader = PromptLoader(self.settings)

        max_total = int(self.settings.config.llm.max_articles_per_run)
        batch_size = int(self.settings.config.llm.batch_size)
        if batch_size < 1:
            batch_size = 1

        processed_total = 0
        usage_stats = LLMRunUsageStats()
        self.logger.info(
            "llm_stage_start",
            max_articles_per_run=max_total,
            batch_size=batch_size,
            domain_count=len(enabled_domains),
        )

        for domain in enabled_domains:
            remaining = max_total - processed_total
            if remaining <= 0:
                break

            prompt = prompt_loader.load_domain_pipeline_prompt(domain=domain)
            candidates = self.articles_repo.list_llm_candidates(conn, domain_ids=[domain.id], limit=remaining)
            if not candidates:
                continue

            self.logger.info(
                "llm_domain_candidates",
                domain_id=domain.id,
                candidate_count=len(candidates),
                remaining=remaining,
            )

            # batch_size=1 preserves current single-article mode.
            if batch_size == 1:
                for row in candidates:
                    _process_single_llm_article(
                        conn=conn,
                        logger=self.logger,
                        domain=domain,
                        prompt=prompt,
                        row=row,
                        client=client,
                        articles_repo=self.articles_repo,
                        events_repo=self.article_events_repo,
                        default_language=self.settings.config.llm.default_target_language,
                    )
                    processed_total += 1
                continue

            for batch_index, chunk in enumerate(_chunks(candidates, batch_size), start=1):
                expected_ids = {r["id"] for r in chunk}
                batch_articles = [
                    {
                        "article_id": r["id"],
                        "source_name": r["source_name"],
                        "canonical_url": r["canonical_url"],
                        "published_at": r["published_at"],
                        "title": r["title"],
                        "content": r["content"],
                    }
                    for r in chunk
                ]
                input_text = build_batch_input(articles=batch_articles)

                self.logger.info(
                    "llm_batch_request_start",
                    domain_id=domain.id,
                    batch_index=batch_index,
                    batch_size=len(chunk),
                )

                usage_stats.record_attempt(requested_model=client.requested_model, articles_in_request=len(chunk))
                try:
                    resp = client.create_text_response_full(instructions=prompt, input_text=input_text)
                except Exception as exc:
                    usage_stats.record_failure()
                    for r in chunk:
                        aid = r["id"]
                        self.articles_repo.mark_error(
                            conn, article_id=aid, stage="LLM_BATCH_REQUEST", message=str(exc)
                        )
                        self.article_events_repo.append(
                            conn, ArticleEvent(article_id=aid, event_type="ERROR", error_message=str(exc))
                        )
                    self.logger.exception(
                        "llm_batch_request_failed",
                        domain_id=domain.id,
                        batch_index=batch_index,
                        error=str(exc),
                    )
                    processed_total += len(chunk)
                    continue

                if resp.total_tokens == 0:
                    self.logger.warning(
                        "llm_batch_usage_missing",
                        domain_id=domain.id,
                        batch_index=batch_index,
                        model=resp.model,
                        status=resp.status,
                        incomplete_reason=resp.incomplete_reason,
                    )

                resolved_pricing = resolve_pricing_for_model(
                    model=resp.model, pricing_models=self.settings.config.llm.pricing.models
                )
                if resolved_pricing is None:
                    self.logger.warning(
                        "llm_model_pricing_missing",
                        model=resp.model,
                        configured_models=sorted(self.settings.config.llm.pricing.models.keys()),
                    )

                costs = usage_stats.add_response(
                    response=resp,
                    articles_in_request=len(chunk),
                    pricing_models=self.settings.config.llm.pricing.models,
                )
                self.logger.info(
                    "llm_batch_usage",
                    domain_id=domain.id,
                    batch_index=batch_index,
                    batch_size=len(chunk),
                    model=resp.model,
                    status=resp.status,
                    incomplete_reason=resp.incomplete_reason,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    total_tokens=resp.total_tokens,
                    cached_tokens=resp.cached_tokens,
                    reasoning_tokens=resp.reasoning_tokens,
                    input_cost_usd=format_usd(costs["input_cost_usd"]),
                    output_cost_usd=format_usd(costs["output_cost_usd"]),
                    total_cost_usd=format_usd(costs["total_cost_usd"]),
                )

                try:
                    parsed_batch = parse_batch_output(resp.output_text, expected_article_ids=expected_ids)
                except Exception as exc:
                    for r in chunk:
                        aid = r["id"]
                        self.articles_repo.mark_error(
                            conn, article_id=aid, stage="LLM_BATCH_PARSE", message=str(exc)
                        )
                        self.article_events_repo.append(
                            conn, ArticleEvent(article_id=aid, event_type="ERROR", error_message=str(exc))
                        )
                    self.logger.exception(
                        "llm_batch_parse_failed",
                        domain_id=domain.id,
                        batch_index=batch_index,
                        error=str(exc),
                    )
                    processed_total += len(chunk)
                    continue

                if parsed_batch.unknown_article_ids:
                    self.logger.warning(
                        "llm_batch_unknown_article_ids",
                        domain_id=domain.id,
                        batch_index=batch_index,
                        unknown_article_ids=parsed_batch.unknown_article_ids,
                    )

                duplicate_ids = set(parsed_batch.duplicate_article_ids)
                for dup_id in duplicate_ids:
                    self.articles_repo.mark_error(
                        conn, article_id=dup_id, stage="LLM_BATCH_PARSE", message="duplicate_article_id_in_response"
                    )
                    self.article_events_repo.append(
                        conn,
                        ArticleEvent(
                            article_id=dup_id,
                            event_type="ERROR",
                            error_message="duplicate_article_id_in_response",
                        ),
                    )

                ok = 0
                err = 0
                missing = 0

                for r in chunk:
                    aid = r["id"]
                    if aid in duplicate_ids:
                        err += 1
                        continue

                    parse_error = parsed_batch.errors_by_article_id.get(aid)
                    if parse_error is not None:
                        err += 1
                        self.articles_repo.mark_error(conn, article_id=aid, stage="LLM_BATCH_PARSE", message=parse_error)
                        self.article_events_repo.append(
                            conn,
                            ArticleEvent(
                                article_id=aid,
                                event_type="ERROR",
                                error_message=parse_error,
                            ),
                        )
                        continue

                    result = parsed_batch.results_by_article_id.get(aid)
                    if result is None:
                        missing += 1
                        self.articles_repo.mark_error(
                            conn, article_id=aid, stage="LLM_BATCH_PARSE", message="missing_result_for_article"
                        )
                        self.article_events_repo.append(
                            conn,
                            ArticleEvent(
                                article_id=aid,
                                event_type="ERROR",
                                error_message="missing_result_for_article",
                            ),
                        )
                        continue

                    try:
                        if result.decision == "REJECT":
                            self.articles_repo.mark_rejected(conn, article_id=aid, explanation=result.rejection_explanation)
                            self.article_events_repo.append(
                                conn,
                                ArticleEvent(
                                    article_id=aid,
                                    event_type="LLM_PROCESSED",
                                    details={"decision": "REJECT", "explanation": result.rejection_explanation},
                                ),
                            )
                            ok += 1
                        else:
                            validate_telegram_html(result.final_text or "")
                            final_language = domain.llm.target_language or self.settings.config.llm.default_target_language
                            self.articles_repo.mark_ready(
                                conn,
                                article_id=aid,
                                final_language=final_language,
                                final_title=result.final_title or "-",
                                final_text=result.final_text or "-",
                            )
                            self.article_events_repo.append(
                                conn,
                                ArticleEvent(
                                    article_id=aid,
                                    event_type="LLM_PROCESSED",
                                    details={"decision": "READY_TO_PUBLISH", "final_language": final_language},
                                ),
                            )
                            ok += 1
                    except Exception as exc:
                        err += 1
                        self.articles_repo.mark_error(conn, article_id=aid, stage="LLM_BATCH_PARSE", message=str(exc))
                        self.article_events_repo.append(
                            conn, ArticleEvent(article_id=aid, event_type="ERROR", error_message=str(exc))
                        )

                self.logger.info(
                    "llm_batch_parse_summary",
                    domain_id=domain.id,
                    batch_index=batch_index,
                    ok_count=ok,
                    error_count=err + len(duplicate_ids),
                    missing_count=missing,
                    unknown_id_count=len(parsed_batch.unknown_article_ids),
                    duplicate_id_count=len(duplicate_ids),
                )

                processed_total += len(chunk)

        if usage_stats.attempted_request_count > 0:
            total_cost_usd = usage_stats.total_cost_usd
            self.logger.info(
                "llm_stage_usage_summary",
                model=usage_stats.effective_model(),
                request_count=usage_stats.successful_request_count,
                article_count=usage_stats.article_count,
                attempted_request_count=usage_stats.attempted_request_count,
                successful_request_count=usage_stats.successful_request_count,
                failed_request_count=usage_stats.failed_request_count,
                attempted_article_count=usage_stats.attempted_article_count,
                completed_response_count=usage_stats.completed_response_count,
                incomplete_response_count=usage_stats.incomplete_response_count,
                input_tokens=usage_stats.input_tokens,
                output_tokens=usage_stats.output_tokens,
                total_tokens=usage_stats.total_tokens,
                cached_tokens=usage_stats.cached_tokens,
                reasoning_tokens=usage_stats.reasoning_tokens,
                input_cost_usd=format_usd(usage_stats.input_cost_usd),
                output_cost_usd=format_usd(usage_stats.output_cost_usd),
                total_cost_usd=format_usd(total_cost_usd),
                total_cost_cents=usd_to_cents(total_cost_usd),
                average_cost_per_article_usd=format_usd(usage_stats.average_cost_per_article_usd()),
            )

        self.logger.info("llm_stage_end", processed_count=processed_total)
        return usage_stats

    def _publish_one_ready_article(self, conn, domains: list[DomainConfig]) -> bool:
        if self.settings.config.publishing.default_publisher != "telegram":
            return False

        env_key = self.settings.config.publishing.telegram.bot_token_env
        token = os.getenv(env_key, "")
        if not token:
            raise ValueError(f"Missing Telegram bot token env var: {env_key}")

        publisher = TelegramPublisher(
            client=TelegramClient(bot_token=token),
            parse_mode=self.settings.config.publishing.telegram.parse_mode,
            disable_web_page_preview=self.settings.config.publishing.telegram.disable_web_page_preview,
        )

        channel_map = {d.id: d.telegram.channel_id for d in domains if d.telegram and d.telegram.channel_id}
        domain_ids = list(channel_map.keys())
        if not domain_ids:
            return False

        row = self.articles_repo.get_next_ready_unpublished_for_publication(conn, domain_ids=domain_ids)
        if row is None:
            return False

        domain_id = row["domain_id"]
        channel_id = channel_map.get(domain_id)
        if not channel_id:
            return False

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
            return True
        except Exception as exc:
            self.articles_repo.mark_error(conn, article_id=article.id, stage="PUBLISH", message=str(exc))
            self.articles_repo.mark_publish_failed(conn, article_id=article.id, error=str(exc))
            self.article_events_repo.append(
                conn, ArticleEvent(article_id=article.id, event_type="ERROR", error_message=str(exc))
            )
            self.logger.exception("publish_failed", domain_id=domain_id, article_id=article.id, error=str(exc))
            return False

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


def _chunks(items, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _process_single_llm_article(
    *,
    conn,
    logger,
    domain: DomainConfig,
    prompt: str,
    row,
    client: OpenAIResponsesClient,
    articles_repo: ArticleRepository,
    events_repo: ArticleEventsRepository,
    default_language: str,
) -> None:
    """
    Single-article LLM mode (used only when batch_size=1).
    """

    article_id = row["id"]
    try:
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
            articles_repo.mark_rejected(conn, article_id=article_id, explanation=parsed.rejection_explanation)
            events_repo.append(
                conn,
                ArticleEvent(
                    article_id=article_id,
                    event_type="LLM_PROCESSED",
                    details={"decision": "REJECT", "explanation": parsed.rejection_explanation},
                ),
            )
            return

        validate_telegram_html(parsed.final_text or "")
        final_language = (
            domain.llm.target_language if domain.llm and domain.llm.target_language else default_language
        )
        articles_repo.mark_ready(
            conn,
            article_id=article_id,
            final_language=final_language,
            final_title=parsed.final_title or "-",
            final_text=parsed.final_text or "-",
        )
        events_repo.append(
            conn,
            ArticleEvent(
                article_id=article_id,
                event_type="LLM_PROCESSED",
                details={"decision": "READY_TO_PUBLISH", "final_language": final_language},
            ),
        )
    except Exception as exc:
        articles_repo.mark_error(conn, article_id=article_id, stage="LLM", message=str(exc))
        events_repo.append(conn, ArticleEvent(article_id=article_id, event_type="ERROR", error_message=str(exc)))
        logger.exception("llm_error", article_id=article_id, domain_id=row["domain_id"], error=str(exc))
