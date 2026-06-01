# Reveal

美股交易助手后台服务，提供 Telegram/飞书 bot 命令、每日选股、Twitter/X 监控和交易日记。

## Run

```bash
uv sync
cp .env.example .env
uv run start
```

开发热重载：

```bash
RELOAD=1 uv run start
```

可选环境变量：

- `HOST`，默认 `0.0.0.0`
- `PORT`，默认 `8000`

## Feishu

Reveal 支持两种飞书接入：

- WebSocket bot，默认启用，配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 后随服务启动。
- HTTP callback API，路径是 `POST /feishu/event`，用于飞书开放平台事件订阅。

飞书开放平台事件订阅 URL 示例：

```text
https://<your-domain>/feishu/event
```

本地调试可以先用健康检查确认服务可达：

```bash
curl http://127.0.0.1:8000/health
```

如果只想使用 HTTP callback，不启动 WebSocket：

```env
FEISHU_ENABLE_WS=false
FEISHU_VERIFICATION_TOKEN=<飞书事件订阅 Verification Token>
FEISHU_ADMIN_CHAT_ID=<允许执行命令的 chat_id>
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

## Twitter/X Monitor

`TWITTER_ACCOUNTS` 支持逗号分隔或 JSON 数组，例如 `elonmusk,naval`。

也可以在 bot 里动态管理：

```text
/twatch list
/twatch add @elonmusk
/twatch del @elonmusk
/twatch check
```

首次添加账号后的第一次检查只会记录当前最新推文作为基线，不会回推历史推文。后续新推文会推送正文、原文链接、外部链接、媒体 URL，以及引用/转推/回复关联信息。

## Deep Research

每条 Twitter/X 推送都会带一个稳定的消息 ID，并给出后续操作命令：

```text
/deep POST_ID [研究重点]
/ask POST_ID 问题
/topic start POST_ID [研究重点]
```

也可以用 `latest` 指向最近一条更新：

```text
/deep latest
/ask latest 这条消息对 NVDA 有什么影响？
/topic start latest AI 基建
```

`/deep` 会基于原推、引用推、外部链接和搜索结果生成深度解析。`/topic start` 会开启一个绑定到该消息的研究线程，之后可以直接发送普通消息继续追问；用 `/topic summary` 汇总当前线程，用 `/topic stop` 结束线程。

搜索是可选能力。默认 `SEARCH_PROVIDER=none`，只使用原推和已有链接；配置搜索后会额外联网检索：

```env
SEARCH_PROVIDER=google
GOOGLE_SEARCH_API_KEY=...
GOOGLE_SEARCH_ENGINE_ID=...
```

或：

```env
SEARCH_PROVIDER=brave
BRAVE_SEARCH_API_KEY=...
```
