import pytest

from backend.core.database import create_engine, create_session_factory, session_scope

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_session_scope_commits_and_closes_on_success() -> None:
    session = _FakeSession()

    async with session_scope(lambda: session):
        pass

    assert session.committed
    assert session.closed
    assert not session.rolled_back


@pytest.mark.asyncio
async def test_session_scope_rolls_back_and_closes_on_error() -> None:
    session = _FakeSession()

    with pytest.raises(RuntimeError):
        async with session_scope(lambda: session):
            raise RuntimeError("boom")

    assert session.rolled_back
    assert session.closed
    assert not session.committed


@pytest.mark.asyncio
async def test_create_engine_uses_settings_dsn_without_connecting(settings_factory) -> None:
    settings = settings_factory(
        POSTGRES_HOST="dbhost",
        POSTGRES_PORT=5555,
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="pw",
        POSTGRES_DB="db",
    )

    engine = create_engine(settings)
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.host == "dbhost"
        assert engine.url.port == 5555
        assert engine.url.database == "db"
        assert create_session_factory(engine) is not None
    finally:
        await engine.dispose()
