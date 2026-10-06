# Manual scripts

These scripts are **not** collected by pytest. They are retained for manual,
live exploration against a running API, Neo4j, and (for character
creation) a real model provider. They are not deterministic tests.

Run them from the repository root with the locked environment, for example:

```bash
uv run --locked python scripts/manual/run_test_query.py
```

| Script | Requires | Purpose |
| --- | --- | --- |
| `run_test_query.py` | running Neo4j with seeded data | Manual context traversal probe. |
| `context_extraction.py` | running API, Neo4j | Exercises `/api/v1/context` with entity tags. |
| `character_creation.py` | running API, Neo4j, `OPENAI_API_KEY` | Interactive character questionnaire and graph write. |

All scripts target the canonical `/api/v1` surface; the deprecated `/api` aliases
were removed in Phase 1. Automated coverage lives in `tests/contract/` and
`tests/integration/`.