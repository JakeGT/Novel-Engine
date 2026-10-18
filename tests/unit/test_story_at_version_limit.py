"""The story at an earlier version is refused, not cut short, past the replay's limit."""

import asyncio
from datetime import UTC, datetime

import pytest

from backend.domain.errors import ValidationError
from backend.domain.projection import RecordVersion
from backend.services import story_at_version

pytestmark = pytest.mark.unit


class _Canon:
    """One record version per revision, newest first, like the repository's pages."""

    def __init__(self, revisions: int) -> None:
        self.revisions = revisions

    async def list_record_versions(self, *, max_revision, limit, **_):
        top = min(max_revision, self.revisions)
        return [
            RecordVersion(
                workspace_id="ws",
                project_id="pr",
                branch_id="main",
                record_type="entity",
                record_id=f"ent_{revision}",
                revision=revision,
                operation_type="create_entity",
                commit_id=f"cmt_{revision}",
                change={},
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            for revision in range(top, max(top - limit, 0), -1)
        ]


def _versions(canon, revision: int):
    return asyncio.run(story_at_version._versions_up_to(canon, "ws", "pr", revision))


def test_a_history_within_the_limit_is_read_whole(monkeypatch) -> None:
    monkeypatch.setattr(story_at_version, "_PAGE", 3)
    monkeypatch.setattr(story_at_version, "MAX_VERSIONS", 20)
    found = _versions(_Canon(10), 10)
    assert [version.revision for version in found] == list(range(1, 11))


def test_a_history_past_the_limit_is_refused_in_plain_words(monkeypatch) -> None:
    monkeypatch.setattr(story_at_version, "_PAGE", 3)
    monkeypatch.setattr(story_at_version, "MAX_VERSIONS", 5)
    with pytest.raises(ValidationError, match="history is too long to show an earlier version"):
        _versions(_Canon(10), 10)
