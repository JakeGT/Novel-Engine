# ADR-024: Other assistants connect through MCP with personal access keys, and can only read or suggest

- Status: Accepted (2026-10-18)
- Backlog: QRY-003
- Builds on: ADR-014 (review), ADR-017 (one tool boundary), ADR-020 (what-ifs and knowledge),
  plan documents 06 ("MCP transport"), 07 (security), 18 ("Read-only MCP transport")

## Context

Authors already pay for assistants (Claude, ChatGPT, Cursor and others) and want them to look
things up in their story: who knows what, what the plan says, what a scene holds. The Model
Context Protocol (MCP) is how those assistants reach outside tools. Until now every request to
Novel Engine came from a person signed in through a browser session, so there was no way for a
program to authenticate, and no transport for one.

Two things had to be decided: how a program proves who it acts for, and what it may do.

## Decision

**Personal access keys.** A member makes a key under People, for one workspace and either one
story or every story they can read there. It reads by default; it may also suggest changes only
when its holder can change the story and asks for that. It has a name, an expiry (90 days unless
chosen, never over a year), a last-used time, and can be withdrawn. It is shown once, in the answer
to the request that made it, and never again.

The format follows GitHub's 2021 token format (researched for this decision): a fixed prefix
(`nvlpat_`) that secret scanners can match with few false positives (now in
`scripts/ci/secret_scan.py`), 256 random bits in base62, and a 6-character base62 CRC32 of the
random part so a mistyped or made-up key is refused without a database lookup. Only the SHA-256
digest is stored (`access_keys.token_hash`, unique): a key is high-entropy, so a slow password hash
adds nothing, and a digest lets the key be found in one indexed lookup. The digest is compared
with `hmac.compare_digest` after the lookup. A keyed HMAC (pepper) was considered and left out: it
protects only against an attacker who reads the table but not the configuration, and adds a secret
to rotate.

**Checked on every request, never cached.** `AccessKeyService.authenticate` refuses an expired or
withdrawn key, and checks the holder's account and their membership (and the story, for a story
key) against the stores each time. A key whose holder lost access, or whose account is closed, is
withdrawn for good, with the reason recorded, and does not come back if access does. Removing a
member withdraws every key they hold in that workspace at once (reason `access_lost`, one workspace
audit event per key), so re-adding them before a key is next used does not revive it. What a key may
do is never more than its holder may do at that moment: a key that may suggest stops suggesting if
its holder becomes a reader. Calls are rate limited per key (120 a minute) through the shared
limiter, and keys that fail, including withdrawn and expired ones, are rate limited per address
(30 a minute). A member has at most 20
working keys.

**One endpoint, `/mcp`, mounted in the FastAPI app.** Streamable HTTP, POST only, one JSON-RPC
message per request, always answered with one JSON object. Mounting in the app (not a separate
process) keeps one deployment, one container of services, and one place where scope is checked.
It speaks both eras of MCP on the same endpoint, as the 2026-07-28 specification allows for a
"dual-era" server:

- 2026-07-28 (stateless): the protocol version in `params._meta`; `MCP-Protocol-Version`,
  `Mcp-Method` and `Mcp-Name` must match the body (400 `HeaderMismatch`, -32020); unsupported
  versions get -32022 with the supported list; `server/discover`; `resultType`; `tools/list` is
  cacheable privately for a minute.
- 2025-11-25, 2025-06-18, 2025-03-26: the `initialize` handshake, answered without a session id
  (those versions make sessions optional), so every request still stands alone.

GET and DELETE answer 405. A browser `Origin` must be the hosted site's configured address, or on
a local install a page on this machine; the Host header is never trusted for this, since under DNS
rebinding a hostile page's Origin and Host agree. A missing or failing key gets 401 with
`WWW-Authenticate: Bearer`. MCP's OAuth authorization flow is not used: the well-known OAuth
discovery addresses answer 404 with a sentence saying to use an access key.

**Our own small JSON-RPC subset, not the official SDK.** The official Python SDK (`mcp` 2.x,
which supports 2026-07-28) was considered. It was not added because only tools are offered and
every answer is one JSON object, so the subset is small and fully tested; the SDK would bring about
ten dependencies (an HTTP client, JWT and cryptography, OpenTelemetry, SSE, JSON Schema, uvicorn)
onto the authentication boundary; its authorization support assumes an OAuth authorization server;
and its stateless handling runs tools on its own task group, outside the request whose context
carries the working what-if. Revisit if resources, prompts, streaming or long-running tools are
wanted.

**Tools are thin calls into the same application services, answered by the same response models
as the HTTP API** (`backend/api/mcp_tools.py`): list stories, search entries, read an entry (with
`as_known_by`, only what that character knows or believes, the same reading as "What they know";
the entry is given only by its id, kind and name: its details, other names (an alias can give away
a secret identity), outside ids and slug are left out because nothing says the character knows
them; a point of view reads the story as it stands, with no "as of" moment; and a mistaken
belief is given as a belief, without saying it is mistaken), read the plan, read a scene's plan card
and current text, the timeline or what is true at a moment, a record's history with provenance,
and suggestions waiting for review. Tests compare them with the HTTP API. A `story` argument only
narrows what a key reads; a `what_if` argument reads in one of that story's what-ifs, once it is
proven to be that story's.

**One write tool, proposals only.** `suggest_changes` exists only for a key that may suggest whose
holder may still change the story. It makes one proposal of the given changes with origin
`machine` (new in `OriginKind`; never a direct-commit origin, so review is always required) and
sends it to the review queue, with the key's id (never the key) in the submission's reason, so each
suggestion can be traced to its key. Only the MCP path can make a `machine` proposal
(`MutationService.suggest`): the HTTP API refuses that origin. There is no tool that approves, commits, rejects, changes rules,
review settings, delegation or who may do what. Machine suggestions are not offered to the
"keep the safe ones for me" rules (AST-EXP-015): they always wait.

**Counts, never content.** Use is counted per key, day and tool, with how many calls were refused
(`access_key_uses`); logs name the method, the tool and the key. Nothing a tool was asked or
answered is stored or logged. Making and withdrawing keys are workspace audit events.

## Consequences

- An author can point an assistant at their story with one pasted block of settings, and withdraw
  it with one button. Every suggestion lands in Review, labelled "from a connected assistant".
- A leaked key reads (or suggests to) only what its holder can, until it expires or is withdrawn;
  the prefix lets secret scanners and the CI scan find it.
- Hosted: the endpoint is public behind TLS; the bearer key is the boundary. Local: the API binds
  wherever the operator runs it; the runbook says to keep it on localhost.
- Not built: OAuth sign-in for MCP clients, resources and prompts, streaming, a key that reads
  only part of a story, and keeping machine suggestions under delegation rules.
