# Search by meaning off, behind, or changing model

Finding passages and pictures by meaning uses a derived search index of vectors, kept in
*generations*: one per model setting. Names and links work without it; nothing in the story
depends on it, and it can always be built again. See ADR-010 (amendment 2026-10-20).

## How you notice

- Readiness says `200 degraded` with "Finding things by meaning is off for now; names and links
  still work." The detailed answer has a `search_index` check with `active_generation`,
  `active_model`, `active_dimension`, `active_rows`, `lag` (rows the index in use is missing),
  and any `building_generation` with its `building_lag`.
- Its detail says one of:
  - "The search index in use was built with a different model from the one set for this
    server." The model setting (`EMBEDDING_PROVIDER` and its settings) was changed and no new
    index has been switched to yet.
  - "Nothing has been indexed with the model set for this server yet." A fresh install; it
    starts with the next writing indexed.
  - "No search index has been built yet." / "The search index could not be read."
  - the model's own reason, such as "no model revision is pinned (EMBEDDING_GEMMA_REVISION)" or
    "the embeddings extra is not installed".
- `/health/capabilities` shows `search_model`: the configured model, revision, size and what it
  can read (`text`, `image`).

## What to check

1. `docker compose exec server python -m backend.devtools.embeddings status` (locally:
   `make reindex-status`). It prints the configured model, the index in use, any build, what
   each is missing, and whether search works now. Counts and model names only.
2. Rows per generation, with no content:
   ```sh
   docker compose exec -T postgres psql -U novel -d novel_engine -c \
     "SELECT g.id, g.model, g.dimension, g.state, count(v.id) AS rows
        FROM embedding_generations g LEFT JOIN embedding_vectors v ON v.generation_id = g.id
       GROUP BY g.id ORDER BY g.created_at;"
   ```
3. The log: `docker compose logs --since 1h server | grep -E 'embedding\.|vectors\.'`.
   `vectors.not_indexed` with `reason=space_mismatch` means writing was not indexed because the
   index in use is another model's; `embedding.picture_failed` means a picture was not embedded
   when it was prepared (the next refresh catches it up).

## What to do

**Changing the model** (for example to EmbeddingGemma 2):

1. Install the extra (`uv sync --locked --extra embeddings`) and pin a revision:
   `EMBEDDING_PROVIDER=embeddinggemma`, `EMBEDDING_GEMMA_REVISION=<exact commit>`,
   `EMBEDDING_GEMMA_DIMENSION=512` (or 768; measure first with `make eval-media
   EVAL_MEDIA_ARGS='--provider embeddinggemma'`). Never float16: `EMBEDDING_DTYPE` is `auto`,
   `bfloat16` or `float32`.
2. Restart the server. Search by meaning is now off (the index in use is the old model's); names
   and links keep working.
3. Build the new index beside the old: `make reindex` (in Docker:
   `docker compose exec server python -m backend.devtools.embeddings build`). It can be stopped
   from another terminal with `make reindex-cancel`; what it made is removed. Add
   `REINDEX_ARGS=--from-texts` to cut passages from the texts again instead of re-embedding the
   stored ones.
4. Switch: `make reindex-activate`. It refuses while rows are missing; finish the build first.
   `REINDEX_ARGS=--force` switches anyway (only when you accept missing results).

**Going back:** `make reindex-rollback` switches to the index used before. If the model setting
is still the new one, search stays off until the setting is changed back and the server
restarted.

**Behind** (`lag` above zero; a restore, or pictures and captions that arrived while the worker
was down): `make reindex-refresh` (after a restore, `REINDEX_ARGS=--from-texts`). It only adds
what is missing.

**A build left waiting** (`building_generation` set, nothing running): continue it with
`python -m backend.devtools.embeddings build --resume`, or remove it with
`python -m backend.devtools.embeddings discard --generation <id>`.

## How to confirm

`make reindex-status` ends with `searchable: True`, `active_lag` totals 0, and readiness no longer
lists `search_index` among its reasons.

Checked against a disposable database with the stand-in model (2026-10-20): status, build,
activate, rollback, build with `--activate`, cancel and refresh. Not yet run with a real model or
on the hosted stack.
