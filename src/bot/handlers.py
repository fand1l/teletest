import asyncio
import logging
from html import escape

from aiogram import types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import text

from src.bot.main import dp
from src.database.session import AsyncSessionLocal
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
    "📡 /channels\n"
    "<blockquote>Керування каналами моніторингу (увімкнути/вимкнути збір)</blockquote>\n"
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
        from datetime import timezone
        from src.core.channels_config import get_all_monitored_channels
        from src.collector.client import client
        from src.pipeline.orchestrator import process_message

        channel_ids = await get_all_monitored_channels()
        payloads = []

        # Phase 1: collect raw messages from Telegram.
        # Telethon itself sleeps through FloodWait up to flood_sleep_threshold;
        # longer waits raise and we skip that channel instead of hanging.
        for ch_idx, chat_id in enumerate(channel_ids, 1):
            try:
                await status_msg.edit_text(
                    f"📥 <b>Завантаження історії (1/2)</b>\n"
                    f"Канал: <code>{ch_idx}/{len(channel_ids)}</code>\n"
                    f"Зібрано повідомлень: <code>{len(payloads)}</code>"
                )
            except Exception:
                pass  # "message is not modified" and similar are non-fatal

            try:
                entity = await client.get_entity(chat_id)
                chat_title = getattr(entity, 'title', 'Unknown')

                async for msg in client.iter_messages(chat_id, limit=limit):
                    if not msg.text or not msg.date:
                        continue
                    payloads.append({
                        'telegram_msg_id': msg.id,
                        'channel_id': chat_id,
                        'channel_name': chat_title,
                        'timestamp': msg.date.astimezone(timezone.utc),
                        'sender': None,
                        'forwarded_from': None,
                        'raw_text': msg.text,
                        'media_type': None,
                        'has_media': msg.media is not None,
                    })
            except Exception as e:
                logger.error(f"Failed to fetch from {chat_id}: {e}")
                continue  # Skip this chat and move to the next

        # Sort chronologically (oldest to newest) so deduplication sees
        # the original report before its reposts.
        payloads.sort(key=lambda p: p['timestamp'])

        # Phase 2: run each message through the SAME pipeline as live intake:
        # spam filter -> embedding -> entities -> event matching/dedup.
        stats = {'ok': 0, 'spam': 0, 'empty': 0, 'duplicate': 0}
        for p_idx, payload in enumerate(payloads, 1):
            if p_idx == 1 or p_idx % 10 == 0:
                try:
                    await status_msg.edit_text(
                        f"🧠 <b>Обробка історії (2/2)</b>\n"
                        f"Повідомлення: <code>{p_idx}/{len(payloads)}</code>\n"
                        f"Додано: <code>{stats['ok']}</code> · "
                        f"Дублікати: <code>{stats['duplicate']}</code> · "
                        f"Спам: <code>{stats['spam']}</code>"
                    )
                except Exception:
                    pass

            async with AsyncSessionLocal() as session:
                already_stored = await message_repo.exists(
                    session,
                    channel_id=payload['channel_id'],
                    telegram_msg_id=payload['telegram_msg_id']
                )
            if already_stored:
                stats['duplicate'] += 1
                continue

            try:
                result = await process_message(payload)
                stats[result] = stats.get(result, 0) + 1
            except Exception as e:
                logger.error(f"Pipeline failed for history message {payload['telegram_msg_id']}: {e}")

        await status_msg.edit_text(
            f"✅ <b>Історію завантажено та оброблено</b>\n"
            f"Каналів: <code>{len(channel_ids)}</code>\n"
            f"➕ Додано подій/повідомлень: <code>{stats['ok']}</code>\n"
            f"♻️ Пропущено дублікатів: <code>{stats['duplicate']}</code>\n"
            f"🚫 Відфільтровано (спам/порожні): <code>{stats['spam'] + stats['empty']}</code>"
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


# ------------------------------------------------------------- /channels ---

_CHANNELS_PAGE_SIZE = 10


async def _channels_view(page: int):
    """Builds the text + keyboard for the channel management screen."""
    from src.core.channels_config import get_channels_overview

    overview = await get_channels_overview()
    items = sorted(overview.items(), key=lambda kv: (kv[1].get('name') or '').lower())

    total = len(items)
    enabled_count = sum(1 for _, info in items if info.get('enabled', True))
    total_pages = max(1, (total + _CHANNELS_PAGE_SIZE - 1) // _CHANNELS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    text_value = (
        f"📡 <b>Канали моніторингу</b>\n"
        f"Всього: <code>{total}</code> · Активних: <code>{enabled_count}</code>\n\n"
        f"Натисніть на канал, щоб увімкнути/вимкнути збір:"
    )

    rows = []
    start = page * _CHANNELS_PAGE_SIZE
    for cid, info in items[start:start + _CHANNELS_PAGE_SIZE]:
        mark = "✅" if info.get('enabled', True) else "⛔️"
        name = (info.get('name') or str(cid))[:40]
        rows.append([InlineKeyboardButton(
            text=f"{mark} {name}",
            callback_data=f"ch_toggle:{cid}:{page}"
        )])

    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="◀️", callback_data=f"ch_page:{(page - 1) % total_pages}"),
            InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ch_noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"ch_page:{(page + 1) % total_pages}"),
        ])

    return text_value, InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("channels"))
async def cmd_channels(message: types.Message):
    text_value, keyboard = await _channels_view(page=0)
    if not keyboard.inline_keyboard:
        await message.answer(
            "📡 Список каналів порожній.\n"
            "Канали з'являться тут автоматично після підключення юзербота."
        )
        return
    await message.answer(text_value, reply_markup=keyboard)


@dp.callback_query(F.data == "ch_noop")
async def cb_channels_noop(callback: types.CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("ch_page:"))
async def cb_channels_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[1])
    text_value, keyboard = await _channels_view(page=page)
    try:
        await callback.message.edit_text(text_value, reply_markup=keyboard)
    except Exception:
        pass  # "message is not modified"
    await callback.answer()


@dp.callback_query(F.data.startswith("ch_toggle:"))
async def cb_channels_toggle(callback: types.CallbackQuery):
    from src.core.channels_config import get_channels_overview, set_channel_enabled

    _, cid_str, page_str = callback.data.split(":")
    channel_id = int(cid_str)

    overview = await get_channels_overview()
    info = overview.get(channel_id)
    if info is None:
        await callback.answer("Канал не знайдено в конфігурації", show_alert=True)
        return

    new_state = not info.get('enabled', True)
    await set_channel_enabled(channel_id, new_state)

    text_value, keyboard = await _channels_view(page=int(page_str))
    try:
        await callback.message.edit_text(text_value, reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer("✅ Моніторинг увімкнено" if new_state else "⛔️ Моніторинг вимкнено")


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
