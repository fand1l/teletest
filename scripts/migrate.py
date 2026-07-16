import asyncio
import sys
import os
sys.path.append(os.getcwd())

from src.database.session import engine
from sqlalchemy import text
from src.database.models.base import Base
from src.database.models import TopicSummary, Event

async def main():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE events ADD COLUMN topic VARCHAR;"))
            await conn.execute(text("CREATE INDEX ix_events_topic ON events (topic);"))
            print("Added topic column to events.")
        except Exception as e:
            print("Topic column might already exist:", e)
            
        await conn.run_sync(Base.metadata.create_all)
        print("Created new tables (TopicSummary, etc.).")

if __name__ == "__main__":
    asyncio.run(main())
