from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Text, DateTime, Float, ForeignKey, Enum
from .base import Base
import uuid
import enum
from datetime import datetime
from typing import List, Optional

class EventStatus(enum.Enum):
    NEW = "NEW"
    EVOLVING = "EVOLVING"
    CONTRADICTED = "CONTRADICTED"
    VERIFIED = "VERIFIED"
    RESOLVED = "RESOLVED"

class UpdateType(enum.Enum):
    CORROBORATION = "CORROBORATION"
    CONTRADICTION = "CONTRADICTION"
    NEW_DETAIL = "NEW_DETAIL"

class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String, nullable=False)
    current_summary: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), default=EventStatus.NEW)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    updates: Mapped[List["EventUpdate"]] = relationship(back_populates="event", order_by="EventUpdate.timestamp")

class EventUpdate(Base):
    __tablename__ = "event_updates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), nullable=False)
    
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    update_type: Mapped[UpdateType] = mapped_column(Enum(UpdateType), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # LLM explanation of the update

    event: Mapped["Event"] = relationship(back_populates="updates")
    message: Mapped["Message"] = relationship(back_populates="event_updates")
