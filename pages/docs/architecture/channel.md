# The live channel

What the engine can see while a run is in progress, and what it deliberately
cannot
([RFC 0045](https://github.com/morzecrew/torve/blob/main/rfcs/0045-the-live-channel.md)).

Before this existed, a running attempt was opaque: the engine started a
container, waited, and learned everything at once when the process ended.
An agent stuck in a loop and an agent thinking hard looked identical, and a
divergence the agent had already decided on sat in a file nobody would read
until the gates ran.

## Liveness is the burn stream

The broker already sees every provider response, because it injects the key
and meters the answer. It now emits one `seat.consumed` event per metered
response — the provider's own token counts and its reported cost where there
is one.

That makes liveness a **rate** rather than a total, and derived rather than
reported: an attempt with no recent burn is not working, whatever it would
say about itself, and it is never asked. A wedged agent cannot report itself
healthy because nothing invites it to.

```console
$ torve manager board morzecrew/torve --dsn "$TORVE_PG_DSN"
 task     state     attempts   held by   burn              landing
 T-0279   running   2          w-1       3m ago $0.00
```

The aggregate the broker reports at close equals what the events sum to, so
the two views of one run's spending cannot disagree. Whether a stalled
stream should also *end* an attempt is deliberately unanswered (D-45.8) —
the board surfaces it, nothing acts on it, and the decision waits for
recorded burn to argue from rather than for an argument.

## The sandbox writes through the broker, never to the store

A sandbox holds no store credential and never will (D-45.1). With one, the
authority table would become advice: anything holding the connection can
write anything.

Instead the broker serves an authenticated route the sandbox posts to. The
request carries content and nothing else:

- `actor.kind` is stamped `agent`, never taken from the request;
- `partition` and `subject` are the run's own, so a record can only ever be
  about the task the sandbox was dispatched for;
- the kind is checked against the authority table before anything is
  appended.

Forging is not refused — it is unexpressible, because the channel was built
for one run and there is no field in which to say otherwise (D-45.2).

The same route reads back. A sandbox posting through the channel never sees
its own entries in the worktree, since the engine writes that file at the
next gate pass, so reading them back is how an attempt checks its own
bookkeeping before it is judged on it.

!!! note "The sandbox cannot use git"

    A worktree's `.git` is a pointer into a host tree the container cannot
    follow. Three parts of the design fall out of that one fact: the
    divergence pin is dropped into the worktree at dispatch instead of being
    derived, the engine records and re-projects the log host-side, and
    `torve log owed` takes the changed files as arguments rather than
    computing a diff.

## Notes go the other way

An operator or the manager may write a note into a running task, and the
agent reads it when it polls:

```console
$ torve manager note morzecrew/torve T-0279 "the flake in test_x is known, do not chase it"
$ torve log notes            # inside the sandbox
```

It is a poll, never a push. Nothing interrupts an agent mid-thought, no
prompt is rewritten underneath it, and a note the agent never reads is still
a recorded fact about what the engine tried to say — which is the point of
it being an event rather than an edit.

## What the engine still cannot see

- **What the agent is doing right now.** The burn stream says it is
  spending; it does not say on what. Reading the model's stream and
  interpreting it would be the derive-don't-record antipattern with a
  parser attached.
- **Anything without a broker.** A run configured with `broker: none` has no
  burn stream and no channel; its intake writes the worktree file and the
  engine picks it up after the attempt. The channel is an addition, never a
  dependency.
- **Cost, on subscription seats.** A provider that bills a subscription
  reports no per-response cost, so the burn stream carries tokens and a zero
  cost honestly rather than an invented estimate.
