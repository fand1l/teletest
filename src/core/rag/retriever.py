"""
Semantic retrieval for RAG.

Instead of feeding the entire message history to the LLM (expensive, slow,
and eventually impossible), we embed the user's question, pull only the
top-K most relevant messages via pgvector, and greedily pack them into a
strict token budget (settings.RAG_CONTEXT_TOKENS, ~15-20k tokens).
"""
import asyncio
import logging
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.llm.budget_manager import budget_manager
from src.core.rag.chunker import chunk_text
from src.database.models.messages import Message
from src.database.repositories.messages import message_repo
from src.pipeline.nlp import generate_query_embedding

logger = logging.getLogger(__name__)

# A single message is capped at this many tokens inside the context block,
# so one giant forwarded longread can't crowd out every other source.
_MAX_TOKENS_PER_MESSAGE = 1500


async def retrieve_context(
    session: AsyncSession,
    query: str,
    max_context_tokens: Optional[int] = None,
    candidate_limit: int = 60,
    threshold: float = 0.55,
    within_hours: Optional[int] = None,
) -> Tuple[List[Tuple[Message, str]], int]:
    """
    Returns ([(message, text_to_include)], tokens_used) — the most relevant
    messages for the query, packed into the token budget in relevance order.
    """
    max_context_tokens = max_context_tokens or settings.RAG_CONTEXT_TOKENS

    # Model inference is CPU-bound; keep it off the event loop.
    embedding = await asyncio.to_thread(generate_query_embedding, query)

    candidates = await message_repo.find_similar(
        session,
        embedding=embedding,
        limit=candidate_limit,
        threshold=threshold,
        within_hours=within_hours,
    )

    packed: List[Tuple[Message, str]] = []
    used_tokens = 0

    for msg in candidates:
        text = (msg.raw_text or msg.extracted_text or '').strip()
        if not text:
            continue

        tokens = budget_manager.estimate_tokens(text)
        if tokens > _MAX_TOKENS_PER_MESSAGE:
            # Keep only the first (most informative) chunk of oversized texts
            text = chunk_text(text, max_tokens=_MAX_TOKENS_PER_MESSAGE)[0]
            tokens = budget_manager.estimate_tokens(text)

        if used_tokens + tokens > max_context_tokens:
            break

        packed.append((msg, text))
        used_tokens += tokens

    logger.info(
        "RAG retrieval: %d/%d candidates packed into %d tokens (budget %d)",
        len(packed), len(candidates), used_tokens, max_context_tokens
    )
    return packed, used_tokens


async def build_context_block(packed: List[Tuple[Message, str]]) -> str:
    """
    Formats retrieved messages into a numbered context block for the LLM,
    with source channel and timestamp for citation.
    """
    lines = []
    for idx, (msg, text) in enumerate(packed, 1):
        channel = await msg.awaitable_attrs.channel
        channel_name = channel.name if channel else str(msg.channel_id)
        ts = msg.timestamp.strftime('%Y-%m-%d %H:%M UTC') if msg.timestamp else 'unknown time'
        lines.append(f"[{idx}] ({channel_name}, {ts})\n{text}")
    return "\n\n".join(lines)
