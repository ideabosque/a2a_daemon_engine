# -*- coding: utf-8 -*-
"""PostgreSQL SQLAlchemy model for A2ATask entity.

Mirrors the DynamoDB A2ATaskModel schema with PostgreSQL-appropriate types.
Table: a2a_tasks

Composite primary key: (partition_key, task_id)
Secondary indexes (LSI equivalents):
  idx_a2a_tasks_partition_status
  idx_a2a_tasks_partition_priority

A2A v1.0 SDK camelCase fields are stored with explicit PG column names
(contextId, createdAt, lastModified) to match the DynamoDB attr_name aliases.
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


class A2ATaskModel(Base):
    """SQLAlchemy model for the A2ATask entity (table: a2a_tasks)."""

    __tablename__ = "a2a_tasks"

    # Primary key: composite (partition_key, task_id)
    partition_key = Column(String(128), nullable=False, primary_key=True)
    task_id = Column(String, nullable=False, primary_key=True)

    # Attributes
    endpoint_id = Column(String, nullable=True)
    part_id = Column(String, nullable=True)
    task_type = Column(String, nullable=True)
    assigned_agent_id = Column(String, nullable=True)
    status = Column(String, nullable=True)  # SCREAMING_SNAKE_CASE A2A task state
    priority = Column(String, nullable=True)  # low, medium, high, critical
    input_data = Column(JSONB, nullable=True)
    output_data = Column(JSONB, nullable=True)
    updated_by = Column(String(64), nullable=True)

    # Timestamps (legacy fields)
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
    completed_at = Column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # A2A v1.0 SDK fields (snake_case column names; DynamoDB attr_name aliases
    # are a DynamoDB-storage concern only and do not affect the PG schema).
    context_id = Column(String, nullable=True)
    created_at_sdk = Column(TIMESTAMP(timezone=True), nullable=True)
    last_modified = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        # LSI equivalents: status-index, priority-index
        Index(
            "idx_a2a_tasks_partition_status",
            "partition_key",
            "status",
        ),
        Index(
            "idx_a2a_tasks_partition_priority",
            "partition_key",
            "priority",
        ),
    )


__all__ = ["A2ATaskModel"]