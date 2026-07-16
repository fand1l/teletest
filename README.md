# Telegram Intelligence Aggregator

A personal, AI-powered intelligence platform that continuously collects, analyzes, and summarizes news from Telegram channels.

## Features

- **Continuous Collection:** Asynchronous `Telethon` client that listens to Telegram channels without downloading history.
- **Local-First AI Pipeline:** Minimizes API costs by performing initial processing locally:
  - Embeddings generation via `intfloat/multilingual-e5-small`
  - Entity Extraction via `GLiNER`
- **Semantic Deduplication:** Uses `pgvector` in PostgreSQL to group incoming reports into unified "Events".
- **Contradiction Resolution:** Queries Gemini only when conflicting facts are detected in semantically matched reports.
- **Bot Interface:** Search across the intelligence database and get on-demand event summaries via an `aiogram` bot.

## Architecture

This project is built as a **Modular Monolith**:
- `src/collector`: Telethon ingestion.
- `src/pipeline`: Background workers that filter spam, run local NLP models, and match events.
- `src/reasoning`: Gemini LLM wrappers.
- `src/bot`: The user-facing Telegram bot interface.
- `src/database`: PostgreSQL schema, repositories, and Alembic migrations.

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys (Telegram, Bot, Gemini).
2. Install dependencies:
   ```bash
   pip install -e .
   ```
3. Run Alembic migrations (make sure PostgreSQL is running):
   ```bash
   alembic upgrade head
   ```
4. Start the application:
   ```bash
   python src/main.py
   ```

*Note: On first run, Telethon will ask for your Telegram login code in the terminal.*
