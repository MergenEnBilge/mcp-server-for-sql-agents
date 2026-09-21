# gui-frontend

The admin console: a React + TypeScript app (built with Vite) that talks to the
[gui-backend](../gui-backend/README.md) REST API. The visual choices and the reasons behind them are in
[docs/gui-design.md](../docs/gui-design.md).

## Running it

```bash
cd gui-frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to the backend on :8100
```

You need the backend running and an identity provider to sign in against; the dev compose file starts
Keycloak for that (see the root README). The app signs people in with the OpenID Connect authorization
code flow and PKCE, keeps the access token in session storage, and sends it to the API as a Bearer token.

Where the app finds the identity provider and the API is set in `public/config.js`, which is generated at
container start in a deployment so one built image works everywhere. Locally the defaults in
`src/config.ts` point at the dev Keycloak.

## Scripts

| Command | What it does |
|---|---|
| `npm run typecheck` | TypeScript, no emit |
| `npm run lint` | ESLint |
| `npm test` | Unit and component tests (Vitest + Testing Library) against a fake API |
| `npm run build` | Type-check, then a production build in `dist/` |
| `npm run e2e` | Playwright against a running stack (set `E2E_BASE_URL`), signing in through the real login page |

The end-to-end tests read the sample users' password from `KEYCLOAK_DEV_USER_PASSWORD` (the repo's `.env`,
or the environment in CI).

## Layout

```
src/
  api/          typed fetch client, request state hook, response types
  auth/         OpenID Connect sign-in
  components/   shell (nav rail + identity), shared UI, formatting helpers
  features/     one folder per screen
  styles/       design tokens, base styles, component styles
```

Tables are real `<table>` elements, the permission toggles are real checkboxes, and every screen works
from the keyboard. Changes to permissions and descriptions are staged and saved explicitly; nothing is
written by clicking a cell.
