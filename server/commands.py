"""
Bot command handlers shared across Telegram and Feishu.
"""

import asyncio
import re
from datetime import UTC, datetime, time, timedelta

from loguru import logger

from server.bot.base import BotContext
from server.capabilities.planner import confirmation_plan, plan_from_command_route
from server.research.service import ResearchError


async def cmd_help(ctx: BotContext, adapter):
    from server.capabilities.registry import format_command_help

    await adapter.send_message(ctx.chat_id, format_command_help())


async def cmd_tools(ctx: BotContext, adapter):
    from server.capabilities.registry import format_capability_catalog

    await adapter.send_message(ctx.chat_id, format_capability_catalog())


async def cmd_status(ctx: BotContext, adapter):
    from config.settings import get_settings
    from server.db.engine import engine

    settings = get_settings()
    lines = [
        "*Reveal 系统状态*",
        "",
        f"Telegram Bot: {'✅' if settings.telegram_bot_token else '❌'}",
        f"飞书 Bot: {'✅' if settings.is_feishu_configured() else '❌'}",
        f"LLM: {'✅' if settings.is_llm_configured() else '❌'}",
        f"Finnhub: {'✅' if settings.is_finnhub_configured() else '❌'}",
        f"数据库: {'✅' if engine else '❌'}",
        f"时区: {settings.scheduler_timezone}",
        f"选股时间: {settings.daily_pick_time} (ET)",
    ]
    await adapter.send_message(ctx.chat_id, "\n".join(lines))


# ═══════════════════════════════════════════════════════════════════════════════
# Stock Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_pick(ctx: BotContext, adapter):
    """Manually trigger a daily pick."""
    await adapter.send_message(ctx.chat_id, "🔍 正在扫描美股市场，寻找今日最佳标的...")
    try:
        from server.stock.scanner import format_pick_message, run_daily_pick

        pick = await run_daily_pick()
        if pick is None:
            await adapter.send_message(ctx.chat_id, "❌ 选股失败，请稍后重试。")
            return

        text = format_pick_message(pick)
        await adapter.send_message(ctx.chat_id, text)
    except Exception as e:
        logger.error(f"Pick command error: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 选股异常，请稍后重试。")


async def cmd_track(ctx: BotContext, adapter):
    """Show tracking report."""
    ticker = ctx.args[0] if ctx.args else None
    from server.stock.tracker import get_tracking_report

    report = await get_tracking_report(ticker)
    await adapter.send_message(ctx.chat_id, report)


async def cmd_score(ctx: BotContext, adapter):
    """Score a specific ticker."""
    if not ctx.args:
        await adapter.send_message(ctx.chat_id, "用法: /score AAPL")
        return
    ticker = ctx.args[0].upper()
    await adapter.send_message(ctx.chat_id, f"🔍 正在分析 {ticker}...")

    try:
        from server.stock.data import fetch_stock_data
        from server.stock.scorer import score_stock

        data = await fetch_stock_data(ticker)
        if data is None:
            await adapter.send_message(ctx.chat_id, f"❌ 无法获取 {ticker} 的数据")
            return

        scored = await score_stock(data)
        from server.stock.scanner import format_pick_message

        text = format_pick_message(scored)
        await adapter.send_message(ctx.chat_id, text)
    except Exception as e:
        logger.error(f"Score command error: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 评分异常，请稍后重试。")


async def cmd_quote(ctx: BotContext, adapter):
    """Show real-time-ish quote for a ticker: /quote AAPL"""
    if not ctx.args:
        await adapter.send_message(ctx.chat_id, "用法: /quote AAPL")
        return
    ticker = ctx.args[0].upper()
    try:
        from server.capabilities.market import format_stock_quote, get_stock_quote_payload

        await adapter.send_message(
            ctx.chat_id,
            format_stock_quote(await get_stock_quote_payload(ticker), ticker),
        )
    except Exception as e:
        logger.exception(f"Quote command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 报价查询失败，请稍后重试。")


async def cmd_technical(ctx: BotContext, adapter):
    """Show technical indicators for a ticker: /technical AAPL"""
    if not ctx.args:
        await adapter.send_message(ctx.chat_id, "用法: /technical AAPL")
        return
    ticker = ctx.args[0].upper()
    try:
        from server.capabilities.market import (
            format_technical_analysis,
            get_technical_analysis_payload,
        )

        await adapter.send_message(
            ctx.chat_id,
            format_technical_analysis(await get_technical_analysis_payload(ticker), ticker),
        )
    except Exception as e:
        logger.exception(f"Technical command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 技术指标查询失败，请稍后重试。")


async def cmd_news(ctx: BotContext, adapter):
    """Show recent company news: /news AAPL"""
    if not ctx.args:
        await adapter.send_message(ctx.chat_id, "用法: /news AAPL")
        return
    ticker = ctx.args[0].upper()
    try:
        from server.capabilities.market import format_stock_news, get_stock_news_payload

        await adapter.send_message(
            ctx.chat_id,
            format_stock_news(await get_stock_news_payload(ticker, limit=8), ticker),
        )
    except Exception as e:
        logger.exception(f"News command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 新闻查询失败，请稍后重试。")


async def cmd_portfolio(ctx: BotContext, adapter):
    """Show current open portfolio positions."""
    try:
        from server.capabilities.market import format_portfolio, get_portfolio_payload

        await adapter.send_message(ctx.chat_id, format_portfolio(await get_portfolio_payload()))
    except Exception as e:
        logger.exception(f"Portfolio command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 持仓查询失败，请稍后重试。")


async def cmd_history(ctx: BotContext, adapter):
    """Show past research for a ticker: /history AAPL"""
    if not ctx.args:
        await adapter.send_message(ctx.chat_id, "用法: /history AAPL")
        return
    ticker = ctx.args[0].upper()
    try:
        from server.capabilities.market import (
            format_research_history,
            get_research_history_payload,
        )

        await adapter.send_message(
            ctx.chat_id,
            format_research_history(await get_research_history_payload(ticker, limit=5), ticker),
        )
    except Exception as e:
        logger.exception(f"History command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 历史研究查询失败，请稍后重试。")


# ═══════════════════════════════════════════════════════════════════════════════
# Journal Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_log(ctx: BotContext, adapter):
    """Record a trade: /log buy/sell TICKER PRICE [QTY]"""
    from server.journal.service import add_note, add_trade, close_trade

    if not ctx.args:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n/log buy AAPL 150 100\n/log sell AAPL 155\n/log note AAPL 备注内容",
        )
        return

    action = ctx.args[0].lower()

    if action == "buy" and len(ctx.args) >= 3:
        ticker = ctx.args[1].upper()
        try:
            price = float(ctx.args[2])
            qty = int(ctx.args[3]) if len(ctx.args) > 3 else 100
        except ValueError:
            await adapter.send_message(ctx.chat_id, "价格和数量必须是数字。")
            return
        trade = await add_trade(ticker, "long", price, qty)
        await adapter.send_message(
            ctx.chat_id, f"✅ 买入记录: {trade.ticker} x{trade.quantity} @ ${trade.entry_price:.2f}"
        )

    elif action == "short" and len(ctx.args) >= 3:
        ticker = ctx.args[1].upper()
        try:
            price = float(ctx.args[2])
            qty = int(ctx.args[3]) if len(ctx.args) > 3 else 100
        except ValueError:
            await adapter.send_message(ctx.chat_id, "价格和数量必须是数字。")
            return
        trade = await add_trade(ticker, "short", price, qty)
        await adapter.send_message(
            ctx.chat_id, f"✅ 做空记录: {trade.ticker} x{trade.quantity} @ ${trade.entry_price:.2f}"
        )

    elif action == "sell" and len(ctx.args) >= 2:
        ticker = ctx.args[1].upper()
        try:
            price = float(ctx.args[2]) if len(ctx.args) > 2 else 0
        except ValueError:
            await adapter.send_message(ctx.chat_id, "价格必须是数字。")
            return
        if price == 0:
            await adapter.send_message(ctx.chat_id, "用法: /log sell TICKER PRICE")
            return
        trade = await close_trade(ticker, price)
        if trade:
            await adapter.send_message(
                ctx.chat_id,
                f"✅ 卖出记录: {trade.ticker} | PnL: ${trade.pnl:+.2f} | "
                f"入场 ${trade.entry_price:.2f} → 出场 ${trade.exit_price:.2f}",
            )
        else:
            await adapter.send_message(ctx.chat_id, f"❌ 找不到 {ticker} 的未平仓记录")

    elif action == "note" and len(ctx.args) >= 2:
        ticker = ctx.args[1].upper()
        note = " ".join(ctx.args[2:]) if len(ctx.args) > 2 else ""
        trade = await add_note(ticker, note)
        if trade:
            await adapter.send_message(ctx.chat_id, f"✅ 已添加备注到 {ticker}")
        else:
            await adapter.send_message(ctx.chat_id, f"❌ 找不到 {ticker} 的交易记录")

    else:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n/log buy TICKER PRICE QTY\n/log sell TICKER PRICE\n/log note TICKER 备注",
        )


async def cmd_journal(ctx: BotContext, adapter):
    """View journal: /journal [today|week|month|year|all]"""
    from server.journal.service import format_journal, get_trades_for_period

    period = ctx.args[0].lower() if ctx.args else "today"
    if period not in ("today", "week", "month", "year", "all"):
        period = "today"

    trades = await get_trades_for_period(period)
    text = format_journal(trades, period)
    await adapter.send_message(ctx.chat_id, text)

    # For week/month, offer LLM analysis
    if period in ("week", "month") and trades:
        from server.llm.client import get_llm_client

        llm = get_llm_client()
        if llm and len([t for t in trades if t.pnl is not None]) >= 3:
            await adapter.send_message(ctx.chat_id, "🤖 AI 分析中...")
            if period == "week":
                from server.journal.analyzer import generate_weekly_report

                report = await generate_weekly_report()
            else:
                from server.journal.analyzer import generate_monthly_report

                report = await generate_monthly_report()
            if report:
                await adapter.send_message(ctx.chat_id, f"*🤖 AI {period}度分析*\n\n{report}")


async def cmd_pnl(ctx: BotContext, adapter):
    """Quick P&L summary: /pnl [period]"""
    from server.journal.service import format_pnl, get_pnl_summary

    period = ctx.args[0].lower() if ctx.args else "month"
    if period not in ("today", "week", "month", "year", "all"):
        period = "month"

    summary = await get_pnl_summary(period)
    text = format_pnl(summary)

    # Best/worst detail
    if summary.get("best_trade"):
        bt = summary["best_trade"]
        text += f"\n最佳: {bt.ticker} ${bt.pnl:+.2f}"
    if summary.get("worst_trade"):
        wt = summary["worst_trade"]
        text += f"\n最差: {wt.ticker} ${wt.pnl:+.2f}"

    await adapter.send_message(ctx.chat_id, text)


# ═══════════════════════════════════════════════════════════════════════════════
# Research Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_deep(ctx: BotContext, adapter):
    """Run deep research for a social post: /deep latest|POST_ID [focus]"""
    if not ctx.args:
        await adapter.send_message(
            ctx.chat_id, "用法: /deep latest [研究重点] 或 /deep POST_ID [研究重点]"
        )
        return

    post_ref = ctx.args[0]
    focus = " ".join(ctx.args[1:]).strip()
    _spawn_background_task(
        _run_deep_research_job(ctx.chat_id, post_ref, focus, adapter, ctx.reply_to_message_id),
        "deep research",
    )


async def cmd_ask(ctx: BotContext, adapter):
    """Ask about a social post: /ask latest|POST_ID question"""
    if len(ctx.args) < 2:
        await adapter.send_message(ctx.chat_id, "用法: /ask latest 问题 或 /ask POST_ID 问题")
        return

    post_ref = ctx.args[0]
    question = " ".join(ctx.args[1:]).strip()
    _spawn_background_task(
        _run_research_ask_job(ctx.chat_id, post_ref, question, adapter, ctx.reply_to_message_id),
        "research ask",
    )


async def cmd_research(ctx: BotContext, adapter):
    """Start research: /research latest|POST_ID|TICKER|freeform question [focus]"""
    if not ctx.args:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n"
            "/research latest [研究重点] — 基于最新推文\n"
            "/research NVDA [研究重点] — 研究某只股票\n"
            "/research 美联储加息影响 — 自由研究",
        )
        return

    first_arg = ctx.args[0]
    focus = " ".join(ctx.args[1:]).strip()

    # Route: latest or numeric → tweet research
    if first_arg == "latest" or first_arg.isdigit():
        await _start_research_topic(ctx, adapter, first_arg, focus)
        return

    # Route: looks like a ticker (1-5 uppercase letters) → ticker research
    if _looks_like_ticker(first_arg):
        await _start_ticker_research(ctx, adapter, first_arg.upper(), focus)
        return

    # Route: freeform query
    query = " ".join(ctx.args).strip()
    await _start_freeform_research(ctx, adapter, query)


async def cmd_topic(ctx: BotContext, adapter):
    """Manage a research topic: /topic start|summary|stop"""
    sub = ctx.args[0].lower() if ctx.args else "status"
    try:
        from server.research.service import (
            get_active_topic,
            stop_topic,
        )

        if sub == "start" and len(ctx.args) >= 2:
            post_ref = ctx.args[1]
            focus = " ".join(ctx.args[2:]).strip()
            await _start_research_topic(ctx, adapter, post_ref, focus)
        elif sub == "summary":
            _spawn_background_task(
                _run_topic_summary_job(ctx.chat_id, adapter, ctx.reply_to_message_id),
                "topic summary",
            )
        elif sub in {"stop", "reset"}:
            stopped = await stop_topic(ctx.chat_id)
            await adapter.send_message(
                ctx.chat_id, "✅ 已结束当前研究线程。" if stopped else "当前没有活跃研究线程。"
            )
        elif sub == "status":
            topic = await get_active_topic(ctx.chat_id)
            if topic:
                await adapter.send_message(
                    ctx.chat_id,
                    f"当前研究线程: #{topic.id}\n主题: {topic.topic or '未命名'}",
                )
            else:
                await adapter.send_message(
                    ctx.chat_id, "当前没有活跃研究话题。用 /research latest 开启。"
                )
        else:
            await adapter.send_message(
                ctx.chat_id,
                "用法:\n"
                "/research latest [研究重点]\n"
                "/topic start latest [研究重点]\n"
                "/topic summary\n"
                "/topic stop",
            )
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Topic command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 研究线程操作失败，请稍后重试。")


def _looks_like_ticker(text: str) -> bool:
    """Check if text looks like a US stock ticker (1-5 uppercase alpha chars)."""
    cleaned = text.upper().strip()
    return bool(cleaned) and len(cleaned) <= 5 and cleaned.isalpha()


async def _start_ticker_research(
    ctx: BotContext,
    adapter,
    ticker: str,
    focus: str = "",
) -> None:
    try:
        await adapter.send_message(ctx.chat_id, f"🔎 正在研究 {ticker}，完成后会推送结果。")
        _spawn_background_task(
            _run_ticker_research_job(ctx.chat_id, ticker, focus, adapter),
            f"ticker research {ticker}",
        )
    except Exception as e:
        logger.exception(f"Ticker research start failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 研究启动失败，请稍后重试。")


async def _start_freeform_research(
    ctx: BotContext,
    adapter,
    query: str,
) -> None:
    try:
        await adapter.send_message(ctx.chat_id, "🔎 已开始自由研究，完成后会推送结果。")
        _spawn_background_task(
            _run_freeform_research_job(ctx.chat_id, query, adapter),
            "freeform research",
        )
    except Exception as e:
        logger.exception(f"Freeform research start failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 研究启动失败，请稍后重试。")


async def _start_research_topic(
    ctx: BotContext,
    adapter,
    post_ref: str,
    focus: str = "",
) -> None:
    try:
        from server.research.service import start_topic

        topic = await start_topic(ctx.chat_id, post_ref, focus)
        await adapter.send_message(
            ctx.chat_id,
            f"✅ 已建立研究话题 #{topic.id}，绑定消息 #{topic.source_id}。\n"
            "现在直接发送普通消息即可继续追问；需要 Agent 主动深挖时使用 "
            f"/deep {topic.source_id}。",
        )
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Start research topic failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 建立研究话题失败，请稍后重试。")


async def handle_plain_message(ctx: BotContext, adapter):
    """Route plain text: bound IM thread → active topic → intent classification."""
    text = ctx.text.strip()
    if not text:
        return
    try:
        from server.research.service import get_active_topic

        # Priority 1: a reply under a pushed alert/card is bound to that source.
        if ctx.reply_to_message_id:
            routed = await _route_bound_reply(ctx, adapter, text)
            if routed:
                return

        # Priority 2: high-confidence Twitter operations in natural language.
        if await _try_route_natural_command(ctx, adapter, text):
            return

        # Priority 3: route to active research topic
        topic = await get_active_topic(ctx.chat_id)
        if topic is not None:
            _spawn_background_task(
                _run_topic_message_job(ctx.chat_id, text, adapter, ctx.reply_to_message_id),
                "topic message",
            )
            return

        # Priority 4: classify intent with LLM
        from server.llm.client import classify_intent_locally, get_llm_client

        llm = get_llm_client()
        intent = await llm.classify_intent(text) if llm else classify_intent_locally(text)
        intent_type = intent.get("intent", "chat")
        ticker = intent.get("ticker")
        query = intent.get("query") or text

        if intent_type == "research":
            if ticker:
                await _start_ticker_research(ctx, adapter, ticker.upper(), query)
            else:
                await _start_freeform_research(ctx, adapter, query)

        elif intent_type == "question":
            await adapter.send_message(ctx.chat_id, "🤔 正在查询...")
            _spawn_background_task(
                _run_quick_question_job(ctx.chat_id, query, adapter),
                "quick question",
            )

        elif intent_type == "trade":
            await adapter.send_message(
                ctx.chat_id,
                "交易记录请使用命令格式:\n/log buy TICKER PRICE QTY\n/log sell TICKER PRICE",
            )

        elif intent_type == "status":
            await cmd_status(ctx, adapter)

        else:
            _spawn_background_task(
                _run_chat_reply_job(ctx.chat_id, text, adapter),
                "chat reply",
            )

    except Exception as e:
        logger.exception(f"Message handling failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 消息处理失败。")


async def _route_bound_reply(ctx: BotContext, adapter, text: str) -> bool:
    from server.bot.bindings import resolve_message_binding

    binding = await resolve_message_binding(ctx.chat_id, ctx.reply_to_message_id)
    if binding is None:
        return False
    if binding.source_type != "twitter":
        return False

    try:
        from server.research.service import get_or_start_topic_for_post

        topic = await get_or_start_topic_for_post(ctx.chat_id, str(binding.source_id), "")
        _spawn_background_task(
            _run_topic_message_job(
                ctx.chat_id,
                text,
                adapter,
                ctx.reply_to_message_id,
                topic.id,
            ),
            "bound topic message",
        )
        return True
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
        return True


async def _try_route_natural_command(ctx: BotContext, adapter, text: str) -> bool:
    if await _try_route_twitter_natural_language(ctx, adapter, text):
        return True

    route = _parse_general_natural_command(text)
    if route is None:
        confirmation = _natural_command_confirmation_prompt(text)
        if not confirmation:
            return False
        plan = confirmation_plan(confirmation, text)
        logger.info(
            "Natural language plan needs confirmation: confidence={} reason={}",
            plan.confidence,
            plan.reason,
        )
        await adapter.send_message(ctx.chat_id, confirmation)
        return True

    plan = plan_from_command_route(route, text)
    if plan.needs_confirmation or not plan.command:
        await adapter.send_message(ctx.chat_id, plan.confirmation_prompt or "请确认要执行的操作。")
        return True

    command = plan.command
    args = plan.args
    logger.info(
        "Natural language plan: capability={} command={} args={} confidence={} reason={}",
        plan.capability_id or "-",
        command,
        args,
        plan.confidence,
        plan.reason,
    )
    routed_ctx = _ctx_for_command(ctx, command, args)
    handlers = {
        "help": cmd_help,
        "tools": cmd_tools,
        "status": cmd_status,
        "pick": cmd_pick,
        "quote": cmd_quote,
        "technical": cmd_technical,
        "news": cmd_news,
        "track": cmd_track,
        "score": cmd_score,
        "portfolio": cmd_portfolio,
        "history": cmd_history,
        "deep": cmd_deep,
        "research": cmd_research,
        "pnl": cmd_pnl,
        "alert": cmd_alert,
        "briefing": cmd_briefing,
        "digest": cmd_digest,
        "summary": cmd_summary,
    }
    handler = handlers.get(command)
    if handler is None:
        return False
    await handler(routed_ctx, adapter)
    return True


def _ctx_for_command(ctx: BotContext, command: str, args: list[str]) -> BotContext:
    return BotContext(
        chat_id=ctx.chat_id,
        user_id=ctx.user_id,
        text=ctx.text,
        command=command,
        args=args,
        raw_data=ctx.raw_data,
        reply_to_message_id=ctx.reply_to_message_id,
    )


def _parse_general_natural_command(text: str) -> dict | None:
    lowered = text.lower().strip()
    ticker = _extract_ticker(text)

    if lowered in {"help", "status", "pnl"}:
        return {"command": lowered, "args": []}
    if lowered in {"tools", "capabilities", "skills"}:
        return {"command": "tools", "args": []}
    if any(phrase in text for phrase in ("帮助", "怎么用", "有哪些命令")):
        return {"command": "help", "args": []}
    if any(
        phrase in text
        for phrase in ("有哪些工具", "有什么工具", "有哪些能力", "有哪些技能", "你能做什么")
    ):
        return {"command": "tools", "args": []}
    if "系统状态" in text or text in {"状态", "服务状态"}:
        return {"command": "status", "args": []}
    if "每日简报" in text or "市场简报" in text or lowered == "briefing":
        return {"command": "briefing", "args": []}
    if "twitter 日报" in lowered or "推特日报" in text or lowered.startswith("digest"):
        return {
            "command": "digest",
            "args": [_extract_days_ago(text)] if _extract_days_ago(text) else [],
        }
    if ("日报" in text or "总结" in text) and (
        "推特" in text or "twitter" in lowered or "tweet" in lowered
    ):
        username = _extract_twitter_username(text)
        if username:
            args = [f"@{username}"]
            if target_date := _extract_iso_date(text):
                args.append(target_date)
            return {"command": "summary", "args": args}
        return {"command": "digest", "args": []}
    if "选股" in text or "推荐股票" in text or lowered in {"pick", "daily pick"}:
        return {"command": "pick", "args": []}
    if any(word in text or word in lowered for word in ("持仓", "仓位", "portfolio")):
        return {"command": "portfolio", "args": []}
    if "盈亏" in text or "pnl" in lowered:
        return {"command": "pnl", "args": []}
    if "告警" in text or "alert" in lowered:
        args = ["check"] if any(word in text for word in ("检查", "跑一下", "立即")) else []
        return {"command": "alert", "args": args}
    if ("新闻" in text or "news" in lowered) and ticker:
        return {"command": "news", "args": [ticker]}
    if (
        any(word in text or word in lowered for word in ("技术指标", "technical", "rsi", "均线"))
        and ticker
    ):
        return {"command": "technical", "args": [ticker]}
    if (
        any(
            word in text or word in lowered for word in ("报价", "现价", "多少钱", "price", "quote")
        )
        and ticker
    ):
        return {"command": "quote", "args": [ticker]}
    if any(word in text for word in ("历史研究", "之前研究", "过往研究")) and ticker:
        return {"command": "history", "args": [ticker]}
    if ("评分" in text or "打分" in text or "score" in lowered) and ticker:
        return {"command": "score", "args": [ticker]}
    if ("追踪" in text or "tracking" in lowered or "track" in lowered) and "推特" not in text:
        return {"command": "track", "args": [ticker] if ticker else []}
    if ("深挖" in text or "deep" in lowered) and ("最新" in text or "latest" in lowered):
        focus = _remaining_focus(text, ("深挖", "最新", "推文", "twitter", "tweet", "latest"))
        return {"command": "deep", "args": ["latest", *focus]}
    if ("研究" in text or "research" in lowered) and ("最新" in text or "latest" in lowered):
        focus = _remaining_focus(text, ("研究", "最新", "推文", "twitter", "tweet", "latest"))
        return {"command": "research", "args": ["latest", *focus]}
    return None


def _natural_command_confirmation_prompt(text: str) -> str | None:
    lowered = text.lower()
    if any(word in lowered or word in text for word in ("watch", "关注列表", "监控")):
        return (
            "你是不是想执行 Twitter 关注列表操作？\n"
            "可以直接说：把 @username 加到 watch list，或把 @username 从 watch list 移除。"
        )
    if any(word in text for word in ("评分", "打分")):
        return "你是不是想执行股票评分？请带上 ticker，例如：给 MRVL 打分。"
    if "告警" in text:
        return "你是不是想查看或检查告警？可以说：查看告警配置，或立即检查告警。"
    if "推特" in text or "twitter" in lowered or "tweet" in lowered:
        return (
            "你是不是想查询 Twitter 更新？\n"
            "可以说：@username 最新 5 条推特、@username 昨天发了什么推特，"
            "或 有没有关于 MRVL 的推特。"
        )
    return None


def _extract_ticker(text: str) -> str | None:
    cashtag = re.search(r"\$([A-Za-z]{1,5})\b", text)
    if cashtag:
        return cashtag.group(1).upper()
    match = re.search(r"\b([A-Z]{1,5})\b", text)
    if match:
        return match.group(1).upper()
    return None


def _remaining_focus(text: str, stop_words: tuple[str, ...]) -> list[str]:
    cleaned = text
    for word in stop_words:
        cleaned = re.sub(re.escape(word), " ", cleaned, flags=re.IGNORECASE)
    return [part for part in re.split(r"\s+", cleaned.strip()) if part]


async def _try_route_twitter_natural_language(ctx: BotContext, adapter, text: str) -> bool:
    intent = _parse_twitter_natural_language(text)
    if not intent:
        return False

    action = intent["action"]
    if action in {"watch_add", "watch_remove"}:
        username = intent["username"]
        from server.social.monitor import set_twitter_account_active

        is_active = action == "watch_add"
        await set_twitter_account_active(username, is_active)
        if is_active:
            await adapter.send_message(
                ctx.chat_id,
                f"✅ 已添加 @{username}\n正在获取最近最多 10 条推文；后续会按缓存增量检查。",
            )
            _spawn_background_task(
                _run_twitter_check_job(
                    [username],
                    ctx.chat_id,
                    adapter,
                    "✅ 首次检查完成，没有获取到新推文。",
                ),
                "twitter natural watch add",
            )
        else:
            await adapter.send_message(ctx.chat_id, f"✅ 已移除 @{username}")
        return True

    if action == "latest":
        username = intent["username"]
        limit = intent["limit"]
        await adapter.send_message(ctx.chat_id, f"🔎 正在获取 @{username} 最新 {limit} 条推文...")
        _spawn_background_task(
            _run_twitter_latest_job(ctx.chat_id, username, limit, adapter),
            "twitter latest lookup",
        )
        return True

    if action == "yesterday":
        username = intent["username"]
        await adapter.send_message(ctx.chat_id, f"🔎 正在查 @{username} 昨天的推文...")
        _spawn_background_task(
            _run_twitter_yesterday_job(ctx.chat_id, username, adapter),
            "twitter yesterday lookup",
        )
        return True

    if action == "search":
        query = intent["query"]
        posts = await _search_cached_twitter_posts(query, limit=intent["limit"])
        await adapter.send_message(
            ctx.chat_id,
            _format_twitter_posts(f"本地缓存中关于「{query}」的 Twitter 更新", posts),
        )
        return True

    return False


def _parse_twitter_natural_language(text: str) -> dict | None:
    lowered = text.lower()
    has_twitter_keyword = any(
        keyword in lowered or keyword in text
        for keyword in ("推特", "twitter", "tweet", "tweets", "watch", "监控", "关注列表")
    )
    if not has_twitter_keyword:
        return None

    if username := _parse_watch_username(text, add=True):
        return {"action": "watch_add", "username": username}
    if username := _parse_watch_username(text, add=False):
        return {"action": "watch_remove", "username": username}

    if ("最新" in text or "latest" in lowered) and (
        "推特" in text or "twitter" in lowered or "tweet" in lowered
    ):
        username = _extract_twitter_username(text)
        if username:
            return {"action": "latest", "username": username, "limit": _extract_limit(text)}

    if "昨天" in text and ("推特" in text or "twitter" in lowered or "tweet" in lowered):
        username = _extract_twitter_username(text)
        if username:
            return {"action": "yesterday", "username": username}

    query = _parse_cached_twitter_search_query(text)
    if query:
        return {"action": "search", "query": query, "limit": _extract_limit(text, default=8)}

    return None


def _parse_watch_username(text: str, add: bool) -> str | None:
    lowered = text.lower()
    if not any(keyword in lowered or keyword in text for keyword in ("watch", "关注", "监控")):
        return None

    action_words = (
        ("加到", "加入", "添加", "关注", "add")
        if add
        else ("移除", "删除", "取消", "取关", "remove", "delete")
    )
    if not any(word in lowered or word in text for word in action_words):
        return None
    return _extract_twitter_username(text)


def _extract_twitter_username(text: str) -> str | None:
    url_match = re.search(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,20})(?:\b|/)", text)
    if url_match:
        return url_match.group(1)

    at_match = re.search(r"@([A-Za-z0-9_]{1,20})", text)
    if at_match:
        return at_match.group(1)

    patterns = [
        r"(?:把|将)?\s*([A-Za-z][A-Za-z0-9_]{1,19})\s*(?:加到|加入|添加|移除|删除|取消|取关)",
        r"([A-Za-z][A-Za-z0-9_]{1,19}).{0,8}(?:最新|昨天)",
        r"([A-Za-z][A-Za-z0-9_]{1,19})\s*(?:最新|昨天)",
        r"(?:最新|昨天)\s*([A-Za-z][A-Za-z0-9_]{1,19})",
    ]
    ignored = {"twitter", "tweet", "tweets", "watch", "list", "latest"}
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            username = match.group(1)
            if username.lower() not in ignored:
                return username
    return None


def _extract_limit(text: str, default: int = 5, max_limit: int = 20) -> int:
    match = re.search(r"(\d{1,2})\s*(?:条|篇|个|tweets?)?", text, flags=re.IGNORECASE)
    if not match:
        return default
    try:
        return max(1, min(max_limit, int(match.group(1))))
    except ValueError:
        return default


def _parse_cached_twitter_search_query(text: str) -> str | None:
    patterns = [
        r"关于\s*([A-Za-z0-9.$_\-\s\u4e00-\u9fff]{1,40}?)\s*(?:的)?(?:推特|twitter|tweets?)",
        r"有没有\s*([A-Za-z0-9.$_\-\s\u4e00-\u9fff]{1,40}?)\s*(?:相关)?(?:推特|twitter|tweets?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        query = re.sub(r"\s+", " ", match.group(1)).strip(" ，,。.?？")
        if query and query not in {"某个公司", "某某某"}:
            return query
    return None


def _extract_days_ago(text: str) -> str | None:
    match = re.search(r"(\d{1,2})\s*天前", text)
    if not match:
        return None
    return match.group(1)


def _extract_iso_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


async def _run_twitter_latest_job(chat_id: str, username: str, limit: int, adapter) -> None:
    try:
        from server.social.monitor import cache_user_tweets

        posts = await cache_user_tweets(username, count=limit)
        posts = sorted(posts, key=lambda post: post.posted_at, reverse=True)[:limit]
        await adapter.send_message(chat_id, _format_twitter_posts(f"@{username} 最新推文", posts))
    except Exception as e:
        logger.exception(f"Twitter latest lookup failed: {e}")
        await adapter.send_message(chat_id, "❌ 获取最新推文失败，请稍后重试。")


async def _run_twitter_yesterday_job(chat_id: str, username: str, adapter) -> None:
    try:
        from server.social.monitor import cache_user_tweets

        await cache_user_tweets(username, count=50)
        start_utc, end_utc = _local_day_range_utc(days_ago=1)
        posts = await _search_cached_twitter_posts(
            "",
            limit=20,
            username=username,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        await adapter.send_message(chat_id, _format_twitter_posts(f"@{username} 昨天的推文", posts))
    except Exception as e:
        logger.exception(f"Twitter yesterday lookup failed: {e}")
        await adapter.send_message(chat_id, "❌ 查询昨天推文失败，请稍后重试。")


async def _search_cached_twitter_posts(
    query: str,
    limit: int = 8,
    username: str | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> list:
    from sqlalchemy import desc, select

    from server.db.engine import get_session_factory
    from server.db.models import SocialPost

    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(SocialPost)
        if username:
            statement = statement.where(SocialPost.username == username.strip().lstrip("@"))
        if start_utc:
            statement = statement.where(SocialPost.posted_at >= start_utc)
        if end_utc:
            statement = statement.where(SocialPost.posted_at < end_utc)
        result = await session.execute(
            statement.order_by(desc(SocialPost.posted_at), desc(SocialPost.id)).limit(300)
        )
        posts = list(result.scalars().all())

    if query:
        posts = [post for post in posts if _post_matches_cached_query(post, query)]
    return posts[:limit]


def _post_matches_cached_query(post, query: str) -> bool:
    needle = query.strip().lower()
    ticker = query.strip().upper().lstrip("$")
    haystack = " ".join(
        str(value or "")
        for value in (
            post.username,
            post.content,
            post.summary,
            post.translated_content,
            " ".join(str(item) for item in post.topics or []),
            " ".join(str(item) for item in post.links or []),
        )
    ).lower()
    if needle and needle in haystack:
        return True
    return ticker in {str(item).upper().lstrip("$") for item in post.mentioned_tickers or []}


def _format_twitter_posts(title: str, posts: list) -> str:
    if not posts:
        return f"{title}\n\n本地缓存里暂时没有匹配内容。"

    lines = [f"*{title}*", ""]
    for post in posts:
        posted = post.posted_at.astimezone().strftime("%Y-%m-%d %H:%M")
        preview = _compact_text(post.summary or post.content or "（无正文）", 180)
        marker = "重点关注 · " if getattr(post, "is_noteworthy", False) else ""
        lines.append(f"#{post.id} {marker}@{post.username} · {posted}")
        lines.append(preview)
        if post.tweet_url:
            lines.append(post.tweet_url)
        lines.append("")
    return "\n".join(lines).strip()


def _compact_text(text: str, limit: int) -> str:
    clean = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def _local_day_range_utc(days_ago: int) -> tuple[datetime, datetime]:
    local_tz = datetime.now().astimezone().tzinfo
    now_local = datetime.now(local_tz)
    target_date = (now_local - timedelta(days=days_ago)).date()
    start_local = datetime.combine(target_date, time.min, tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _spawn_background_task(coro, label: str) -> None:
    task = asyncio.create_task(coro)

    def _log_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as e:
            logger.exception(f"{label} background task failed: {e}")

    task.add_done_callback(_log_result)


async def _run_deep_research_job(
    chat_id: str, post_ref: str, focus: str, adapter, reply_to: str = ""
) -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import run_deep_research

        await reporter.start("开始深度研究...")
        run = await run_deep_research(chat_id, post_ref, focus, on_progress=reporter.on_progress)
        post_label = f" · 推文 #{run.post.id}" if run.post else ""
        post_id = run.post.id if run.post else run.session_id
        text = (
            f"*研究线程 #{run.session_id}{post_label}*\n\n"
            f"{run.answer}\n\n"
            "继续追问可以直接发普通消息，或使用:\n"
            f"/ask {post_id} 你的问题\n"
            "/topic summary\n"
            "/topic stop"
        )
        await reporter.finish(text)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Deep research failed: {e}")
        await reporter.error("深挖失败，请稍后重试。")


async def _run_research_ask_job(
    chat_id: str, post_ref: str, question: str, adapter, reply_to: str = ""
) -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import ask_about_post

        await reporter.start("正在分析问题...")
        answer = await ask_about_post(chat_id, post_ref, question, on_progress=reporter.on_progress)
        await reporter.finish(answer)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Research ask failed: {e}")
        await reporter.error("回答失败，请稍后重试。")


async def _run_topic_summary_job(chat_id: str, adapter, reply_to: str = "") -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import summarize_topic

        await reporter.start("正在总结研究线程...")
        summary = await summarize_topic(chat_id, on_progress=reporter.on_progress)
        await reporter.finish(summary)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Topic summary failed: {e}")
        await reporter.error("研究线程总结失败，请稍后重试。")


async def _run_topic_message_job(
    chat_id: str,
    text: str,
    adapter,
    reply_to: str = "",
    session_id: int | None = None,
) -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import handle_topic_message

        await reporter.start("研究 Agent 分析中...")
        answer = await handle_topic_message(
            chat_id,
            text,
            session_id=session_id,
            on_progress=reporter.on_progress,
        )
        if answer:
            await reporter.finish(answer)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Topic message handling failed: {e}")
        await reporter.error("当前研究线程处理失败。")


async def _run_quick_question_job(chat_id: str, query: str, adapter) -> None:
    try:
        from server.llm.client import get_llm_client
        from server.research.context import build_portfolio_context

        context = await build_portfolio_context()
        llm = get_llm_client()
        if not llm:
            await adapter.send_message(chat_id, "❌ LLM 未配置。")
            return
        answer = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"你是 Reveal 美股交易助手。简洁回答用户问题。\n\n用户当前持仓:\n{context}"
                    ),
                },
                {"role": "user", "content": query},
            ],
            temperature=0.3,
        )
        await adapter.send_message(chat_id, answer)
    except Exception as e:
        logger.exception(f"Quick question failed: {e}")
        await adapter.send_message(chat_id, "❌ 回答失败，请稍后重试。")


async def _run_chat_reply_job(chat_id: str, text: str, adapter) -> None:
    try:
        from server.llm.client import get_llm_client

        llm = get_llm_client()
        if not llm:
            return
        answer = await llm.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是 Reveal 美股交易助手。简短友好地回复用户。"
                        "如果用户的问题涉及股票或投资，建议他们使用 /research 命令进行深度研究。"
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0.5,
            max_tokens=500,
        )
        await adapter.send_message(chat_id, answer)
    except Exception as e:
        logger.debug(f"Chat reply failed: {e}")


async def _run_ticker_research_job(
    chat_id: str, ticker: str, focus: str, adapter, reply_to: str = ""
) -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import research_ticker

        await reporter.start(f"正在研究 {ticker}...")
        run = await research_ticker(chat_id, ticker, focus, on_progress=reporter.on_progress)
        text = (
            f"*研究: {ticker} · 线程 #{run.session_id}*\n\n"
            f"{run.answer}\n\n"
            "继续追问可以直接发普通消息，或使用:\n"
            "/topic summary — 汇总\n"
            "/topic stop — 结束"
        )
        await reporter.finish(text)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Ticker research failed: {e}")
        await reporter.error("研究失败，请稍后重试。")


async def _run_freeform_research_job(chat_id: str, query: str, adapter, reply_to: str = "") -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import start_freeform_research

        await reporter.start("正在自由研究...")
        run = await start_freeform_research(chat_id, query, on_progress=reporter.on_progress)
        text = (
            f"*自由研究 · 线程 #{run.session_id}*\n\n"
            f"{run.answer}\n\n"
            "继续追问可以直接发普通消息，或使用:\n"
            "/topic summary — 汇总\n"
            "/topic stop — 结束"
        )
        await reporter.finish(text)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Freeform research failed: {e}")
        await reporter.error("研究失败，请稍后重试。")


async def _run_twitter_check_job(
    accounts: list[str],
    reply_chat_id: str,
    adapter,
    no_updates_text: str | None = None,
) -> None:
    try:
        from server.social.monitor import run_twitter_monitor
        from server.social.processor import TweetProcessor

        processor = TweetProcessor()
        total = await run_twitter_monitor(accounts, adapter, processor)
        if total == 0 and no_updates_text:
            await adapter.send_message(reply_chat_id, no_updates_text)
    except Exception as e:
        logger.exception(f"Twitter monitor check failed: {e}")
        await adapter.send_message(reply_chat_id, "❌ Twitter 检查失败，请稍后重试。")


# ═══════════════════════════════════════════════════════════════════════════════
# Twitter Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_twatch(ctx: BotContext, adapter):
    """Twitter watch commands: /twatch [list|add @user|del @user]"""
    sub = ctx.args[0] if ctx.args else "list"

    if sub == "list":
        from config.settings import get_settings
        from server.social.monitor import list_active_twitter_accounts

        accounts = await list_active_twitter_accounts(get_settings().twitter_accounts)
        if accounts:
            text = "*🐦 Twitter 监控列表*\n\n" + "\n".join(f"  • @{a}" for a in accounts)
        else:
            text = "暂无监控账号。\n用 /twatch add @用户名 添加。"
        await adapter.send_message(ctx.chat_id, text)

    elif sub == "add" and len(ctx.args) > 1:
        username = ctx.args[1].lstrip("@")
        from server.social.monitor import set_twitter_account_active

        await set_twitter_account_active(username, True)
        await adapter.send_message(
            ctx.chat_id,
            f"✅ 已添加 @{username}\n正在获取最近最多 10 条推文；后续会按缓存增量检查。",
        )
        _spawn_background_task(
            _run_twitter_check_job(
                [username],
                ctx.chat_id,
                adapter,
                "✅ 首次检查完成，没有获取到新推文。",
            ),
            "twitter watch add",
        )

    elif sub == "del" and len(ctx.args) > 1:
        username = ctx.args[1].lstrip("@")
        from server.social.monitor import set_twitter_account_active

        await set_twitter_account_active(username, False)
        await adapter.send_message(ctx.chat_id, f"✅ 已移除 @{username}")

    elif sub == "check":
        await adapter.send_message(ctx.chat_id, "🔍 正在检查新推文...")
        from config.settings import get_settings
        from server.social.monitor import list_active_twitter_accounts

        accounts = await list_active_twitter_accounts(get_settings().twitter_accounts)
        _spawn_background_task(
            _run_twitter_check_job(accounts, ctx.chat_id, adapter, "✅ 检查完成，没有新推文。"),
            "twitter manual check",
        )

    else:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n"
            "/twatch list — 查看列表\n"
            "/twatch add @user — 添加\n"
            "/twatch del @user — 删除\n"
            "/twatch check — 立即检查\n\n"
            "收到提醒后:\n"
            "/research latest [研究重点] — 建立研究话题\n"
            "/deep latest [研究重点] — 让 Agent 主动深挖\n"
            "/ask latest 问题 — 直接追问",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Alert Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_alert(ctx: BotContext, adapter):
    """Alert config and manual check: /alert [check|config]"""
    sub = ctx.args[0] if ctx.args else "status"
    from config.settings import get_settings
    from server.alerts.engine import get_active_tickers_for_alert

    settings = get_settings()

    if sub == "check":
        await adapter.send_message(ctx.chat_id, "🔍 正在检查告警...")
        from server.alerts.engine import run_alert_cycle

        await run_alert_cycle(adapter)
        await adapter.send_message(ctx.chat_id, "✅ 告警检查完成")

    elif sub == "config" and len(ctx.args) >= 2:
        key = ctx.args[1].lower()
        try:
            val = float(ctx.args[2]) if len(ctx.args) > 2 else 0
        except ValueError:
            await adapter.send_message(ctx.chat_id, "阈值必须是数字")
            return

        if key == "price" and val > 0:
            settings.alert_price_pct = val
            await adapter.send_message(ctx.chat_id, f"✅ 价格告警阈值设为 {val}%")
        elif key == "volume" and val > 0:
            settings.alert_volume_ratio = val
            await adapter.send_message(ctx.chat_id, f"✅ 成交量告警阈值设为 {val}x")
        else:
            await adapter.send_message(
                ctx.chat_id, "用法: /alert config price 3.0 或 /alert config volume 2.5"
            )

    else:
        tickers = await get_active_tickers_for_alert()
        text = (
            "*⚙️ 告警配置*\n\n"
            f"状态: {'✅ 启用' if settings.alert_enabled else '❌ 禁用'}\n"
            f"检查间隔: {settings.alert_interval_minutes} 分钟\n"
            f"价格阈值: {settings.alert_price_pct}%\n"
            f"成交量阈值: {settings.alert_volume_ratio}x 均量\n\n"
            f"*监控标的 ({len(tickers)}):*\n"
            + (" ".join(f"`{t}`" for t in tickers) if tickers else "暂无")
        )
        await adapter.send_message(ctx.chat_id, text)


# ═══════════════════════════════════════════════════════════════════════════════
# Briefing Command
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_briefing(ctx: BotContext, adapter):
    """Manually trigger daily briefing."""
    await adapter.send_message(ctx.chat_id, "📋 正在生成每日简报...")
    from server.briefing import generate_daily_briefing

    text = await generate_daily_briefing()
    await adapter.send_message(ctx.chat_id, text)


async def cmd_digest(ctx: BotContext, adapter):
    """Twitter daily digest: /digest [days_ago]"""
    days_ago = 1
    if ctx.args and ctx.args[0].isdigit():
        days_ago = max(1, min(30, int(ctx.args[0])))

    label = "昨日" if days_ago == 1 else f"{days_ago} 天前"
    await adapter.send_message(ctx.chat_id, f"📰 正在生成 {label} Twitter 关注日报...")
    from server.social.digest import generate_twitter_digest

    messages = await generate_twitter_digest(days_ago=days_ago)
    if not messages:
        await adapter.send_message(ctx.chat_id, f"❌ {label} 没有推文记录。")
        return
    for msg in messages:
        await adapter.send_message(ctx.chat_id, msg)


async def cmd_summary(ctx: BotContext, adapter):
    """Per-account Twitter summary: /summary @username [YYYY-MM-DD]"""
    if not ctx.args:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n/summary @elonmusk — 查看昨日日报\n/summary @elonmusk 2025-06-01 — 指定日期",
        )
        return

    username = ctx.args[0].lstrip("@")
    target_date = None
    if len(ctx.args) > 1:
        try:
            from datetime import date

            target_date = date.fromisoformat(ctx.args[1])
        except ValueError:
            await adapter.send_message(ctx.chat_id, "❌ 日期格式错误，请使用 YYYY-MM-DD。")
            return

    date_label = str(target_date) if target_date else "昨日"
    await adapter.send_message(ctx.chat_id, f"📰 正在生成 @{username} {date_label} 日报...")
    from server.social.digest import generate_user_digest

    text = await generate_user_digest(username, target_date)
    if text is None:
        await adapter.send_message(ctx.chat_id, f"❌ @{username} 在 {date_label} 没有推文记录。")
        return
    await adapter.send_message(ctx.chat_id, text)


def register_all_commands(router, adapter):
    """Register all shared command handlers."""
    router.register_many(
        {
            "help": lambda ctx: cmd_help(ctx, adapter),
            "tools": lambda ctx: cmd_tools(ctx, adapter),
            "status": lambda ctx: cmd_status(ctx, adapter),
            "pick": lambda ctx: cmd_pick(ctx, adapter),
            "quote": lambda ctx: cmd_quote(ctx, adapter),
            "technical": lambda ctx: cmd_technical(ctx, adapter),
            "news": lambda ctx: cmd_news(ctx, adapter),
            "track": lambda ctx: cmd_track(ctx, adapter),
            "score": lambda ctx: cmd_score(ctx, adapter),
            "portfolio": lambda ctx: cmd_portfolio(ctx, adapter),
            "history": lambda ctx: cmd_history(ctx, adapter),
            "deep": lambda ctx: cmd_deep(ctx, adapter),
            "ask": lambda ctx: cmd_ask(ctx, adapter),
            "research": lambda ctx: cmd_research(ctx, adapter),
            "thread": lambda ctx: cmd_research(ctx, adapter),
            "topic": lambda ctx: cmd_topic(ctx, adapter),
            "log": lambda ctx: cmd_log(ctx, adapter),
            "journal": lambda ctx: cmd_journal(ctx, adapter),
            "pnl": lambda ctx: cmd_pnl(ctx, adapter),
            "twatch": lambda ctx: cmd_twatch(ctx, adapter),
            "alert": lambda ctx: cmd_alert(ctx, adapter),
            "briefing": lambda ctx: cmd_briefing(ctx, adapter),
            "digest": lambda ctx: cmd_digest(ctx, adapter),
            "summary": lambda ctx: cmd_summary(ctx, adapter),
        }
    )
    router.register_message_handler(lambda ctx: handle_plain_message(ctx, adapter))
