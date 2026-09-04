# The fault line — what does not survive distribution

The engine is correct today because one operator runs one node. That is not
a criticism: the local regime was the fastest path to a working loop, and
most of the architecture (layers, gates, contracts, the git/store boundary)
distributes untouched. But a specific set of assumptions is single-node, and
they are worth naming while the manager/connector direction is being
decided.

## Inventory of single-node assumptions

| # | Assumption | Where | Breaks when |
| --- | --- | --- | --- |
| 1 | Worktrees on the local filesystem are the work surface | runner, workspace adapter | executors run on remote sandboxes without a shared FS |
| 2 | Mock store is JSONL appended by whoever writes | telemetry, run states | two writers tear the file — already the reason queues are serial |
| 3 | Landings serialize through one `main` on one clone | merge lane, operator chain | any second lander; throughput wall named in the manager analysis |
| 4 | Outbox root is a directory; relay runs inside the tick | tracker, outbox | tracker runs anywhere but the repo host — see [tracker outbox](tracker-outbox.md) |
| 5 | Credential broker binds loopback routes into local sandboxes | broker adapter | sandboxes are remote (RFC 0041 added bind/advertise for exactly this — and its first executor got the precedence wrong, blocker T-0276) |
| 6 | Host proxy and `.env` passthrough shape egress | runconfig, docker adapter | a fleet node has different egress; one test already fails on any host with `http_proxy` set |
| 7 | Docker socket is the runtime | runtime adapter | opensandbox adapter exists; parity is tested, but the *scheduler* is still "whoever invoked `torve run`" |
| 8 | Attempt budgets reset per dispatch | runner | re-dispatch across nodes multiplies the reset (0026 §5.5 gap) |

## What already distributes

RFC 0041 moved the sandbox side out: image push, remote endpoints,
conformance and transfer telemetry. The store has a Postgres adapter with
leases and fences (the forze substrate) — the *data* layer is distribution-
ready; the *actor* layer is not.

## The three-regime picture

| | Local (today) | Durable store | Distributed runner |
| --- | --- | --- | --- |
| Store | mock JSONL | Postgres | Postgres |
| Sandboxes | local Docker | local + remote (0041) | remote fleet |
| Tick | operator/cron, one node | same | resident manager (forze durable runner) |
| Tracker relay | in-tick, sync | in-tick, sync | relay worker over store outbox |
| Landings | one operator chain | one lane, ancestry-verified | the open problem — landing throughput is the wall |
| D-19.1 / D-42.6 | intact | intact | **must be reopened** |

The middle column is reachable without touching any charter: switch the
dogfood store to Postgres, keep the tick as-is. It removes assumption 2 and
hardens 8, and it is the honest prerequisite for judging the third column
with data instead of doctrine.

## The decision queue this implies

1. **Reopen or reaffirm D-19.1** (tick-not-daemon) explicitly, as an RFC —
   not implicitly through an outbox migration. Everything in column three
   hangs off it; deciding it by side effect would be the drift the corpus
   exists to prevent.
2. **If reopened:** the store-document regime from the
   [tracker page](tracker-outbox.md) becomes the natural shape, forze's
   outbox included, with the failure-policy delta (parked `failed` rows)
   decided consciously.
3. **If reaffirmed:** the tracker stays engine-local by charter; distribute
   executors and store, and say out loud that the board projection is
   pinned to the repo host.
4. **Independent of 1–3:** ancestry-verified `ready` (assumption 3's
   engine half) and cumulative budgets (assumption 8) are defects today,
   regime-agnostic, and cheap relative to everything above.

## Verdict offered for review

The emerged architecture is **internally consistent and correct for its
declared regime** — nothing found in this review is a bug in the design's
own terms. The two real findings are: the residency doctrine (D-19.1/D-42.6)
is doing more load-bearing work than its RFC prose acknowledges — it silently
decided the outbox question, the fleet question, and the tracker's
distributability; and the D-42.5 verdict should be read as *conditional*,
not absolute. Whether that doctrine is charter or scaffolding is the one
question only the owner can answer.
