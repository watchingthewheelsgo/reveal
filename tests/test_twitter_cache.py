import unittest
from unittest.mock import AsyncMock, patch

from server.social.monitor import cache_user_tweets


class TwitterCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_cache_user_tweets_checks_database_before_fetching_twitter(self):
        fetch_user_tweets = AsyncMock()

        with (
            patch(
                "server.social.monitor.get_session_factory",
                side_effect=RuntimeError("Database not initialized."),
            ),
            patch("server.social.monitor.fetch_user_tweets", new=fetch_user_tweets),
        ):
            with self.assertRaisesRegex(RuntimeError, "Database not initialized"):
                await cache_user_tweets("alice", count=2)

        fetch_user_tweets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
