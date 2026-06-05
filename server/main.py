"""
Reveal — 美股交易助手主入口
Telegram + 飞书双通道，集选股推荐、Twitter 监控、交易日记于一体。
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.staticfiles import StaticFiles
from loguru import logger

from config.settings import global_settings
from server.web import router as web_router


def configure_logging() -> None:
    logger.remove()
    logger.add(sys.stderr, level=global_settings.log_level.upper())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: init → run → shutdown."""
    settings = global_settings
    configure_logging()
    logger.info("Starting Reveal...")

    # Init database
    from server.db.engine import init_db

    await init_db()
    logger.info("Database initialized")

    # Init bots
    telegram_bot = None
    feishu_bot = None

    if settings.telegram_bot_token:
        from server.bot.base import CommandRouter
        from server.bot.telegram import TelegramBot
        from server.commands import register_all_commands

        if not settings.get_telegram_admin_chat_ids():
            logger.warning("Telegram bot is configured without TELEGRAM_ADMIN_CHAT_ID")

        for attempt in range(3):
            candidate = TelegramBot()
            try:
                await candidate.initialize()
                router = CommandRouter(candidate)
                candidate.set_router(router)
                register_all_commands(router, candidate)
                await candidate.start_polling()
                telegram_bot = candidate
                logger.info("Telegram bot polling started")
                break
            except Exception as e:
                logger.warning(f"Telegram init attempt {attempt + 1}/3 failed: {e}")
                await candidate.stop()
                if attempt < 2:
                    await asyncio.sleep(5)
        if telegram_bot is None:
            logger.error("Telegram bot disabled after repeated startup failures")

    if settings.is_feishu_configured():
        from server.bot.base import CommandRouter
        from server.bot.feishu import FeishuBot
        from server.commands import register_all_commands

        feishu_bot = FeishuBot()
        if not settings.get_feishu_admin_chat_ids():
            logger.warning("Feishu bot is configured without FEISHU_ADMIN_CHAT_ID")
        feishu_router = CommandRouter(feishu_bot)
        feishu_bot.set_router(feishu_router)
        register_all_commands(feishu_router, feishu_bot)
        if settings.feishu_enable_ws:
            feishu_bot.set_event_loop(asyncio.get_event_loop())
            feishu_bot.start_in_thread()
            logger.info("Feishu bot WebSocket started")
        else:
            logger.info("Feishu bot WebSocket disabled; HTTP callback API is available")

    # Init scheduler with real jobs
    from server.scheduler import Scheduler

    scheduler = Scheduler()

    # Daily stock pick
    async def daily_pick_job():
        from server.stock.scanner import format_pick_message, run_daily_pick

        logger.info("Running daily pick job...")
        pick = await run_daily_pick()
        if pick is None:
            logger.warning("Daily pick returned no results")
            return
        text = format_pick_message(pick)
        if telegram_bot:
            await telegram_bot.push_to_admin(text)
        if feishu_bot:
            await feishu_bot.push_to_admin(text)

    pick_hour, pick_minute = map(int, settings.daily_pick_time.split(":"))
    scheduler.register_cron("daily_pick", daily_pick_job, pick_hour, pick_minute)

    # Daily tracking update (after market close ~4:30 PM ET)
    async def tracking_update_job():
        from server.stock.tracker import apply_feedback, update_tracking

        await update_tracking()
        await apply_feedback()

    scheduler.register_cron("tracking_update", tracking_update_job, 16, 30)

    # Daily briefing (before market open ~8:30 AM ET)
    async def daily_briefing_job():
        logger.info("Running daily briefing...")
        from server.briefing import generate_daily_briefing

        text = await generate_daily_briefing()
        if telegram_bot:
            await telegram_bot.push_to_admin(text)
        if feishu_bot:
            await feishu_bot.push_to_admin(text)

    brief_hour, brief_minute = map(int, settings.daily_briefing_time.split(":"))
    scheduler.register_cron("daily_briefing", daily_briefing_job, brief_hour, brief_minute)

    # Twitter daily digest
    if settings.twitter_digest_enabled:

        async def twitter_digest_job():
            from server.social.digest import generate_twitter_digest

            logger.info("Running Twitter daily digest...")
            messages = await generate_twitter_digest()
            if not messages:
                logger.info("Twitter digest: no posts yesterday, skipping push")
                return
            for msg in messages:
                if telegram_bot:
                    await telegram_bot.push_to_admin(msg)
                if feishu_bot:
                    await feishu_bot.push_to_admin(msg)

        digest_hour, digest_minute = map(int, settings.twitter_digest_time.split(":"))
        scheduler.register_cron(
            "twitter_digest",
            twitter_digest_job,
            digest_hour,
            digest_minute,
            timezone=settings.twitter_digest_timezone,
        )
        logger.info(
            "Twitter digest scheduled at {} {}",
            settings.twitter_digest_time,
            settings.twitter_digest_timezone,
        )

    # Twitter monitor
    async def twitter_monitor_job():
        from config.settings import get_settings
        from server.social.monitor import list_active_twitter_accounts, run_twitter_monitor
        from server.social.processor import TweetProcessor

        tg = telegram_bot if telegram_bot else feishu_bot
        accounts = await list_active_twitter_accounts(get_settings().twitter_accounts)
        if not accounts:
            logger.info("Twitter monitor skipped: no active accounts")
            if tg:
                await tg.push_to_admin("当前没有 Twitter 关注列表。")
            return
        processor = TweetProcessor()
        await run_twitter_monitor(accounts, tg, processor, notify_no_updates=True)

    scheduler.register_interval(
        "twitter_monitor",
        twitter_monitor_job,
        settings.twitter_monitor_interval,
        run_immediately=True,
    )

    # Intraday alerts (every 30 min during market hours)
    if settings.alert_enabled:

        async def alert_cycle_job():
            from server.alerts.engine import run_alert_cycle

            tg = telegram_bot if telegram_bot else feishu_bot
            await run_alert_cycle(tg)

        scheduler.register_interval(
            "alert_cycle", alert_cycle_job, settings.alert_interval_minutes * 60
        )

    scheduler.start()
    logger.info("Scheduler started")

    # Store references
    app.state.telegram_bot = telegram_bot
    app.state.feishu_bot = feishu_bot
    app.state.scheduler = scheduler

    logger.info("Reveal is running.")
    yield

    # Shutdown
    logger.info("Shutting down Reveal...")
    scheduler.stop()
    if feishu_bot:
        feishu_bot.stop()
    if telegram_bot:
        await telegram_bot.stop()
    from server.db.engine import close_db

    await close_db()
    logger.info("Reveal stopped.")


app = FastAPI(title="Reveal", version="0.1.0", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
app.include_router(web_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/feishu/event")
async def feishu_event(
    request: Request,
    x_lark_request_timestamp: str | None = Header(default=None),
    x_lark_request_nonce: str | None = Header(default=None),
    x_lark_signature: str | None = Header(default=None),
):
    body = await request.json()
    bot = getattr(request.app.state, "feishu_bot", None)
    if bot is None:
        return {"error": "Feishu bot is not configured."}

    if bot.verification_token and x_lark_signature:
        encrypt = body.get("encrypt", "")
        if not bot.verify_signature(
            x_lark_request_timestamp or "",
            x_lark_request_nonce or "",
            encrypt,
            x_lark_signature,
        ):
            logger.warning("Invalid Feishu callback signature")
            return {"error": "Invalid signature"}

    return await bot.handle_event(body)


def start() -> None:
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload = os.getenv("RELOAD", "").lower() in {"1", "true", "yes"}
    uvicorn.run("server.main:app", host=host, port=port, reload=reload)
