from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppMemorySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_memory_host: str = "localhost"
    app_memory_port: int = 5433
    app_memory_user: str = "postgres"
    app_memory_password: str = "mysecretpassword"
    app_memory_db: str = "app_memory"
    default_user_id: str = "default"
    default_thread_id: str = "default-thread"
    persist_prefs_timeout_ms: int = 1500
