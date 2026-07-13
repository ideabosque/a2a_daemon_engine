# -*- coding: utf-8 -*-
"""Alembic migration environment for a2a_daemon_engine PostgreSQL backend."""
from __future__ import print_function

import logging
from logging.config import fileConfig

from alembic import context

# Import Base metadata for autogenerate support
from a2a_daemon_engine.models.postgresql.base import Base

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set up the target metadata for autogenerate
target_metadata = Base.metadata

logger = logging.getLogger("alembic.env")


def get_url():
    """Get the database URL from config or environment."""
    import os

    # Check for environment variable override
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url

    # Try to use Config if initialized
    try:
        from a2a_daemon_engine.handlers.config import Config

        if Config.DB_BACKEND == "postgresql":
            setting = Config.get_setting()
            from urllib.parse import quote_plus

            password = quote_plus(setting["db_password"])
            return (
                f"postgresql+psycopg2://{setting['db_user']}:{password}"
                f"@{setting['db_host']}:{setting['db_port']}/{setting['db_schema']}"
            )
    except Exception:
        pass

    # Fall back to migration/alembic.ini
    return config.get_main_option("sqlalchemy.url")


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table="a2a_alembic_version",
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    from sqlalchemy import create_engine

    url = get_url()
    connectable = create_engine(url, pool_pre_ping=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table="a2a_alembic_version",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()