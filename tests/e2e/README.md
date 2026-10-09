# End-to-end tests

The browser end-to-end suite lives with the frontend toolchain in
`frontend/e2e/` (Playwright resolves its dependencies from `frontend/`). It
drives the real UI against a real backend on disposable state:

- `scripts/e2e/serve.py` creates a throwaway PostgreSQL database, migrates it,
  and serves the API in local auth mode against the `neo4j-test` service;
- Vite serves the app and proxies to that API.

Run it with `make e2e` (needs `docker compose --profile test up -d --wait`).
Install the browser once with `make frontend-e2e-install`, or set
`E2E_BROWSER_CHANNEL=chrome` to use an installed Chrome.
