import pytest

from backend.devtools.guard import ResetNotAllowed, ensure_reset_allowed, ensure_seed_allowed

pytestmark = pytest.mark.unit


def test_production_is_always_refused(settings_factory) -> None:
    settings = settings_factory(
        APP_ENV="production",
        NEO4J_PASSWORD="strong-neo4j",
        POSTGRES_PASSWORD="strong-postgres",
    )
    with pytest.raises(ResetNotAllowed):
        ensure_reset_allowed(settings, confirm=True, environ={"APP_ENV": "production"})


def test_test_environment_allowed_with_confirmation(settings_factory) -> None:
    settings = settings_factory(APP_ENV="test")
    assert ensure_reset_allowed(settings, confirm=True, environ={"APP_ENV": "test"}) == "test"


def test_development_local_allowed_with_confirmation(settings_factory) -> None:
    settings = settings_factory(APP_ENV="development")
    assert (
        ensure_reset_allowed(settings, confirm=True, environ={"APP_ENV": "development"})
        == "development"
    )


def test_confirmation_required(settings_factory) -> None:
    settings = settings_factory(APP_ENV="development")
    with pytest.raises(ResetNotAllowed):
        ensure_reset_allowed(settings, confirm=False, environ={"APP_ENV": "development"})


def test_unknown_environment_refused(settings_factory) -> None:
    settings = settings_factory(APP_ENV="development")
    with pytest.raises(ResetNotAllowed):
        ensure_reset_allowed(settings, confirm=True, environ={"APP_ENV": "staging"})


def test_missing_app_env_with_remote_stores_refused(settings_factory) -> None:
    settings = settings_factory(POSTGRES_HOST="db.prod.example.com")
    with pytest.raises(ResetNotAllowed):
        ensure_reset_allowed(settings, confirm=True, environ={})


def test_missing_app_env_with_local_stores_allowed(settings_factory) -> None:
    settings = settings_factory()
    assert ensure_reset_allowed(settings, confirm=True, environ={}) == "development"


def test_seed_refuses_production(settings_factory) -> None:
    settings = settings_factory(
        APP_ENV="production",
        NEO4J_PASSWORD="strong-neo4j",
        POSTGRES_PASSWORD="strong-postgres",
    )
    with pytest.raises(ResetNotAllowed):
        ensure_seed_allowed(settings)
