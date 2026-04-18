from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from config.memory_settings import AppMemorySettings


def get_app_memory_connection(settings: AppMemorySettings | None = None):
    """Return a psycopg connection to the app_memory database."""
    s = settings or AppMemorySettings()
    dsn = (
        f"host={s.app_memory_host} port={s.app_memory_port} "
        f"dbname={s.app_memory_db} user={s.app_memory_user} "
        f"password={s.app_memory_password} connect_timeout=2"
    )
    return psycopg.connect(dsn, row_factory=dict_row)
