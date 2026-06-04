"""System capability catalog.

This module is the single source of truth for user-facing commands, natural-language
entrypoints, and Agent/MCP tool exposure. Concrete implementations live in domain
modules; adapters should route to capabilities instead of reimplementing behavior.
"""

from dataclasses import dataclass
from typing import Literal

CapabilityKind = Literal["system", "tool", "skill", "workflow"]

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
    agent_tool: str | None = None
    notes: str = ""


CAPABILITIES: tuple[CapabilitySpec, ...] = (
    CapabilitySpec(
        id="system.help",
        title="帮助",
        kind="system",
        group="系统",
        description="查看 Reveal 可用命令。",
        slash_commands=("help",),
        natural_examples=("帮助", "怎么用", "有哪些命令"),
    ),
    CapabilitySpec(
        id="system.tools",
        title="能力目录",
        kind="system",
        group="系统",
        description="查看系统工具、技能和自然语言示例。",
        slash_commands=("tools",),
        natural_examples=("有哪些工具", "你能做什么", "有哪些技能"),
    ),
    CapabilitySpec(
        id="system.status",
        title="系统状态",
        kind="tool",
        group="系统",
        description="查看 bot、LLM、数据库、行情源配置状态。",
        slash_commands=("status",),
        natural_examples=("系统状态", "服务状态"),
    ),
    CapabilitySpec(
        id="stock.pick",
        title="每日选股",
        kind="workflow",
        group="股票",
        description="扫描市场并推荐标的。",
        slash_commands=("pick",),
        natural_examples=("今日选股", "推荐股票"),
    ),
    CapabilitySpec(
        id="stock.quote",
        title="实时报价",
        kind="tool",
        group="股票",
        description="查询股票现价、涨跌幅、成交量等。",
        slash_commands=("quote",),
        natural_examples=("NVDA 现在多少钱", "查一下 MRVL 报价"),
        required_args=("ticker",),
        agent_tool="mcp__reveal__stock_quote",
    ),
    CapabilitySpec(
        id="stock.technical",
        title="技术指标",
        kind="tool",
        group="股票",
        description="查询 RSI、均线、量比、52 周高低点等。",
        slash_commands=("technical",),
        natural_examples=("MRVL 技术指标", "看一下 NVDA RSI 和均线"),
        required_args=("ticker",),
        agent_tool="mcp__reveal__technical_analysis",
    ),
    CapabilitySpec(
        id="stock.news",
        title="股票新闻",
        kind="tool",
        group="股票",
        description="查询公司最近新闻。",
        slash_commands=("news",),
        natural_examples=("查一下 MRVL 新闻", "NVDA 最近有什么新闻"),
        required_args=("ticker",),
        agent_tool="mcp__reveal__stock_news",
    ),
    CapabilitySpec(
        id="stock.score",
        title="多因子评分",
        kind="tool",
        group="股票",
        description="对股票做技术、基本面、新闻情绪和板块评分。",
        slash_commands=("score",),
        natural_examples=("给 MRVL 打分", "NVDA 评分"),
        required_args=("ticker",),
        agent_tool="mcp__reveal__stock_score",
    ),
    CapabilitySpec(
        id="stock.track",
        title="追踪标的",
        kind="tool",
        group="股票",
        description="查看正在追踪的标的表现。",
        slash_commands=("track",),
        natural_examples=("查看追踪标的", "MRVL 追踪情况"),
    ),
    CapabilitySpec(
        id="portfolio.view",
        title="当前持仓",
        kind="tool",
        group="交易",
        description="查看未平仓持仓和浮盈。",
        slash_commands=("portfolio",),
        natural_examples=("我的持仓", "当前仓位", "portfolio"),
        agent_tool="mcp__reveal__portfolio",
    ),
    CapabilitySpec(
        id="research.history",
        title="历史研究",
        kind="tool",
        group="研究",
        description="查询某只股票过去的研究结论。",
        slash_commands=("history",),
        natural_examples=("MRVL 之前研究过什么", "查 NVDA 历史研究"),
        required_args=("ticker",),
        agent_tool="mcp__reveal__research_history",
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
    ),
    CapabilitySpec(
        id="research.tweet",
        title="推文研究",
        kind="skill",
        group="研究",
        description="基于 Twitter/X 更新建立研究线程或主动深挖。",
        slash_commands=("deep", "ask", "topic", "thread"),
        natural_examples=("深挖最新推文", "基于这条消息分析影响"),
    ),
    CapabilitySpec(
        id="twitter.watch",
        title="Twitter 关注列表",
        kind="workflow",
        group="Twitter",
        description="添加、移除、检查关注账号。",
        slash_commands=("twatch",),
        natural_examples=("把 @OwenCarter_k 加到 watch list", "移除 @user"),
    ),
    CapabilitySpec(
        id="twitter.digest",
        title="Twitter 日报",
        kind="workflow",
        group="Twitter",
        description="生成关注账号日报或单账号总结。",
        slash_commands=("digest", "summary"),
        natural_examples=("推特日报", "@OwenCarter_k 昨天发了什么"),
    ),
    CapabilitySpec(
        id="journal.log",
        title="交易记录",
        kind="workflow",
        group="交易",
        description="记录买入、卖出、做空和交易备注。",
        slash_commands=("log",),
        natural_examples=("记录买入 AAPL 180 100 股", "卖出 TSLA 250"),
    ),
    CapabilitySpec(
        id="journal.view",
        title="交易日记",
        kind="tool",
        group="交易",
        description="查看交易日记和盈亏汇总。",
        slash_commands=("journal", "pnl"),
        natural_examples=("今日交易日记", "本月盈亏", "pnl"),
    ),
    CapabilitySpec(
        id="alert.manage",
        title="告警",
        kind="workflow",
        group="系统",
        description="查看、配置和手动检查价格/成交量告警。",
        slash_commands=("alert",),
        natural_examples=("查看告警配置", "立即检查告警"),
    ),
    CapabilitySpec(
        id="briefing.daily",
        title="市场简报",
        kind="workflow",
        group="股票",
        description="生成每日市场简报。",
        slash_commands=("briefing",),
        natural_examples=("每日简报", "市场简报"),
    ),
)


def list_capabilities() -> tuple[CapabilitySpec, ...]:
    return CAPABILITIES


def agent_mcp_tool_names() -> list[str]:
    return [cap.agent_tool for cap in CAPABILITIES if cap.agent_tool]


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
    lines.append("层次: 核心实现函数 → system tool/skill → slash command / 自然语言 / Agent MCP")
    for group, items in grouped.items():
        lines.append("")
        lines.append(f"*{group}*")
        for cap in items:
            command = ", ".join(f"/{cmd}" for cmd in cap.slash_commands) or "-"
            examples = "；".join(cap.natural_examples[:2]) or "-"
            agent = f"；Agent tool: {cap.agent_tool}" if cap.agent_tool else ""
            lines.append(f"- `{cap.id}` {cap.title} [{cap.kind}] {cap.description}")
            lines.append(f"  命令: {command}{agent}")
            lines.append(f"  自然语言: {examples}")
    return "\n".join(lines)


def format_agent_tool_catalog() -> str:
    lines = ["Agent 可用工具:"]
    for cap in CAPABILITIES:
        if cap.agent_tool:
            lines.append(f"- {cap.agent_tool}: {cap.title}。{cap.description}")
    lines.extend(
        [
            "- WebSearch: 搜索互联网。",
            "- WebFetch: 抓取网页内容。",
        ]
    )
    return "\n".join(lines)


def _group_capabilities(include_system_help: bool) -> dict[str, list[CapabilitySpec]]:
    grouped: dict[str, list[CapabilitySpec]] = {}
    for cap in CAPABILITIES:
        if not include_system_help and cap.id == "system.help":
            continue
        grouped.setdefault(cap.group, []).append(cap)
    return grouped
