# Changes

Novel Engine has no tagged release yet. This file records what a first release would contain,
for authors and for whoever runs an install. Version numbers start when the owner tags one.

## Unreleased: release-one candidate

### For authors

- **A story bible you can trust.** Characters, places and anything else you define, with facts
  between them, history for every change, and undo. Nothing the assistant suggests is added until
  you approve it.
- **Start with the assistant or by hand.** Describe your story and the assistant drafts entries for
  you to review; or write them yourself. Every assistant feature has a by-hand way to do the same.
- **A story plan** from the whole book down to scenes, with threads, set-ups and pay-offs.
- **Your writing.** Paste or bring in text; names in it are found and linked; the assistant can read
  it and suggest what it adds to the story bible, quoting your words for each suggestion.
- **Story rules** you write in words, checked against the story.
- **Time, what-ifs and who knows what.** Facts can start and end in story time; a what-if is a copy
  of the story to try something without touching the real one, and can be brought back; you can
  record who knows a secret and what the reader has been shown.
- **What the assistant would use** for a scene, item by item with the reason, and your say over it.
- **The scene studio.** Write a scene; ask the assistant for up to three drafts, after seeing what it
  would cost; keep one or discard them; every version says who wrote it. Checks without any model
  look for new names, secrets on the page, planned reveals missing and stock phrasing; the assistant
  can check a scene too, and every note quotes your words. "How you write" guides every draft.
- **From scenes back to the story bible.** "Update the story from this scene" reads what the scene
  says and suggests it for review; the second time, only what changed.
- **Checks you can answer.** Dismiss a check or the assistant's note with a reason; it comes back
  if the words it points at change. A story rule can be set to hold back any change that breaks it.
- **Deeper plans.** Scenes can move a thread along; a scene can say what the viewpoint character
  wants, what stands in the way and how it ends for them; compare what the assistant would use now
  with what a kept draft was written from.
- **Promises to the reader**: what your plan owes the reader and where each promise is kept; a
  reveal that comes too early is flagged; scenes can say what must be true before them.
- **Continuity checks**: someone in two places at once, a journey too quick for the travel times
  you set, a scene after a death, who has what, places that do not exist yet — warnings only.
- **Richer story rules**: "if this then that", "never both", ranges, exceptions, beliefs, and
  whether a rule must hold at every moment or by the end.
- **How each character learned something**, and a warning when a scene uses it too early.
- **Names that depend on where you are**: "the captain" can mean one person from a scene on, or
  only in a what-if, or only through one character's eyes.
- **Said is not true**: record what someone says or the narration claims without making it a fact;
  each character's trust and who sees what decide which beliefs are suggested.
- **Keep the safe ones for me** (off unless you choose it): new things of a kind you have approved
  three times can be added without asking, each listed with Undo.
- **Connect another assistant** (Claude, ChatGPT and others) with an access key: it can read your
  story and, if you allow, suggest changes that wait in Review.
- **Character arcs**: the stages a character passes through, each placed in a scene, checked
  for order and against the story bible.
- **What a change touches**, shown before you approve it: scenes, threads, rules, writing and
  kept drafts that depend on it, and what could not be checked.
- **The assistant's check in separate passes** you choose, each with its cost, and **audits** of
  a whole part of the story.
- **A reader's guide you can print**, also as of an earlier version of the story.
- **Type @ to name an entry** while writing; **pictures on scenes**.
- **Spending you control**: how much the assistant checks on its own, a budget per conversation,
  and being asked first before any request above a size you choose.
- **Pictures**: small copies in lists; a page for each picture where the assistant can describe
  it for you to accept, correct or reject; choose whether the assistant is sent the picture, its
  description, or nothing; a picture can be kept secret or apply from a point in the story.
- **Planning help**: the assistant can suggest what a scene is for and bridge scenes when a
  scene's conditions are not met; shared scales such as trust; checks of time words, things in
  use and eye or hair colour against your story bible.
- **Checks only re-run what changed**, findings can be marked intended, and dismissed findings
  come back when what they depended on changes.
- **The reader's guide in parts**: characters, places, timeline or a recap.
- **Photos lose their hidden details** (location, camera) when uploaded.
- **Use a model on your own computer** (Ollama, LM Studio) on a self-hosted install, so nothing
  about your story leaves it.

### For whoever runs it

- **Upgrading:** take a backup first (`deploy/hosted/backup.sh` on the hosted profile). The server
  applies migrations on start; this release adds migrations up to `0052_context_layers`. `0047` moves search vectors into a
  new table; run `make reindex-status` afterwards.
- **Readiness** now answers `ready`, `degraded` (200) or `unavailable` (503, a required store is
  down); a watcher that looked for `degraded` on a store outage must look for `unavailable`.
- **Archives** are version 4 and carry what-ifs, conversations and scene readings; version 3
  archives still restore. `make retention` also clears old finished work.
- **Rolling back:** migrations are applied forward; to go back a version, restore the backup taken
  before the upgrade (`deploy/hosted/restore.sh`, which checks the backup before changing
  anything). Downgrade scripts exist for development and never delete an author's words, but they
  drop newer information such as who wrote each version.
- **Background work** (drafting, checking, reading scenes) runs inside the server process from a
  queue in PostgreSQL; no other service is needed. A restart puts unfinished work back in the queue.
- **The story graph updates by itself** in the background; readiness reports it. Scheduled
  backups and recovery runbooks are in `deploy/hosted/` and `plan/runbooks/`.
- **Checking the model route:** `python -m backend.devtools.diagnostics --check-ai`.
- **Nightly backups:** an example systemd timer and a cron line in `deploy/hosted/README.md`;
  `deploy/hosted/prune-backups.sh` keeps the newest complete backups. Recovery runbooks for common
  failures are in `plan/runbooks/`.
- **Pictures are prepared** in a separate limited process (Pillow); settings `MEDIA_*` in
  `.env.example`; `make media` and the runbook `plan/runbooks/media-processing.md`.
- **Evaluations**: `make eval` (no model) and `make eval-model` / `make eval-media` for real models.
- **Browser tests** come in two sets; `make e2e` runs both (one with no model, one with a local
  stand-in model).

### Known limits

- No real model has been measured; the assistant's quality is untested beyond scripted answers.
- Pictures can be shown to a model only on a route you mark as seeing pictures; no audio or video.
- No real vision or embedding model has been tried; search by meaning for pictures uses a stand-in
  in tests.
- No open-source licence has been chosen yet.
