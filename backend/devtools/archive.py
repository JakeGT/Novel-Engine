"""Archive CLI: export, import, inspect, and upgrade project archives.

Operator tool, not a request path. The operator acts as ``--actor`` (a user id),
which is authorized like any other caller in a hosted deployment and recorded in
the security audit log together with counts and checksums, never content.

    --export PATH                       write a canonical archive (format v3)
    --import PATH                       restore one into --target-workspace/--target-project
    --imports                           show the target project's import ledger
    --remove-import ID                  erase the canon a failed import wrote
    --upgrade-graph PATH                file a Phase 1 graph archive as a review proposal
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from backend.container import build_container
from backend.domain.identity import AccessScope, AuthMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.devtools.archive")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--project", default=None)
    parser.add_argument("--export", metavar="PATH")
    parser.add_argument("--import", dest="import_path", metavar="PATH")
    parser.add_argument("--imports", action="store_true", help="List the import ledger.")
    parser.add_argument("--remove-import", metavar="IMPORT_ID")
    parser.add_argument("--upgrade-graph", metavar="PATH")
    parser.add_argument("--expected-revision", type=int, default=None)
    parser.add_argument("--target-workspace")
    parser.add_argument("--target-project")
    parser.add_argument("--actor", default=None, help="User id the operator acts as.")
    return parser


def _scope(container: Any, args: argparse.Namespace, workspace: str, project: str) -> AccessScope:
    settings = container.settings
    return AccessScope(
        actor_user_id=args.actor or settings.AUTH_LOCAL_USER_ID,
        auth_mode=AuthMode.HOSTED if settings.is_hosted else AuthMode.LOCAL,
        correlation_id="cli:archive",
        workspace_id=workspace,
        project_id=project,
    )


def _print_import(record: Any) -> None:
    print(
        f"{record.id}  {record.status.value:<10} stage={record.stage:<10} "
        f"commits={record.commits_applied}/{record.commits_total} "
        f"objects={record.objects_imported} error={record.error or '-'}"
    )


async def _run(args: argparse.Namespace) -> int:
    container = build_container()
    try:
        if args.export:
            workspace = args.workspace or container.settings.AUTH_LOCAL_WORKSPACE_ID
            project = args.project or container.settings.AUTH_LOCAL_PROJECT_ID
            archive = await container.archive_service.export_project_as(
                _scope(container, args, workspace, project), workspace, project
            )
            with open(args.export, "w", encoding="utf-8") as handle:
                json.dump(archive, handle, indent=2, sort_keys=True)
            print(f"exported {len(archive['commits'])} commit(s) to {args.export}")
            return 0

        target_actions = (
            args.import_path or args.imports or args.remove_import or args.upgrade_graph
        )
        if target_actions and not (args.target_workspace and args.target_project):
            print("--target-workspace and --target-project are required")
            return 2
        workspace, project = args.target_workspace, args.target_project
        if not target_actions:
            print("nothing to do: see --help")
            return 2
        scope = _scope(container, args, workspace, project)

        if args.import_path:
            with open(args.import_path, encoding="utf-8") as handle:
                archive = json.load(handle)
            try:
                result = await container.archive_service.import_project_as(
                    scope, workspace, project, archive
                )
            except Exception as error:
                print(f"import FAILED ({type(error).__name__}): {error}")
                print("the project is not a successful restore; retry or use --remove-import")
                return 1
            print(
                f"imported {result['commits']} commit(s), {result['objects']} object(s); "
                f"projection rebuilt: {result['projection_rebuilt']}; "
                f"verified: {result['verified']}; import id: {result['import_id']}"
            )
            return 0
        if args.imports:
            for record in await container.archive_service.list_imports_as(
                scope, workspace, project
            ):
                _print_import(record)
            return 0
        if args.remove_import:
            record = await container.archive_service.remove_import_as(
                scope, workspace, project, args.remove_import
            )
            _print_import(record)
            return 0

        with open(args.upgrade_graph, encoding="utf-8") as handle:
            legacy = json.load(handle)
        expected = args.expected_revision
        if expected is None:
            branch = await container.journal.ensure_root_branch(
                workspace_id=workspace, project_id=project
            )
            expected = await container.canon_repository.get_head(
                workspace_id=workspace, project_id=project, branch_id=branch.id
            )
        upgrade = await container.graph_upgrade_service.propose(
            scope, workspace, project, legacy, expected_revision=expected
        )
        print(f"filed proposal {upgrade.mutation.id} ({upgrade.plan.summary})")
        print("nothing was committed: review and approve it under /mutations before commit")
        for item in upgrade.plan.skipped:
            print(f"  skipped {item.kind} {item.reference}: {item.reason}")
        return 0
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
