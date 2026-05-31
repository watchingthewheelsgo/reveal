"""
Multi-provider LLM client compatible with OpenAI SDK format.
Supports DeepSeek, Qwen, OpenAI, and any OpenAI-compatible endpoint.
"""

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from config.settings import get_settings


class LLMClient:
    def __init__(self):
        settings = get_settings()
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )
        self.model = settings.openai_model
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature

    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        response = await self.client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        return response.choices[0].message.content or ""

    async def translate(self, text: str, target_lang: str = "zh") -> str:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": f"将以下内容翻译为{target_lang}，只输出译文。"},
            {"role": "user", "content": text},
        ]
        return await self.chat(messages, temperature=0.1)

    async def summarize(self, text: str, lang: str = "zh") -> str:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": f"用{lang}简洁总结以下内容，保留关键信息。"},
            {"role": "user", "content": text},
        ]
        return await self.chat(messages, temperature=0.3)

    async def ask(self, context: str, question: str) -> str:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": "基于以下内容回答问题。"},
            {"role": "user", "content": f"内容：{context}\n\n问题：{question}"},
        ]
        return await self.chat(messages, temperature=0.5)

    async def analyze_journal(self, trades_json: str) -> str:
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": """分析以下交易记录，输出：
1. 胜率和盈亏比
2. 最大回撤
3. 最佳/最差交易
4. 策略有效性排序
5. 情绪与盈亏的相关性
6. 改进建议""",
            },
            {"role": "user", "content": trades_json},
        ]
        return await self.chat(messages, temperature=0.3)


_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient | None:
    global _llm_client
    if _llm_client is None:
        from config.settings import get_settings

        if get_settings().is_llm_configured():
            _llm_client = LLMClient()
    return _llm_client
