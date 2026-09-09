# Torve — how the engine works

Torve runs a standing team of coding agents against a specification corpus:
RFCs with graded decision tables are the input, sandboxed task execution
under a gate battery is the machine, and landed commits are the only output
that counts.

This site is documentation for whoever operates or extends the engine. It
explains shape and reasons — what the parts are, why they are separated the
way they are, and which failures each separation exists to prevent.

!!! note "What this site never does"

    It never restates a decision. Every graded decision lives in the corpus
    under [`rfcs/`](https://github.com/morzecrew/torve/tree/main/rfcs), with
    an id, a grade and the paths it governs; this site links to them and
    stops there. A third copy of a decision is a third thing that can
    disagree with the other two, and the engine has spent a lot of its life
    removing exactly that.

If you are here to *run* the engine rather than to understand it,
[Operating the engine](operating.md) is the page you want: the verbs, which
carrier answers a report, and why a single un-triaged escalation stops new
work.

## Reading order

1. [System overview](architecture/overview.md) — the layers, the actors, and
   who talks to whom. Five minutes.
2. [The record](architecture/record.md) — the event log that is the system
   of record: its vocabulary, who may write what, and what is projected from
   it.
3. [The execution model](architecture/execution.md) — a task's life from
   mint to landing, and what each step writes down.
4. [The live channel](architecture/channel.md) — what the engine can see
   while a run is in progress, and what it deliberately cannot.
5. [State and truth](architecture/state.md) — which carrier holds what, and
   which ones are projections of another.
6. [What does not distribute](architecture/distribution.md) — the
   assumptions that are still single-node, and what each would cost.

Two older pages are kept as [records of decisions](decisions/deep-pass.md):
the external review that produced S-0044, and the tracker-outbox argument
it settled. They are dated and marked; they are not documentation of what
the engine does now.

## Running the site

```bash
just docs             # live at http://localhost:8000
just docs-diagrams    # re-render the D2 sources to SVG
```

Diagrams are [D2](https://d2lang.com) sources under `pages/diagrams/`.
