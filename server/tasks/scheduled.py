"""Persistent one-shot scheduled tasks created by users or Agent tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from loguru import logger
from sqlalchemy import desc, select

from config.settings import get_settings
from server.db.engine import get_session_factory
from server.db.models import ScheduledTask
from server.db.time import assume_utc, to_naive_utc, utc_now_naive

TaskScheduler = Any
TaskAdapter = Any
TaskAdapters = dict[str, TaskAdapter | None]

_runtime_scheduler: TaskScheduler | None = None
_runtime_adapters: TaskAdapters = {}

_CHINESE_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass(frozen=True)
class ParsedScheduleTime:
    run_at_utc: datetime
    run_at_local: datetime
    timezone: str
    source_text: str


def configure_scheduled_task_runtime(
    scheduler: TaskScheduler,
    adapters: TaskAdapters,
) -> None:
    """Set the live scheduler/adapters used by commands and in-process Agent tools."""
    global _runtime_scheduler, _runtime_adapters
    _runtime_scheduler = scheduler
    _runtime_adapters = adapters


def clear_scheduled_task_runtime() -> None:
    global _runtime_scheduler, _runtime_adapters
    _runtime_scheduler = None
    _runtime_adapters = {}


async def restore_pending_scheduled_tasks(
    scheduler: TaskScheduler | None = None,
    adapters: TaskAdapters | None = None,
) -> int:
    """Register pending DB tasks with APScheduler after process startup."""
    if scheduler is not None:
        configure_scheduled_task_runtime(scheduler, adapters or _runtime_adapters)

    session_factory = get_session_factory()
    now = utc_now_naive()
    restored = 0
    async with session_factory() as session:
        result = await session.execute(
            select(ScheduledTask)
            .where(ScheduledTask.status == "pending")
            .order_by(ScheduledTask.run_at.asc(), ScheduledTask.id.asc())
        )
        tasks = result.scalars().all()
        for task in tasks:
            if task.run_at <= now:
                _schedule_task(task.id, datetime.now(UTC) + timedelta(seconds=1))
            else:
                _schedule_task(task.id, assume_utc(task.run_at))
            restored += 1

    logger.info("Restored {} pending scheduled tasks", restored)
    return restored


async def create_scheduled_task(
    *,
    chat_id: str,
    platform: str,
    run_at_text: str,
    prompt: str,
    timezone: str | None = None,
    created_by: str | None = None,
    source_message_id: str | None = None,
) -> dict[str, Any]:
    """Create and register a one-shot Agent research task."""
    chat_id = chat_id.strip()
    if not chat_id:
        raise ValueError("chat_id is required")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt is required")

    parsed = parse_schedule_time(run_at_text, timezone=timezone)
    if parsed.run_at_utc <= datetime.now(UTC):
        raise ValueError("scheduled time must be in the future")

    normalized_platform = _normalize_platform(platform)
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = ScheduledTask(
            chat_id=chat_id,
            platform=normalized_platform,
            created_by=created_by,
            source_message_id=source_message_id,
            task_type="agent_research",
            prompt=prompt,
            run_at=to_naive_utc(parsed.run_at_utc),
            timezone=parsed.timezone,
            status="pending",
        )
        session.add(task)
        await session.flush()
        task_id = task.id
        await session.commit()

    _schedule_task(task_id, parsed.run_at_utc)
    task = await get_scheduled_task(task_id)
    if task is None:
        raise RuntimeError("scheduled task was not persisted")
    return scheduled_task_payload(task)


async def get_scheduled_task(task_id: int) -> ScheduledTask | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))
        return result.scalar_one_or_none()


async def list_scheduled_tasks(
    *,
    chat_id: str | None = None,
    include_done: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        query = select(ScheduledTask)
        if chat_id:
            query = query.where(ScheduledTask.chat_id == chat_id)
        if include_done:
            query = query.order_by(desc(ScheduledTask.run_at), desc(ScheduledTask.id))
        else:
            query = query.where(ScheduledTask.status.in_(("pending", "running"))).order_by(
                ScheduledTask.run_at.asc(), ScheduledTask.id.asc()
            )
        result = await session.execute(query.limit(max(1, min(limit, 100))))
        tasks = result.scalars().all()
    return {
        "count": len(tasks),
        "items": [scheduled_task_payload(task) for task in tasks],
    }


async def cancel_scheduled_task(task_id: int, chat_id: str | None = None) -> dict[str, Any]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        clauses = [ScheduledTask.id == task_id]
        if chat_id:
            clauses.append(ScheduledTask.chat_id == chat_id)
        result = await session.execute(select(ScheduledTask).where(*clauses))
        task = result.scalar_one_or_none()
        if task is None:
            return {"task_id": task_id, "cancelled": False, "message": "找不到定时任务"}
        if task.status not in {"pending", "running"}:
            return {
                "task_id": task.id,
                "cancelled": False,
                "message": f"任务 #{task.id} 已经是 {task.status}，无法取消",
            }
        task.status = "cancelled"
        task.completed_at = utc_now_naive()
        task.updated_at = utc_now_naive()
        await session.commit()

    removed = _remove_scheduled_job(task_id)
    return {
        "task_id": task_id,
        "cancelled": True,
        "job_removed": removed,
        "message": f"已取消定时任务 #{task_id}",
    }


async def execute_scheduled_task(task_id: int, adapters: TaskAdapters | None = None) -> None:
    """Run one scheduled task, update DB status, and push the result back to chat."""
    adapters = adapters or _runtime_adapters
    task = await _claim_task(task_id)
    if task is None:
        return

    adapter = _adapter_for_task(task, adapters)
    if adapter is None:
        await _fail_task(task.id, f"No adapter available for platform={task.platform}")
        return

    try:
        await adapter.send_message(
            task.chat_id,
            f"⏰ 定时任务 #{task.id} 开始执行\n{task.prompt}",
        )
        from server.research.service import run_agent_session_message, start_agent_session

        session = await start_agent_session(task.chat_id, task.prompt)
        answer = await run_agent_session_message(session, task.prompt, platform=task.platform)
        result_text = f"*定时任务 #{task.id} · 执行完成*\n\n{answer}"
        await adapter.send_message(task.chat_id, result_text)
        await _complete_task(task.id, answer)
    except Exception as exc:
        logger.exception("Scheduled task execution failed: task_id={}", task.id)
        await _fail_task(task.id, str(exc))
        try:
            await adapter.send_message(
                task.chat_id,
                f"❌ 定时任务 #{task.id} 执行失败: {exc}",
            )
        except Exception:
            logger.exception("Scheduled task failure notification failed: task_id={}", task.id)


def parse_schedule_time(
    text: str,
    *,
    timezone: str | None = None,
    now: datetime | None = None,
) -> ParsedScheduleTime:
    source_text = text.strip()
    if not source_text:
        raise ValueError("run_at_text is required")
    tz_name = _normalize_timezone(timezone)
    tz = ZoneInfo(tz_name)
    now_local = (now or datetime.now(tz)).astimezone(tz)

    relative = _parse_relative_time(source_text, now_local)
    if relative is not None:
        return _parsed(relative, tz_name, source_text)

    chinese = _parse_chinese_absolute_time(source_text, now_local)
    if chinese is not None:
        return _parsed(chinese, tz_name, source_text)

    english = _parse_english_absolute_time(source_text, now_local)
    if english is not None:
        return _parsed(english, tz_name, source_text)

    try:
        default = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        value = date_parser.parse(source_text, fuzzy=True, default=default)
    except (TypeError, ValueError) as exc:
        raise ValueError("无法解析时间，请使用 '2小时后'、'今晚7点' 或 ISO 时间") from exc

    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    else:
        value = value.astimezone(tz)
    if value <= now_local:
        raise ValueError("scheduled time must be in the future")
    return _parsed(value, tz_name, source_text)


def split_schedule_command_body(body: str) -> tuple[str, str] | None:
    """Split a compact /task add body into (run_at_text, prompt)."""
    text = body.strip()
    if not text:
        return None
    if "|" in text:
        left, right = text.split("|", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()

    patterns = [
        r"^(?P<time>(?:in\s*)?\d+\s*(?:h|hr|hrs|hour|hours|m|min|mins|minute|minutes|d|day|days))\s+(?P<prompt>.+)$",
        r"^(?P<time>[零一二两三四五六七八九十\d]+\s*(?:个)?\s*(?:小时|钟头|分钟|分|天|日)后)\s*(?P<prompt>.+)$",
        r"^(?P<time>(?:今天|今日|今晚|明天|明晚|后天)?\s*(?:早上|上午|中午|下午|晚上|今晚|明晚)?\s*\d{1,2}(?:[:：]\d{1,2})?\s*点(?:半)?)\s*(?P<prompt>.+)$",
        r"^(?P<time>(?:today|tonight|tomorrow)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?P<prompt>.+)$",
        r"^(?P<time>\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2})?)\s+(?P<prompt>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("time").strip(), match.group("prompt").strip()
    return None


def scheduled_task_payload(task: ScheduledTask) -> dict[str, Any]:
    run_at_utc = assume_utc(task.run_at)
    timezone = task.timezone or "UTC"
    run_at_local = run_at_utc.astimezone(ZoneInfo(timezone))
    return {
        "id": task.id,
        "chat_id": task.chat_id,
        "platform": task.platform,
        "task_type": task.task_type,
        "prompt": task.prompt,
        "run_at": run_at_utc.isoformat(),
        "run_at_local": run_at_local.isoformat(),
        "timezone": timezone,
        "status": task.status,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def format_scheduled_task_created(payload: dict[str, Any]) -> str:
    return (
        f"✅ 已创建定时任务 #{payload['id']}\n"
        f"时间: {_format_local_time(payload)}\n"
        f"内容: {payload['prompt']}"
    )


def format_scheduled_task_list(payload: dict[str, Any]) -> str:
    items = payload["items"]
    if not items:
        return "当前没有未来定时任务。"
    lines = ["*未来定时任务*"]
    for item in items:
        lines.append(
            f"#{item['id']} · {item['status']} · {_format_local_time(item)}\n  {item['prompt']}"
        )
    return "\n".join(lines)


def _format_local_time(payload: dict[str, Any]) -> str:
    return f"{payload['run_at_local']} ({payload['timezone']})"


async def _claim_task(task_id: int) -> ScheduledTask | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))
        task = result.scalar_one_or_none()
        if task is None:
            logger.warning("Scheduled task not found: {}", task_id)
            return None
        if task.status != "pending":
            logger.info("Scheduled task skipped: task_id={} status={}", task.id, task.status)
            return None
        task.status = "running"
        task.updated_at = utc_now_naive()
        await session.commit()
        return task


async def _complete_task(task_id: int, result_text: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = "done"
            task.result = result_text
            task.completed_at = utc_now_naive()
            task.updated_at = utc_now_naive()
            await session.commit()


async def _fail_task(task_id: int, error: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        result = await session.execute(select(ScheduledTask).where(ScheduledTask.id == task_id))
        task = result.scalar_one_or_none()
        if task:
            task.status = "failed"
            task.error = error[:2000]
            task.completed_at = utc_now_naive()
            task.updated_at = utc_now_naive()
            await session.commit()


def _schedule_task(task_id: int, run_at_utc: datetime) -> bool:
    scheduler = _runtime_scheduler
    if scheduler is None:
        logger.info("Scheduled task persisted without live scheduler: task_id={}", task_id)
        return False

    async def run() -> None:
        await execute_scheduled_task(task_id, _runtime_adapters)

    scheduler.register_date(_job_id(task_id), run, run_at_utc.astimezone(UTC))
    return True


def _remove_scheduled_job(task_id: int) -> bool:
    scheduler = _runtime_scheduler
    if scheduler is None:
        return False
    return bool(scheduler.remove_job(_job_id(task_id)))


def _job_id(task_id: int) -> str:
    return f"user_scheduled_task:{task_id}"


def _adapter_for_task(task: ScheduledTask, adapters: TaskAdapters) -> TaskAdapter | None:
    if task.platform in adapters and adapters[task.platform] is not None:
        return adapters[task.platform]
    if task.platform == "auto":
        return adapters.get("telegram") or adapters.get("feishu")
    return adapters.get("telegram") or adapters.get("feishu")


def _normalize_platform(platform: str | None) -> str:
    value = (platform or "auto").strip().lower()
    if value in {"telegram", "feishu", "auto"}:
        return value
    return "auto"


def _normalize_timezone(timezone: str | None) -> str:
    value = (timezone or get_settings().scheduler_timezone).strip()
    if not value:
        value = get_settings().scheduler_timezone
    try:
        ZoneInfo(value)
    except Exception as exc:
        raise ValueError(f"unknown timezone: {value}") from exc
    return value


def _parse_relative_time(text: str, now_local: datetime) -> datetime | None:
    normalized = text.strip().lower()
    match = re.search(
        r"(?:in\s*)?([0-9]+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes|d|day|days)\b",
        normalized,
    )
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("h"):
            return now_local + timedelta(hours=amount)
        if unit.startswith("m"):
            return now_local + timedelta(minutes=amount)
        return now_local + timedelta(days=amount)

    match = re.search(
        r"([零一二两三四五六七八九十\d]+)\s*(?:个)?\s*(小时|钟头|分钟|分|天|日)后",
        text,
    )
    if not match:
        return None
    amount = _parse_number(match.group(1))
    unit = match.group(2)
    if unit in {"小时", "钟头"}:
        return now_local + timedelta(hours=amount)
    if unit in {"分钟", "分"}:
        return now_local + timedelta(minutes=amount)
    return now_local + timedelta(days=amount)


def _parse_chinese_absolute_time(text: str, now_local: datetime) -> datetime | None:
    day_offset, explicit_day = _chinese_day_offset(text)
    match = re.search(r"(\d{1,2})(?:[:：](\d{1,2}))?\s*点(半)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if match.group(3):
        minute = 30
    if re.search(r"下午|晚上|今晚|明晚", text) and hour < 12:
        hour += 12
    if "中午" in text and hour < 11:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time values must be in 00:00..23:59")
    value = (now_local + timedelta(days=day_offset)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if value <= now_local:
        if explicit_day:
            raise ValueError("scheduled time must be in the future")
        value += timedelta(days=1)
    return value


def _parse_english_absolute_time(text: str, now_local: datetime) -> datetime | None:
    normalized = text.strip().lower()
    day_offset = 0
    explicit_day = False
    if "tomorrow" in normalized:
        day_offset = 1
        explicit_day = True
    elif "today" in normalized or "tonight" in normalized:
        explicit_day = True

    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", normalized)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if "tonight" in normalized and meridiem is None and hour < 12:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time values must be in 00:00..23:59")
    value = (now_local + timedelta(days=day_offset)).replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if value <= now_local:
        if explicit_day:
            raise ValueError("scheduled time must be in the future")
        value += timedelta(days=1)
    return value


def _chinese_day_offset(text: str) -> tuple[int, bool]:
    if "后天" in text:
        return 2, True
    if "明天" in text or "明晚" in text:
        return 1, True
    if "今天" in text or "今日" in text or "今晚" in text:
        return 0, True
    return 0, False


def _parse_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = _CHINESE_NUMBERS.get(left, 1) if left else 1
        ones = _CHINESE_NUMBERS.get(right, 0) if right else 0
        return tens * 10 + ones
    if value in _CHINESE_NUMBERS:
        return _CHINESE_NUMBERS[value]
    raise ValueError(f"unsupported number: {value}")


def _parsed(value_local: datetime, timezone: str, source_text: str) -> ParsedScheduleTime:
    if value_local.tzinfo is None:
        value_local = value_local.replace(tzinfo=ZoneInfo(timezone))
    run_at_local = value_local.astimezone(ZoneInfo(timezone))
    return ParsedScheduleTime(
        run_at_utc=run_at_local.astimezone(UTC),
        run_at_local=run_at_local,
        timezone=timezone,
        source_text=source_text,
    )
