import logging
import sys

from db_multiagent_system.bootstrap import run


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    return run()


if __name__ == "__main__":
    sys.exit(main())
