# -*- coding: utf-8 -*-
"""PostgreSQL SQLAlchemy model for A2AAgent entity.

Mirrors the DynamoDB A2AAgentModel schema with PostgreSQL-appropriate types.
Table: a2a_agents

Composite primary key: (partition_key, agent_id)
Secondary index (LSI equivalent): idx_a2a_agents_partition_status
"""
from __future__ import print_function

__author__ = "bibow"

from sqlalchemy import (
    Column,
    Index,
    String,
    Text,
    TIMESTAMP,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from .base import Base


class A2AAgentModel(Base):
    """SQLAlchemy model for the A2AAgent entity (table: a2a_agents)."""

    __tablename__ = "a2a_agents"

    # Primary key: composite (partition_key, agent_id)
    partition_key = Column(String(128), nullable=False, primary_key=True)
    agent_id = Column(String, nullable=False, primary_key=True)

    # Attributes
    endpoint_id = Column(String, nullable=True)
    part_id = Column(String, nullable=True)
    agent_name = Column(String, nullable=True)
    capabilities = Column(Text, nullable=True)  # JSON string of list (DynamoDB stores as string)
    endpoint_url = Column(String, nullable=True)
    status = Column(String, nullable=True)  # active, inactive, error
    # 'metadata' is reserved by SQLAlchemy declarative base, so the Python
    # attribute is named agent_metadata while the PG column stays 'metadata'
    # (normalize_row keys output by col.name, so GraphQL still sees 'metadata').
    agent_metadata = Column("metadata", JSONB, nullable=True)

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

    __table_args__ = (
        # LSI equivalent: status-index
        Index(
            "idx_a2a_agents_partition_status",
            "partition_key",
            "status",
        ),
    )


__all__ = ["A2AAgentModel"]