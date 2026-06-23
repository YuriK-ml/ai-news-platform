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


def build_batch_input(*, articles: list[dict[str, str | None]]) -> str:
    """
    Batch input contract.

    Each item in `articles` must contain:
    - article_id
    - source_name
    - canonical_url
    - published_at
    - title
    - content
    """

    blocks: list[str] = []
    for a in articles:
        blocks.append(
            "### ARTICLE\n\n"
            f"Article-ID: {a.get('article_id')}\n"
            f"Source: {a.get('source_name')}\n"
            f"URL: {a.get('canonical_url')}\n"
            f"Published: {a.get('published_at') or '-'}\n\n"
            "Title:\n"
            f"{(a.get('title') or '-').strip() or '-'}\n\n"
            "Content:\n"
            f"{(a.get('content') or '-').strip() or '-'}\n\n"
            "### END ARTICLE\n"
        )
    return "\n".join(blocks).strip() + "\n"
