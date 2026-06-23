from __future__ import annotations

import pytest

from ai_news_platform.llm.input_builder import build_batch_input
from ai_news_platform.llm.output_parser import parse_batch_output


def test_build_batch_input_includes_article_id_and_markers() -> None:
    text = build_batch_input(
        articles=[
            {
                "article_id": "a1",
                "source_name": "BBC",
                "canonical_url": "https://example.com/1",
                "published_at": "2026-01-01",
                "title": "T",
                "content": "C",
            }
        ]
    )
    assert "### ARTICLE" in text
    assert "Article-ID: a1" in text
    assert "### END ARTICLE" in text


def test_parse_batch_output_happy_path() -> None:
    output = """
### RESULT

Article-ID: a1
Status: REJECT

Reason:
not football

### END RESULT

### RESULT

Article-ID: a2
Status: READY_TO_PUBLISH

Title:
<b>Hello</b>

Text:
Body

### END RESULT
""".strip()

    parsed = parse_batch_output(output, expected_article_ids={"a1", "a2"})
    assert parsed.duplicate_article_ids == []
    assert parsed.unknown_article_ids == []
    assert parsed.results_by_article_id["a1"].decision == "REJECT"
    assert parsed.results_by_article_id["a2"].decision == "READY_TO_PUBLISH"
    assert parsed.results_by_article_id["a2"].final_title == "<b>Hello</b>"


def test_parse_batch_output_missing_blocks_is_error() -> None:
    with pytest.raises(ValueError):
        parse_batch_output("READY_TO_PUBLISH", expected_article_ids={"a1"})


def test_parse_batch_output_unknown_article_id_reported() -> None:
    output = """
### RESULT
Article-ID: x999
Status: REJECT
### END RESULT
""".strip()
    parsed = parse_batch_output(output, expected_article_ids={"a1"})
    assert parsed.unknown_article_ids == ["x999"]


def test_parse_batch_output_duplicate_article_id_detected() -> None:
    output = """
### RESULT
Article-ID: a1
Status: REJECT
### END RESULT

### RESULT
Article-ID: a1
Status: REJECT
### END RESULT
""".strip()
    parsed = parse_batch_output(output, expected_article_ids={"a1"})
    assert parsed.duplicate_article_ids == ["a1"]

