"""
schema.py
---------
SQLAlchemy ORM models and database session management.

Tables
------
file_records   — one row per uploaded geospatial file
feature_records — one row per feature/geometry in a file
"""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker


# ---------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:4444/geospatial",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # drop stale connections automatically
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,   # keep attributes accessible after session closes
)


# ---------------------------------------------------------
# Base
# ---------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------
# ORM Models
# ---------------------------------------------------------

class FileRecord(Base):
    """
    Stores metadata about each uploaded geospatial file.

    Columns match the fields consumed/returned by services.py:
        filename, feature_count, crs, measurement_crs, status
    """

    __tablename__ = "file_records"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(512), nullable=False)
    feature_count = Column(Integer, nullable=False)
    crs = Column(Text, nullable=False)
    measurement_crs = Column(Text, nullable=False)
    status = Column(String(64), nullable=False, default="pending")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship — cascade deletes features when file is deleted
    features = relationship(
        "FeatureRecord",
        back_populates="file",
        cascade="all, delete-orphan",
    )


class FeatureRecord(Base):
    """
    Stores per-feature measurements for a processed file.

    Columns match what services.py writes via _save_feature():
        file_id, feature_index, geometry_type,
        properties (JSON), area_m2, length_m
    """

    __tablename__ = "feature_records"

    id = Column(BigInteger, primary_key=True, index=True)
    file_id = Column(
        Integer,
        ForeignKey("file_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_index = Column(Integer, nullable=False)
    geometry_type = Column(String(64), nullable=False)
    properties = Column(JSON, nullable=True)
    area_m2 = Column(Float, nullable=True)
    length_m = Column(Float, nullable=True)

    file = relationship("FileRecord", back_populates="features")


# ---------------------------------------------------------
# Table creation helper
# ---------------------------------------------------------

def create_tables() -> None:
    """Create all tables if they do not yet exist."""
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------

def get_db():
    """
    Yield a SQLAlchemy session for use in FastAPI routes/services.

    Usage in a route:
        from app.schema import get_db
        from sqlalchemy.orm import Session
        from fastapi import Depends

        def my_route(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
