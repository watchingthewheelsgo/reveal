import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from server.bot.base import BotContext
from server.commands import cmd_task
from server.db import engine as db_engine
from server.tasks.scheduled import (
    cancel_scheduled_task,
    clear_scheduled_task_runtime,
    configure_scheduled_task_runtime,
    create_scheduled_task,
    execute_scheduled_task,
    list_scheduled_tasks,
    parse_schedule_time,
    restore_pending_scheduled_tasks,
    split_schedule_command_body,
)


class DummyScheduler:
    def __init__(self):
        self.jobs: dict[str, datetime] = {}
        self.removed: list[str] = []

    def register_date(self, name, func, run_at):
        self.jobs[name] = run_at

    def remove_job(self, name: str) -> bool:
        if name in self.jobs:
            self.jobs.pop(name)
            self.removed.append(name)
            return True
        return False


class DummyAdapter:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        self.messages.append((chat_id, text))


class ScheduledTasksTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()
        clear_scheduled_task_runtime()

    async def asyncTearDown(self):
        clear_scheduled_task_runtime()
        await db_engine.close_db()
        self.tmpdir.cleanup()

    def test_parse_relative_and_absolute_chinese_time(self):
        now = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)

        relative = parse_schedule_time("2小时后", timezone="UTC", now=now)
        self.assertEqual(relative.run_at_utc, datetime(2026, 6, 11, 12, 0, tzinfo=UTC))

        tonight = parse_schedule_time("今晚7点", timezone="UTC", now=now)
        self.assertEqual(tonight.run_at_utc, datetime(2026, 6, 11, 19, 0, tzinfo=UTC))

    def test_split_schedule_command_body(self):
        self.assertEqual(
            split_schedule_command_body("2小时后 | 推送 CPI 数据新闻"),
            ("2小时后", "推送 CPI 数据新闻"),
        )
        self.assertIsNone(split_schedule_command_body("今晚7点 推送 CPI 数据新闻"))

    async def test_create_list_and_cancel_task(self):
        scheduler = DummyScheduler()
        configure_scheduled_task_runtime(scheduler, {"telegram": DummyAdapter()})

        payload = await create_scheduled_task(
            chat_id="chat-1",
            platform="telegram",
            run_at_text="2小时后",
            prompt="推送 CPI 数据新闻",
            timezone="UTC",
            created_by="user-1",
        )

        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["prompt"], "推送 CPI 数据新闻")
        self.assertIn(f"user_scheduled_task:{payload['id']}", scheduler.jobs)

        task_list = await list_scheduled_tasks(chat_id="chat-1")
        self.assertEqual(task_list["count"], 1)

        cancelled = await cancel_scheduled_task(payload["id"], chat_id="chat-1")
        self.assertTrue(cancelled["cancelled"])
        self.assertIn(f"user_scheduled_task:{payload['id']}", scheduler.removed)

        task_list = await list_scheduled_tasks(chat_id="chat-1")
        self.assertEqual(task_list["count"], 0)

    async def test_restore_pending_tasks_registers_jobs(self):
        payload = await create_scheduled_task(
            chat_id="chat-1",
            platform="telegram",
            run_at_text="2小时后",
            prompt="推送 CPI 数据新闻",
            timezone="UTC",
        )
        clear_scheduled_task_runtime()

        scheduler = DummyScheduler()
        restored = await restore_pending_scheduled_tasks(scheduler, {"telegram": DummyAdapter()})

        self.assertEqual(restored, 1)
        self.assertIn(f"user_scheduled_task:{payload['id']}", scheduler.jobs)

    async def test_execute_task_runs_agent_and_pushes_result(self):
        adapter = DummyAdapter()
        payload = await create_scheduled_task(
            chat_id="chat-1",
            platform="telegram",
            run_at_text="2小时后",
            prompt="推送 CPI 数据新闻",
            timezone="UTC",
        )

        async def fake_start_agent_session(chat_id: str, message: str):
            return type("Session", (), {"id": 99, "chat_id": chat_id, "source_query": message})()

        async def fake_run_agent_session_message(session, message: str, platform: str = "auto"):
            return "CPI 新闻摘要"

        with (
            patch("server.research.service.start_agent_session", new=fake_start_agent_session),
            patch(
                "server.research.service.run_agent_session_message",
                new=fake_run_agent_session_message,
            ),
        ):
            await execute_scheduled_task(payload["id"], {"telegram": adapter})

        self.assertEqual(adapter.messages[0][0], "chat-1")
        self.assertIn("开始执行", adapter.messages[0][1])
        self.assertIn("CPI 新闻摘要", adapter.messages[1][1])

        task_list = await list_scheduled_tasks(chat_id="chat-1", include_done=True)
        self.assertEqual(task_list["items"][0]["status"], "done")

    async def test_task_command_add_list_and_cancel(self):
        scheduler = DummyScheduler()
        adapter = DummyAdapter()
        configure_scheduled_task_runtime(scheduler, {"auto": adapter})

        await cmd_task(
            BotContext(
                chat_id="chat-1",
                user_id="user-1",
                text="/task add 2小时后 | 推送 CPI 数据新闻",
                command="task",
                args=["add", "2小时后", "|", "推送", "CPI", "数据新闻"],
                message_id="msg-1",
            ),
            adapter,
        )

        self.assertIn("已创建定时任务 #1", adapter.messages[-1][1])
        self.assertIn("user_scheduled_task:1", scheduler.jobs)

        await cmd_task(
            BotContext(
                chat_id="chat-1",
                user_id="user-1",
                text="/task list",
                command="task",
                args=["list"],
            ),
            adapter,
        )
        self.assertIn("未来定时任务", adapter.messages[-1][1])
        self.assertIn("#1", adapter.messages[-1][1])

        await cmd_task(
            BotContext(
                chat_id="chat-1",
                user_id="user-1",
                text="/task cancel 1",
                command="task",
                args=["cancel", "1"],
            ),
            adapter,
        )
        self.assertIn("已取消定时任务 #1", adapter.messages[-1][1])
        self.assertIn("user_scheduled_task:1", scheduler.removed)


if __name__ == "__main__":
    unittest.main()
