from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from ai_news_platform.ingestion.rss.connector import RssConnector
from ai_news_platform.models.source import Source


def _ok_feedparser_result() -> SimpleNamespace:
    return SimpleNamespace(bozo=0, entries=[])


class _FakeHttpxClient:
    def __init__(self, *, timeout: float, headers: dict[str, str], follow_redirects: bool, get_impl):
        self.timeout = timeout
        self.headers = headers
        self.follow_redirects = follow_redirects
        self._get_impl = get_impl

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        return self._get_impl(url)


def test_success_first_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def get_impl(url: str):
        calls.append(url)
        return httpx.Response(200, text="<rss></rss>", request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr(
        "ai_news_platform.ingestion.rss.connector.httpx.Client",
        lambda **kw: _FakeHttpxClient(get_impl=get_impl, **kw),
    )

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60, "retry_attempts": 3},
    )

    items = RssConnector().fetch(source)
    assert items == []
    assert calls == ["https://example.com/feed.xml"]


def test_connecttimeout_then_success_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = 0
    calls: list[str] = []
    sleeps: list[float] = []

    def get_impl(url: str):
        nonlocal attempt
        attempt += 1
        calls.append(url)
        if attempt == 1:
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(200, text="<rss></rss>", request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr(
        "ai_news_platform.ingestion.rss.connector.httpx.Client",
        lambda **kw: _FakeHttpxClient(get_impl=get_impl, **kw),
    )
    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.time.sleep", lambda s: sleeps.append(s))

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60, "retry_attempts": 3},
    )

    items = RssConnector().fetch(source)
    assert items == []
    assert calls == ["https://example.com/feed.xml", "https://example.com/feed.xml"]
    assert sleeps == [2]


def test_all_attempts_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def get_impl(url: str):
        calls.append(url)
        raise httpx.ReadTimeout("read timeout")

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr(
        "ai_news_platform.ingestion.rss.connector.httpx.Client",
        lambda **kw: _FakeHttpxClient(get_impl=get_impl, **kw),
    )
    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.time.sleep", lambda s: sleeps.append(s))

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60, "retry_attempts": 3},
    )

    with pytest.raises(httpx.ReadTimeout):
        RssConnector().fetch(source)

    assert calls == ["https://example.com/feed.xml"] * 3
    assert sleeps == [2, 2]


def test_http_404_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def get_impl(url: str):
        calls.append(url)
        return httpx.Response(404, text="nope", request=httpx.Request("GET", url))

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr(
        "ai_news_platform.ingestion.rss.connector.httpx.Client",
        lambda **kw: _FakeHttpxClient(get_impl=get_impl, **kw),
    )
    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.time.sleep", lambda s: sleeps.append(s))

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60, "retry_attempts": 3},
    )

    with pytest.raises(httpx.HTTPStatusError):
        RssConnector().fetch(source)

    assert calls == ["https://example.com/feed.xml"]
    assert sleeps == []


def test_timeout_seconds_passed_to_httpx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_timeout: list[float] = []

    def get_impl(url: str):
        return httpx.Response(200, text="<rss></rss>", request=httpx.Request("GET", url))

    def client_factory(**kw):
        seen_timeout.append(kw["timeout"])
        return _FakeHttpxClient(get_impl=get_impl, **kw)

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.httpx.Client", client_factory)

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60},
    )

    RssConnector().fetch(source)
    assert seen_timeout == [60.0]


def test_retry_attempts_default_is_1(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    sleeps: list[float] = []

    def get_impl(url: str):
        calls.append(url)
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.feedparser.parse", lambda _: _ok_feedparser_result())
    monkeypatch.setattr(
        "ai_news_platform.ingestion.rss.connector.httpx.Client",
        lambda **kw: _FakeHttpxClient(get_impl=get_impl, **kw),
    )
    monkeypatch.setattr("ai_news_platform.ingestion.rss.connector.time.sleep", lambda s: sleeps.append(s))

    source = Source(
        id="s",
        domain_id="ai",
        type="rss",
        name="S",
        url="https://example.com/feed.xml",
        config={"timeout_seconds": 60},
    )

    with pytest.raises(httpx.ConnectTimeout):
        RssConnector().fetch(source)

    assert calls == ["https://example.com/feed.xml"]
    assert sleeps == []

