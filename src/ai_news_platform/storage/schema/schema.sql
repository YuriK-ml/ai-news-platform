PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  domain_id TEXT NOT NULL,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT,

  -- Monitoring fields
  last_fetch_at TEXT,
  last_success_at TEXT,
  last_error_at TEXT,
  consecutive_error_count INTEGER NOT NULL DEFAULT 0,
  total_error_count INTEGER NOT NULL DEFAULT 0,
  last_http_status INTEGER,
  last_error_message TEXT,
  last_item_external_id TEXT,
  last_item_published_at TEXT,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sources_domain_id ON sources(domain_id);

CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY,
  domain_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  source_item_external_id TEXT,

  title TEXT,
  content TEXT,
  published_at TEXT,

  lifecycle_state TEXT NOT NULL, -- raw|normalized|rewritten|published

  raw_json TEXT,
  normalized_json TEXT,
  rewritten_json TEXT,

  publication_status TEXT NOT NULL DEFAULT 'unpublished', -- unpublished|published|failed|skipped_old
  publication_error TEXT,
  telegram_message_id INTEGER,
  telegram_channel TEXT,
  telegram_published_at TEXT,

  editorial_status TEXT NOT NULL DEFAULT 'NEW', -- NEW|REJECTED|READY|PUBLISHED|ERROR
  status_updated_at TEXT,
  rejection_reason TEXT,
  rejected_at TEXT,
  llm_processed_at TEXT,
  final_language TEXT,
  final_title TEXT,
  final_text TEXT,
  last_error_stage TEXT, -- INGEST|LLM|PUBLISH|OTHER
  last_error_message TEXT,
  last_error_at TEXT,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,

  FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_articles_domain_url ON articles(domain_id, canonical_url);
CREATE UNIQUE INDEX IF NOT EXISTS uq_articles_source_external_id
  ON articles(source_id, source_item_external_id)
  WHERE source_item_external_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_articles_domain_id ON articles(domain_id);
CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_publication_status ON articles(publication_status);
CREATE INDEX IF NOT EXISTS idx_articles_editorial_status ON articles(editorial_status);

CREATE TABLE IF NOT EXISTS article_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  article_id TEXT NOT NULL,
  event_type TEXT NOT NULL, -- INGESTED|LLM_PROCESSED|PUBLISHED|ERROR
  created_at TEXT NOT NULL,
  details_json TEXT,
  error_message TEXT,
  FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_article_events_article_id ON article_events(article_id);
CREATE INDEX IF NOT EXISTS idx_article_events_event_type ON article_events(event_type);
