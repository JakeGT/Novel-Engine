"""Archive CLI: export/import a canonical project archive (format v3).

Operator tool, not a request path. Export requires owner-level authorization in a
hosted deployment (enforced by the caller/operator policy, not this CLI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from backend.container import build_container


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.devtools.archive")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--export", metavar="PATH")
    parser.add_argument("--import", dest="import_path", metavar="PATH")
    parser.add_argument("--target-workspace")
    parser.add_argument("--target-project")
    return parser


async def _run(args: argparse.Namespace) -> int:
    container = build_container()
    try:
        if args.export:
            workspace = args.workspace or container.settings.AUTH_LOCAL_WORKSPACE_ID
            project = args.project or container.settings.AUTH_LOCAL_PROJECT_ID
            archive = await container.archive_service.export_project(workspace, project)
            with open(args.export, "w", encoding="utf-8") as handle:
                json.dump(archive, handle, indent=2, sort_keys=True)
            print(f"exported {len(archive['commits'])} commit(s) to {args.export}")
            return 0
        if args.import_path:
            if not args.target_workspace or not args.target_project:
                print("--target-workspace and --target-project are required for import")
                return 2
            with open(args.import_path, encoding="utf-8") as handle:
                archive = json.load(handle)
            result = await container.archive_service.import_project(
                args.target_workspace, args.target_project, archive
            )
            print(f"imported {result['commits']} commit(s), {result['objects']} object(s)")
            return 0
    finally:
        await container.aclose()
    print("nothing to do: pass --export PATH or --import PATH")
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
