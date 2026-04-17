"""PostgreSQL catalog queries for schema inspection (dvdrental)."""

from __future__ import annotations

from typing import Any

import psycopg

from config.settings import Settings
from utils.postgres import connect_async


async def fetch_schema_metadata(
    settings: Settings,
    *,
    schema_name: str = "public",
    table_name: str | None = None,
) -> dict[str, Any]:
    """Return columns, primary keys, and foreign keys for the given scope."""
    try:
        async with (
            await connect_async(settings) as conn,
            conn.cursor() as cur,
        ):
            col_sql = """
                SELECT
                    c.table_schema,
                    c.table_name,
                    c.column_name,
                    c.ordinal_position,
                    c.data_type,
                    c.is_nullable,
                    c.column_default
                FROM information_schema.columns c
                WHERE c.table_schema = %s
            """
            params: list[Any] = [schema_name]
            if table_name:
                col_sql += " AND c.table_name = %s"
                params.append(table_name)
            col_sql += " ORDER BY c.table_name, c.ordinal_position LIMIT 5000"
            await cur.execute(col_sql, params)
            col_rows = await cur.fetchall()

            await cur.execute(
                """
                SELECT
                    kcu.table_name,
                    kcu.column_name,
                    kcu.ordinal_position
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_catalog = kcu.constraint_catalog
                    AND tc.constraint_schema = kcu.constraint_schema
                    AND tc.constraint_name = kcu.constraint_name
                WHERE tc.table_schema = %s
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.table_name, kcu.ordinal_position
                """,
                (schema_name,),
            )
            pk_rows = await cur.fetchall()

            await cur.execute(
                """
                SELECT
                    kcu.table_name,
                    kcu.column_name,
                    ccu.table_schema AS foreign_table_schema,
                    ccu.table_name AS foreign_table_name,
                    ccu.column_name AS foreign_column_name,
                    rc.update_rule,
                    rc.delete_rule
                FROM information_schema.table_constraints tc
                JOIN information_schema.referential_constraints rc
                    ON tc.constraint_catalog = rc.constraint_catalog
                    AND tc.constraint_schema = rc.constraint_schema
                    AND tc.constraint_name = rc.constraint_name
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_catalog = kcu.constraint_catalog
                    AND tc.constraint_schema = kcu.constraint_schema
                    AND tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_catalog = tc.constraint_catalog
                    AND ccu.constraint_schema = tc.constraint_schema
                    AND ccu.constraint_name = tc.constraint_name
                WHERE tc.table_schema = %s
                  AND tc.constraint_type = 'FOREIGN KEY'
                """,
                (schema_name,),
            )
            fk_rows = await cur.fetchall()
    except psycopg.OperationalError as e:
        return {
            "success": False,
            "error": {
                "type": "connection_error",
                "message": (
                    "Cannot connect to dvdrental database. "
                    "Verify database is running and accessible."
                ),
                "details": str(e)[:500],
            },
        }
    except psycopg.Error as e:
        return {
            "success": False,
            "error": {
                "type": "database_error",
                "message": str(e).split("\n", 1)[0],
                "details": getattr(e, "pgcode", None) or str(e)[:500],
            },
        }

    pk_by_table: dict[str, list[str]] = {}
    for tname, cname, _pos in pk_rows:
        if table_name and tname != table_name:
            continue
        pk_by_table.setdefault(tname, []).append(cname)

    fk_by_table: dict[str, list[dict[str, Any]]] = {}
    for row in fk_rows:
        (
            tname,
            col,
            fts,
            ftn,
            fcn,
            ur,
            dr,
        ) = row
        if table_name and tname != table_name:
            continue
        fk_by_table.setdefault(tname, []).append(
            {
                "column": col,
                "references_schema": fts,
                "references_table": ftn,
                "references_column": fcn,
                "update_rule": ur,
                "delete_rule": dr,
            }
        )

    tables: dict[str, dict[str, Any]] = {}
    for row in col_rows:
        (
            sch,
            tname,
            cname,
            ord_pos,
            data_type,
            nullable,
            default,
        ) = row
        if table_name and tname != table_name:
            continue
        entry = tables.setdefault(
            tname,
            {
                "table_name": tname,
                "schema_name": sch,
                "columns": [],
                "primary_key": pk_by_table.get(tname, []),
                "foreign_keys": fk_by_table.get(tname, []),
            },
        )
        entry["columns"].append(
            {
                "name": cname,
                "ordinal_position": ord_pos,
                "data_type": data_type,
                "is_nullable": nullable == "YES",
                "column_default": default,
            }
        )

    for tname in tables:
        tables[tname]["primary_key"] = pk_by_table.get(tname, [])
        tables[tname]["foreign_keys"] = fk_by_table.get(tname, [])

    return {
        "success": True,
        "schema_name": schema_name,
        "table_filter": table_name,
        "tables": sorted(tables.values(), key=lambda t: t["table_name"]),
    }
