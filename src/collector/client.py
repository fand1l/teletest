import logging
from telethon import TelegramClient, events
from src.config import settings
from src.core.queues import enqueue_message
from src.core.channels_config import is_channel_monitored, sync_all_channels
from datetime import timezone

logger = logging.getLogger(__name__)

# Initialize the Telethon client.
# flood_sleep_threshold makes Telethon transparently sleep-and-retry on
# FloodWaitError up to N seconds instead of raising, which keeps the
# collector alive through Telegram rate limits.
client = TelegramClient(
    'telegram_aggregator_session',
    settings.TELEGRAM_API_ID,
    settings.TELEGRAM_API_HASH,
    flood_sleep_threshold=settings.TELETHON_FLOOD_SLEEP_THRESHOLD
)

@client.on(events.NewMessage())
async def new_message_handler(event: events.NewMessage.Event):
    """
    Listens for new incoming messages across all dialogs.
    Parses the essential information and pushes it to the internal queue.
    """
    # Fast pre-filter: we only care about broadcast channels (not private
    # chats, groups or megagroups). This avoids any extra work for the
    # overwhelming majority of irrelevant updates.
    if not event.is_channel or event.is_group:
        return

    message = event.message

    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', 'Unknown')

    if not getattr(chat, 'broadcast', False):
        return

    chat_id = chat.id
    username = getattr(chat, 'username', None)

    # Check if the channel is monitored (cached config, no disk I/O per message)
    if not await is_channel_monitored(chat_id, chat_title, username):
        return

    logger.info("Accepted message %s from channel '%s' (ID: %s)", message.id, chat_title, chat_id)

    sender = await event.get_sender()
    sender_name = getattr(sender, 'username', getattr(sender, 'first_name', 'Unknown')) if sender else None

    # Handle media
    media_type = None
    if message.photo:
        media_type = 'photo'
    elif message.video:
        media_type = 'video'
    elif message.document:
        media_type = 'document'

    forwarded_from = None
    if message.fwd_from:
        if message.fwd_from.from_id:
            forwarded_from = str(message.fwd_from.from_id)
        elif message.fwd_from.from_name:
            forwarded_from = message.fwd_from.from_name

    # Create the raw payload. We deliberately do NOT keep a reference to the
    # Telethon message object: it pins memory while items sit in the queue.
    # If media processing is added later, re-fetch by (chat_id, message.id).
    payload = {
        'telegram_msg_id': message.id,
        'channel_id': chat_id,
        'channel_name': chat_title,
        'timestamp': message.date.astimezone(timezone.utc) if message.date else None,
        'sender': sender_name,
        'forwarded_from': forwarded_from,
        'raw_text': message.text,
        'media_type': media_type,
        'has_media': message.media is not None,
    }

    # Push to queue for the AI pipeline to process (non-blocking, drop-oldest)
    enqueue_message(payload)
    logger.debug(f"Queued new message from {chat_title} (ID: {message.id})")

async def start_collector():
    """
    Starts the Telethon client. Requires the user to be logged in.
    The first time this runs, it will require terminal interaction to input the phone code.
    """
    logger.info("Starting Telegram Collector...")
    await client.start(phone=settings.TELEGRAM_PHONE)

    # Sync all currently subscribed channels
    logger.info("Syncing subscribed channels with configuration...")
    channels_to_sync = []
    async for dialog in client.iter_dialogs():
        if dialog.is_channel and getattr(dialog.entity, 'broadcast', False):
            channels_to_sync.append({
                'id': dialog.entity.id,
                'name': dialog.name,
                'username': getattr(dialog.entity, 'username', None)
            })

    await sync_all_channels(channels_to_sync)

    logger.info("Telegram Collector is running and listening for new messages.")

    # We don't use client.run_until_disconnected() here because we want to yield
    # back to the main asyncio event loop (which will run other services alongside this).
    # The client is now connected and listening via the registered event handlers.
