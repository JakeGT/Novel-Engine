"""What each check that needs no model reads, as fingerprints (CON-007, CON-001).

A scene's checks that need no model (``backend.domain.drafting`` and the continuity
checks) are split here into named checks, each with the inputs it reads:

* ``prose``: the words checked, exactly (offsets depend on every character);
* ``names``: who each phrase means in this scene (names, aliases, the author's answers);
* ``secrets``, ``knowledge``, ``reveals``, ``cast``, ``length``: the slice of the story
  bible and the plan that one check reads, as the studio's brief gives it;
* ``brief``: the whole brief, for the checks this index does not know by name;
* ``timeline``: for continuity, which reads the whole of story time: the line of the story
  and its version, the travel table and the story's clock.

Each input is a SHA-256 digest; a check's fingerprint is a digest of the version of the
checks, the check's name and the digests of the inputs it reads. The same fingerprint
means the same answer, so a result can be kept and used again (a "constructive trace",
Mokhov, Mitchell and Peyton Jones, *Build systems à la carte*, ICFP 2018). When an input
changes, exactly the checks that read it are worked out again; the others are reused.

A digest never lets the words be read back, and nothing here stores them.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# The code the checks that need no model run, under ``backend/``: the checks of the words
# (``drafting``, with its list of stock phrases, and the appearance checks it calls), how
# names are found, the continuity checks and what they read of story time, and how a
# scene's checks are split into named checks here and run one by one.
CHECK_SOURCES: tuple[str, ...] = (
    "domain/appearance.py",
    "domain/check_fingerprints.py",
    "domain/continuity.py",
    "domain/drafting.py",
    "domain/knowledge.py",
    "domain/plan_checks.py",
    "domain/sources.py",
    "domain/worldtime.py",
    "services/continuity_service.py",
    "services/incremental_checks.py",
    "services/story_time_service.py",
)
_BACKEND = Path(__file__).resolve().parent.parent


def checks_version(sources: Iterable[tuple[str, bytes]]) -> str:
    """The version of the checks: a SHA-256 digest of the code they run, file by file, so
    any change to it is a new version and no result kept by older code is used again."""
    digest = hashlib.sha256()
    for name, raw in sorted(sources):
        digest.update(name.encode("utf-8") + b"\x00" + hashlib.sha256(raw).digest())
    return f"scene-checks/{digest.hexdigest()[:16]}"


def _source(name: str) -> bytes:
    try:
        return (_BACKEND / name).read_bytes()
    except OSError:
        # Run without its source (only compiled files): the name still counts.
        return b"missing"


# Worked out once, as the app starts: the same code always gives the same version.
CHECKS_VERSION = checks_version((name, _source(name)) for name in CHECK_SOURCES)


@dataclass(frozen=True)
class SceneCheck:
    name: str
    # The finding codes it gives; a prefix ends in "*".
    codes: tuple[str, ...]
    reads: tuple[str, ...]
    # What it is called where the author sees it.
    words: str

    def owns(self, code: str) -> bool:
        return any(
            code.startswith(item[:-1]) if item.endswith("*") else code == item
            for item in self.codes
        )


# In the order ``check_draft`` gives its findings, so findings put together from the
# separate checks come out exactly as one run of every check would give them.
SCENE_CHECKS: tuple[SceneCheck, ...] = (
    SceneCheck("names", ("new_name",), ("prose", "names"), "New names"),
    SceneCheck(
        "secrets",
        ("secret", "secret_denied"),
        ("prose", "names", "secrets"),
        "Secrets the viewpoint character does not know",
    ),
    SceneCheck(
        "knowledge",
        (
            "learned_later",
            "learned_later_planned",
            "learned_later_said",
            "learned_later_context",
        ),
        ("prose", "names", "knowledge"),
        "Something used before it is learned",
    ),
    SceneCheck(
        "reveals",
        ("reveal_missing",),
        ("prose", "names", "reveals"),
        "What the plan says the reader learns",
    ),
    SceneCheck(
        "presence",
        ("place_missing", "viewpoint_missing"),
        ("prose", "names", "cast"),
        "The place and the viewpoint named",
    ),
    SceneCheck("stock", ("stock_phrase",), ("prose",), "Stock phrasing"),
    SceneCheck("length", ("short", "long"), ("prose", "length"), "Length"),
    SceneCheck("continuity", ("continuity_*",), ("timeline",), "Continuity in story time"),
)
# Any finding no named check claims (a check added to ``drafting`` and not yet listed
# here) belongs to this one, which reads everything, so nothing new is ever lost.
OTHER = SceneCheck("other", (), ("prose", "names", "brief"), "Other checks of the words")
ALL_CHECKS: tuple[SceneCheck, ...] = (*SCENE_CHECKS, OTHER)
BY_NAME: dict[str, SceneCheck] = {check.name: check for check in ALL_CHECKS}

# Plain words for a finding's code, for the calibration note.
CODE_WORDS: dict[str, str] = {
    "new_name": "A new name",
    "secret": "A secret given away",
    "secret_denied": "A secret named to deny it",
    "learned_later": "Something used before it is learned",
    "learned_later_planned": "The plan shows something before it is learned",
    "learned_later_said": "Someone says something before they learn it",
    "learned_later_context": "Drafting would give something before it is learned",
    "reveal_missing": "A reveal the plan promised may be missing",
    "place_missing": "The place is never named",
    "viewpoint_missing": "The viewpoint character is never named",
    "stock_phrase": "Stock phrasing",
    "short": "Shorter than asked for",
    "long": "Longer than asked for",
}


def check_for(code: str) -> SceneCheck:
    """The named check a finding's code belongs to."""
    return next((check for check in SCENE_CHECKS if check.owns(code)), OTHER)


def code_words(code: str) -> str:
    if code in CODE_WORDS:
        return CODE_WORDS[code]
    if code.startswith("continuity_"):
        return "Continuity: " + code.removeprefix("continuity_").replace("_", " ")
    return code.replace("_", " ").capitalize()


# ------------------------------------------------------------------ digests


def _plain(value: Any) -> Any:
    """A value as plain JSON data, in a stable order."""
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _plain(item) for key, item in sorted(value.items(), key=lambda i: str(i[0]))
        }
    if isinstance(value, set | frozenset):
        return sorted((_plain(item) for item in value), key=lambda item: json.dumps(item))
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _plain(value.value)
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if hasattr(value, "__dict__"):
        return {"__type": type(value).__name__, **_plain(vars(value))}
    if hasattr(value, "__slots__"):
        return {name: _plain(getattr(value, name, None)) for name in value.__slots__}
    return str(value)


def digest_of(value: Any) -> str:
    """A SHA-256 digest of any plain value, the same for the same value every time."""
    raw = json.dumps(_plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prose_fingerprint(content: str) -> str:
    """The words exactly (normalised to one Unicode form): any other change counts."""
    return hashlib.sha256(unicodedata.normalize("NFC", content).encode("utf-8")).hexdigest()


def names_fingerprint(index: Any) -> str:
    """Who each phrase means where the text is read (``NameIndex``)."""
    return digest_of(
        {
            "answers": getattr(index, "answers", {}),
            "ruled_out": getattr(index, "ruled_out", {}),
            "ignored": getattr(index, "ignored", set()),
            "labels": getattr(index, "labels", {}),
        }
    )


def brief_inputs(brief: Any) -> dict[str, str]:
    """The slices of the studio's brief (``DraftBrief``) each named check reads.

    ``also`` (other phrases meaning each name) is part of every slice whose check looks
    for names; the continuity findings are not part of the brief here (see ``timeline``).
    """
    also = getattr(brief, "also", {})
    ids = getattr(brief, "ids", {})
    viewpoint = (brief.point_of_view, brief.point_of_view_names)
    whole = {
        key: value
        for key, value in (
            asdict(brief) if is_dataclass(brief) and not isinstance(brief, type) else {}
        ).items()
        if key != "continuity"
    }
    return {
        "secrets": digest_of({"secrets": brief.secrets, "viewpoint": viewpoint, "also": also}),
        "knowledge": digest_of({"later": brief.learned_later, "also": also}),
        "reveals": digest_of({"reveals": brief.reveals, "also": also}),
        "cast": digest_of(
            {
                "viewpoint": viewpoint,
                "place": (brief.place, brief.place_names),
                "also": also,
                "ids": ids,
            }
        ),
        "length": digest_of(brief.words_asked),
        "brief": digest_of(whole),
    }


def timeline_fingerprint(*, branch_id: str, head: int, scene: Any, unit: str, how_long: Any) -> str:
    """What the continuity checks read: the whole of story time in one line of the story
    at one version, the scene's own card, and the story's clock and travel table."""
    return digest_of(
        {
            "branch": branch_id,
            "head": head,
            "scene": {
                "id": getattr(scene, "id", ""),
                "details": getattr(scene, "details", {}),
                "refs": [
                    (getattr(ref, "entity_id", ""), getattr(ref, "role", ""))
                    for ref in getattr(scene, "refs", [])
                ],
            },
            "unit": unit,
            "how_long": how_long,
        }
    )


def check_fingerprints(inputs: Mapping[str, str]) -> dict[str, str]:
    """One fingerprint per check, from the version of the checks and what it reads."""
    return {
        check.name: digest_of(
            [CHECKS_VERSION, check.name, [(name, inputs.get(name, "")) for name in check.reads]]
        )
        for check in ALL_CHECKS
    }


def changed_checks(before: Mapping[str, str], now: Mapping[str, str]) -> set[str]:
    """The checks whose fingerprint differs (or that had none before)."""
    return {name for name, value in now.items() if before.get(name) != value}


def rules_fingerprint(rules: Iterable[Any]) -> str:
    """The story rules as they stand: each rule and its version, its status and whether
    it warns or blocks."""
    return digest_of(
        sorted(
            (
                str(getattr(rule, "id", "")),
                int(getattr(rule, "version", 0) or 0),
                str(getattr(getattr(rule, "status", ""), "value", getattr(rule, "status", ""))),
                str(
                    getattr(
                        getattr(rule, "enforcement", ""),
                        "value",
                        getattr(rule, "enforcement", ""),
                    )
                ),
            )
            for rule in rules
        )
    )


def finding_dependency(code: str, subject: str, message: str) -> str:
    """What a finding depended on, as a digest of what it said: its message is worked out
    from the facts, times, places and people it read, so when those change in a way that
    matters to it, so does this (CON-016)."""
    return digest_of(["finding-dependency/1", code, subject, " ".join(message.split())])


__all__ = [
    "ALL_CHECKS",
    "BY_NAME",
    "CHECKS_VERSION",
    "CHECK_SOURCES",
    "CODE_WORDS",
    "OTHER",
    "SCENE_CHECKS",
    "SceneCheck",
    "brief_inputs",
    "changed_checks",
    "check_fingerprints",
    "check_for",
    "checks_version",
    "code_words",
    "digest_of",
    "finding_dependency",
    "names_fingerprint",
    "prose_fingerprint",
    "rules_fingerprint",
    "timeline_fingerprint",
]
