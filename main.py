import logging
import sys

from db_multiagent_system import bootstrap
from db_multiagent_system.graph_demo import run as run_graph_demo


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    code = bootstrap.run()
    if code != 0:
        return code
    return run_graph_demo()


if __name__ == "__main__":
    sys.exit(main())
