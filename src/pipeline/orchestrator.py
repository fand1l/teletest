import asyncio
import logging
from typing import Dict, Any
from src.core.queues import message_queue
from src.database.session import AsyncSessionLocal
from src.database.repositories.channels import channel_repo
from src.database.repositories.messages import message_repo
from src.database.repositories.entities import entity_repo
from .filters import is_spam_or_ad
from .nlp import generate_embedding, extract_entities
from .matcher import process_event_matching

logger = logging.getLogger(__name__)

async def process_message(payload: Dict[str, Any]) -> str:
    """
    The main AI pipeline. Passes a single message through all processing stages.
    Returns a status string: 'ok', 'spam' or 'empty' (useful for batch callers
    like /fetch_history that report statistics).
    """
    raw_text = payload.get('raw_text', '')

    # 1. Spam & Ad Filtering
    if is_spam_or_ad(raw_text):
        logger.debug(f"Filtered out spam/ad message {payload['telegram_msg_id']}")
        return 'spam'

    # TODO: 2. Media Processing (OCR / Florence-2)
    # If payload['has_media'] is True, we would normally download the media using
    # the telethon client and run Florence-2 here.
    extracted_text = None

    combined_text = raw_text
    if extracted_text:
        combined_text = f"{raw_text}\n{extracted_text}"

    if not combined_text or not combined_text.strip():
        return 'empty' # Nothing to process
        
    # 3. NLP Pipeline (Embeddings & Entities)
    # This should preferably run in a threadpool or separate process to not block asyncio
    embedding = await asyncio.to_thread(generate_embedding, combined_text)
    entities = await asyncio.to_thread(extract_entities, combined_text)
    
    # 4. Database Persistence (Raw Message)
    async with AsyncSessionLocal() as session:
        # Ensure channel exists
        await channel_repo.get_or_create(
            session, 
            channel_id=payload['channel_id'], 
            name=payload['channel_name']
        )
        
        # Save message
        message = await message_repo.create_and_commit(session, obj_in={
            "telegram_msg_id": payload['telegram_msg_id'],
            "channel_id": payload['channel_id'],
            "timestamp": payload['timestamp'],
            "sender": payload['sender'],
            "forwarded_from": payload['forwarded_from'],
            "raw_text": raw_text,
            "extracted_text": extracted_text,
            "media_type": payload['media_type'],
            "embedding": embedding
        })

        # 5. Persist extracted entities (previously computed and discarded)
        try:
            linked = await entity_repo.save_message_entities(session, message.id, entities)
            if linked:
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to save entities for message {message.id}: {e}")
            await session.rollback()

        # 6. Event Matching & Deduplication
        await process_event_matching(session, message, entities)

    return 'ok'

async def pipeline_worker():
    """
    Continuously consumes messages from the queue and processes them.
    """
    logger.info("Starting AI Pipeline Worker...")
    while True:
        try:
            payload = await message_queue.get()
            await process_message(payload)
            message_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
