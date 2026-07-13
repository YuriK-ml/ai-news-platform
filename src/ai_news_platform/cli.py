from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import structlog

from ai_news_platform.logging.setup import configure_logging
from ai_news_platform.pipeline.runner import IngestSummary, PipelineRunner
from ai_news_platform.settings.loader import load_settings
from ai_news_platform.llm.usage_stats import format_usd, usd_to_cents


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai-news-platform")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Fetch RSS feeds and store normalized articles in SQLite")
    ingest.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.example.yaml"),
        help="Path to YAML configuration file",
    )
    ingest.add_argument(
        "--domain",
        action="append",
        dest="domains",
        help="Domain id(s) to ingest (repeatable). If omitted, ingests all configured domains.",
    )

    publish_worker = sub.add_parser("publish-worker", help="Publish READY articles to Telegram in a worker loop")
    publish_worker.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.example.yaml"),
        help="Path to YAML configuration file",
    )
    publish_worker.add_argument(
        "--domain",
        action="append",
        dest="domains",
        required=True,
        help="Domain id(s) to publish (repeatable).",
    )

    publish_once = sub.add_parser("publish-once", help="Publish exactly one READY article and exit")
    publish_once.add_argument(
        "--config",
        type=Path,
        default=Path("config/settings.example.yaml"),
        help="Path to YAML configuration file",
    )
    publish_once.add_argument(
        "--domain",
        action="append",
        dest="domains",
        required=True,
        help="Domain id(s) to publish (repeatable).",
    )

    args = parser.parse_args()

    settings = load_settings(args.config)
    configure_logging(level=settings.config.logging.level, service_name=settings.config.app.service_name)
    logger = structlog.get_logger(__name__)

    if args.command == "ingest":
        runner = PipelineRunner(settings)
        summary = runner.ingest_rss(domain_ids=args.domains)
        logger.info(
            "cli_done",
            sources_processed=summary.sources_processed,
            fetched_articles=summary.fetched_articles,
            inserted_articles=summary.inserted_articles,
            duplicate_articles=summary.duplicate_articles,
            failed_sources=summary.failed_sources,
            published_articles=summary.published_articles,
            publication_failures=summary.publication_failures,
            skipped_old_articles=summary.skipped_old_articles,
            execution_time_seconds=round(summary.execution_time_seconds, 3),
        )
        _print_summary(summary)
        return

    if args.command == "publish-once":
        runner = PipelineRunner(settings)
        runner.publish_once(domain_ids=args.domains)
        return

    if args.command == "publish-worker":
        runner = PipelineRunner(settings)
        runner.publish_worker(domain_ids=args.domains)
        return

    raise SystemExit(2)


def _print_summary(summary: IngestSummary) -> None:
    width = shutil.get_terminal_size(fallback=(88, 24)).columns
    title = "Ingestion Summary"
    line = "=" * min(width, 88)

    failed_ids = ", ".join(summary.failed_source_ids) if summary.failed_source_ids else "-"
    duration = f"{summary.execution_time_seconds:.2f}s"

    print(line)
    print(title)
    print(line)
    print(f"Execution time    : {duration}")
    print(f"Sources processed : {summary.sources_processed}")
    print(f"Failed sources    : {summary.failed_sources}")
    print(f"Failed source ids : {failed_ids}")
    print(f"Fetched articles  : {summary.fetched_articles}")
    print(f"Inserted articles : {summary.inserted_articles}")
    print(f"Duplicate articles: {summary.duplicate_articles}")
    print(f"Skipped old       : {summary.skipped_old_articles}")
    print(f"Published         : {summary.published_articles}")
    print(f"Publish failures  : {summary.publication_failures}")
    print(line)

    s = summary.llm_usage_stats
    if not s or s.attempted_request_count == 0:
        print("LLM stage not executed")
        return

    if s.successful_request_count == 0 and s.failed_request_count > 0:
        print(line)
        print("LLM Usage Summary")
        print(line)
        print(f"Model                  : {s.effective_model()}")
        print(f"Requests attempted     : {s.attempted_request_count}")
        print(f"Successful requests    : {s.successful_request_count}")
        print(f"Failed requests        : {s.failed_request_count}")
        print(f"Articles attempted     : {s.attempted_article_count}")
        print(f"Completed responses    : {s.completed_response_count}")
        print(f"Incomplete responses   : {s.incomplete_response_count}")
        print("Input tokens           : unavailable")
        print("Output tokens          : unavailable")
        print("Total cost USD         : unavailable")
        return

    total_cost_usd = s.total_cost_usd
    print(line)
    print("LLM Usage Summary")
    print(line)
    print(f"Model                  : {s.effective_model()}")
    print(f"Requests attempted     : {s.attempted_request_count}")
    print(f"Successful requests    : {s.successful_request_count}")
    print(f"Failed requests        : {s.failed_request_count}")
    print(f"Articles attempted     : {s.attempted_article_count}")
    print(f"Articles processed     : {s.article_count}")
    print(f"Input tokens           : {s.input_tokens}")
    print(f"Output tokens          : {s.output_tokens}")
    print(f"Total tokens           : {s.total_tokens}")
    print(f"Completed responses    : {s.completed_response_count}")
    print(f"Incomplete responses   : {s.incomplete_response_count}")
    print(f"Total cost USD         : ${format_usd(total_cost_usd)}")
    print(f"Total cost cents       : {usd_to_cents(total_cost_usd)}")
    print(f"Average cost/article   : ${format_usd(s.average_cost_per_article_usd())}")


if __name__ == "__main__":
    main()
