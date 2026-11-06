"""Alembic environment; database URL comes only from process environment."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

from controller.schema import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url():
    value = os.environ.get("CONTROLLER_DATABASE_URL")
    if not value:
        raise RuntimeError("CONTROLLER_DATABASE_URL is required to run migrations")
    try:
        url = make_url(value)
    except Exception as exc:
        raise RuntimeError("CONTROLLER_DATABASE_URL is malformed") from exc
    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError("CONTROLLER_DATABASE_URL must use postgresql+asyncpg")
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url().render_as_string(hide_password=True),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        do_run_migrations(supplied_connection)
        return

    connectable = create_async_engine(
        _database_url(),
        poolclass=pool.NullPool,
        hide_parameters=True,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        do_run_migrations(supplied_connection)
        return
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
