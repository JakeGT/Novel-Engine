# Manual and legacy scripts

These scripts are **not** collected by pytest. They are retained for manual,
live exploration against a running API, Neo4j, Chroma, and (for character
creation) a real model provider. They are not deterministic tests.

Run them from the repository root with the locked environment, for example:

```bash
uv run --locked python scripts/manual/run_test_query.py
```

| Script | Requires | Purpose |
| --- | --- | --- |
| `run_test_query.py` | running Neo4j with seeded data | Manual context traversal probe. |
| `context_extraction.py` | running API, Neo4j, Chroma | Exercises `/api/rag/context` with entity tags. |
| `character_creation.py` | running API, Neo4j, Chroma, `OPENAI_API_KEY` | Interactive character questionnaire and graph write. |
| `legacy_endpoint_suite.py` | running API, Neo4j, Chroma | Legacy HTTP endpoint walkthrough. |

`legacy_endpoint_suite.py` is a pytest module by construction but is excluded
here; its coverage is to be replaced by in-process API contract tests under
`tests/contract/` and disposable integration tests under `tests/integration/`
during Phase 0 (0F/0H). Do not re-enable it as a CI test.
