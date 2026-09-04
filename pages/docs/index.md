# Torve — architecture review

This site documents the architecture that **emerged** from executing RFCs
0001–0043, written for one purpose: to let the owner judge whether the design
that grew is the design we want — especially where the local-regime
assumptions meet the distributed future.

It is written independently of the corpus (it links to RFCs, it never
restates their decision tables) and it is deliberately opinionated in one
place only: [The fault line](architecture/distribution.md) names the
assumptions that do not survive distribution and the decisions that pin them.

## Reading order

1. [System overview](architecture/overview.md) — the layers and who talks to
   whom. Five minutes.
2. [The execution model](architecture/execution.md) — a task's life: attempt
   loop, gates, review, landing, escalation.
3. [State and truth](architecture/state.md) — the git/store boundary (D-27)
   and what lives on each side.
4. [The tracker outbox](architecture/tracker-outbox.md) — the contested
   design. The D-42.5 no-fit verdict, the owner's three counters, and what
   each would actually change.
5. [The fault line](architecture/distribution.md) — every single-node
   assumption in the engine today, and the three-regime picture.

## How to view

```bash
uvx zensical serve pages/   # live at http://localhost:8000
uvx zensical build pages/   # static site into pages/site/
```

Diagrams are [D2](https://d2lang.com) sources in `pages/diagrams/`, rendered
to SVG by:

```bash
cd pages && for f in diagrams/*.d2; do
  d2 --theme 0 --dark-theme 200 --pad 12 "$f" "docs/assets/diagrams/$(basename "$f" .d2).svg"
done
```
