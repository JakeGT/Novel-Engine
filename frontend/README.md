# Novel Engine frontend

The web app: a public landing page, the author's library, and the project workspace (story bible,
review inbox, history, types and fields, media, AI key). React, React Router, TanStack Query, and
hand-written CSS; no component framework.

The visual direction ("ink on paper") and the reasons for it are in
[`../plan/20-design-direction.md`](../plan/20-design-direction.md); the interaction model is in
[`../plan/19-assistant-first-experience.md`](../plan/19-assistant-first-experience.md). Read both
before changing how things look or flow.

Use Node.js 24 LTS and the pnpm version declared by `packageManager` in `package.json`.

```bash
pnpm install --frozen-lockfile
pnpm run dev        # proxies /api, /auth and /health to http://localhost:8000
pnpm run lint
pnpm run typecheck
pnpm run test
pnpm run build
```

From the repository root: `make frontend-install`, `make frontend-lint`, `make frontend-typecheck`,
`make frontend-test`, `make frontend-build`, and `make e2e` (real browser against a disposable
backend, with accessibility checks).

## Layout

- `src/styles/` — `tokens.css` (paper, ink, one accent, pigments; light and dark), `app.css`,
  `landing.css`. Sections are ruled, not boxed; the accent marks, it never fills.
- `src/components/InkCanvas.tsx` — the landing page's WebGL2 ink shader. Decorative, capped, paused
  when off screen, still under reduced motion, and optional: the page works without it.
- `src/lib/words.ts` — the one place that turns server vocabulary (types, predicates, operations,
  revisions) into the words shown to authors. Screens never show raw ids or operation names.
- `src/components/` — shared pieces (`ui.tsx`, `icons.tsx`, entry form, facts, activity, dialogs).
- `src/pages/` — `Landing`, `Home` (library), `ProjectLayout` (shell), and one file per screen.
- `src/api/` — `schema.ts` is generated from the OpenAPI snapshot (`pnpm run gen:api`); `client.ts`
  is the typed client.

## Rules worth keeping

- The hosted site sends a strict content security policy: no inline scripts, and fonts, images and
  requests from its own origin only. Fonts are bundled (`@fontsource-variable/*`: Newsreader and Instrument Sans);
  do not add CDN links.
- Every canon write goes through `useCanonWrite`, which keeps what the author typed when a save
  clashes with someone else's change.
- The landing page describes only what the product does today.
