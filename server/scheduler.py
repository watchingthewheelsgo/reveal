"""APScheduler-based task scheduler for periodic jobs."""

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config.settings import get_settings

JobFunc = Callable[[], Awaitable[object]]


class Scheduler:
    def __init__(self):
        settings = get_settings()
        self._scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    def register_cron(
        self, name: str, func: JobFunc, hour: int, minute: int, timezone: str | None = None
    ):
        tz = timezone or get_settings().scheduler_timezone
        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
        self._scheduler.add_job(func, trigger, id=name, name=name)
        logger.info(f"Cron job registered: {name} ({hour:02d}:{minute:02d} {tz})")

    def register_interval(self, name: str, func: JobFunc, seconds: int):
        trigger = IntervalTrigger(seconds=seconds)
        self._scheduler.add_job(func, trigger, id=name, name=name)
        logger.info(f"Interval job registered: {name} (every {seconds}s)")

    def start(self):
        self._scheduler.start()
        jobs = [job.id for job in self._scheduler.get_jobs()]
        logger.info(f"Scheduler started with jobs: {jobs}")

    def stop(self):
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    async def run_job_now(self, name: str):
        jobs = self._scheduler.get_jobs()
        for job in jobs:
            if job.id == name:
                logger.info(f"Running job now: {name}")
                await job.func()
                return
        logger.warning(f"Job not found: {name}")
