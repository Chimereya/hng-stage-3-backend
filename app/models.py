from sqlalchemy import (
    Column, String, Boolean, Integer,
      Float, DateTime, Index, ForeignKey
)
from datetime import datetime, timezone
from .database import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid


class Profile(Base):
    __tablename__ = "profiles"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    gender = Column(String, nullable=False)
    gender_probability = Column(Float, nullable=False)
    age = Column(Integer, nullable=False)
    age_group = Column(String, nullable=False)
    country_id = Column(String(2), nullable=False)
    country_name = Column(String, nullable=False)
    country_probability = Column(Float, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_profiles_gender_country", "gender", "country_id"),
        Index("ix_profiles_age_group", "age_group"),
        Index("ix_profiles_age", "age"),
    )




class User(Base):
    __tablename__ = "users"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    github_id     = Column(String, unique=True, nullable=False)
    username      = Column(String, nullable=False)
    email         = Column(String, nullable=True)
    avatar_url    = Column(String, nullable=True)
    role          = Column(String, nullable=False, default="analyst")
    is_active     = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))




class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token      = Column(String, unique=True, nullable=False)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_revoked = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False)