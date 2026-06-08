"""Recorded job execution helpers."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import desc, select

from server.db.engine import get_session_factory
from server.db.models import JobRun

JobFunc = Callable[[], Awaitable[Any]]


class JobSkippedError(Exception):
    """Raised by a recorded job when skip is an expected outcome."""

    def __init__(self, summary: str = "skipped", metrics: dict[str, Any] | None = None):
        super().__init__(summary)
        self.summary = summary
        self.metrics = metrics or {}


async def run_recorded_job(
    job_id: str,
    module_id: str,
    func: JobFunc,
) -> Any:
    """Run a job and persist status, duration, and lightweight metrics."""
    row = await _create_job_run(job_id, module_id)
    started = time.monotonic()
    try:
        result = await func()
    except JobSkippedError as exc:
        await _finish_job_run(
            row.id,
            "skipped",
            started,
            summary=exc.summary,
            metrics=exc.metrics,
        )
        return None
    except Exception as exc:
        await _finish_job_run(
            row.id,
            "failed",
            started,
            summary="failed",
            error=str(exc),
        )
        logger.exception("Recorded job failed: job_id={} module_id={}", job_id, module_id)
        raise
    await _finish_job_run(
        row.id,
        "succeeded",
        started,
        summary=_summary_for_result(result),
        metrics=_metrics_for_result(result),
    )
    return result


async def list_recent_job_runs(limit: int = 50, job_id: str | None = None) -> list[dict[str, Any]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        statement = select(JobRun)
        if job_id:
            statement = statement.where(JobRun.job_id == job_id)
        result = await session.execute(
            statement.order_by(desc(JobRun.started_at), desc(JobRun.id)).limit(limit)
        )
        return [_job_run_payload(row) for row in result.scalars().all()]


async def _create_job_run(job_id: str, module_id: str) -> JobRun:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = JobRun(job_id=job_id, module_id=module_id, status="running", started_at=_utcnow())
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _finish_job_run(
    run_id: int,
    status: str,
    started_monotonic: float,
    *,
    summary: str = "",
    metrics: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.get(JobRun, run_id)
        if row is None:
            return
        row.status = status
        row.finished_at = _utcnow()
        row.duration_ms = int((time.monotonic() - started_monotonic) * 1000)
        row.summary = summary or status
        row.metrics = metrics or {}
        row.error = error
        await session.commit()


def _summary_for_result(result: Any) -> str:
    if result is None:
        return "completed"
    if isinstance(result, list):
        return f"{len(result)} items"
    if isinstance(result, dict):
        if "summary" in result:
            return str(result["summary"])
        return "completed"
    if isinstance(result, int):
        return f"{result} items"
    return "completed"


def _metrics_for_result(result: Any) -> dict[str, Any]:
    if isinstance(result, list):
        return {"count": len(result)}
    if isinstance(result, dict):
        return {key: value for key, value in result.items() if _json_scalar(value)}
    if isinstance(result, int):
        return {"count": result}
    return {}


def _job_run_payload(row: JobRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "module_id": row.module_id,
        "status": row.status,
        "started_at": _dt(row.started_at),
        "finished_at": _dt(row.finished_at),
        "duration_ms": row.duration_ms,
        "summary": row.summary,
        "metrics": row.metrics or {},
        "error": row.error,
    }


def _json_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) or value is None


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
