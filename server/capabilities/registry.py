"""System capability catalog.

This module is the single source of truth for Reveal's product capabilities.
Concrete implementations live in domain modules; adapters should route to
capabilities instead of reimplementing behavior.
"""

from dataclasses import dataclass
from typing import Literal

CapabilityKind = Literal["system", "tool", "skill", "workflow"]
ServiceKind = Literal["mcp", "external_api", "llm", "bot", "database", "builtin", "scheduler"]

BUILTIN_AGENT_TOOLS = ["WebSearch", "WebFetch"]
DISALLOWED_LOCAL_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    title: str
    kind: CapabilityKind
    group: str
    description: str
    slash_commands: tuple[str, ...] = ()
    natural_examples: tuple[str, ...] = ()
    required_args: tuple[str, ...] = ()
    agent_tools: tuple[str, ...] = ()
    external_services: tuple[str, ...] = ()
    usage: str = ""
    side_effects: str = ""
    notes: str = ""

    @property
    def agent_tool(self) -> str | None:
        """Backward-compatible primary MCP tool name."""
        return self.agent_tools[0] if self.agent_tools else None


@dataclass(frozen=True)
class ExternalServiceSpec:
    id: str
    title: str
    kind: ServiceKind
    description: str
    config_keys: tuple[str, ...] = ()
    notes: str = ""


EXTERNAL_SERVICES: tuple[ExternalServiceSpec, ...] = (
    ExternalServiceSpec(
        id="builtin.websearch",
        title="Claude Agent WebSearch",
        kind="builtin",
        description="Agent SDK 内置联网搜索工具，用于补充最新事实和网页证据。",
    ),
    ExternalServiceSpec(
        id="builtin.webfetch",
        title="Claude Agent WebFetch",
        kind="builtin",
        description="Agent SDK 内置网页读取工具，用于抓取 URL 内容。",
    ),
    ExternalServiceSpec(
        id="mcp.reveal",
        title="Reveal MCP adapter",
        kind="mcp",
        description=(
            "Reveal 内部能力的 MCP 适配层；Agent 默认使用 in-process SDK server，"
            "stdio server 仅用于调试/兼容。"
        ),
    ),
    ExternalServiceSpec(
        id="database.app",
        title="Reveal database",
        kind="database",
        description="SQLite/Postgres 持久化交易、Twitter 缓存、研究线程和消息绑定。",
        config_keys=("DATABASE_URL",),
    ),
    ExternalServiceSpec(
        id="bot.feishu",
        title="Feishu/Lark Bot API",
        kind="bot",
        description="飞书消息、卡片、图片上传、WebSocket 事件和 HTTP callback。",
        config_keys=("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_ADMIN_CHAT_ID"),
    ),
    ExternalServiceSpec(
        id="bot.telegram",
        title="Telegram Bot API",
        kind="bot",
        description="Telegram 命令、普通消息和管理员推送。",
        config_keys=("TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_CHAT_ID"),
    ),
    ExternalServiceSpec(
        id="llm.deepseek_chat",
        title="DeepSeek OpenAI-compatible chat",
        kind="llm",
        description="轻量意图分类、推文摘要、普通问答和日报总结。",
        config_keys=("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"),
    ),
    ExternalServiceSpec(
        id="llm.deepseek_agent",
        title="DeepSeek Anthropic-compatible Agent runtime",
        kind="llm",
        description=(
            "Claude Agent SDK 通过 DeepSeek Anthropic-compatible endpoint 执行多轮工具循环。"
        ),
        config_keys=("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"),
    ),
    ExternalServiceSpec(
        id="market.finnhub",
        title="Finnhub API",
        kind="external_api",
        description="实时报价和公司新闻的优先行情源。",
        config_keys=("FINNHUB_API_KEY", "FINNHUB_BASE_URL"),
    ),
    ExternalServiceSpec(
        id="market.yfinance",
        title="Yahoo Finance via yfinance",
        kind="external_api",
        description="历史行情、技术指标和 Finnhub fallback 行情源。",
    ),
    ExternalServiceSpec(
        id="market.longbridge",
        title="Longbridge OpenAPI",
        kind="external_api",
        description="OAuth Bearer API，用于美股/港股等行情异动发现和行情权限状态。",
        config_keys=("LONGBRIDGE_ENABLED", "LONGBRIDGE_OAUTH_TOKEN_PATH"),
    ),
    ExternalServiceSpec(
        id="sec.edgar",
        title="SEC EDGAR APIs",
        kind="external_api",
        description="SEC 公司 ticker/CIK 映射和 EDGAR submissions JSON，用于监管申报事件监控。",
        config_keys=("SEC_USER_AGENT", "SEC_ALERT_FORMS"),
    ),
    ExternalServiceSpec(
        id="fda.openfda",
        title="openFDA APIs",
        kind="external_api",
        description=(
            "FDA drug/device/food enforcement endpoints，用于 recall/enforcement 事件监控。"
        ),
        config_keys=("FDA_ALERT_ENABLED", "FDA_ALERT_CATEGORIES", "FDA_ALERT_KEYWORDS"),
    ),
    ExternalServiceSpec(
        id="social.x_graphql",
        title="X/Twitter GraphQL",
        kind="external_api",
        description="配置 auth token 后获取用户时间线和历史分页 cursor。",
        config_keys=("TWITTER_AUTH_TOKENS",),
    ),
    ExternalServiceSpec(
        id="social.vxtwitter",
        title="vxTwitter API",
        kind="external_api",
        description="无 X token 时的公开 Twitter/X fallback；也用于单条推文详情补全。",
    ),
    ExternalServiceSpec(
        id="scheduler.apscheduler",
        title="APScheduler",
        kind="scheduler",
        description="定时选股、Twitter 监控、告警和日报任务。",
        config_keys=(
            "SCHEDULER_TIMEZONE",
            "TWITTER_MONITOR_INTERVAL",
            "DAILY_PICK_TIME",
            "TWITTER_DIGEST_TIME",
        ),
    ),
)


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="system.help",
        title="帮助",
        kind="system",
        group="系统",
        description="查看 Reveal 可用命令。",
        slash_commands=("help",),
        natural_examples=("帮助", "怎么用", "有哪些命令"),
        usage="/help",
    ),
    CapabilitySpec(
        id="system.tools",
        title="能力目录",
        kind="system",
        group="系统",
        description="查看系统工具、技能和自然语言示例。",
        natural_examples=("有哪些工具", "你能做什么", "有哪些技能"),
        agent_tools=("mcp__reveal__capability_catalog",),
        external_services=("mcp.reveal",),
        usage="自然语言: 有哪些工具 / 你能做什么",
    ),
    CapabilitySpec(
        id="system.status",
        title="系统状态",
        kind="tool",
        group="系统",
        description="查看 bot、LLM、数据库、行情源配置状态。",
        slash_commands=("status",),
        natural_examples=("系统状态", "服务状态"),
        agent_tools=("mcp__reveal__system_status",),
        external_services=(
            "database.app",
            "bot.feishu",
            "bot.telegram",
            "llm.deepseek_chat",
            "llm.deepseek_agent",
            "market.finnhub",
        ),
        usage="/status",
    ),
    CapabilitySpec(
        id="stock.pick",
        title="每日选股",
        kind="workflow",
        group="股票",
        description="扫描市场并推荐标的。",
        natural_examples=("今日选股", "推荐股票"),
        external_services=("market.finnhub", "market.yfinance", "database.app"),
        usage="自动每日执行；也可用自然语言让 Agent 生成选股建议。",
        side_effects="会写入/更新每日选股和追踪记录。",
    ),
    CapabilitySpec(
        id="stock.quote",
        title="实时报价",
        kind="tool",
        group="股票",
        description="查询股票现价、涨跌幅、成交量等。",
        natural_examples=("NVDA 现在多少钱", "查一下 MRVL 报价"),
        required_args=("ticker",),
        agent_tools=("mcp__reveal__stock_quote",),
        external_services=("market.finnhub", "market.yfinance", "mcp.reveal"),
        usage="自然语言: NVDA 现在多少钱",
    ),
    CapabilitySpec(
        id="stock.technical",
        title="技术指标",
        kind="tool",
        group="股票",
        description="查询 RSI、均线、量比、52 周高低点等。",
        natural_examples=("MRVL 技术指标", "看一下 NVDA RSI 和均线"),
        required_args=("ticker",),
        agent_tools=("mcp__reveal__technical_analysis",),
        external_services=("market.yfinance", "mcp.reveal"),
        usage="自然语言: 看一下 NVDA 技术指标",
    ),
    CapabilitySpec(
        id="stock.news",
        title="股票新闻",
        kind="tool",
        group="股票",
        description="查询公司最近新闻。",
        natural_examples=("查一下 MRVL 新闻", "NVDA 最近有什么新闻"),
        required_args=("ticker",),
        agent_tools=("mcp__reveal__stock_news",),
        external_services=("market.finnhub", "mcp.reveal"),
        usage="自然语言: NVDA 最近有什么新闻",
    ),
    CapabilitySpec(
        id="stock.score",
        title="多因子评分",
        kind="tool",
        group="股票",
        description="对股票做技术、基本面、新闻情绪和板块评分。",
        natural_examples=("给 MRVL 打分", "NVDA 评分"),
        required_args=("ticker",),
        agent_tools=("mcp__reveal__stock_score",),
        external_services=("market.yfinance", "mcp.reveal"),
        usage="自然语言: 给 NVDA 打分",
    ),
    CapabilitySpec(
        id="stock.track",
        title="追踪标的",
        kind="tool",
        group="股票",
        description="查看正在追踪的标的表现。",
        natural_examples=("查看追踪标的", "MRVL 追踪情况"),
        agent_tools=("mcp__reveal__tracking_report",),
        external_services=("market.yfinance", "database.app", "mcp.reveal"),
        usage="自然语言: 查看追踪标的 / MRVL 追踪情况",
    ),
    CapabilitySpec(
        id="stock.watch",
        title="股票观察列表",
        kind="workflow",
        group="股票",
        description="按会话添加/移除股票观察；每 5 分钟比较上次检查价格，超过阈值推送到原会话。",
        slash_commands=("stock",),
        natural_examples=("把 NVDA 加入股票观察列表", "移除 MRVL 股票观察", "查看股票观察列表"),
        agent_tools=(
            "mcp__reveal__stock_watch_list",
            "mcp__reveal__stock_watch_add",
            "mcp__reveal__stock_watch_remove",
        ),
        external_services=("database.app", "market.finnhub", "market.yfinance", "mcp.reveal"),
        usage="/stock list|add TICKER [阈值%]|del TICKER",
        side_effects="add/remove 会更新当前会话的股票观察列表；后台每 5 分钟检查价格并推送异动。",
    ),
    CapabilitySpec(
        id="market.movers",
        title="Longbridge 市场异动",
        kind="workflow",
        group="股票",
        description="使用 Longbridge anomaly API 发现市场异动，入库去重并主动推送到管理员会话。",
        slash_commands=("movers",),
        natural_examples=("检查美股异动", "最近有哪些市场异动", "Longbridge 异动状态"),
        agent_tools=(
            "mcp__reveal__market_movers_status",
            "mcp__reveal__market_movers_check",
            "mcp__reveal__market_movers_recent",
        ),
        external_services=("market.longbridge", "database.app", "mcp.reveal"),
        usage="/movers check|recent [数量]|status",
        side_effects="后台每 5 分钟检查 Longbridge 异动；新事件会推送到 admin chat。",
    ),
    CapabilitySpec(
        id="portfolio.view",
        title="当前持仓",
        kind="tool",
        group="交易",
        description="查看未平仓持仓和浮盈。",
        slash_commands=("portfolio",),
        natural_examples=("我的持仓", "当前仓位", "portfolio"),
        agent_tools=("mcp__reveal__portfolio",),
        external_services=("database.app", "market.finnhub", "market.yfinance", "mcp.reveal"),
        usage="/portfolio",
    ),
    CapabilitySpec(
        id="portfolio.holding_marker",
        title="持仓关注标记",
        kind="workflow",
        group="交易",
        description="只记录某 ticker 需要按持仓影响来关注，不保存数量或成本，不代表真实交易。",
        natural_examples=("我持有 NVDA，后面相关消息提醒我", "记一下我有 TSLA，不用记录数量"),
        required_args=("ticker",),
        agent_tools=(
            "mcp__reveal__portfolio_holding_add",
            "mcp__reveal__portfolio_holding_remove",
        ),
        external_services=("database.app", "mcp.reveal"),
        usage="自然语言: 我持有 TICKER，后面消息关注它；移除 TICKER 持仓关注标记",
        side_effects="会写入或关闭一个不含数量/成本的持仓关注标记；只在用户明确要求时执行。",
    ),
    CapabilitySpec(
        id="research.history",
        title="历史研究",
        kind="tool",
        group="研究",
        description="查询某只股票过去的研究结论。",
        natural_examples=("MRVL 之前研究过什么", "查 NVDA 历史研究"),
        required_args=("ticker",),
        agent_tools=("mcp__reveal__research_history",),
        external_services=("database.app", "mcp.reveal"),
        usage="自然语言: 查 NVDA 历史研究",
    ),
    CapabilitySpec(
        id="research.ticker",
        title="股票深度研究",
        kind="skill",
        group="研究",
        description="用 Agent loop 调用内部工具和 WebSearch/WebFetch 做多轮研究。",
        slash_commands=("research",),
        natural_examples=("深度研究 MRVL", "分析 NVDA 最新情况"),
        required_args=("query",),
        external_services=(
            "llm.deepseek_agent",
            "builtin.websearch",
            "builtin.webfetch",
            "mcp.reveal",
            "database.app",
        ),
        usage="/research TICKER|latest|POST_ID|QUESTION",
        side_effects="会创建或更新研究线程。",
    ),
    CapabilitySpec(
        id="research.tweet",
        title="推文研究",
        kind="skill",
        group="研究",
        description="基于 Twitter/X 更新建立研究线程或主动深挖。",
        slash_commands=("topic",),
        natural_examples=("深挖最新推文", "基于这条消息分析影响"),
        external_services=(
            "llm.deepseek_agent",
            "builtin.websearch",
            "builtin.webfetch",
            "mcp.reveal",
            "database.app",
        ),
        usage="/topic status|start|summary|stop；也可直接回复研究结果继续追问",
        side_effects="会创建、恢复或更新研究线程。",
    ),
    CapabilitySpec(
        id="research.market_skills",
        title="Market Skills",
        kind="skill",
        group="研究",
        description="查看内置市场分析视角，包括显式 bias、触发条件和证据策略。",
        natural_examples=("有哪些 market skills", "有哪些分析视角", "查看 bias agent 技能"),
        agent_tools=("mcp__reveal__market_skill_catalog",),
        external_services=("mcp.reveal",),
        usage="Agent 可调用 market_skill_catalog 查看可用分析视角。",
    ),
    CapabilitySpec(
        id="twitter.watch",
        title="Twitter 关注列表",
        kind="workflow",
        group="Twitter",
        description="添加、移除、检查关注账号；Agent 添加账号时会返回最近推文供用户立即查看。",
        slash_commands=("x",),
        natural_examples=("把 @OwenCarter_k 加到 watch list", "当前关注了哪些推特账号"),
        agent_tools=(
            "mcp__reveal__twitter_watch_list",
            "mcp__reveal__twitter_watch_add",
            "mcp__reveal__twitter_watch_remove",
        ),
        external_services=("database.app", "social.x_graphql", "social.vxtwitter", "mcp.reveal"),
        usage="/x list|add @user|del @user|check",
        side_effects="add/remove 会更新 Twitter 关注状态。",
    ),
    CapabilitySpec(
        id="twitter.digest",
        title="Twitter 日报",
        kind="workflow",
        group="Twitter",
        description="生成关注账号日报或单账号总结。",
        natural_examples=("推特日报", "@OwenCarter_k 昨天发了什么"),
        agent_tools=("mcp__reveal__twitter_latest", "mcp__reveal__twitter_search"),
        external_services=(
            "database.app",
            "social.x_graphql",
            "social.vxtwitter",
            "llm.deepseek_chat",
            "mcp.reveal",
        ),
        usage="自动日报；也可自然语言询问某账号昨日总结。",
    ),
    CapabilitySpec(
        id="journal.log",
        title="交易记录",
        kind="workflow",
        group="交易",
        description="记录买入、卖出、做空和交易备注。",
        natural_examples=("记录买入 AAPL 180 100 股", "卖出 TSLA 250"),
        external_services=("database.app",),
        usage="不作为默认快捷命令暴露；真实交易记录需显式功能入口。",
        side_effects="会写入或修改交易日记。",
    ),
    CapabilitySpec(
        id="journal.view",
        title="交易日记",
        kind="tool",
        group="交易",
        description="查看交易日记和盈亏汇总。",
        natural_examples=("今日交易日记", "本月盈亏", "pnl"),
        agent_tools=("mcp__reveal__trading_journal", "mcp__reveal__pnl_summary"),
        external_services=("database.app", "mcp.reveal"),
        usage="自然语言: 今日交易日记 / 本月盈亏",
    ),
    CapabilitySpec(
        id="alert.manage",
        title="告警",
        kind="workflow",
        group="系统",
        description="查看、配置和手动检查价格/成交量/SEC/FDA 事件告警。",
        slash_commands=("alert",),
        natural_examples=("查看告警配置", "立即检查告警"),
        agent_tools=("mcp__reveal__alert_status",),
        external_services=(
            "database.app",
            "market.finnhub",
            "market.yfinance",
            "sec.edgar",
            "fda.openfda",
            "mcp.reveal",
        ),
        usage="/alert [status|check|config]",
        side_effects="/alert check 会主动检查并推送价格、成交量、新闻和监管事件告警。",
    ),
    CapabilitySpec(
        id="task.schedule",
        title="定时任务",
        kind="workflow",
        group="系统",
        description="创建、查看和取消用户的一次性定时 Agent 任务。",
        slash_commands=("task",),
        natural_examples=("2小时后推送 CPI 新闻", "今晚7点提醒我看 CPI 数据", "查看未来任务"),
        agent_tools=(
            "mcp__reveal__scheduled_task_create",
            "mcp__reveal__scheduled_task_list",
            "mcp__reveal__scheduled_task_cancel",
        ),
        external_services=("database.app", "scheduler.apscheduler", "llm.deepseek_agent"),
        usage="/task list; /task add 2小时后 | 推送 CPI 数据新闻; /task cancel TASK_ID",
        side_effects="create/cancel 会写入任务表；到点后会运行 Agent 并推送到原会话。",
    ),
    CapabilitySpec(
        id="briefing.daily",
        title="市场简报",
        kind="workflow",
        group="股票",
        description="生成每日市场简报。",
        natural_examples=("每日简报", "市场简报"),
        agent_tools=("mcp__reveal__daily_briefing",),
        external_services=("market.finnhub", "market.yfinance", "database.app", "mcp.reveal"),
        usage="自动每日执行；也可自然语言请求市场简报。",
    ),
)


def list_capabilities() -> tuple[CapabilitySpec, ...]:
    return CAPABILITIES


def list_external_services() -> tuple[ExternalServiceSpec, ...]:
    return EXTERNAL_SERVICES


def agent_mcp_tool_names() -> list[str]:
    return [tool for cap in CAPABILITIES for tool in cap.agent_tools]


def agent_allowed_tools() -> list[str]:
    return [*BUILTIN_AGENT_TOOLS, *agent_mcp_tool_names()]


def format_command_help() -> str:
    grouped = _group_capabilities(include_system_help=False)
    lines = ["*Reveal — 美股交易助手*"]
    for group, items in grouped.items():
        lines.append("")
        lines.append(f"*{group}*")
        for cap in items:
            if not cap.slash_commands:
                continue
            command = "/" + "|/".join(cap.slash_commands)
            args = f" {' '.join(cap.required_args).upper()}" if cap.required_args else ""
            lines.append(f"{command}{args} — {cap.title}")
    return "\n".join(lines)


def format_capability_catalog() -> str:
    grouped = _group_capabilities(include_system_help=True)
    lines = ["*Reveal 能力目录*", ""]
    lines.append("层次: capability → 核心实现函数 → slash command / 自然语言 / Agent MCP")
    for group, items in grouped.items():
        lines.append("")
        lines.append(f"*{group}*")
        for cap in items:
            command = ", ".join(f"/{cmd}" for cmd in cap.slash_commands) or "-"
            examples = "；".join(cap.natural_examples[:2]) or "-"
            agent = ", ".join(cap.agent_tools) or "-"
            services = ", ".join(cap.external_services) or "-"
            lines.append(f"- `{cap.id}` {cap.title} [{cap.kind}] {cap.description}")
            lines.append(f"  快捷命令: {command}")
            lines.append(f"  Agent MCP: {agent}")
            lines.append(f"  External services: {services}")
            lines.append(f"  自然语言: {examples}")
            if cap.usage:
                lines.append(f"  用法: {cap.usage}")
            if cap.side_effects:
                lines.append(f"  副作用: {cap.side_effects}")
    lines.append("")
    lines.append("*三方服务 / Runtime*")
    for service in EXTERNAL_SERVICES:
        keys = ", ".join(service.config_keys) or "-"
        lines.append(f"- `{service.id}` [{service.kind}] {service.title}: {service.description}")
        lines.append(f"  配置: {keys}")
    return "\n".join(lines)


def format_agent_tool_catalog(
    allowed_tools: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    allowed_tool_set = set(allowed_tools or [])
    lines = [
        "Reveal system capabilities:",
        "- 你知道所有 capability；只有 Agent MCP / built-in tools 里的名称可以真实调用。",
        "- 常用能力也有 slash command；如果用户要快速执行，可以建议或解释对应命令。",
        "- 带 side effects 的能力只有在用户明确表达意图时执行；"
        "交易写入类能力不要通过 Agent 工具擅自执行；持仓关注标记不是交易，但也必须用户明确要求。",
        "",
        "Built-in Agent tools:",
    ]
    if not allowed_tool_set or "WebSearch" in allowed_tool_set:
        lines.append("- WebSearch: 搜索互联网，适合最新新闻、公司公告、市场观点。")
    if not allowed_tool_set or "WebFetch" in allowed_tool_set:
        lines.append("- WebFetch: 抓取用户提供或搜索结果中的网页内容。")
    if allowed_tool_set and not ({"WebSearch", "WebFetch"} & allowed_tool_set):
        lines.append("- 当前 Agent runtime 未启用 built-in web tools。")
    lines.extend(["", "Reveal MCP tools and capabilities:"])
    for cap in CAPABILITIES:
        if allowed_tool_set and cap.agent_tools and not (set(cap.agent_tools) & allowed_tool_set):
            continue
        if allowed_tool_set and not cap.agent_tools and not cap.slash_commands:
            continue
        command = ", ".join(f"/{cmd}" for cmd in cap.slash_commands) or "-"
        tools = ", ".join(cap.agent_tools) or "not directly callable"
        args = ", ".join(cap.required_args) or "none"
        services = ", ".join(cap.external_services) or "-"
        lines.append(
            f"- {cap.id} [{cap.kind}] {cap.title}: {cap.description} "
            f"Args: {args}. Slash: {command}. Agent MCP: {tools}. Backing: {services}."
        )
        if cap.usage:
            lines.append(f"  Usage: {cap.usage}")
        if cap.side_effects:
            lines.append(f"  Side effects: {cap.side_effects}")
    return "\n".join(lines)


def _group_capabilities(include_system_help: bool) -> dict[str, list[CapabilitySpec]]:
    grouped: dict[str, list[CapabilitySpec]] = {}
    for cap in CAPABILITIES:
        if not include_system_help and cap.id == "system.help":
            continue
        grouped.setdefault(cap.group, []).append(cap)
    return grouped
