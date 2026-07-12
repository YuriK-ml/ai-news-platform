from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from ai_news_platform.ingestion.rss.connector import RssConnector
from ai_news_platform.models.source import Source


@dataclass
class DummyResponse:
    text: str = "<rss/>"

    def raise_for_status(self) -> None:
        return None


class DummyHttpxClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        return DummyResponse()


class Parsed:
    bozo = 0

    def __init__(self, entries):
        self.entries = entries


def test_rss_fetch_backward_compatible_no_config(monkeypatch) -> None:
    import ai_news_platform.ingestion.rss.connector as mod

    monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: DummyHttpxClient())

    # Two entries in original order.
    entries = [
        {"link": "https://e/1", "title": "t1"},
        {"link": "https://e/2", "title": "t2"},
    ]
    monkeypatch.setattr(mod.feedparser, "parse", lambda text: Parsed(entries))

    src = Source(id="s", domain_id="ai", type="rss", name="S", url="https://rss", config={})
    items = RssConnector().fetch(src)
    assert [i.payload["link"] for i in items] == ["https://e/1", "https://e/2"]


def test_rss_fetch_filters_sorts_limits(monkeypatch) -> None:
    import ai_news_platform.ingestion.rss.connector as mod

    monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: DummyHttpxClient())

    now = time.gmtime()
    old = time.gmtime(0)  # 1970

    entries = [
        # Old -> must be dropped by max age
        {"link": "https://e/old", "title": "old", "published_parsed": old},
        # No date -> must be dropped by drop_if_no_date
        {"link": "https://e/nodate", "title": "nodate"},
        # Fresh A
        {"link": "https://e/a", "title": "a", "published_parsed": now},
        # Fresh B, but use updated_parsed fallback
        {"link": "https://e/b", "title": "b", "updated_parsed": now},
    ]
    monkeypatch.setattr(mod.feedparser, "parse", lambda text: Parsed(entries))

    src = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://rss",
        config={
            "rss_max_age_hours": 24,
            "rss_sort_by_published_desc": True,
            "rss_max_items": 1,
            "rss_drop_if_no_date": True,
        },
    )
    items = RssConnector().fetch(src)

    # After filtering only fresh A/B remain; after limiting to 1 we get one item.
    assert len(items) == 1
    assert items[0].payload["link"] in {"https://e/a", "https://e/b"}


def test_rss_max_items_zero_returns_empty(monkeypatch) -> None:
    import ai_news_platform.ingestion.rss.connector as mod

    monkeypatch.setattr(mod.httpx, "Client", lambda *a, **k: DummyHttpxClient())
    monkeypatch.setattr(mod.feedparser, "parse", lambda text: Parsed([{"link": "https://e/1", "title": "t"}]))

    src = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://rss",
        config={"rss_max_items": 0},
    )
    items = RssConnector().fetch(src)
    assert items == []

