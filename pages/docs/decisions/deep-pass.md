# The deep pass — meaningful or garbage?

!!! info "A record, not documentation — 2026-09-03"

    This is the external review that produced S-0044. It describes the
    engine as it stood on 2026-09-03 and argues for replacing part of it;
    both the description and the argument are kept unedited, because the
    rebuild rests on them, and an argument revised after it won is not
    evidence of anything.

    What the engine does now is [the architecture
    section](../architecture/overview.md). What was decided here is
    [S-0044](https://github.com/morzecrew/torve/blob/main/rfcs/0044-the-manager-domain.md).

Written 2026-09-04, after the thirteen-contract queue closed, in response to
the owner's challenge: *"persistence can hold the truth, repo can project
it... we need yet another deep pass to see from an external point what we
are doing and whether it is actually something meaningful or just garbage."*

This page takes the external stance seriously: no loyalty to the corpus, no
defense of decisions because they are LOCKED. Where the owner's counters are
right, it says so; where something survives scrutiny, it says why with
evidence from our own execution telemetry — the one thing an outside
reviewer would not have.

## The verdict up front

**The experiment is meaningful; the vehicle is not the product.** Torve as a
codebase is a v1 laboratory whose most important output is not the code — it
is a set of validated findings about how autonomous execution actually fails
and what actually contains it. Several of those findings, read honestly,
argue *against* the current architecture. The owner's instinct to invert the
truth boundary is supported by our own data, not just by taste.

The strongest single piece of evidence: in the S-0034/D-9 measurement window,
**every poison-ceiling was a form conviction — six of them — and zero were
functional**. Unquoted YAML scalars, evidence-grammar mismatches, unstaged
files. The work was green; the *machine interface* — hand-written repo files
judged by grammar gates — was what failed. When the dominant failure class
of a design is the seam itself, the seam is wrong. That is the external
view's headline, and it comes from torve's own telemetry.

## The owner's six points, examined

### 1. "RFCs are the wrong seam — a task is the main execution unit"

**Right, with a precision.** What the RFC construct actually carries is
three separable things: *provenance* (where the intent came from), *graded
constraints* (the decision table — LOCKED/ASSUMED/OPEN with paths), and a
*minting shape* (phasing → contracts). Only the markdown document welds them
together. Execution history already shows the weld straining: bugfixes,
external audits, review findings, and operator asks all had to be dressed as
RFCs or amendments to enter the system, and `rfc_parse` is a regex scraper
over prose — a fragile machine interface pretending to be a human one.

The keeper is the **graded-constraint discipline**, not the document form.
In a persistence-first design: a `Source` (any provenance — RFC text,
incident, audit, a review finding that minted a follow-up), `Decision` rows
as first-class versioned records with a queryable graph, and `Task` as the
execution unit inheriting constraints by path intersection. The decision
graph then costs a query, not a repo scan — exactly the owner's point.

### 2. "Dependencies are logical for tasks and for sources"

Agreed, and it is nearly free: `depends_on` already exists on both tasks and
documents; the generalization is one DAG over typed nodes instead of two
half-DAGs joined by convention. Nothing in the current semantics (dependency
satisfied only by a landing) needs to change — that rule is one of the
keepers.

### 3. "We could have a resident process... same for agent communication, same for the whole single-node inventory"

**Agreed — with one invariant worth rescuing from each doctrine before it
is dropped.**

- *Tick-not-daemon* (S-0019/D-1) bought crash-correctness by construction and
  trivial observability while the loop was fragile. That job is done; a
  durable-execution substrate provides both properties in resident form.
  The doctrine is scaffolding, not charter. Let it go.
- *No agent communication* (D-31) has a load-bearing core that is **not**
  "no communication": it is *no ephemeral, prompt-level entanglement*. The
  owner's own formulation — "strict, type-safe and through a persistent
  layer" — preserves exactly what mattered (auditability, replayability, no
  context contamination) while dropping the blanket ban. Typed messages in
  the event log are reviewable artifacts; two agents sharing a context
  window are not. Keep the invariant, drop the prohibition.
- The single-node inventory (the [fault line](../architecture/distribution.md)
  table) dissolves almost entirely under manager/workers-over-persistence.
  The one item that survives any architecture: landings serialize *per
  repo*. Multi-repo scaling turns that from a wall into a partition key.

### 4. "Derive, don't record is not efficient and can drift if the derivation source changes"

**The drift half is correct and is the sharper argument.** Derivation
re-interprets history through the *current* schema: change the state shape
and every rebuild silently reinterprets the past. An event log inverts this
— record at write, immutable, with derivation demoted from hot path to
rebuild path. The efficiency half matters less (the sweeps are small), but
the drift half is exactly the standard argument for event sourcing, and it
lands.

Honesty requires the flip side: derive-don't-record was chosen when the
only durable thing was git and the mock store was a torn JSONL. It was the
right call *for that substrate*. With Postgres-grade persistence as truth,
record-at-write with idempotent handlers is simply the correct default, and
S-0008/D-2's rebuild property is preserved as "replay the log", which is
stronger, not weaker.

### 5. "Self-hosted durable stuff vs maintained Temporal — not completely sure"

The right amount of unsure. External view: this is an **adapter decision,
not a domain decision**, and the only architectural mistake available here
is coupling the domain to either answer. Temporal buys maturity, visibility
UI, signals/queries — and operational weight (a cluster) or a bill (Cloud).
forze buys control and zero external dependency — and a maintenance
obligation the product will feel at every scaling step. The hexagonal move:
define the manager's workflows in domain terms, keep the durable-execution
engine behind a port, bind forze first (it exists and is understood), and
let the Temporal question be answered by operational pain, not upfront.
What must be owned either way is the **persistence schema** — events,
tasks, decisions, verdicts. That schema *is* the product; the runner is a
tenant.

### 6. "Tracker projections need a big refactor — not important now"

Agreed on both halves. In the v2 shape the tracker becomes an ordinary
projection consumer of the event log and the current pain (including the
whole [outbox debate](tracker-outbox.md)) evaporates as a
side effect. Park it.

## What the telemetry says survives

An external reviewer with our data would sort the codebase into three piles:

**The IP — carries over nearly intact.** The gate battery with sabotage
twins (gates that prove they can fail); review-as-a-run with the
evidence-locatability discard rule — it caught seven genuine blockers in
one queue with zero noise, including two defects (`why.py`, the bind
precedence) that would have shipped; the closed escalation vocabulary;
the poison ceiling; the measurement regime (config-hash regimes, shadow
replays, eval arms, character/axis routing) — this is the part almost no
other agent harness has, and it is what made every claim in this document
checkable. These are domain libraries; they do not care what substrate
hosts them.

**The lessons — carry over as design inputs, not code.** Warm starts and
image provisioning; the index-helps-structural / hurts-routine finding;
the credential-broker shape; sizing calibration; the review corpus of
escapes.

**The vehicle — dies without regret.** The mock store, the YAML-file
machine interface, the rfc-parse scraper, the operator shell chain, the
run-state files, the tick loop, the tracker glue. Note what else dies with
the YAML seam: the *entire dominant failure class*. Divergence entries
written through a typed intake (a tool call into persistence, projected
into the landing for human review) cannot be unparseable, cannot violate
evidence grammar, cannot be left unstaged. Six poison-ceilings and three
unstaged-artifact escalations — the whole triage burden of the last
week — are artifacts of hand-written files as protocol. S-0043 patches
this class; the v2 seam deletes it.

**One structural note on the code itself:** the internal hexagonality is
real (five import-linter contracts, application-never-imports-adapters,
held under agent fire for months). The "pet-project CLI" feel is not in the
layering — it is at the system boundary, where every interface is a file in
a repo. The hexagon is good; it is drawn around the wrong center.

## The v2 shape, stated plainly

The owner's target, made concrete:

- **Truth**: an event log + documents in Postgres. Sources, decisions,
  tasks, attempts, verdicts, landings, seats, repos. Human authority is
  itself an event (an acceptance, a triage signature) — the load-bearing
  half of D-27 ("nothing enters planning without human review") survives as
  a rule about *who may write which events*, not about which filesystem
  holds truth.
- **Manager**: resident, durable, multi-repo. Owns queues, enforces the
  invariants that survive (scope disjointness in flight, serialized
  landings per repo, dependency-by-landing), routes on character/axis,
  escalates to humans.
- **Workers**: stateless claim-pullers — sandbox + harness + battery +
  review. Capability scaling = more workers, more seats/credentials.
  Communication with the manager is typed commands/events through the
  persistence layer — never shared context.
- **Repos**: projection targets and work surfaces. The engine stages and
  lands; agents never hold git discipline responsibilities. Contract
  materialization in, landing commits out, optional generated
  decision-graph docs — the repo is scanned at import/reconcile, not on
  every question.

## Recommended path — not a big bang

Starting fresh is justified; discarding the evidence is not. The sequence
that keeps the proof while replacing the vehicle:

1. **Write the v2 domain charter** as a small document set (~10 decisions:
   the event schema, the authority rule, the typed-communication invariant,
   the per-repo landing partition). This is where the graded-decision
   discipline gets applied to its own successor.
   *Done 2026-09-04 — S-0044 "The manager domain", 13 decisions, three
   mintable phases (event log → divergence intake → manager and worker),
   awaiting acceptance.*
2. **Stand up the persistence schema** behind a durable-execution port;
   bind forze. Import the v1 evidence (telemetry, verdicts, the review
   corpus) as the first data.
3. **Port the IP pile as libraries** — gates, review lane, sizing,
   measurement. Mostly pure already; this step is small.
4. **Run the v2 manager against one repo** (torve itself is the natural
   dogfood target) with the typed divergence intake, while v1 stays
   operational. The exit criterion writes itself: v2 lands its own tasks
   with a lower escalation rate than v1's form-conviction baseline — a
   number we already have.
5. **Multi-repo second.** The partition key is designed in from step 1 but
   exercised only after one repo is boring.

## Answer to the question as asked

Not garbage — but the meaningful thing is not what it looked like. The
meaningful thing is a **validated doctrine of contained autonomous
execution** (graded constraints, gate batteries that self-test, adversarial
review as a lane, escalation as a closed vocabulary, measurement as a
first-class regime) plus the telemetry proving which parts earn their keep.
The CLI, the YAML, the file seams — the parts that felt like the project —
are the packaging of the experiment, and the experiment's own results
recommend replacing them. Feeling fine about dropping the tasks and files
is the correct instinct; the thing worth not dropping fits in a schema and
four libraries.
