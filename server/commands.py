"""
Bot command handlers shared across Telegram and Feishu.
"""

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
/topic start latest — 开启研究线程

*交易日记*
/log buy TICKER PRICE QTY — 记录买入
/log short TICKER PRICE QTY — 记录做空
/log sell TICKER PRICE — 记录卖出
/journal today — 今日日记
/journal week — 本周汇总
/pnl — 盈亏汇总

*系统*
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
    await adapter.send_message(
        ctx.chat_id, "🔎 正在深挖这条更新，会结合原文、引用、外链和搜索结果..."
    )
    try:
        from server.research.service import run_deep_research

        run = await run_deep_research(ctx.chat_id, post_ref, focus)
        text = (
            f"*研究线程 #{run.session_id} · 推文 #{run.post.id}*\n\n"
            f"{run.answer}\n\n"
            "继续追问可以直接发普通消息，或使用:\n"
            f"/ask {run.post.id} 你的问题\n"
            "/topic summary\n"
            "/topic stop"
        )
        await adapter.send_message(ctx.chat_id, text)
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Deep research failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 深挖失败，请稍后重试。")


async def cmd_ask(ctx: BotContext, adapter):
    """Ask about a social post: /ask latest|POST_ID question"""
    if len(ctx.args) < 2:
        await adapter.send_message(ctx.chat_id, "用法: /ask latest 问题 或 /ask POST_ID 问题")
        return

    post_ref = ctx.args[0]
    question = " ".join(ctx.args[1:]).strip()
    await adapter.send_message(ctx.chat_id, "🤔 正在基于这条更新和已收集材料回答...")
    try:
        from server.research.service import ask_about_post

        answer = await ask_about_post(ctx.chat_id, post_ref, question)
        await adapter.send_message(ctx.chat_id, answer)
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Research ask failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 回答失败，请稍后重试。")


async def cmd_topic(ctx: BotContext, adapter):
    """Manage a research topic: /topic start|summary|stop"""
    sub = ctx.args[0].lower() if ctx.args else "status"
    try:
        from server.research.service import (
            get_active_topic,
            start_topic,
            stop_topic,
            summarize_topic,
        )

        if sub == "start" and len(ctx.args) >= 2:
            post_ref = ctx.args[1]
            focus = " ".join(ctx.args[2:]).strip()
            topic = await start_topic(ctx.chat_id, post_ref, focus)
            await adapter.send_message(
                ctx.chat_id,
                f"✅ 已开启研究线程 #{topic.id}。\n"
                "现在可以直接发送普通消息继续追问；需要联网深挖时使用 /deep "
                f"{topic.source_id}。",
            )
        elif sub == "summary":
            summary = await summarize_topic(ctx.chat_id)
            await adapter.send_message(ctx.chat_id, summary)
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
                    ctx.chat_id, "当前没有活跃研究线程。用 /topic start latest 开启。"
                )
        else:
            await adapter.send_message(
                ctx.chat_id,
                "用法:\n/topic start latest [研究重点]\n/topic summary\n/topic stop",
            )
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Topic command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 研究线程操作失败，请稍后重试。")


async def handle_plain_message(ctx: BotContext, adapter):
    """Route normal text into the active research topic, if one exists."""
    text = ctx.text.strip()
    if not text:
        return
    try:
        from server.research.service import handle_topic_message

        answer = await handle_topic_message(ctx.chat_id, text)
        if answer:
            await adapter.send_message(ctx.chat_id, answer)
    except Exception as e:
        logger.exception(f"Topic message handling failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 当前研究线程处理失败。")


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
            f"✅ 已添加 @{username}\n首次检查只会建立当前最新推文基线，不会回推历史推文。",
        )

    elif sub == "del" and len(ctx.args) > 1:
        username = ctx.args[1].lstrip("@")
        from server.social.monitor import set_twitter_account_active

        await set_twitter_account_active(username, False)
        await adapter.send_message(ctx.chat_id, f"✅ 已移除 @{username}")

    elif sub == "check":
        await adapter.send_message(ctx.chat_id, "🔍 正在检查新推文...")
        from config.settings import get_settings
        from server.social.monitor import list_active_twitter_accounts, run_twitter_monitor
        from server.social.processor import TweetProcessor

        processor = TweetProcessor()
        accounts = await list_active_twitter_accounts(get_settings().twitter_accounts)
        await run_twitter_monitor(accounts, adapter, processor)
        await adapter.send_message(ctx.chat_id, "✅ 检查完成")

    else:
        await adapter.send_message(
            ctx.chat_id,
            "用法:\n"
            "/twatch list — 查看列表\n"
            "/twatch add @user — 添加\n"
            "/twatch del @user — 删除\n"
            "/twatch check — 立即检查",
        )


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
            "topic": lambda ctx: cmd_topic(ctx, adapter),
            "log": lambda ctx: cmd_log(ctx, adapter),
            "journal": lambda ctx: cmd_journal(ctx, adapter),
            "pnl": lambda ctx: cmd_pnl(ctx, adapter),
            "twatch": lambda ctx: cmd_twatch(ctx, adapter),
        }
    )
    router.register_message_handler(lambda ctx: handle_plain_message(ctx, adapter))
