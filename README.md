# AI News Platform

Production-oriented, multi-domain platform for automated news ingestion, AI-based editorial processing, and Telegram publishing.

## Overview

The platform collects articles from configured RSS sources, removes duplicates, evaluates each article using an LLM, prepares publication-ready content, and automatically publishes selected news to Telegram.

The current working domain is football news. The architecture supports adding additional domains through configuration files and domain-specific prompts.

## Implemented Features

* RSS ingestion from multiple configured sources
* Article parsing and normalization
* SQLite storage
* URL-based deduplication
* Configurable article age filtering
* LLM-based editorial filtering
* Automatic rejection of irrelevant or low-quality content
* Generation of publication-ready Russian-language posts
* Telegram channel publishing
* Publication limits per run
* Editorial workflow statuses:

  * `NEW`
  * `REJECTED`
  * `READY`
  * `PUBLISHED`
  * `ERROR`
* Structured JSON logging
* Domain-specific prompts and configuration
* CLI-based execution

## Processing Pipeline

```text
RSS sources
    ↓
Article normalization
    ↓
SQLite storage and deduplication
    ↓
LLM editorial processing
    ↓
REJECTED or READY
    ↓
Telegram publishing
    ↓
PUBLISHED
```

## Project Structure

* Configuration: `config/settings.example.yaml`
* Environment template: `.env.example`
* Domain prompts: `prompts/`
* Architecture documentation: `docs/architecture_v1.md`
* Application source: `src/ai_news_platform/`
* Database schema: `src/ai_news_platform/storage/schema/schema.sql`
* Tests: `tests/`

## Local Installation

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install the project:

```powershell
pip install -e .
```

Copy the environment template:

```powershell
Copy-Item .env.example .env
```

Configure the required environment variables in `.env`:

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=
TELEGRAM_BOT_TOKEN=
DATABASE_PATH=./data/app.sqlite3
```

## Run

```powershell
ai-news-platform ingest --config config/settings.example.yaml --domain football
```

Repeated execution processes only newly discovered articles. Previously stored URLs are recognized as duplicates.

## Current Deployment Model

The application is designed to run as a scheduled background CLI process. A web API is not required for the current single-process publishing workflow.

For server deployment, the command can be triggered periodically using cron or a systemd timer.

## Planned Improvements

* Batch processing of multiple articles in a single LLM request
* Scheduled server deployment
* Additional news domains
* Extended operational monitoring
* Improved test coverage
