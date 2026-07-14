# -*- coding: utf-8 -*-
"""PostgreSQL SQLAlchemy base metadata and shared helpers.

This module is only imported when ``DB_BACKEND=postgresql``.
DynamoDB-only installs never import SQLAlchemy.
"""
from __future__ import print_function

__author__ = "bibow"

from typing import Any, Dict, Optional

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import scoped_session, sessionmaker
except ImportError:  # pragma: no cover - DynamoDB-only environments
    raise ImportError(
        "SQLAlchemy is required for PostgreSQL backend. "
        "Install with: pip install a2a-daemon-engine[postgresql]"
    )

Base = declarative_base()


def normalize_row(row: Any) -> Optional[Dict[str, Any]]:
    """Convert a SQLAlchemy model instance to a normalized dict.

    Handles UUID, datetime, JSONB, and Decimal types for JSON serialization.
    """
    if row is None:
        return None

    from ...utils.normalization import normalize_to_json

    if isinstance(row, dict):
        return normalize_to_json(row)

    # SQLAlchemy ORM object — extract column attributes via the ORM mapper
    # so the value is read from the correct Python attribute (mapper key),
    # not from a class-level attribute that may shadow the column (e.g. the
    # declarative 'metadata' attribute shadows a column named 'metadata').
    # Output keys use the DB column name (col.name) to match GraphQL fields.
    if hasattr(row, "__table__"):
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(type(row))
        result = {}
        for mapper_key, col_obj in mapper.columns.items():
            key = col_obj.name
            val = getattr(row, mapper_key, None)
            result[key] = _serialize_value(val)
        return normalize_to_json(result)

    return normalize_to_json(row)


def _serialize_value(val: Any) -> Any:
    """Serialize individual SQLAlchemy column values to JSON-safe types."""
    import datetime
    from decimal import Decimal
    from uuid import UUID as UUIDType

    if val is None:
        return None
    if isinstance(val, UUIDType):
        return str(val)
    if isinstance(val, (datetime.datetime, datetime.date)):
        # Return the datetime object directly — graphene's DateTime scalar
        # calls .isoformat() itself.  Returning a string here causes
        # "DateTime cannot represent value" because graphene's serialize()
        # requires a datetime.datetime / datetime.date instance, not a str.
        return val
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (list, dict)):
        return val
    return val


__all__ = ["Base", "normalize_row"]