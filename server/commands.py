"""
Bot command handlers shared across Telegram and Feishu.
"""

import asyncio

from loguru import logger

from server.bot.base import BotContext
from server.research.service import ResearchError


async def cmd_help(ctx: BotContext, adapter):
    text = """*Reveal — 美股交易助手*

*选股相关*
/pick — 立即触发选股
/track — 查看追踪中的标的
/score TICKER — 查看标的评分明细

*Twitter 监控*
/twatch list — 查看监控列表
/twatch add @user — 添加监控
/twatch del @user — 删除监控
/deep latest — 深挖最近一条更新
/ask latest 问题 — 基于更新追问
/research latest — 建立研究话题
/topic summary — 汇总当前研究话题

*交易日记*
/log buy TICKER PRICE QTY — 记录买入
/log short TICKER PRICE QTY — 记录做空
/log sell TICKER PRICE — 记录卖出
/journal today — 今日日记
/journal week — 本周汇总
/pnl — 盈亏汇总

*系统*
/alert — 查看/配置告警阈值
/alert check — 立即检查告警
/status — 系统状态
/help — 帮助"""
    await adapter.send_message(ctx.chat_id, text)


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

        # Priority 2: route to active research topic
        topic = await get_active_topic(ctx.chat_id)
        if topic is not None:
            _spawn_background_task(
                _run_topic_message_job(ctx.chat_id, text, adapter, ctx.reply_to_message_id),
                "topic message",
            )
            return

        # Priority 3: classify intent with LLM
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
        from server.research.service import get_active_topic, start_topic

        topic = await get_active_topic(ctx.chat_id)
        if not (
            topic
            and topic.source_type == "twitter"
            and topic.source_id == binding.source_id
            and topic.status == "active"
        ):
            await start_topic(ctx.chat_id, str(binding.source_id), "")
        _spawn_background_task(
            _run_topic_message_job(ctx.chat_id, text, adapter, ctx.reply_to_message_id),
            "bound topic message",
        )
        return True
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
        return True


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


async def _run_topic_message_job(chat_id: str, text: str, adapter, reply_to: str = "") -> None:
    from server.research.progress import ResearchProgressReporter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    try:
        from server.research.service import handle_topic_message

        await reporter.start("研究 Agent 分析中...")
        answer = await handle_topic_message(chat_id, text, on_progress=reporter.on_progress)
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


def register_all_commands(router, adapter):
    """Register all shared command handlers."""
    router.register_many(
        {
            "help": lambda ctx: cmd_help(ctx, adapter),
            "status": lambda ctx: cmd_status(ctx, adapter),
            "pick": lambda ctx: cmd_pick(ctx, adapter),
            "track": lambda ctx: cmd_track(ctx, adapter),
            "score": lambda ctx: cmd_score(ctx, adapter),
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
        }
    )
    router.register_message_handler(lambda ctx: handle_plain_message(ctx, adapter))
