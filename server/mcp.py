"""Reveal MCP Server — exposes internal data tools for the research Agent."""

import asyncio
import json

from mcp.server import FastMCP

mcp = FastMCP("reveal")
_db_init_lock = asyncio.Lock()


async def _ensure_database() -> None:
    """Initialize DB lazily so MCP stdio can start before external DB connects."""
    from server.db import engine as db_engine

    if db_engine.AsyncSessionLocal is not None:
        return
    async with _db_init_lock:
        if db_engine.AsyncSessionLocal is None:
            await db_engine.init_db()


@mcp.tool()
async def capability_catalog() -> str:
    """查看 Reveal 全部系统能力、MCP 工具、slash command 和三方服务目录。"""
    from server.capabilities.system import get_capability_catalog_payload

    await _ensure_database()
    return json.dumps(get_capability_catalog_payload(), ensure_ascii=False)


@mcp.tool()
async def system_status() -> str:
    """查看 Reveal 当前运行配置状态：bot、LLM、数据库、行情、Twitter、告警。"""
    from server.capabilities.system import get_system_status_payload

    await _ensure_database()
    return json.dumps(get_system_status_payload(), ensure_ascii=False)


@mcp.tool()
async def stock_quote(ticker: str) -> str:
    """获取股票实时报价：现价、涨跌幅、成交量。"""
    from server.capabilities.market import get_stock_quote_payload

    await _ensure_database()
    payload = await get_stock_quote_payload(ticker)
    if not payload:
        return f"{ticker}: 数据不可用"
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def technical_analysis(ticker: str) -> str:
    """获取股票技术指标：RSI、SMA、量比、52周高低点、Beta。"""
    from server.capabilities.market import get_technical_analysis_payload

    await _ensure_database()
    payload = await get_technical_analysis_payload(ticker)
    if not payload:
        return f"{ticker}: 数据不可用"
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def stock_news(ticker: str, limit: int = 10) -> str:
    """获取股票最近新闻（来源: Finnhub）。"""
    from server.capabilities.market import get_stock_news_payload

    await _ensure_database()
    articles = await get_stock_news_payload(ticker, limit=limit)
    if not articles:
        return f"{ticker}: 暂无新闻"
    return json.dumps(articles, ensure_ascii=False)


@mcp.tool()
async def portfolio() -> str:
    """查看用户当前持仓：未平仓头寸、入场价、浮盈。"""
    from server.capabilities.market import get_portfolio_payload

    await _ensure_database()
    positions = await get_portfolio_payload()
    if not positions:
        return "当前无持仓"
    return json.dumps(positions, ensure_ascii=False)


@mcp.tool()
async def research_history(ticker: str, limit: int = 5) -> str:
    """查找过去关于某只股票的研究结论。"""
    from server.capabilities.market import get_research_history_payload

    await _ensure_database()
    results = await get_research_history_payload(ticker, limit=limit)
    if not results:
        return f"{ticker}: 暂无历史研究记录"
    for r in results:
        r["answer"] = r["answer"][:300]
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def stock_score(ticker: str) -> str:
    """对股票进行多因子评分：技术面、基本面、新闻情绪、板块强度。"""
    from server.capabilities.market import get_stock_score_payload

    await _ensure_database()
    result = await get_stock_score_payload(ticker)
    if not result:
        return f"{ticker}: 数据不可用，无法评分"
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def tracking_report(ticker: str | None = None) -> str:
    """查看每日选股/追踪标的表现，可选指定 ticker。"""
    from server.stock.tracker import get_tracking_report

    await _ensure_database()
    return await get_tracking_report(ticker)


@mcp.tool()
async def twitter_watch_list() -> str:
    """查看当前 Twitter/X watch list 和每个账号的缓存 cursor/check 状态。"""
    from server.capabilities.twitter import get_twitter_watch_list_payload

    await _ensure_database()
    return json.dumps(await get_twitter_watch_list_payload(), ensure_ascii=False)


@mcp.tool()
async def twitter_watch_add(username: str, backfill_limit: int = 10) -> str:
    """把一个 Twitter/X 用户加入 watch list，并返回最近最多 backfill_limit 条推文。"""
    from server.capabilities.twitter import set_twitter_watch_account_payload

    await _ensure_database()
    return json.dumps(
        await set_twitter_watch_account_payload(
            username,
            is_active=True,
            backfill_limit=backfill_limit,
        ),
        ensure_ascii=False,
    )


@mcp.tool()
async def twitter_watch_remove(username: str) -> str:
    """把一个 Twitter/X 用户移出 watch list。"""
    from server.capabilities.twitter import set_twitter_watch_account_payload

    await _ensure_database()
    return json.dumps(
        await set_twitter_watch_account_payload(username, is_active=False),
        ensure_ascii=False,
    )


@mcp.tool()
async def twitter_latest(username: str, limit: int = 5) -> str:
    """获取并缓存某个 Twitter/X 用户最新推文，返回正文、摘要、媒体、链接和引用。"""
    from server.capabilities.twitter import get_twitter_latest_payload

    await _ensure_database()
    return json.dumps(await get_twitter_latest_payload(username, limit=limit), ensure_ascii=False)


@mcp.tool()
async def twitter_search(query: str, limit: int = 8, username: str | None = None) -> str:
    """搜索 Reveal 数据库中已缓存的 Twitter/X 推文，可按用户名过滤。"""
    from server.capabilities.twitter import search_cached_twitter_posts_payload

    await _ensure_database()
    return json.dumps(
        await search_cached_twitter_posts_payload(query, limit=limit, username=username),
        ensure_ascii=False,
    )


@mcp.tool()
async def trading_journal(period: str = "today") -> str:
    """查看交易日记。period: today/week/month/year/all。"""
    from server.capabilities.journal import get_trading_journal_payload

    await _ensure_database()
    return json.dumps(await get_trading_journal_payload(period), ensure_ascii=False)


@mcp.tool()
async def pnl_summary(period: str = "month") -> str:
    """查看交易盈亏汇总。period: today/week/month/year/all。"""
    from server.capabilities.journal import get_pnl_summary_payload

    await _ensure_database()
    return json.dumps(await get_pnl_summary_payload(period), ensure_ascii=False)


@mcp.tool()
async def alert_status() -> str:
    """查看告警配置和当前告警监控 ticker。"""
    from server.capabilities.alerts import get_alert_status_payload

    await _ensure_database()
    return json.dumps(await get_alert_status_payload(), ensure_ascii=False)


@mcp.tool()
async def daily_briefing() -> str:
    """生成每日市场简报。"""
    from server.briefing import generate_daily_briefing

    await _ensure_database()
    return await generate_daily_briefing()


async def _run() -> None:
    """Run the MCP stdio server; DB connects lazily on first tool use."""
    from server.db.engine import close_db

    try:
        await mcp.run_stdio_async()
    finally:
        await close_db()


def main():
    asyncio.run(_run())


if __name__ == "__main__":
    main()
