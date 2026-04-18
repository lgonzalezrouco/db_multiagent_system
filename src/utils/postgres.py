"""Shared PostgreSQL connection helpers."""

from __future__ import annotations

import psycopg
from psycopg import conninfo

from config.postgres_settings import PostgresSettings


def postgres_conninfo(settings: PostgresSettings, *, connect_timeout: int = 10) -> str:
    """Build a libpq connection string from application settings."""
    return conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
        connect_timeout=connect_timeout,
    )


async def connect_async(
    settings: PostgresSettings,
    *,
    connect_timeout: int = 10,
) -> psycopg.AsyncConnection:
    """Open an async PostgreSQL connection with shared defaults."""
    return await psycopg.AsyncConnection.connect(
        postgres_conninfo(settings, connect_timeout=connect_timeout),
    )
