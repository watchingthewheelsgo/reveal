"""
LLM-powered tweet processor: structured analysis with entity extraction.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from server.llm.client import get_llm_client

_ANALYZE_SYSTEM_PROMPT = """你是一个市场信息分析助手。分析以下 Twitter/X 推文，提取结构化信息。

返回严格的 JSON（不要 markdown 代码块），包含以下字段:
{
  "summary": "中文摘要，2-3句话概括核心信息",
  "translation": "只翻译主推文正文；如果主推文原文是中文则为 null。不要把链接或引用内容放进译文",
  "mentioned_tickers": ["NVDA", "TSLA"],
  "topics": ["AI基建", "关税"],
  "canonical_event": {
    "id": "tariff-chip-supply-chain",
    "title": "特朗普关税消息影响芯片供应链",
    "summary": "同一事实事件的一句话描述"
  },
  "sentiment": "bullish 或 bearish 或 neutral 或 mixed",
  "urgency": "high 或 medium 或 low",
  "urgency_reason": "判定理由，一句话",
  "is_market_relevant": true,
  "is_noteworthy": true,
  "attention_reason": "如果非常值得关注，用一句话说明原因；否则为空字符串"
}

判定规则:
- mentioned_tickers: 提取推文提到的美股 ticker 或公司名对应 ticker（如 Nvidia→NVDA）
- topics: 提取 2-5 个关键话题标签，优先使用事件本身，例如“关税”“FOMC”“中东冲突”
- canonical_event: 识别这条推文对应的“事实事件”，用于跨博主合并同一新闻。
  - id 必须基于事实本身，不要包含作者名、情绪词或随机数。
  - 如果只是个人闲聊或没有明确事实事件，id/title/summary 为空字符串。
  - 多个博主讲同一新闻时，应该给出相同或高度稳定的 id。
- sentiment: 对股市/投资或宏观风险偏好的情绪倾向；无法判断时为 neutral
- is_market_relevant: 只有股市、金融、经济、政治、军事、地缘冲突、监管政策相关内容才为 true。
  NBA/体育、电影娱乐、粉丝增长、生活感想、无市场含义的 meme 或广告都必须为 false。
- urgency 判定:
  - high: 涉及政策变化、重大事件、财报意外、CEO 离职、并购等对股价有即时影响的信息
  - medium: 宏观数据、行业趋势、监管动态、政治军事进展、分析师观点等有参考价值的信息
  - low: 日常讨论、个人观点、非相关内容，或相关但没有新增事实的信息
- is_noteworthy: 只有当 is_market_relevant 为 true，且值得用户立即停下来关注时才为 true，例如会
  显著影响持仓/关注标的、揭示重大催化、出现异常市场信号、政策/军事事件升级，或包含可行动
  的新事实。普通观点、复述、涨跌感想不要标为 true。
- attention_reason: 写清“这是什么事件/新闻，以及为什么值得关注”；非重点则为空字符串。"""

_STORY_CLUSTER_SYSTEM_PROMPT = """你是市场消息聚类助手。
你会收到同一轮监控中新抓到的多条 Twitter/X 推文。

任务: 判断哪些推文在讨论同一个“具体事实事件”，用于生成一张合并通知卡片。

返回严格 JSON（不要 markdown 代码块）:
{
  "stories": [
    {
      "title": "具体事件标题",
      "summary": "这组推文共同讨论的事实，一句话",
      "confidence": "high 或 medium 或 low",
      "post_ids": ["101", "102"],
      "reason": "为什么这些 post_ids 属于同一事实事件"
    }
  ]
}

规则:
- 只有在推文讨论同一件具体事实时才合并，例如同一家公司发布的同一条并购、财报、监管、
  政策、军事或宏观事件。
- 不要因为属于同一大类、同一行业、同一公司、同一资产、同一天、同一个博主或都提到热门
  词就合并。
- 并购 A 收购 B、Yum 出售 Pizza Hut、英国国防预算压力、SpaceX 估值变化，这些是不同事件。
- 如果只是相似主题但事实不同，必须拆成不同 stories。
- 如果不确定，拆开，并把 confidence 设为 low。
- 每个输入 post_id 必须且只能出现在一个 story 中。
- 只有非常明确的同一事实事件才使用 confidence=high。"""


@dataclass
class TweetAnalysis:
    summary: str = ""
    translation: str | None = None
    mentioned_tickers: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    canonical_event_id: str = ""
    canonical_event_title: str = ""
    canonical_event_summary: str = ""
    sentiment: str = "neutral"
    urgency: str = "low"
    urgency_reason: str = ""
    is_market_relevant: bool = False
    is_noteworthy: bool = False
    attention_reason: str = ""


@dataclass
class TweetStoryCluster:
    title: str = ""
    summary: str = ""
    confidence: str = "low"
    post_ids: list[str] = field(default_factory=list)
    reason: str = ""


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
                logger.exception(
                    "Tweet analysis disabled: LLM authentication failed. "
                    "Check DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN for the lightweight "
                    "tweet analyzer."
                )
            else:
                logger.exception("Tweet analysis failed")
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

    async def cluster_stories(self, posts: list[dict[str, Any]]) -> list[TweetStoryCluster] | None:
        if not self._llm or self._auth_failed or not posts:
            return None
        try:
            raw = await self._llm.chat(
                [
                    {"role": "system", "content": _STORY_CLUSTER_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(posts, ensure_ascii=False)},
                ],
                temperature=0.1,
                max_tokens=2500,
            )
            return _parse_story_clusters(raw, [str(post.get("id") or "") for post in posts])
        except Exception as e:
            if _is_auth_error(e):
                self._auth_failed = True
                logger.exception(
                    "Tweet story clustering disabled: LLM authentication failed. "
                    "Check DEEPSEEK_API_KEY or ANTHROPIC_AUTH_TOKEN for the lightweight "
                    "tweet analyzer."
                )
            else:
                logger.exception("Tweet story clustering failed")
            return None


def _parse_analysis(raw: str) -> TweetAnalysis:
    cleaned = _strip_json_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.exception("Tweet analysis JSON parse failed; using raw summary fallback")
        return TweetAnalysis(summary=raw[:300])

    tickers = data.get("mentioned_tickers") or []
    normalized_tickers = [t.upper().strip() for t in tickers if isinstance(t, str) and t.strip()]
    canonical_event = data.get("canonical_event")
    if not isinstance(canonical_event, dict):
        canonical_event = {}

    return TweetAnalysis(
        summary=str(data.get("summary") or ""),
        translation=data.get("translation"),
        mentioned_tickers=normalized_tickers,
        topics=data.get("topics") or [],
        canonical_event_id=_normalize_canonical_event_id(canonical_event.get("id")),
        canonical_event_title=str(canonical_event.get("title") or "").strip(),
        canonical_event_summary=str(canonical_event.get("summary") or "").strip(),
        sentiment=str(data.get("sentiment") or "neutral"),
        urgency=str(data.get("urgency") or "low"),
        urgency_reason=str(data.get("urgency_reason") or ""),
        is_market_relevant=_parse_market_relevance(data, normalized_tickers),
        is_noteworthy=_parse_bool(data.get("is_noteworthy")),
        attention_reason=str(data.get("attention_reason") or ""),
    )


def _parse_story_clusters(
    raw: str,
    ordered_allowed_ids: list[str],
) -> list[TweetStoryCluster] | None:
    allowed_ids = [post_id for post_id in ordered_allowed_ids if post_id]
    allowed_set = set(allowed_ids)
    if not allowed_ids:
        return []

    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        logger.exception("Tweet story cluster JSON parse failed")
        return None

    stories = data.get("stories")
    if not isinstance(stories, list):
        return None

    used: set[str] = set()
    clusters: list[TweetStoryCluster] = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        post_ids: list[str] = []
        for raw_id in story.get("post_ids") or []:
            post_id = str(raw_id or "").strip()
            if post_id in allowed_set and post_id not in used:
                post_ids.append(post_id)
                used.add(post_id)
        if not post_ids:
            continue
        clusters.append(
            TweetStoryCluster(
                title=str(story.get("title") or "").strip(),
                summary=str(story.get("summary") or "").strip(),
                confidence=str(story.get("confidence") or "low").strip().lower(),
                post_ids=post_ids,
                reason=str(story.get("reason") or "").strip(),
            )
        )

    for post_id in allowed_ids:
        if post_id not in used:
            clusters.append(TweetStoryCluster(confidence="low", post_ids=[post_id]))

    return clusters


def _strip_json_fence(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines)
    return cleaned.strip()


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "是"}
    return bool(value)


def _parse_market_relevance(data: dict, normalized_tickers: list[str]) -> bool:
    if "is_market_relevant" in data:
        return _parse_bool(data.get("is_market_relevant"))
    return bool(data.get("is_noteworthy") or normalized_tickers)


def _normalize_canonical_event_id(value) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = "".join(ch if ch.isalnum() else "-" for ch in raw)
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:96]


def _is_auth_error(error: Exception) -> bool:
    text = str(error).lower()
    return "401" in text or "authentication" in text or "api key" in text
