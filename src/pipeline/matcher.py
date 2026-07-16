import logging
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any
from src.database.models.messages import Message
from src.database.repositories.messages import message_repo
from src.database.repositories.events import event_repo
from src.database.models.events import Event, EventStatus, UpdateType
from src.reasoning.intelligence import evaluate_event_match, summarize_new_event
from src.config import settings

logger = logging.getLogger(__name__)

async def _create_new_event(session: AsyncSession, new_message: Message, fallback_title: str) -> Event:
    try:
        summary_result = await summarize_new_event(new_message.raw_text or "")
        title = summary_result.title
        current_summary = summary_result.summary
    except Exception as e:
        logger.error(f"Failed to generate initial summary: {e}")
        title = fallback_title
        current_summary = new_message.raw_text or ""
        
    new_event = event_repo.create(session, obj_in={
        "title": title,
        "current_summary": current_summary,
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
    Groups them into events, calling Gemini if there is potential contradiction.
    """
    # 1. Find semantically similar recent messages
    similar_messages = await message_repo.find_similar(
        session, 
        embedding=new_message.embedding,
        limit=5,
        threshold=settings.SIMILARITY_THRESHOLD
    )
    
    # Remove self if returned
    similar_messages = [m for m in similar_messages if m.id != new_message.id]
    
    if not similar_messages:
        # Create a new event
        logger.info(f"No similar events found. Creating new event for message {new_message.id}")
        await _create_new_event(session, new_message, f"Нова Подія (Канал {new_message.channel_id})")
        return

    # For simplicity, we compare against the most similar message's event
    most_similar = similar_messages[0]
    
    # Find the event this message belongs to
    event_updates = await most_similar.awaitable_attrs.event_updates
    if not event_updates:
        # Edge case: similar message exists but has no event. Let's create one.
        logger.warning(f"Similar message {most_similar.id} has no event. This shouldn't happen.")
        await _create_new_event(session, new_message, f"Нова Подія (Канал {new_message.channel_id})")
        return
        
    target_event = await event_updates[0].awaitable_attrs.event
    
    logger.info(f"Message {new_message.id} is similar to Event {target_event.id}. Triggering Gemini analysis.")
    
    # 2. Use Gemini to verify match and check for contradictions
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
            # Gemini decided they are actually different events despite semantic similarity
            logger.info("Gemini rejected semantic match. Creating new event.")
            await _create_new_event(session, new_message, "Нова Подія (Відхилений збіг)")
            
    except Exception as e:
        logger.error(f"Gemini reasoning failed: {e}", exc_info=True)
        # Fallback: create a new event for the message so it is not lost
        await _create_new_event(session, new_message, "Нова Подія (Резерв)")
