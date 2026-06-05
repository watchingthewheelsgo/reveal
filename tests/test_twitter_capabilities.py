import unittest
from unittest.mock import AsyncMock, patch

from server.capabilities.twitter import set_twitter_watch_account_payload


class TwitterCapabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_watch_add_can_backfill_latest_posts_for_agent(self):
        latest_payload = {
            "username": "aleabitoreddit",
            "limit": 10,
            "posts": [{"id": 1, "content": "latest"}],
        }

        with (
            patch(
                "server.social.monitor.set_twitter_account_active",
                new=AsyncMock(),
            ) as set_active,
            patch(
                "server.capabilities.twitter.get_twitter_latest_payload",
                new=AsyncMock(return_value=latest_payload),
            ) as get_latest,
        ):
            payload = await set_twitter_watch_account_payload(
                "@aleabitoreddit",
                is_active=True,
                backfill_limit=10,
            )

        set_active.assert_awaited_once_with("aleabitoreddit", True)
        get_latest.assert_awaited_once_with("aleabitoreddit", limit=10)
        self.assertEqual(payload["username"], "aleabitoreddit")
        self.assertTrue(payload["active"])
        self.assertEqual(payload["backfill_limit"], 10)
        self.assertEqual(payload["posts"], latest_payload["posts"])

    async def test_watch_remove_does_not_backfill(self):
        with (
            patch(
                "server.social.monitor.set_twitter_account_active",
                new=AsyncMock(),
            ) as set_active,
            patch(
                "server.capabilities.twitter.get_twitter_latest_payload",
                new=AsyncMock(),
            ) as get_latest,
        ):
            payload = await set_twitter_watch_account_payload(
                "aleabitoreddit",
                is_active=False,
                backfill_limit=10,
            )

        set_active.assert_awaited_once_with("aleabitoreddit", False)
        get_latest.assert_not_awaited()
        self.assertFalse(payload["active"])
        self.assertEqual(payload["backfill_limit"], 0)
        self.assertEqual(payload["posts"], [])


if __name__ == "__main__":
    unittest.main()
