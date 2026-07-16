import logging
from telethon import TelegramClient, events
from src.config import settings
from src.core.queues import message_queue
from datetime import timezone

logger = logging.getLogger(__name__)

# Initialize the Telethon client
client = TelegramClient(
    'telegram_aggregator_session',
    settings.TELEGRAM_API_ID,
    settings.TELEGRAM_API_HASH
)

@client.on(events.NewMessage())
async def new_message_handler(event: events.NewMessage.Event):
    """
    Listens for new incoming messages across all dialogs.
    Parses the essential information and pushes it to the internal asyncio.Queue.
    """
    message = event.message
    
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', 'Unknown')
    
    print(f"\033[93m[TELEGRAM] Отримано подію від: {chat_title} (ID: {chat.id})\033[0m")
    
    # We only care about broadcast channels (not groups or megagroups)
    is_broadcast = getattr(chat, 'broadcast', False)
    if not is_broadcast:
        return

    chat_id = chat.id
    username = getattr(chat, 'username', None)
    
    # Check if the channel is monitored via the configuration file
    from src.core.channels_config import is_channel_monitored
    if not await is_channel_monitored(chat_id, chat_title, username):
        return
        
    print(f"\033[92m[TELEGRAM] ✅ Повідомлення від {chat_title} пройшло фільтри і додається в чергу!\033[0m")
    
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

    # Create the raw payload
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
        # We store the actual telethon message object temporarily in case the pipeline needs to download the media
        '_telethon_message': message 
    }
    
    # Push to queue for the AI pipeline to process
    await message_queue.put(payload)
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
    from src.core.channels_config import sync_all_channels
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
