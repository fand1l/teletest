from abc import ABC, abstractmethod
from typing import TypeVar, Type, Any, Dict
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)

class LLMException(Exception):
    """Base exception for all LLM related errors."""
    pass

class LLMTimeoutError(LLMException):
    """Raised when an LLM API call times out."""
    pass

class LLMRateLimitError(LLMException):
    """Raised when the LLM API rate limit is exceeded."""
    pass

class LLMValidationError(LLMException):
    """Raised when the LLM response cannot be parsed into the expected structure."""
    pass

class BaseLLMProvider(ABC):
    """
    Abstract base class for all LLM providers (e.g., Gemini, OpenAI, Claude).
    Ensures that any integrated provider implements a consistent async interface.
    """
    
    @abstractmethod
    async def generate_structured_content(
        self, 
        prompt: str, 
        response_schema: Type[T],
        temperature: float = None,
        max_retries: int = None
    ) -> T:
        """
        Generates content from the LLM and guarantees it matches the provided Pydantic schema.
        
        Args:
            prompt (str): The combined system and user prompt.
            response_schema (Type[T]): A Pydantic BaseModel class for structural enforcement.
            temperature (float, optional): Override the default temperature.
            max_retries (int, optional): Override the default max retries.
            
        Returns:
            T: An instance of the requested Pydantic schema.
            
        Raises:
            LLMValidationError: If the model fails to return matching structure after retries.
            LLMTimeoutError: If the API call times out.
        """
        pass

    @abstractmethod
    async def generate_text_content(
        self, 
        prompt: str, 
        temperature: float = None,
        max_retries: int = None
    ) -> str:
        """
        Generates unstructured text content from the LLM.
        
        Args:
            prompt (str): The combined system and user prompt.
            temperature (float, optional): Override the default temperature.
            max_retries (int, optional): Override the default max retries.
            
        Returns:
            str: The raw text response.
            
        Raises:
            LLMTimeoutError: If the API call times out.
        """
        pass
