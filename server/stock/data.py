"""Stock data sources: yfinance (price/technicals) + Finnhub (news + real-time quote)."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, cast

import yfinance as yf
from loguru import logger

from config.settings import get_settings

# ---------------------------------------------------------------------------
# Finnhub real-time quote with 2-second in-memory cache
# ---------------------------------------------------------------------------

_quote_cache: dict[str, tuple[float, dict]] = {}
_QUOTE_CACHE_TTL = 2.0  # seconds


async def fetch_quote_finnhub(ticker: str) -> dict | None:
    """Fetch real-time quote from Finnhub /quote with a 2-second cache.

    Returns keys: price, change, change_pct, high, low, open, prev_close.
    Returns None if Finnhub is not configured or the ticker is unknown.
    """
    now = time.monotonic()
    cached = _quote_cache.get(ticker)
    if cached and now - cached[0] < _QUOTE_CACHE_TTL:
        return cached[1]

    settings = get_settings()
    if not settings.is_finnhub_configured():
        logger.debug("Finnhub not configured, skipping quote fetch")
        return None

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{settings.finnhub_base_url}/quote",
                params={"symbol": ticker, "token": settings.finnhub_api_key},
            )
        if resp.status_code != 200:
            logger.warning(f"Finnhub quote {ticker}: HTTP {resp.status_code}")
            return None

        raw = resp.json()
        if not raw.get("c"):  # c==0 means ticker not found
            return None

        result: dict = {
            "price": raw["c"],
            "change": raw.get("d", 0.0),
            "change_pct": raw.get("dp", 0.0),
            "high": raw.get("h"),
            "low": raw.get("l"),
            "open": raw.get("o"),
            "prev_close": raw.get("pc"),
        }
        _quote_cache[ticker] = (now, result)
        return result
    except Exception as e:
        logger.warning(f"Finnhub quote error for {ticker}: {e}")
        return None


async def fetch_stock_data(ticker: str, period: str = "6mo") -> dict | None:
    """Fetch historical price data for a ticker using yfinance."""
    return await asyncio.to_thread(_fetch_stock_data_sync, ticker, period)


def _fetch_stock_data_sync(ticker: str, period: str = "6mo") -> dict | None:
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)
        if hist.empty:
            return None

        info = stock.info
        latest = hist.iloc[-1]
        prev = hist.iloc[-2] if len(hist) > 1 else latest
        close_series = cast(Any, hist["Close"])
        volume_series = cast(Any, hist["Volume"])
        avg_volume = volume_series.mean()

        return {
            "ticker": ticker,
            "name": info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "peg_ratio": info.get("pegRatio"),
            "revenue_growth": info.get("revenueGrowth"),
            "current_price": latest["Close"],
            "prev_close": prev["Close"],
            "change_pct": (latest["Close"] / prev["Close"] - 1) * 100,
            "volume": latest["Volume"],
            "avg_volume": avg_volume,
            "volume_ratio": latest["Volume"] / avg_volume if avg_volume > 0 else 1.0,
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
            "rsi_14": _calc_rsi(close_series, 14),
            "sma_20": close_series.rolling(20).mean().iloc[-1],
            "sma_50": close_series.rolling(50).mean().iloc[-1],
            "sma_200": close_series.rolling(200).mean().iloc[-1] if len(hist) >= 200 else None,
            "beta": info.get("beta"),
        }
    except Exception as e:
        logger.warning(f"yfinance fetch error for {ticker}: {e}")
        return None


async def fetch_news(ticker: str, limit: int = 20) -> list[dict]:
    """Fetch recent news for a ticker using Finnhub."""
    settings = get_settings()
    if not settings.is_finnhub_configured():
        logger.debug("Finnhub not configured, skipping news fetch")
        return []

    try:
        import httpx

        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.finnhub_base_url}/company-news",
                params={
                    "symbol": ticker,
                    "from": week_ago.isoformat(),
                    "to": today.isoformat(),
                    "token": settings.finnhub_api_key,
                },
                timeout=15,
            )
            if resp.status_code == 200:
                articles = resp.json()[:limit]
                return [
                    {
                        "headline": a.get("headline", ""),
                        "summary": a.get("summary", ""),
                        "source": a.get("source", ""),
                        "url": a.get("url", ""),
                        "datetime": datetime.fromtimestamp(a.get("datetime", 0)).isoformat(),
                    }
                    for a in articles
                ]
            logger.warning(f"Finnhub news error: {resp.status_code}")
            return []
    except Exception as e:
        logger.warning(f"News fetch error for {ticker}: {e}")
        return []


async def fetch_spy_return(start_date: str) -> float | None:
    """Get SPY return since start_date for benchmarking."""
    return await asyncio.to_thread(_fetch_spy_return_sync, start_date)


def _fetch_spy_return_sync(start_date: str) -> float | None:
    try:
        spy = yf.Ticker("SPY")
        hist = spy.history(start=start_date)
        if hist.empty or len(hist) < 2:
            return None
        close_series = cast(Any, hist["Close"])
        return (close_series.iloc[-1] / close_series.iloc[0] - 1) * 100
    except Exception:
        return None


async def get_current_price(ticker: str) -> float | None:
    """Get current price for a ticker."""
    return await asyncio.to_thread(_get_current_price_sync, ticker)


def _get_current_price_sync(ticker: str) -> float | None:
    try:
        stock = yf.Ticker(ticker)
        info = stock.fast_info
        return info.get("lastPrice") or info.get("regularMarketPreviousClose")
    except Exception:
        return None


def _calc_rsi(closes, period: int = 14) -> float | None:
    """Calculate RSI for the given period."""
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)
