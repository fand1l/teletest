"""
Semantic text chunking for RAG.

Splits large texts into token-bounded chunks along sentence boundaries,
with a configurable token overlap between adjacent chunks so that facts
sitting on a chunk border are never lost to both chunks.
"""
import re
from typing import List, Optional

from src.config import settings
from src.core.llm.budget_manager import budget_manager

# Sentence boundary: punctuation followed by whitespace. Keeps abbreviations
# imperfectly, but for news-style Telegram text this is a good tradeoff over
# pulling in a full NLP sentence tokenizer (nltk punkt requires downloads).
_SENTENCE_RE = re.compile(r'(?<=[.!?…])\s+')


def split_sentences(text: str) -> List[str]:
    """Splits text into sentences, treating paragraph breaks as hard boundaries."""
    sentences: List[str] = []
    for paragraph in text.split('\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences.extend(s.strip() for s in _SENTENCE_RE.split(paragraph) if s.strip())
    return sentences


def _hard_split(sentence: str, max_tokens: int) -> List[str]:
    """Token-level split for a single sentence that exceeds the chunk size."""
    encoder = budget_manager.encoder
    token_ids = encoder.encode(sentence)
    return [
        encoder.decode(token_ids[i:i + max_tokens])
        for i in range(0, len(token_ids), max_tokens)
    ]


def chunk_text(
    text: str,
    max_tokens: Optional[int] = None,
    overlap_tokens: Optional[int] = None,
) -> List[str]:
    """
    Splits text into chunks of at most `max_tokens` tokens along sentence
    boundaries, where each chunk starts with the last `overlap_tokens` worth
    of sentences from the previous chunk.
    """
    max_tokens = max_tokens or settings.RAG_CHUNK_TOKENS
    overlap_tokens = overlap_tokens if overlap_tokens is not None else settings.RAG_CHUNK_OVERLAP_TOKENS
    if overlap_tokens >= max_tokens:
        overlap_tokens = max_tokens // 4

    if not text or not text.strip():
        return []

    # Fast path: the whole text fits in one chunk.
    if budget_manager.estimate_tokens(text) <= max_tokens:
        return [text.strip()]

    sentences: List[str] = []
    for sentence in split_sentences(text):
        if budget_manager.estimate_tokens(sentence) > max_tokens:
            sentences.extend(_hard_split(sentence, max_tokens))
        else:
            sentences.append(sentence)

    chunks: List[str] = []
    current: List[str] = []
    current_tokens = 0

    for sentence in sentences:
        tokens = budget_manager.estimate_tokens(sentence)
        if current and current_tokens + tokens > max_tokens:
            chunks.append(' '.join(current))
            # Seed the next chunk with trailing sentences as overlap
            overlap: List[str] = []
            overlap_used = 0
            for prev in reversed(current):
                prev_tokens = budget_manager.estimate_tokens(prev)
                if overlap_used + prev_tokens > overlap_tokens:
                    break
                overlap.insert(0, prev)
                overlap_used += prev_tokens
            current = overlap
            current_tokens = overlap_used
        current.append(sentence)
        current_tokens += tokens

    if current:
        chunks.append(' '.join(current))

    return chunks
