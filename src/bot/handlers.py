from aiogram import types, F
from aiogram.filters import Command
from src.bot.main import dp
from src.database.session import AsyncSessionLocal
from src.database.repositories.events import event_repo
from src.database.repositories.messages import message_repo
from src.pipeline.nlp import generate_embedding
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Welcome to the Telegram Intelligence Aggregator.\n\n"
        "Commands:\n"
        "/find &lt;query&gt; - Search across events and messages using semantic search.\n"
        "/summary - Generate a summary of current active events.\n"
        "/clear_db - Clear the entire database (messages, events).\n"
        "/fetch_history - Load the last 10 messages from each channel chronologically."
    )

@dp.message(Command("clear_db"))
async def cmd_clear_db(message: types.Message):
    logger.info("Received /clear_db command from user %s", message.from_user.id)
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE TABLE messages, events CASCADE;"))
            await session.commit()
            logger.info("Database truncated successfully.")
            await message.answer("✅ Базу даних успішно очищено! Всі старі повідомлення та події видалено.")
    except Exception as e:
        logger.error(f"Failed to clear db: {e}", exc_info=True)
        await message.answer("❌ Виникла помилка під час очищення бази даних.")

@dp.message(Command("fetch_history"))
async def cmd_fetch_history(message: types.Message):
    args = message.text.split()
    limit = 10
    if len(args) > 1 and args[1].isdigit():
        limit = int(args[1])
        
    await message.answer(f"Починаю завантаження останніх {limit} повідомлень з кожного каналу... Зачекайте.")
    try:
        import asyncio
        from src.core.channels_config import get_all_monitored_channels
        from src.collector.client import client
        from src.database.models.messages import Message
        from src.database.models.events import Event, EventStatus, UpdateType
        from src.database.repositories.channels import channel_repo
        from datetime import timezone
        from telethon.errors import FloodWaitError
        
        channel_ids = await get_all_monitored_channels()
        all_messages = []
        
        for chat_id in channel_ids:
            while True:
                try:
                    # telethon iter_messages
                    async for msg in client.iter_messages(chat_id, limit=limit):
                        if msg.text and msg.date:
                            all_messages.append({'chat_id': chat_id, 'msg': msg})
                    break  # Success, move to the next chat
                except FloodWaitError as e:
                    logger.warning(f"Flood wait required: {e.seconds} seconds.")
                    await message.answer(f"⚠️ Телеграм обмежив частоту запитів. Чекаю {e.seconds} секунд для продовження...")
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.error(f"Failed to fetch from {chat_id}: {e}")
                    break  # Skip this chat and move to the next on other errors
                
        # Sort chronologically (oldest to newest)
        all_messages.sort(key=lambda x: x['msg'].date)
        
        async with AsyncSessionLocal() as session:
            count = 0
            for item in all_messages:
                chat_id = item['chat_id']
                telethon_msg = item['msg']
                
                chat = await telethon_msg.get_chat()
                chat_title = getattr(chat, 'title', 'Unknown')
                
                await channel_repo.get_or_create(session, channel_id=chat_id, name=chat_title)
                
                sender = await telethon_msg.get_sender()
                sender_name = getattr(sender, 'username', getattr(sender, 'first_name', 'Unknown')) if sender else None
                
                db_msg = await message_repo.create_and_commit(session, obj_in={
                    "telegram_msg_id": telethon_msg.id,
                    "channel_id": chat_id,
                    "timestamp": telethon_msg.date.astimezone(timezone.utc),
                    "sender": sender_name,
                    "raw_text": telethon_msg.text,
                    "embedding": None
                })
                
                title = f"Історія: {chat_title}"
                current_summary = telethon_msg.text or ""
                
                new_event = event_repo.create(session, obj_in={
                    "title": title,
                    "current_summary": current_summary,
                    "status": EventStatus.NEW
                })
                new_event.updated_at = db_msg.timestamp
                new_event.created_at = db_msg.timestamp
                await session.flush()
                
                await event_repo.add_update(
                    session,
                    event_id=new_event.id,
                    message_id=db_msg.id,
                    update_type=UpdateType.NEW_DETAIL
                )
                await session.commit()
                count += 1
                
        await message.answer(f"✅ Успішно завантажено та збережено {count} повідомлень зі збереженням хронології!")
        
    except Exception as e:
        logger.error(f"Fetch history failed: {e}", exc_info=True)
        await message.answer("❌ Виникла помилка під час завантаження історії.")

@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    query = message.text.replace("/find", "").strip()
    if not query:
        await message.answer("Please provide a search query. Example: /find Kharkiv")
        return
        
    await message.answer(f"Searching for: {query}...")
    
    try:
        # Generate embedding for the search query
        query_embedding = generate_embedding(query)
        
        async with AsyncSessionLocal() as session:
            # Semantic search across recent active messages
            # For a full search, we would search across the archive as well, 
            # but we use the same repository method for now.
            results = await message_repo.find_similar(
                session, 
                embedding=query_embedding, 
                limit=5, 
                threshold=0.7 # lower threshold for search
            )
            
            if not results:
                await message.answer("No relevant reports found.")
                return
                
            response = "<b>Top Search Results:</b>\n\n"
            for idx, msg in enumerate(results, 1):
                text = (msg.raw_text or msg.extracted_text or "Media only")[:200] + "..."
                channel = await msg.awaitable_attrs.channel
                response += f"{idx}. <b>{channel.name}</b>: {text}\n\n"
                
            await message.answer(response)
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        await message.answer("An error occurred during the search.")

@dp.message(Command("summary"))
async def cmd_summary(message: types.Message):
    logger.info("Received /summary command from user %s", message.from_user.id)
    
    try:
        from src.reasoning.summarization.hierarchical import run_hierarchical_summarization, estimate_summarization_time
        
        # 1. Estimate time
        logger.info("Estimating summarization time...")
        estimated_time = await estimate_summarization_time()
        
        # 2. Inform user
        status_msg = await message.answer(f"⏳ Генерую загальне зведення новин...\nОрієнтовний час очікування: <b>{estimated_time}</b>.", parse_mode="HTML")
        
        async def update_progress(text: str):
            try:
                # Add the estimated time to the progress text so it's always visible
                full_text = f"⏳ Орієнтовний час очікування: <b>{estimated_time}</b>\n\n{text}"
                await status_msg.edit_text(full_text, parse_mode="HTML")
            except Exception:
                pass # Ignore "message is not modified" exceptions from Telegram
        
        # 3. Run hierarchical summarization
        logger.info("Running hierarchical summarization pipeline...")
        global_summary_html = await run_hierarchical_summarization(progress_callback=update_progress)
        
        # 4. Send the result
        logger.info("Sending global summary to user.")
        await status_msg.delete()
        await message.answer(global_summary_html, disable_web_page_preview=True)
            
    except Exception as e:
        logger.error(f"Summary failed with error: {e}", exc_info=True)
        if "429" in str(e) or "Rate limited" in str(e) or "quota" in str(e).lower():
            await message.answer("❌ Перевищено ліміт запитів до ШІ (Rate Limit). Будь ласка, зачекайте кілька хвилин та спробуйте ще раз.")
        else:
            await message.answer(f"❌ Виникла помилка під час генерації зведення.")
