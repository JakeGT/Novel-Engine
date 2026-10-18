"""Stored snapshots of every prompt, and the rule that wording never changes under one version.

Each listed prompt (``backend/domain/prompts.py``) is built from the fixed input in
``cases.py`` and written as plain text to ``__snapshots__/<id>.txt`` with its version, so a
change shows in review as a readable diff. ``__snapshots__/approved.txt`` records a
fingerprint of every version ever approved, one line each; a version's wording, once
approved, cannot be approved again with different words.

    uv run python -m tests.prompts.snapshots --update   # after raising a prompt's version
    uv run python -m tests.prompts.snapshots            # report only

Updating refuses a prompt whose wording changed while its version did not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.domain.prompts import PROMPTS, PromptSpec

SNAPSHOT_DIR = Path(__file__).parent / "__snapshots__"
LEDGER = "approved.txt"
HEADER = "# Prompt snapshot. Change it only with: uv run python -m tests.prompts.snapshots --update"


class SnapshotRefused(RuntimeError):
    """An update that would change a prompt's wording without a new version."""


@dataclass(frozen=True)
class Stored:
    id: str
    version: int
    body: str


def render(spec: PromptSpec) -> str:
    """Every case of one prompt, built exactly as sent, and the answer shape asked for."""
    from tests.prompts.cases import CASES

    build = spec.build()
    parts: list[str] = []
    for name, kwargs in CASES[spec.id].items():
        parts.append(f"##### case: {name}\n")
        for message in build(**kwargs):
            parts.append(f"=== {message['role']} ===\n{message['content']}\n")
    model = spec.output_model()
    if model is not None:
        schema = json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False)
        parts.append(f"##### answer shape\n{schema}\n")
    return "\n".join(parts)


def fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def snapshot_text(prompt_id: str, version: int, body: str) -> str:
    return f"{HEADER}\nid: {prompt_id}\nversion: {version}\n\n{body}"


def parse(text: str) -> Stored:
    lines = text.split("\n", 4)
    if len(lines) < 5 or not lines[1].startswith("id: ") or not lines[2].startswith("version: "):
        raise ValueError("not a prompt snapshot")
    return Stored(id=lines[1][4:], version=int(lines[2][9:]), body=lines[4])


def read(directory: Path, prompt_id: str) -> Stored | None:
    path = directory / f"{prompt_id}.txt"
    return parse(path.read_text(encoding="utf-8")) if path.exists() else None


def read_ledger(directory: Path) -> dict[tuple[str, int], str]:
    path = directory / LEDGER
    approved: dict[tuple[str, int], str] = {}
    if not path.exists():
        return approved
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        prompt_id, version, digest = line.split()
        key = (prompt_id, int(version))
        if key in approved and approved[key] != digest:
            raise ValueError(
                f"{prompt_id} version {version} is approved twice with different words"
            )
        approved[key] = digest
    return approved


def problems(
    spec: PromptSpec, body: str, stored: Stored | None, approved: dict[tuple[str, int], str]
) -> list[str]:
    """What is wrong between a prompt as built now and its snapshot; empty when it matches."""
    name = spec.id
    if stored is None:
        return [f"{name} has no snapshot. Run the update."]
    found: list[str] = []
    if stored.id != name:
        found.append(f"{name}'s snapshot names {stored.id}.")
    if spec.version < stored.version:
        found.append(f"{name}'s version went down from {stored.version} to {spec.version}.")
    elif spec.version > stored.version:
        found.append(
            f"{name} is now version {spec.version}; its snapshot is still {stored.version}. Run the update."
        )
    elif stored.body != body:
        found.append(
            f"{name}'s wording changed but its version is still {spec.version}. Raise the "
            "version in backend/domain/prompts.py, then run the update."
        )
    digest = approved.get((name, stored.version))
    if digest is None:
        found.append(f"{name} version {stored.version} is not in {LEDGER}. Run the update.")
    elif digest != fingerprint(stored.body):
        found.append(
            f"{name} version {stored.version}'s snapshot differs from what was approved for "
            "that version. Approved wording never changes; raise the version instead."
        )
    return found


def update(spec: PromptSpec, body: str, directory: Path) -> str:
    """Write the snapshot for the prompt's current version. Refuses a change of wording
    under a version that was already approved, and a version that went down."""
    stored = read(directory, spec.id)
    approved = read_ledger(directory)
    key = (spec.id, spec.version)
    if stored is not None and spec.version < stored.version:
        raise SnapshotRefused(
            f"{spec.id}'s version went down from {stored.version} to {spec.version}."
        )
    if key in approved and approved[key] != fingerprint(body):
        raise SnapshotRefused(
            f"{spec.id} version {spec.version} was approved with different wording. Raise its "
            "version in backend/domain/prompts.py first."
        )
    if (
        stored is not None
        and stored.version == spec.version
        and stored.body == body
        and key in approved
    ):
        return "unchanged"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{spec.id}.txt").write_text(
        snapshot_text(spec.id, spec.version, body), encoding="utf-8"
    )
    if key not in approved:
        ledger = directory / LEDGER
        existing = (
            ledger.read_text(encoding="utf-8")
            if ledger.exists()
            else (
                "# Every approved prompt version: id, version, fingerprint of its snapshot.\n"
                "# Lines are only ever added.\n"
            )
        )
        ledger.write_text(
            existing + f"{spec.id} {spec.version} {fingerprint(body)}\n", encoding="utf-8"
        )
    return "written"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tests.prompts.snapshots", description=__doc__)
    parser.add_argument("--update", action="store_true", help="write snapshots for new versions")
    args = parser.parse_args(argv)
    failed = False
    for spec in PROMPTS.values():
        body = render(spec)
        if args.update:
            try:
                print(f"{spec.version_id}: {update(spec, body, SNAPSHOT_DIR)}")
            except SnapshotRefused as refused:
                print(f"{spec.version_id}: refused. {refused}")
                failed = True
            continue
        found = problems(spec, body, read(SNAPSHOT_DIR, spec.id), read_ledger(SNAPSHOT_DIR))
        print(f"{spec.version_id}: {'ok' if not found else ' '.join(found)}")
        failed = failed or bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
