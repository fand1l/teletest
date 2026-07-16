from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, String, Text, DateTime, Boolean, ForeignKey, Index
from pgvector.sqlalchemy import Vector
from .base import Base
import uuid
from datetime import datetime
from typing import List, Optional

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), nullable=False)
    
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    
    sender: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    forwarded_from: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # From OCR/Florence-2
    
    media_type: Mapped[Optional[str]] = mapped_column(String, nullable=True) # 'photo', 'video', 'document'
    media_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Embedding for multilingual-e5-small is usually 384 dimensions
    embedding = mapped_column(Vector(384), nullable=True)
    
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    channel: Mapped["Channel"] = relationship(back_populates="messages")
    event_updates: Mapped[List["EventUpdate"]] = relationship(back_populates="message")

    __table_args__ = (
        Index("ix_messages_timestamp", "timestamp"),
        Index("ix_messages_is_archived", "is_archived"),
    )
