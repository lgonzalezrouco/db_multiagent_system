import pytest
from pydantic import ValidationError

from config import Settings
from db_multiagent_system.bootstrap import run


def test_settings_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "example.invalid")
    monkeypatch.setenv("POSTGRES_PORT", "1234")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "mydb")
    s = Settings()
    assert s.postgres_host == "example.invalid"
    assert s.postgres_port == 1234
    assert s.postgres_user == "u"
    assert s.postgres_password == "p"
    assert s.postgres_db == "mydb"


@pytest.mark.integration
def test_run_live_postgres() -> None:
    try:
        Settings()
    except ValidationError:
        pytest.skip("Postgres settings missing/invalid (.env not found?)")

    code = run()
    if code != 0:
        pytest.skip("Postgres unreachable (is docker-compose up running?)")
    assert code == 0
