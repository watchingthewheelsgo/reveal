import unittest
from datetime import datetime

from server.db.models import SocialPost
from server.social.digest import _digest_relevant_posts, _format_event_digest


class TwitterDigestTest(unittest.TestCase):
    def test_event_digest_groups_related_market_posts(self):
        posts = [
            SocialPost(
                id=1,
                username="alice",
                tweet_id="1",
                tweet_url="https://x.com/alice/status/1",
                content="Fed keeps rates unchanged",
                summary="美联储维持利率不变，并暗示后续观察通胀。",
                raw_json={"reveal_analysis": {"canonical_event": {"id": "fed-rate-decision"}}},
                mentioned_tickers=["SPY"],
                topics=["FOMC"],
                urgency="high",
                is_noteworthy=True,
                attention_reason="利率路径影响风险偏好。",
                posted_at=datetime(2026, 6, 17, 12, 0),
            ),
            SocialPost(
                id=2,
                username="bob",
                tweet_id="2",
                tweet_url="https://x.com/bob/status/2",
                content="Another take on the Fed decision",
                summary="交易员关注点转向下次会议点阵图。",
                raw_json={"reveal_analysis": {"canonical_event": {"id": "fed-rate-decision"}}},
                mentioned_tickers=["QQQ"],
                topics=["FOMC"],
                urgency="medium",
                attention_reason="科技股估值对利率预期敏感。",
                posted_at=datetime(2026, 6, 17, 12, 5),
            ),
        ]

        text = _format_event_digest(datetime(2026, 6, 17).date(), posts)

        self.assertIn("Twitter 事件日报", text)
        self.assertIn("归并为 1 个事件", text)
        self.assertIn("@alice", text)
        self.assertIn("@bob", text)
        self.assertIn("SPY", text)
        self.assertIn("QQQ", text)

    def test_digest_relevance_filter_skips_non_market_noise(self):
        relevant = SocialPost(
            id=1,
            username="alice",
            tweet_id="1",
            content="Market update",
            urgency="medium",
            posted_at=datetime(2026, 6, 17, 12, 0),
        )
        noise = SocialPost(
            id=2,
            username="bob",
            tweet_id="2",
            content="Movie discussion",
            urgency="low",
            posted_at=datetime(2026, 6, 17, 12, 1),
        )

        filtered = _digest_relevant_posts([relevant, noise])

        self.assertEqual([post.tweet_id for post in filtered], ["1"])


if __name__ == "__main__":
    unittest.main()
