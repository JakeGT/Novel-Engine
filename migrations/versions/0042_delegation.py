"""keep the safe ones for me: opt-in delegated approval

Revision ID: 0042_delegation
Revises: 0041_claims
Create Date: 2026-10-18

AST-EXP-015 / OSS-007. ``delegation_settings`` holds, per story, which kinds of the
assistant's suggestions are kept without asking (``rules``: kind -> when and by whom it
was switched on) and whether suggestions from connected programs may be kept
(``allow_clients``, off). The row whose ``project_id`` is empty is the workspace's own:
``forbidden`` there turns the feature off for every story in the workspace.

``delegation_records`` says, per suggestion, how it was decided: ``by_hand`` (a person
approved it; counted per kind, so a kind can only be switched on after it has been
approved by hand often enough), ``kept`` (kept under a rule: ``pending`` while it
commits, then ``kept``) or ``waited`` (considered and left for the author, with the
reason in plain words). The commit transaction reads a ``pending`` row and refuses the
commit unless the rule is still on. ``delegation_seen`` keeps when each person last
looked at what was kept.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_delegation"
down_revision: str | None = "0041_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delegation_settings (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            rules JSONB NOT NULL DEFAULT '{}'::jsonb,
            allow_clients BOOLEAN NOT NULL DEFAULT false,
            forbidden BOOLEAN NOT NULL DEFAULT false,
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delegation_records (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            how TEXT NOT NULL CHECK (how IN ('by_hand', 'kept', 'waited')),
            state TEXT NOT NULL CHECK (state IN ('counted', 'pending', 'kept', 'waiting')),
            via TEXT NOT NULL DEFAULT 'session',
            reason_code TEXT,
            reason TEXT,
            kinds TEXT[] NOT NULL DEFAULT '{}',
            branch_id TEXT NOT NULL DEFAULT '',
            actor_user_id TEXT,
            revision INTEGER,
            undo_mutation_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, mutation_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS delegation_records_kept_idx "
        "ON delegation_records (workspace_id, project_id, how, created_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS delegation_seen (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, user_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS delegation_seen")
    op.execute("DROP TABLE IF EXISTS delegation_records")
    op.execute("DROP TABLE IF EXISTS delegation_settings")
