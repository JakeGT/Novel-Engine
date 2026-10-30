"""A pass sent in groups (CON-013), taken over part of the way through: the groups an
earlier run of the job finished are not asked again, the rest are, and no note is stored
twice; a group's note quotes only the passages that group was sent."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.domain.check_passes import CheckPass, PassEstimate
from backend.domain.studio import AssistantFinding
from backend.ports import AIProviderError
from backend.services.scene_checks import (
    CheckNote,
    CheckNotes,
    GroupCall,
    GroupedPass,
    PassRunner,
    group_of,
    groups_done,
    keep_notes,
    pass_finished,
    pass_notes,
)
from tests.doubles import ScriptedAIGateway

pytestmark = pytest.mark.unit

PARAGRAPHS = [f"Paragraph {n} of the harbour, where Tobin waited." for n in range(1, 6)]
CONTENT = "\n\n".join(PARAGRAPHS)


def _spans() -> list[tuple[int, int]]:
    found, at = [], 0
    for paragraph in PARAGRAPHS:
        start = CONTENT.index(paragraph, at)
        found.append((start, start + len(paragraph)))
        at = start + len(paragraph)
    return found


def _grouped() -> GroupedPass:
    return GroupedPass(
        CheckPass.CONTINUITY,
        calls=tuple(
            GroupCall(
                group_id=f"g{n}",
                family="people",
                words="people",
                story="",
                passages=PARAGRAPHS[n],
                spans=(span,),
                secrets=0,
                estimate=PassEstimate(CheckPass.CONTINUITY, 100, 20),
            )
            for n, span in enumerate(_spans())
        ),
    )


class _Store:
    def __init__(self) -> None:
        self.findings: list[AssistantFinding] = []

    async def add_findings(self, *, workspace_id, project_id, findings):
        self.findings.extend(findings)


class _Context:
    def __init__(self) -> None:
        self.job = SimpleNamespace(
            id="job_1", workspace_id="ws", project_id="pj", actor_user_id="usr_1"
        )

    async def step(self, stage, progress):
        return None

    async def used(self, *args):
        return None

    @asynccontextmanager
    async def holding(self):
        yield


async def _sink(*args, **kwargs):
    return None


def _note(n: int) -> CheckNotes:
    return CheckNotes(notes=[CheckNote(quote=f"Paragraph {n} of the harbour", problem=f"P{n}.")])


async def _run(ai, store, earlier=()):
    grouped = _grouped()
    material = SimpleNamespace(check=CheckPass.CONTINUITY, has_material=True)
    return await PassRunner(ai=ai, store=store).run(
        _Context(),
        materials=(material,),
        content=CONTENT,
        pov=None,
        target_kind="version",
        target_id="ver_1",
        provider=(None, None, _sink),
        call_key=lambda check: f"job_1:1:{check.value}",
        remaining_tokens=lambda index: grouped.estimate.total,
        groups={CheckPass.CONTINUITY: grouped},
        earlier=earlier,
    )


@pytest.mark.asyncio
async def test_a_pass_stopped_after_group_two_of_five_goes_on_from_group_three():
    store = _Store()
    first = ScriptedAIGateway([_note(1), _note(2), AIProviderError("gone")])
    with pytest.raises(AIProviderError):
        await _run(first, store)
    assert [group_of(note) for note in store.findings] == [1, 2]
    earlier = pass_notes(list(store.findings), CheckPass.CONTINUITY, "ver_1")
    assert groups_done(earlier) == 2
    # Not finished: an earlier run that stored notes for two groups is not the whole pass.
    assert not pass_finished(earlier, _grouped())

    again = ScriptedAIGateway([_note(3), _note(4), _note(5)])
    outcome = await _run(again, store, earlier=earlier)
    # Only the three groups not finished were asked, each once.
    assert len(again.calls) == 3
    assert all(
        f"Paragraph {n} of" in call["messages"][1]["content"]
        for n, call in zip((3, 4, 5), again.calls, strict=True)
    )
    assert [group_of(note) for note in store.findings] == [1, 2, 3, 4, 5]
    assert sorted(note.message for note in store.findings) == ["P1.", "P2.", "P3.", "P4.", "P5."]
    assert outcome.kept == 5
    assert pass_finished(pass_notes(store.findings, CheckPass.CONTINUITY, "ver_1"), _grouped())

    # Taken over once more after it finished: nothing is asked or stored again.
    done = ScriptedAIGateway([])
    await _run(done, store, earlier=list(store.findings))
    assert done.calls == [] and len(store.findings) == 5


def test_a_pass_sent_whole_is_finished_once_it_stored_a_note():
    whole = AssistantFinding(
        id="fnd_1",
        target_kind="version",
        target_id="ver_1",
        job_id="job_1",
        category="continuity",
        message="m",
        suggestion="",
        start=0,
        end=1,
        check_pass="continuity",
    )
    assert group_of(whole) is None
    assert pass_finished([whole], None)
    # Stored by the pass sent whole before it came to be sent in groups: not asked again.
    assert pass_finished([whole], _grouped())
    assert not pass_finished([], _grouped())


def test_a_groups_note_quotes_only_the_passages_it_was_sent():
    # Group 3 was sent paragraph 3 only; "where Tobin waited" is in every paragraph.
    (third,) = [span for n, span in enumerate(_spans()) if n == 2]
    kept, dropped = keep_notes(
        [CheckNote(quote="where Tobin waited", problem="He is at sea.")],
        CONTENT,
        check=CheckPass.CONTINUITY,
        target_kind="version",
        target_id="ver_1",
        job_id="job_1",
        group=3,
        within=[third],
    )
    assert dropped == 0 and kept[0].start is not None
    assert third[0] <= kept[0].start < kept[0].end <= third[1]
    assert group_of(kept[0]) == 3
    # A quote from a passage the group was not sent is thrown away.
    _, lost = keep_notes(
        [CheckNote(quote="Paragraph 1 of the harbour", problem="x")],
        CONTENT,
        check=CheckPass.CONTINUITY,
        target_kind="version",
        target_id="ver_1",
        job_id="job_1",
        group=3,
        within=[third],
    )
    assert lost == 1
