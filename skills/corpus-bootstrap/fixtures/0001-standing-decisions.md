---
id: "0001"
title: Standing decisions
status: draft
depends_on: []
informed_by: []
supersedes: []
superseded_by: null
amended_by: []
owner: <adopter>
description: >-
  The brownfield baseline for this repository: the standing rows the survey
  extracted from its history, drafted by the corpus-bootstrap skill for a
  human to edit, commit and accept.
schema_version: 1
---

# RFC 0001 — Standing decisions

- **Scope:** The baseline for this repository — the standing rows the survey
  of its last five landings on `main` implies, extracted from the survey
  report and the repository's history. From acceptance these rows govern
  work minted after it, never the tree as it stands and never its history.
  No phasing: this document is not a plan.
- **Related:** the survey report it was extracted from, `fixtures/survey-report.json`
  in the corpus-bootstrap skill.
- **Origin:** extracted by the corpus-bootstrap skill from the survey of the
  last five landings on `main`.

---

## 1. Summary

The survey found two crossed boundaries and three gates that never measured
a single landing. The secrets boundary was defended: a key landed in
`src/leak.py` and the next landing removed it, so that row stands `LOCKED`.
The source-layout boundary was crossed once and left uncorrected in the
window, so its row stands `ASSUMED`. The three gates that never measured
anything for want of a corpus — test licensing, decision recording, and the
self-audit log — each get an `ASSUMED` row that gives them something to
measure once work is minted.

## 2. The survey findings

- **`secrets` fired** on landing `f901a7b` ("chore: wire stripe
  credentials"): a new file `src/leak.py` carried an access key. Landing `d55fecb`
  ("fix: rotate credentials out of the tree") deleted the file and added a
  `.env.example` placeholder, and measured clean — the boundary was
  defended in the window.
- **`scope` fired** on landing `8ef5b12` ("chore: deployment script at
  repository root"): `scripts/deploy.sh` landed outside the manifest's
  `src/**` and `.env.example` allow. No later landing corrected it in the
  window, and the tree's `src/` home for product code holds across the
  other four landings.
- **`no-test-tampering`, `decisions-reported`, `self-audit` skipped every
  landing** with the no-task skip: no contract has ever existed in this
  repository, so nothing gave them a spec to check against. The survey's
  `corpus_adds` names exactly these three — the corpus's absence made
  visible.

## Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-1.1 | `LOCKED` | Credentials never enter the source tree; secrets are injected from the deployment environment | `src/**` `.env.example` | The repository's history defended this boundary: the survey's secrets gate fired when a key landed in `src/leak.py` and the next landing removed it; crossing it again is expensive to undo |
| D-1.2 | `ASSUMED` | Product code lives under `src/**`; operational scripts live under `scripts/**` | `src/**` `scripts/**` | The survey's scope gate fired once on a root-level deploy script and stayed clean on the other four landings; whether the layout is deliberate is the acceptance edit's call |
| D-1.3 | `ASSUMED` | Test files are edited only under a task's licence; adding a test is an addition, editing one needs scope | `tests/**` | The no-test-tampering gate never measured a single landing — no task existed — so this row gives the gate a spec once work is minted |
| D-1.4 | `ASSUMED` | Work touching governed areas records its decisions in the task log | `.torve/tasks/**` `logs/**` | The decisions-reported gate never measured a landing; this row is the corpus layer that turns its no-task skip into something that can fire |
| D-1.5 | `ASSUMED` | Execution logs carry the declared drift count | `.torve/tasks/**` `logs/**` | The self-audit gate never measured a landing; this row names the log discipline the gate reads |
