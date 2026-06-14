# ai-news-platform

Production-oriented, multi-domain AI news aggregation platform.

## Minimal Working Slice (implemented)

- RSS ingestion from configured sources
- Parse RSS entries and normalize to `Article`
- Store in SQLite
- Deduplicate by URL (unique constraint)
- Structured logging (JSON)

Not implemented yet:

- LLM processing
- Telegram publishing
- FastAPI endpoints

## Key Paths

- Example config: `config/settings.example.yaml`
- Example env: `.env.example`
- Source + article schema: `src/ai_news_platform/storage/schema/schema.sql`

## Local Test

1) Create a virtualenv and install dependencies:

- `python -m venv .venv`
- PowerShell: `.venv\\Scripts\\Activate.ps1`
- `pip install -e .`

2) Configure environment:

- Copy `.env.example` to `.env`
- Set `DATABASE_PATH` (default: `./data/app.sqlite3`)
- Set `TELEGRAM_BOT_TOKEN` to enable publishing

3) Run ingestion:

- `ai-news-platform ingest --config config/settings.example.yaml --domain football`

Re-running the same command should log duplicates and avoid inserting the same URL twice.
