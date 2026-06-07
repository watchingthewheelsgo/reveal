"""Feishu card Markdown rendering helpers."""

CARD_MARKDOWN_CHUNK_SIZE = 2800


def markdown_to_card_elements(text: str) -> list[dict]:
    """Render Markdown as Feishu Card JSON 2.0 markdown components."""
    return [
        {"tag": "markdown", "content": chunk}
        for chunk in _split_markdown(text, chunk_size=CARD_MARKDOWN_CHUNK_SIZE)
    ]


def _split_markdown(text: str, chunk_size: int = 2800) -> list[str]:
    normalized = text.strip() or "（无内容）"
    chunks: list[str] = []
    current = ""
    for paragraph in normalized.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(paragraph) > chunk_size:
            chunks.append(paragraph[:chunk_size])
            paragraph = paragraph[chunk_size:]
        current = paragraph
    if current:
        chunks.append(current)
    return chunks
