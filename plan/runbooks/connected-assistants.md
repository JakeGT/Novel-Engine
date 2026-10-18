# Connected assistants and access keys (QRY-003)

Other assistants (Claude Desktop, Claude Code, Cursor, ChatGPT and others that speak MCP) read a
story through `/mcp` with an access key that a member made under **People → Other assistants**.
This runbook covers connecting one, and what to do when a key leaks, an assistant is refused, or
something looks wrong. ADR-024 has the design.

## Connecting an assistant

1. Under **People**, in **Other assistants**, name the key after the assistant or computer that
   will use it, choose how long it works (90 days unless you choose), whether it reads only or may
   also suggest changes, and (hosted) one story or every story you can read in the workspace.
2. **Make access key.** The key and a block of settings are shown once. Copy the settings into the
   assistant's MCP settings (`mcpServers`), for example Claude Desktop's
   `claude_desktop_config.json`, Cursor's `mcp.json`, or for Claude Code:

   ```text
   claude mcp add --transport http novel-engine https://<your site>/mcp \
     --header "Authorization: Bearer <the key>"
   ```

3. Ask the assistant something about the story. Under the key, **Show use** lists what it asked
   for, by kind, over the last 30 days (counts only: what it read is never kept).

Suggestions from a key that may suggest arrive in **Review** as "Changes from a connected
assistant". Nothing changes until a person keeps them; delegation rules never keep them.

## Hosted and local boundaries

- **Hosted**: `/mcp` is public behind TLS, like the rest of the site. The key is the boundary; a
  browser page from any other address is refused (403). Keys are workspace data: deleting a story
  removes its keys, deleting a workspace removes all of them.
- **Local**: the key is checked the same way. Keep the API on this machine (`make api` starts
  uvicorn on 127.0.0.1, its default; do not add `--host 0.0.0.0`); an assistant on the same
  computer reaches `http://127.0.0.1:8000/mcp` (or the Vite address in development, which forwards
  `/mcp`). Only pages served from this machine may call it from a browser.

## A key has leaked

1. **People → Other assistants → Withdraw** on that key. It stops working on the next request
   (nothing is cached). An owner can withdraw anyone's key in the workspace.
2. Make a new key and paste its settings into the assistant.
3. See what it was used for: **Show use** on the withdrawn key, and the workspace audit
   (`access_key.created`, `access_key.revoked`):

   ```sql
   SELECT created_at, actor_user_id, action, target_id, detail
   FROM audit_events
   WHERE target_type = 'access_key' ORDER BY created_at DESC LIMIT 50;
   ```

   (Run with `docker compose exec -T postgres psql -U novel -d novel_engine` on the hosted stack.)
4. Look in **Review** for suggestions from a connected assistant you did not expect, and discard
   them. A key could never commit anything. Each suggestion's submission names the key that made
   it ("suggested by a connected assistant (key <key id>)"; the id is in People, never the key
   itself). To list one key's suggestions:

   ```sql
   SELECT m.id, m.project_id, m.status, t.created_at
   FROM mutation_transitions t JOIN mutation_sets m ON m.id = t.mutation_id
   WHERE m.origin = 'machine' AND t.to_status = 'needs_review'
     AND t.reason LIKE 'suggested by a connected assistant (key <key id>)%'
   ORDER BY t.created_at DESC;
   ```

The key format (`nvlpat_` and 49 letters and digits) is in the CI secret scan
(`make secret-scan`), so a key committed to this repository fails the check. GitHub and other
scanners can be given the same pattern.

## An assistant is refused

| What the assistant reports | Why | What to do |
|---|---|---|
| 401 "An access key from Novel Engine is required." | No `Authorization: Bearer` header, or not a key in the right form | Paste the settings again; check the header survived |
| 401 "That access key does not work." | Mistyped, or from another site | Make a new key |
| 401 "…has expired" | Past its end date | Make a new key |
| 401 "…was withdrawn" | Withdrawn by its holder or an owner, or its holder lost access to the workspace or story (removing a member withdraws their keys at once, and re-adding them does not bring the keys back), or their account was closed (the key's row in People says which) | Make a new key, if the person should still have access |
| 403 | A browser page from another address | Use the assistant's own MCP settings, not a web page |
| 429 "Too many requests with this key" | More than 120 calls a minute | Wait a minute; the assistant is looping |
| 429 "Too many attempts with keys that do not work" | 30 failed keys (unknown, withdrawn or expired) from one address in a minute | Wait a minute, then fix the key |
| 413 | A request over 1 MB | The assistant sent too much in one call; split the suggestion |
| 400 with code -32022 | The assistant asked for a protocol version this server does not speak | Update the assistant; supported versions are in the error |
| A tool answers "This key may only read." | `suggest_changes` with a read-only key, or the holder is now a reader | Make a key that may suggest (needs editor or owner) |

An assistant that tries to sign in through OAuth gets 404 from `/.well-known/oauth-*` with a
sentence saying to use an access key: give it the bearer header instead.

## Checks

- Logs: `mcp.request` events carry the method, tool, key id, workspace and outcome; never what
  was asked or answered.
  `docker compose logs server | grep '"mcp.request"' | tail`
- Keys that work, per workspace:

  ```sql
  SELECT workspace_id, count(*) FROM access_keys
  WHERE revoked_at IS NULL AND expires_at > now() GROUP BY workspace_id;
  ```

None of this has been rehearsed on the live site yet; record the first real use in
`plan/phase-2-evidence.md`.
