import logging

import psycopg
from psycopg import OperationalError

from config import Settings

logger = logging.getLogger(__name__)


def run() -> int:
    settings = Settings()
    conninfo = psycopg.conninfo.make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db,
    )
    try:
        with (
            psycopg.connect(conninfo) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT 1 AS one, current_database() AS db, current_user AS role"
            )
            row = cur.fetchone()
        logger.info(
            "bootstrap_ok database=%s user=%s select_one=%s",
            row[1],
            row[2],
            row[0],
        )
    except OperationalError as exc:
        logger.error("connection failed: %s", exc)
        return 1
    return 0
