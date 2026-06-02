"""
LLM-powered tweet processor: structured analysis with entity extraction.
"""

import json
from dataclasses import dataclass, field

from loguru import logger

from server.llm.client import get_llm_client

_ANALYZE_SYSTEM_PROMPT = """你是一个金融信息分析助手。分析以下 Twitter/X 推文，提取结构化信息。

返回严格的 JSON（不要 markdown 代码块），包含以下字段:
{
  "summary": "中文摘要，2-3句话概括核心信息",
  "translation": "只翻译主推文正文；如果主推文原文是中文则为 null。不要把链接或引用内容放进译文",
  "mentioned_tickers": ["NVDA", "TSLA"],
  "topics": ["AI基建", "关税"],
  "sentiment": "bullish 或 bearish 或 neutral 或 mixed",
  "urgency": "high 或 medium 或 low",
  "urgency_reason": "判定理由，一句话",
  "is_noteworthy": true,
  "attention_reason": "如果非常值得关注，用一句话说明原因；否则为空字符串"
}

判定规则:
- mentioned_tickers: 提取推文提到的美股 ticker 或公司名对应 ticker（如 Nvidia→NVDA）
- topics: 提取 2-5 个关键话题标签
- sentiment: 对股市/投资的情绪倾向
- urgency 判定:
  - high: 涉及政策变化、重大事件、财报意外、CEO 离职、并购等对股价有即时影响的信息
  - medium: 行业趋势、分析师观点、产品发布等有参考价值的信息
  - low: 日常讨论、个人观点、非财经内容
- is_noteworthy: 只有当该信息值得用户立即停下来关注时才为 true，例如会显著影响持仓/关注
  标的、揭示重大催化、出现异常市场信号，或包含可行动的新事实。普通观点、复述、涨跌感想
  不要标为 true。"""


@dataclass
class TweetAnalysis:
    summary: str = ""
    translation: str | None = None
    mentioned_tickers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    sentiment: str = "neutral"
    urgency: str = "low"
    urgency_reason: str = ""
    is_noteworthy: bool = False
    attention_reason: str = ""


class TweetProcessor:
    def __init__(self):
        self._llm = get_llm_client()
        self._auth_failed = False

    @property
    def is_available(self) -> bool:
        return self._llm is not None and not self._auth_failed

    async def analyze(self, context: str, author: str = "") -> TweetAnalysis | None:
        if not self._llm or self._auth_failed or not context.strip():
            return None
        user_content = context
        if author:
            user_content = f"作者: @{author}\n\n{context}"
        try:
            raw = await self._llm.chat(
                [
                    {"role": "system", "content": _ANALYZE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=1000,
            )
            return _parse_analysis(raw)
        except Exception as e:
            if _is_auth_error(e):
                self._auth_failed = True
                logger.warning(
                    "Tweet analysis disabled: LLM authentication failed. "
                    "Check DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN for the lightweight "
                    "tweet analyzer."
                )
            else:
                logger.warning(f"Tweet analysis failed: {e}")
            return None

    async def translate(self, text: str) -> str | None:
        if not self._llm or self._auth_failed:
            return None
        return await self._llm.translate(text, target_lang="中文")

    async def summarize(self, text: str) -> str | None:
        if not self._llm or self._auth_failed:
            return None
        return await self._llm.summarize(text, lang="中文")

    async def ask(self, tweet_text: str, question: str) -> str | None:
        if not self._llm or self._auth_failed:
            return None
        return await self._llm.ask(tweet_text, question)


def _parse_analysis(raw: str) -> TweetAnalysis:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return TweetAnalysis(summary=raw[:300])

    tickers = data.get("mentioned_tickers") or []
    normalized_tickers = [t.upper().strip() for t in tickers if isinstance(t, str) and t.strip()]

    return TweetAnalysis(
        summary=str(data.get("summary") or ""),
        translation=data.get("translation"),
        mentioned_tickers=normalized_tickers,
        topics=data.get("topics") or [],
        sentiment=str(data.get("sentiment") or "neutral"),
        urgency=str(data.get("urgency") or "low"),
        urgency_reason=str(data.get("urgency_reason") or ""),
        is_noteworthy=_parse_bool(data.get("is_noteworthy")),
        attention_reason=str(data.get("attention_reason") or ""),
    )


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "是"}
    return bool(value)


def _is_auth_error(error: Exception) -> bool:
    text = str(error).lower()
    return "401" in text or "authentication" in text or "api key" in text
