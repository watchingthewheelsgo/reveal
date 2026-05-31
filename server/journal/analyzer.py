"""
LLM-powered journal analysis — weekly and monthly reports.
"""

import json

from server.journal.service import get_pnl_summary, get_trades_for_period
from server.llm.client import get_llm_client


def _trade_to_dict(trade) -> dict:
    return {
        "ticker": trade.ticker,
        "direction": trade.direction,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "quantity": trade.quantity,
        "pnl": trade.pnl,
        "date": str(trade.trade_date),
        "strategy": trade.strategy,
        "entry_reason": trade.entry_reason,
        "exit_reason": trade.exit_reason,
        "emotions_before": trade.emotions_before,
        "emotions_after": trade.emotions_after,
        "review": trade.review,
    }


async def generate_weekly_report() -> str | None:
    """Generate an LLM-powered weekly trading report."""
    llm = get_llm_client()
    if llm is None:
        return None

    trades = await get_trades_for_period("week")
    if not trades:
        return "本周暂无交易记录。"

    summary = await get_pnl_summary("week")
    trades_data = [_trade_to_dict(t) for t in trades]

    payload = json.dumps(
        {"summary": {k: str(v) for k, v in summary.items()}, "trades": trades_data},
        ensure_ascii=False,
    )
    report = await llm.analyze_journal(payload)
    return report


async def generate_monthly_report() -> str | None:
    """Generate an LLM-powered monthly trading report."""
    llm = get_llm_client()
    if llm is None:
        return None

    trades = await get_trades_for_period("month")
    if not trades:
        return "本月暂无交易记录。"

    summary = await get_pnl_summary("month")
    trades_data = [_trade_to_dict(t) for t in trades]

    payload = json.dumps(
        {"summary": {k: str(v) for k, v in summary.items()}, "trades": trades_data},
        ensure_ascii=False,
    )
    report = await llm.analyze_journal(payload)
    return report
