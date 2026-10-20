# The assistant and pictures (CTX-013, AST-005)

Whether the assistant can be sent pictures is the operator's declaration, not something the
server discovers. With it off (the default), pictures are never sent: a picture an author chose
to send is given as its accepted description instead, and the record of each draft or check
says so; "Describe this picture" is refused before anything is queued or spent.

## How you notice

- The picture page says "The assistant you use cannot see pictures, so it cannot describe this
  one."
- A describe job fails with "The assistant cannot see pictures. This needs the picture itself,
  so nothing was sent." (job error code `refused`). Nothing was spent.
- A draft's kept context, or a check's result, has `"degraded": true` in its `delivery`: a
  picture was wanted and its description (or only its name) went instead.
- A describe job fails with "The AI provider did not answer": see
  [provider outage](provider-outage.md). A model that rejects images usually answers 400 here.

## What to check

1. Does the chosen model take images? Check the provider's model page (OpenRouter lists input
   modalities per model). A text-only model with picture input turned on is refused by the
   provider, not by Novel Engine.
2. The settings in the server's environment:
   - `AI_IMAGE_INPUT=true` — the server's own model (`AI_MODEL`, `AI_BASE_URL`) can see pictures.
   - `AI_IMAGE_MAX_COUNT` (default 4), `AI_IMAGE_MAX_BYTES` (default 5 MB),
     `AI_IMAGE_TYPES` (default `image/png,image/jpeg,image/webp`).
   - Hosted: `AI_IMAGE_PROVIDERS=openrouter` (comma-separated provider ids) — a workspace's own
     provider is treated as able to see pictures only when listed. Every workspace using that
     provider must then choose a model that takes images.
3. `GET /health/capabilities` → `ai.capabilities.input_modalities` includes `image` when the
   server's own route is declared able to.

## What to do

- To turn picture input on: set the settings above and restart the server and worker. Nothing
  else changes; authors choose per picture whether it is sent ("Use in the assistant's
  context").
- To turn it off (a provider's retention terms changed, a cost spike): set
  `AI_IMAGE_INPUT=false` (and empty `AI_IMAGE_PROVIDERS`) and restart. Queued describe jobs fail
  before their call with nothing spent; drafts and checks fall back to descriptions, recorded as
  such.
- A picture larger than `AI_IMAGE_MAX_BYTES` is sent as its 1024px web copy when one was made
  (see [media processing](media-processing.md)); if the copy is missing, its description is used.

## Confirm

Open a picture's page: with picture input on, "Describe this picture" shows its cost; run it
and the suggestions appear. Logs carry `media.described` with counts only, and `ai.generate`
with `images: 1` — never the picture, its words or the data address.
