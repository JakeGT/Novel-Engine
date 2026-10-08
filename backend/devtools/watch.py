"""Bounded outbox -> graph projection runner (ADR-012).

Drains committed canonical outbox messages into the derived graph projection and
reports lag. This is a development/in-process runner; a worker can replace it
without changing the consumer contract. Canonical commits never depend on it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from backend.container import build_container
from backend.core.config import settings as default_settings
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID


async def _run(
    workspace_id: str, project_id: str, interval: float, limit: int, rebuild: bool
) -> int:
    container = build_container(default_settings)
    try:
        branch = await container.journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        if rebuild:
            report = await container.projection_service.rebuild(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
            )
            print(f"rebuild: applied={report.applied} status={report.status.value}")
        while True:
            result = await container.outbox_consumer.drain(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
                limit=limit,
            )
            print(
                f"drain: processed={result.processed} published={result.published} "
                f"failed={result.failed} lag={result.lag}"
            )
            if interval <= 0:
                return 0 if result.failed == 0 else 1
            await asyncio.sleep(interval)
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.watch",
        description="Drain the canonical outbox into the graph projection.",
    )
    parser.add_argument("--workspace", default=SEED_WORKSPACE_ID)
    parser.add_argument("--project", default=SEED_PROJECT_ID)
    parser.add_argument(
        "--interval", type=float, default=0.0, help="Seconds between drains (0 = run once)."
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--rebuild", action="store_true", help="Discard and replay the projection.")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.workspace, args.project, args.interval, args.limit, args.rebuild))


if __name__ == "__main__":
    sys.exit(main())
