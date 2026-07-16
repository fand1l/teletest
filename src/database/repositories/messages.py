from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from src.database.models.messages import Message
from .base import BaseRepository

class MessageRepository(BaseRepository[Message]):
    def __init__(self):
        super().__init__(Message)

    async def get_recent_messages(self, session: AsyncSession, hours: int = 24) -> List[Message]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = select(Message).where(
            and_(
                Message.timestamp >= cutoff,
                Message.is_archived == False
            )
        ).order_by(Message.timestamp.desc())

        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def find_similar(
        self,
        session: AsyncSession,
        embedding: list[float],
        limit: int = 5,
        threshold: float = 0.85,
        within_hours: Optional[int] = 24,
    ) -> List[Message]:
        # Uses pgvector's cosine distance (<=>). Cosine similarity = 1 - cosine_distance.
        # So similarity > 0.85 is distance < 0.15
        distance_threshold = 1.0 - threshold

        conditions = [
            Message.embedding.cosine_distance(embedding) < distance_threshold,
            Message.is_archived == False,
        ]
        # Deduplication only makes sense against recent reports; without a
        # time window the query compares against the entire history and can
        # merge today's news into a months-old event.
        if within_hours is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
            conditions.append(Message.timestamp >= cutoff)

        stmt = select(Message).where(and_(*conditions)).order_by(
            Message.embedding.cosine_distance(embedding)
        ).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all())

message_repo = MessageRepository()
