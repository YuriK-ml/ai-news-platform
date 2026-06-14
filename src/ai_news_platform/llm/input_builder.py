from __future__ import annotations


def build_article_input(
    *,
    source_name: str,
    canonical_url: str,
    published_at: str | None,
    title: str | None,
    content: str | None,
) -> str:
    """
    Approved LLM input contract (non-JSON).
    """

    return (
        "### ARTICLE\n\n"
        f"Source: {source_name}\n"
        f"URL: {canonical_url}\n"
        f"Published: {published_at or '-'}\n\n"
        "Title:\n"
        f"{(title or '-').strip() or '-'}\n\n"
        "Content:\n"
        f"{(content or '-').strip() or '-'}\n"
    )

