# Tests

Deterministic tests live here. The suite is organized by layer:

| Directory | Marker | Responsibility |
| --- | --- | --- |
| `unit/` | `unit` | Pure domain logic: no services, network, secrets, or persistent writes. |
| `application/` | `application` | Use cases run through in-memory ports and scripted fakes. |
| `contract/` | `contract` | In-process API and repository contract tests. |
| `integration/` | `integration` | Disposable PostgreSQL/Neo4j containers; owns and destroys its data. |
| `e2e/` | `e2e` | Complete user workflows against disposable services. |
| `prompts/` | `unit` | Every prompt the app sends, built from fixed input and compared with its stored snapshot (`__snapshots__/`). After raising a prompt's version: `uv run python -m tests.prompts.snapshots --update`. |

Live model-provider tests are marked `llm` and are excluded from ordinary CI.

## Deterministic fixtures and doubles

- `conftest.py` provides `fixed_clock`, `id_generator`, `settings_factory`, and
  `scripted_ai`. Settings factories never read a developer's `.env` unless a
  test explicitly asks for it.
- `doubles.py` provides in-memory graph/vector fakes, a scripted AI client, a
  deterministic embedding provider, a fake migration checker, and a clock/ID
  source. Fakes model ports in plain terms; they do not mock driver calls.

## Commands

```bash
make test               # unit + application + contract (hermetic)
make test-integration   # integration marker (disposable services)
```

Root-level `test_*.py` manual scripts have moved to `scripts/manual/` and are no
longer collected by pytest.
