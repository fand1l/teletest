from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from src.database.models.events import Event, EventUpdate, EventStatus, UpdateType
from src.database.models.messages import Message
from .base import BaseRepository
import uuid

class EventRepository(BaseRepository[Event]):
    def __init__(self):
        super().__init__(Event)

    async def get_active_events(self, session: AsyncSession) -> List[Event]:
        stmt = select(Event).where(
            Event.status.in_([EventStatus.NEW, EventStatus.EVOLVING, EventStatus.CONTRADICTED])
        ).order_by(Event.updated_at.desc())
        
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_events_with_messages(self, session: AsyncSession) -> List[Event]:
        from sqlalchemy.orm import selectinload
        stmt = select(Event).where(
            Event.status.in_([EventStatus.NEW, EventStatus.EVOLVING, EventStatus.CONTRADICTED])
        ).options(
            selectinload(Event.updates).selectinload(EventUpdate.message).selectinload(Message.channel)
        ).order_by(Event.updated_at.desc())
        
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def add_update(self, session: AsyncSession, event_id: uuid.UUID, message_id: uuid.UUID, update_type: UpdateType, description: Optional[str] = None) -> EventUpdate:
        update = EventUpdate(
            event_id=event_id,
            message_id=message_id,
            update_type=update_type,
            description=description
        )
        session.add(update)
        
        # Also update the event's updated_at timestamp and potentially status
        event = await self.get(session, event_id)
        if event:
            if update_type == UpdateType.CONTRADICTION:
                event.status = EventStatus.CONTRADICTED
            elif event.status == EventStatus.NEW:
                event.status = EventStatus.EVOLVING
                
        await session.commit()
        return update

event_repo = EventRepository()
