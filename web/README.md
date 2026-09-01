# torve serve dashboard

The `torve serve` frontend: a read-only single page over the two JSON
endpoints the server exposes. It renders the board, escalations, findings
ledger, proposals, gate health, cost and token shape, and programme sections
in the order the context projection itself carries them, and polls both
endpoints every few seconds. The projected-at stamp in the header is the
projection's own timestamp, so staleness is visible even when the numbers
look healthy.

- React + shadcn/ui + Tailwind, built with Vite.
- Data comes from `GET /api/context` and `GET /api/status`; the page renders
  what the projection already carries and derives nothing of its own.
- Node is a build-time concern: the bundle ships as built assets and the
  running server never touches node.

## Build

```bash
./scripts/build.sh
```

The script installs the pinned dependencies and runs the production build
into `src/torve/_web`, the package-data path the server serves from a
development checkout. Whether the produced bundle is committed to the
repository is a release-pipeline question, not this script's.

## Develop

Run the server against a checkout that has a corpus, then start Vite's dev
server against it:

```bash
torve serve --root /path/to/a/repo
cd web
npm install
npm run dev
```

The dev server proxies nothing — point it at the serve port (`localhost:7433`)
and both endpoints resolve by path. The bundle is only involved in the
production path (`npm run build` or `scripts/build.sh`).

## Layout

- `src/App.tsx` — the page: header stamp, error surface, sections.
- `src/components/sections/` — one component per projection section.
- `src/components/ui/` — shadcn/ui primitives.
- `src/lib/api.ts` — the endpoint types, mirroring the projection shapes.
- `src/hooks/use-poll.ts` — the polling loop; `use-now.ts` keeps ages live
  between polls.
- `scripts/build.sh` — the production build into `src/torve/_web`.
