from __future__ import annotations

from ai_news_platform.models.article import Article
from ai_news_platform.models.source import Source
from ai_news_platform.storage.db import Database
from ai_news_platform.storage.repositories.articles import ArticleRepository
from ai_news_platform.storage.repositories.sources import SourceRepository


def test_deduplicate_by_url(tmp_path) -> None:
    db_path = tmp_path / "test.sqlite3"
    db = Database(path=str(db_path))

    with db.connect() as conn:
        db.initialize_schema(conn)

        source = Source(
            id="bbc",
            domain_id="football",
            type="rss",
            name="BBC",
            url="https://example.com/rss",
            config={},
        )
        SourceRepository().upsert(conn, source)

        article = Article(
            id="a1",
            domain_id="football",
            source_id="bbc",
            canonical_url="https://example.com/article/1",
            title="Hello",
            content="World",
            metadata={"published_at": "2020-01-01T00:00:00Z"},
        )
        repo = ArticleRepository()
        first = repo.insert_normalized(conn, article=article, source_item_external_id="x1", raw_json={})
        second = repo.insert_normalized(conn, article=article, source_item_external_id="x1", raw_json={})

        assert first.inserted is True
        assert second.inserted is False

