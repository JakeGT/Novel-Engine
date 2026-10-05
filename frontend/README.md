# Novel Engine frontend

This is the current Vite/React prototype shell. Product UI work begins after the Phase 0 foundation is deterministic; do not mistake the starter screen for an implemented workflow.

Use Node.js 24 LTS and the pnpm version declared by `packageManager` in `package.json`.

```bash
pnpm install --frozen-lockfile
pnpm run dev
pnpm run lint
pnpm run build
```

From the repository root, the equivalent validation commands are `make frontend-install`, `make frontend-lint`, and `make frontend-build`.

The planned frontend architecture and delivery order are maintained in [`../plan/06-api-and-frontend.md`](../plan/06-api-and-frontend.md) and [`../plan/09-delivery-roadmap.md`](../plan/09-delivery-roadmap.md).
