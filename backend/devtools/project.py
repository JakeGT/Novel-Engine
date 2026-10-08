"""Project committed canon envelopes into the graph projection (ADR-012).

Canonical truth lives in PostgreSQL; Neo4j is a rebuildable projection. This CLI
advances (or rebuilds) the graph projection and reports lag.

    uv run --locked python -m backend.devtools.project --lag
    uv run --locked python -m backend.devtools.project
    uv run --locked python -m backend.devtools.project --rebuild
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from backend.container import build_container
from backend.core.config import settings as default_settings
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID


async def _run(workspace: str, project: str, *, rebuild: bool, show_lag: bool) -> int:
    container = build_container(default_settings)
    try:
        branch = await container.journal.ensure_root_branch(
            workspace_id=workspace, project_id=project
        )
        projection = container.projection_service
        if show_lag:
            lag = await projection.lag(
                workspace_id=workspace, project_id=project, branch_id=branch.id
            )
            print(
                f"projection: head={lag.head_revision} applied={lag.applied_revision} "
                f"lag={lag.lag} status={lag.status}"
            )
            return 0
        if rebuild:
            report = await projection.rebuild(
                workspace_id=workspace, project_id=project, branch_id=branch.id
            )
            print(f"projection rebuilt: applied {report.applied} commits (status {report.status})")
            return 0 if report.error is None else 1
        report = await projection.project_pending(
            workspace_id=workspace, project_id=project, branch_id=branch.id
        )
        print(
            f"projection: applied {report.applied} commits "
            f"(revision {report.revision}, status {report.status})"
        )
        return 0 if report.error is None else 1
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.devtools.project")
    parser.add_argument("--workspace", default=SEED_WORKSPACE_ID)
    parser.add_argument("--project", default=SEED_PROJECT_ID)
    parser.add_argument("--rebuild", action="store_true", help="Discard and replay the projection.")
    parser.add_argument("--lag", action="store_true", help="Report projection lag only.")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.workspace, args.project, rebuild=args.rebuild, show_lag=args.lag))


if __name__ == "__main__":
    sys.exit(main())
