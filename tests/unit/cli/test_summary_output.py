from __future__ import annotations

from decimal import Decimal

from ai_news_platform.cli import _print_summary
from ai_news_platform.llm.usage_stats import LLMRunUsageStats
from ai_news_platform.pipeline.runner import IngestSummary


def test_print_summary_when_llm_not_executed(capsys) -> None:
    summary = IngestSummary(
        sources_processed=0,
        fetched_articles=0,
        inserted_articles=0,
        duplicate_articles=0,
        failed_sources=0,
        failed_source_ids=[],
        execution_time_seconds=0.1,
        published_articles=0,
        publication_failures=0,
        skipped_old_articles=0,
        llm_usage_stats=None,
    )
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "LLM stage not executed" in out


def test_print_summary_with_llm_usage_table(capsys) -> None:
    stats = LLMRunUsageStats(
        model="gpt-5.4-mini",
        attempted_request_count=2,
        successful_request_count=2,
        failed_request_count=0,
        attempted_article_count=40,
        article_count=40,
        completed_response_count=2,
        incomplete_response_count=0,
        input_tokens=2000,
        output_tokens=1000,
        total_tokens=3000,
        cached_tokens=200,
        reasoning_tokens=0,
        input_cost_usd=Decimal("0.002000"),
        output_cost_usd=Decimal("0.002000"),
    )
    summary = IngestSummary(
        sources_processed=0,
        fetched_articles=0,
        inserted_articles=0,
        duplicate_articles=0,
        failed_sources=0,
        failed_source_ids=[],
        execution_time_seconds=0.1,
        published_articles=0,
        publication_failures=0,
        skipped_old_articles=0,
        llm_usage_stats=stats,
    )
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "LLM Usage Summary" in out
    assert "Model                  : gpt-5.4-mini" in out
    assert "Total cost USD         : $0.004000" in out
    assert "Average cost/article   : $0.000100" in out


def test_print_summary_with_total_llm_failure(capsys) -> None:
    stats = LLMRunUsageStats(
        requested_model="gpt-5-mini",
        attempted_request_count=5,
        successful_request_count=0,
        failed_request_count=5,
        attempted_article_count=100,
        article_count=0,
    )
    summary = IngestSummary(
        sources_processed=0,
        fetched_articles=0,
        inserted_articles=0,
        duplicate_articles=0,
        failed_sources=0,
        failed_source_ids=[],
        execution_time_seconds=0.1,
        published_articles=0,
        publication_failures=0,
        skipped_old_articles=0,
        llm_usage_stats=stats,
    )
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "LLM Usage Summary" in out
    assert "Model                  : gpt-5-mini" in out
    assert "Requests attempted     : 5" in out
    assert "Failed requests        : 5" in out
    assert "Total cost USD         : unavailable" in out
