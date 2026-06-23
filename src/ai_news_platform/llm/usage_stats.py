from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from ai_news_platform.llm.openai_responses_client import OpenAITextResponse
from ai_news_platform.settings.models import LLMModelPricing


_ONE_MILLION = Decimal("1000000")


def calculate_cost_usd(*, tokens: int, price_per_million_usd: Decimal) -> Decimal:
    if tokens <= 0 or price_per_million_usd <= 0:
        return Decimal("0")
    return (Decimal(tokens) / _ONE_MILLION) * price_per_million_usd


def format_usd(value: Decimal) -> str:
    """
    Format USD for logs with >= 6 decimal places.
    """

    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def usd_to_cents(value: Decimal) -> int:
    return int((value * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def resolve_pricing_for_model(
    *, model: str, pricing_models: dict[str, LLMModelPricing]
) -> tuple[str, LLMModelPricing] | None:
    """
    Map versioned model ids (e.g. gpt-5.2-2025-12-11) to a base key (gpt-5.2).
    """

    if not model or not pricing_models:
        return None

    keys = sorted(pricing_models.keys(), key=len, reverse=True)
    for key in keys:
        if model == key or model.startswith(f"{key}-"):
            return key, pricing_models[key]
    return None


@dataclass
class LLMRunUsageStats:
    requested_model: str | None = None
    model: str | None = None

    attempted_request_count: int = 0
    successful_request_count: int = 0
    failed_request_count: int = 0

    attempted_article_count: int = 0
    article_count: int = 0
    completed_response_count: int = 0
    incomplete_response_count: int = 0

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    input_cost_usd: Decimal = Decimal("0")
    output_cost_usd: Decimal = Decimal("0")

    def record_attempt(self, *, requested_model: str, articles_in_request: int) -> None:
        self.attempted_request_count += 1
        self.attempted_article_count += int(articles_in_request)
        if self.requested_model is None:
            self.requested_model = requested_model
        elif self.requested_model != requested_model:
            self.requested_model = "multiple"

    def record_failure(self) -> None:
        self.failed_request_count += 1

    def add_response(
        self,
        *,
        response: OpenAITextResponse,
        articles_in_request: int,
        pricing_models: dict[str, LLMModelPricing],
    ) -> dict[str, Decimal]:
        self.successful_request_count += 1
        self.article_count += int(articles_in_request)

        if self.model is None:
            self.model = response.model
        elif self.model != response.model:
            self.model = "multiple"

        if response.status == "completed":
            self.completed_response_count += 1
        elif response.status == "incomplete":
            self.incomplete_response_count += 1

        self.input_tokens += int(response.input_tokens)
        self.output_tokens += int(response.output_tokens)
        self.total_tokens += int(response.total_tokens)
        self.cached_tokens += int(response.cached_tokens)
        self.reasoning_tokens += int(response.reasoning_tokens)

        resolved = resolve_pricing_for_model(model=response.model, pricing_models=pricing_models)
        if resolved is None:
            input_cost = Decimal("0")
            output_cost = Decimal("0")
        else:
            _, pricing = resolved
            input_cost = calculate_cost_usd(
                tokens=response.input_tokens,
                price_per_million_usd=pricing.input_price_per_million_usd,
            )
            output_cost = calculate_cost_usd(
                tokens=response.output_tokens,
                price_per_million_usd=pricing.output_price_per_million_usd,
            )

        self.input_cost_usd += input_cost
        self.output_cost_usd += output_cost

        return {
            "input_cost_usd": input_cost,
            "output_cost_usd": output_cost,
            "total_cost_usd": input_cost + output_cost,
        }

    def effective_model(self) -> str | None:
        return self.model or self.requested_model

    @property
    def total_cost_usd(self) -> Decimal:
        return self.input_cost_usd + self.output_cost_usd

    def average_cost_per_article_usd(self) -> Decimal:
        if self.article_count <= 0:
            return Decimal("0")
        return self.total_cost_usd / Decimal(self.article_count)
