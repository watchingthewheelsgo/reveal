"""Reveal MCP Server — exposes internal data tools for the research Agent."""

import asyncio
import json

from mcp.server import FastMCP

mcp = FastMCP("reveal")


@mcp.tool()
async def stock_quote(ticker: str) -> str:
    """获取股票实时报价：现价、涨跌幅、成交量。"""
    from server.capabilities.market import get_stock_quote_payload

    payload = await get_stock_quote_payload(ticker)
    if not payload:
        return f"{ticker}: 数据不可用"
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def technical_analysis(ticker: str) -> str:
    """获取股票技术指标：RSI、SMA、量比、52周高低点、Beta。"""
    from server.capabilities.market import get_technical_analysis_payload

    payload = await get_technical_analysis_payload(ticker)
    if not payload:
        return f"{ticker}: 数据不可用"
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def stock_news(ticker: str, limit: int = 10) -> str:
    """获取股票最近新闻（来源: Finnhub）。"""
    from server.capabilities.market import get_stock_news_payload

    articles = await get_stock_news_payload(ticker, limit=limit)
    if not articles:
        return f"{ticker}: 暂无新闻"
    return json.dumps(articles, ensure_ascii=False)


@mcp.tool()
async def portfolio() -> str:
    """查看用户当前持仓：未平仓头寸、入场价、浮盈。"""
    from server.capabilities.market import get_portfolio_payload

    positions = await get_portfolio_payload()
    if not positions:
        return "当前无持仓"
    return json.dumps(positions, ensure_ascii=False)


@mcp.tool()
async def research_history(ticker: str, limit: int = 5) -> str:
    """查找过去关于某只股票的研究结论。"""
    from server.capabilities.market import get_research_history_payload

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

    result = await get_stock_score_payload(ticker)
    if not result:
        return f"{ticker}: 数据不可用，无法评分"
    return json.dumps(result, ensure_ascii=False)


def main():
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
