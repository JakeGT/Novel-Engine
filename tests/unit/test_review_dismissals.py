"""Dismissing advisory findings on a proposal in review (CON-016): who may read and who
may dismiss, findings of one kind on one change told apart, and only standing dismissals
counting toward what a proposal can hold."""

from types import SimpleNamespace

import pytest

import backend.services.review_dismissals as module
from backend.domain.dismissals import DismissReason, review_fingerprint
from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.tenancy import Permission
from backend.services.review_dismissals import ReviewDismissalService
from tests.doubles import InMemoryReviewDismissalStore

pytestmark = pytest.mark.unit

SCOPE = AccessScope(
    actor_user_id="usr_reader",
    auth_mode=AuthMode.LOCAL,
    correlation_id="",
    workspace_id="ws",
    project_id="pj",
)


class _Mutations:
    """What the service reads of the mutation service: a proposal with one change that
    has two possible duplicates, for someone who may read it and, unless said, write."""

    def __init__(self, *, may_write: bool = True) -> None:
        self.may_write = may_write
        self.asked: list[Permission] = []
        self.mutation = SimpleNamespace(
            workspace_id="ws", project_id="pj", branch_id="main", id="mut_1"
        )
        self._journal = SimpleNamespace(get_branch=self._branch)

    async def _branch(self, **_kwargs):
        return SimpleNamespace(is_root=True, id="main")

    async def _require(self, scope, mutation_id, permission):
        self.asked.append(permission)
        if permission is Permission.PROJECT_WRITE and not self.may_write:
            raise ForbiddenError("You may only read this story.")
        return self.mutation

    async def review(self, scope, mutation_id):
        await self._require(scope, mutation_id, Permission.PROJECT_READ)
        return SimpleNamespace(
            operations=[SimpleNamespace(ordinal=0, revision=1, payload={"name": "lantern"})]
        )

    async def validate(self, scope, mutation_id):
        await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        return await self._validate(self.mutation, mutation_id)

    async def _validate(self, mutation, mutation_id):
        return SimpleNamespace(
            issues=[
                SimpleNamespace(
                    ordinal=0,
                    code="possible_duplicate",
                    severity="advisory",
                    message="It may be Lantern Harbor.",
                ),
                SimpleNamespace(
                    ordinal=0,
                    code="possible_duplicate",
                    severity="advisory",
                    message="It may be Lantern Hall.",
                ),
            ]
        )


def _service(mutations: _Mutations) -> ReviewDismissalService:
    return ReviewDismissalService(store=InMemoryReviewDismissalStore(), mutations=mutations)


@pytest.mark.asyncio
async def test_two_findings_of_one_kind_on_one_change_are_dismissed_apart():
    service = _service(_Mutations())
    first, second = await service.findings(SCOPE, "mut_1")
    assert first.key != second.key
    # The first keeps the key such a finding always had.
    assert first.key == review_fingerprint(
        "mut_1", f"0:1:{module.digest_of({'name': 'lantern'})}", "possible_duplicate"
    )
    after = await service.dismiss(SCOPE, "mut_1", key=second.key, reason=DismissReason.INTENDED)
    assert after[0].dismissal is None
    assert after[1].dismissal is not None and after[1].came_back is False


@pytest.mark.asyncio
async def test_a_reader_sees_the_findings_and_only_a_writer_dismisses_them():
    mutations = _Mutations(may_write=False)
    service = _service(mutations)
    found = await service.findings(SCOPE, "mut_1")
    assert len(found) == 2
    assert Permission.PROJECT_WRITE not in mutations.asked
    with pytest.raises(ForbiddenError):
        await service.dismiss(SCOPE, "mut_1", key=found[0].key, reason=DismissReason.INTENDED)
    with pytest.raises(ForbiddenError):
        await service.undo(SCOPE, "mut_1", "rvd_1")


@pytest.mark.asyncio
async def test_only_standing_dismissals_count_toward_what_a_proposal_holds(monkeypatch):
    monkeypatch.setattr(module, "MAX_DISMISSALS_PER_SCENE", 1)
    service = _service(_Mutations())
    first, _ = await service.findings(SCOPE, "mut_1")
    done = await service.dismiss(SCOPE, "mut_1", key=first.key, reason=DismissReason.INTENDED)
    assert done[0].dismissal is not None
    await service.undo(SCOPE, "mut_1", done[0].dismissal.id)
    # The one brought back holds nothing back: dismissing again is allowed.
    again = await service.dismiss(SCOPE, "mut_1", key=first.key, reason=DismissReason.NOT_A_PROBLEM)
    assert again[0].dismissal is not None
    assert again[0].dismissal.reason is DismissReason.NOT_A_PROBLEM
