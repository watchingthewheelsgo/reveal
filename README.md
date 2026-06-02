# Reveal

美股交易助手后台服务，提供 Telegram/飞书 bot 命令、每日选股、Twitter/X 监控和交易日记。

## Run

```bash
uv sync
cp .env.example .env
uv run start
```

Web 工作台默认随服务启动，但它不是 Reveal 的即时研究主路径。它只作为电脑边上的辅助看板，后续用于实盘操作、关注股票和消息归档查看：

```text
http://127.0.0.1:8000
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

首次添加账号后会立即检查一次，最多回拉最近 10 条推文并缓存。之后按 `TWITTER_MONITOR_INTERVAL` 增量检查新推文，默认每 3600 秒一次。飞书/Telegram 是主交互入口：系统会推送可操作研究卡片，完整正文、外链、媒体和引用信息保存在 Reveal 数据库中。

收到提醒后，优先在 IM 里继续操作：

```text
/research POST_ID [研究重点]  # 建立研究话题
/deep POST_ID [研究重点]      # 让 Agent 主动深挖
/ask POST_ID 问题            # 基于这条更新直接追问
```

`/research` 建立话题后，直接发送普通消息就会进入当前研究话题；用 `/topic summary` 汇总，用 `/topic stop` 结束。

## Deep Research

每条 Twitter/X 推送都会带一个稳定的消息 ID，并给出后续操作命令：

```text
/deep POST_ID [研究重点]
/ask POST_ID 问题
/research POST_ID [研究重点]
/topic start POST_ID [研究重点]
```

也可以用 `latest` 指向最近一条更新：

```text
/deep latest
/ask latest 这条消息对 NVDA 有什么影响？
/research latest AI 基建
/topic start latest AI 基建
```

`/deep` 会通过 Claude Agent SDK 调用 DeepSeek，并让 agent 使用 WebSearch/WebFetch 对原推、引用推、外部链接和外部证据做深度解析。`/research` 和 `/topic start` 都会开启一个绑定到该消息的研究线程，之后可以直接发送普通消息继续追问；用 `/topic summary` 汇总当前线程，用 `/topic stop` 结束线程。

Deep research 不再维护 Reveal 自己的 Google/SearXNG/Brave planner 或抓取 runtime。Reveal 只保存研究线程状态，把联网搜索、页面读取和多轮工具循环交给 Claude Agent SDK。

DeepSeek 配置示例：

```env
DEEPSEEK_API_KEY=<DeepSeek API Key>
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
AGENT_RUNTIME=claude_sdk
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
ANTHROPIC_AUTH_TOKEN=<DeepSeek API Key>
ANTHROPIC_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-pro[1m]
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-flash
AGENT_EFFORT=max
AGENT_MAX_TURNS=8
```

普通聊天、摘要和意图分类使用 DeepSeek OpenAI-compatible endpoint。深度研究使用 `ANTHROPIC_*` 这组 Claude Code / Agent SDK 原生变量名连接 DeepSeek Anthropic-compatible endpoint。`ANTHROPIC_AUTH_TOKEN` 为空时会复用 `DEEPSEEK_API_KEY`。`AGENT_EFFORT` 和 `AGENT_MAX_TURNS` 是 Reveal 对 Agent 循环强度和最大轮数的控制。

Claude Agent SDK 运行时只开放 `WebSearch` 和 `WebFetch`，不会读取本地文件、运行命令或修改文件。
