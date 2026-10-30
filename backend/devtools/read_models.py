"""The story's ready views, for whoever runs the server (QRY-001).

Commands (counts and ids only are printed; never a name or the author's words):

* ``status``   how many lines of the story have views, how many are waiting to be built,
               and each view's verification outcome counts.
* ``build``    build every line the outbox says has moved on, now, in this process (the
               running server's worker does the same every few seconds).
* ``verify``   rebuild every stored view from the record history and compare it with the
               stored one; a matching view is trusted and answers reads from then on.
               ``--repair`` forgets a line whose views differ so it is built afresh (it is
               trusted again only after a later verify matches). ``--project`` limits it
               to one story (``--workspace`` too in a hosted install). Exits 1 when any
               view differed.
* ``forget``   remove one story's views (``--workspace``/``--project``) so they are built
               again from scratch; reads work everything out meanwhile.

See ``plan/runbooks/story-views.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from backend.container import build_container
from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.domain.read_views import BUILDER_VERSION, VIEWS


async def status(settings: Settings) -> dict[str, Any]:
    container = build_container(settings)
    try:
        store = container.read_model_store
        lines = await store.lines()
        waiting = await store.pending(builder_version=BUILDER_VERSION, views=len(VIEWS), limit=1000)
        outcomes: dict[str, int] = {}
        for workspace_id, project_id, branch_id in lines:
            for view in await store.list_views(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            ):
                key = view.verify_outcome or "not verified"
                if view.builder_version != BUILDER_VERSION:
                    key = "built by another version"
                outcomes[key] = outcomes.get(key, 0) + 1
    finally:
        await container.aclose()
    return {
        "builder_version": BUILDER_VERSION,
        "lines_with_views": len(lines),
        "lines_waiting": len(waiting),
        "views": outcomes,
    }


async def build(settings: Settings, *, rounds: int = 100) -> dict[str, Any]:
    container = build_container(settings)
    built = 0
    try:
        for _ in range(rounds):
            done = await container.read_model_consumer.run_once()
            if not done:
                break
            built += done
    finally:
        await container.aclose()
    return {"lines_built": built}


async def verify(
    settings: Settings,
    *,
    repair: bool,
    workspace_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    container = build_container(settings)
    try:
        service = container.read_models
        stories = sorted({(w, p) for w, p, _b in await service.lines()})
        if project_id is not None:
            stories = [
                (w, p)
                for w, p in stories
                if p == project_id and (workspace_id is None or w == workspace_id)
            ]
        results = []
        for w, p in stories:
            results.extend(await service.verify(w, p, repair=repair))
    finally:
        await container.aclose()
    return {
        "checked": sum(1 for r in results if r.skipped is None),
        "matched": sum(1 for r in results if r.matched),
        "differed": sum(1 for r in results if r.skipped is None and not r.matched),
        "skipped": sum(1 for r in results if r.skipped is not None),
        "repaired": repair,
        "views": [
            {
                key: value
                for key, value in asdict(result).items()
                if key
                in (
                    "project_id",
                    "branch_id",
                    "view",
                    "revision",
                    "matched",
                    "differences",
                    "rows",
                    "skipped",
                )
            }
            for result in results
        ],
    }


async def forget(settings: Settings, *, workspace_id: str, project_id: str) -> dict[str, Any]:
    container = build_container(settings)
    try:
        removed = await container.read_models.forget(workspace_id, project_id)
    finally:
        await container.aclose()
    return {"views_removed": removed}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.read_models",
        description="The story's ready views: build, verify by replay, forget.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="how many lines have views, wait, and verified")
    sub.add_parser("build", help="build every line waiting, now")
    check = sub.add_parser("verify", help="compare each stored view with its replay")
    check.add_argument("--repair", action="store_true", help="forget lines that differ")
    check.add_argument("--workspace", default=None)
    check.add_argument("--project", default=None)
    drop = sub.add_parser("forget", help="remove one story's views so they are rebuilt")
    drop.add_argument("--workspace", default=None)
    drop.add_argument("--project", required=True)
    args = parser.parse_args(argv)

    settings = default_settings
    code = 0
    result: dict[str, Any]
    if args.command == "status":
        result = asyncio.run(status(settings))
    elif args.command == "build":
        result = asyncio.run(build(settings))
    elif args.command == "verify":
        result = asyncio.run(
            verify(
                settings, repair=args.repair, workspace_id=args.workspace, project_id=args.project
            )
        )
        code = 1 if result["differed"] else 0
    else:
        workspace = args.workspace or settings.AUTH_LOCAL_WORKSPACE_ID
        result = asyncio.run(forget(settings, workspace_id=workspace, project_id=args.project))
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
