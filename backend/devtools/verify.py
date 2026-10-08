"""Canonical verification CLI (ADR-012).

Verifies that PostgreSQL canonical state is internally consistent: branch heads
match the highest committed revision, every envelope checksum recomputes from its
canonical payload, revision result heads equal their revision, and projection
watermarks do not exceed the head and are marked current only when caught up.

Exit status is non-zero when any inconsistency is found. This is an operator
tool; it never mutates canon.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text

from backend.core.config import settings
from backend.core.database import create_engine, create_session_factory, session_scope


def recompute_checksum(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate(
    branches: Sequence[Mapping[str, Any]],
    revisions: Sequence[Mapping[str, Any]],
    envelopes: Sequence[Mapping[str, Any]],
    watermarks: Sequence[Mapping[str, Any]],
) -> list[str]:
    problems: list[str] = []

    heads = {branch["id"]: branch["head_revision"] for branch in branches}
    max_revision: dict[str, int] = {branch_id: 0 for branch_id in heads}
    for revision in revisions:
        branch_id = revision["branch_id"]
        max_revision[branch_id] = max(max_revision.get(branch_id, 0), revision["revision"])
        if revision["result_head"] != revision["revision"]:
            problems.append(
                f"revision {revision['revision']} on {branch_id}: "
                f"result_head {revision['result_head']} != revision"
            )

    for branch_id, head in heads.items():
        if head != max_revision.get(branch_id, 0):
            problems.append(
                f"branch {branch_id}: head {head} != max committed revision "
                f"{max_revision.get(branch_id, 0)}"
            )

    for envelope in envelopes:
        actual = recompute_checksum(envelope["canonical"])
        if actual != envelope["checksum"]:
            problems.append(
                f"envelope {envelope['commit_id']}: checksum mismatch "
                f"(expected {envelope['checksum']}, recomputed {actual})"
            )

    for watermark in watermarks:
        branch_id = watermark["branch_id"]
        head = heads.get(branch_id)
        if head is None:
            problems.append(f"watermark references unknown branch {branch_id}")
            continue
        if watermark["revision"] > head:
            problems.append(
                f"watermark {watermark['projection']} on {branch_id}: "
                f"applied {watermark['revision']} > head {head}"
            )
        if watermark["status"] == "current" and watermark["revision"] != head:
            problems.append(
                f"watermark {watermark['projection']} on {branch_id}: "
                f"marked current at {watermark['revision']} but head is {head}"
            )

    return problems


async def _collect(session: Any) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    branches = [
        dict(row._mapping)
        for row in (await session.execute(text("SELECT id, head_revision FROM branches"))).all()
    ]
    revisions = [
        dict(row._mapping)
        for row in (
            await session.execute(
                text("SELECT branch_id, revision, result_head, commit_id FROM canon_revisions")
            )
        ).all()
    ]
    envelopes = [
        dict(row._mapping)
        for row in (
            await session.execute(
                text("SELECT commit_id, canonical, checksum FROM commit_envelopes")
            )
        ).all()
    ]
    watermarks = [
        dict(row._mapping)
        for row in (
            await session.execute(
                text("SELECT projection, branch_id, revision, status FROM projection_watermarks")
            )
        ).all()
    ]
    return branches, revisions, envelopes, watermarks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.verify", description="Verify canonical PostgreSQL state."
    )
    parser.parse_args(argv)

    engine = create_engine(settings)
    factory = create_session_factory(engine)

    async def _run() -> list[str]:
        try:
            async with session_scope(factory) as session:
                branches, revisions, envelopes, watermarks = await _collect(session)
            return evaluate(branches, revisions, envelopes, watermarks)
        finally:
            await engine.dispose()

    problems = asyncio.run(_run())

    if problems:
        print("canonical verification FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("canonical verification OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
