---
id: "0010"
title: VCS, provenance and revert
status: accepted
implementation: complete
depends_on: ["0003"]
informed_by: ["0005", "0006"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  How agent work becomes commits and pull requests, provenance trailers, signing at the runner boundary, and revert as a task role.
schema_version: 1
---

# RFC 0010 — VCS, provenance and revert

- **Implementation state:** phases 1–2 executed 2026-08-23 (T-0044 provenance/signing/revert; T-0045 the credentialed forge leg — live: the engine pushed and opened Misery7100/torve-remote-lab#1). All three §9 exit criteria demonstrated. Standing open: D-10.9; the state-conditional force-push guard becomes meaningful with the forge polling leg (today no force path exists at all, which is stronger); vault injection maps to a named environment variable until the substrate's vault arrives
- **Scope:** How agent work becomes commits, branches and pull requests; who signs them; how they are attributed so history explains itself; and how landed work is undone. Excludes merge ordering (RFC 0006) and conflict resolution (permanently out of scope).
- **Inherits:** D-4b (agents hold no credentials), D-6, D-22 from RFC 0001

---

## 1. The rule everything here follows

**The agent produces code. The runner produces every forge artefact.**

Commits, branch pushes, pull-request bodies, review comments, labels — all composed and issued by the runner from data the agent returned. The agent's output is a working tree and a set of records; it never speaks to the forge.

This is not tidiness. It is what makes D-4b achievable: an agent that never issues a push never needs a token, and a credential that is never present cannot be exfiltrated. Every exception to this rule reintroduces the credential.

## 2. Two ports, deliberately separate

| Port | Owns |
| --- | --- |
| `Vcs` | local git: worktree, `merge-base` diff, commit, rebase, revert, sign |
| `Scm` | remote forge: create pull request, comment, statuses, merge |

Merging them binds the domain to one vendor. Keeping them apart also puts the credential boundary in one place: `Vcs` operations are local and need no secret, `Scm` operations go through the vault.

## 3. Branches and commits

Branch names derive from the task id, consistent with the derivation rule in RFC 0003:

```text
torve/T-0142
torve/T-0142-review        # review runs that produce artefacts
```

Commits carry provenance in trailers, because six months later `git log` is the only surviving explanation:

```text
feat(api): rotate session keys on privilege change

Torve-Task: T-0142
Torve-Attempt: 2
Torve-Agent: harness/deepseek-v4-flash@2026-07-11
Torve-Config: 7f3a91c
Torve-Decisions: D-3(LOCKED) D-7(OPEN)
```

- **Author** is the agent identity — not a human's name, ever. Attributing machine work to a person corrupts blame, review statistics and, in some jurisdictions, authorship claims.
- **Committer** is Torve.
- `Torve-Config` is the same `config_hash` as the telemetry record, which is what lets a bad commit be traced back to the regime that produced it.
- Trailers are machine-parseable, so `git log --grep` reconstructs a task's history without the store.

**One commit per attempt**, not per file edit. An attempt is the atomic unit everywhere else in this design; history should agree.

## 4. Signing

If a repository requires signed commits — and it should — the agent cannot hold the key. D-4b makes that non-negotiable.

**Signing happens at the runner boundary**: the sandbox produces the tree and the commit message; the runner creates and signs the commit outside the sandbox, with a key the agent never sees. The signature therefore attests *"Torve produced this under task T-0142"* and not *"a human reviewed this"*, which is the honest claim and should be documented as such so nobody reads a green verification badge as approval.

## 5. Push and force-push

- Push targets only the task's own branch. Any other target is a bug, not a permission question.
- **Force-push is allowed before review starts and forbidden after.** A rewritten branch invalidates review freshness (RFC 0006 §3) and orphans line comments, so post-review changes are additive commits. The runner enforces this from state, not from convention.
- Credentials reach `git push` through vault injection; the sandbox holds nothing.

## 6. Pull-request composition

Composed by the runner, from data:

- **Title** from the task's summary.
- **Body**: the task contract summary, the acceptance commands and their results, gate outcomes with durations, inherited decisions with grades, execution-log divergences, cost, `trace_ref`.
- **Never** the agent's prose about what it did. That is a self-report, and self-reports are what this system exists to stop trusting. If the agent has something to say beyond code, it belongs in an execution-log entry with evidence.

The pull request therefore reads as a claim with proof attached, and a reviewer can check the proof without opening a terminal.

## 7. Revert as a role

Escaped defects are already a metric in RFC 0005; until now nothing has owned the response.

**Revert is a task role**, like implement and review — same contract, same gates, same telemetry:

```yaml
id: T-0207
role: revert
targets: [T-0142]              # or explicit commit shas
scope:
  allow: ["packages/api/**"]   # inherited from the reverted task
acceptance:
  - pnpm test tests/api
  - pnpm typecheck
```

Behaviour:

- **Prefer `git revert` over reconstructing a fix.** A revert is reviewable, mechanical and reversible; a corrective patch under time pressure is none of those.
- **Reverts pass the same gates.** A revert that breaks the build is not an emergency exit, it is a second incident.
- **Reverts go through the same merge lane.** Urgency is not a reason to skip ordering — that is precisely when ordering matters.
- **A revert is not a rollback of production.** Deployment is outside this system entirely; the revert lands in the repository and whatever pipeline exists takes it from there.
- A dependent-commit conflict escalates as `merge_conflict`. Torve does not resolve it (D-6).

**Every revert emits a `resolved` execution-log entry against the decisions the original task inherited.** This is the loop paying off: the reason the work was undone reaches the next planning session as a candidate decision-table row rather than as folklore.

*Execution note 2026-08-23 (T-0044):* reverts are not review-triggering in v1 — the diff is a mechanical inverse of already-reviewed work, and the engine may not resolve anything by hand; if reverts ever carry hand-resolved content, that calculus changes, but that content is exactly what D-6 forbids the engine to produce.

## 8. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-10.1 | `LOCKED` | The agent produces code; the runner produces every forge artefact | `src/torve/application/runner.py` `src/torve/application/ports.py` | The mechanism that makes D-4b achievable |
| D-10.2 | `LOCKED` | Commit author is the agent identity, never a human | `src/torve/adapters/vcs/git.py` | Attributing machine work to a person corrupts blame and review data |
| D-10.3 | `LOCKED` | Signing happens outside the sandbox, at the runner boundary | `src/torve/adapters/vcs/git.py` `src/torve/config/runconfig.py` | The agent cannot hold the key |
| D-10.4 | `LOCKED` | Provenance trailers on every commit, including `config_hash` | `src/torve/adapters/vcs/git.py` `src/torve/application/runner.py` | The only durable link from a bad commit back to its regime |
| D-10.5 | `LOCKED` | Force-push forbidden once review has started *(scoped by amendment A-34 2026-08-24, registered on RFC 0019 D-19.12: at landing, after the sha-bound approvals conclude the review, the engine republishes its own candidate branch to the landed tip with lease — the republish is what keeps this row's line comments anchored to landed code)* | `src/torve/application/runner.py` `src/torve/adapters/vcs/git.py` | Protects review freshness and line comments |
| D-10.6 | `LOCKED` | Pull-request bodies are composed from data, never from agent prose | `src/torve/application/forge.py` `src/torve/adapters/vcs/git.py` | A self-report is not evidence |
| D-10.7 | `LOCKED` | Revert is a role, preferring `git revert`, passing the same gates and the same lane | `src/torve/application/runner.py` `src/torve/domain/task.py` `src/torve/adapters/vcs/git.py` | An unreviewed emergency path is how the guarantees get bypassed |
| D-10.8 | `ASSUMED` | One commit per attempt | `src/torve/adapters/vcs/git.py` | Depart if attempts routinely produce unrelated changes — which is itself a task-size finding |
| D-10.9 | `OPEN` | Whether review-run artefacts get their own branch or live only as comments | — | Decided when 0005 ships |

## Phasing

*(Added 2026-08-23 while draft, with the path relocation to RFC 0015's tree.
A subset already exists from RFC 0003's exit criterion: `GitVcs.commit_all`
writes the Task/Attempt/Config trailers with committer Torve, `GhScm` opens
pull requests through the gh CLI, `ScmConfig.open_pr` is off by default —
phase 1 completes the local record, phase 2 makes the forge leg real. The
remote and its credential live in configuration, not in this document: the
repository slug and the token's environment-variable NAME (D-4b — never the
value) ride `ScmConfig`. Signing key material likewise: a path in
configuration, held by the runner, never mounted into a sandbox; unset means
unsigned. Review comment posting and PR-triggered review stay with RFC 0005's
forge deferrals; CI polling and the approvals/quiet-window fields stay with
RFC 0006's.)*

```yaml
- phase: 1
  title: Provenance and revert, local
  intent: |
    The runner's commit becomes the full provenance record: author is the
    agent identity from the attempt's agent block, committer is Torve,
    trailers complete — Torve-Task, Torve-Attempt, Torve-Agent,
    Torve-Config, Torve-Decisions with grades — one commit per attempt,
    reconstructible from git log with the store offline. Signing happens
    at the runner boundary when a key path is configured: the sandbox
    produces the tree, the runner signs outside it, and the signature
    attests provenance, never approval. Revert becomes a role: targets
    are legal for role revert, the runner executes git revert of the
    target's landed shas in the worktree — preferring revert over
    reconstruction — the same gates judge it, the same lane lands it,
    and every revert emits a resolved execution-log entry against the
    decisions the reverted task inherited.
  scope:
    - "src/torve/adapters/vcs/**"
    - "src/torve/application/**"
    - "src/torve/domain/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
- phase: 2
  title: The forge leg
  depends_on: [1]
  intent: |
    The Scm surface grows from the 0003 skeleton into the credentialed
    leg, against the remote named in configuration: push targets only the
    task's own branch, with the token injected at the runner boundary
    from a configured environment-variable name — the sandbox holds
    nothing, the config holds the name, only the runner's process holds
    the value. The pull request is composed from data: contract summary,
    acceptance commands and results, gate outcomes with durations,
    inherited decisions with grades, execution-log divergences, cost and
    trace_ref — never the agent's prose. Force-push is allowed before
    review starts and forbidden after, enforced from run state, not
    convention. Exit is live: one task pushed and opened as a pull
    request against the configured remote, its history reconstructible
    from trailers alone.
  scope:
    - "src/torve/adapters/**"
    - "src/torve/application/**"
    - "src/torve/cli/**"
    - "src/torve/config/**"
    - "tests/**"
  acceptance:
    - "uv run ruff check src tests"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run pytest"
    - "uv run lint-imports"
    - "uv run torve rfc check"
```

## 9. Exit criteria

- A task's full history reconstructible from `git log` trailers alone, with the store offline.
- A signed commit produced with no key ever present inside a sandbox.
- One revert executed as a task, passing gates and emitting its execution-log entry.
