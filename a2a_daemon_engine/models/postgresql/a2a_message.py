# -*- coding: utf-8 -*-
"""PostgreSQL SQLAlchemy model for A2AMessage entity.

Mirrors the DynamoDB A2AMessageModel schema with PostgreSQL-appropriate types.
Table: a2a_messages

Composite primary key: (partition_key, message_id)
Secondary index (LSI equivalent): idx_a2a_messages_partition_status
"""
from __future__ import print_function

__author__ = "bibow"

from sqlalchemy import (
    Column,
    Index,
    String,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


class A2AMessageModel(Base):
    """SQLAlchemy model for the A2AMessage entity (table: a2a_messages)."""

    __tablename__ = "a2a_messages"

    # Primary key: composite (partition_key, message_id)
    partition_key = Column(String(128), nullable=False, primary_key=True)
    message_id = Column(String, nullable=False, primary_key=True)

    # Attributes
    endpoint_id = Column(String, nullable=True)
    part_id = Column(String, nullable=True)
    from_agent_id = Column(String, nullable=True)
    to_agent_id = Column(String, nullable=True)
    message_type = Column(String, nullable=True)
    task_id = Column(String, nullable=True)  # Links to a2a_tasks.task_id
    payload = Column(JSONB, nullable=True)
    status = Column(String, nullable=True)  # sent, delivered, acknowledged, failed

    # Timestamps
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    delivered_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # LSI equivalent: status-index
        Index(
            "idx_a2a_messages_partition_status",
            "partition_key",
            "status",
        ),
    )


__all__ = ["A2AMessageModel"]