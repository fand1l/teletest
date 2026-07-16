import asyncio
import logging
from html import escape

from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import text

from src.bot.main import dp
from src.database.session import AsyncSessionLocal
from src.database.repositories.events import event_repo
from src.database.repositories.messages import message_repo
from src.pipeline.nlp import generate_query_embedding

logger = logging.getLogger(__name__)


def _esc(value) -> str:
    """Escapes untrusted text for Telegram HTML parse mode."""
    return escape(str(value or ""), quote=False)


def _snippet(text_value: str, limit: int = 200) -> str:
    text_value = (text_value or "").strip().replace('\n', ' ')
    if len(text_value) > limit:
        text_value = text_value[:limit].rsplit(' ', 1)[0] + "…"
    return text_value


# ---------------------------------------------------------------- /start ---

WELCOME_TEXT = (
    "🛰 <b>Telegram Intelligence Aggregator</b>\n"
    "<i>Персональна система збору та аналізу новин</i>\n"
    "\n"
    "📌 <b>Доступні команди:</b>\n"
    "\n"
    "🔎 /find <code>запит</code>\n"
    "<blockquote>Семантичний пошук по всіх зібраних повідомленнях</blockquote>\n"
    "🧠 /ask <code>питання</code>\n"
    "<blockquote>Відповідь ШІ на основі найрелевантніших повідомлень бази (RAG)</blockquote>\n"
    "📰 /summary\n"
    "<blockquote>Тематичне зведення (SITREP) за активними подіями</blockquote>\n"
    "📥 /fetch_history <code>[N]</code>\n"
    "<blockquote>Завантажити останні N повідомлень з кожного каналу (типово 10)</blockquote>\n"
    "🗑 /clear_db\n"
    "<blockquote>Повне очищення бази (з підтвердженням)</blockquote>"
)


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(WELCOME_TEXT)


# ------------------------------------------------------------- /clear_db ---

_CONFIRM_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="🗑 Так, очистити", callback_data="clear_db:confirm"),
    InlineKeyboardButton(text="✖️ Скасувати", callback_data="clear_db:cancel"),
]])


@dp.message(Command("clear_db"))
async def cmd_clear_db(message: types.Message):
    await message.answer(
        "⚠️ <b>Очищення бази даних</b>\n\n"
        "Будуть <u>безповоротно</u> видалені всі повідомлення та події.\n"
        "Підтвердіть дію:",
        reply_markup=_CONFIRM_KB
    )


@dp.callback_query(F.data == "clear_db:cancel")
async def cb_clear_db_cancel(callback: types.CallbackQuery):
    await callback.message.edit_text("✖️ Очищення скасовано. Дані не змінено.")
    await callback.answer()


@dp.callback_query(F.data == "clear_db:confirm")
async def cb_clear_db_confirm(callback: types.CallbackQuery):
    logger.info("Confirmed /clear_db from user %s", callback.from_user.id)
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("TRUNCATE TABLE messages, events CASCADE;"))
            await session.commit()
        logger.info("Database truncated successfully.")
        await callback.message.edit_text("✅ <b>Базу даних очищено.</b>\nВсі повідомлення та події видалено.")
    except Exception as e:
        logger.error(f"Failed to clear db: {e}", exc_info=True)
        await callback.message.edit_text("❌ Помилка під час очищення бази даних. Деталі в логах.")
    await callback.answer()


# -------------------------------------------------------- /fetch_history ---

@dp.message(Command("fetch_history"))
async def cmd_fetch_history(message: types.Message):
    args = message.text.split()
    limit = 10
    if len(args) > 1 and args[1].isdigit():
        limit = int(args[1])

    status_msg = await message.answer(
        f"📥 <b>Завантаження історії</b>\n"
        f"Повідомлень з каналу: <code>{limit}</code>\n"
        f"Статус: <i>отримую список каналів...</i>"
    )
    try:
        from src.core.channels_config import get_all_monitored_channels
        from src.collector.client import client
        from src.database.models.events import EventStatus, UpdateType
        from src.database.repositories.channels import channel_repo
        from datetime import timezone
        from telethon.errors import FloodWaitError

        channel_ids = await get_all_monitored_channels()
        all_messages = []

        for ch_idx, chat_id in enumerate(channel_ids, 1):
            try:
                await status_msg.edit_text(
                    f"📥 <b>Завантаження історії</b>\n"
                    f"Канал: <code>{ch_idx}/{len(channel_ids)}</code>\n"
                    f"Зібрано повідомлень: <code>{len(all_messages)}</code>"
                )
            except Exception:
                pass  # "message is not modified" and similar are non-fatal

            while True:
                try:
                    async for msg in client.iter_messages(chat_id, limit=limit):
                        if msg.text and msg.date:
                            all_messages.append({'chat_id': chat_id, 'msg': msg})
                    break  # Success, move to the next chat
                except FloodWaitError as e:
                    logger.warning(f"Flood wait required: {e.seconds} seconds.")
                    try:
                        await status_msg.edit_text(
                            f"📥 <b>Завантаження історії</b>\n"
                            f"⏸ Telegram обмежив частоту запитів.\n"
                            f"Очікування: <code>{e.seconds} с</code>..."
                        )
                    except Exception:
                        pass
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

        await status_msg.edit_text(
            f"✅ <b>Історію завантажено</b>\n"
            f"Збережено повідомлень: <code>{count}</code>\n"
            f"Каналів опрацьовано: <code>{len(channel_ids)}</code>"
        )

    except Exception as e:
        logger.error(f"Fetch history failed: {e}", exc_info=True)
        await status_msg.edit_text("❌ Помилка під час завантаження історії. Деталі в логах.")


# ----------------------------------------------------------------- /find ---

@dp.message(Command("find"))
async def cmd_find(message: types.Message):
    query = message.text.replace("/find", "", 1).strip()
    if not query:
        await message.answer(
            "🔎 Вкажіть пошуковий запит.\n"
            "Приклад: <code>/find вибухи у Харкові</code>"
        )
        return

    status_msg = await message.answer(f"🔎 Шукаю: <i>{_esc(query)}</i>...")

    try:
        # Blocking model inference is offloaded to a thread
        query_embedding = await asyncio.to_thread(generate_query_embedding, query)

        async with AsyncSessionLocal() as session:
            # Semantic search across the whole archive (no time window)
            results = await message_repo.find_similar(
                session,
                embedding=query_embedding,
                limit=5,
                threshold=0.7,  # lower threshold for search
                within_hours=None
            )

            if not results:
                await status_msg.edit_text(
                    f"🤷 За запитом <i>{_esc(query)}</i> нічого не знайдено.\n"
                    "Спробуйте переформулювати."
                )
                return

            parts = [f"🔎 <b>Результати пошуку:</b> <i>{_esc(query)}</i>\n"]
            for idx, msg in enumerate(results, 1):
                channel = await msg.awaitable_attrs.channel
                snippet = _snippet(msg.raw_text or msg.extracted_text or "Лише медіа")
                ts = msg.timestamp.strftime('%d.%m.%Y %H:%M') if msg.timestamp else "—"
                parts.append(
                    f"{idx}. 📡 <b>{_esc(channel.name)}</b> · <code>{ts}</code>\n"
                    f"<blockquote>{_esc(snippet)}</blockquote>"
                )

            await status_msg.edit_text("\n".join(parts))
    except Exception as e:
        logger.error(f"Search failed: {e}", exc_info=True)
        await status_msg.edit_text("❌ Помилка під час пошуку. Спробуйте пізніше.")


# ------------------------------------------------------------------ /ask ---

@dp.message(Command("ask"))
async def cmd_ask(message: types.Message):
    """
    RAG-grounded Q&A: retrieves only the top relevant messages (packed into
    a strict token budget) and sends THEM to the LLM instead of the whole DB.
    """
    question = message.text.replace("/ask", "", 1).strip()
    if not question:
        await message.answer(
            "🧠 Вкажіть питання.\n"
            "Приклад: <code>/ask Що відбувалось у Харкові сьогодні?</code>"
        )
        return

    status_msg = await message.answer("🔎 <i>Шукаю релевантні повідомлення в базі...</i>")

    try:
        from src.core.rag.retriever import retrieve_context, build_context_block
        from src.reasoning.intelligence import answer_question

        async with AsyncSessionLocal() as session:
            packed, used_tokens = await retrieve_context(session, question)
            if not packed:
                await status_msg.edit_text(
                    f"🤷 Не знайшов релевантних повідомлень за запитом:\n"
                    f"<i>{_esc(question)}</i>"
                )
                return
            context_block = await build_context_block(packed)

        await status_msg.edit_text(
            f"🧠 <b>Генерую відповідь...</b>\n"
            f"Знайдено джерел: <code>{len(packed)}</code>\n"
            f"Контекст: <code>~{used_tokens}</code> токенів"
        )

        answer = await answer_question(question, context_block)
        await status_msg.delete()
        await message.answer(answer, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ask failed: {e}", exc_info=True)
        await status_msg.edit_text("❌ Помилка під час пошуку відповіді. Спробуйте пізніше.")


# -------------------------------------------------------------- /summary ---

@dp.message(Command("summary"))
async def cmd_summary(message: types.Message):
    logger.info("Received /summary command from user %s", message.from_user.id)

    try:
        from src.reasoning.summarization.hierarchical import run_hierarchical_summarization, estimate_summarization_time

        # 1. Estimate time
        logger.info("Estimating summarization time...")
        estimated_time = await estimate_summarization_time()

        # 2. Inform user
        status_msg = await message.answer(
            f"📰 <b>Готую зведення новин...</b>\n"
            f"⏱ Орієнтовний час: <code>{estimated_time}</code>"
        )

        async def update_progress(text_value: str):
            try:
                await status_msg.edit_text(
                    f"📰 <b>Зведення новин</b> · ⏱ <code>{estimated_time}</code>\n\n{text_value}"
                )
            except Exception:
                pass  # Ignore "message is not modified" exceptions from Telegram

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
            await message.answer(
                "⏸ <b>Перевищено ліміт запитів до ШІ.</b>\n"
                "Зачекайте кілька хвилин і спробуйте ще раз."
            )
        else:
            await message.answer("❌ Помилка під час генерації зведення. Деталі в логах.")
