"""Stock data sources: Finnhub primary, yfinance as local fallback."""

import asyncio
import time
from datetime import UTC, datetime, timedelta
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
    """Fetch quote and historical indicators for a ticker.

    Finnhub is the primary provider when configured. yfinance is only used as a
    local fallback when Finnhub credentials are absent.
    """
    settings = get_settings()
    if settings.is_finnhub_configured():
        data = await _fetch_stock_data_finnhub(ticker, period)
        if data:
            return data
        logger.debug("Finnhub stock data unavailable for {}, not falling back to yfinance", ticker)
        return None
    return await asyncio.to_thread(_fetch_stock_data_sync, ticker, period)


async def _fetch_stock_data_finnhub(ticker: str, period: str = "6mo") -> dict | None:
    settings = get_settings()
    try:
        import httpx

        now = datetime.now(UTC)
        from_ts = int((now - _period_delta(period)).timestamp())
        to_ts = int(now.timestamp())
        async with httpx.AsyncClient(timeout=15) as client:
            candles_response = await client.get(
                f"{settings.finnhub_base_url}/stock/candle",
                params={
                    "symbol": ticker,
                    "resolution": "D",
                    "from": from_ts,
                    "to": to_ts,
                    "token": settings.finnhub_api_key,
                },
            )
            profile_response = await client.get(
                f"{settings.finnhub_base_url}/stock/profile2",
                params={"symbol": ticker, "token": settings.finnhub_api_key},
            )
            metric_response = await client.get(
                f"{settings.finnhub_base_url}/stock/metric",
                params={"symbol": ticker, "metric": "all", "token": settings.finnhub_api_key},
            )

        quote = await fetch_quote_finnhub(ticker)
        candles = candles_response.json() if candles_response.status_code == 200 else {}
        profile = profile_response.json() if profile_response.status_code == 200 else {}
        metrics_payload = metric_response.json() if metric_response.status_code == 200 else {}
        metrics = metrics_payload.get("metric") if isinstance(metrics_payload, dict) else {}
        if not isinstance(metrics, dict):
            metrics = {}

        closes = _float_list(candles.get("c")) if isinstance(candles, dict) else []
        volumes = _float_list(candles.get("v")) if isinstance(candles, dict) else []
        if not closes and not quote:
            return None

        latest_close = closes[-1] if closes else float(quote["price"]) if quote else 0.0
        prev_close = (
            float(quote["prev_close"])
            if quote and quote.get("prev_close")
            else closes[-2]
            if len(closes) > 1
            else latest_close
        )
        current_price = float(quote["price"]) if quote else latest_close
        change_pct = (
            float(quote.get("change_pct", 0.0))
            if quote
            else (current_price / prev_close - 1) * 100
            if prev_close
            else 0.0
        )
        latest_volume = volumes[-1] if volumes else 0.0
        avg_volume = sum(volumes) / len(volumes) if volumes else 0.0

        return {
            "ticker": ticker,
            "name": profile.get("name") or ticker if isinstance(profile, dict) else ticker,
            "sector": metrics.get("gicsSector") or metrics.get("finnhubIndustry") or "Unknown",
            "industry": profile.get("finnhubIndustry") if isinstance(profile, dict) else "Unknown",
            "market_cap": _market_cap(profile),
            "pe_ratio": _first_metric(
                metrics,
                "peNormalizedAnnual",
                "peBasicExclExtraTTM",
                "peTTM",
            ),
            "peg_ratio": _first_metric(metrics, "pegRatio"),
            "revenue_growth": _normalize_growth(
                _first_metric(
                    metrics,
                    "revenueGrowthTTMYoy",
                    "revenueGrowthQuarterlyYoy",
                    "revenueGrowth3Y",
                )
            ),
            "current_price": current_price,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "volume": latest_volume,
            "avg_volume": avg_volume,
            "volume_ratio": latest_volume / avg_volume if avg_volume > 0 else 1.0,
            "high_52w": _first_metric(metrics, "52WeekHigh"),
            "low_52w": _first_metric(metrics, "52WeekLow"),
            "rsi_14": _calc_rsi_values(closes, 14),
            "sma_20": _calc_sma(closes, 20),
            "sma_50": _calc_sma(closes, 50),
            "sma_200": _calc_sma(closes, 200),
            "beta": _first_metric(metrics, "beta"),
            "source": "finnhub",
        }
    except Exception as e:
        logger.warning(f"Finnhub stock data error for {ticker}: {e}")
        return None


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
    settings = get_settings()
    if settings.is_finnhub_configured():
        result = await _fetch_return_finnhub("SPY", start_date)
        if result is not None:
            return result
    return await asyncio.to_thread(_fetch_spy_return_sync, start_date)


async def _fetch_return_finnhub(ticker: str, start_date: str) -> float | None:
    settings = get_settings()
    try:
        import httpx

        start = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
        now = datetime.now(UTC)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                f"{settings.finnhub_base_url}/stock/candle",
                params={
                    "symbol": ticker,
                    "resolution": "D",
                    "from": int(start.timestamp()),
                    "to": int(now.timestamp()),
                    "token": settings.finnhub_api_key,
                },
            )
        if response.status_code != 200:
            return None
        closes = _float_list(response.json().get("c"))
        if len(closes) < 2:
            return None
        return (closes[-1] / closes[0] - 1) * 100
    except Exception:
        return None


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
    quote = await fetch_quote_finnhub(ticker)
    if quote:
        return float(quote["price"])
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


def _period_delta(period: str) -> timedelta:
    normalized = period.strip().lower()
    if normalized.endswith("d"):
        return timedelta(days=max(int(normalized[:-1] or "1"), 1))
    if normalized.endswith("mo"):
        return timedelta(days=max(int(normalized[:-2] or "1"), 1) * 31)
    if normalized.endswith("y"):
        return timedelta(days=max(int(normalized[:-1] or "1"), 1) * 366)
    return timedelta(days=186)


def _float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    result: list[float] = []
    for item in value:
        try:
            result.append(float(item))
        except (TypeError, ValueError):
            continue
    return result


def _calc_sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _calc_rsi_values(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        delta = current - previous
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _first_metric(metrics: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metrics.get(key)
        if value not in {None, ""}:
            return value
    return None


def _market_cap(profile: Any) -> float | None:
    if not isinstance(profile, dict):
        return None
    value = profile.get("marketCapitalization")
    try:
        return float(value) * 1_000_000 if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_growth(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100 if abs(number) > 1 else number
