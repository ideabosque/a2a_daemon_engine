# -*- coding: utf-8 -*-
"""PostgreSQL table initialization and shared utilities.

Only imported when DB_BACKEND=postgresql.
"""
from __future__ import print_function

__author__ = "bibow"

import logging
from typing import Any

from .base import Base


def initialize_tables(logger: logging.Logger, db_session: Any) -> None:
    """Create all PostgreSQL tables that have been imported.

    This uses SQLAlchemy metadata.create_all() which is idempotent —
    it only creates tables that don't already exist.
    """
    _import_all_models()

    engine = db_session.get_bind()
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("PostgreSQL tables initialized (create_all with checkfirst=True).")


def _import_all_models() -> None:
    """Import all PostgreSQL model modules to register them with Base.metadata."""
    model_modules = [
        ".a2a_agent",
        ".a2a_task",
        ".a2a_message",
        ".a2a_setting",
    ]
    for mod_name in model_modules:
        try:
            __import__(f"a2a_daemon_engine.models.postgresql{mod_name}", fromlist=["x"])
        except ImportError:
            logger = logging.getLogger(__name__)
            logger.debug(f"PostgreSQL model not yet available: {mod_name}")