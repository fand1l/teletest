import asyncio
import logging
from typing import Dict, Any

from src.config import settings

logger = logging.getLogger(__name__)

# We use a central module for the queue so that both the collector (producer)
# and the pipeline (consumer) can easily import and share the same queue instance.
# Each item in the queue should be a dictionary representing the raw message data.
#
# The queue is bounded: if the pipeline falls behind during a message burst,
# the producer drops the OLDEST queued item instead of growing memory forever
# (for a news aggregator, fresh messages are worth more than stale ones).

message_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(
    maxsize=settings.MESSAGE_QUEUE_MAX_SIZE
)


def enqueue_message(payload: Dict[str, Any]) -> None:
    """
    Non-blocking enqueue with drop-oldest overflow policy.
    Never raises on a full queue, so the Telethon event handler can't pile up.
    """
    try:
        message_queue.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            dropped = message_queue.get_nowait()
            message_queue.task_done()
            logger.warning(
                "Ingest queue full (%d items). Dropped oldest message %s from channel %s.",
                message_queue.maxsize,
                dropped.get('telegram_msg_id'),
                dropped.get('channel_name'),
            )
        except asyncio.QueueEmpty:
            pass
        try:
            message_queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.error("Ingest queue still full after dropping oldest; message lost.")
