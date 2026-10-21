"""Whom a wider dismissal covers (CON-016), which codes it may name, and the key of a
voice note on a whole scene."""

import ast
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.domain.dismissals import (
    DismissalScope,
    DismissReason,
    FindingKind,
    ScopedDismissal,
    voice_fingerprint,
)
from backend.services.scoped_dismissals import (
    ScopedView,
    Scoping,
    finding_words,
    is_studio_code,
)

pytestmark = pytest.mark.unit

NAMES = {"ent_mara": ("Mara", "The Fox"), "ent_tobin": ("Tobin Ashe", "Tobin")}


def _for_mara(kind: FindingKind, code: str) -> Scoping:
    item = ScopedDismissal(
        workspace_id="ws",
        project_id="pr",
        branch_id="main",
        id="sd_1",
        kind=kind,
        code=code,
        scope=DismissalScope.CHARACTER,
        reason=DismissReason.NOT_A_PROBLEM,
        note="",
        created_at=datetime.now(UTC),
        entity_id="ent_mara",
    )
    view = ScopedView(item, "about Mara", True)
    return Scoping(standing=[view], position={"pln_a": 0}, names=dict(NAMES))


def test_a_check_dismissed_for_a_character_covers_only_findings_about_them() -> None:
    scoping = _for_mara(FindingKind.CHECK, "appearance_differs")
    about_mara = finding_words("Mara\x1eeyes\x1eblue")
    about_tobin = finding_words("Tobin\x1eeyes\x1eblue")
    assert scoping.covering("pln_a", FindingKind.CHECK, "appearance_differs", about_mara)
    # Tobin's eyes, in a finding whose message mentions Mara: not about her.
    assert about_tobin == "Tobin eyes blue"
    assert scoping.covering("pln_a", FindingKind.CHECK, "appearance_differs", about_tobin) is None
    assert scoping.about(about_tobin, ["ent_mara", "ent_tobin"]) == [("ent_tobin", "Tobin Ashe")]
    # By an alias too, as whole words only.
    assert scoping.covering("pln_a", FindingKind.CHECK, "appearance_differs", "the fox eyes")
    assert not scoping.covering("pln_a", FindingKind.CHECK, "appearance_differs", "Marat eyes")


def test_a_note_dismissed_for_a_character_covers_only_notes_naming_them_alone() -> None:
    scoping = _for_mara(FindingKind.NOTE, "voice")
    assert scoping.covering("pln_a", FindingKind.NOTE, "voice", "Mara sounds unlike herself.")
    both = "Tobin answers Mara in words that sound like his own."
    assert scoping.covering("pln_a", FindingKind.NOTE, "voice", both) is None
    assert scoping.about(both, ["ent_mara", "ent_tobin"], FindingKind.NOTE) == []


def test_whom_a_finding_is_about_is_read_in_one_pass_over_many_characters() -> None:
    names = {f"ent_{index}": (f"Person{index} Surname{index}",) for index in range(2000)}
    scoping = Scoping(standing=[], position={}, names=names)
    words = " ".join(f"Person{index % 2000} Surname{index % 2000} waited." for index in range(500))
    started = time.perf_counter()
    for _ in range(200):
        scoping.about_ids(FindingKind.CHECK, words)
    # One regular expression for all 2,000 characters, built once: a note is about a
    # character only when it names no one else, so every name is looked for.
    assert time.perf_counter() - started < 3.0
    assert len(scoping.about_ids(FindingKind.CHECK, words)) == 500


def test_only_the_studios_own_codes_can_be_dismissed_wider() -> None:
    assert not is_studio_code("made_up")
    assert not is_studio_code("continuity_")
    assert is_studio_code("continuity_two_places_at_once")
    # Every code the studio's checks give is one.
    drafting = Path(__file__).resolve().parents[2] / "backend" / "domain" / "drafting.py"
    source = drafting.read_text(encoding="utf-8")
    codes = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "DraftFinding"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            codes.add(node.args[0].value)
    assert codes, "no codes found"
    assert sorted(code for code in codes if not is_studio_code(code)) == []


def test_two_notes_on_a_whole_scene_of_one_kind_have_their_own_keys() -> None:
    one = voice_fingerprint("voice_person_mixed", "", "“I” turns up in the narration.", "")
    other = voice_fingerprint("voice_person_mixed", "", "“We” turns up in the narration.", "")
    assert one != other
    # Only its numbers moving: the same note, so a dismissal of it still holds.
    assert voice_fingerprint(
        "voice_dialogue", "more", "About 40% of this scene is dialogue.", ""
    ) == voice_fingerprint("voice_dialogue", "more", "About 46% of this scene is dialogue.", "")
    # A note on a passage is known by its sentence, as before.
    assert voice_fingerprint("voice_echoes", "", "One message.", "A sentence.") == (
        voice_fingerprint("voice_echoes", "", "Another message.", "A sentence.")
    )
