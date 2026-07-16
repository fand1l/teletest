from src.config import settings
from .base import BaseLLMProvider

def get_llm_provider() -> BaseLLMProvider:
    """
    Factory function to return the configured LLM Provider.
    Currently supports: 'gemini'
    """
    if settings.LLM_PROVIDER.lower() == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    else:
        raise ValueError(f"Unsupported LLM Provider: {settings.LLM_PROVIDER}")
