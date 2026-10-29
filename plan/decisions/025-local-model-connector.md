# ADR-025: A model on the author's own computer, reached through an outbound connector

- Status: Proposed (2026-10-21)
- Date: 2026-10-21
- Owners: project owner, implementer
- Related backlog: SCN-002A (and SCN-002, OPS-002 spending, SaaS-006)
- Builds on: ADR-002 (provider routing; this settles its deferred "authenticated outbound local
  companion"), ADR-022 (jobs), ADR-024 (access-key format), plan 07 ("Local inference connector")

## Context

A self-hosted copy can point `AI_BASE_URL` at Ollama or LM Studio. A person using the hosted
site cannot: their model runs on their own computer, behind a home router, and the site cannot
and must not reach into it. Plan 07 asked for a separately authenticated companion that makes
outbound connections only, receives only authorized job context and never platform secrets, with
its protocol, updates, revocation and threat model in an ADR. This is that ADR.

The constraints: no port opened on the person's computer; the story's words go only to the
computer the person connected; the computer can be disconnected at once; a lost or copied
credential has a short useful life; nothing is downloaded or installed automatically; spending
controls apply to every model call, even a free one; the connector must be small enough to read.

## Decision

**A connector program, standard library only.** `connector/` (`uv run python -m connector`, or
plain `python -m connector` with Python 3.11+) runs next to the person's model server. It talks
to the site over HTTPS (plain HTTP only to this computer itself, for testing), never follows a
redirect (so its credential cannot be bounced elsewhere), and to the model server without a proxy.
It relays exactly two calls to the model server: `GET /models` and `POST /chat/completions`
(always `stream: false`). It prints counts and states, never a request's words. Its credential is
kept in a file only its user can read (`0600`, written atomically), one per site.

**Registration is the OAuth 2.0 device authorization grant (RFC 8628).** The connector asks
`POST /api/v1/connector/codes` for a code; the site answers with a high-entropy `device_code`
(stored only as its SHA-256 digest; RFC 8628 §5.2) and an eight-letter `user_code` from the RFC's
20-consonant alphabet (`BCDFGHJKLMNPQRSTVWXZ`, about 34.5 bits, shown as `WDJB-MJHT`; §6.1),
valid for ten minutes and usable once. The person types the code under **AI assistant → Use a model
on your own computer**; dashes, spaces and small letters are forgiven (§6.1). Before confirming they
see the computer's name, the connector version and when it asked, with a warning to turn down a
code someone sent them: the remote-phishing attack in §5.4. We deliberately do not offer
`verification_uri_complete` (a link with the code filled in), which §5.4 notes makes phishing
easier. Wrong codes are limited per person (5 in 10 minutes; §5.1 shows about 5 tries in a code's
life keeps a random guess near 2⁻³²) and lookups to 30; once the wrong ones ran out, no code is
looked up for that person at all, right or wrong, until the window passes (otherwise the limit
would only slow a guesser down, never stop a lucky guess). Code requests are limited to 10 per
address in 10 minutes. Names a connector gives are shown without control or format characters
(bidirectional overrides, zero-width marks), so a name cannot be made to read as another. The connector polls `POST /api/v1/connector/token` at the given interval (5 s); the token
endpoint answers errors the RFC's way (`authorization_pending`, `slow_down` adding 5 s,
`access_denied`, `expired_token`, and `invalid_grant` once used), with `Cache-Control: no-store`.
Only workspace owners (the role with workspace admin rights) may confirm a code; the computer
belongs to the workspace it was confirmed in, and stops when the member who connected it leaves or
loses that role (checked when they are demoted, and on every request the computer makes).

**The device credential has the access-key format (ADR-024) with its own prefix**, `nvldev_` +
256 random bits in base62 + a CRC32 checksum, so secret scanners can match it
(`scripts/ci/secret_scan.py`) and a made-up one is refused without a lookup. Only its SHA-256
digest is stored. Following the rotation advice of the OAuth 2.0 Security BCP (RFC 9700, refresh
token rotation for clients that cannot keep a sender-constrained token), the site asks the
connector to rotate once the credential is a day old (`POST /api/v1/connector/rotate`); the one it
replaced keeps working for ten minutes so a lost answer does not lock the computer out, and using
it asks for another rotation. That rotation keeps the current credential as the previous one (never
drops it for the one presented), and is allowed only while the current one has never been used:
once it has, the replaced one asking to rotate means two copies exist, and the computer is
disconnected ("reused"), the refresh token reuse detection of RFC 9700 §4.14.2. When the current
credential was last used is recorded for this. A credential not rotated for 30 days stops working
and the computer is disconnected ("expired"). An owner can disconnect a computer at once; removing
the member who connected it disconnects it too (as with access keys). Failed sign-ins are limited
per address (30 a minute); once used up, nothing from that address is checked until the minute
passes. Mutual TLS or DPoP (sender-constrained tokens) were left out: they would need key
management in a standard-library program for a modest gain over short-lived, rotated credentials.

**The job channel is an HTTPS long poll, not a WebSocket.** The connector holds
`POST /api/v1/connector/poll` open for up to 25 seconds (the GitHub Actions self-hosted runner uses
the same shape, a 50-second HTTPS long poll with no inbound port). A long poll works through
corporate proxies and load balancers that drop or block WebSockets, needs no sticky sessions (any
server can answer, because the queue is in PostgreSQL), and needs nothing beyond `urllib`. The cost
is a little latency (the site checks the queue at once, a quarter second later, then less
often, up to once a second, while nothing comes) and one request per 25 seconds per idle computer.
A computer may hold two polls open at once (a long poll and a heartbeat) on a server and make 60 a
minute; more is refused (429). While a slow model works, the connector sends a heartbeat poll that
takes nothing (`take: false`), so the computer stays "connected" without taking a second request.

**The provider route "my own computer".** A workspace chooses one of the computer's listed models
(`POST /api/v1/ai/connector/devices/{id}/use`); this is stored as the provider `my_computer` in
`workspace_ai_settings` and the computer is marked `in_use`. `resolve_provider` then routes every
assistant call, hosted or self-hosted, to `device://<workspace>/<device>`, and the OpenAI client
used for every provider is given a transport that puts the request in that computer's queue and
waits (`backend/adapters/connector_transport.py`), so structured answers, the prompted fallback and
usage work exactly as for any provider. A computer whose server does not keep to a JSON schema is
asked in words. Plain errors, never a provider's text: "Your computer is not connected right now"
(no poll for 75 s), "did not pick up the request" (20 s), "took too long" (10 minutes, operator
setting `CONNECTOR_ANSWER_SECONDS`), "could not reach the model server", "no longer connected"
(503 `device_unavailable`; in a job, the same words). When the chosen computer is disconnected the
assistant stops; it never quietly moves to a cloud key, because the author chose to keep their
words on their own computer. Pictures are not sent to a computer (text only).

**Words leave only for that computer, and not for long.** A queued request is checked against the
workspace and the device on every step (`relay` refuses a device of another workspace whatever the
address says); a computer can take and answer only its own requests. The request body and the
answer are cleared as soon as the waiting call has read the answer, or gives up; what stays (30
days) is who asked, when, the model and the token counts. Disconnecting fails what was waiting, and
a disconnected computer is never handed a request. If the server holding the waiting call stops, a
request older than the pickup window is never handed out, and a sweep (at most once a minute per
server, on a poll or a new request) expires any request still open after the pickup and answer
windows and clears any answer not read within 5 minutes. A request names the story it was for, and
deleting the story removes it. An answer is measured as received (not only by its stated length)
and one over 4 MiB is refused (413), ending its request at once.

**Spending.** `ensure_within_limits` runs before every call as for any provider; usage is recorded
with provider `my_computer` and no cost (a cost the computer reports is dropped).

**Capability discovery is honest.** The connector lists models from `/v1/models` and recognises
Ollama (`/api/version`; a context size only when the model's settings fix `num_ctx`, since
Ollama's default is far below the trained length), LM Studio (`/api/v0/models`; the *loaded*
context, not `max_context_length`, which is the model's ceiling) and llama.cpp's server (`/props`,
`default_generation_settings.n_ctx`, the real context). Ollama 0.5+ (via llama.cpp's grammar
decoding), LM Studio and llama.cpp constrain output to a `json_schema`; any other server is asked
in words. What a connector reports is treated as a description to show and plan with, never as a
permission; malformed parts are dropped.

**Updates are never downloaded.** `GET /api/v1/connector/version` gives the minimum and latest
versions. The connector refuses to run (exit 2) when it is older than the minimum, and the site
refuses an old connector on every call (426 `connector_too_old`). A newer version is announced, not
fetched.

## Alternatives considered

### A WebSocket channel

Lower latency and push without polling. Rejected for now: blocked or cut by some proxies, needs
sticky sessions or a pub/sub layer between servers, and a WebSocket client is not in Python's
standard library. The request/answer protocol here could move to a WebSocket later without
changing registration or the route.

### A tunnel (the person exposes their model server, e.g. through a tunnelling service)

Simple for us, but the model server would be reachable from the internet with whatever
authentication it has (often none), and the site would need to store a URL and secret per person.
Rejected: it opens exactly the port plan 07 rules out.

### A long-lived API key the person pastes into the connector

Fewer moving parts than the device grant. Rejected: the person would copy a secret between screens,
it would not rotate, and a key pasted into the wrong program is not noticed. The device grant moves
a short, single-use code the person can read aloud, and the secret never appears on screen.

### Keeping the request words encrypted at rest

The queue row holds the words for the seconds a request is in flight. Encrypting them with the
credential key (envelope encryption, as for provider keys) was considered; left out because the row
is cleared on completion and the database is already the store of the story itself. Revisit if
requests start waiting longer.

## Consequences

### Positive

- A person on the hosted site can use a model on their own computer with no port opened and no
  provider bill; their words go to that computer only.
- One route for every assistant feature: nothing else changed to support it.
- Disconnecting, member removal, expiry and version floors stop a computer at once.

### Negative

- A computer must keep the connector running; a sleeping laptop fails requests with "not connected".
- Long-poll requests hold a server connection each; at many idle computers this needs tuning.
- Local models are slow: a scene draft can take minutes, and context sizes are often unknown.
- The connector is distributed only with the source (the `connector/` folder); there is no signed
  package yet.

### Follow-up work

- A signed, versioned download of the connector, and a place on the site that says how to get it.
- Measuring real local models (Ollama, LM Studio, llama.cpp) against the assistant's prompts: every
  path here has run against the stand-in model only.
- Pictures for a computer whose model sees them.

## Validation

`tests/contract/test_local_connector.py` (registration, confirm, slow down, turned down and expired
codes, wrong-code limits, old connectors, the poll/answer channel through the real OpenAI client
transport, plain failures, offline and not picked up, one computer cannot take or answer another's
request, disconnect, rotation and grace, expiry, capabilities, heartbeat, the hosted route with
spending recorded and no fallback to a key, wrong workspace, purge and member removal),
`tests/integration/test_connector_store.py` (PostgreSQL: digests only, one-time codes, concurrent
takes, words cleared, workspace deletion), `tests/unit/test_local_connector.py` (codes, credential
format, versions, addresses, discovery for Ollama/LM Studio/llama.cpp, relay limits, the credential
file, version refusal, the secret scanner), `tests/application/test_connector_end_to_end.py` (the
real `python -m connector` process between a local site and the stand-in model), and
`frontend/e2e/local-connector.spec.ts` (the whole flow in a browser, with the assistant's suggestion
answered through the connector by a site that has no model of its own). All against the stand-in
model only.

## Sources

- RFC 8628, OAuth 2.0 Device Authorization Grant: https://www.rfc-editor.org/rfc/rfc8628
  (§3.2 response, §3.5 errors and `slow_down`, §5.1 user-code brute force, §5.2 device-code entropy,
  §5.4 remote phishing, §6.1 user-code alphabet and normalisation).
- RFC 9700, OAuth 2.0 Security Best Current Practice (refresh token rotation or sender-constraining):
  https://datatracker.ietf.org/doc/rfc9700/
- GitHub, Communicating with self-hosted runners (HTTPS long poll, outbound only):
  https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/communicating-with-self-hosted-runners
- Long polling versus WebSockets behind proxies and load balancers:
  https://www.pubnub.com/blog/long-polling-vs-websockets/
- Ollama structured outputs: https://ollama.com/blog/structured-outputs ; Pydantic AI's notes on
  Ollama 0.5+ enforcing `json_schema` through llama.cpp grammars:
  https://pydantic.dev/docs/ai/models/ollama/
- LM Studio REST API (`/api/v0/models`, `max_context_length`, loaded context):
  https://lmstudio.ai/docs/api/rest-api
- llama.cpp server `/props` (`default_generation_settings.n_ctx`): the server README in
  https://github.com/ggml-org/llama.cpp/tree/master/tools/server

## Amendment 2026-10-21 (batch 10 A): answers come whole, not streamed

Drafts stream to the studio from cloud providers (ADR-022 amendment, SCN-002). Through the
connector they do not: the relay carries one request and one whole answer, and the route declares
`supports_streaming=False`, so the gateway never asks a computer for a stream and the studio shows
progress only. Relaying pieces would need a new protocol version (the connector posting parts of an
answer while the site's long poll holds the request), with its own limits and tests, for little gain
with a local model. Revisit if authors ask for it.
