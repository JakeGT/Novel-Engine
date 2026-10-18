"""Every prompt the app sends is listed, built from fixed input and matches its snapshot
(SCN-003). A change of wording without a new version fails here."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from backend.domain.prompts import PROMPTS, PromptSpec, prompt_version
from tests.prompts import snapshots
from tests.prompts.cases import CASES

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[2] / "backend"


@pytest.mark.parametrize("prompt_id", sorted(PROMPTS))
def test_prompt_matches_its_snapshot(prompt_id: str) -> None:
    spec = PROMPTS[prompt_id]
    body = snapshots.render(spec)
    stored = snapshots.read(snapshots.SNAPSHOT_DIR, prompt_id)
    found = snapshots.problems(spec, body, stored, snapshots.read_ledger(snapshots.SNAPSHOT_DIR))
    assert not found, " ".join(found)


def test_every_prompt_has_cases_and_every_snapshot_a_prompt() -> None:
    assert set(CASES) == set(PROMPTS)
    stored = {path.stem for path in snapshots.SNAPSHOT_DIR.glob("*.txt")} - {"approved"}
    assert stored == set(PROMPTS)


def test_every_listed_builder_and_answer_shape_can_be_found() -> None:
    for spec in PROMPTS.values():
        assert callable(spec.build())
        model = spec.output_model()
        assert model is None or issubclass(model, BaseModel)
        assert re.fullmatch(r"[a-z][a-z-]*", spec.id) and spec.version >= 1
        assert spec.purpose and spec.purpose.endswith(".")


def test_versions_are_written_as_recorded() -> None:
    assert prompt_version("scene-draft") == f"scene-draft/{PROMPTS['scene-draft'].version}"


def test_scene_prompts_name_the_version_that_gives_scene_conditions() -> None:
    """STY-005 changed what a draft and a check send; provenance must tell them apart."""
    assert PROMPTS["scene-draft"].version >= 3
    # The all-in-one check (scene-review, last 3) became one prompt per pass (CON-008).
    assert "scene-review" not in PROMPTS
    for name in ("viewpoint", "continuity", "plan", "voice", "clarity"):
        assert f"check-{name}" in PROMPTS


def test_no_service_writes_its_own_prompt_version() -> None:
    """A version typed into a service would bypass the list and its snapshot."""
    pattern = re.compile(r"PROMPT_VERSION\s*=\s*[\"']")
    offenders = [
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def _spec(version: int) -> PromptSpec:
    return PromptSpec("example", version, "An example.", "tests.prompts.cases:_entity")


def test_changed_wording_without_a_new_version_is_refused(tmp_path: Path) -> None:
    snapshots.update(_spec(1), "Say hello.\n", tmp_path)
    approved = snapshots.read_ledger(tmp_path)
    stored = snapshots.read(tmp_path, "example")
    assert snapshots.problems(_spec(1), "Say hello.\n", stored, approved) == []

    # The wording changes, the version does not: the check fails and the update refuses.
    found = snapshots.problems(_spec(1), "Say hello loudly.\n", stored, approved)
    assert found and "version is still 1" in found[0]
    with pytest.raises(snapshots.SnapshotRefused, match="approved with different wording"):
        snapshots.update(_spec(1), "Say hello loudly.\n", tmp_path)
    assert snapshots.read(tmp_path, "example") == stored

    # Raising the version is what lets the new wording in; the old fingerprint stays.
    assert snapshots.update(_spec(2), "Say hello loudly.\n", tmp_path) == "written"
    approved = snapshots.read_ledger(tmp_path)
    assert set(approved) == {("example", 1), ("example", 2)}
    stored = snapshots.read(tmp_path, "example")
    assert snapshots.problems(_spec(2), "Say hello loudly.\n", stored, approved) == []


def test_a_snapshot_edited_by_hand_under_an_approved_version_fails(tmp_path: Path) -> None:
    snapshots.update(_spec(1), "Say hello.\n", tmp_path)
    path = tmp_path / "example.txt"
    path.write_text(snapshots.snapshot_text("example", 1, "Say hello loudly.\n"), encoding="utf-8")
    found = snapshots.problems(
        _spec(1),
        "Say hello loudly.\n",
        snapshots.read(tmp_path, "example"),
        snapshots.read_ledger(tmp_path),
    )
    assert found and "differs from what was approved" in found[0]


def test_a_version_cannot_go_down(tmp_path: Path) -> None:
    snapshots.update(_spec(2), "Say hello.\n", tmp_path)
    with pytest.raises(snapshots.SnapshotRefused, match="went down"):
        snapshots.update(_spec(1), "Say hello.\n", tmp_path)
    found = snapshots.problems(
        _spec(1),
        "Say hello.\n",
        snapshots.read(tmp_path, "example"),
        snapshots.read_ledger(tmp_path),
    )
    assert any("went down" in item for item in found)


def test_a_new_version_needs_its_snapshot(tmp_path: Path) -> None:
    snapshots.update(_spec(1), "Say hello.\n", tmp_path)
    found = snapshots.problems(
        _spec(2),
        "Say hello.\n",
        snapshots.read(tmp_path, "example"),
        snapshots.read_ledger(tmp_path),
    )
    assert found and "Run the update" in found[0]
