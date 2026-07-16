import logging
from typing import Any, Dict, List
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.entities import Entity, EntityType, MessageEntity
from .base import BaseRepository

logger = logging.getLogger(__name__)

# GLiNER labels (see src/pipeline/nlp.py) -> EntityType
_LABEL_MAP = {
    "location": EntityType.LOCATION,
    "person": EntityType.PERSON,
    "equipment": EntityType.EQUIPMENT,
    "organization": EntityType.ORGANIZATION,
}

# Entities below this GLiNER confidence are noise more often than signal
_MIN_SCORE = 0.5


class EntityRepository(BaseRepository[Entity]):
    def __init__(self):
        super().__init__(Entity)

    async def get_or_create(self, session: AsyncSession, name: str, entity_type: EntityType) -> Entity:
        stmt = select(Entity).where(Entity.name == name)
        entity = (await session.execute(stmt)).scalar_one_or_none()
        if entity is None:
            entity = Entity(name=name, entity_type=entity_type)
            session.add(entity)
            await session.flush()
        return entity

    async def save_message_entities(
        self,
        session: AsyncSession,
        message_id: uuid.UUID,
        raw_entities: List[Dict[str, Any]],
    ) -> int:
        """
        Persists GLiNER extraction results for a message: upserts Entity rows
        by name and links them via MessageEntity. Returns the number of links
        created. Does not commit — the caller owns the transaction.
        """
        seen_ids: set[uuid.UUID] = set()
        created = 0

        for raw in raw_entities:
            name = (raw.get("text") or "").strip()
            if not name or len(name) > 200:
                continue
            if raw.get("score", 1.0) < _MIN_SCORE:
                continue

            entity_type = _LABEL_MAP.get(str(raw.get("label", "")).lower(), EntityType.OTHER)
            entity = await self.get_or_create(session, name=name, entity_type=entity_type)

            if entity.id in seen_ids:
                continue  # same entity mentioned multiple times in one message
            seen_ids.add(entity.id)

            link_exists = (await session.execute(
                select(MessageEntity).where(
                    MessageEntity.message_id == message_id,
                    MessageEntity.entity_id == entity.id,
                )
            )).scalar_one_or_none()
            if link_exists is None:
                session.add(MessageEntity(message_id=message_id, entity_id=entity.id))
                created += 1

        return created


entity_repo = EntityRepository()
