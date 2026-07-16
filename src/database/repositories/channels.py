from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from src.database.models.channels import Channel
from .base import BaseRepository

class ChannelRepository(BaseRepository[Channel]):
    def __init__(self):
        super().__init__(Channel)

    async def get_or_create(self, session: AsyncSession, channel_id: int, name: str, username: Optional[str] = None) -> Channel:
        channel = await self.get(session, channel_id)
        if not channel:
            channel = self.create(session, obj_in={
                "id": channel_id,
                "name": name,
                "username": username
            })
            await session.commit()
        return channel

channel_repo = ChannelRepository()
