from __future__ import annotations

import structlog

from ai_news_platform.models.article import Article
from ai_news_platform.publishing.telegram.client import TelegramClient
from ai_news_platform.publishing.telegram.formatter import format_article_message


class TelegramPublisher:
    type = "telegram"

    def __init__(
        self,
        *,
        client: TelegramClient,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> None:
        self._client = client
        self._parse_mode = parse_mode
        self._disable_web_page_preview = disable_web_page_preview
        self._logger = structlog.get_logger(__name__)

    def publish(
        self,
        *,
        article: Article,
        channel_id: str,
        source_name: str,
        published_at: str | None,
    ) -> int:
        # When LLM is used, `article.title` should be the final title and `article.content` the final text.
        # For non-LLM paths, this falls back to the raw/normalized title+content.
        title = article.title
        body = article.content
        if title and body:
            text = f"{title}\n\n{body}"
        else:
            text = format_article_message(
                title=title,
                source_name=source_name,
                published_at=published_at,
                url=article.canonical_url,
                parse_mode=self._parse_mode,
            )
        self._logger.info(
            "telegram_publish_start",
            domain_id=article.domain_id,
            article_id=article.id,
            channel=channel_id,
        )
        result = self._client.send_message(
            chat_id=channel_id,
            text=text,
            parse_mode=self._parse_mode,
            disable_web_page_preview=self._disable_web_page_preview,
        )
        self._logger.info(
            "telegram_publish_success",
            domain_id=article.domain_id,
            article_id=article.id,
            channel=channel_id,
            telegram_message_id=result.message_id,
        )
        return result.message_id
