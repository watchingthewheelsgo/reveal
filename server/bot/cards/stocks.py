"""Stock alert card builders."""

from __future__ import annotations

from dataclasses import dataclass

from server.bot.cards._base import card_shell, markdown_elements, note


@dataclass(frozen=True)
class StockAlertCardData:
    ticker: str
    message: str
    previous_price: float | None = None
    current_price: float | None = None
    change_pct: float | None = None
    threshold_pct: float | None = None
    severity: str = "warning"
    footer: str = "继续回复本话题即可追问"


def stock_watch_alert_card(data: StockAlertCardData) -> dict:
    lines = [f"**{data.ticker.upper()} 股票观察提醒**", "", data.message]
    price_line = _price_line(data)
    if price_line:
        lines.extend(["", price_line])
    if data.threshold_pct is not None:
        lines.append(f"触发阈值: {data.threshold_pct:.1f}%")

    elements = markdown_elements("\n".join(lines))
    elements.append({"tag": "hr"})
    elements.append(note(data.footer))
    return card_shell(
        "Reveal · 股票异动",
        elements,
        template="red" if data.severity == "critical" else "orange",
    )


def _price_line(data: StockAlertCardData) -> str:
    parts = []
    if data.previous_price is not None and data.current_price is not None:
        parts.append(f"${data.previous_price:.2f} -> ${data.current_price:.2f}")
    elif data.current_price is not None:
        parts.append(f"现价: ${data.current_price:.2f}")
    if data.change_pct is not None:
        parts.append(f"变化: {data.change_pct:+.1f}%")
    return " · ".join(parts)
