"""Filesystem paths for schema documentation and presence marker."""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    """``src/graph/schema_paths.py`` → repository root."""
    return Path(__file__).resolve().parents[2]


def default_schema_docs_path() -> Path:
    """Default approved docs JSON: ``<repo>/data/schema_docs.json``."""
    return _repo_root() / "data" / "schema_docs.json"


def schema_docs_path_from_env() -> Path:
    raw = os.environ.get("SCHEMA_DOCS_PATH", "").strip()
    return Path(raw).expanduser() if raw else default_schema_docs_path()
