import logging
import asyncio
from typing import Type
from google import genai
from google.genai.errors import APIError
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings
from .base import BaseLLMProvider, LLMTimeoutError, LLMValidationError, LLMRateLimitError, LLMException, T
from .budget_manager import budget_manager

logger = logging.getLogger(__name__)

class GeminiProvider(BaseLLMProvider):
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.default_model = settings.LLM_MODEL_NAME
        self.default_temp = settings.LLM_TEMPERATURE
        self.max_retries = settings.LLM_MAX_RETRIES
        self.timeout = settings.LLM_TIMEOUT_SECONDS

    async def _execute_with_retry(self, call_func, max_retries: int):
        retries = max_retries if max_retries is not None else self.max_retries
        
        @retry(
            stop=stop_after_attempt(retries),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            retry=retry_if_exception_type((LLMTimeoutError, LLMRateLimitError, APIError)),
            reraise=True
        )
        async def _run():
            try:
                # Wrap the actual LLM call in asyncio.wait_for for strict timeout control
                return await asyncio.wait_for(call_func(), timeout=self.timeout)
            except asyncio.TimeoutError:
                logger.warning("LLM API call timed out.")
                raise LLMTimeoutError("Gemini API call timed out.")
            except APIError as e:
                # Basic handling for 429 Too Many Requests
                if "429" in str(e) or "quota" in str(e).lower():
                    logger.warning(f"LLM API rate limit hit: {e}")
                    raise LLMRateLimitError(f"Rate limited: {e}")
                logger.warning(f"LLM API error: {e}")
                raise
        
        return await _run()

    async def generate_structured_content(
        self, 
        prompt: str, 
        response_schema: Type[T],
        temperature: float = None,
        max_retries: int = None
    ) -> T:
        temp = temperature if temperature is not None else self.default_temp
        
        async def _call():
            logger.debug(f"Calling Gemini ({self.default_model}) for structured content.")
            return await self.client.aio.models.generate_content(
                model=self.default_model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": response_schema,
                    "temperature": temp
                }
            )

        try:
            estimated_tokens = budget_manager.estimate_tokens(prompt)
            await budget_manager.wait_for_capacity(estimated_tokens)
            
            response = await self._execute_with_retry(_call, max_retries)
            # Log basic metrics if available
            usage = getattr(response, 'usage_metadata', None)
            if usage:
                logger.debug(f"LLM Token Usage: prompt={usage.prompt_token_count}, candidates={usage.candidates_token_count}")
                
            return response_schema.model_validate_json(response.text)
        except ValidationError as e:
            logger.error(f"Failed to validate LLM JSON response: {e}")
            raise LLMValidationError(f"Invalid structured response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in generate_structured_content: {e}", exc_info=True)
            raise LLMException(f"LLM generation failed: {e}")

    async def generate_text_content(
        self, 
        prompt: str, 
        temperature: float = None,
        max_retries: int = None
    ) -> str:
        temp = temperature if temperature is not None else self.default_temp
        
        async def _call():
            logger.debug(f"Calling Gemini ({self.default_model}) for text content.")
            return await self.client.aio.models.generate_content(
                model=self.default_model,
                contents=prompt,
                config={
                    "temperature": temp
                }
            )

        try:
            estimated_tokens = budget_manager.estimate_tokens(prompt)
            await budget_manager.wait_for_capacity(estimated_tokens)
            
            response = await self._execute_with_retry(_call, max_retries)
            usage = getattr(response, 'usage_metadata', None)
            if usage:
                logger.debug(f"LLM Token Usage: prompt={usage.prompt_token_count}, candidates={usage.candidates_token_count}")
                
            text = response.text.strip()
            # Generic cleanup for markdown code block wrappers
            if text.startswith("```html"):
                text = text[7:]
            elif text.startswith("```json"):
                text = text[7:]
            elif text.startswith("```"):
                text = text[3:]
                
            if text.endswith("```"):
                text = text[:-3]
                
            text = text.replace('\\n', '\n')
            return text.strip()
            
        except Exception as e:
            logger.error(f"Unexpected error in generate_text_content: {e}", exc_info=True)
            raise LLMException(f"LLM text generation failed: {e}")
