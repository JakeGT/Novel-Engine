"""Retention purge for operational rows and assistant leftovers (OPS-005).

Two kinds of thing are removed:

* Expired access records: sessions, login attempts, unaccepted invitations and old
  rate-limit hits.
* What is left behind by work with the assistant once it no longer serves the author,
  after ``--days`` (default 30):

  - finished background work (its request holds the author's direction for a draft),
    unless a draft it wrote is still waiting to be kept or discarded;
  - the record of what the assistant was given for a request, when no version of a
    text and no waiting draft points at it;
  - the assistant's notes on a version of a scene that a newer version replaced
    (``--days`` ago or more);
  - and, at once, whatever is left of drafts already kept or discarded: the assistant's
    notes on them, and any words still stored on a kept draft (they live on in the text).

Never removed: a text or any of its versions, a draft still waiting, the record of what
was given for a version the author kept, a decision the author made (such as dismissing
a note), the story itself, or users and workspaces. ``--dry-run`` only counts.

Each rule runs on its own; a store migrated only part of the way counts what it lacks
as nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import psycopg
from psycopg.rows import tuple_row

from backend.core.config import settings as default_settings

DEFAULT_DAYS = 30
_CUTOFF = "now() - make_interval(days => %(days)s)"
_SAME_STORY = "{a}.workspace_id = t.workspace_id AND {a}.project_id = t.project_id"


def _same(alias: str) -> str:
    return _SAME_STORY.format(a=alias)


@dataclass(frozen=True)
class Rule:
    name: str
    label: str
    table: str
    where: str
    # "delete" removes the rows; "clear_words" empties a kept draft's stored words.
    action: str = "delete"

    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {self.table} t WHERE {self.where}"

    def apply_sql(self) -> str:
        if self.action == "clear_words":
            return f"UPDATE {self.table} t SET content = '' WHERE {self.where}"
        return f"DELETE FROM {self.table} t WHERE {self.where}"


EXPIRED_RULES: tuple[Rule, ...] = (
    Rule("sessions", "expired sign-ins", "sessions", "t.expires_at < now()"),
    Rule("login_attempts", "expired sign-in attempts", "login_attempts", "t.expires_at < now()"),
    Rule(
        "invitations",
        "expired invitations never accepted",
        "invitations",
        "t.expires_at < now() AND t.accepted_at IS NULL",
    ),
    Rule(
        "rate_limit_hits",
        "old request counts",
        "rate_limit_hits",
        "t.occurred_at < now() - interval '1 day'",
    ),
)

ASSISTANT_RULES: tuple[Rule, ...] = (
    Rule(
        "finished_jobs",
        "finished background work",
        "jobs",
        "t.status IN ('succeeded', 'failed', 'cancelled') "
        f"AND COALESCE(t.finished_at, t.updated_at) < {_CUTOFF} "
        "AND NOT EXISTS (SELECT 1 FROM scene_drafts d WHERE "
        f"{_same('d')} AND d.job_id = t.id AND d.status = 'proposed')",
    ),
    Rule(
        "unused_context_records",
        "records of what the assistant was given, no longer used",
        "context_manifests",
        f"t.created_at < {_CUTOFF} "
        "AND NOT EXISTS (SELECT 1 FROM source_versions v WHERE "
        f"{_same('v')} AND v.manifest_id = t.id) "
        "AND NOT EXISTS (SELECT 1 FROM scene_drafts d WHERE "
        f"{_same('d')} AND d.manifest_id = t.id AND d.status = 'proposed')",
    ),
    Rule(
        "notes_on_finished_drafts",
        "assistant notes on drafts already kept or discarded",
        "draft_findings",
        "t.target_kind = 'draft' AND NOT EXISTS (SELECT 1 FROM scene_drafts d WHERE "
        f"{_same('d')} AND d.id = t.target_id AND d.status = 'proposed')",
    ),
    Rule(
        "words_on_kept_drafts",
        "words still stored on kept drafts (they are in the text)",
        "scene_drafts",
        "t.status = 'kept' AND t.content <> ''",
        action="clear_words",
    ),
    Rule(
        "notes_on_replaced_versions",
        "assistant notes on versions a newer version replaced",
        "draft_findings",
        f"t.target_kind = 'version' AND t.created_at < {_CUTOFF} AND ("
        "NOT EXISTS (SELECT 1 FROM source_versions v WHERE "
        f"{_same('v')} AND v.id = t.target_id) "
        "OR EXISTS (SELECT 1 FROM source_versions v JOIN source_versions later "
        "ON later.workspace_id = v.workspace_id AND later.project_id = v.project_id "
        "AND later.source_id = v.source_id AND later.number > v.number "
        f"WHERE {_same('v')} AND v.id = t.target_id AND later.created_at < {_CUTOFF}))",
    ),
)

RULES: tuple[Rule, ...] = EXPIRED_RULES + ASSISTANT_RULES
LABELS = {rule.name: rule.label for rule in RULES}


def purge_expired(dsn: str, *, days: int = DEFAULT_DAYS, dry_run: bool = False) -> dict[str, int]:
    """Return rows removed (or, with ``dry_run``, that would be) per rule."""
    if days < 1:
        raise ValueError("days must be at least 1")
    counts: dict[str, int] = {}
    params = {"days": days}
    with psycopg.connect(dsn, autocommit=True, row_factory=tuple_row) as connection:
        for rule in RULES:
            try:
                if dry_run:
                    row = connection.execute(rule.count_sql(), params).fetchone()  # type: ignore[arg-type]
                    counts[rule.name] = int(row[0]) if row else 0
                else:
                    counts[rule.name] = connection.execute(rule.apply_sql(), params).rowcount  # type: ignore[arg-type]
            except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
                counts[rule.name] = 0
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backend.devtools.retention",
        description="Remove expired access records and what work with the assistant left behind.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"keep the assistant's leftovers this many days (default {DEFAULT_DAYS})",
    )
    parser.add_argument("--dry-run", action="store_true", help="only count; change nothing")
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be at least 1")
    counts = purge_expired(
        default_settings.postgres_dsn_psycopg, days=args.days, dry_run=args.dry_run
    )
    verb = "would remove" if args.dry_run else "removed"
    for name, count in counts.items():
        print(f"retention: {LABELS[name]}: {verb} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
