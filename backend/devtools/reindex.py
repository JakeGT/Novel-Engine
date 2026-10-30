"""Rebuild the index of an author's writing (ADR-021).

The passage index is derived: it can be dropped and rebuilt from the texts at any
time without touching the story. This finds names again with the story as it is now.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from backend.container import build_container
from backend.core.config import settings as default_settings
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID


async def _run(workspace_id: str, project_id: str, status_only: bool) -> int:
    container = build_container(default_settings)
    try:
        if not status_only:
            count = await container.passage_index.rebuild(workspace_id, project_id)
            print(f"rebuild: passages={count}")
        status = await container.passage_index.status(workspace_id, project_id)
        print(
            f"index: texts={status.texts} indexed={status.indexed} behind={len(status.behind)} "
            f"names_as_of={status.names_as_of} semantic={status.semantic}"
        )
        return 0 if not status.behind else 1
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.reindex",
        description="Rebuild the index of the author's writing for one project.",
    )
    parser.add_argument("--workspace", default=SEED_WORKSPACE_ID)
    parser.add_argument("--project", default=SEED_PROJECT_ID)
    parser.add_argument("--status", action="store_true", help="Only report; change nothing.")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.workspace, args.project, args.status))


if __name__ == "__main__":
    raise SystemExit(main())
