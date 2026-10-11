# Resolving an escalation

An escalation is the engine handing a task to a person. Start every one with
`torve why <task>` and the attempt's own account: its divergence entries and
the gate output. Then sort the cause into one of three kinds:

- the **contract** was wrong (scope, acceptance, a missing decision);
- the **infrastructure** failed (image, network, store, forge, a killed process);
- the **agent** failed at work it could have done.

Only the third is the agent's. Most escalations are the first two.

## Closing one

`torve manager resolve <partition> <task> --resolution <requeued|abandoned|landed>`
takes `--note` (why, for whoever reads it later) and `--sha` (for `landed`: the
commit on the document branch).

To requeue, always reap first:

```bash
torve reap --escalated
torve manager resolve <partition> <task> --resolution requeued --note "<what changed>"
```

A requeue on top of a leftover escalated worktree fails as
`gate_infrastructure_failure` the moment a worker claims it.

## By reason

**`underspecified`, or a halt on scope.** The agent found that the change
needs a file the phase does not own, and its divergence entry names the file.
Check the claim. If it holds, widen the phase's scope in the document and
refresh the minted contract with `torve plan S-NNNN --refresh` (or edit the
contract to match), then reap and requeue. If a test or document already
proves the scope right, requeue with a note that says so.

**`locked_conflict`.** The work contradicts a `LOCKED` row. That is the
owner's decision: amend the row (`torve spec amend`) or change the phase.
Never requeue with a note telling the agent to ignore the row. Before
accepting an amendment, grep the corpus for every other `LOCKED` row that
states the same rule.

**`poison_ceiling`.** Every allowed attempt went red. Read the gate output of
each attempt:

- red on something outside the task's control, such as stale projections, a
  flaky test or an image problem: fix that, then reap and requeue;
- red on scope alone with the work itself right: finish it by hand
  (`torve guide torve by-hand`);
- three attempts that each produced nothing: the contract is probably
  unclear, so rewrite its intent or acceptance before you requeue.

**`blocker_finding`.** The reviewer blocked the candidate. Check the finding:

- real and inside the scope: requeue with a note;
- real but outside the scope: widen the scope, or fix it by hand on the
  document branch;
- wrong: resolve with a note that shows why.

**`lease_expired`, or a killed worker.** If the task's landing file is on its
document branch, resolve it `landed` with that commit. Otherwise requeue it.

**`gate_infrastructure_failure`, `seat_refused`.** Infrastructure. Run
`torve doctor`, fix what it names, then reap and requeue.

**`merge_conflict`.** The lane could not rebase the candidate. Resolve the
conflict by hand on the document branch, or requeue after the conflicting
work lands.

## After

When the engine itself was at fault, file it so the fault is counted rather
than lost: `torve source new operator <slug> --title "<what happened>"`.
