import tempfile
import unittest
from pathlib import Path

from server.db import engine as db_engine
from server.runtime.jobs import JobSkippedError, list_recent_job_runs, run_recorded_job


class JobRunsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "reveal-jobs-test.db"
        await db_engine.close_db()
        db_engine.global_settings.database_url = f"sqlite+aiosqlite:///{db_path}"
        db_engine.global_settings.database_echo = False
        await db_engine.init_db()

    async def asyncTearDown(self):
        await db_engine.close_db()
        self.tmpdir.cleanup()

    async def test_run_recorded_job_records_success(self):
        async def job():
            return ["a", "b"]

        result = await run_recorded_job("job-1", "module-1", job)
        runs = await list_recent_job_runs()

        self.assertEqual(result, ["a", "b"])
        self.assertEqual(runs[0]["status"], "succeeded")
        self.assertEqual(runs[0]["metrics"]["count"], 2)

    async def test_run_recorded_job_records_skipped(self):
        async def job():
            raise JobSkippedError("outside market hours", {"market_open": False})

        result = await run_recorded_job("job-2", "module-2", job)
        runs = await list_recent_job_runs(job_id="job-2")

        self.assertIsNone(result)
        self.assertEqual(runs[0]["status"], "skipped")
        self.assertEqual(runs[0]["summary"], "outside market hours")

    async def test_run_recorded_job_records_failure_and_reraises(self):
        async def job():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await run_recorded_job("job-3", "module-3", job)

        runs = await list_recent_job_runs(job_id="job-3")
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("boom", runs[0]["error"])


if __name__ == "__main__":
    unittest.main()
