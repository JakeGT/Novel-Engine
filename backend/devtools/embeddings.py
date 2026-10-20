"""Build, switch and roll back the search index by meaning (CTX-012).

    python -m backend.devtools.embeddings status
    python -m backend.devtools.embeddings build [--from-texts] [--activate] [--resume]
    python -m backend.devtools.embeddings activate [--generation ID] [--force]
    python -m backend.devtools.embeddings rollback [--force]
    python -m backend.devtools.embeddings cancel
    python -m backend.devtools.embeddings discard --generation ID
    python -m backend.devtools.embeddings refresh   # catch the index in use up

A build makes a new generation with the model set for this server (EMBEDDING_PROVIDER
and its settings) beside the one in use, fills it project by project and leaves it
waiting; ``activate`` switches searching to it in one step once nothing is missing, and
``rollback`` switches back to the one used before. A build can be stopped with
``cancel`` from another terminal (it notices between batches and removes what it made).
Prints counts and model names only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import asdict

from backend.container import build_container
from backend.core.config import settings as default_settings
from backend.domain.embedding import EmbeddingError


def _print(label: str, value: object) -> None:
    print(f"{label}: {json.dumps(value, sort_keys=True, default=str)}")


async def _run(args: argparse.Namespace) -> int:
    container = build_container(default_settings)
    service = container.embedding_index_service
    try:
        if args.command == "status":
            status = await service.status()
            _print("configured", status.configured)
            _print("active", status.active)
            _print("active_lag", status.lag)
            _print("building", status.building)
            _print("building_lag", status.building_lag)
            _print("retired", [g["id"] for g in status.retired])
            print(
                f"searchable: {status.searchable}"
                + (f" ({status.reason})" if status.reason else "")
            )
            return 0 if status.searchable else 1
        if args.command in ("build", "refresh"):
            projects = [(args.workspace, args.project)] if args.project else None
            if args.command == "refresh":
                active = await asyncio.to_thread(container.embedding_index.active)
                if active is None:
                    print("refresh: there is no search index in use; run build first")
                    return 1
                generation = active
            elif args.resume:
                generation = await asyncio.to_thread(container.embedding_index.building)
                if generation is None:
                    print("build: nothing is being built; run build without --resume")
                    return 1
            else:
                generation = await service.start_build(note=args.note)
            print(f"{args.command}: generation={generation.id} model={generation.model}")
            report = await service.build_all(
                generation.id, from_texts=args.from_texts, projects=projects
            )
            _print("built", asdict(report))
            if args.command == "build" and args.activate:
                activated = await service.activate(generation.id)
                print(f"activate: generation={activated.id}")
            return 0
        if args.command == "activate":
            generation_id = args.generation
            if not generation_id:
                building = await asyncio.to_thread(container.embedding_index.building)
                if building is None:
                    print("activate: nothing is waiting; name one with --generation")
                    return 1
                generation_id = building.id
            activated = await service.activate(generation_id, force=args.force)
            print(
                f"activate: generation={activated.id} previous={activated.previous_generation_id}"
            )
            return 0
        if args.command == "rollback":
            restored = await service.rollback(force=args.force)
            print(f"rollback: generation={restored.id} model={restored.model}")
            status = await service.status(with_lag=False)
            if not status.searchable and status.reason:
                print(f"note: {status.reason}")
            return 0
        if args.command == "cancel":
            asked = await service.cancel(args.generation)
            print("cancel: asked to stop" if asked else "cancel: nothing is being built")
            return 0 if asked else 1
        if args.command == "discard":
            removed = await service.discard(args.generation)
            print(f"discard: rows={removed}")
            return 0
        return 2
    except EmbeddingError as error:
        print(f"{args.command}: {error}")
        return 1
    finally:
        await container.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.embeddings",
        description="Build, switch and roll back the search index by meaning.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Show the index in use, any build, and what is missing.")
    for name, text in (
        ("build", "Build a new index with the configured model beside the one in use."),
        ("refresh", "Bring the index in use up to date (new pictures, captions)."),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("--from-texts", action="store_true", help="Cut passages again.")
        command.add_argument("--workspace", default="")
        command.add_argument("--project", default="", help="Only this project.")
        if name == "build":
            command.add_argument("--activate", action="store_true", help="Switch when done.")
            command.add_argument("--resume", action="store_true", help="Continue the build.")
            command.add_argument("--note", default=None)
    activate = sub.add_parser("activate", help="Switch searching to a built index.")
    activate.add_argument("--generation", default="")
    activate.add_argument(
        "--force", action="store_true", help="Switch even if rows are missing or the model differs."
    )
    rollback = sub.add_parser("rollback", help="Switch back to the index used before.")
    rollback.add_argument(
        "--force", action="store_true", help="Switch back even if the earlier one is missing rows."
    )
    cancel = sub.add_parser("cancel", help="Ask a running build to stop.")
    cancel.add_argument("--generation", default=None)
    discard = sub.add_parser("discard", help="Remove an index that is not in use.")
    discard.add_argument("--generation", required=True)
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
