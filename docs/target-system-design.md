# Reveal 目标系统设计

这份文档定义 Reveal 的目标系统设计：从 model 到 system design，再到 execution。目标是在现有代码基础上建立清晰、稳定、可扩展的实现，而不是重写系统或引入重型框架。

## 设计原则

### 1. Explicit contracts over implicit coupling

当前系统已经有能力、告警、研究、报告、数据源等概念，但部分概念只存在于代码路径中。目标设计要求把跨模块协作的概念显式化，例如事件、交互线程、告警投递、任务运行状态。

### 2. Keep domain records source-specific

不要一开始把 `SocialPost`、`RegulatoryEvent`、`MarketMoverEvent` 全部迁到一个大表。源数据各有结构，保留源特定表更简单，也更利于调试。

统一性放在读取和交互层：

```text
Source-specific Domain Record -> EventItem projection -> Web/IM/Research
```

### 3. State is data

用户能看到的状态应该尽量入库：

- 哪条事件推送过。
- 推送到了哪个 chat。
- 哪条 IM 消息绑定到了哪个事件或研究线程。
- 哪个后台 job 最近运行失败。

不应该只存在于日志里。

### 4. Runtime modules stay simple

不引入插件框架，不要求所有模块继承统一基类。模块目录仍然按业务组织。新增 registry 只是描述系统，不接管执行。

### 5. One capability, many interfaces

Slash command、Agent MCP、Web API 应复用同一批 capability/service 函数。接口层不重复业务逻辑。

### 6. Alert is separate from Event

事件是事实，告警是投递决策。

```text
Event = what happened
AlertDelivery = who was notified, when, via which channel, with what message id
```

同一个事件可以不推送，也可以推送到多个平台或多个 chat。

## Target Architecture

```text
External Data Sources
  -> Source Clients
  -> Domain Modules
  -> Domain Records
  -> Event Feed Projection
  -> Interaction Thread / Research / Alert Delivery
  -> Feishu / Telegram / Web Workbench / Reports
```

系统分为七层：

```text
1. Interface Layer
   Feishu / Telegram / Web / MCP

2. Interaction Layer
   message routing / thread binding / card rendering

3. Capability Layer
   user-facing capability catalog and reusable service functions

4. Intelligence Layer
   LLM processing / Agent research / scoring

5. Domain Modules
   social / market / alerts / reports / journal

6. State Layer
   source records / threads / deliveries / job runs

7. Runtime Layer
   scheduler / settings / app lifecycle
```

Dependency direction:

```text
Interface -> Interaction -> Capability -> Domain -> State
Research  -> Capability -> Domain -> State
Reports   -> Domain -> State
Scheduler -> Domain jobs -> State
```

Domain modules should not import Feishu/Telegram adapters directly except through explicit delivery boundaries.

## Model Design

目标模型分三类：

1. Static catalog models：描述系统结构，不入库。
2. Persistent state models：描述用户和系统运行状态，入库。
3. Runtime DTOs：模块之间传递的结构化对象，不一定入库。

## Static Catalog Models

### DataSourceSpec

描述外部事实来源。

```text
id                 stable id, e.g. market.longbridge
title              display name
kind               market_data / regulatory / social / llm / bot / database
config_keys        env keys used by this source
event_types        event types this source can produce
owner_modules      system modules that consume this source
```

示例：

```text
id: market.longbridge
kind: market_data
event_types: market_mover
owner_modules: longbridge_market_movers
```

位置：

```text
server/system/catalog.py
```

### SystemModuleSpec

描述系统模块。它是元数据，不是插件接口。

```text
id                 twitter_monitor / regulatory_alerts / daily_briefing
title
module_type        monitor / alert / interactive / research / report / maintenance
owner_path         source file or package
schedule           interval / cron / on_demand
output_types       event / alert / report / research_session / message
data_sources       DataSourceSpec ids
capability_ids     CapabilitySpec ids
```

模块类型定义：

```text
monitor      持续采集外部变化并入库
alert        判断是否需要主动打断用户
interactive 由用户或 Agent 主动触发
research     多步 Agent 分析
report       定时聚合总结
maintenance 后台维护任务
```

### CapabilitySpec

继续沿用 `server/capabilities/registry.py` 的现有模型，作为用户能力事实源。

目标关系：

```text
CapabilitySpec -> SystemModuleSpec -> DataSourceSpec
```

也就是说：

- Capability 描述“用户能做什么”。
- SystemModule 描述“系统哪个模块执行这件事”。
- DataSource 描述“它依赖哪些外部数据”。

## Persistent State Models

### Existing Source Records

这些模型继续保留：

- `SocialPost`
- `TwitterState`
- `StockWatch`
- `RegulatoryEvent`
- `MarketMoverEvent`
- `Trade`
- `StockPick`
- `TrackingLog`
- `ResearchSession`
- `ConversationMessage`
- `ScoringWeights`

设计约束：

- 源特定数据保留在源特定表。
- 新的统一展示不通过迁移源表实现，而通过 projection 实现。

### InteractionThread

表示一次可追问、可展示、可关联研究的交互线程。

```text
id
platform              feishu / telegram / web
chat_id
root_message_id       IM 根消息；Web 可使用 synthetic id
source_type           twitter / regulatory / market_mover / stock_watch / agent / ticker
source_id             source-specific record id; nullable
source_key            stable string key; nullable, e.g. ticker:NVDA
research_session_id   nullable
status                active / completed / failed / muted / archived
created_at
updated_at
last_activity_at
```

Indexes:

```text
(platform, chat_id, root_message_id)
(source_type, source_id)
(research_session_id)
```

Invariants:

- 一个 IM root message 最多对应一个 active thread。
- 一个 thread 可以关联一个当前 research session。
- 同一个 source 可以有多个 thread，因为不同 chat 或 Web 用户可能独立讨论。

### BotMessageBinding

保留现有模型，并扩展成 message-to-thread 绑定。

现有字段：

```text
chat_id
message_id
source_type
source_id
```

新增字段：

```text
platform
thread_id
role                  root / source / status / result / user_reply / assistant_reply
```

兼容策略：

- 旧逻辑仍可通过 `source_type/source_id` fallback。
- 新逻辑优先通过 `thread_id` 解析上下文。

### AlertDelivery

表示一次实际推送。

```text
id
event_type            twitter / regulatory / market_mover / stock_watch / price / volume / news
event_source_id       source-specific id; nullable
event_key             stable dedupe key
thread_id             nullable
platform              feishu / telegram
chat_id
message_id            returned by adapter if available
status                pending / sent / failed / skipped
reason                why this alert was sent or skipped
severity              critical / warning / info
payload               JSON snapshot used to render the alert
error                 nullable
created_at
sent_at
updated_at
```

Indexes:

```text
(event_key, platform, chat_id)
(thread_id)
(created_at)
```

Invariants:

- 推送前先生成 deterministic `event_key`。
- 同一个 `event_key + platform + chat_id` 不重复发送，除非明确允许 repeat。
- AlertDelivery 记录投递结果，不替代源事件表。

### JobRun

表示后台任务的一次运行。

```text
id
job_id                twitter_monitor / alert_cycle / daily_briefing
module_id             SystemModuleSpec id
status                running / succeeded / failed / skipped
started_at
finished_at
duration_ms
summary               short human-readable result
metrics               JSON, e.g. fetched/new/pushed/errors
error                 nullable
```

Indexes:

```text
(job_id, started_at)
(status, started_at)
```

Invariants:

- 每个 scheduler job 运行时创建 JobRun。
- 正常跳过也记录为 `skipped`，例如非交易时段。
- Web 工作台和 `/status` 使用 JobRun，而不是要求用户翻 Docker logs。

## Runtime DTOs

### EventItem

统一事件展示对象，不直接入库。

```text
id                    source_type:source_id
source_type           twitter / regulatory / market_mover / stock_watch / price / volume / news
source_id
event_key
title
summary
body
tickers
priority             critical / warning / info / low
sentiment            bullish / bearish / neutral / unknown
occurred_at
created_at
url
raw_ref               pointer to source record
has_research
thread_id
delivery_status
```

位置：

```text
server/events/feed.py
```

职责：

- 把 `SocialPost`、`RegulatoryEvent`、`MarketMoverEvent`、`StockWatch` alert history 投影成统一列表。
- 给 Web 工作台、报告、Agent 上下文复用。

### AlertCandidate

告警判断后的候选对象。

```text
event_key
event_type
source_id
severity
title
summary
payload
target_chats
dedupe_policy
```

用途：

- Alert module 返回 candidates。
- Delivery service 决定是否投递和记录 AlertDelivery。

### CardPayload

adapter-neutral card dict。

位置：

```text
server/bot/cards/
```

设计：

- card 函数只接受结构化数据。
- Feishu adapter 负责 JSON 2.0 转换。
- Telegram adapter 可降级成 Markdown text。

## System Module Design

## 1. Data Sources

数据源模块负责外部 API 调用和原始响应标准化。

位置：

```text
server/stock/data.py
server/stock/longbridge.py
server/social/twitter_graphql.py
server/alerts/regulatory.py
server/llm/client.py
```

规则：

- 数据源函数不直接推送消息。
- 数据源函数不决定用户是否需要被打断。
- 数据源函数可以做 API-specific normalization。

## 2. Domain Modules

Domain module 负责业务事实入库和查询。

当前模块：

```text
server/social/*
server/stock/*
server/alerts/*
server/journal/*
server/research/*
```

规则：

- Domain module 可以读写数据库。
- Domain module 返回结构化 payload。
- Domain module 不直接构造平台专属卡片。

## 3. Event Feed

Event Feed 是 Web 工作台的统一读取层。

位置：

```text
server/events/feed.py
```

职责：

- 查询多个源表。
- 投影成 `EventItem`。
- 支持过滤：ticker、source_type、priority、query、time range。
- 关联最新 thread、research、delivery 状态。

不负责：

- 不负责采集。
- 不负责告警判断。
- 不负责 LLM 分析。

## 4. Interaction Service

Interaction service 负责消息线程和上下文恢复。

位置：

```text
server/interactions/threading.py
```

核心函数：

```text
get_or_create_thread_for_source(...)
create_agent_thread(...)
resolve_thread_by_message(...)
bind_message_to_thread(...)
attach_research_session(...)
touch_thread(...)
```

路由规则：

```text
reply_to_message_id
-> BotMessageBinding
-> InteractionThread
-> research session or source event
```

Fallback：

```text
BotMessageBinding without thread_id
-> source_type/source_id
-> create thread lazily
```

## 5. Delivery Service

Delivery service 负责推送和投递记录。

位置：

```text
server/delivery/service.py
```

核心函数：

```text
send_alert(adapter, candidate)
send_research_status(adapter, thread, status)
send_research_result(adapter, thread, result)
record_delivery(...)
```

职责：

- 执行 dedupe。
- 创建或更新 AlertDelivery。
- 调用 BotAdapter。
- 绑定返回的 message_id 到 InteractionThread。

## 6. Card Rendering

位置：

```text
server/bot/cards/
  research.py
  events.py
  stocks.py
  reports.py
```

原则：

- 纯函数。
- 不访问数据库。
- 不读取 settings。
- 不调用 BotAdapter。

## 7. Scheduler Runtime

当前 `server/scheduler.py` 保持轻量包装 APScheduler。

新增 job wrapper：

```text
run_recorded_job(job_id, module_id, func)
```

行为：

- 创建 JobRun。
- 执行业务函数。
- 记录 succeeded / failed / skipped。
- 捕获异常并重新抛给日志系统。

`server/main.py` 继续注册 jobs，但每个 job 包一层 recorded job。

## 8. Web Workbench

Web 工作台定位为系统状态和事件分析工作台。

API：

```text
GET /api/events
GET /api/events/{source_type}/{source_id}
GET /api/threads/{thread_id}
GET /api/research/{session_id}
GET /api/watchlist/stocks
GET /api/system/modules
GET /api/system/jobs
```

页面区域：

```text
Event Inbox
Event Detail
Research Thread
Watchlists
System Health
Job Runs
```

## Execution Design

## Phase 0: Catalog and Contracts

目标：先建立系统语言，不改变行为。

Files:

```text
server/system/catalog.py
server/events/types.py
server/bot/cards/
tests/test_system_catalog.py
tests/test_event_types.py
```

Deliverables:

- `DataSourceSpec`
- `SystemModuleSpec`
- `EventItem`
- card rendering module skeleton
- Web/API 暂不切换

Acceptance:

- `uv run pytest tests/test_system_catalog.py tests/test_event_types.py`
- `/tools` 和现有 capability catalog 不受影响。

## Phase 1: Interaction Thread

目标：让 IM 回复、研究结果、Web 详情能共享同一个 thread 概念。

Files:

```text
server/db/models.py
server/interactions/threading.py
server/bot/bindings.py
server/commands.py
tests/test_interaction_threading.py
tests/test_feishu_event.py
tests/test_research_service.py
```

Schema:

- add `InteractionThread`
- extend `BotMessageBinding`

Behavior:

- 新消息创建 agent thread。
- 回复绑定消息时优先解析 thread。
- 旧 source binding fallback 仍工作。
- research status/result 绑定到 thread。

Acceptance:

- 现有 Twitter reply-to-research 行为不回退。
- Feishu 话题内回复仍能继续研究。
- 无 thread 的旧消息仍能通过 source fallback。

## Phase 2: Event Feed

目标：Web 可以统一展示不同来源事件。

Files:

```text
server/events/feed.py
server/web.py
server/static/app.js
server/static/app.css
tests/test_event_feed.py
tests/test_web_api.py
```

Sources:

- `SocialPost`
- `RegulatoryEvent`
- `MarketMoverEvent`
- `AlertDelivery` if available

Behavior:

- `/api/events` 返回统一列表。
- `/api/events/{source_type}/{source_id}` 返回源详情。
- 现有 `/api/posts` 保留兼容。

Acceptance:

- Web 可看到 Twitter、SEC/FDA、Longbridge 事件。
- 事件详情能看到相关 research sessions。

## Phase 3: Alert Delivery

目标：主动推送可追踪、可去重、可在 Web 查看。

Files:

```text
server/db/models.py
server/delivery/service.py
server/alerts/*
server/social/monitor.py
server/stock/watchlist.py
tests/test_alert_delivery.py
tests/test_stock_watchlist.py
tests/test_market_movers.py
tests/test_regulatory_alerts.py
```

Schema:

- add `AlertDelivery`

Behavior:

- alert modules return `AlertCandidate` or equivalent payload。
- delivery service handles push, dedupe, message binding。
- existing direct push paths migrate gradually。

Acceptance:

- 同一个 event_key 不重复推送到同一 chat。
- 推送失败入库为 failed，并保留 error。
- Web 能显示某事件是否已推送。

## Phase 4: JobRun and System Health

目标：后台任务可观察。

Files:

```text
server/db/models.py
server/runtime/jobs.py
server/scheduler.py
server/main.py
server/web.py
tests/test_job_runs.py
```

Schema:

- add `JobRun`

Behavior:

- scheduler job 通过 recorded wrapper 执行。
- Web `/api/system/jobs` 展示最近运行状态。
- skipped 不是错误，例如非市场时间。

Acceptance:

- Docker logs 不再是唯一故障入口。
- Web 能看到最近 job 成功/失败/跳过。

## Phase 5: Web Workbench UI

目标：把 Web 从 Twitter Research Desk 升级为系统工作台。

Files:

```text
server/static/index.html
server/static/app.js
server/static/app.css
```

Views:

- Event Inbox
- Event Detail
- Research Thread
- Watchlist
- System Health

Acceptance:

- 用户可以从一个列表看到 Twitter、监管事件、市场异动和观察列表告警。
- 用户可以从事件进入研究线程。
- 用户可以看到数据源和 job 状态。

## Phase 6: Card Actions and Feedback

目标：让 IM 卡片变成轻交互入口。

Actions:

- 深入研究
- 加入观察
- 静音
- 有用 / 无用

可能新增模型：

```text
EventUserState
  event_key
  chat_id
  status read / archived / muted
  feedback useful / not_useful
```

这一步不是第一阶段必需项。只有当 AlertDelivery 和 EventFeed 稳定后再做。

## Target File Map

```text
server/
  system/
    catalog.py
  events/
    types.py
    feed.py
  interactions/
    threading.py
  delivery/
    service.py
  runtime/
    jobs.py
  bot/
    cards/
      research.py
      events.py
      stocks.py
      reports.py
```

Existing modules stay:

```text
server/social/*
server/stock/*
server/alerts/*
server/research/*
server/capabilities/*
server/journal/*
```

## Migration Strategy

### Backward compatibility

- 不删除现有 `/api/posts`。
- 不删除现有 `BotMessageBinding.source_type/source_id`。
- 不改变 slash command 名称。
- 不改变 MCP tool 名称。

### Data migration

SQLite/Postgres 当前没有 Alembic。迁移策略继续沿用 `create_all` 可创建新表，但生产库新增列需要手动兼容：

- 新增表风险低。
- 给旧表新增 nullable column 风险低。
- 不做 destructive migration。

### Rollout order

```text
1. Add new models and helpers, no behavior switch.
2. New behavior writes both old and new state.
3. Read path prefers new state, fallback old state.
4. Web starts consuming new EventFeed.
5. Direct push paths migrate to Delivery service.
```

## Testing Strategy

Unit tests:

- catalog shape
- EventItem projection
- InteractionThread create/resolve
- AlertDelivery dedupe
- JobRun status recording
- card rendering output shape

Integration tests:

- Feishu reply event -> thread -> research follow-up
- stock watch alert -> delivery -> binding
- market mover alert -> event feed -> Web API
- regulatory alert -> event feed -> Web API

Regression tests:

- existing slash commands still route
- existing MCP tools still return JSON/text
- existing `/api/posts` tests pass
- existing research session tests pass

## Best-practice Checklist

This target design satisfies these constraints:

- Stable domain records are source-specific.
- Cross-source display uses projection, not premature unification.
- User-visible state is persisted.
- Interfaces reuse capability functions.
- Agent tools remain whitelisted and controlled.
- Delivery is separated from event detection.
- Scheduler execution is observable.
- IM thread routing has a first-class model.
- Web workbench reads system state instead of duplicating business logic.
- New abstractions are metadata/helpers, not heavyweight frameworks.

## End State

After implementation, Reveal should behave like this:

```text
Data source produces facts
-> domain module stores source record
-> EventFeed exposes unified event
-> alert module may create AlertCandidate
-> Delivery service pushes and records AlertDelivery
-> InteractionThread binds user replies and research
-> Web shows event, thread, delivery, research, and job status
```

The system remains small, but the important boundaries become explicit.
