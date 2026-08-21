"""The DST world (RFC 0003 §6 layer 3, D-3.5): the real `drive_attempts` loop
and the real TaskStore over the mock durable store, driven concurrently by
simulated operations. The hooks are simulated (instant agents, scripted
gates); the loop, the transitions, the store and the fences are the shipped
code.

Twins — each a deliberately broken variant the oracle must catch:
    no_ceiling   the poison ceiling check is effectively removed
    lie_gates    the gate hook reports green over a red truth
    dup_land     landing is not idempotent under at-least-once delivery
    rogue        dispatch bypasses the store's idempotent claim convergence
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from forze.application.contracts.durable.function import DurableRunStatus
from forze.application.execution.operations.registry import OperationRegistry
from forze_dst import OperationCase
from forze_dst.markers import reached, record_event

from torve.adapters.durable_store import open_mock_store
from torve.domain import TaskState
from torve.models import Budget, Task
from torve.ports import AgentResult
from torve.run import AttemptHooks, drive_attempts
from torve.runconfig import RunnerConfig, StoreConfig
from torve.runstate import RunState
from torve.taskstore import TaskStore

CEILING = 2
TASK_POOL = ("T-A", "T-B")


class World:
    def __init__(self, tmp_root: Path, twin: str | None = None) -> None:
        self.tmp_root = tmp_root
        self.twin = twin
        self.reach: set[str] = set()  # accumulated across the whole sweep
        self.reset()

    def reset(self) -> None:
        self.taskstore = TaskStore(
            open_mock_store(),
            StoreConfig(lease_for=0.06, heartbeat_divisor=3, max_run_duration=30),
        )
        self.taskstore.register(self.body)
        self.holding: dict[str, str] = {}
        self.landed: set[str] = set()
        self.generation: dict[str, int] = dict.fromkeys(TASK_POOL, 0)
        self.run_dir = self.tmp_root / uuid.uuid4().hex
        self.run_dir.mkdir(parents=True)

    def _reached(self, label: str) -> None:
        self.reach.add(label)
        reached(label)

    # ------------------------------------------------------------------ #
    # the durable body: the real loop over simulated hooks
    # ------------------------------------------------------------------ #

    async def body(self, _ctx, input_json):
        task_id = input_json["task"]
        engine_run = input_json["engine_run_id"]
        plan = input_json["plan"]
        run_key = f"{task_id}:{engine_run}"

        task = Task(id=task_id, decisions=[], budget=Budget())
        ceiling = 99 if self.twin == "no_ceiling" else CEILING
        config = RunnerConfig(poison_ceiling=ceiling)
        state = RunState(task_id=task_id,
                         path=self.run_dir / f"{run_key.replace(':', '-')}.state.json")
        state.transition(TaskState.CLAIMED, "sim claim")  # what run_task does before the loop
        saw_red_gates = False

        tracked = input_json.get("tracked", True)

        async def attempt(current: RunState) -> AgentResult:
            # The hold registry asserts mutual exclusion of store-dispatched
            # runs; a run that deliberately bypassed the claim (at-least-once
            # redelivery) is outside that claim by construction.
            if tracked:
                if task_id in self.holding and self.holding[task_id] != engine_run:
                    record_event("double_hold", task=task_id)
                self.holding[task_id] = engine_run
            record_event("attempt", task=task_id, run=run_key, n=current.attempts)
            # Interleave point; slow plans keep the body alive across a lease
            # renewal, which is the only window a cancel can be observed in.
            await asyncio.sleep(plan.get("sleep", 0))
            if tracked and self.holding.get(task_id) == engine_run:
                del self.holding[task_id]
            exit_code = 137 if current.attempts <= plan["agent_fails"] else 0
            return AgentResult(exit_code=exit_code, output="")

        async def gates(current: RunState) -> tuple[int, str, str]:
            nonlocal saw_red_gates
            await asyncio.sleep(0)
            green_attempt = plan["agent_fails"] + plan["gate_fails"] + 1
            red = current.attempts < green_attempt
            if red:
                saw_red_gates = True
            elif saw_red_gates:
                self._reached("gate_red_then_green")
            if self.twin == "lie_gates":
                return 0, "lied", "cafe"  # green over a red truth
            return (1 if red else 0), "sim", "cafe"

        async def land(current: RunState, _digest: str) -> str:
            truth_green = current.attempts >= plan["agent_fails"] + plan["gate_fails"] + 1
            record_event("ready", task=task_id, run=run_key,
                         gates="green" if truth_green else "red")
            if self.twin == "dup_land" or run_key not in self.landed:
                self.landed.add(run_key)
                record_event("landed", run=run_key)
            return "sim landed"

        hooks = AttemptHooks(attempt=attempt, halted=lambda: False, gates=gates, land=land)
        try:
            final = await drive_attempts(state, task, config, hooks)
        except asyncio.CancelledError:
            self._reached("cancel_observed")
            raise
        return {"state": str(final.state), "attempts": final.attempts}

    # ------------------------------------------------------------------ #
    # operations: what the simulation interleaves
    # ------------------------------------------------------------------ #

    def _plan(self, rng) -> dict:
        return {
            "agent_fails": rng.choice([0, 0, 1]),
            "gate_fails": rng.choice([0, 1, 1, 2]),
            "sleep": rng.choice([0.0, 0.0, 0.1]),
        }

    async def op_run(self, args) -> None:
        task_id = args["task"]
        generation = self.generation[task_id]
        engine_run = f"e{uuid.uuid4().hex[:8]}"
        key = args["key"] or f"{task_id}:g{generation}"  # idempotent claim convergence
        record = await self.taskstore.run_now(
            {"task": task_id, "engine_run_id": engine_run, "plan": args["plan"]},
            idempotency_key=key,
        )
        if record.status is not DurableRunStatus.PENDING and \
                record.status is not DurableRunStatus.RUNNING:
            self.generation[task_id] = generation + 1  # terminal: next dispatch is a new claim

    async def op_zombie(self, args) -> None:
        from datetime import timedelta

        # Zombies live on their own task id: recovery re-running an abandoned
        # task while a fresh dispatch of the same task runs is a dispatch-layer
        # concern (RFC 0006), not this simulation's mutual-exclusion claim.
        record = await self.taskstore.enqueue({
            "task": "T-Z", "engine_run_id": f"z{uuid.uuid4().hex[:8]}",
            "plan": args["plan"],
        })
        claimed = await self.taskstore.store.begin(
            record.run_id, lease_for=timedelta(milliseconds=20)
        )
        if claimed is not None:
            self._reached("zombie_abandoned")  # the worker dies here, no terminal write

    async def op_recover(self, _args) -> None:
        await asyncio.sleep(0.06)  # let abandoned leases expire
        recovered = await self.taskstore.recover(limit=4)
        if recovered:
            self._reached("lease_reclaimed")

    async def op_cancel(self, _args) -> None:
        live = await self.taskstore.live_records()
        if live:
            await self.taskstore.request_cancel(live[0].run_id)

    async def op_double_deliver(self, args) -> None:
        """At-least-once delivery: the same body invoked twice for one run.
        Its own task id — the property under test is landing idempotence;
        mutual exclusion against store-claimed runs is the store's property,
        which direct invocation deliberately bypasses."""
        payload = {"task": "T-D", "engine_run_id": f"d{uuid.uuid4().hex[:8]}",
                   "plan": args["plan"], "tracked": False}
        await self.body(None, payload)
        await self.body(None, dict(payload))

    # ------------------------------------------------------------------ #

    def registry(self):
        def wrap(fn):
            return lambda _ctx: fn

        return OperationRegistry(handlers={
            "run": wrap(self.op_run),
            "zombie": wrap(self.op_zombie),
            "recover": wrap(self.op_recover),
            "cancel": wrap(self.op_cancel),
            "double_deliver": wrap(self.op_double_deliver),
        }).freeze()

    def cases(self) -> list[OperationCase]:
        rogue = self.twin == "rogue"

        def run_inputs(rng):
            return {"task": rng.choice(TASK_POOL), "plan": self._plan(rng),
                    "key": f"rogue:{rng.random()}" if rogue else None}

        def task_inputs(rng):
            return {"task": rng.choice(TASK_POOL), "plan": self._plan(rng)}

        return [
            OperationCase(op="run", weight=5.0, inputs=run_inputs),
            OperationCase(op="zombie", weight=1.0, inputs=task_inputs),
            OperationCase(op="recover", weight=2.0, inputs=lambda _rng: {}),
            OperationCase(op="cancel", weight=2.0, inputs=lambda _rng: {}),
            OperationCase(op="double_deliver", weight=1.0, inputs=task_inputs),
        ]
