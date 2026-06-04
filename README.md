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

## Database

本地默认使用 SQLite：

```env
DATABASE_URL=sqlite+aiosqlite:///./data/reveal.db
```

线上建议使用 Postgres/Supabase。Reveal 可以直接接受 Supabase 提供的普通连接串，运行时会自动转换成 async SQLAlchemy 使用的 `postgresql+asyncpg://`，并在未显式配置 SSL 时默认要求 SSL：

```env
DATABASE_URL=postgresql://postgres:<password>@db.<project-ref>.supabase.co:5432/postgres
```

注意：Supabase direct connection `db.<project-ref>.supabase.co:5432` 默认是 IPv6-only。Render 等 IPv4-only 环境应使用 Supabase Dashboard → Connect 里的 Session Pooler URL：

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

## Render

Reveal 可以用仓库根目录的 `render.yaml` 作为 Render Blueprint 部署。当前配置使用 Docker runtime 和 `/health` 健康检查。

关键点：

- 服务必须监听 `0.0.0.0:$PORT`；Docker 镜像默认 `PORT=10000`，Render 也会通过 `PORT` 注入实际端口。
- 在 Render Environment 里设置 `DATABASE_URL`，不要把数据库密码写进 `render.yaml`；Supabase 上建议填 Session Pooler URL。
- Blueprint 使用 `starter` plan，因为 Reveal 的 bot、Twitter monitor 和定时任务需要常驻；不建议用 Free web service 跑主服务，Free 实例会空闲休眠。

部署后先检查：

```bash
curl https://<your-service>.onrender.com/health
```

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

## Capability Architecture

Reveal 的用户入口分为三层：

- **System command**：稳定、可审计的操作入口，例如 `/quote`、`/twatch`、`/research`。
- **System tool**：确定性工具能力，例如报价、技术指标、新闻、持仓、历史研究。命令、自然语言和 Agent MCP 都复用同一批实现函数。
- **Skill / workflow**：多步任务，例如 Twitter 研究线程、股票深度研究、选股、告警、日报。Skill 可以调用多个 tools，也可以交给 Claude Agent SDK 执行多轮工具循环。

代码层次：

- `server/capabilities/registry.py` 是系统能力目录，定义每个 command/tool/skill 的名称、命令、自然语言示例和 Agent MCP 工具映射。
- `server/capabilities/planner.py` 是自然语言规划层，把用户表达编译成结构化 `PlannedAction`，包含 capability、command、args、confidence 和 reason。
- `server/capabilities/market.py` 放可复用的核心工具实现。
- `server/commands.py` 只负责 IM/slash command 和自然语言 planner。
- `server/mcp.py` 把同一批核心工具暴露给 Claude Agent SDK。
- `server/research/claude_sdk_runtime.py` 使用 Claude Agent SDK + DeepSeek，把 `WebSearch`、`WebFetch` 和 Reveal MCP 工具放进白名单，禁止本地文件/命令类工具。

设计约束：

- 每个对用户可见的 system command 必须先登记为 `CapabilitySpec`。
- 如果一个 command 可以被 Agent 调用，应同时登记 `agent_tool`，并在 `server/mcp.py` 复用同一个核心实现函数。
- 自然语言入口先生成 plan，再执行 command；低置信度或参数不完整时先向用户确认。
- Claude Agent SDK 负责多轮工具循环和会话恢复；Reveal 只提供受控工具白名单、MCP server 和业务上下文，不手写 Agent runtime。

用户可以用 slash command 精确触发，也可以用自然语言触发同一能力，例如：

```text
NVDA 现在多少钱
查一下 MRVL 新闻
我的持仓
把 @OwenCarter_k 加到 watch list
深度研究 MRVL
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
配置 `TWITTER_AUTH_TOKENS` 后，Reveal 会优先使用 X GraphQL 拉取列表页并保存历史分页 cursor；没有 token 时才退回 vxTwitter。

也可以在 bot 里动态管理：

```text
/twatch list
/twatch add @elonmusk
/twatch del @elonmusk
/twatch check
/digest
/summary @elonmusk
```

首次添加账号后会立即检查一次，最多推送最近 10 条推文；列表接口已经返回的内容都会缓存。之后按 `TWITTER_MONITOR_INTERVAL` 增量检查新推文，默认每 3600 秒一次。飞书/Telegram 是主交互入口：系统会推送可操作研究卡片，完整正文、外链、媒体和引用信息保存在 Reveal 数据库中。

`TWITTER_DIGEST_ENABLED=true` 时，Reveal 会按 `TWITTER_DIGEST_TIME` 和 `TWITTER_DIGEST_TIMEZONE` 自动发送昨日 Twitter 关注日报。也可以手动用 `/digest` 生成关注账号日报，或用 `/summary @user [YYYY-MM-DD]` 查看单个账号日报。

收到提醒后，优先直接回复那张推送卡片并 @Reveal 继续提问。Reveal 会根据被回复的卡片自动绑定这条 Twitter/X 更新并建立研究话题。

飞书卡片按优先级使用四档标题颜色：橙色代表 LLM 判定的重点关注或高优先级，蓝色代表中等优先级，绿色代表低优先级但市场相关，灰色代表低相关或未完成分析。图片直接展示在卡片底部，外链和引用只放在底部参考区。

命令也保留为备用入口：

```text
/research POST_ID [研究重点]  # 建立研究话题
/deep POST_ID [研究重点]      # 让 Agent 主动深挖
/ask POST_ID 问题            # 基于这条更新直接追问
```

`/research` 建立话题后，直接发送普通消息就会进入当前研究话题；用 `/topic summary` 汇总，用 `/topic stop` 结束。

## Deep Research

每条 Twitter/X 推送都会绑定到一个稳定的消息 ID。直接回复卡片是主路径，也可以用命令手动指定：

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
