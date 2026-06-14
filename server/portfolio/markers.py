"""Portfolio markers for personalization without trade details."""

import re
from typing import Any

from sqlalchemy import select

from server.db.engine import get_session_factory
from server.db.models import PortfolioMarker

HOLDING_MARKER_TYPE = "holding"


async def add_portfolio_holding_marker(ticker: str, note: str | None = None) -> dict[str, Any]:
    """Mark a ticker as held without recording quantity, cost basis, or a trade."""
    normalized = normalize_ticker(ticker)
    clean_note = note.strip() if isinstance(note, str) and note.strip() else None
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(PortfolioMarker).where(
                PortfolioMarker.ticker == normalized,
                PortfolioMarker.marker_type == HOLDING_MARKER_TYPE,
            )
        )
        marker = result.scalar_one_or_none()
        created = marker is None
        if marker is None:
            marker = PortfolioMarker(ticker=normalized, marker_type=HOLDING_MARKER_TYPE)
            session.add(marker)

        marker.note = clean_note
        marker.is_active = True
        await session.commit()
        await session.refresh(marker)
        return portfolio_marker_payload(marker, created=created)


async def remove_portfolio_holding_marker(ticker: str) -> dict[str, Any]:
    """Deactivate a portfolio holding marker without touching trade journal records."""
    normalized = normalize_ticker(ticker)
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(PortfolioMarker).where(
                PortfolioMarker.ticker == normalized,
                PortfolioMarker.marker_type == HOLDING_MARKER_TYPE,
                PortfolioMarker.is_active.is_(True),
            )
        )
        marker = result.scalar_one_or_none()
        if marker is None:
            return {
                "ticker": normalized,
                "removed": False,
                "message": f"{normalized} 没有持仓关注标记。",
            }

        marker.is_active = False
        await session.commit()
        await session.refresh(marker)
        return {
            "ticker": normalized,
            "removed": True,
            "message": f"{normalized} 持仓关注标记已移除。",
        }


async def get_portfolio_holding_markers() -> list[PortfolioMarker]:
    """Return active holding markers."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(
            select(PortfolioMarker)
            .where(
                PortfolioMarker.marker_type == HOLDING_MARKER_TYPE,
                PortfolioMarker.is_active.is_(True),
            )
            .order_by(PortfolioMarker.ticker.asc())
        )
        return list(result.scalars().all())


async def get_portfolio_holding_marker_tickers() -> list[str]:
    return [marker.ticker for marker in await get_portfolio_holding_markers()]


def portfolio_marker_payload(
    marker: PortfolioMarker, created: bool | None = None
) -> dict[str, Any]:
    payload = {
        "ticker": marker.ticker,
        "marker_type": marker.marker_type,
        "note": marker.note,
        "active": marker.is_active,
        "portfolio_marker": True,
        "created_at": marker.created_at.isoformat() if marker.created_at else None,
        "updated_at": marker.updated_at.isoformat() if marker.updated_at else None,
        "message": (
            f"{marker.ticker} 已记录为持仓关注标记（不代表真实交易，不记录数量/成本），"
            "后续相关消息会考虑对持仓的影响。"
        ),
    }
    if created is not None:
        payload["created"] = created
    return payload


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper().lstrip("$")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", normalized):
        raise ValueError("ticker must look like a US stock symbol")
    return normalized
