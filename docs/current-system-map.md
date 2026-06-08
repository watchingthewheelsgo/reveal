# Reveal 当前系统抽象地图

这份文档只概括当前实现中的系统概念、功能模块和数据源边界，不描述重构方案。

## 系统定位

Reveal 当前可以抽象为一个个人市场情报助手后台：

```text
外部数据源 -> 采集/查询 -> 入库/缓存 -> 信号判断 -> IM 推送/Web 展示/定期报告 -> Agent 研究 -> 用户追问或记录交易
```

系统里有两类主要使用方式：

- 被动模式：系统定时监控外部变化，发现值得关注的事件后主动推送。
- 主动模式：用户通过 IM、slash command、Web、Agent 自然语言请求主动查询、研究或维护配置。

## 核心系统概念

### Data Source

数据源是 Reveal 获取外部事实的入口。当前数据源由 `server/capabilities/registry.py` 的 `ExternalServiceSpec` 描述，但具体调用分散在各业务模块。

数据源不直接代表业务能力。一个数据源可以被多个模块使用，例如 Finnhub 同时服务报价、新闻、告警、日报。

### Domain Record

Domain Record 是系统持久化的业务事实。当前主要模型在 `server/db/models.py`：

- `SocialPost`：Twitter/X 推文缓存和处理结果。
- `TwitterState`：Twitter/X 账号监控状态。
- `StockWatch`：按会话维护的股票观察列表。
- `RegulatoryEvent`：SEC/FDA 监管事件。
- `MarketMoverEvent`：Longbridge 市场异动事件。
- `Trade`：交易记录。
- `StockPick`：每日选股结果。
- `TrackingLog`：追踪标的表现。
- `ResearchSession`：研究线程。
- `ConversationMessage`：研究线程中的消息。
- `BotMessageBinding`：IM 消息和业务对象的绑定。
- `ScoringWeights`：评分权重。

### Signal

Signal 是从原始事实中提取出的“值得关注的信息”。当前没有统一 `Signal` 表，但已有类似字段和返回结构：

- `SocialPost.is_noteworthy`
- `SocialPost.attention_reason`
- `SocialPost.sentiment`
- `SocialPost.urgency`
- price/volume/news alert 返回的 dict
- `RegulatoryEvent.severity`
- `MarketMoverEvent.direction`

### Alert

Alert 是需要打断用户的 Signal。当前 Alert 不是独立模型，而是由各模块直接判断并通过 adapter 推送：

- 价格/成交量/新闻告警：`server/alerts/engine.py`
- SEC/FDA 告警：`server/alerts/regulatory.py`
- Longbridge 异动告警：`server/alerts/market_movers.py`
- 股票观察价格告警：`server/stock/watchlist.py`
- Twitter/X 重要推文推送：`server/social/monitor.py`

### Interaction

Interaction 是用户和系统的消息交互。当前由这些对象组成：

- `BotContext`：归一化 IM 输入。
- `BotAdapter`：发送消息、卡片、话题回复。
- `CommandRouter`：slash command 和普通消息路由。
- `BotMessageBinding`：把 IM message id 绑定到 `research_session` 或 `twitter` 等源对象。

### Capability

Capability 是对用户可见的系统能力。当前事实源是 `server/capabilities/registry.py`：

- slash command
- natural language examples
- Agent MCP tools
- external services
- side effects

同一个 Capability 可以被 IM command、Agent MCP tool、Web API 复用。

### Research

Research 是多步 Agent 分析流程。当前核心对象：

- `ResearchSession`
- `ConversationMessage`
- `server/research/service.py`
- `server/research/progress.py`
- `server/research/claude_sdk_runtime.py`
- `server/research/sdk_mcp.py`

Research 可以由 Twitter post、股票 ticker、自然语言消息或 Web 请求触发。

### Report

Report 是定期或手动生成的聚合内容。当前主要包括：

- 市场简报：`server/briefing.py`
- Twitter 日报：`server/social/digest.py`
- 追踪报告：`server/stock/tracker.py`

Report 和 Alert 的区别是：Report 聚合已有状态，通常不代表即时打断。

### Scheduler Job

Scheduler Job 是周期执行的任务。当前由 `server/scheduler.py` 包装 APScheduler，在 `server/main.py` 注册具体任务。

当前主要 job：

- `daily_pick`
- `tracking_update`
- `daily_briefing`
- `twitter_digest`
- `twitter_monitor`
- `alert_cycle`
- `regulatory_alert_cycle`
- `stock_watch_price`
- `longbridge_market_movers`

## 功能模块地图

### 1. Interface Layer

负责系统和外部用户界面的连接。

代码：

- `server/bot/telegram.py`
- `server/bot/feishu.py`
- `server/web.py`
- `server/mcp.py`

职责：

- 接收 Telegram/Feishu/Web/MCP 请求。
- 发送文本、卡片、话题回复。
- 暴露 Web API 和静态工作台。
- 把内部能力暴露给 Agent。

### 2. Interaction Layer

负责把用户输入路由到正确能力。

代码：

- `server/bot/base.py`
- `server/bot/bindings.py`
- `server/commands.py`
- `server/capabilities/planner.py`

职责：

- 解析 slash command。
- 处理普通自然语言消息。
- 处理回复消息和话题追问。
- 绑定 IM message 和业务对象。
- 调用 capability 或 research 流程。

### 3. Capability Layer

负责定义和实现用户可见能力。

代码：

- `server/capabilities/registry.py`
- `server/capabilities/market.py`
- `server/capabilities/twitter.py`
- `server/capabilities/journal.py`
- `server/capabilities/alerts.py`
- `server/capabilities/system.py`

职责：

- 维护能力目录。
- 复用核心业务函数。
- 给 slash command、MCP tool、Agent planner 提供共同语义。

### 4. Research Layer

负责 Agent 多步研究和研究线程管理。

代码：

- `server/research/service.py`
- `server/research/context.py`
- `server/research/progress.py`
- `server/research/claude_sdk_runtime.py`
- `server/research/sdk_mcp.py`

职责：

- 创建和恢复研究线程。
- 管理 conversation history。
- 构造 Agent prompt。
- 向 Agent 暴露受控 MCP tools。
- 把进度和结果推送到 IM。

### 5. Social Intelligence Module

负责 Twitter/X 账号监控、缓存、摘要和日报。

代码：

- `server/social/monitor.py`
- `server/social/processor.py`
- `server/social/twitter_graphql.py`
- `server/social/digest.py`
- `server/capabilities/twitter.py`

职责：

- 维护 Twitter watch list。
- 拉取账号最新推文。
- 缓存推文、媒体、链接、引用。
- 用 LLM 处理摘要、翻译、情绪、优先级。
- 推送重要更新。
- 生成 Twitter digest。

### 6. Market Data and Stock Module

负责行情、新闻、技术指标、选股、追踪和观察列表。

代码：

- `server/stock/data.py`
- `server/stock/scanner.py`
- `server/stock/scorer.py`
- `server/stock/tracker.py`
- `server/stock/watchlist.py`
- `server/stock/longbridge.py`
- `server/capabilities/market.py`

职责：

- 查询股票报价和历史数据。
- 获取公司新闻。
- 计算技术指标和多因子评分。
- 生成每日选股。
- 追踪选股表现。
- 维护手动股票观察列表。
- 接入 Longbridge 行情异动。

### 7. Alert Module

负责主动提醒。

代码：

- `server/alerts/engine.py`
- `server/alerts/price_alert.py`
- `server/alerts/volume_alert.py`
- `server/alerts/news_alert.py`
- `server/alerts/regulatory.py`
- `server/alerts/market_movers.py`
- `server/stock/watchlist.py`

职责：

- 汇总需要监控的 ticker。
- 检查价格、成交量、新闻。
- 检查 SEC/FDA 事件。
- 检查 Longbridge 市场异动。
- 检查手动股票观察列表价格变化。
- 去重并推送告警。

### 8. Report Module

负责定期报告和聚合摘要。

代码：

- `server/briefing.py`
- `server/social/digest.py`
- `server/stock/tracker.py`

职责：

- 生成每日市场简报。
- 生成 Twitter 账号日报。
- 生成追踪标的报告。
- 聚合持仓、市场、Twitter、研究和 P&L。

### 9. Journal and Portfolio Module

负责交易记录、持仓和 P&L。

代码：

- `server/journal/service.py`
- `server/journal/analyzer.py`
- `server/capabilities/journal.py`
- `server/capabilities/market.py`

职责：

- 记录交易。
- 查询当前持仓。
- 统计 P&L。
- 给日报、告警和 Agent 提供用户资产上下文。

### 10. Persistence Layer

负责数据库连接和业务对象持久化。

代码：

- `server/db/engine.py`
- `server/db/models.py`

职责：

- 初始化 SQLite/Postgres async engine。
- 管理 SQLAlchemy async session。
- 定义所有核心表结构。

### 11. Runtime and Scheduling Layer

负责应用启动、Bot 生命周期和周期任务。

代码：

- `server/main.py`
- `server/scheduler.py`
- `config/settings.py`

职责：

- 启动 FastAPI。
- 初始化数据库。
- 初始化 Telegram/Feishu bot。
- 注册 APScheduler jobs。
- 读取 env 配置。

### 12. Web Workbench

当前 Web 是辅助工作台，主对象是 Twitter/X research desk。

代码：

- `server/web.py`
- `server/static/index.html`
- `server/static/app.js`
- `server/static/app.css`

职责：

- 展示 Twitter/X 缓存更新。
- 查看单条 post 详情。
- 查看关联 research sessions。
- 从 Web 触发 deep research 和 follow-up ask。

## 数据源模块地图

### Market Data Sources

#### Finnhub

配置：

- `FINNHUB_API_KEY`
- `FINNHUB_BASE_URL`

主要使用：

- 股票现价。
- 公司新闻。
- 告警和日报中的行情上下文。

相关模块：

- `server/stock/data.py`
- `server/capabilities/market.py`
- `server/alerts/news_alert.py`
- `server/briefing.py`

#### yfinance

配置：

- 无显式 token。

主要使用：

- 历史行情。
- 技术指标。
- Finnhub fallback。

相关模块：

- `server/stock/data.py`
- `server/stock/scorer.py`
- `server/capabilities/market.py`

#### Longbridge

配置：

- `LONGBRIDGE_ENABLED`
- `LONGBRIDGE_OAUTH_TOKEN_PATH`
- `LONGBRIDGE_API_BASE`
- `LONGBRIDGE_MOVERS_ENABLED`

主要使用：

- 市场异动发现。
- 行情权限和 OAuth token 状态。

相关模块：

- `server/stock/longbridge.py`
- `server/alerts/market_movers.py`
- `server/capabilities/registry.py`

### Regulatory Sources

#### SEC EDGAR

配置：

- `SEC_USER_AGENT`
- `SEC_ALERT_FORMS`

主要使用：

- ticker/CIK 映射。
- EDGAR submissions。
- 监管申报告警。

相关模块：

- `server/alerts/regulatory.py`

#### openFDA

配置：

- `FDA_ALERT_ENABLED`
- `FDA_ALERT_CATEGORIES`
- `FDA_ALERT_KEYWORDS`
- `FDA_ALERT_CLASSIFICATIONS`

主要使用：

- drug/device/food enforcement。
- recall 相关告警。

相关模块：

- `server/alerts/regulatory.py`

### Social Sources

#### X/Twitter GraphQL

配置：

- `TWITTER_AUTH_TOKENS`

主要使用：

- 拉取用户时间线。
- 历史分页 cursor。

相关模块：

- `server/social/twitter_graphql.py`
- `server/social/monitor.py`

#### vxTwitter

配置：

- 无强 token 配置。

主要使用：

- Twitter fallback。
- 单条推文详情补全。

相关模块：

- `server/social/monitor.py`

### LLM and Agent Sources

#### DeepSeek OpenAI-compatible Chat

配置：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`

主要使用：

- 推文摘要、翻译、情绪、优先级。
- 轻量自然语言处理。
- 日报摘要。

相关模块：

- `server/llm/client.py`
- `server/social/processor.py`
- `server/social/digest.py`

#### Claude Agent SDK Runtime

配置：

- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_MODEL`
- `AGENT_MAX_TURNS`

主要使用：

- 多步研究。
- 调用 WebSearch、WebFetch 和 Reveal MCP tools。

相关模块：

- `server/research/claude_sdk_runtime.py`
- `server/research/service.py`
- `server/research/sdk_mcp.py`
- `server/mcp.py`

### Communication Sources

#### Feishu

配置：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_ADMIN_CHAT_ID`
- `FEISHU_ENABLE_WS`

主要使用：

- Bot 命令。
- 普通消息。
- 卡片推送。
- 话题内回复。
- HTTP callback / WebSocket 事件。

相关模块：

- `server/bot/feishu.py`
- `server/bot/feishu_markdown.py`
- `server/main.py`

#### Telegram

配置：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_CHAT_ID`

主要使用：

- Bot 命令。
- 普通消息。
- 管理员推送。

相关模块：

- `server/bot/telegram.py`
- `server/main.py`

### Storage Source

#### Database

配置：

- `DATABASE_URL`

主要使用：

- 所有业务状态、缓存、研究记录、交易记录和消息绑定。

相关模块：

- `server/db/engine.py`
- `server/db/models.py`

## 当前主要数据流

### IM 主动请求流

```text
Telegram/Feishu message
-> BotContext
-> CommandRouter / handle_plain_message
-> Capability function or Research service
-> DB / external data source / Agent
-> BotAdapter response
```

### Agent 研究流

```text
User request or bound reply
-> ResearchSession
-> Claude Agent SDK
-> WebSearch/WebFetch + Reveal MCP tools
-> ConversationMessage + ResearchSession.answer
-> ResearchProgressReporter
-> IM result card or Web response
```

### Twitter 监控流

```text
Scheduler twitter_monitor
-> TwitterState active accounts
-> X GraphQL or vxTwitter
-> SocialPost
-> TweetProcessor / LLM
-> noteworthy check
-> IM push
-> BotMessageBinding
```

### 股票观察流

```text
/stock add or Agent stock_watch_add
-> StockWatch
-> Scheduler stock_watch_price every 5 minutes
-> current price
-> compare with last_price
-> threshold hit
-> IM alert
```

### 常规告警流

```text
Scheduler alert_cycle
-> active tickers from trades/tracking/watchlist
-> price/volume/news checks
-> dedupe by ticker/severity
-> IM push
```

### 监管事件流

```text
Scheduler regulatory_alert_cycle
-> watched tickers + FDA keywords
-> SEC EDGAR / openFDA
-> RegulatoryEvent dedupe
-> IM push
```

### Longbridge 异动流

```text
Scheduler longbridge_market_movers
-> Longbridge anomaly API
-> MarketMoverEvent dedupe
-> IM push
```

### 定期报告流

```text
Scheduler daily_briefing / twitter_digest
-> DB + market data + social posts + research records
-> formatted report
-> IM push
```

### Web 工作台流

```text
Browser
-> server/web.py
-> SocialPost + ResearchSession + ConversationMessage
-> static app rendering
-> optional deep/ask research
```

## 当前系统的抽象分层

从当前实现可以把 Reveal 抽象成六层：

```text
1. Interface Layer
   Feishu / Telegram / Web / MCP

2. Interaction and Capability Layer
   CommandRouter / planner / CapabilitySpec / MCP tools

3. Intelligence Layer
   LLM processing / Agent research / scoring

4. Domain Modules
   Social / Market / Alerts / Reports / Journal

5. Persistence Layer
   SQLAlchemy models / DB engine

6. Runtime Layer
   FastAPI lifecycle / scheduler / settings
```

也可以按系统行为分成五类：

```text
Monitor      自动采集外部变化
Alert        主动打断用户
Interactive  用户主动查询或配置
Research     多步分析和追问
Report       定期聚合总结
```

当前代码已经具备这些概念，只是部分概念还没有被显式命名为统一抽象。
