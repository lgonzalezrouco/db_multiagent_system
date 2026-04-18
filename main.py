import asyncio
import logging
import os
import sys
from pathlib import Path

from db_multiagent_system import bootstrap
from db_multiagent_system.graph_demo import run_async as run_graph_demo_async
from db_multiagent_system.memory_demo import run_async as run_memory_demo_async
from db_multiagent_system.schema_export import export_schema_catalog_json


def _schema_export_path() -> Path:
    raw = os.environ.get("SCHEMA_EXPORT_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    repo_root = Path(__file__).resolve().parent
    return repo_root / "data" / "schema_catalog.json"


async def _main_async(memory_demo: bool) -> int:
    if memory_demo:
        return await run_memory_demo_async()

    out_path = _schema_export_path()
    try:
        await export_schema_catalog_json(out_path)
    except Exception as exc:
        logging.getLogger(__name__).error("Schema JSON export failed: %s", exc)
        return 1
    return await run_graph_demo_async()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    memory_demo = "--memory-demo" in sys.argv

    if not memory_demo:
        code = bootstrap.run()
        if code != 0:
            return code

    return asyncio.run(_main_async(memory_demo=memory_demo))


if __name__ == "__main__":
    sys.exit(main())
