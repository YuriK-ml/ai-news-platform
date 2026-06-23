from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    id: str
    type: Literal["rss", "website"]
    name: str
    url: str | None = None
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class TelegramDomainConfig(BaseModel):
    channel_id: str


class DomainConfig(BaseModel):
    id: str
    name: str
    telegram: TelegramDomainConfig | None = None
    llm: "DomainLLMConfig | None" = None
    sources: list[SourceConfig] = Field(default_factory=list)


class LoggingConfig(BaseModel):
    format: Literal["json"] = "json"
    level: str = "INFO"


class AppConfig(BaseModel):
    service_name: str = "ai-news-platform"


class StorageConfig(BaseModel):
    database_path_env: str = "DATABASE_PATH"
    database_path_default: str = "./data/app.sqlite3"


class IngestionConfig(BaseModel):
    max_article_age_hours: int = 24


class TelegramPublishingConfig(BaseModel):
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    parse_mode: Literal["HTML", "MarkdownV2", "Markdown", "plain"] = "HTML"
    disable_web_page_preview: bool = False


class PublishingConfig(BaseModel):
    max_articles_per_run: int = 2
    interval_seconds: int = 60
    default_publisher: Literal["telegram"] = "telegram"
    telegram: TelegramPublishingConfig = Field(default_factory=TelegramPublishingConfig)


class LLMConfig(BaseModel):
    """
    Global LLM configuration (no provider integration in current scope).

    Prompts are stored as text files in the repository.
    """

    enabled: bool = True
    prompts_dir: str = "prompts"
    default_target_language: str = "ru"
    max_articles_per_run: int = 100
    batch_size: int = 20
    reasoning_effort: Literal["minimal", "low", "medium", "high"] = "minimal"
    pricing: "LLMPricingConfig" = Field(default_factory=lambda: LLMPricingConfig())


class LLMModelPricing(BaseModel):
    input_price_per_million_usd: Decimal = Decimal("0")
    output_price_per_million_usd: Decimal = Decimal("0")


class LLMPricingConfig(BaseModel):
    models: dict[str, LLMModelPricing] = Field(default_factory=dict)


class DomainLLMConfig(BaseModel):
    """
    Per-domain LLM configuration.

    A single prompt is used for the whole domain editorial pipeline.
    """

    enabled: bool = True
    target_language: str | None = None
    pipeline_prompt: str


class RootConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    domains: list[DomainConfig] = Field(default_factory=list)


DomainConfig.model_rebuild()


@dataclass(frozen=True)
class Settings:
    """
    Top-level settings container.

    Loaded from `.env` + a config file (e.g., YAML).
    """

    raw: dict[str, Any]
    config: RootConfig
