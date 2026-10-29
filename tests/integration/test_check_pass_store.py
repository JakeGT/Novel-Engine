"""Notes from every pass can be stored in PostgreSQL (CON-008, migration 0061): the
pictures pass (CON-014) and the three optional passes, which the check written in 0039
did not list; anything else is still refused."""

from __future__ import annotations

from dataclasses import replace

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.check_passes import PASS_ORDER
from tests.integration.test_studio_store import S, _finding

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def studio(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresStudioStore(create_session_factory(engine))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_note_from_every_pass_is_kept_with_its_pass(studio) -> None:
    notes = [
        replace(_finding(f"fnd_{item.value}", "version", "srv_1", 0), check_pass=item.value)
        for item in PASS_ORDER
    ]
    await studio.add_findings(**S, findings=notes)
    found = {
        f.id: f.check_pass
        for f in await studio.findings(**S, target_kind="version", target_id="srv_1")
    }
    assert found == {f"fnd_{item.value}": item.value for item in PASS_ORDER}
    with pytest.raises(IntegrityError):
        await studio.add_findings(
            **S,
            findings=[replace(_finding("fnd_bad", "version", "srv_1", 0), check_pass="spelling")],
        )
