"""Provision the first workspace for someone who has signed in.

A hosted deployment is invitation-only and (by default) nobody may create a
workspace through the API, so a brand-new site has no way in. This operator
command closes that gap without widening the request path: it gives an existing
user (someone who has already signed in through the identity provider once) a
workspace they own and a first project. Everyone else is then invited by that
owner in the normal way.

    python -m backend.devtools.provision --list-users
    python -m backend.devtools.provision --owner-email you@example.com \\
        --workspace-slug studio --workspace-name "Studio" \\
        --project-slug first-novel --project-name "First novel"

It is idempotent, never changes an existing membership, and writes a security
audit record.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.domain.identity import User
from backend.domain.tenancy import Role

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ProvisionError(RuntimeError):
    """The request cannot be carried out as given."""


@dataclass(frozen=True)
class ProvisionResult:
    user_id: str
    workspace_id: str
    project_id: str
    created_workspace: bool
    created_project: bool


def find_owner(users: Sequence[User], email: str) -> User:
    """Exactly one active user with this address; anything else is refused."""
    wanted = email.strip().casefold()
    matches = [user for user in users if user.is_active and (user.email or "").casefold() == wanted]
    if not matches:
        raise ProvisionError(
            "no active user has that email address; they must sign in once before "
            "a workspace can be provisioned for them"
        )
    if len(matches) > 1:
        raise ProvisionError("more than one user has that email address; provision by --owner-id")
    return matches[0]


async def provision(
    tenancy: Any,
    owner: User,
    *,
    workspace_slug: str,
    workspace_name: str,
    project_slug: str,
    project_name: str,
) -> ProvisionResult:
    for slug in (workspace_slug, project_slug):
        if not _SLUG.match(slug) or len(slug) > 60:
            raise ProvisionError(f"invalid slug {slug!r}: use lowercase letters, digits, hyphens")

    workspace = await tenancy.get_workspace_by_slug(workspace_slug)
    created_workspace = workspace is None
    if workspace is None:
        workspace = await tenancy.create_workspace(slug=workspace_slug, name=workspace_name)
        await tenancy.add_membership(workspace_id=workspace.id, user_id=owner.id, role=Role.OWNER)
    else:
        membership = await tenancy.get_membership(workspace.id, owner.id)
        if membership is None or membership.role is not Role.OWNER:
            # Never grant access to a workspace that already belongs to someone else.
            raise ProvisionError("that workspace slug already exists and this user does not own it")

    project = next(
        (item for item in await tenancy.list_projects(workspace.id) if item.slug == project_slug),
        None,
    )
    created_project = project is None
    if project is None:
        project = await tenancy.create_project(
            workspace_id=workspace.id, slug=project_slug, name=project_name
        )

    if created_workspace or created_project:
        await tenancy.record_audit(
            workspace_id=workspace.id,
            actor_user_id="operator:cli",
            action="workspace.provisioned",
            target_type="workspace",
            target_id=workspace.id,
            detail={
                "owner_user_id": owner.id,
                "project_id": project.id,
                "created_workspace": created_workspace,
                "created_project": created_project,
            },
            correlation_id="cli:provision",
        )
    return ProvisionResult(
        user_id=owner.id,
        workspace_id=workspace.id,
        project_id=project.id,
        created_workspace=created_workspace,
        created_project=created_project,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backend.devtools.provision")
    parser.add_argument("--list-users", action="store_true")
    parser.add_argument("--owner-email")
    parser.add_argument("--owner-id")
    parser.add_argument("--workspace-slug")
    parser.add_argument("--workspace-name")
    parser.add_argument("--project-slug")
    parser.add_argument("--project-name")
    return parser


async def _run(args: argparse.Namespace) -> int:
    from backend.adapters.identity_store import PostgresIdentityStore
    from backend.adapters.tenancy_store import PostgresTenancyStore
    from backend.core.config import settings
    from backend.core.database import create_engine, create_session_factory

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    identities = PostgresIdentityStore(factory)
    try:
        users = await identities.list_users()
        if args.list_users:
            if not users:
                print("no one has signed in yet")
            for user in users:
                state = "active" if user.is_active else "inactive"
                print(f"{user.id}  {user.email or '-'}  {user.display_name or '-'}  {state}")
            return 0
        required = (
            args.workspace_slug,
            args.workspace_name,
            args.project_slug,
            args.project_name,
        )
        if not all(required) or not (args.owner_email or args.owner_id):
            print(
                "an owner (--owner-email or --owner-id) and all four workspace/project options are required"
            )
            return 2
        try:
            if args.owner_id:
                owner = next((user for user in users if user.id == args.owner_id), None)
                if owner is None or not owner.is_active:
                    raise ProvisionError("no active user has that id")
            else:
                owner = find_owner(users, args.owner_email)
            result = await provision(
                PostgresTenancyStore(factory),
                owner,
                workspace_slug=args.workspace_slug,
                workspace_name=args.workspace_name,
                project_slug=args.project_slug,
                project_name=args.project_name,
            )
        except ProvisionError as error:
            print(f"refused: {error}")
            return 1
        print(
            f"owner {result.user_id}: workspace {result.workspace_id} "
            f"({'created' if result.created_workspace else 'already existed'}), "
            f"project {result.project_id} "
            f"({'created' if result.created_project else 'already existed'})"
        )
        return 0
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
