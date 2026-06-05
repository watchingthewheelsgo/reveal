"""In-process Reveal MCP adapter for Claude Agent SDK."""

import inspect
import types
from collections.abc import Awaitable, Callable
from typing import Any, get_args, get_origin

from claude_agent_sdk import McpSdkServerConfig, SdkMcpTool, create_sdk_mcp_server, tool
from loguru import logger

from server import mcp as reveal_mcp

ToolHandler = Callable[..., Awaitable[str]]


def build_reveal_sdk_mcp_server() -> McpSdkServerConfig:
    """Build a per-run in-process MCP server exposing Reveal capability tools."""
    return create_sdk_mcp_server("reveal", tools=_build_tools())


def _build_tools() -> list[SdkMcpTool[Any]]:
    handlers: list[tuple[str, ToolHandler]] = [
        ("capability_catalog", reveal_mcp.capability_catalog),
        ("system_status", reveal_mcp.system_status),
        ("stock_quote", reveal_mcp.stock_quote),
        ("technical_analysis", reveal_mcp.technical_analysis),
        ("stock_news", reveal_mcp.stock_news),
        ("portfolio", reveal_mcp.portfolio),
        ("research_history", reveal_mcp.research_history),
        ("stock_score", reveal_mcp.stock_score),
        ("tracking_report", reveal_mcp.tracking_report),
        ("twitter_watch_list", reveal_mcp.twitter_watch_list),
        ("twitter_watch_add", reveal_mcp.twitter_watch_add),
        ("twitter_watch_remove", reveal_mcp.twitter_watch_remove),
        ("twitter_latest", reveal_mcp.twitter_latest),
        ("twitter_search", reveal_mcp.twitter_search),
        ("trading_journal", reveal_mcp.trading_journal),
        ("pnl_summary", reveal_mcp.pnl_summary),
        ("alert_status", reveal_mcp.alert_status),
        ("daily_briefing", reveal_mcp.daily_briefing),
    ]
    return [_sdk_tool(name, handler) for name, handler in handlers]


def _sdk_tool(name: str, handler: ToolHandler) -> SdkMcpTool[Any]:
    description = inspect.getdoc(handler) or name
    input_schema = _schema_from_signature(handler)

    @tool(name, description, input_schema)
    async def wrapped(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await handler(**args)
        except Exception as exc:
            from server.db.engine import database_diagnostic_context

            logger.warning(
                "Reveal SDK MCP tool failed: tool={} args={} db_context={} exc_type={} error={}",
                name,
                args,
                database_diagnostic_context(),
                type(exc).__name__,
                exc,
            )
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Reveal tool {name} failed: {type(exc).__name__}: {exc}",
                    }
                ],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": result}]}

    return wrapped


def _schema_from_signature(handler: ToolHandler) -> dict[str, Any]:
    signature = inspect.signature(handler)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in signature.parameters.values():
        if param.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            continue
        properties[param.name] = _json_schema_for(param.annotation)
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
        elif param.default is not None:
            properties[param.name]["default"] = param.default
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _json_schema_for(annotation: Any) -> dict[str, str]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    origin = get_origin(annotation)
    if origin in {types.UnionType, getattr(types, "UnionType", None)}:
        args = [item for item in get_args(annotation) if item is not type(None)]
        if len(args) == 1:
            return _json_schema_for(args[0])
    if origin is None and isinstance(annotation, types.UnionType):
        args = [item for item in annotation.__args__ if item is not type(None)]
        if len(args) == 1:
            return _json_schema_for(args[0])
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is list:
        return {"type": "array"}
    if annotation is dict:
        return {"type": "object"}
    return {"type": "string"}
