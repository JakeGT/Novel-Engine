# Security

## Reporting a problem

Please report security problems privately through GitHub's private vulnerability reporting:
the **Security** tab of this repository, then **Report a vulnerability**. Do not open a public
issue for them.

Say what you found, how to reproduce it, and what someone could do with it. You should hear back
within a week. Fixes are made on a branch and released before the problem is described publicly.

## What counts

Anything that lets one person see or change another person's stories, workspaces or keys; anything
that puts an author's words, a provider key or a session somewhere it should not be (logs, error
reports, the browser of someone else); a way to make the assistant change a story without the
author's approval; anything that gets around sign-in or invitations on the hosted profile; a way
for an access key (MCP) to read more than its story or do more than suggest; a way for a connector
credential to reach anything but its own computer's requests; and a picture that escapes the
decoding worker.

## How the project protects authors

- Hosted sign-in is delegated to OpenID Connect; access to each workspace and story is checked on
  every request and again before background work runs.
- Provider keys are stored envelope-encrypted and never returned by the API; the key that protects
  them can be rotated (`make rekey`).
- Access keys for connected assistants and connector credentials are stored only as digests, shown
  once, expire, and stop working when withdrawn or when their holder loses access; their prefixes
  (`nvlpat_`, `nvldev_`) are in the secret scan.
- Pictures are decoded in a separate, resource-limited process that shuts itself in (Landlock and
  seccomp where the machine allows) and lose their location and camera metadata on upload.
- Logs and error reports carry counts, timings and kinds of error, never an author's words; the
  database engine hides the values in failed statements.
- Nothing a model proposes changes a story until the author approves it, or a rule the author
  switched on keeps it (opt-in, additive changes only, never for connected assistants).
- `make check` runs a secret scan; CI runs `pip-audit` and `pnpm audit` on dependencies.

See `plan/07-security-and-operations.md` for the full set of controls.
