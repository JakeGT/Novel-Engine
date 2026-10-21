# Deleting a story, a workspace, or a person's data

Deleting is done in the app by the people who own the data. This runbook is for the operator:
what each deletion removes, what stays (and for how long), and what to do when someone asks for
their data to be removed.

## What deletion removes

- **A story** (owner or administrator of the story: Delete on the story's settings, or
  `DELETE /api/v1/workspaces/<workspace>/projects/<project>`): its change log and every story
  record, its plan, texts and their versions, the passage index and search rows, pictures (the
  stored files and their copies), what the assistant kept (conversations, notes, readings,
  summaries), check runs, the story graph, access keys made for it, and its usage records. A
  `project.deleted` event stays in the workspace's audit log (the story's id and slug only).
- **A workspace** (its owner: `DELETE /api/v1/workspaces/<workspace>`): every story in it as
  above, plus its members, invitations, stored provider keys, usage records and allowances,
  assistant settings, connected computers and access keys. A `workspace.deleted` event is kept.
- **One picture's file**, before its story: [media-processing.md](media-processing.md)
  ("Deleting one picture's file before its story").
- **Discarded drafts** go at once; what the assistant left behind goes after `make retention`
  (see `backend/devtools/retention.py`). Nothing an author kept is removed by retention.

## What stays

- **Backups.** Each nightly backup holds everything as it was that night, deleted stories
  included, until `prune-backups.sh` removes it (14 kept by default; copies made off the host
  follow the other side's own retention). Tell the person how long that is.
- **The identity provider** keeps the sign-in account; removing it is done there (Authentik).
- **The user row** (`users`): an id, email and display name, so audit events stay readable.
- **Logs** never carry story text; they carry ids and counts.

## Someone asks for their data to be removed

1. Confirm who is asking (their sign-in email, from the identity provider) and what: one story,
   their whole workspace, or everything about them.
2. Ask them to delete it themselves where they can (story or workspace), so it is their own
   decision on record. If they cannot sign in any more, an operator acts for them only on a
   written request: find the ids (`README.md`, "Finding a story's ids") and call the same
   endpoint signed in as a platform administrator, or run the deletion with the story's owner
   present.
3. For a person who is only a member of other workspaces: an owner removes them from each
   (People page). Their sign-ins end:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "DELETE FROM sessions WHERE user_id = '<user id>';"
   ```
   Their name stays on the changes they made (the history of someone else's story is that
   story's owner's record); say so.
4. Write down the date, the request and what was deleted, and when the last backup that holds it
   will be pruned.

## How to confirm

- The story or workspace is gone from the app, and:
  ```sh
  docker compose exec -T postgres psql -U novel -d novel_engine -c \
    "SELECT count(*) FROM canon_revisions WHERE project_id = '<project id>';"
  ```
  prints 0 (and the same for `source_texts`, `media_assets` and `jobs`).
- The audit log has the `project.deleted` or `workspace.deleted` event.

Not built: removing a person's name from history in stories they do not own, and removing one
person's data from existing backups (a backup is kept or pruned whole).
