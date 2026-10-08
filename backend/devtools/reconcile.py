"""Reconcile an interrupted canon commit from durable state (ADR-011).

Finishes or classifies a prepared / graph-applied / finalization-failed commit.
Callable before any worker exists.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from backend.container import build_container
from backend.core.config import settings as default_settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.reconcile", description="Reconcile a canon commit."
    )
    parser.add_argument("commit_id", help="Commit id to reconcile.")
    args = parser.parse_args(argv)

    async def _run() -> int:
        container = build_container(default_settings)
        try:
            result = await container.mutation_service.reconcile(args.commit_id)
        finally:
            await container.aclose()
        detail = f" (revision {result.revision})" if result.revision is not None else ""
        print(f"reconcile {args.commit_id}: {result.status}{detail}")
        return 0 if result.status == "committed" else 1

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
