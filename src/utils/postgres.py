"""Shared PostgreSQL connection helpers."""

from __future__ import annotations

import psycopg

from config.settings import Settings


def postgres_conninfo(settings: Settings, *, connect_timeout: int = 10) -> str:
    """Build a libpq connection string from application settings."""
    return psycopg.conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
        connect_timeout=connect_timeout,
    )


async def connect_async(
    settings: Settings,
    *,
    connect_timeout: int = 10,
) -> psycopg.AsyncConnection:
    """Open an async PostgreSQL connection with shared defaults."""
    return await psycopg.AsyncConnection.connect(
        postgres_conninfo(settings, connect_timeout=connect_timeout),
    )
