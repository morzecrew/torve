# Amendments — RFC corpus

Changes to already-accepted decisions, discovered during implementation. Each amendment states what changed, what deliberately did not, and which documents to edit.

**These are amendments, not a new RFC.** A separate document would create a second description of the same format, and the stale one would have the lower number and get read first. Apply the edits in §5 to the existing corpus and keep this file as the record of why.

Every amendment here follows the process the corpus specifies: implementation disagreed with a `LOCKED` decision, stopped, and returned to a human. That is `flag-dont-flip` applied to Torve itself.

---

## A-1 — Log serialization

**Amends:** D-21a · charter §6

**Found in implementation.** Extracting entries from markdown required a regular expression. A regex pulling data out of a document is a reliable sign the data is in the wrong container. Task contracts were already `tasks/T-nnnn.yaml`; the log being markdown was an inconsistency with no reason behind it.

**Changed:** `logs/<task-id>.md` with fenced ` ```divergence ` blocks → `logs/<task-id>.yaml`, one `entries:` list.

```yaml
schema_version: 1
task: T-0142
entries:
  - decision: D-3
    grade: LOCKED
    kind: contradicted
    at: 2026-08-20T11:04:12Z
    attempt: 2
    claim: sessions cannot live in Redis; no Redis service in this deployment
    evidence: infra/compose.yaml:1-40 — no redis service defined
    action: halted
    notes: |
      Tried a sidecar container; the compose file is generated from a
      template in another repository, so it cannot be changed from here.
```

**Unchanged — the substance of D-21a stands in full:** one file per task, append-only, grade copied at write time, silence is a finding, evidence must be locatable. Only the serialization changed.

**Prose stays inside the entry**, in `notes:`. A separate `.md` beside the `.yaml` was considered and rejected: nothing would guarantee the two describe the same entries, and within a month they diverge — producing two sources of truth in the one artefact that exists to have exactly one.

**Rejected alternative:** JSONL. Strictly safer for appending (`>>`, line-wise conflicts, no parser) but materially worse to read in a pull request during escalation triage. One agent writes a given log at a time, so the indentation risk is small. Revisit if concurrent writes to one log ever become possible.

**Migration:** a single-use `scripts/migrate_logs.py` reads `.md`, writes `.yaml`, deletes the original. **The gate does not accept both formats.** Dual-format support in a gate is two code paths forever, two branches in tests, and two ways to write the same thing; within six months nobody remembers which is canonical. Compatibility lives in the converter and dies with it.

---

## A-2 — Gate implementations belong to the package

**Amends:** RFC 0002 §4 · SKILLS-REFACTOR §5

**Found in implementation.** `log_check.py` was shipped inside the `flag-dont-flip` skill directory, so every repository installing the skill got its own copy — precisely the cross-repository copy-paste that RFC 0002 exists to remove.

**Changed:** gate implementations live in `torve`, not in skill directories.

```
src/torve/gates/
  scope.py
  decisions_reported.py      # was flag-dont-flip/scripts/log_check.py
  no_test_tampering.py
  secrets.py
  sabotage.py
```

The skill keeps one line naming its enforcing gate and loses its `scripts/` directory.

**A skill is not replaced by its gate.** The gate reports that an entry is missing; it cannot say when one should have been written. `flag-dont-flip` retains the plan gate, the readiness gate, the unlisted-decision rule, and how to phrase `claim` and `evidence`. Delete the skill and the gate reddens on every task, costing an iteration each time. Per D-9.5: the gate is the source of truth, the skill is how it is passed on the first attempt.

---

## A-3 — Skills whose format Torve parses ship with Torve

**Amends:** RFC 0009 · SKILLS-REFACTOR §4

**Found in implementation.** A skill and its gate encode one rule in two forms. Versioned separately, they drift: the gate tightens in the package, the skill in another repository does not know, and agents write to the old rule and redden on every task.

**Changed:** a skill and its gate are **one unit of versioning**. Skills whose output Torve parses move into the Torve repository and ship with the package.

**Ownership rule.** General practice lives in `agent-skills`. Anything whose format Torve parses lives in Torve.

| Skill | Home | Parsed by |
| --- | --- | --- |
| `flag-dont-flip` | **torve** | `decisions_reported` |
| `rfc-writer` | **torve** (specialised — see the specialisation guide) | `RfcDirectory`, `rfc_index` |
| `ratchet-what-you-build` | **torve** | `sabotage` |
| everything else | `agent-skills` | nothing |

`escape-hatch-policy` was initially on this list and removed on review: the existing skill is about API and abstraction design, while RFC 0002 §6a's gate bypass is a human sign-off with no agent involvement. Same word, different concept. Three skills move, not four.

**The boundary is narrow and should stay narrow.** The test is: *does Torve parse what this skill produces?* An execution log — yes. A sabotage set — yes. A bypass record — yes. An RFC decision table — yes. "Check that you actually ran the tests" — no; that is general practice, and Torve reads an exit code, not the skill.

Without this line, `self-audit`, `reading-isnt-proof` and most of the verification cluster get pulled in on the grounds that they are "about execution too", and Torve ends up owning fifteen skills instead of four.

**Distribution: none required on the engine path.** The runner composes the sandbox and writes the role-scoped skill set into it from package data at dispatch time. Nothing is checked into the consuming repository, so nothing can drift, and the skill version is the Torve version by construction — skill and gate stay one unit of versioning structurally rather than by policy. For interactive human use outside the engine, ordinary ecosystem distribution applies; do not build a bespoke installer.

**Consequence for D-9.3:** `config_hash` must include the **Torve package version**, not only the `agent-skills` lockfile. Otherwise upgrading Torve silently changes the regime and telemetry does not notice.

---

## A-4 — Git and store: a boundary, not a prohibition

**Amends:** D-27

**Found in review.** D-27 read as "nothing ever moves from git to a database", which raised a fair question: how does anything query execution history, then? The decision was written as a ban when it is a division of authority.

**The boundary:**

| | git | store |
| --- | --- | --- |
| Holds | what should be | what happened |
| Contents | task contracts, gate manifests, RFCs, decision tables, divergence logs | run state, leases, attempts, gate results, findings, telemetry, outbox, idempotency |
| Needs | diffability, pinning to a sha, review | transactions, fencing, concurrency, queries |
| Changes by | pull request | append-only stream |

**Clarified:** the engine **may** index git-held artefacts into the store for querying. What is forbidden is the reverse — making the store authoritative for them. The direction is one-way: git → store, read-only projection.

This is the same pattern already accepted in RFC 0008 for trackers: authority in one place, projections elsewhere, one direction. It was described there and merely assumed here.

**Also clarified — task contracts are derived artefacts.** `torve plan` mints them mechanically; calling them "reviewed intent" overstated it. They belong in git as a lockfile does, and the reason is stronger than review:

- **Reproducibility.** An attempt is pinned to a sha. Six months later, reconstructing why something landed retrieves exactly the contract the agent saw — not the current version, not a row someone edited. That makes an attempt replayable, which a database row cannot, even append-only, because there is no commit to pin it to.
- **Refusability.** The test is not "a human wrote this" but "a human can see it in a diff and refuse it."

The store holds only a reference: `task_id` plus sha.

---

## A-5 — Agents do not communicate; the runner coordinates

**Amends:** nothing — recording an implicit decision that was questioned and confirmed

**Raised in review:** if execution facts live in a store, how do agents become aware of what other agents are doing?

**They do not, by design.**

- What an agent may touch is **copied** into its contract.
- What has already been decided is **copied** there too, with grades.
- What others are doing right now is known to the **runner**, which uses it to avoid dispatching overlapping tasks. The agent neither sees it nor should.

Shared memory between agents sounds like a team getting smarter and delivers cross-contamination of context and non-determinism in exactly the place this design removes it. D-2a already locks the narrow case: an executor that can read other tasks' escalations is an executor that can rationalise its way out of its own scope.

Quality here comes from every agent receiving a complete, isolated, non-overlapping contract — not from agents sharing knowledge. Knowledge accumulates as facts in the store and is read once per phase by a human with an expensive model, who writes the next contracts. The bottleneck sits where judgement lives, deliberately.

**Falsifiable prediction, so this can be revisited on evidence:** if the model is wrong, the symptom is recognisable — tasks escalating with "insufficient context about adjacent work". Until that appears in telemetry, no change.

---

## A-6 — Substrate schema provisioning is ours

**Amends:** RFC 0003 §7

**Found in implementation.** §7 states that substrate tables have their own provisioning path. They do not — schemas are documented in docstrings and no migrations are shipped. The claim was inferred from the fact that the self-hosted durable tier runs on Postgres, and never verified.

**Changed:** Torve owns migrations for substrate tables (outbox, inbox, run store, step store, schedules, idempotency, distributed locks) as well as for its own document tables. One set, Postgres only.

Multi-backend was already moot under D-3.6 — mock for tests, Postgres for any real run — so this is one alembic tree and a few hundred lines of DDL, not a matrix.

**The schema contract is enforced by test, not by file.** The differential conformance battery runs the same properties against the mock and a real Postgres via testcontainers; DDL that does not match what the adapters expect fails it. Running that battery against the migrated database is therefore a **required gate**, not an optional check. The contract exists; it is expressed as a test rather than as a migration file.

**Rejected — generating migrations inside forze.** It is a backend engine, not a migration generator, and generated migrations need review regardless, so the convenience is smaller than it looks.

**Consequence, and the real cost of this finding:** substrate schema versions are pinned alongside the forze version. A forze upgrade that changes a substrate schema becomes a migration task in Torve, not a silent `pip install -U`. Add the pin to `config_hash`.

---

## 5. Edits to apply

| Document | Edit |
| --- | --- |
| charter §3 | task contracts described as derived artefacts, lockfile-grade; refusability and sha-pinning as the rationale (A-4) |
| charter §6 | log example becomes a YAML file with an `entries:` list (A-1) |
| charter §7, D-21a | amendment marker — A-1; substance unchanged |
| charter §7, D-27 | reworded as a boundary; one-way projection git → store explicitly permitted (A-4) |
| charter §7 | new: agents do not communicate; the runner coordinates (A-5) |
| charter, new §10 | this amendment log, or a link to this file |
| RFC 0002 §4 | `decisions-reported` implemented in the package, not in a skill (A-2) |
| RFC 0002 §6a | bypass record shape and `bypass-count` gate stated here; no skill involved, the signer is always human (A-3) |
| RFC 0009 | new decision: a skill whose format Torve parses ships with Torve; skill and gate are one unit of versioning (A-3) |
| RFC 0009 D-9.3 | `config_hash` includes the Torve package version (A-3) and the pinned forze version (A-6) |
| RFC 0003 §7 | substrate migrations are ours; conformance battery against the migrated database is a required gate (A-6) |
| SKILLS-REFACTOR §4 | four skills marked as relocating; `flag-dont-flip` loses `scripts/` |
| `agent-skills` README | note that three skills moved to Torve and ship with it — not a silent deletion, or someone recreates them from memory |

## 6. Migration order

Four tasks, in this order. The first is a good candidate to run through Torve itself — narrow, clean boundaries, and it inherits exactly the decision being amended.

1. **Gates into the package.** `log_check.py` → `src/torve/gates/decisions_reported.py`, registered in the gate manifest, sabotage cases moved with it.
2. **`scripts/migrate_logs.py`**, single-use, no dual-format support in the gate.
3. **Migrate Torve's own logs first.** Dogfooding, and it tests the converter on real data before other repositories see it.
4. **Skills relocated and specialised**, then the note added to `agent-skills`.
