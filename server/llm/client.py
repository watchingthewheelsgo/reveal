"""
Multi-provider LLM client compatible with OpenAI SDK format.
Supports DeepSeek, Qwen, OpenAI, and any OpenAI-compatible endpoint.
"""

import json
import re

from loguru import logger
from openai import AsyncOpenAI, AuthenticationError
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
            api_key=settings.get_llm_auth_token(),
            base_url=settings.get_llm_base_url(),
        )
        self.model = settings.get_llm_model()
        self.max_tokens = settings.max_tokens
        self.temperature = settings.temperature
        self.auth_failed = False

    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if self.auth_failed:
            raise LLMAuthenticationError("LLM authentication is disabled after a previous failure.")
        try:
            response = await self.client.chat.completions.create(
                model=model or self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            )
        except AuthenticationError as exc:
            self.auth_failed = True
            raise LLMAuthenticationError(
                "DeepSeek authentication failed. Check DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN."
            ) from exc
        return response.choices[0].message.content or ""

    async def classify_intent(self, message: str) -> dict:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": _INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": message},
        ]
        raw = ""
        try:
            raw = await self.chat(messages, temperature=0, max_tokens=200)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                cleaned = "\n".join(lines)
            return json.loads(cleaned)
        except (json.JSONDecodeError, KeyError):
            logger.debug(f"Intent classification parse error: {raw[:200]}")
            return classify_intent_locally(message)
        except LLMAuthenticationError as e:
            logger.warning(
                "Intent classification disabled: LLM authentication failed. "
                "Check DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN."
            )
            logger.debug(f"Intent classification auth error: {e}")
            return classify_intent_locally(message)

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


class LLMAuthenticationError(RuntimeError):
    """Raised after the configured OpenAI-compatible key has failed authentication."""


def classify_intent_locally(message: str) -> dict:
    """Small deterministic fallback used when the lightweight LLM classifier is unavailable."""
    text = message.strip()
    lowered = text.lower()
    ticker = _extract_ticker(text)

    if any(keyword in lowered for keyword in {"status", "状态", "系统"}):
        return {"intent": "status", "ticker": ticker, "query": text}

    trade_keywords = {"买入", "卖出", "做空", "平仓", "trade", "buy", "sell"}
    if any(keyword in lowered for keyword in trade_keywords):
        return {"intent": "trade", "ticker": ticker, "query": text}

    if ticker:
        quick_keywords = {"价格", "多少钱", "现价", "当前", "quote", "price"}
        if any(keyword in lowered for keyword in quick_keywords):
            return {"intent": "question", "ticker": ticker, "query": text}
        return {"intent": "research", "ticker": ticker, "query": text}

    research_keywords = {
        "分析",
        "研究",
        "深挖",
        "怎么看",
        "为什么",
        "影响",
        "财报",
        "新闻",
        "search",
        "research",
    }
    if any(keyword in lowered for keyword in research_keywords):
        return {"intent": "research", "ticker": None, "query": text}

    question_keywords = {"持仓", "portfolio", "行情", "今天", "收益", "pnl", "盈亏"}
    if any(keyword in lowered for keyword in question_keywords):
        return {"intent": "question", "ticker": None, "query": text}

    return {"intent": "chat", "ticker": None, "query": text}


def _extract_ticker(message: str) -> str | None:
    cashtag = re.search(r"\$([A-Za-z]{1,6})(?=\b)", message)
    if cashtag:
        return cashtag.group(1).upper()

    for match in re.finditer(r"(?<![A-Za-z])([A-Z]{1,6})(?![A-Za-z])", message):
        value = match.group(1).upper()
        if value not in {"AI", "CEO", "CFO", "ETF", "IPO", "USD", "API", "LLM"}:
            return value
    return None


def get_llm_client() -> LLMClient | None:
    global _llm_client
    if _llm_client is None:
        from config.settings import get_settings

        if get_settings().is_llm_configured():
            _llm_client = LLMClient()
    return _llm_client
