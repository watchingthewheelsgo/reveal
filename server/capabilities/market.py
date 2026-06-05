"""Reusable market and portfolio tool implementations."""

from typing import Any


async def get_stock_quote_payload(ticker: str) -> dict[str, Any] | None:
    ticker = ticker.upper().strip()
    from server.stock.data import fetch_quote_finnhub, fetch_stock_data

    quote = await fetch_quote_finnhub(ticker)
    if quote:
        return {
            "ticker": ticker,
            "source": "finnhub",
            "price": quote["price"],
            "change": quote.get("change", 0.0),
            "change_pct": quote.get("change_pct", 0.0),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "open": quote.get("open"),
            "prev_close": quote.get("prev_close"),
        }

    data = await fetch_stock_data(ticker, period="5d")
    if not data:
        return None
    return {
        "ticker": ticker,
        "source": "yfinance",
        "name": data.get("name", ticker),
        "price": data["current_price"],
        "change_pct": data.get("change_pct", 0.0),
        "volume": data.get("volume", 0),
        "volume_ratio": data.get("volume_ratio", 0.0),
    }


async def get_technical_analysis_payload(ticker: str) -> dict[str, Any] | None:
    ticker = ticker.upper().strip()
    from server.stock.data import fetch_stock_data

    data = await fetch_stock_data(ticker, period="6mo")
    if not data:
        return None
    return {
        "ticker": ticker,
        "price": data["current_price"],
        "change_pct": data.get("change_pct", 0.0),
        "rsi_14": data.get("rsi_14"),
        "sma_20": data.get("sma_20"),
        "sma_50": data.get("sma_50"),
        "sma_200": data.get("sma_200"),
        "volume_ratio": data.get("volume_ratio", 0.0),
        "high_52w": data.get("high_52w"),
        "low_52w": data.get("low_52w"),
        "beta": data.get("beta"),
        "pe_ratio": data.get("pe_ratio"),
        "peg_ratio": data.get("peg_ratio"),
        "revenue_growth": data.get("revenue_growth"),
        "sector": data.get("sector"),
        "market_cap": data.get("market_cap"),
    }


async def get_stock_news_payload(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
    from server.stock.data import fetch_news

    return await fetch_news(ticker.upper().strip(), limit=limit)


async def get_stock_score_payload(ticker: str) -> dict[str, Any] | None:
    ticker = ticker.upper().strip()
    from server.stock.data import fetch_stock_data
    from server.stock.scorer import score_stock

    data = await fetch_stock_data(ticker, period="6mo")
    if not data:
        return None
    scored = await score_stock(data)
    return {
        "ticker": scored["ticker"],
        "composite_score": round(scored["composite_score"], 3),
        "factors": {
            key: {"score": round(value["score"], 3), "reason": value.get("reason", "")}
            for key, value in scored["factors"].items()
        },
    }


async def get_portfolio_payload() -> list[dict[str, Any]]:
    from server.stock.data import get_current_price

    try:
        from server.journal.service import get_trades_for_period

        trades = await get_trades_for_period("all")
    except Exception:
        return []

    positions = []
    for trade in [item for item in trades if item.exit_price is None]:
        current = trade.entry_price
        try:
            price = await get_current_price(trade.ticker)
            if price:
                current = price
        except Exception:
            pass
        unrealized = (current - trade.entry_price) * trade.quantity
        if trade.direction == "short":
            unrealized = -unrealized
        positions.append(
            {
                "ticker": trade.ticker,
                "direction": trade.direction,
                "quantity": trade.quantity,
                "entry_price": trade.entry_price,
                "current_price": current,
                "unrealized_pnl": unrealized,
                "trade_date": trade.trade_date.isoformat() if trade.trade_date else None,
            }
        )
    return positions


async def get_research_history_payload(ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    from server.research.context import find_past_research_by_ticker

    return await find_past_research_by_ticker(ticker.upper().strip(), limit=limit)


def format_stock_quote(payload: dict[str, Any] | None, ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not payload:
        return f"❌ 无法获取 {ticker} 报价。"
    lines = [f"*{ticker} 报价*", ""]
    if payload.get("source") == "finnhub":
        lines.extend(
            [
                f"现价: ${payload['price']:.2f}",
                f"涨跌: {payload.get('change', 0):+.2f} ({payload.get('change_pct', 0):+.2f}%)",
                f"开盘: {_fmt_optional(payload.get('open'), money=True)}",
                f"日高/日低: {_fmt_optional(payload.get('high'), money=True)} / "
                f"{_fmt_optional(payload.get('low'), money=True)}",
                f"昨收: {_fmt_optional(payload.get('prev_close'), money=True)}",
            ]
        )
    else:
        lines.extend(
            [
                f"现价: ${payload['price']:.2f}",
                f"涨跌幅: {payload.get('change_pct', 0):+.2f}%",
                f"成交量: {int(payload.get('volume', 0)):,}",
                f"量比: {payload.get('volume_ratio', 0):.2f}x",
            ]
        )
    return "\n".join(lines)


def format_stock_score(payload: dict[str, Any] | None, ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not payload:
        return f"❌ 无法获取 {ticker} 的评分数据。"
    lines = [
        f"*{ticker} 多因子评分*",
        "",
        f"综合评分: {payload.get('composite_score', 0):.3f}",
    ]
    factors = payload.get("factors") or {}
    for name, item in factors.items():
        lines.append(
            f"- {name}: {item.get('score', 0):.2f}"
            + (f" · {item.get('reason')}" if item.get("reason") else "")
        )
    return "\n".join(lines)


def format_technical_analysis(payload: dict[str, Any] | None, ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not payload:
        return f"❌ 无法获取 {ticker} 技术指标。"
    return "\n".join(
        [
            f"*{ticker} 技术指标*",
            "",
            f"现价: ${payload['price']:.2f}",
            f"涨跌幅: {payload.get('change_pct', 0):+.2f}%",
            f"RSI(14): {_fmt_optional(payload.get('rsi_14'))}",
            f"SMA20: {_fmt_optional(payload.get('sma_20'), money=True)}",
            f"SMA50: {_fmt_optional(payload.get('sma_50'), money=True)}",
            f"SMA200: {_fmt_optional(payload.get('sma_200'), money=True)}",
            f"量比: {payload.get('volume_ratio', 0):.2f}x",
            f"52周高/低: {_fmt_optional(payload.get('high_52w'), money=True)} / "
            f"{_fmt_optional(payload.get('low_52w'), money=True)}",
            f"Beta: {_fmt_optional(payload.get('beta'))}",
            f"PE: {_fmt_optional(payload.get('pe_ratio'))}",
            f"PEG: {_fmt_optional(payload.get('peg_ratio'))}",
            f"行业: {payload.get('sector') or 'Unknown'}",
        ]
    )


def format_stock_news(articles: list[dict[str, Any]], ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not articles:
        return f"{ticker}: 暂无最近新闻。"
    lines = [f"*{ticker} 最近新闻*", ""]
    for index, article in enumerate(articles[:8], start=1):
        headline = article.get("headline") or "Untitled"
        source = article.get("source") or "Unknown"
        url = article.get("url") or ""
        lines.append(f"{index}. {headline}")
        lines.append(f"来源: {source}")
        if url:
            lines.append(url)
        lines.append("")
    return "\n".join(lines).strip()


def format_portfolio(positions: list[dict[str, Any]]) -> str:
    if not positions:
        return "*当前持仓*\n\n当前无未平仓持仓。"
    lines = ["*当前持仓*", ""]
    for item in positions[:20]:
        lines.append(
            f"- {item['ticker']} {item['direction']} "
            f"qty={item['quantity']} entry=${item['entry_price']:.2f} "
            f"current=${item['current_price']:.2f} pnl=${item['unrealized_pnl']:+.2f}"
        )
    return "\n".join(lines)


def format_research_history(items: list[dict[str, Any]], ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not items:
        return f"{ticker}: 暂无历史研究记录。"
    lines = [f"*{ticker} 历史研究*", ""]
    for item in items:
        answer = _compact_text(str(item.get("answer") or ""), 260)
        lines.append(f"#{item.get('id')} {item.get('topic') or item.get('source_query') or ticker}")
        lines.append(answer or "（无摘要）")
        if item.get("updated_at"):
            lines.append(f"更新时间: {item['updated_at']}")
        lines.append("")
    return "\n".join(lines).strip()


def _fmt_optional(value: Any, money: bool = False) -> str:
    if value is None:
        return "-"
    try:
        return f"${float(value):.2f}" if money else f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _compact_text(text: str, limit: int) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."
