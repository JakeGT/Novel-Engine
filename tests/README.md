# Tests

- `unit/` contains deterministic tests with no databases, model providers, or network.
- `application/` will exercise workflows through in-memory ports.
- `integration/` will use disposable service containers.
- `e2e/` will cover complete user workflows.

Root-level `test_*.py` files predate this layout and are manual/live integration scripts. They are intentionally outside pytest's configured `testpaths` until they are converted.
