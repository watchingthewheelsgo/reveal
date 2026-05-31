"""
LLM-powered tweet processor: translation, summarization, and QA.
"""

from server.llm.client import get_llm_client


class TweetProcessor:
    def __init__(self):
        self._llm = get_llm_client()

    @property
    def is_available(self) -> bool:
        return self._llm is not None

    async def translate(self, text: str) -> str | None:
        if not self._llm:
            return None
        return await self._llm.translate(text, target_lang="中文")

    async def summarize(self, text: str) -> str | None:
        if not self._llm:
            return None
        return await self._llm.summarize(text, lang="中文")

    async def ask(self, tweet_text: str, question: str) -> str | None:
        if not self._llm:
            return None
        return await self._llm.ask(tweet_text, question)
