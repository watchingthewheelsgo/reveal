"""
Multi-provider LLM client compatible with OpenAI SDK format.
Supports DeepSeek, Qwen, OpenAI, and any OpenAI-compatible endpoint.
"""

import json

from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from config.settings import get_settings

_INTENT_SYSTEM_PROMPT = """判断用户消息的意图。返回严格的 JSON（不要 markdown 代码块）:
{
  "intent": "research" 或 "trade" 或 "question" 或 "status" 或 "chat",
  "ticker": "NVDA" 或 null,
  "query": "用户实际想问或研究的问题"
}

意图判定规则:
- research: 需要深度分析、搜索外部信息的问题（如"帮我分析NVDA"、"美联储加息影响"）
- trade: 用户想记录交易（如"买入AAPL 180 100股"、"卖出TSLA"）
- question: 可以用已有数据快速回答的问题（如"NVDA现在多少钱"、"我的持仓"、"今天行情怎么样"）
- status: 查看系统状态
- chat: 日常对话、感谢、问候等

如果消息提到具体股票 ticker 或公司名，在 ticker 字段填入对应 ticker。"""


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
            temperature=temperature if temperature is not None else self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        return response.choices[0].message.content or ""

    async def classify_intent(self, message: str) -> dict:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        raw = await self.chat(messages, temperature=0, max_tokens=200)
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except (json.JSONDecodeError, KeyError):
            logger.debug(f"Intent classification parse error: {raw[:200]}")
            return {"intent": "chat", "ticker": None, "query": message}

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
