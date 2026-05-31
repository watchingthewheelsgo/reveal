# Reveal — Agent Developer Guide

## Quick Start

```bash
# Install dependencies
uv sync

# Copy and edit config
cp .env.example .env

# Run
uv run uvicorn server.main:app --reload
```

## Project Structure

```
reveal/
├── server/          # Application code
│   ├── main.py       # FastAPI entry + lifecycle
│   ├── scheduler.py  # APScheduler jobs
│   ├── commands.py   # Shared bot command handlers
│   ├── bot/          # Telegram + Feishu adapters
│   ├── stock/        # Stock scanner + tracker (Phase 2)
│   ├── social/       # Twitter monitor (Phase 3)
│   ├── journal/      # Trading journal (Phase 4)
│   ├── llm/          # LLM client
│   └── db/           # SQLAlchemy models + engine
├── config/settings.py  # Env-based configuration
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Tech Stack

- Python 3.13+, uv package manager
- FastAPI, python-telegram-bot, lark-oapi
- SQLAlchemy async + aiosqlite
- APScheduler for cron jobs
- OpenAI-compatible LLM (DeepSeek/Qwen)

## Code Style

- Python: ruff (line-length=100), pyright type checking
- Use `loguru` for logging
- Async everywhere
