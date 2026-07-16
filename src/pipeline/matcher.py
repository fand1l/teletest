import logging
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from src.database.models.messages import Message
from src.database.repositories.messages import message_repo
from src.database.repositories.events import event_repo
from src.database.models.events import Event, EventStatus, UpdateType
from src.reasoning.intelligence import evaluate_event_match
from src.config import settings

logger = logging.getLogger(__name__)


def _title_from_text(text: str, max_len: int = 80) -> str:
    """
    Derives a cheap event title from the first line of the message.
    A clean LLM title/summary is produced later by the hierarchical
    summarization stage, so we don't burn an LLM call per new event here.
    """
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return "Нова подія"
    if len(first_line) > max_len:
        first_line = first_line[:max_len].rsplit(' ', 1)[0] + "…"
    return first_line


async def _create_new_event(session: AsyncSession, new_message: Message, title: Optional[str] = None) -> Event:
    raw_text = new_message.raw_text or ""
    new_event = event_repo.create(session, obj_in={
        "title": title or _title_from_text(raw_text),
        "current_summary": raw_text,
        "status": EventStatus.NEW
    })
    await session.flush()
    await event_repo.add_update(
        session,
        event_id=new_event.id,
        message_id=new_message.id,
        update_type=UpdateType.NEW_DETAIL
    )
    await session.commit()
    return new_event


async def process_event_matching(session: AsyncSession, new_message: Message, entities: List[Dict[str, Any]]):
    """
    Matches a new message against recent messages using vector similarity.
    Groups them into events. The LLM is consulted ONLY in the ambiguous band:
      - no candidates above SIMILARITY_THRESHOLD  -> new event, no LLM call
      - similarity >= AUTO_MERGE_SIMILARITY       -> merge as corroboration, no LLM call
      - in between                                -> LLM verifies match / contradictions
    """
    # 1. Find semantically similar recent messages (with similarity scores)
    scored = await message_repo.find_similar_with_scores(
        session,
        embedding=new_message.embedding,
        limit=5,
        threshold=settings.SIMILARITY_THRESHOLD,
        within_hours=settings.ACTIVE_RETENTION_HOURS
    )

    # Remove self if returned
    scored = [(m, s) for m, s in scored if m.id != new_message.id]

    if not scored:
        logger.info(f"No similar events found. Creating new event for message {new_message.id}")
        await _create_new_event(session, new_message)
        return

    most_similar, similarity = scored[0]

    # Find the event this message belongs to
    event_updates = await most_similar.awaitable_attrs.event_updates
    if not event_updates:
        logger.warning(f"Similar message {most_similar.id} has no event. This shouldn't happen.")
        await _create_new_event(session, new_message)
        return

    target_event = await event_updates[0].awaitable_attrs.event

    # 2a. Near-duplicate: merge without spending an LLM call. Reposts and
    # forwarded copies of the same report dominate Telegram news traffic,
    # so this branch eliminates most reasoning calls.
    if similarity >= settings.AUTO_MERGE_SIMILARITY:
        await event_repo.add_update(
            session,
            event_id=target_event.id,
            message_id=new_message.id,
            update_type=UpdateType.CORROBORATION
        )
        logger.info(
            f"Auto-merged message {new_message.id} into Event {target_event.id} "
            f"(similarity {similarity:.3f} >= {settings.AUTO_MERGE_SIMILARITY}, no LLM call)"
        )
        return

    logger.info(
        f"Message {new_message.id} is similar to Event {target_event.id} "
        f"(similarity {similarity:.3f}). Triggering LLM analysis."
    )

    # 2b. Ambiguous band: use the LLM to verify the match and check for contradictions
    try:
        analysis = await evaluate_event_match(
            existing_summary=target_event.current_summary,
            new_message=new_message.raw_text
        )

        if analysis.is_same_event:
            update_type = UpdateType.CONTRADICTION if analysis.has_contradiction else UpdateType.CORROBORATION

            # Update the event's summary
            target_event.current_summary = analysis.merged_summary

            await event_repo.add_update(
                session,
                event_id=target_event.id,
                message_id=new_message.id,
                update_type=update_type,
                description=analysis.contradiction_description
            )

            logger.info(f"Merged message {new_message.id} into Event {target_event.id}. Contradiction: {analysis.has_contradiction}")
        else:
            # LLM decided they are actually different events despite semantic similarity
            logger.info("LLM rejected semantic match. Creating new event.")
            await _create_new_event(session, new_message)

    except Exception as e:
        logger.error(f"LLM reasoning failed: {e}", exc_info=True)
        # Fallback: create a new event for the message so it is not lost
        await _create_new_event(session, new_message)
