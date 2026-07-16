import logging
import asyncio
import json
from typing import List, Dict, Optional
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from src.database.models.events import Event
from src.database.models.summaries import TopicSummary
from src.database.session import AsyncSessionLocal
from src.core.llm.factory import get_llm_provider
from src.core.llm.budget_manager import budget_manager
from src.core.llm.prompts import PROMPT_INJECTION_DEFENSE, TELEGRAM_HTML_RULES
from sqlalchemy import select

logger = logging.getLogger(__name__)

llm = get_llm_provider()

DEFAULT_TOPICS = ["Політика", "Фронт", "Економіка", "ППО", "Міжнародні події", "Інше"]

class EventClassification(BaseModel):
    event_id: str = Field(description="The UUID string of the event.")
    topic: str = Field(description="The assigned topic for the event. Use 'Спам/Реклама' for ads or off-topic content.")

class EventClassifications(BaseModel):
    classifications: List[EventClassification] = Field(description="List of event classifications.")

BATCH_CLASSIFICATION_PROMPT = f"""
You are an expert military and news intelligence analyst.
Categorize the following news events into EXACTLY ONE of these topics:
{{topics}}

If an event is clearly an advertisement, promotion, giveaway, off-topic spam, or irrelevant to news/intelligence, categorize it strictly as: "Спам/Реклама".

{PROMPT_INJECTION_DEFENSE}

Respond with strict JSON providing a list of event classifications. Each item must have 'event_id' and 'topic'. If an event doesn't clearly fit any category but is valid news, use "Інше".

Events:
{{events_text}}
"""

CHUNK_SUMMARY_PROMPT = f"""
You are an expert military and news intelligence analyst.
Below is a list of recent events related to the topic: {{topic}}.

{PROMPT_INJECTION_DEFENSE}

{{events_text}}

Your task is to synthesize these events into a cohesive, readable intermediate summary in Ukrainian.
Focus on the most important facts. Do not mention individual sources unless critical.
"""

TOPIC_UPDATE_PROMPT = f"""
You are an expert military and news intelligence analyst.
You need to update an existing SITREP (Situation Report) for the topic: {{topic}}.

Existing Report:
{{existing_summary}}

New Events to incorporate:
{{new_events_summary}}

Your task:
Merge the new events into the existing report seamlessly.
If the new events contradict the old ones, reflect the latest updates.
Respond with the updated HTML-formatted summary for this topic in Ukrainian.

{TELEGRAM_HTML_RULES}

Respond ONLY with the HTML output.
"""

GLOBAL_CONSOLIDATION_PROMPT = f"""
You are a senior intelligence analyst.
Below are the latest thematic summaries for various topics.

{{thematic_summaries}}

Your task:
Combine them into a single, highly professional, cohesive Global SITREP (Situation Report) in Ukrainian.
Organize it elegantly.

{TELEGRAM_HTML_RULES}

Respond ONLY with the HTML output.
"""

def chunk_events(events: List[Event], max_tokens_per_chunk: Optional[int] = None) -> List[List[Event]]:
    """Splits events into chunks that fit within the configured RAG context budget."""
    if max_tokens_per_chunk is None:
        from src.config import settings
        max_tokens_per_chunk = settings.RAG_CONTEXT_TOKENS
    chunks = []
    current_chunk = []
    current_tokens = 0
    
    for event in events:
        text = f"Title: {event.title}\nSummary: {event.current_summary}\n\n"
        tokens = budget_manager.estimate_tokens(text)
        if current_tokens + tokens > max_tokens_per_chunk and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [event]
            current_tokens = tokens
        else:
            current_chunk.append(event)
            current_tokens += tokens
            
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

async def summarize_chunk(topic: str, events_chunk: List[Event]) -> str:
    events_text = "\n\n".join([f"- {e.title}: {e.current_summary}" for e in events_chunk])
    prompt = CHUNK_SUMMARY_PROMPT.format(topic=topic, events_text=events_text)
    return await llm.generate_text_content(prompt)

async def generate_final_global_summary(topic_summaries: List[TopicSummary]) -> str:
    """Combines all thematic summaries into the final global HTML summary."""
    if not topic_summaries:
        return "Наразі немає новин для зведення."
        
    thematic_text = ""
    for ts in topic_summaries:
        thematic_text += f"=== ТЕМА: {ts.topic} ===\n{ts.content_html}\n\n"
        
    prompt = GLOBAL_CONSOLIDATION_PROMPT.format(thematic_summaries=thematic_text)
    return await llm.generate_text_content(prompt)

async def estimate_summarization_time() -> str:
    """Estimates the time required to complete the hierarchical summarization."""
    async with AsyncSessionLocal() as session:
        stmt = select(Event).where(Event.topic.is_(None))
        unclassified_count = len((await session.execute(stmt)).scalars().all())
        
        stmt = select(TopicSummary)
        existing_summaries = {ts.topic: ts for ts in (await session.execute(stmt)).scalars().all()}
        
        new_events_count = 0
        for topic in DEFAULT_TOPICS:
            existing = existing_summaries.get(topic)
            if existing and existing.last_event_timestamp:
                stmt = select(Event).where(Event.topic == topic, Event.created_at > existing.last_event_timestamp)
            else:
                stmt = select(Event).where(Event.topic == topic)
            new_events_count += len((await session.execute(stmt)).scalars().all())
            
        # Rough token estimation for wait time calculation
        total_tokens = (unclassified_count * 300) + (new_events_count * 500)
        
        # Generation time baseline
        baseline_seconds = 15 + (unclassified_count // 20 * 5) + (new_events_count // 50 * 10)
        
        if total_tokens <= budget_manager.tpm_limit:
            if baseline_seconds < 60:
                return f"близько {baseline_seconds} секунд"
            return f"близько {baseline_seconds // 60} хв {baseline_seconds % 60} с"
            
        # Calculate API wait time
        wait_minutes = int(total_tokens / budget_manager.tpm_limit)
        wait_seconds = int(((total_tokens / budget_manager.tpm_limit) % 1) * 60)
        
        total_seconds = wait_minutes * 60 + wait_seconds + baseline_seconds
        return f"близько {total_seconds // 60} хв {total_seconds % 60} с"

async def run_hierarchical_summarization(progress_callback=None) -> str:
    """
    Main entry point for lazy hierarchical summarization.
    """
    async def report(msg: str):
        logger.info(msg)
        if progress_callback:
            try:
                await progress_callback(msg)
            except Exception as e:
                logger.error(f"Failed to update progress: {e}")

    async with AsyncSessionLocal() as session:
        # 1. Classify unclassified events
        stmt = select(Event).where(Event.topic.is_(None))
        unclassified_events = (await session.execute(stmt)).scalars().all()
        if unclassified_events:
            total_batches = (len(unclassified_events) + 19) // 20
            await report(f"🔄 <b>Етап 1/3: Класифікація подій</b>\nЗнайдено {len(unclassified_events)} нових подій. Розподіляємо по категоріях (0/{total_batches} партій)...")
            
            for i in range(0, len(unclassified_events), 20):
                batch = unclassified_events[i:i+20]
                batch_num = (i // 20) + 1
                await report(f"🔄 <b>Етап 1/3: Класифікація подій</b>\nАналіз партії {batch_num}/{total_batches}...")
                
                events_text = "\n".join([f"ID: {e.id}\nTitle: {e.title}\nSummary: {e.current_summary}\n---" for e in batch])
                prompt = BATCH_CLASSIFICATION_PROMPT.format(topics=", ".join(DEFAULT_TOPICS), events_text=events_text)
                try:
                    result = await llm.generate_structured_content(prompt, response_schema=EventClassifications)
                    topic_map = {item.event_id: item.topic for item in result.classifications}
                    for event in batch:
                        topic = topic_map.get(str(event.id), "Інше")
                        event.topic = topic if topic in DEFAULT_TOPICS else "Інше"
                        session.add(event)
                    await session.commit()
                except Exception as e:
                    logger.error(f"Failed to classify batch {batch_num}: {e}")
                    for event in batch:
                        event.topic = "Інше"
                        session.add(event)
                    await session.commit()
            
        # 2. Process events per topic
        stmt = select(TopicSummary)
        existing_summaries = {ts.topic: ts for ts in (await session.execute(stmt)).scalars().all()}
        
        await report("🔄 <b>Етап 2/3: Підготовка тематичних зведень</b>\nПошук нових подій для кожної теми...")
        
        # Determine topics with new events first
        topics_with_new_events = []
        for topic in DEFAULT_TOPICS:
            existing = existing_summaries.get(topic)
            if existing and existing.last_event_timestamp:
                stmt = select(Event).where(Event.topic == topic, Event.created_at > existing.last_event_timestamp).order_by(Event.created_at.asc())
            else:
                stmt = select(Event).where(Event.topic == topic).order_by(Event.created_at.asc())
            new_events = (await session.execute(stmt)).scalars().all()
            if new_events:
                topics_with_new_events.append((topic, existing, new_events))
                
        total_topics_to_update = len(topics_with_new_events)
        
        for idx, (topic, existing, new_events) in enumerate(topics_with_new_events, 1):
            await report(f"🔄 <b>Етап 2/3: Оновлення тем ({idx}/{total_topics_to_update})</b>\nТема: <i>{topic}</i> (Нових подій: {len(new_events)})")
            
            # Chunk and summarize
            chunks = chunk_events(new_events)
            intermediate_summaries = []
            for c_idx, chunk in enumerate(chunks, 1):
                if len(chunks) > 1:
                    await report(f"🔄 <b>Етап 2/3: Оновлення тем ({idx}/{total_topics_to_update})</b>\nТема: <i>{topic}</i>\nОбробка частини {c_idx}/{len(chunks)}...")
                summary = await summarize_chunk(topic, chunk)
                intermediate_summaries.append(summary)
                
            merged_new_events_summary = "\n\n---\n\n".join(intermediate_summaries)
            existing_text = existing.content_html if existing else "No existing summary."
            prompt = TOPIC_UPDATE_PROMPT.format(topic=topic, existing_summary=existing_text, new_events_summary=merged_new_events_summary)
            final_html = await llm.generate_text_content(prompt)
            
            if not existing:
                existing = TopicSummary(topic=topic)
                session.add(existing)
            existing.content_html = final_html
            latest_event_time = max((e.created_at for e in new_events if e.created_at), default=datetime.now(timezone.utc))
            existing.last_event_timestamp = latest_event_time
            await session.commit()
            
            existing_summaries[topic] = existing
                
        # 3. Generate Global Summary
        active_summaries = list(existing_summaries.values())
        if not active_summaries:
            return "Наразі немає даних для зведення."
            
        await report(f"🔄 <b>Етап 3/3: Формування глобального зведення</b>\nСинтез {len(active_summaries)} тем у фінальний звіт...")
        final_html = await generate_final_global_summary(active_summaries)
        
        return final_html
