# The torve operator surface

React + Tailwind + TanStack Table over the two `torve serve` endpoints
(RFC 0032; A-77): eight tabs, sortable data tables, filters, glass. Built
with Vite into `src/torve/_web/`, which ships as wheel package data — the
build is deterministic per lockfile, so CI byte-compares the vendored
bundle against a fresh build.

Develop: `npm install && npm run dev` (proxies /api to a running
`torve serve` on 7433). Ship: `scripts/build.sh`.
