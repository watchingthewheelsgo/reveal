"""
Bot command handlers shared across Telegram and Feishu.
"""

import asyncio

from loguru import logger

from server.bot.base import BotContext
from server.research.service import ResearchError


async def cmd_help(ctx: BotContext, adapter):
    from server.capabilities.registry import format_command_help

    await adapter.send_message(ctx.chat_id, format_command_help())


async def cmd_status(ctx: BotContext, adapter):
    from server.capabilities.system import format_system_status, get_system_status_payload

    await adapter.send_message(ctx.chat_id, format_system_status(get_system_status_payload()))


# ═══════════════════════════════════════════════════════════════════════════════
# Stock Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_stock_watch(ctx: BotContext, adapter):
    """Manage manual stock watchlist: /stock [list|add|del] TICKER [threshold_pct]."""
    from server.stock.watchlist import (
        add_stock_watch,
        format_stock_watch_add_result,
        format_stock_watch_list,
        get_stock_watch_list_payload,
        platform_for_adapter,
        remove_stock_watch,
    )

    sub = ctx.args[0].lower() if ctx.args else "list"
    if sub in {"list", "ls"}:
        payload = await get_stock_watch_list_payload(ctx.chat_id)
        await adapter.send_message(ctx.chat_id, format_stock_watch_list(payload))
        return

    if sub in {"add", "watch"} and len(ctx.args) >= 2:
        threshold_pct = 5.0
        if len(ctx.args) >= 3:
            try:
                threshold_pct = float(ctx.args[2])
            except ValueError:
                await adapter.send_message(ctx.chat_id, "阈值必须是数字，例如 /stock add NVDA 5")
                return
        try:
            payload = await add_stock_watch(
                ctx.args[1],
                chat_id=ctx.chat_id,
                platform=platform_for_adapter(adapter),
                threshold_pct=threshold_pct,
            )
        except ValueError as exc:
            await adapter.send_message(ctx.chat_id, f"❌ {exc}")
            return
        await adapter.send_message(ctx.chat_id, format_stock_watch_add_result(payload))
        return

    if sub in {"del", "delete", "remove", "rm"} and len(ctx.args) >= 2:
        try:
            payload = await remove_stock_watch(ctx.args[1], chat_id=ctx.chat_id)
        except ValueError as exc:
            await adapter.send_message(ctx.chat_id, f"❌ {exc}")
            return
        prefix = "✅" if payload["removed"] else "ℹ️"
        await adapter.send_message(ctx.chat_id, f"{prefix} {payload['message']}")
        return

    await adapter.send_message(
        ctx.chat_id,
        "用法:\n/stock list\n/stock add NVDA [阈值%]\n/stock del NVDA",
    )


async def cmd_portfolio(ctx: BotContext, adapter):
    """Show current open portfolio positions."""
    try:
        from server.capabilities.market import format_portfolio, get_portfolio_payload

        await adapter.send_message(ctx.chat_id, format_portfolio(await get_portfolio_payload()))
    except Exception as e:
        logger.exception(f"Portfolio command failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 持仓查询失败，请稍后重试。")


# ═══════════════════════════════════════════════════════════════════════════════
# Research Commands
# ═══════════════════════════════════════════════════════════════════════════════


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
        await _bind_research_session_message(ctx.chat_id, ctx.message_id, topic.id)
        message_id = await adapter.send_message_returning_id(
            ctx.chat_id,
            f"✅ 已建立研究话题 #{topic.id}，绑定消息 #{topic.source_id}。\n"
            "在这条消息下面回复即可继续追问；需要汇总时使用 /topic summary。",
        )
        await _bind_research_session_message(ctx.chat_id, message_id, topic.id)
    except ResearchError as e:
        await adapter.send_message(ctx.chat_id, f"❌ {e}")
    except Exception as e:
        logger.exception(f"Start research topic failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 建立研究话题失败，请稍后重试。")


async def handle_plain_message(ctx: BotContext, adapter):
    """Route plain text: bound IM thread/quote → Agent session."""
    text = ctx.text.strip()
    if not text:
        return
    try:
        if ctx.reply_to_message_id:
            routed = await _route_bound_reply(ctx, adapter, text)
            if routed:
                return

        reply_anchor = ctx.reply_to_message_id or ctx.message_id
        _spawn_background_task(
            _run_agent_message_job(
                ctx.chat_id,
                text,
                adapter,
                reply_anchor,
                ctx.message_id,
            ),
            "agent message",
        )

    except Exception as e:
        logger.exception(f"Message handling failed: {e}")
        await adapter.send_message(ctx.chat_id, "❌ 消息处理失败。")


async def _route_bound_reply(ctx: BotContext, adapter, text: str) -> bool:
    from server.bot.bindings import resolve_message_binding

    binding = await resolve_message_binding(ctx.chat_id, ctx.reply_to_message_id)
    if binding is None:
        return False
    if binding.source_type == "research_session":
        _spawn_background_task(
            _run_topic_message_job(
                ctx.chat_id,
                text,
                adapter,
                ctx.reply_to_message_id,
                binding.source_id,
            ),
            "bound agent session message",
        )
        return True
    if binding.source_type not in {"twitter", "reddit", "social"}:
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


def _spawn_background_task(coro, label: str) -> None:
    task = asyncio.create_task(coro)

    def _log_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as e:
            logger.exception(f"{label} background task failed: {e}")

    task.add_done_callback(_log_result)


def _failure_message(prefix: str, exc: Exception) -> str:
    detail = str(exc).strip() or exc.__class__.__name__
    return f"{prefix}: {detail}"


async def _bind_research_session_message(
    chat_id: str,
    message_id: str | None,
    session_id: int | None,
) -> None:
    if session_id is None:
        return
    try:
        from server.bot.bindings import bind_message_to_source

        await bind_message_to_source(chat_id, message_id, "research_session", session_id)
    except Exception:
        logger.exception(
            "Research session message binding failed: chat_id={} message_id={} session_id={}",
            chat_id,
            message_id or "-",
            session_id,
        )


async def _bind_unbound_research_session_message(
    chat_id: str,
    message_id: str | None,
    session_id: int | None,
) -> None:
    if not chat_id or not message_id or session_id is None:
        return
    try:
        from server.bot.bindings import bind_message_to_source, resolve_message_binding

        existing = await resolve_message_binding(chat_id, message_id)
        if existing is None:
            await bind_message_to_source(chat_id, message_id, "research_session", session_id)
    except Exception:
        logger.exception(
            "Unbound research session message binding failed: "
            "chat_id={} message_id={} session_id={}",
            chat_id,
            message_id or "-",
            session_id,
        )


async def _run_agent_message_job(
    chat_id: str,
    text: str,
    adapter,
    reply_to: str = "",
    source_message_id: str = "",
) -> None:
    from server.research.progress import ResearchProgressReporter
    from server.research.service import run_agent_session_message, start_agent_session
    from server.stock.watchlist import platform_for_adapter

    reporter = ResearchProgressReporter(adapter, chat_id, reply_to)
    platform = platform_for_adapter(adapter)
    try:
        session = await start_agent_session(chat_id, text)
        await _bind_research_session_message(chat_id, source_message_id, session.id)
        await _bind_unbound_research_session_message(chat_id, reply_to, session.id)
        await reporter.start("Agent 处理中...")
        await _bind_research_session_message(chat_id, reporter.status_message_id, session.id)
        answer = await run_agent_session_message(
            session,
            text,
            platform=platform,
            on_progress=reporter.on_progress,
        )
        result_message_id = await reporter.finish(answer)
        await _bind_research_session_message(chat_id, result_message_id, session.id)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Agent message failed: {e}")
        await reporter.error(_failure_message("Agent 处理失败", e))


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
        await reporter.error(_failure_message("研究线程总结失败", e))


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
            result_message_id = await reporter.finish(answer)
            if session_id:
                await _bind_research_session_message(
                    chat_id, reporter.status_message_id, session_id
                )
                await _bind_research_session_message(chat_id, result_message_id, session_id)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Topic message handling failed: {e}")
        await reporter.error(_failure_message("当前研究线程处理失败", e))


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
            "继续追问请在这条结果下面回复，或使用:\n"
            "/topic summary — 汇总\n"
            "/topic stop — 结束"
        )
        result_message_id = await reporter.finish(text)
        await _bind_research_session_message(chat_id, reporter.status_message_id, run.session_id)
        await _bind_research_session_message(chat_id, result_message_id, run.session_id)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Ticker research failed: {e}")
        await reporter.error(_failure_message("研究失败", e))


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
            "继续追问请在这条结果下面回复，或使用:\n"
            "/topic summary — 汇总\n"
            "/topic stop — 结束"
        )
        result_message_id = await reporter.finish(text)
        await _bind_research_session_message(chat_id, reporter.status_message_id, run.session_id)
        await _bind_research_session_message(chat_id, result_message_id, run.session_id)
    except ResearchError as e:
        await reporter.error(str(e))
    except Exception as e:
        logger.exception(f"Freeform research failed: {e}")
        await reporter.error(_failure_message("研究失败", e))


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


async def _run_reddit_check_job(
    subreddits: list[str],
    reply_chat_id: str,
    adapter,
    no_updates_text: str | None = None,
) -> None:
    try:
        from server.social.processor import TweetProcessor
        from server.social.reddit import run_reddit_monitor

        processor = TweetProcessor()
        total = await run_reddit_monitor(subreddits, adapter, processor)
        if total == 0 and no_updates_text:
            await adapter.send_message(reply_chat_id, no_updates_text)
    except Exception as e:
        logger.exception(f"Reddit monitor check failed: {e}")
        await adapter.send_message(reply_chat_id, f"❌ Reddit 检查失败：{type(e).__name__}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Twitter Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_twatch(ctx: BotContext, adapter):
    """Twitter watch commands: /x [list|add @user|del @user]"""
    sub = ctx.args[0] if ctx.args else "list"

    if sub == "list":
        from server.capabilities.twitter import (
            format_twitter_watch_list,
            get_twitter_watch_list_payload,
        )

        await adapter.send_message(
            ctx.chat_id,
            format_twitter_watch_list(await get_twitter_watch_list_payload()),
        )

    elif sub == "add" and len(ctx.args) > 1:
        username = ctx.args[1].lstrip("@")
        from server.capabilities.twitter import set_twitter_watch_account_payload

        await set_twitter_watch_account_payload(username, True)
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
        from server.capabilities.twitter import set_twitter_watch_account_payload

        await set_twitter_watch_account_payload(username, False)
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
            "/x list — 查看列表\n"
            "/x add @user — 添加\n"
            "/x del @user — 删除\n"
            "/x check — 立即检查\n\n"
            "收到提醒后:\n"
            "/research latest [研究重点] — 建立研究话题\n"
            "也可以直接回复推送卡片继续追问。",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Reddit Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_reddit_watch(ctx: BotContext, adapter):
    """Reddit watch commands: /reddit [list|add subreddit|del subreddit|check]"""
    sub = ctx.args[0].lower() if ctx.args else "list"

    if sub in {"list", "ls"}:
        from server.capabilities.reddit import (
            format_reddit_watch_list,
            get_reddit_watch_list_payload,
        )

        await adapter.send_message(
            ctx.chat_id, format_reddit_watch_list(await get_reddit_watch_list_payload())
        )
        return

    if sub == "add" and len(ctx.args) > 1:
        subreddit = ctx.args[1]
        from server.capabilities.reddit import set_reddit_watch_subreddit_payload

        try:
            payload = await set_reddit_watch_subreddit_payload(subreddit, True)
        except Exception as exc:
            await adapter.send_message(
                ctx.chat_id, f"❌ 添加 subreddit 失败：{type(exc).__name__}: {exc}"
            )
            return
        subreddit_name = payload["subreddit"]
        await adapter.send_message(
            ctx.chat_id,
            f"✅ 已添加 r/{subreddit_name}\n"
            "正在获取最近帖子；后续每小时检查，只推送 market/stock 强相关内容。",
        )
        _spawn_background_task(
            _run_reddit_check_job(
                [subreddit_name],
                ctx.chat_id,
                adapter,
                "✅ 首次检查完成，没有发现需要推送的强市场相关帖子。",
            ),
            "reddit watch add",
        )
        return

    if sub in {"del", "delete", "remove", "rm"} and len(ctx.args) > 1:
        subreddit = ctx.args[1]
        from server.capabilities.reddit import set_reddit_watch_subreddit_payload

        try:
            payload = await set_reddit_watch_subreddit_payload(subreddit, False)
        except Exception as exc:
            await adapter.send_message(
                ctx.chat_id, f"❌ 移除 subreddit 失败：{type(exc).__name__}: {exc}"
            )
            return
        await adapter.send_message(ctx.chat_id, f"✅ 已移除 r/{payload['subreddit']}")
        return

    if sub == "check":
        await adapter.send_message(ctx.chat_id, "🔍 正在检查 Reddit subreddit...")
        from config.settings import get_settings
        from server.social.reddit import list_active_reddit_subreddits

        subreddits = await list_active_reddit_subreddits(get_settings().reddit_subreddits)
        _spawn_background_task(
            _run_reddit_check_job(
                subreddits,
                ctx.chat_id,
                adapter,
                "✅ 检查完成，没有发现需要推送的强市场相关 Reddit 帖子。",
            ),
            "reddit manual check",
        )
        return

    await adapter.send_message(
        ctx.chat_id,
        "用法:\n"
        "/reddit list — 查看 subreddit 列表\n"
        "/reddit add stocks — 添加 subreddit\n"
        "/reddit del stocks — 删除 subreddit\n"
        "/reddit check — 立即检查\n\n"
        "后台每小时检查，只推送 Agent 判定为 market/stock 强相关的帖子。",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Scheduled Task Commands
# ═══════════════════════════════════════════════════════════════════════════════


async def cmd_task(ctx: BotContext, adapter):
    """Manage user-created one-shot scheduled tasks."""
    from server.stock.watchlist import platform_for_adapter
    from server.tasks.scheduled import (
        cancel_scheduled_task,
        create_scheduled_task,
        format_scheduled_task_created,
        format_scheduled_task_list,
        list_scheduled_tasks,
        split_schedule_command_body,
    )

    action = ctx.args[0].lower() if ctx.args else "list"
    if action in {"list", "ls"}:
        include_done = len(ctx.args) > 1 and ctx.args[1].lower() in {"all", "done", "history"}
        await adapter.send_message(
            ctx.chat_id,
            format_scheduled_task_list(
                await list_scheduled_tasks(chat_id=ctx.chat_id, include_done=include_done)
            ),
        )
        return

    if action in {"cancel", "del", "delete", "remove", "rm"}:
        if len(ctx.args) < 2 or not ctx.args[1].isdigit():
            await adapter.send_message(ctx.chat_id, "用法: /task cancel TASK_ID")
            return
        result = await cancel_scheduled_task(int(ctx.args[1]), chat_id=ctx.chat_id)
        prefix = "✅ " if result["cancelled"] else "ℹ️ "
        await adapter.send_message(ctx.chat_id, prefix + result["message"])
        return

    if action in {"add", "at", "in", "create"}:
        body = " ".join(ctx.args[1:]).strip()
        if action in {"at", "in"}:
            body = f"{action} {body}".strip()
        if action == "in" and body.lower().startswith("in "):
            body = body[3:].strip()
        elif action == "at" and body.lower().startswith("at "):
            body = body[3:].strip()
        split = split_schedule_command_body(body)
        if split is None:
            await adapter.send_message(
                ctx.chat_id,
                "用法:\n"
                "/task list\n"
                "/task add 2小时后 | 推送 CPI 数据新闻\n"
                "/task add 今晚7点 | 推送 CPI 数据新闻\n"
                "/task cancel TASK_ID",
            )
            return
        run_at_text, prompt = split
        try:
            payload = await create_scheduled_task(
                chat_id=ctx.chat_id,
                platform=platform_for_adapter(adapter),
                run_at_text=run_at_text,
                prompt=prompt,
                created_by=ctx.user_id,
                source_message_id=ctx.message_id,
            )
        except ValueError as exc:
            await adapter.send_message(ctx.chat_id, f"❌ {exc}")
            return
        await adapter.send_message(ctx.chat_id, format_scheduled_task_created(payload))
        return

    await adapter.send_message(
        ctx.chat_id,
        "用法:\n"
        "/task list\n"
        "/task add 2小时后 | 推送 CPI 数据新闻\n"
        "/task add 今晚7点 | 推送 CPI 数据新闻\n"
        "/task cancel TASK_ID",
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
        from server.alerts.regulatory import run_regulatory_alert_cycle

        await run_alert_cycle(adapter)
        if settings.regulatory_alert_enabled:
            await run_regulatory_alert_cycle(adapter)
        if settings.is_longbridge_configured() and settings.longbridge_movers_enabled:
            from server.alerts.market_movers import run_market_mover_alert_cycle

            await run_market_mover_alert_cycle(adapter)
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
        longbridge_enabled = (
            settings.is_longbridge_configured() and settings.longbridge_movers_enabled
        )
        text = (
            "*⚙️ 告警配置*\n\n"
            f"状态: {'✅ 启用' if settings.alert_enabled else '❌ 禁用'}\n"
            f"检查间隔: {settings.alert_interval_minutes} 分钟\n"
            f"价格阈值: {settings.alert_price_pct}%\n"
            f"成交量阈值: {settings.alert_volume_ratio}x 均量\n\n"
            "*监管事件*\n"
            f"状态: {'✅ 启用' if settings.regulatory_alert_enabled else '❌ 禁用'}\n"
            f"检查间隔: {settings.regulatory_alert_interval_minutes} 分钟\n"
            f"SEC: {'✅ 启用' if settings.sec_user_agent else '❌ 缺少 SEC_USER_AGENT'}\n"
            f"FDA: {'✅ 启用' if settings.fda_alert_enabled else '❌ 禁用'} "
            f"({'/'.join(settings.fda_alert_categories)})\n\n"
            "*Longbridge 异动*\n"
            f"状态: {'✅ 启用' if longbridge_enabled else '❌ 禁用/未配置'}\n"
            f"检查间隔: {settings.longbridge_movers_interval_seconds} 秒\n"
            f"市场: {settings.longbridge_movers_market} | "
            f"每次 {settings.longbridge_movers_count} 条\n\n"
            f"*监控标的 ({len(tickers)}):*\n"
            + (" ".join(f"`{t}`" for t in tickers) if tickers else "暂无")
        )
        await adapter.send_message(ctx.chat_id, text)


async def cmd_movers(ctx: BotContext, adapter):
    """Longbridge market mover alerts: /movers [check|recent|status]."""
    action = ctx.args[0].lower() if ctx.args else "recent"
    try:
        from server.alerts.market_movers import (
            check_market_movers,
            format_market_mover_alert,
            format_market_mover_list,
            get_market_mover_status_payload,
            get_recent_market_movers,
            persist_new_market_mover_events,
        )

        if action == "check":
            await adapter.send_message(ctx.chat_id, "🔍 正在检查 Longbridge 市场异动...")
            events = await check_market_movers()
            new_events = await persist_new_market_mover_events(events, mark_pushed=True)
            if not new_events:
                await adapter.send_message(ctx.chat_id, "Longbridge 暂无新的异动。")
                return
            for event in new_events[:10]:
                await adapter.send_message(ctx.chat_id, format_market_mover_alert(event))
            if len(new_events) > 10:
                await adapter.send_message(
                    ctx.chat_id, f"还有 {len(new_events) - 10} 条新异动未展开。"
                )
            return

        if action in {"recent", "list"}:
            limit = 10
            if len(ctx.args) > 1 and ctx.args[1].isdigit():
                limit = max(1, min(50, int(ctx.args[1])))
            await adapter.send_message(
                ctx.chat_id, format_market_mover_list(await get_recent_market_movers(limit))
            )
            return

        if action == "status":
            payload = await get_market_mover_status_payload()
            await adapter.send_message(
                ctx.chat_id,
                "\n".join(
                    [
                        "*Longbridge 异动监控*",
                        f"状态: {'✅ 启用' if payload['enabled'] else '❌ 禁用'}",
                        f"配置: {'✅ OK' if payload['configured'] else '❌ 缺少 token path'}",
                        f"市场: {payload['market']}",
                        f"间隔: {payload['interval_seconds']} 秒",
                        f"每次拉取: {payload['count']} 条",
                        f"每轮最多推送: {payload['push_limit']} 条",
                    ]
                ),
            )
            return

        await adapter.send_message(
            ctx.chat_id, "用法:\n/movers check\n/movers recent [数量]\n/movers status"
        )
    except Exception:
        logger.exception("Longbridge movers command failed")
        await adapter.send_message(ctx.chat_id, "❌ Longbridge 异动检查失败，请稍后重试。")


def register_all_commands(router, adapter):
    """Register all shared command handlers."""
    router.register_many(
        {
            "help": lambda ctx: cmd_help(ctx, adapter),
            "status": lambda ctx: cmd_status(ctx, adapter),
            "stock": lambda ctx: cmd_stock_watch(ctx, adapter),
            "portfolio": lambda ctx: cmd_portfolio(ctx, adapter),
            "research": lambda ctx: cmd_research(ctx, adapter),
            "topic": lambda ctx: cmd_topic(ctx, adapter),
            "x": lambda ctx: cmd_twatch(ctx, adapter),
            "reddit": lambda ctx: cmd_reddit_watch(ctx, adapter),
            "task": lambda ctx: cmd_task(ctx, adapter),
            "alert": lambda ctx: cmd_alert(ctx, adapter),
            "movers": lambda ctx: cmd_movers(ctx, adapter),
        }
    )
    router.register_message_handler(lambda ctx: handle_plain_message(ctx, adapter))
