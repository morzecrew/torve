# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The planted feedback record leaves the tree before the gates measure
  it (T-0076, RFC 0005 D-5.13): the revision loop's first live firing
  poisoned its own re-runs — the scope gate saw `.torve/feedback.md`
  as an out-of-scope change and failed three attempts in one minute.
  The record is planted per attempt for the agent's eyes and unlinked
  in the attempt's cleanup: feedback steers the attempt, never the
  candidate, and untrusted review text can never ride a commit.
- The board is for humans (T-0075, A-33, RFC 0008 D-8.16): review-role
  tasks project no issues — their attempt summaries post as comments on
  the target's thread and their escalations notify there. The review
  label and sub-issue nesting retire with the rows they decorated;
  legacy review issues keep the landings close-out. The CI adapter
  gains the T-0058 transient retry — one in seven lane legs was dying
  on a flaked actions call.
- The revision loop (T-0074, A-32, RFC 0005 §4a, D-5.12/D-5.13):
  retry captures the previous candidate's diff and the PR's
  line-anchored review threads from `review.feedback_from` logins —
  verbatim, whole, attributed, size-capped with recorded truncation —
  and the re-run gets the record in its sandbox with a prompt that
  names it untrusted review data: revise, not restart. Empty
  allow-list = off; scope, gates and the sha-bound approval unchanged.
- A-31 (T-0073): a dependency is satisfied only by its landing — the
  ready clause admitted a dependent whose worktree was cut from a base
  missing its foundation, which the approvals regime turned from a
  benign race into a systematic failure. Invisible in the automatic
  regime, where the lane precedes dispatch inside the tick.
- An ff landing closes its own pull request (T-0072): comment naming
  the landing sha and where the approval lives, head branch deleted —
  the reading surface retires with the work.
- The eval loop (T-0070 + T-0071, RFC 0009 §5, D-9.14/D-9.15): every
  record names the skills materialized for its attempt, and `torve eval
  <skill> --task ...` replays each task twice in shadow — configured
  role sets versus the sets with the skill removed — landing one
  skill-eval record in the evals ledger with a baseline-matched verdict
  (direction, never magnitude; deletion stays a human act). First live
  eval: flag-dont-flip over two lab tasks — baseline matched.
- Review issues nest under their targets as forge sub-issues (T-0068,
  RFC 0008 D-8.15): the board's top level is the work, the machine's
  meta-work indents beneath it; adapters without the concept refuse
  into the divergence path.
- A-30 (T-0069): a revisited state is a new fact — the state effect's
  key gains the run's transition ordinal, so ready-again-after-a-retry
  re-reflects and the board stops wearing yesterday's escalation label
  over a ready candidate.
- The board says where the human is needed (T-0067, RFC 0008
  D-8.13/D-8.14): an approvals-short candidate gains a needs:approval
  label and an on-thread prompt naming the tip and count (once per tip;
  a superseded tip prompts afresh); review-task issues wear a review
  label; and /torve approve refuses review-role tasks — a review is
  never landed, so there is nothing to approve.
- Board hygiene for unattended operation (T-0065 + T-0066, RFC 0008
  D-8.11/D-8.12): sync closes a landed task's issue via a landings pass
  over the landing trailers (one close-out effect per task, ever; an
  issue is never created just to be closed), review issues close when
  their every target has landed, and setting a state label retires the
  stale state siblings so the board wears exactly one.
- A-29 (T-0064): the repository outranks the host — a task whose
  landing trailer is already in base history is never queued, whatever
  run records the host holds, and the check is authoritative over the
  QUEUED re-entry state (a landed task's re-entry is a revert and a new
  contract). Found by the first scheduled tick against a fresh clone,
  which saw the entire landed history as queued: state files and
  telemetry are host-local, and the reboot that followed proved the
  point twice by wiping them.
- The store joins the doctor (T-0062, RFC 0012 D-12.7's sibling
  question): under a postgres store, `torve doctor` verifies the DSN
  variable is set, the database answers, and no substrate step is
  pending — each failure an instruction — while a mock store names
  itself as the in-process, test-only regime. The currency count
  (`pending_count`) is shared with `migrate --status`.
- The tick's reap leg carries the store factory (T-0063): under a
  postgres store the durable reap — the lease as liveness authority —
  is now reachable from the standing loop instead of erroring on
  missing injection every tick. Found flipping the lab to the durable
  store; with it, RFC 0003's D-3.6 regime went live in dogfood (a
  migrated Postgres behind the lab's dispatch → approve → land cycle)
  and RFC 0003 is judged complete.
- The retry command completes its own re-queue (T-0059, RFC 0008
  D-8.10): `/torve retry`'s apply deletes the task's stale remote branch
  — a ref deletion under the commander's authority, never a rewrite —
  before the state transitions; a failed cleanup refuses with the
  escalation left standing. The loop selects the QUEUED state and the
  runner admits it for re-dispatch.
- One transient retry in the GitHub adapters (T-0058): network-shaped gh
  failures (timeout, TLS handshake, reset, lookup, 5xx) retry exactly
  once; everything else raises unchanged. Idempotent destinations make
  at-least-once safe.
- A-27 + A-28 (T-0056, T-0057): the tick's order is poll, lane, reap,
  dispatch, sync — the lane before the reaper, merge-before-reap inside
  the tick; the loop publishes what it lands (base pushed fast-forward
  only after a landing); the reaper keeps the state file of a READY
  implement/revert run whose task has not landed; the lane adopts
  byte-identical untracked engine records the landing carries. All four
  found by the first live drains.
- The standing loop (T-0055, RFC 0019 phase 1): `torve tick` — one
  bounded pass: reap, poll the board, dispatch at most one queued task
  (no run record, dependencies satisfied, ascending id), the lane only
  under `promotion.auto_merge`, tracker sync last. Tick lock with loud
  stale break; intake pauses while the escalation queue is non-empty;
  one engine event per tick with honest noops. Cadence belongs to the
  environment — there is no daemon.
- Tracker command authorization (T-0054, RFC 0008 D-8.9): a `/torve`
  command applies only when its actor is in `tracker.commanders`; an
  empty list refuses everyone, and refusals are answered on-thread.
- Review on pull-request open and update (T-0053, RFC 0005 §4 forge leg):
  `torve review pr N` — skip rules (draft, empty, closed, configured
  authors), one review per head via the `pr-reviews` ledger, Torve-Task
  trailer mapping to a task contract or degraded input told so
  explicitly, and findings posted back by the runner as one
  marker-deduped comment. Triggers `pr_opened`/`pr_synchronized` join
  the review vocabulary.
- The lane tolerates the engine's own records (T-0052): the cleanliness
  guard refuses only dirt in landed content — now naming the offending
  paths — while runner-minted task directories, the telemetry file, and
  the outbox pair never block a landing. `LaneVcs.is_clean` became
  `dirty_paths`.
- The notifier (T-0051, RFC 0006 D-6.11, closing RFC 0003 D-3.18):
  interrupt-class escalations stage a `notify` effect through the outbox,
  keyed (task, attempt, reason) for exactly-once delivery under replay;
  GitHub delivery is issue assignment (best-effort) plus @mention of the
  configured `tracker.notify` login. Batch-routed escalations stay
  board-visible only; an empty login keeps the notifier inert.

- RFC corpus 0001–0010 under `rfcs/` with index.
- `torve` package: the RFC 0002 gates-library increment.
- Six builtin gates (`scope`, `acceptance`, `no-test-tampering`,
  `decisions-reported`, `self-audit`, `secrets`) plus shell gates via
  `gates.yaml`; cheapest-first ordering with fail-fast on blocking failures.
- `flaky` gate outcome with per-command counters and a reviewed quarantine
  list; `Torve-Bypass` commit-trailer bypass, counted and logged, with the
  secrets gate exempt.
- `torve gates run` / `torve gates check` (17-case sabotage suite) and
  `torve size`; JSONL telemetry stamped with `config_hash` and denormalised
  decisions.
- Dogfood wiring for this repository: `gates.yaml`, task contract
  `tasks/T-0002.yaml`, execution log `logs/T-0002.md`, GitHub Actions CI.
- Runner core (RFC 0003 phase 1): `torve run` — one task, synchronous, exit
  code is the outcome — with the state machine and enumerated escalation
  reasons, git-worktree workspaces, and JSON run state beside the worktree.
- Two Runtime adapters behind one "workspace in, changed files out" contract:
  Docker (bind mount, `--init`, platform-bounded lifecycle) and OpenSandbox
  (tar sync over the files API; SDK as the `torve[opensandbox]` extra), with a
  shared conformance battery.
- `FakeAgent` scripted scenarios (always sandboxed), `torve status`, and
  `torve reap` — convention-driven sweep that expires stale runs as
  `lease_expired`, proven after `kill -9`.
- Shell gates in `torve run` execute in a fresh sandbox the agent never
  touched, via a new executor seam in the gate runner; `torve.yaml` carries
  runner configuration.
- Durability (RFC 0003 phase 2): the attempt loop runs as one durable
  function over forze's run store — real leases, fenced terminal writes,
  cancellation riding the lease heartbeat, and recovery via
  `claim_abandoned`. Mock store for tests and simulation, Postgres for real
  runs (`torve[postgres]`), with torve-owned SQL migrations applied by `torve migrate substrate`.
- `torve cancel` (cooperative, fail-closed on backend capability) and a
  durable reap path that replaces the heartbeat heuristic under Postgres.
- Deterministic simulation (forze_dst): the real attempt loop and real
  TaskStore driven concurrently under one master seed set — four invariants,
  four reachability targets, and four deliberately broken twins the oracle
  must catch.

- Corpus amendments A-1..A-6 (`rfcs/AMENDMENTS.md`) applied: execution logs
  are YAML (`logs/<task-id>.yaml`, single-use `scripts/migrate_logs.py`,
  gate accepts YAML only), gate implementations carry their amendment names
  (`decisions_reported.py`, `no_test_tampering.py`, `gates/sabotage.py`),
  D-27 reworded as a git↔store boundary, and the charter records that agents
  do not communicate (D-31).
- Three specialised skills ship with the package (`skills/`, A-3/D-9.7):
  `flag-dont-flip`, `rfc-writer`, `ratchet-what-you-build` — materialized
  role-scoped into the sandbox at dispatch; no install command by design.
- Hardened `rfc_index.py` (shipped with the specialised `rfc-writer`):
  requires a Paths column, paths on every `LOCKED` row, and unique decision
  identifiers; the corpus decision tables gained Paths columns throughout.

- Migrations per `rfcs/0012-migrations.md`: owner-grouped SQL histories
  (`migrations/{torve,substrate,telemetry}/`), `yoyo-migrations` behind the
  `torve[migrate]` extra (lazy import, exit code 3 with the install hint),
  migrations shipped as wheel package data, `torve migrate <target>` with
  `--all`/`--status`, `torve doctor` enforcing the `FORZE_VERSION` pin, and
  the conformance battery run against fresh *and* populated databases in CI.
  Forward-only, checksummed; `torve store provision` is replaced outright.

- Charter A-12 and 0003 A-13 executed: one directory per task —
  `.torve/tasks/T-nnnn/` holding `contract.yaml` and, once anything was
  written, `log.yaml` — with a three-level fallback chain in
  `config/layout.py` (per-task dir, flat `.torve` layout, root legacy) and
  `task_dir()` as the retention unit (D-A.13). All fourteen existing tasks
  moved via `git mv`; `.torve/logs/` is gone. A missing log is an empty
  log to every reader (D-3.21): `decisions-reported` runs the silence
  check over a synthesized empty document — a touched `LOCKED` area still
  convicts — and `self-audit` passes on absence instead of demanding a
  file into existence (retiring its T-0002 presence check; the
  written-log drift_count claim survives). The FakeAgent writes its log
  at the canonical path, created by its first entry (D-3.20), with a
  Docker end-to-end test proving an entry written before a `kill` is on
  disk. Sabotage suite at 28 cases.
- Real agent adapter mechanics (RFC 0004 phase 1, `implementation:
  partial`): `tier` on the task maps to an adapter through `tiers:` in
  the runner configuration — `api`, `harness` and `subscription` are
  one `HarnessAgent` mechanism whose configured command runs *inside*
  the sandbox (D-4.1, never an SDK in the engine), differing only in
  how authentication reaches the process: named env passthrough (the
  runtime forwards `-e NAME`, the value never transits torve — D-4b)
  or a per-worker-slot auth volume mounted read-write (D-4.2;
  OpenSandbox refuses volumes, its credentials belong to the vault).
  The staged prompt carries the contract's intent, decisions, scope
  and acceptance; the session trace lands beside the worktree as
  `trace_ref`.
- Provider routing enforced at dispatch (RFC 0004 §6b, D-4.8), before
  a sandbox exists: `providers:` names default and per-repository
  allow-lists — no permitted provider for the tier exits 3, and empty
  policy denies every real provider (silence is not a policy).
  `never_send` globs are withheld from the worktree for the attempt
  (a worktree's `.git` is a host-side pointer, so removal is removal
  from the sandbox's world) and restored from memory afterwards.
- Attempt telemetry grew the fields nothing reconstructs later (RFC
  0004 §6): an `agent` block (tier, adapter-that-ran, provider, model,
  `model_version` — None marks an uncontrolled regime per D-4.6,
  `cost_usd`, `trace_ref`), the tier mapping and provider policy
  joined `config_hash` (D-4.3), and `torve feedback` appends the two
  hand-entered `ReviewFeedback` fields to their own stream.
- RFC 0008 executed, phases 1–2 (T-0049/T-0050): the tracker projection.
  A transactional outbox (the RFC 0003 §5 leg, built for its first
  consumer): effects derive from run state, stage idempotently by key,
  relay at-least-once with a ledger — a deliberate replay delivers
  nothing, a failed destination never dams the queue. On top of it, the
  GitHub Issues projection: one issue per task, states as labels,
  escalations with reason and detail, one comment per attempt composed
  from telemetry; a refused or unsupported reflection is a logged
  divergence, never an exception, and the board announces its own
  non-authority in every artefact. Inbound: `/torve retry|abandon|unblock`
  parsed allow-listed from comments, validated against the store,
  answered on their thread. `torve tracker sync|poll`. Live on the lab:
  four issues projected, the replay a no-op, a refusal answered in place.
- RFC 0006's CI leg executed (T-0048): `ci: green_on_current_head` is a
  live requirement. With `promotion.require_ci` on, the lane consults the
  configured remote's Actions verdict for the candidate's branch tip —
  polled with backoff, only the latest run per workflow counting, only
  success landing; a refusal names the verdict, rides the engine-event
  stream, and touches nothing. The rebase path now releases the
  candidate's engine worktree (git refuses a second checkout of a pinned
  branch). Demonstrated end to end on the lab: the lane refused a
  candidate on genuinely red CI, then landed it after the green rerun —
  rebased, battery green, pushed.
- Amendment A-24 executed (T-0046): docker inside the sandbox.
  `runtime.docker: socket` mounts the host daemon's socket into every
  sandbox of the run — attempt and gates alike, the socket's owning
  group added — and the image supplies the CLI; off by default,
  host-equivalent capability granted knowingly per repository.
  OpenSandbox refuses any mode. Containers a sandbox starts carry no
  torve labels; the reaper does not chase them. Exit criterion live: a
  task whose acceptance battery starts a real container replayed green
  in a socket sandbox on the lab repository.
- Amendment A-25 executed (T-0047): vendored skills. A repository
  commits skills under `.torve/skills-vendor/<name>/`; role sets resolve
  them beside package data — a name in both is refused in both
  directions, protecting the parsed-skill boundary — and the vendored
  tree's digest joins `config_hash`, so an edited vendored skill is a
  visible regime change. Torve vendors `reading-isnt-proof` from the
  team library as the first entry.
- The lab repository consumes the gates in CI: a committed torve wheel,
  pip-installed, running `torve gates run` on pushes and pull requests —
  RFC 0002's second consuming repository.
- RFC 0010 executed, phases 1–2 (T-0044/T-0045): VCS, provenance and
  revert. The commit is the runner's artefact: author is the agent
  identity (`adapter/model@version <agents@torve.local>`), committer is
  Torve, trailers complete (Task/Attempt/Agent/Config/Decisions with
  grades) so `git log` reconstructs a task with the store offline;
  SSH signing at the runner boundary when `vcs.signing_key` is set — the
  key never enters a sandbox, and the signature attests provenance,
  never approval. Revert is a role: mechanical `git revert --no-commit`
  of the target's landed shas (resolved from the Torve-Task trailer),
  no agent and no attempt sandbox, same gates, same landing; a
  dependent-commit conflict aborts clean and escalates as
  `merge_conflict`; the runner writes the resolved log entries. The
  forge leg is live: push targets only the task's branch with the token
  resolved by NAME (`scm.token_env`) at the runner boundary — never on
  argv, never in a sandbox — and the pull request is composed from
  records only (contract, gates with durations, decisions with grades,
  log divergences, cost, trace). Exit criterion demonstrated: the
  engine pushed and opened torve-remote-lab#1.
- Amendment A-26 executed (T-0043): a conflicted landing escalates the
  run. `ready → escalated` joins the charter's transition table, taken
  only by the merge lane on a conflicted rebase — reason
  `merge_conflict`, in the vocabulary since v1 and reachable for the
  first time. The branch stays exactly as measured, the escalated
  candidate leaves the lane, and the escalation queue's age starts
  counting the moment a landing fails; `ready` stays terminal to the
  engine everywhere else (swept, kill-refused, never re-dispatched).
- RFC 0006 executed, phases 1–2 (T-0041/T-0042): the merge train.
  Dispatch refuses a task whose scope intersects an active run's — the
  cause in the message, counted per contended path; `torve kill`
  force-terminates one run; engine health rides the telemetry stream as
  `kind: engine` records; escalations carry age and attention route,
  and the context names how long the oldest has waited — the queue's
  age, not its length, is the signal. `torve merge` lands ready
  candidates one at a time: an unmoved base fast-forwards exactly as
  measured, a moved base rebases in a disposable worktree and re-runs
  the gates first, a conflict aborts untouched and exits 2 — the lane
  never resolves one. The operator's invocation is the recorded
  approval; `promotion.auto_merge` exists, off by default, for the
  scheduler that does not exist yet.
- RFC 0005 executed, phases 1–3 (T-0038..T-0040, the first three-phase
  planner mint): review is a run. `Finding` joins the domain and the
  review role becomes real in the contract (targets, no acceptance, the
  gate skipped for the role); the evidence locator is one mechanism
  with two consumers, discarding findings nothing can resolve; the
  runner mints and drives the review when its target's gates go green —
  input composed from diff/contract/decisions/gate results, never the
  author's trace, workspace mounted read-only (physically refused,
  conformance-tested), a surviving blocker escalating the target as
  blocker_finding. `torve review corpus` replays seeded-defect cases
  through the reviewer tier and exits red on a dropped catch or an
  invented blocker on a clean case — measured green live with a
  deepseek reviewer, which also caught the harness-chatter parsing
  defect before it ever gated a prompt change. Off by default;
  `review.on: [task_gated]` enables it. Deferred with the forge: PR
  triggers, comment posting, the replacement sequence.
- RFC 0017 executed (T-0036 mechanism, T-0037 definitions): the sandbox
  image is an input — the runtime resolves the configured image to its
  content digest at dispatch, the digest joins `config_hash` and rides
  attempt and shadow records, and tiers may name their own image
  (harness identity) over the runtime default. `torve sandbox build`
  builds reviewed definitions under `.torve/sandbox/<name>/` and
  reports digests; `torve doctor` reds on a configured image the
  runtime cannot resolve or a torve-agent image with no definition.
  The four-harness roster is committed as definitions, and the drift
  the hash catches was demonstrated live: one tag, a deliberate
  rebuild, two digests, two regimes.
- Spacing pass (T-0035): footer notes render at column 0 — the dim
  style is the separation — and the graph gains blank lines after its
  header and before the omitted-documents note.
- Finished business collapses to a count (T-0034): `rfc graph` nodes
  carry their implementation state and documents both accepted and
  complete are omitted from the tree — their dependents attach where
  they stood, a dim line names the omitted; the context programme table
  hides accepted-and-complete rows that carry no note behind the same
  kind of count; and the footer component ends with a blank line so a
  note never runs into the next section title. JSON unchanged.
- `torve rfc graph` renders the corpus as a dependency tree (T-0032):
  dependents nested under what they build on, roots first, statuses
  styled with the word present; a multi-parent document expands under
  its first parent and back-references dim elsewhere, and standalone
  documents appear as bare roots — the per-edge table never showed
  them. The JSON edge list is unchanged.
- The `user-facing-text` gate (0011 A-23, T-0031): user-facing strings —
  help text, command docstrings, error messages, gate output — carry no
  corpus identifiers, because whoever runs the command has no corpus and
  the reference rots invisibly on every amendment. An AST pass over
  changed files in the cli and gates packages; module docstrings,
  comments, class docstrings and private-function docstrings are exempt
  (references belong there — the sabotage suite's must-pass case pins
  it), the sabotage fixture module is data by nature, and public
  standards like RFC 3339 are not corpus identifiers. The one-time
  rewrite moved ~45 citations up into module docstrings; entered at
  shadow, report read clean, promoted to blocking (the deterministic-
  check precedent).
- Owner-review round three (T-0030): `torve reap` sweeps a terminal
  run's whole `.wt/` footprint — state file and trace logs with the
  worktree, driven by the state files so a footprint whose worktree is
  already gone is still collected (§4.2: stale resources accumulate —
  always) — reported as `run states removed`, while escalated runs keep
  everything for triage; and `torve rfc graph` renders through the
  component vocabulary (header, edge table with statuses styled by
  colour that supplements the word) instead of bare f-strings.
- Owner-review round two (T-0029): tasks with no run state whose id
  the history mentions derive as `shipped` — evidence of record, never
  engine execution — leaving exactly one honest `unstarted` (the task
  open at measurement time) and dissolving four false asserted-complete
  flags; the proposals table gained row separators and a bounded prose
  column; and `run_gates` grew an optional progress callback so the
  live status names the gate it is inside.
- RFC 0018 phase 2 (T-0028, the second planner-minted contract) plus
  owner-reported phase-1 fixes: every verb renders through the shared
  components (plan and reconcile as tables, doctor speaks verdict
  marks, rfc check/graph style PROBLEM/WARN with the words intact),
  table footnotes print after the table instead of inflating its first
  column, id lists are bounded, and the single-line TTY-only live
  status (spinner + elapsed, stderr, absent under --plain/CI/json)
  covers the two longest hand-run waits: the gate pass and the shadow
  replay. 0018 is implementation: complete.
- RFC 0018 phase 1 executed from the first planner-minted contract
  (`torve plan 0018 --no-dry-run` minted T-0027/T-0028; T-0027 executed
  against its minted scope and decisions): the component vocabulary in
  `torve.cli.console`, `gates run`/`status`/`context` rendered through
  it, and `torve context --format markdown` carrying the pasteable
  document while the default became tables and sections. JSON
  byte-shapes unchanged.
- RFC 0018 drafted (CLI presentation): the D-11.8 deferral discharged
  now that hand-run commands are known — one component vocabulary
  (header, table, verdict marks, failure detail, closing line), fixed
  colour semantics where colour is never the only carrier, `--format
  markdown` on document-producing commands, a single-line TTY-only
  live status for long waits, and the rule that tests pin JSON
  byte-shapes and assert human output by content, never layout. Carries
  the corpus's first mintable Phasing section — written to be the first
  document planned by `torve plan`.
- `torve context` — the loop's read leg (RFC 0007 §4, phase 2): one
  report projecting tasks by state, escalations by reason, execution-log
  proposals ready to become decision rows (with corpus-citation
  cross-referencing separating fresh from likely-landed), per-gate
  health, cost against `config_hash`, the programme view of the RFC
  graph (D-7.11 — status, phases, what became plannable), and asserted
  `implementation` beside derived per-phase progress with disagreements
  flagged (D-7.15). Markdown for a planning session, JSON for machines,
  one report (D-7.4). Progress computed on demand, stored nowhere.
- `stale_inheritance` joins the escalation vocabulary (charter A-22,
  amending D-29): a non-terminal task minted from a document that later
  became superseded carries decisions that no longer stand — not a
  locked conflict, not underspecification, its own population.
  `torve plan --reconcile` sweeps the corpus and escalates each such
  task (dry-run default; a never-run task gains a state through the
  claimed→escalated edge); nothing is deleted or rewritten — what to do
  with in-flight work is a human decision (D-7.10).
- `torve plan` — the deterministic minter (RFC 0007 §3, phase 1): one
  accepted, committed specification in, implement-task contracts out,
  no model call at any point (D-7.1). Admission refuses drafts,
  unsettled dependencies, supersession, cycles and uncommitted text by
  name with exit 3; exactly one document per invocation (D-7.8). The
  Phasing section became a mintable format — a fenced YAML block of
  units (phase, title, intent, scope, acceptance, depends_on), owned
  by `rfc_parse` (D-7.12), validated by `torve rfc check`, documented
  in the rfc-writer template. Minted contracts inherit the document's
  decision table grade-and-paths at write time, same-phase scopes must
  not intersect, ids derive max+1 and are never reused, dry-run is the
  default. The `DecisionSource` port landed with its first adapter
  (`RfcDirectory` — standing rows for an area, accepted documents
  only). With `planner.py` real, the corpus's last standing warning
  cleared: 17 RFCs, zero problems, zero warnings.
- RFC 0017 drafted (sandbox provisioning and harness configuration),
  from the first shadow campaign's findings: the image digest as the
  sandbox's identity joining `config_hash` (a mutable tag is the D-4.6
  failure one artefact over), image definitions as reviewed artefacts
  under `.torve/sandbox/` built by `torve sandbox build`, five
  configuration channels routed by nature (identity/task/secret/knob/
  state), stdio MCP as image content vs remote MCP under provider
  routing, and per-slot never-shared memory that shadow runs never
  mount.
- First live shadow replays (claude CLI in the sandbox, host-proxy
  egress): both green in one attempt with real cost figures. Metadata
  parsing learned the claude CLI's actual shape — models arrive as
  `modelUsage` keys, the dated snapshot ids D-4.6 exists to record —
  and the proxy convention became one vocabulary (`PROXY_ENV` in
  `application.ports`) forwarded by Docker under the network opt-in and
  by the OpenSandbox adapter at its API boundary.
- Sandbox egress through the host: `runtime.network: host` puts Docker
  sandboxes on the host's network stack and forwards the standard proxy
  environment by name — for hosts whose provider access runs through a
  local proxy/VPN, where the default bridge is silently a different
  (often blocked) path. Forwarding rides the opt-in only; OpenSandbox
  keeps its own egress model.
- Shadow runs (RFC 0004 phase 2, §5): `torve shadow <task-id>` replays a
  completed task from the parent of the commit that shipped it — found by
  its `Torve-Task:` trailer — through the same attempt-and-gates loop a
  live run executes, never merging (the landing hook records prose; no
  vcs call exists on the shadow path, D-4.4). The workspace is a
  truncated clone built by exact-SHA fetch at bounded depth: no refs or
  objects beyond the parent exist in it, so the agent cannot read the
  answer out of the repository's future (D-4.7, §6a). One `kind: shadow`
  telemetry record captures cost, iterations, outcome and the diffstat
  comparison against what shipped; gate passes inside a replay are
  marked `agent.shadow` so the measurement population stays separable in
  the shared stream. The exit criteria's fifteen measured runs are now
  one command per task plus live keys.
- RFC validation moved into the package (0007 §3a, charter A-15, 0013 A-16):
  `torve rfc check | index | new | graph` with vocabularies defined once in
  `domain/rfc.py` (D-7.13) and the format owned by `config/rfc_parse.py`
  (D-7.12) — the skill's `rfc_index.py` script is deleted and the skill
  teaches content only. New checks: corpus-directory contents with routing
  messages (D-A.18), `depends_on` cycles, non-accepted inheritance
  surfaced as a warning (D-A.10), line-number citations of real paths, and
  the `kind` vocabulary. Numbering is derived as max+1 with no counter
  file and no way to fill a hole (D-A.17, D-A.19); `rfcs.path` in
  `config.yaml` names the one corpus location (D-13.7). The `rfc-index`
  gate is replaced by the shipped `rfc-valid` product gate (D-7.14,
  shadow, origin rfc/0007); a malformed corpus exits 3 per 0007 §3a.
- `underspecified` joins the escalation vocabulary (charter A-21,
  amending D-29): a contract needing three or more load-bearing
  decisions invented halts as a specification defect — separable in
  telemetry from `locked_conflict` (one indicts the contract, the other
  the code), projected to exit 2, with the triage response being an
  amendment and a re-mint, never a retry. The threshold stays in the
  skill: the engine cannot count what a contract fails to say.
- RFC 0007 accepted (`implementation: partial` — §3a shipped as
  `torve rfc`); the D-32 glob check became a gradient: `none` skips,
  `complete` reddens on rot, `partial` gets one aggregate warning per
  document naming its unbuilt intended modules.
- Proposal sweep T-0009..T-0020: twenty-two execution-log proposals
  promoted into decision tables across eight documents (D-2.24–26,
  D-3.22, D-9.9–10, D-11.9–10, D-13.8–10, D-14.13, D-15.11–12,
  D-16.2–7, D-1.8, D-7.19–20), each row citing its task log. The
  inheritance-from-non-accepted check hardened from warning to problem
  — its own promotion condition (0009/0004 resolved) was met at 0004's
  acceptance.
- Identifier retirement made structural (0016 A-20, D-16.1): a retired
  decision's row is removed and its id recorded in new `retired:`
  frontmatter beside the prose tombstone. Retired ids resolve in
  citations and can never be redefined anywhere in the corpus — which
  let citation resolution harden from warning to problem. First fully
  clean `torve rfc check` run: zero problems, zero warnings.
- Format containment made checkable (0015 A-19, enforcing 0007's
  D-7.18): a fifth import-linter contract forbids gates and the runtime
  and agent adapters from importing `config.rfc_parse`, so the RFC
  format keeps terminating at the planner by check rather than by
  nobody making a mistake. Sabotage twins prove it reddens on a gate
  importing the parser and tolerates the CLI doing so legally.
- Plan-gate deadlock removed from the shipped `flag-dont-flip` (0003
  A-18): the skill's "plan and stop" checkpoint assumed a human at the
  other end — in a sandbox it produces no diff and dies on wall-clock.
  Underspecification is now a halt (`kind: blocked`, three or more
  load-bearing unsettled decisions) and fewer are `UNLISTED` decisions
  with owed proposals; the readiness threshold survives, the waiting
  goes. The rejected-alternatives reading instruction is deleted and
  0003 §5a gains the exclusion: the source specification document never
  enters the sandbox — `rfc` on a contract is provenance, verified by an
  integration test whose contract cites a document that exists nowhere.
- Charter decomposition (A-17): the corpus conventions extracted to
  RFC 0016 — nineteen `D-A.*` decisions and amendments A-7/A-9/A-10/
  A-14/A-15 relocated with identifiers preserved, plus the prose those
  rules never had; the charter keeps only engine decisions, and `D-A.*`
  is closed to new members. 0007, 0011, 0013 and 0015 gain
  `depends_on: ["0016"]`. `torve rfc check` gains duplicate-heading
  detection and corpus-wide decision-citation resolution (a warning,
  pending the retired-identifier convention), both fence-aware.
- RFC 0015 (source tree structure) adopted and executed: `src/torve` is
  layered — `base/`, `domain/` (task, attempt, states aggregates),
  `application/` (ports, run loop, services), `adapters/<port>/<technology>`,
  `gates/` (standalone, importing only domain/base/config), `config/`,
  `cli/` (one module per verb group) — with every move a `git mv`. The
  layer rules are import-linter contracts in pyproject wrapped by the new
  `layering` gate (worktree input, origin rfc/0015); three dependency
  inversions made them hold: the run loop and reaper receive a store
  factory instead of importing the adapter, forze context wiring moved into
  the TaskStore facade, and `config_hash` moved to `application/telemetry`.
  `torve/__init__.py` is a curated lazy front door (PEP 562) — `import
  torve` no longer touches the runner, adapters or CLI. `gates/base.py` is
  `gates/contract.py`; the D-15.5 module-naming rule (`models`/`utils`/
  `helpers`/`common`/`base` forbidden) joins the `source-layout` gate with
  a sabotage case; layering's own red/green cases live in the repository
  test suite because it is a self-development gate and `import-linter`
  stays a dev dependency (D-15.10).
- RFC 0011 (CLI contract) executed: the CLI is Typer plus Rich (D-11.1,
  closing the Click departure logged at RFC promotion); every
  result-producing command takes `--format json` and emits the persisted
  record shape — `run`/`status` emit the RunState record, `gates run` the
  telemetry record (D-11.2/D-11.3); exit codes 0–5 are the escalation
  vocabulary projected in `torve.domain` with a completeness test (D-11.4,
  `doctor` failures now exit 3, `torve run` distinguishes escalated/2,
  infra/4 and exhausted/5); `--plain` global flag implied by `CI`, non-TTY
  or json, `NO_COLOR` honoured (D-11.5); results on stdout, diagnostics on
  stderr (D-11.6); `torve reap --dry-run` reports without touching anything.
  Presentation polish stays deferred (D-11.8).
- RFC 0013 (configuration layout) executed: every Torve file lives under
  `.torve/` — `gates.yaml`, `config.yaml` (renamed from root `torve.yaml`),
  `tasks/`, `logs/` — with the legacy root-level names resolving as a
  fallback (`src/torve/layout.py`, D-13.1); overrides are the explicit
  `--gates` and `--config` flags, never a second file merged over the first
  (D-13.4); a malformed manifest or runner configuration exits 3 per the
  CLI contract's configuration-error code (D-13.6). This repository took
  the one-move migration, and the sabotage rig seeds the canonical layout.
- RFC 0014 (source file layout, `kind: convention`) adopted: two 27-character
  separators (structural dash, rhythmic dot) extracted from forze; the
  checkable half ships as the `@source-layout` builtin over the diff
  (separator form, post-import dash, dash ceiling, dash labels, label-free
  dots) with five red sabotage cases and a green twin; the whole `src` tree
  swept once (post-import dashes in 33 modules, banners normalized); ruff
  selection matched to forze (adds RUF/ASYNC/C4/ISC/PIE/T20, RUF001-003
  ignored for typographic docstrings); the gate enters `gates.yaml` at
  `shadow` with `origin: rfc/0014`, and the T-0010 corpus-validator gate is
  renamed `rfc-index`.
- Gate lifecycle (amendment A-8 to RFC 0002, D-2.18–D-2.23): §7 added with
  the state machine `proposed → shadow → blocking → quarantined → retired`,
  the five filters, the implementation/activation split, health metrics and
  retirement signals. The `Gate` model's `blocking: bool` is replaced by
  required `state` plus required `origin` (and optional `added`) while no
  manifest exists outside this repository; shadow and quarantined failures
  are recorded but never touch the exit code; bypasses apply only to
  blocking-state gates. The starting set is backfilled (`origin:
  structural`, `self-audit` at `shadow`), and `source-layout` — the corpus
  validator as a gate — enters at `shadow` as the lifecycle's first test.
- Document conventions (amendment A-7, charter D-A.1–D-A.8) applied to the
  repository: MIGRATIONS/CLI-contract/configuration-layout promoted to RFCs
  0011–0013 (identifiers renumbered to `D-11.*`/`D-12.*`/`D-13.*`), the
  skill-specialisation guide moved to `ops/`, `pages/` created for derived
  documentation, amendments dispersed into their primary targets'
  `## Amendments` sections (AMENDMENTS.md deleted), YAML frontmatter on all
  thirteen RFCs, `INDEX.md` generated by `rfc_index.py` and CI-checked,
  the validator hardened per the conventions' checklist (exact table
  header, corpus-unique identifiers, LOCKED globs must match files in
  accepted RFCs, dependency and amendment cross-checks), and task logs
  pinned with `repo`/`base_sha` from T-0009 on.
- Pyright strict as a second blocking checker: `[tool.pyright]` in
  pyproject makes the editor's strict mode repo-canonical, the 91
  Unknown-propagation findings over `src` are fixed (typed yaml/SDK
  boundaries via `cast`, annotated container inits, named providers for
  the store deps, `import_module` for the optional yoyo/opensandbox
  imports), and `uv run basedpyright src` joins CI and the acceptance
  fallback at 0 errors.
- Strict typing as a house gate: `mypy --strict` over `src` (0 errors),
  `py.typed` in the wheel, typed against forze's `DurableRunStorePort` /
  `DurableFunctionHandler` contracts; pytest configuration hardened
  (`--strict-markers --strict-config`, `pytest-timeout` safety net,
  src+tests pythonpath). `uv run mypy src` joins CI and the acceptance
  fallback in `gates.yaml`.

### Changed

- `requires-python` is now `>=3.13,<3.15` (the forze substrate's floor).
- `config_hash` now includes the Torve package version and the pinned forze
  version (D-9.8, from `migrations/substrate/FORZE_VERSION`) — both upgrades
  are regime changes telemetry must see.
