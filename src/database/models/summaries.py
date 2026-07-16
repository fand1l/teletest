from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text, DateTime
from datetime import datetime
from .base import Base

class TopicSummary(Base):
    __tablename__ = "topic_summaries"

    topic: Mapped[str] = mapped_column(String, primary_key=True)
    content_html: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Track when this summary was last updated from events
    last_event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
