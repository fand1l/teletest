import logging
from typing import Optional
from pydantic import BaseModel, Field

from src.core.llm.factory import get_llm_provider
from src.core.llm.prompts import (
    DEDUPLICATION_PROMPT_TEMPLATE, 
    SUMMARIZE_EVENT_PROMPT_TEMPLATE,
    GLOBAL_SUMMARY_PROMPT_TEMPLATE
)

logger = logging.getLogger(__name__)

# Initialize the configured LLM provider
llm = get_llm_provider()

class DeduplicationResult(BaseModel):
    is_same_event: bool = Field(description="True if both messages are describing the exact same underlying real-world event.")
    has_contradiction: bool = Field(description="True if the reports contain conflicting facts (e.g. 2 vs 3 casualties).")
    contradiction_description: Optional[str] = Field(None, description="A brief explanation of the conflicting facts, if any.")
    merged_summary: str = Field(description="A concise summary of the event incorporating information from both messages. Must be in Ukrainian.")

async def evaluate_event_match(existing_summary: str, new_message: str) -> DeduplicationResult:
    """
    Evaluates if a new message is part of an existing event, checks for contradictions, and merges summaries.
    """
    logger.debug("Calling LLM for event deduplication/reasoning...")
    
    prompt = DEDUPLICATION_PROMPT_TEMPLATE.format(
        existing_summary=existing_summary,
        new_message=new_message
    )
    
    return await llm.generate_structured_content(
        prompt=prompt,
        response_schema=DeduplicationResult
    )

class EventSummaryResult(BaseModel):
    title: str = Field(description="A short, descriptive title for the event in Ukrainian (e.g., 'Рух ворожих БпЛА на Сумщині').")
    summary: str = Field(description="A concise summary of the core facts of the event in Ukrainian, without unnecessary channel links or spam.")

async def summarize_new_event(raw_text: str) -> EventSummaryResult:
    """
    Generates a title and clean summary from a raw incoming message using the LLM.
    """
    logger.debug("Calling LLM for initial event summarization...")
    
    prompt = SUMMARIZE_EVENT_PROMPT_TEMPLATE.format(
        raw_text=raw_text
    )
    
    return await llm.generate_structured_content(
        prompt=prompt,
        response_schema=EventSummaryResult
    )

async def generate_global_summary(events_context: str) -> str:
    """
    Takes active events and generates a cohesive, topic-based HTML summary.
    """
    logger.debug("Calling LLM for global topic-based summary...")
    
    prompt = GLOBAL_SUMMARY_PROMPT_TEMPLATE.format(
        events_context=events_context
    )
    
    return await llm.generate_text_content(prompt=prompt)
