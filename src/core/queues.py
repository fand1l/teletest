import asyncio
from typing import Dict, Any

# We use a central module for the queue so that both the collector (producer)
# and the pipeline (consumer) can easily import and share the same queue instance.
# Each item in the queue should be a dictionary representing the raw message data.

message_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
