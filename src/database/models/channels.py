from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import BigInteger, String, Float, Integer
from .base import Base
from typing import List

class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=True)
    
    # Source Trust Metrics
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    total_reports: Mapped[int] = mapped_column(Integer, default=0)
    confirmed_reports: Mapped[int] = mapped_column(Integer, default=0)
    false_reports: Mapped[int] = mapped_column(Integer, default=0)
    
    # Metadata
    language: Mapped[str] = mapped_column(String, nullable=True)
    country: Mapped[str] = mapped_column(String, nullable=True)
    topic: Mapped[str] = mapped_column(String, nullable=True)

    messages: Mapped[List["Message"]] = relationship(back_populates="channel")
