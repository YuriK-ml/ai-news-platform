from __future__ import annotations

from ai_news_platform.publishing.telegram.publisher import _append_source_original_for_ai


def test_ai_footer_inserted_before_hashtags() -> None:
    text = "<b>Заголовок</b>\n\nТекст.\n\n#ai\n#openai\n"
    out = _append_source_original_for_ai(
        text=text,
        source_name="OpenAI Blog",
        canonical_url="https://openai.com/index/x",
    )
    assert out.startswith("<b>Заголовок</b>\n\nТекст.")
    assert "Source: OpenAI Blog" in out
    assert "Original: https://openai.com/index/x" in out
    # Source/Original должны быть до хэштегов
    assert out.index("Source:") < out.index("#ai")
    assert out.index("Original:") < out.index("#ai")
    # Хэштеги должны быть в одной строке
    assert "#ai #openai" in out


def test_ai_footer_fallback_source_and_no_original() -> None:
    text = "<b>Title</b>\n\nBody\n"
    out = _append_source_original_for_ai(text=text, source_name="", canonical_url="")
    assert "Source: AI News" in out
    assert "Original:" not in out


def test_ai_footer_not_duplicated_if_already_present() -> None:
    text = "<b>Title</b>\n\nBody\n\nSource: OpenAI Blog\nOriginal: https://x\n\n#ai\n"
    out = _append_source_original_for_ai(text=text, source_name="OpenAI Blog", canonical_url="https://x")
    # Не добавляем второй Source/Original
    assert out.count("Source:") == 1
    assert out.count("Original:") == 1


def test_ai_footer_strips_embedded_source_from_body() -> None:
    text = "<b>Title</b>\n\nПодробнее в посте OpenAI. Source: OpenAI Blog\n\n#ai\n"
    out = _append_source_original_for_ai(
        text=text,
        source_name="OpenAI Blog",
        canonical_url="https://openai.com/index/x",
    )
    # В основном тексте не должно остаться "Подробнее..." и встроенного Source:
    assert "Подробнее" not in out
    assert "Source: OpenAI Blog" in out
    assert out.count("Source:") == 1


def test_ai_footer_moves_inline_hashtags_to_the_end() -> None:
    text = "<b>Title</b>\n\nТекст новости. #ai #openai #productivity\n"
    out = _append_source_original_for_ai(
        text=text,
        source_name="OpenAI Blog",
        canonical_url="https://openai.com/index/x",
    )
    # Внутри основного текста не должно остаться inline-хэштегов.
    assert "Текст новости. #ai" not in out
    # Хэштеги должны быть последней строкой и идти после Source/Original.
    assert out.rstrip().endswith("#ai #openai #productivity")
    assert out.index("Original:") < out.index("#ai")
