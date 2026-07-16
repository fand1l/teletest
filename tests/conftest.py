import os

# Provide dummy required settings so importing src.config in tests
# does not fail when no .env file is present (e.g. in CI).
os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "test_hash")
os.environ.setdefault("TELEGRAM_PHONE", "+380000000000")
os.environ.setdefault("BOT_TOKEN", "123:test_token")
os.environ.setdefault("GEMINI_API_KEY", "test_key")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
