# Reveal

美股交易助手后台服务，提供 Telegram/飞书 bot 命令、每日选股、Twitter/X 监控和交易日记。

## Run

```bash
uv sync
cp .env.example .env
uv run uvicorn server.main:app --reload
```

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pre-commit install
```

`pre-commit install` must be run from a Git repository checkout.

Bot 命令默认只允许配置的管理员 chat 使用。至少配置一个：

- `TELEGRAM_ADMIN_CHAT_ID`
- `FEISHU_ADMIN_CHAT_ID`

`TWITTER_ACCOUNTS` 支持逗号分隔或 JSON 数组，例如 `elonmusk,naval`。
