# AI provider outage or key revoked

On the hosted site each workspace connects its own provider key (OpenRouter or another listed
provider) on its Assistant page; the keys are stored encrypted. Nothing in a story depends on a
model: everything the assistant does can be done by hand, and a failed request changes nothing.

## How you notice

- Authors see "The AI provider did not answer. Nothing was lost; try again later." on a draft,
  check or reading, or a request to the assistant fails with a message that the provider did
  not answer.
- "No AI key is set up for this workspace, so nothing could be drafted." means the workspace has
  no active key (it was removed, or never connected).
- "The AI provider's answer could not be used. Nothing was saved from it." means the provider
  answered with something unusable: usually a model that does not follow the requested shape, not
  an outage.
- The server's log has `ai.generate.error` or `ai.generate_text.error` events. Each names the
  model and the kind of error, never the request or answer.

## What to check

1. The kind of error, over the last hour:
   ```sh
   docker compose logs --since 1h server | grep -E 'ai\.generate(_text)?\.error'
   ```
   - `AuthenticationError` or `PermissionDeniedError`: the key was revoked, expired, or has no
     credit left. Only the workspace that owns it is affected.
   - `RateLimitError`: the provider is limiting that key; it passes.
   - `APIConnectionError`, `APITimeoutError`, `InternalServerError`: the provider (or the way out
     to it) is down. Usually every workspace using that provider is affected.
   - `NotFoundError` or `BadRequestError`: the model chosen on the Assistant page no longer exists
     there.
2. The provider's own status page.
3. That the server can reach the internet: the server is the only container on the
   `platform-proxy` network, which is also its way out. If every provider fails at once, check
   the host's network and Caddy, not the keys.
4. For a server-wide model route (a self-hosted install with `AI_BASE_URL` or `OPENAI_API_KEY`
   set), ask it two tiny questions that carry no story text:
   ```sh
   docker compose exec server python -m backend.devtools.diagnostics --check-ai
   ```
   `ai_check.ok` is true when both answered. This does not test a workspace's own key.

## What to do

- **Key revoked or out of credit.** The workspace's owner connects a new key on the Assistant
  page (an operator cannot and should not see or enter it). The old key is retired when the new
  one is connected. Then they retry; the failed request left nothing behind.
- **Model no longer offered.** The owner picks another model on the Assistant page.
- **Provider outage or rate limit.** Nothing to repair. Tell authors that writing, planning,
  review and every by-hand path keep working, and to retry assistant requests later. Work that
  was waiting is not lost; failed work shows a plain message and can be asked for again.
- **Network.** Fix the host's route out (see `deploy/hosted/README.md`).

## How to confirm

- A new request (a short draft, or "check this scene") finishes.
- No new `ai.generate.error` events: `docker compose logs --since 10m server | grep -c 'ai.generate.error'`
  prints `0`.
