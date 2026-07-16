from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Telegram Auth
    TELEGRAM_API_ID: int = Field(..., description="API ID from my.telegram.org")
    TELEGRAM_API_HASH: str = Field(..., description="API Hash from my.telegram.org")
    TELEGRAM_PHONE: str = Field(..., description="Phone number for Telethon")
    
    # Bot Auth
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    
    # Gemini Auth
    GEMINI_API_KEY: str = Field(..., description="Google Gemini API Key")
    
    # Database
    DATABASE_URL: str = Field(..., description="PostgreSQL async connection string")
    
    # Application Config
    ACTIVE_RETENTION_HOURS: int = Field(24, description="Retention window for active messages in hours")
    SIMILARITY_THRESHOLD: float = Field(0.85, description="Cosine similarity threshold for event deduplication")
    MESSAGE_QUEUE_MAX_SIZE: int = Field(1000, description="Max size of the internal ingest queue (backpressure buffer)")
    TELETHON_FLOOD_SLEEP_THRESHOLD: int = Field(60, description="Telethon auto-sleeps on FloodWait errors up to this many seconds")
    
    # LLM Configuration
    LLM_PROVIDER: str = Field("gemini", description="The LLM provider to use (e.g. gemini, openai)")
    LLM_MODEL_NAME: str = Field("gemini-3.1-flash-lite", description="The specific model name for the provider")
    LLM_TEMPERATURE: float = Field(0.2, description="Default temperature for LLM generation")
    LLM_MAX_RETRIES: int = Field(3, description="Maximum number of retries for transient API errors")
    LLM_TIMEOUT_SECONDS: int = Field(30, description="Timeout in seconds for LLM API calls")
    LLM_TPM_LIMIT: int = Field(250000, description="Tokens-per-minute quota for the LLM API")
    LLM_RPM_LIMIT: int = Field(15, description="Requests-per-minute quota for the LLM API")

    # RAG / Context Management
    RAG_CONTEXT_TOKENS: int = Field(18000, description="Max tokens of retrieved context packed into a single LLM request")
    RAG_CHUNK_TOKENS: int = Field(900, description="Target chunk size (tokens) when splitting large texts")
    RAG_CHUNK_OVERLAP_TOKENS: int = Field(120, description="Token overlap between adjacent chunks")
    AUTO_MERGE_SIMILARITY: float = Field(0.97, description="Above this cosine similarity a message is merged into an event without an LLM call")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
