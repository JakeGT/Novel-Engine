"""Picture operations for whoever runs the server (OPS-007, AST-008).

Commands (counts only are printed; never a picture, its name or a file key):

* ``status``     how many pictures wait to be prepared, the oldest wait, how many failed,
                 and whether this machine can prepare them (Pillow and its formats).
* ``backfill``   queue every current picture not yet prepared by this processor version
                 (pictures from before processing existed, or after the processor
                 changed); ``--include-failed`` queues failed ones again; ``--now``
                 prepares them in this process instead of queuing.
* ``reconcile``  find stored picture files that no picture refers to, older than the grace period
                 (``--grace-hours``, default ``MEDIA_ORPHAN_GRACE_HOURS``); ``--apply``
                 removes them.
* ``copies``     find the smaller copies of retired pictures; ``--apply`` removes them (the
                 originals stay; a copy can always be made again).

``make retention`` runs ``reconcile`` and ``copies`` too. See
``plan/runbooks/media-processing.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from backend.container import build_container
from backend.core.config import Settings
from backend.core.config import settings as default_settings


async def media_maintenance(
    settings: Settings, *, dry_run: bool, grace_hours: int | None = None
) -> dict[str, Any]:
    """Orphaned files and copies of retired pictures: counted, or removed."""
    container = build_container(settings)
    try:
        media = container.media_service
        grace = timedelta(hours=grace_hours) if grace_hours is not None else None
        orphans = await media.reconcile_orphans(dry_run=dry_run, grace=grace)
        copies = await media.remove_retired_copies(dry_run=dry_run)
    finally:
        await container.aclose()
    return {
        "orphans_found": orphans.orphans_found,
        "orphans_removed": orphans.orphans_removed,
        "orphan_bytes": orphans.orphan_bytes,
        "projects_checked": orphans.projects,
        "retired_copies": copies.retired_copies_removed,
    }


async def _status(settings: Settings) -> dict[str, Any]:
    from backend.devtools.diagnostics import picture_capability

    container = build_container(settings)
    try:
        lag = await container.media_service.processing_lag()
    finally:
        await container.aclose()
    return {"processing": lag, "capability": picture_capability(settings)}


async def _backfill(
    settings: Settings, *, include_failed: bool, now: bool, limit: int
) -> dict[str, Any]:
    container = build_container(settings)
    try:
        media = container.media_service
        if not now:
            report = await media.queue_backfill(
                limit=limit,
                include_failed=include_failed,
                local_actor=settings.AUTH_LOCAL_USER_ID,
            )
            return {"queued": report.queued, "skipped": report.skipped}
        from backend.adapters.image_processor import PROCESSOR_VERSION
        from backend.services.media_service import PICTURE_TYPES

        pending = await container.media_processing.pending_versions(
            processor_version=PROCESSOR_VERSION,
            content_types=sorted(PICTURE_TYPES),
            limit=limit,
            include_failed=include_failed,
        )
        outcomes: dict[str, int] = {}
        for item in pending:
            result = await media.process_version(
                item.workspace_id, item.project_id, item.asset_version_id
            )
            outcome = str(result.get("outcome"))
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        return {"prepared": outcomes}
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.media", description="Picture preparation and storage upkeep."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="waiting, failed, and whether pictures can be prepared here")
    backfill = sub.add_parser("backfill", help="queue pictures not yet prepared")
    backfill.add_argument("--include-failed", action="store_true")
    backfill.add_argument("--now", action="store_true", help="prepare here instead of queuing")
    backfill.add_argument("--limit", type=int, default=500)
    reconcile = sub.add_parser("reconcile", help="stored picture files no picture refers to")
    reconcile.add_argument("--apply", action="store_true", help="remove them (default: count)")
    reconcile.add_argument("--grace-hours", type=int, default=None)
    copies = sub.add_parser("copies", help="smaller copies of retired pictures")
    copies.add_argument("--apply", action="store_true", help="remove them (default: count)")
    args = parser.parse_args(argv)

    settings = default_settings
    result: dict[str, Any]
    if args.command == "status":
        result = asyncio.run(_status(settings))
    elif args.command == "backfill":
        result = asyncio.run(
            _backfill(settings, include_failed=args.include_failed, now=args.now, limit=args.limit)
        )
    elif args.command == "reconcile":
        if args.grace_hours is not None and args.grace_hours < 1:
            parser.error("--grace-hours must be at least 1")

        async def _reconcile() -> dict[str, Any]:
            container = build_container(settings)
            try:
                grace = timedelta(hours=args.grace_hours) if args.grace_hours else None
                report = await container.media_service.reconcile_orphans(
                    dry_run=not args.apply, grace=grace
                )
            finally:
                await container.aclose()
            return asdict(report)

        result = asyncio.run(_reconcile())
    else:

        async def _copies() -> dict[str, Any]:
            container = build_container(settings)
            try:
                report = await container.media_service.remove_retired_copies(dry_run=not args.apply)
            finally:
                await container.aclose()
            return asdict(report)

        result = asyncio.run(_copies())
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
