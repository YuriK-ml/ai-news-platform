# Architecture V1 (Implementation Baseline)

This document defines the authoritative architecture for **ai-news-platform** after all approved simplifications. It is the baseline for implementing the upcoming LLM layer.

---

## 1) Overall Pipeline

### Current MVP (implemented)
RSS → SQLite (dedupe/store) → Telegram → mark as published

### Target V1 (next milestone)
RSS → SQLite (dedupe/store) → **Single LLM Stage** → `REJECTED` or `READY` → Telegram → `PUBLISHED`

Principles:
- Multi-domain and source configuration is **domain-driven** and **config-based**.
- Articles are **never deleted** from SQLite.
- Editorial decisions are persisted via a simple lifecycle (`NEW`, `REJECTED`, `READY`, `PUBLISHED`, `ERROR`).
- LLM editorial work is done in **one prompt** and **one model call per article** (per domain).

---

## 2) SQLite Schema Overview

SQLite is the source of truth for:
- ingested articles
- deduplication state
- editorial status and results
- publication status and Telegram identifiers
- audit trail (append-only)

### Core tables

#### `sources`
Purpose: configured source persistence and monitoring.
- identity: `id`, `domain_id`, `type`, `name`, `url`, `enabled`
- monitoring fields: last fetch timestamps, error counters, last error, etc.

#### `articles`
Purpose: store every ingested article and all processing/publication outcomes.

Key groups of fields:
- Identity/dedupe
  - `id` (stable derived id)
  - `domain_id`, `source_id`
  - `canonical_url` (dedupe key)
  - `source_item_external_id` (optional unique per source)
- Ingestion content
  - `title`
  - `content` (RSS content/summary/description)
  - `published_at` (RSS published date when available)
  - `raw_json` (full JSON of RSS entry dict)
  - `normalized_json` (subset JSON used by the pipeline)
- Editorial (LLM stage, planned)
  - `editorial_status` (`NEW|REJECTED|READY|PUBLISHED|ERROR`)
  - `status_updated_at`
  - `rejection_reason`, `rejected_at`
  - `llm_processed_at`
  - `final_language`, `final_title`, `final_text`
  - `last_error_stage`, `last_error_message`, `last_error_at`
- Publishing (Telegram)
  - `publication_status` (`unpublished|published|failed|skipped_old`)
  - `publication_error`
  - `telegram_message_id`, `telegram_channel`, `telegram_published_at`
  - `created_at`, `updated_at`

Deduplication:
- enforced by a unique constraint on `(domain_id, canonical_url)`.

#### `article_events`
Purpose: lightweight append-only audit trail.
- `id` (integer PK)
- `article_id`
- `event_type` (see section 4)
- `created_at`
- `details_json` (small JSON blob, optional)
- `error_message` (optional)

---

## 3) Editorial Statuses (Primary Lifecycle Driver)

The **primary lifecycle driver** is `articles.editorial_status`.

Allowed values:
- `NEW` — stored in SQLite; not yet processed by the LLM stage.
- `REJECTED` — rejected by LLM; stored with reason + timestamp.
- `READY` — accepted by LLM; publication-ready RU content exists (`final_title`, `final_text`).
- `PUBLISHED` — published to Telegram; Telegram ids and timestamps stored.
- `ERROR` — processing failed (LLM or publishing); error details stored in:
  - `last_error_stage`
  - `last_error_message`
  - `last_error_at`

### Visual lifecycle

NEW → REJECTED

NEW → READY → PUBLISHED

NEW → ERROR

No intermediate workflow states are introduced in V1.

---

## 4) Relationship: `editorial_status` vs `publication_status`

To avoid ambiguity, these fields have different roles:

### `editorial_status` (product/editorial lifecycle; drives workflow)
- Represents where the article is in the editorial pipeline (NEW/REJECTED/READY/PUBLISHED/ERROR).
- Pipeline selection rules (future):
  - LLM stage processes `editorial_status=NEW` (for enabled domains).
  - Telegram publisher publishes `editorial_status=READY`.
- This is the primary field developers should use to reason about the article lifecycle.

### `publication_status` (technical publishing state; inherited from MVP)
- Tracks the **technical status of Telegram publishing attempts** and operational outcomes:
  - `unpublished` (not yet published)
  - `published` (published successfully)
  - `failed` (publish attempted and failed)
  - `skipped_old` (skipped by freshness rules)
- It is a technical field used by the publisher to prevent republishing and to keep operational error context (`publication_error`).

### Consistency expectation (V1)
- When `editorial_status=PUBLISHED`, the record should also reflect technical publication:
  - `publication_status='published'`
  - `telegram_message_id`, `telegram_channel`, and `telegram_published_at` set
- For the future LLM-based lifecycle, decisions should be driven by `editorial_status`; `publication_status` remains as a technical publishing field for backward compatibility and operational visibility.

---

## 5) Prompt Structure (Single Prompt per Domain)

Prompts are stored as plain text files in the repository.

Directory:
```
prompts/
├── football_pipeline.txt
├── ai_pipeline.txt
└── finance_pipeline.txt
```

Rules:
- One prompt file per domain.
- YAML references only prompt filenames.
- Prompt contents are not embedded in YAML.
- Prompt instructions should use ASCII characters only.
- For rejection responses, use **reason codes** (not free-form reasons).

---

## 6) LLM Input Contract (Single Article)

The pipeline will provide the model with a single article using only fields already stored in SQLite.

Template:
```
### ARTICLE

Source: {source_name}
URL: {canonical_url}
Published: {published_at_or_dash}

Title:
{title_or_dash}

Content:
{content_or_dash}
```

Field mapping:
- `source_name`: `sources.name` joined by `articles.source_id`
- `canonical_url`: `articles.canonical_url`
- `published_at_or_dash`: `articles.published_at` or `-`
- `title_or_dash`: `articles.title` or `-`
- `content_or_dash`: `articles.content` or `-`

---

## 7) LLM Output Contract (No JSON)

The model must return **only one** of the following formats.

### Reject
```
REJECT
<optional explanation>
```

Only the `REJECT` header is required. Any additional text after `REJECT` is optional and may be stored as a free-form rejection explanation.

### Ready to publish
```
READY_TO_PUBLISH

Title: <final_title>

Text: <final_text>
```

Requirements:
- No JSON.
- No explanations.
- No code blocks.
- `final_text` must be Russian and publication-ready for Telegram.

Persistence mapping (planned):
- `REJECT` → `editorial_status=REJECTED`, store `rejection_reason` (free-form), `rejected_at`, append `LLM_PROCESSED` event
- `READY_TO_PUBLISH` → `editorial_status=READY`, store `final_title`, `final_text`, `final_language='ru'`, `llm_processed_at`, append `LLM_PROCESSED` event
- failures → `editorial_status=ERROR`, store `last_error_*`, append `ERROR` event

---

## 8) Telegram Publishing Flow

Telegram is currently the only output channel.

Publishing rules:
- Publish only items eligible for publication.
- Do not republish already published items.
- Store `telegram_message_id`, `telegram_channel`, and publication timestamp.

Target behavior with LLM stage:
- Publisher selects articles with `editorial_status=READY`.
- On success:
  - set `editorial_status=PUBLISHED`
  - set `publication_status='published'`
  - persist `telegram_message_id`, `telegram_channel`, `telegram_published_at`
  - append `PUBLISHED` event in `article_events`
- On failure:
  - set `editorial_status=ERROR`
  - set `last_error_stage='PUBLISH'`, `last_error_message`, `last_error_at`
  - set `publication_status='failed'` (technical)
  - append `ERROR` event

---

## 9) Multi-domain Configuration

Domains are configured via YAML; new domains and sources are added without changing core architecture.

Each domain defines:
- `id`, `name`
- Telegram routing: `telegram.channel_id`
- sources list (RSS now; website sources later)
- LLM behavior: `domains[].llm`
  - `enabled` (domain-level on/off)
  - `pipeline_prompt` (filename under `llm.prompts_dir`)
  - `target_language` (default `ru`)

Global configuration includes:
- ingestion settings (freshness window)
- publishing limits
- LLM prompts directory and default target language

Secrets remain in `.env`:
- `TELEGRAM_BOT_TOKEN`
- future LLM provider keys (not in scope yet)
- `DATABASE_PATH`

---

## 10) Future Extension Points

This V1 design keeps extension points minimal and stable:

- Multiple LLM providers / OpenAI-compatible integration can be implemented behind a single `LLMStageService` without changing DB schema or prompt structure.
- Different models per domain can be added later as optional per-domain config fields under `domains[].llm`.
- Domain-level LLM disablement is supported via `domains[].llm.enabled` (disabled domains do not enter the LLM processing pipeline).
- Additional output channels can be supported via the publishing abstraction; Telegram remains the first implementation.
- Additional sources can be added via ingestion connectors without changing normalization/storage contracts.
