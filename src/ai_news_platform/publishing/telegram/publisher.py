from __future__ import annotations

import structlog
import re

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

        # Для домена AI добавляем единый хвост поста: источник + оригинальная ссылка.
        # Это не меняет ingestion/LLM/pipeline и не трогает football-домен.
        if article.domain_id == "ai":
            text = _append_source_original_for_ai(
                text=text,
                source_name=source_name,
                canonical_url=article.canonical_url,
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


def _append_source_original_for_ai(*, text: str, source_name: str | None, canonical_url: str | None) -> str:
    """
    Добавить в конец AI-поста:

    Source: <source name>
    Original: <url>

    При этом сохраняем структуру:
    заголовок -> основной текст -> Source/Original -> хэштеги (если они есть).
    """

    raw = (text or "").strip()
    if not raw:
        return raw

    lines = raw.splitlines()

    # Выделяем хвостовые хэштеги: подряд идущие строки в конце, начинающиеся с "#".
    hashtags: list[str] = []
    i = len(lines) - 1
    while i >= 0:
        s = lines[i].strip()
        if s.startswith("#") and s:
            hashtags.append(lines[i])
            i -= 1
            continue
        break
    hashtags.reverse()
    main = _sanitize_ai_main_lines(lines[: i + 1])

    # Если хэштеги написаны в конце последней строки (встроены в текст) — вынесем их в отдельный хвост.
    inline_tags, main = _extract_inline_hashtags_from_last_line(main)
    if inline_tags:
        hashtags.extend(inline_tags)

    hashtags_line = _normalize_hashtags_to_single_line(hashtags)

    has_source = any(l.strip().startswith("Source:") for l in main)
    has_original = any(l.strip().startswith("Original:") for l in main)

    # Нормализуем имя источника.
    src = (source_name or "").strip() or "AI News"
    url = (canonical_url or "").strip() or None

    # Добавляем пустую строку перед Source/Original, если основной текст не заканчивается пустой строкой.
    if main and main[-1].strip():
        main.append("")

    if not has_source:
        main.append(f"Source: {src}")
    if url and not has_original:
        main.append(f"Original: {url}")

    # Между Source/Original и хэштегами оставляем одну пустую строку.
    if hashtags_line:
        if main and main[-1].strip():
            main.append("")
        return "\n".join(main + [hashtags_line]).strip() + "\n"

    return "\n".join(main).strip() + "\n"


def _sanitize_ai_main_lines(lines: list[str]) -> list[str]:
    """
    Минимальная пост-обработка для AI-поста:

    - удаляем любые вкрапления "Source:" / "Original:" из основного текста (чтобы не было дублей),
      в т.ч. когда они встречаются в конце строки;
    - удаляем маркетинговые хвосты вида "Подробнее..." / "Читайте..." и т.п.
    """

    out: list[str] = []
    for line in lines:
        s = line.rstrip()
        low = s.lower()

        # Убираем рекламные/служебные фразы, которые часто добавляет модель.
        if "подробнее" in low or low.startswith("читайте") or low.startswith("подроб"):
            continue

        # Если в строке встречаются служебные поля — обрезаем с этого места.
        for marker in ("Source:", "Original:"):
            idx = s.find(marker)
            if idx != -1:
                s = s[:idx].rstrip()

        if s.strip():
            out.append(s)
        else:
            # сохраняем пустые строки как разделители (если не подряд)
            if out and out[-1].strip():
                out.append("")

    # Убираем лишние пустые строки в конце.
    while out and not out[-1].strip():
        out.pop()
    return out


_HASHTAG_RE = re.compile(r"#([a-z][a-z0-9_]{0,63})")


def _extract_inline_hashtags_from_last_line(lines: list[str]) -> tuple[list[str], list[str]]:
    """
    Если в конце последней строки присутствуют хэштеги вида "#tag #tag2",
    вынести их в хвост (отдельной строкой в самом конце поста).

    Возвращает: (hashtags, updated_lines)
    """

    if not lines:
        return [], lines

    last = lines[-1]

    # Ищем последовательность хэштегов в конце строки.
    # Пример: "Текст новости. #ai #openai #productivity"
    m = re.search(r"(?:\s+#[a-z][a-z0-9_]{0,63})+\s*$", last)
    if not m:
        return [], lines

    tail = last[m.start() :].strip()
    tags = [t for t in tail.split() if t.startswith("#")]
    if not tags:
        return [], lines

    new_last = last[: m.start()].rstrip()
    new_lines = lines[:-1]
    if new_last:
        new_lines.append(new_last)
    return tags, new_lines


def _normalize_hashtags_to_single_line(hashtags: list[str]) -> str | None:
    """
    Привести набор хэштегов к одной строке: "#a #b #c".
    """

    tags: list[str] = []
    for line in hashtags:
        for t in _HASHTAG_RE.findall(line or ""):
            tags.append(f"#{t}")

    # dedupe preserving order
    seen = set()
    unique: list[str] = []
    for t in tags:
        if t in seen:
            continue
        seen.add(t)
        unique.append(t)

    if not unique:
        return None
    return " ".join(unique)
