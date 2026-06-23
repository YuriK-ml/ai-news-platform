from __future__ import annotations

from decimal import Decimal

from ai_news_platform.llm.openai_responses_client import OpenAITextResponse
from ai_news_platform.llm.usage_stats import (
    LLMRunUsageStats,
    calculate_cost_usd,
    format_usd,
    resolve_pricing_for_model,
    usd_to_cents,
)
from ai_news_platform.settings.models import LLMModelPricing


def test_calculate_cost_usd_uses_decimal() -> None:
    cost = calculate_cost_usd(tokens=2500, price_per_million_usd=Decimal("2.0"))
    assert format_usd(cost) == "0.005000"
    assert usd_to_cents(cost) == 1  # $0.005 -> 1 cent (rounded)


def test_usage_stats_sums_multiple_batches_and_costs() -> None:
    stats = LLMRunUsageStats()
    pricing = {
        "gpt-5.2": LLMModelPricing(input_price_per_million_usd=Decimal("1.0"), output_price_per_million_usd=Decimal("2.0"))
    }

    r1 = OpenAITextResponse(
        output_text="x",
        model="gpt-5.2",
        status="completed",
        incomplete_reason=None,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        cached_tokens=10,
        reasoning_tokens=3,
    )
    r2 = OpenAITextResponse(
        output_text="y",
        model="gpt-5.2",
        status="incomplete",
        incomplete_reason="max_output_tokens",
        input_tokens=200,
        output_tokens=80,
        total_tokens=280,
        cached_tokens=0,
        reasoning_tokens=0,
    )

    stats.add_response(
        response=r1,
        articles_in_request=20,
        pricing_models=pricing,
    )
    stats.add_response(
        response=r2,
        articles_in_request=20,
        pricing_models=pricing,
    )

    assert stats.successful_request_count == 2
    assert stats.article_count == 40
    assert stats.completed_response_count == 1
    assert stats.incomplete_response_count == 1
    assert stats.input_tokens == 300
    assert stats.output_tokens == 130
    assert stats.total_tokens == 430
    assert stats.cached_tokens == 10
    assert stats.reasoning_tokens == 3

    total_cost = stats.total_cost_usd
    assert format_usd(total_cost) == "0.000560"
    assert format_usd(stats.average_cost_per_article_usd()) == "0.000014"


def test_resolve_pricing_for_versioned_models() -> None:
    pricing = {
        "gpt-5.2": LLMModelPricing(input_price_per_million_usd=Decimal("1.0"), output_price_per_million_usd=Decimal("2.0")),
        "gpt-5.4-mini": LLMModelPricing(input_price_per_million_usd=Decimal("0.5"), output_price_per_million_usd=Decimal("1.0")),
        "gpt-5-mini": LLMModelPricing(input_price_per_million_usd=Decimal("0.25"), output_price_per_million_usd=Decimal("2.0")),
    }
    k1 = resolve_pricing_for_model(model="gpt-5.2-2025-12-11", pricing_models=pricing)
    assert k1 is not None and k1[0] == "gpt-5.2"

    k2 = resolve_pricing_for_model(model="gpt-5.4-mini-2026-01-01", pricing_models=pricing)
    assert k2 is not None and k2[0] == "gpt-5.4-mini"

    k3 = resolve_pricing_for_model(model="gpt-5-mini", pricing_models=pricing)
    assert k3 is not None and k3[0] == "gpt-5-mini"

    k4 = resolve_pricing_for_model(model="gpt-5-mini-2026-06-01", pricing_models=pricing)
    assert k4 is not None and k4[0] == "gpt-5-mini"


def test_resolve_pricing_missing() -> None:
    pricing = {"gpt-5.2": LLMModelPricing(input_price_per_million_usd=Decimal("1.0"), output_price_per_million_usd=Decimal("2.0"))}
    assert resolve_pricing_for_model(model="unknown-model-1", pricing_models=pricing) is None
