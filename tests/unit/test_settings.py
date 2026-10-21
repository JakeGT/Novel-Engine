import pytest
from pydantic import ValidationError

from backend.core.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APP_ENV",
        "OPENAI_API_KEY",
        "NEO4J_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_development() -> None:
    settings = Settings(_env_file=None)

    assert settings.APP_ENV == "development"
    assert not settings.is_production
    assert settings.OPENAI_API_KEY == ""


def test_postgres_dsn_targets_async_psycopg() -> None:
    settings = Settings(
        _env_file=None,
        POSTGRES_HOST="db",
        POSTGRES_PORT=6543,
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p w",
        POSTGRES_DB="d",
    )

    assert settings.postgres_dsn == "postgresql+psycopg://u:p+w@db:6543/d"


def test_invalid_app_env_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="staging")


def test_production_rejects_default_neo4j_password() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="production", POSTGRES_PASSWORD="strong")


def test_production_rejects_default_postgres_password() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="production", NEO4J_PASSWORD="strong")


def test_production_accepts_non_default_credentials() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        NEO4J_PASSWORD="strong-neo4j",
        POSTGRES_PASSWORD="strong-postgres",
    )

    assert settings.is_production


def test_factory_does_not_require_a_developer_dotenv(settings_factory) -> None:
    settings = settings_factory(APP_ENV="test")

    assert settings.APP_ENV == "test"
    assert settings.postgres_dsn.startswith("postgresql+psycopg://")


def test_previous_credential_keks_parsing() -> None:
    from backend.core.config import Settings

    settings = Settings(_env_file=None, CREDENTIAL_PREVIOUS_KEKS="1:abc, 2:def ,")
    assert settings.previous_credential_keks == {1: "abc", 2: "def"}
    # A malformed entry is refused by its place, never dropped and never quoted.
    settings = Settings(_env_file=None, CREDENTIAL_PREVIOUS_KEKS="1:abc, 2:def ,bad,")
    with pytest.raises(ValueError, match="Entry 3 of CREDENTIAL_PREVIOUS_KEKS") as refused:
        _ = settings.previous_credential_keks
    assert "bad" not in str(refused.value) and "abc" not in str(refused.value)
