# -*- coding: utf-8 -*-
"""Row-Level Security (RLS) helpers for the PostgreSQL backend.

Enforces tenant isolation at the database level. Only imported when
``DB_BACKEND=postgresql``. a2a_daemon_engine uses literal (unprefixed) table
names. All four A2A tables carry a ``partition_key`` column.
"""
from __future__ import print_function

__author__ = "bibow"

import logging
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

_RLS_TABLES = [
    "a2a_agents",
    "a2a_messages",
    "a2a_settings",
    "a2a_tasks",
]


def set_rls_context(session: Any, partition_key: str) -> None:
    """Set the RLS tenant context for the current database session.

    Uses connection-level ``SET`` (not ``SET LOCAL``) so the tenant context
    survives the ``commit()`` a mutation issues before subsequent reads.
    """
    if not partition_key:
        raise ValueError("partition_key must be a non-empty string for RLS context.")

    session.execute(
        text("SET app.tenant_id = :tenant"),
        {"tenant": partition_key},
    )


def create_rls_policies(engine: Any) -> None:
    """Enable RLS and create tenant-isolation policies on all A2A tables.

    Idempotent: existing policies are dropped before re-creation.
    """
    with engine.connect() as conn:
        for table_name in _RLS_TABLES:
            try:
                conn.execute(
                    text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
                )
                conn.execute(
                    text(f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}")
                )
                conn.execute(
                    text(
                        f"CREATE POLICY tenant_isolation ON {table_name} "
                        f"USING (partition_key = current_setting('app.tenant_id', true))"
                    )
                )
                logger.debug(f"RLS policy applied to {table_name}")
            except Exception as exc:
                logger.warning(f"Failed to apply RLS to {table_name}: {exc}")
        conn.commit()


__all__ = ["set_rls_context", "create_rls_policies", "_RLS_TABLES"]
