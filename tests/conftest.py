"""Pytest hooks for test env.

Loads `.env` so `Settings()` works in unit tests and integration tests
without in-code defaults.
Uses `override=False` so existing shell/CI env wins.
"""

from dotenv import load_dotenv
from pytest import Config


def pytest_configure(config: Config) -> None:
    load_dotenv(config.rootpath / ".env", override=False)
