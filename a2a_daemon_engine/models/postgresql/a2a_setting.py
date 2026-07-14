# -*- coding: utf-8 -*-
"""PostgreSQL SQLAlchemy model for A2ASetting entity.

Mirrors the DynamoDB A2ASettingModel schema with PostgreSQL-appropriate types.
Table: a2a_settings

Composite primary key: (partition_key, setting_id)
No secondary index — queries filter on the composite primary key only.
"""
from __future__ import print_function

__author__ = "bibow"

from sqlalchemy import (
    Column,
    String,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


class A2ASettingModel(Base):
    """SQLAlchemy model for the A2ASetting entity (table: a2a_settings)."""

    __tablename__ = "a2a_settings"

    # Primary key: composite (partition_key, setting_id)
    partition_key = Column(String(128), nullable=False, primary_key=True)
    setting_id = Column(String, nullable=False, primary_key=True)

    # Attributes
    endpoint_id = Column(String, nullable=True)
    part_id = Column(String, nullable=True)
    setting = Column(JSONB, nullable=True)  # config blob

    # Timestamps
    updated_by = Column(String(64), nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


__all__ = ["A2ASettingModel"]