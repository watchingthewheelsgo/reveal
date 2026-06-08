# Reveal 交互与 Web 工作台重构设计

## 目标

把 Reveal 从“多个功能各自发消息”收敛成一套简单、可扩展的交互模型：

- IM 负责主动提醒、快捷操作、话题内追问。
- Web 工作台负责统一事件列表、研究状态、观察列表和系统可见性。
- Agent 只围绕明确的事件或会话上下文工作，避免散落的临时状态。

本设计优先复用现有模型和能力，不引入重型消息队列、复杂工作流引擎或过早的通用事件表。

## 当前实现

### IM 入口

当前统一入口在 `server/bot/base.py`：

- `BotContext` 归一化 `chat_id`、`user_id`、`text`、`message_id`、`reply_to_message_id`。
- `BotAdapter` 抽象 Telegram / Feishu 的发送消息、发送卡片、回复话题、编辑消息。
- `CommandRouter` 负责 slash command 和普通消息转发。

普通消息处理在 `server/commands.py`：

- `handle_plain_message()` 收到普通文本。
- 如果是回复消息，先调用 `_route_bound_reply()`。
- 如果回复消息绑定到 `research_session`，继续该研究会话。
- 如果回复消息绑定到 `twitter`，创建或复用 Twitter 研究话题。
- 如果没有命中绑定，则创建新的 Agent session。

### 消息绑定

当前绑定在 `server/bot/bindings.py`：

- `bind_message_to_source(chat_id, message_id, source_type, source_id)`
- `resolve_message_binding(chat_id, message_id)`

数据库模型是 `BotMessageBinding`：

- `chat_id`
- `message_id`
- `source_type`
- `source_id`

这个模型能解决“某条 IM 消息对应哪个对象”，但表达不了完整交互线程，例如 root 消息、状态卡、结果卡、用户回复、研究会话之间的关系。

### 研究进度

当前研究进度在 `server/research/progress.py`：

- `ResearchProgressReporter.start()` 发送状态卡。
- `on_progress()` 更新工具步骤。
- `finish()` 发送结果卡。
- `error()` 更新失败状态。

卡片本身由局部函数拼装，和业务流程耦合较紧。

### Web 工作台

当前 Web 在 `server/web.py` 和 `server/static/*`：

- `/api/posts` 展示 Twitter/X 更新。
- `/api/posts/{post_id}` 展示单条 post 和相关 research sessions。
- `/api/posts/{post_id}/deep` 触发深度研究。
- `/api/posts/{post_id}/ask` 继续追问。

Web 目前是 Twitter Research Desk，还不是统一市场事件工作台。

### 已有数据模型

当前已经有这些可复用模型：

- `SocialPost`
- `ResearchSession`
- `ConversationMessage`
- `BotMessageBinding`
- `StockWatch`
- `RegulatoryEvent`
- `MarketMoverEvent`

第一阶段不需要把这些全部迁移到一个大表。更好的做法是先建立统一读取层，让 Web 可以用一致结构展示它们。

### 已有能力与数据源描述

当前 `server/capabilities/registry.py` 已经有两类描述：

- `CapabilitySpec`：描述用户能使用的能力，例如股票观察、Twitter 关注、告警、日报、深度研究。
- `ExternalServiceSpec`：描述外部服务，例如 Finnhub、Longbridge、SEC EDGAR、openFDA、Twitter GraphQL、vxTwitter、LLM、数据库、Bot。

这已经是一个能力目录，但还不是完整的数据源抽象。它能回答“系统依赖什么服务、暴露什么能力”，不能统一表达：

- 数据源当前是否健康。
- 该数据源产出哪些事件类型。
- 哪些系统模块正在消费该数据源。
- 最近一次采集、告警、报告的运行状态。

### 已有系统模块形态

当前系统模块已经存在，但边界是代码约定，不是显式抽象：

- Monitor：`server/social/monitor.py`，定时拉 Twitter/X，入库并推送。
- Alert：`server/alerts/*`、`server/stock/watchlist.py`，检查价格、成交量、新闻、SEC/FDA、Longbridge 异动。
- Interactive：`server/commands.py`、`server/mcp.py`、`server/capabilities/*`，处理 slash command、自然语言 Agent、MCP tools。
- Regular Report：`server/briefing.py`、`server/social/digest.py`，生成市场简报和 Twitter 日报。
- Scheduler：`server/scheduler.py` 和 `server/main.py` 中注册的 jobs，负责周期触发。

这些模块应被 Web 工作台展示，但不需要一开始重写成复杂插件系统。

## 主要问题

1. **交互线程隐式存在**

用户在 Feishu 话题里追问、Agent 返回状态卡、结果卡继续绑定研究会话，这些行为已经形成了“线程”，但数据库里没有线程对象。

2. **事件展示没有统一入口**

Twitter post、SEC/FDA 事件、市场异动、股票观察提醒分别存储。Web 只能展示 Twitter，后续扩展会变成每类事件各写一套页面。

3. **卡片构造分散**

研究卡、Twitter 卡、股票提醒卡分别拼 dict。Feishu JSON 2.0 已经统一，但业务层仍然知道太多卡片细节。

4. **后台任务状态不够可见**

很多任务通过后台 task 或 scheduler 运行，用户需要靠 Docker logs 判断失败原因。Web 应该展示最近运行、失败、数据源健康状态。

## Proposed Design

### 1. InteractionThread

新增一个轻量表 `interaction_threads`，表达一次可追问、可展示、可关联研究的交互线程。

建议字段：

```text
id
platform              feishu / telegram / web
chat_id
root_message_id       IM 根消息或 Web 虚拟 root
source_type           twitter / stock_watch / regulatory / market_mover / agent
source_id             对应源对象 id，可为空
research_session_id   当前关联研究会话，可为空
status                active / completed / failed / muted
created_at
updated_at
last_activity_at
```

设计约束：

- `source_type + source_id` 是多态引用，不强制数据库外键。
- `ResearchSession` 继续作为研究上下文，不被替代。
- `BotMessageBinding` 继续保留，用于通过 IM message id 快速反查 thread。

`BotMessageBinding` 增加两个可选字段：

```text
thread_id
role                  root / status / result / user_reply / source
```

兼容策略：

- 旧代码仍可用 `source_type/source_id`。
- 新代码优先走 `thread_id`。
- 迁移时不要求回填全部历史数据。

### 2. EventFeed Projection

第一阶段不新增大而全的 `market_events` 表，先增加一个统一读取层：

```text
server/events/feed.py
```

它把现有模型投影成统一结构：

```text
EventItem
  id                 `${source_type}:${source_id}`
  source_type        twitter / regulatory / market_mover / stock_watch
  source_id
  title
  summary
  tickers
  priority
  sentiment
  occurred_at
  url
  has_research
  thread_id
```

这样 Web 可以先展示统一 Inbox，而不破坏现有存储。

后续如果需要 read/unread、archive、feedback，可以单独加 `event_user_states` 或 `alert_deliveries`，不急着重构全部源表。

### 3. Thread-first Message Routing

调整普通消息路由：

当前：

```text
reply_to_message_id -> BotMessageBinding -> source_type/source_id -> 特判 twitter/research
```

改为：

```text
reply_to_message_id -> BotMessageBinding -> InteractionThread -> handler
```

处理规则：

- thread 有 `research_session_id`：继续该研究会话。
- thread 是 `twitter/regulatory/market_mover` 且没有研究会话：先创建研究会话，再绑定 thread。
- thread 是 `agent`：继续 agent session。
- 无 thread 的顶层消息：创建新的 `agent` thread 和 `ResearchSession`。

这样 Twitter 只是普通事件类型之一，SEC/FDA、市场异动、价格告警都可以复用同一套追问逻辑。

### 4. Card Rendering Boundary

新增卡片模块：

```text
server/bot/cards/
  __init__.py
  research.py
  events.py
  stocks.py
```

原则：

- 业务层只传结构化数据。
- 卡片模块负责生成 adapter-neutral card dict。
- Feishu adapter 继续负责把 legacy card 或 sections 转成 JSON 2.0。
- 不引入复杂 Card DSL，保持纯函数。

第一批收敛：

- research status card
- research result card
- event alert card
- stock watch alert card

### 5. Web Workbench

Web 从 Twitter-only 改成事件工作台，先做最小有用版本。

新增 API：

```text
GET /api/events
GET /api/events/{source_type}/{source_id}
GET /api/threads/{thread_id}
GET /api/watchlist/stocks
GET /api/system/jobs
```

第一版页面展示：

- 统一事件 Inbox
- 事件详情
- 相关研究 session 和消息
- 股票观察列表
- 最近 scheduler/job 状态
- 数据源健康摘要

保留现有 `/api/posts`，避免一次性破坏当前 UI。

### 6. DataSource and SystemModule Catalog

为了让 Web 工作台能展示“数据来自哪里、哪个模块在跑、状态是否正常”，增加两个轻量 registry。

建议新增：

```text
server/system/catalog.py
```

数据源描述：

```text
DataSourceSpec
  id                 market.finnhub / market.longbridge / sec.edgar / fda.openfda / social.x_graphql
  title
  kind               market_data / regulatory / social / llm / bot / database
  config_keys
  event_types        可能产出的事件类型
  owner_modules      消费它的系统模块
```

系统模块描述：

```text
SystemModuleSpec
  id                 twitter_monitor / regulatory_alerts / stock_watch_price / daily_briefing
  title
  module_type        monitor / alert / interactive / report / research / maintenance
  owner_path
  schedule           interval / cron / on_demand
  output_types       event / alert / report / research_session / message
  data_sources
  capability_ids
```

设计原则：

- 这是元数据 registry，不是运行时插件框架。
- 不要求所有模块实现同一个抽象基类。
- Web 和 `/status` 可以读取它展示系统结构。
- 后续如果需要 job health，再加运行态表，不混进静态 registry。

模块类型定义：

```text
monitor      持续采集外部变化并入库，例如 Twitter monitor。
alert        基于规则判断是否推送，例如价格/成交量/SEC/FDA/Longbridge 异动。
interactive 由用户或 Agent 触发，例如 slash command、MCP tool、Web ask。
report       定时聚合已有状态，例如 daily briefing、Twitter digest。
research     多步 Agent 分析，例如 ticker research、event deep research。
maintenance 后台维护任务，例如 tracking update、feedback apply。
```

数据流约束：

```text
DataSource -> Monitor/Alert/Interactive -> Domain Model -> EventFeed -> IM/Web/Report
```

其中：

- 数据源只描述外部依赖，不负责业务判断。
- Monitor 负责发现和持久化新事实。
- Alert 负责判断是否需要打断用户。
- Interactive 负责处理用户明确请求。
- Report 负责定期汇总，不产生高优先级打断。
- EventFeed 是 Web 展示层的统一读取投影。

## Implementation Plan

### Phase 1: 线程抽象

- 增加 `InteractionThread` 模型。
- 扩展 `BotMessageBinding`，支持 `thread_id` 和 `role`。
- 新增 `server/interactions/threading.py`：
  - `get_or_create_thread_for_source()`
  - `bind_message_to_thread()`
  - `resolve_thread_by_message()`
  - `attach_research_session()`
- `commands.py` 先用新 helper，保留旧 binding fallback。
- 增加单元测试覆盖 reply routing。

### Phase 1.5: 系统目录

- 新增 `server/system/catalog.py`。
- 把现有 `CapabilitySpec` / `ExternalServiceSpec` 与 `SystemModuleSpec` 关联起来。
- 增加 `/api/system/modules`，给 Web 展示模块类型、数据源、调度方式。
- 不改变现有 scheduler 注册方式。

### Phase 2: Web 事件读取层

- 新增 `server/events/feed.py`。
- 把 `SocialPost`、`RegulatoryEvent`、`MarketMoverEvent` 投影为 `EventItem`。
- 增加 `/api/events`。
- Web 左侧列表切换为 Event Inbox。
- 保留 Twitter detail view，逐步扩展其他事件 detail。

### Phase 3: 卡片收敛

- 把研究卡迁移到 `server/bot/cards/research.py`。
- 把价格提醒、Twitter 推送卡迁移到 `server/bot/cards/events.py` / `stocks.py`。
- 卡片底部统一加：
  - 继续回复本话题即可追问
  - 事件 ID / 线程 ID

### Phase 4: 操作与反馈

在 Feishu 卡片上增加少量高价值动作：

- 深入研究
- 加入观察
- 静音
- 有用 / 无用

这一步再补 `event_user_states` 或 `alert_feedback`，不要提前添加。

## Non-goals

第一轮不做：

- 不引入 Celery/RQ 等队列。
- 不把所有事件迁移到一个大表。
- 不重写 Feishu/Telegram adapter。
- 不把 Web 改成复杂前端框架。
- 不重写 ResearchSession。

## Expected Result

重构完成后：

- 任意推送事件都可以自然开话题追问。
- IM 消息、研究会话、Web 详情页能定位到同一个 thread。
- Web 可以统一展示 Twitter、SEC/FDA、市场异动和股票观察。
- 新增事件源时，只需要提供 EventFeed 投影和可选卡片渲染，不需要复制一整套交互逻辑。
