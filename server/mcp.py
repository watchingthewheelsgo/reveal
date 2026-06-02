"""Reveal MCP Server — exposes internal data tools for the research Agent."""

import asyncio
import json

from mcp.server import FastMCP

mcp = FastMCP("reveal")


@mcp.tool()
async def stock_quote(ticker: str) -> str:
    """获取股票实时报价：现价、涨跌幅、成交量。"""
    from server.stock.data import fetch_stock_data

    data = await fetch_stock_data(ticker, period="5d")
    if not data:
        return f"{ticker}: 数据不可用"
    return json.dumps(
        {
            "ticker": data["ticker"],
            "name": data.get("name", ""),
            "price": round(data["current_price"], 2),
            "change_pct": round(data.get("change_pct", 0), 2),
            "volume": int(data.get("volume", 0)),
            "volume_ratio": round(data.get("volume_ratio", 0), 2),
        },
        ensure_ascii=False,
    )


@mcp.tool()
async def technical_analysis(ticker: str) -> str:
    """获取股票技术指标：RSI、SMA、量比、52周高低点、Beta。"""
    from server.stock.data import fetch_stock_data

    data = await fetch_stock_data(ticker, period="6mo")
    if not data:
        return f"{ticker}: 数据不可用"
    result = {
        "ticker": data["ticker"],
        "price": round(data["current_price"], 2),
        "rsi_14": data.get("rsi_14"),
        "sma_20": round(data["sma_20"], 2) if data.get("sma_20") else None,
        "sma_50": round(data["sma_50"], 2) if data.get("sma_50") else None,
        "sma_200": round(data["sma_200"], 2) if data.get("sma_200") else None,
        "volume_ratio": round(data.get("volume_ratio", 0), 2),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "beta": data.get("beta"),
        "pe_ratio": data.get("pe_ratio"),
        "peg_ratio": data.get("peg_ratio"),
        "revenue_growth": data.get("revenue_growth"),
        "sector": data.get("sector"),
        "market_cap": data.get("market_cap"),
    }
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def stock_news(ticker: str, limit: int = 10) -> str:
    """获取股票最近新闻（来源: Finnhub）。"""
    from server.stock.data import fetch_news

    articles = await fetch_news(ticker, limit=limit)
    if not articles:
        return f"{ticker}: 暂无新闻"
    return json.dumps(articles, ensure_ascii=False)


@mcp.tool()
async def portfolio() -> str:
    """查看用户当前持仓：未平仓头寸、入场价、浮盈。"""
    from server.stock.data import get_current_price

    try:
        from server.journal.service import get_trades_for_period

        trades = await get_trades_for_period("all")
    except Exception:
        return "交易数据不可用"

    open_positions = [t for t in trades if t.exit_price is None]
    if not open_positions:
        return "当前无持仓"

    positions = []
    for t in open_positions:
        current = t.entry_price
        try:
            price = await get_current_price(t.ticker)
            if price:
                current = price
        except Exception:
            pass
        unrealized = (current - t.entry_price) * t.quantity
        if t.direction == "short":
            unrealized = -unrealized
        positions.append(
            {
                "ticker": t.ticker,
                "direction": t.direction,
                "quantity": t.quantity,
                "entry_price": round(t.entry_price, 2),
                "current_price": round(current, 2),
                "unrealized_pnl": round(unrealized, 2),
                "trade_date": t.trade_date.isoformat() if t.trade_date else None,
            }
        )
    return json.dumps(positions, ensure_ascii=False)


@mcp.tool()
async def research_history(ticker: str, limit: int = 5) -> str:
    """查找过去关于某只股票的研究结论。"""
    from server.research.context import find_past_research_by_ticker

    results = await find_past_research_by_ticker(ticker, limit=limit)
    if not results:
        return f"{ticker}: 暂无历史研究记录"
    for r in results:
        r["answer"] = r["answer"][:300]
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
async def stock_score(ticker: str) -> str:
    """对股票进行多因子评分：技术面、基本面、新闻情绪、板块强度。"""
    from server.stock.data import fetch_stock_data
    from server.stock.scorer import score_stock

    data = await fetch_stock_data(ticker, period="6mo")
    if not data:
        return f"{ticker}: 数据不可用，无法评分"
    scored = await score_stock(data)
    result = {
        "ticker": scored["ticker"],
        "composite_score": round(scored["composite_score"], 3),
        "factors": {
            k: {"score": round(v["score"], 3), "reason": v.get("reason", "")}
            for k, v in scored["factors"].items()
        },
    }
    return json.dumps(result, ensure_ascii=False)


def main():
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
