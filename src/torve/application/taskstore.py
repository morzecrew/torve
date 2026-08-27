"""TaskStore — the thin facade over the substrate's durable run store (D-5).

forze's `DurableFunctionRunner` owns the machinery D-5 warns against
hand-writing: lease heartbeat, disambiguating lease-lost from cancel from
timeout, fenced terminal writes (the claim's `attempts` counter), and
recovery that lands a pre-death cancel without invoking the body. Torve
registers one durable function — the attempt loop — and everything else here
is direct port calls the store already made safe:

- `expire_abandoned` is the reaper's verdict: `claim_abandoned` decides
  expiry (the store owns the lease clock), and the reclaimed record's own
  fence lands the `lease_expired` failure.
- `request_cancel` is fail-closed by backend capability declaration; a
  backend that cannot deliver a cancel raises, never returns False.
- `live_records` is the control-plane listing the sandbox sweep matches
  labels against.

The methods that remain bind the execution context and the function name;
a plain read goes through `.store` directly rather than being wrapped for
symmetry (A-49).
"""

from __future__ import annotations

from datetime import timedelta

from forze.application.contracts.durable.function import (
    DurableRunAdminDepKey,
    DurableRunRecord,
    DurableRunStatus,
    DurableRunStoreDepKey,
    DurableRunStorePort,
)
from forze.application.execution import Deps, ExecutionContext
from forze.base.primitives import JsonDict
from forze.testing import context_from_deps
from forze_kits.integrations.durable import (
    DurableFunctionHandler,
    DurableFunctionRegistry,
    DurableFunctionRunner,
    resolve_durable_run_admin,
)

from torve.config.runconfig import StoreConfig

# ----------------------- #

TASK_FUNCTION = "torve.task"


# ....................... #


def context_for(store: object) -> ExecutionContext:
    """The store registered under both the data plane and the control plane —
    forze's mock and Postgres stores each implement both. Lives here rather
    than beside the adapters: it is forze wiring over the port, and the
    facade may not import `adapters` (RFC 0015 §2.1)."""

    def provide(_ctx: ExecutionContext) -> object:
        return store

    return context_from_deps(
        Deps.plain(
            {
                DurableRunStoreDepKey: provide,
                DurableRunAdminDepKey: provide,
            }
        )
    )


# ....................... #


class TaskStore:
    def __init__(self, store: DurableRunStorePort, config: StoreConfig) -> None:
        self.store = store
        self.ctx = context_for(store)
        self.registry = DurableFunctionRegistry()
        self.runner = DurableFunctionRunner(
            registry=self.registry,
            lease_for=timedelta(seconds=config.lease_for),
            heartbeat_divisor=config.heartbeat_divisor,
            max_run_duration=timedelta(seconds=config.max_run_duration),
        )

    # ....................... #

    def register(self, handler: DurableFunctionHandler, name: str = TASK_FUNCTION) -> None:
        self.registry.register(name, handler)

    # ....................... #

    async def run_now(
        self,
        input_json: JsonDict,
        *,
        idempotency_key: str | None = None,
        name: str = TASK_FUNCTION,
    ) -> DurableRunRecord:
        return await self.runner.run_now(
            self.ctx, name, input_json, idempotency_key=idempotency_key
        )

    # ....................... #

    async def enqueue(self, input_json: JsonDict, *, name: str = TASK_FUNCTION) -> DurableRunRecord:
        return await self.runner.enqueue(self.ctx, name, input_json)

    # ....................... #

    async def recover(self, *, limit: int = 10) -> int:
        return await self.runner.recover(self.ctx, limit=limit)

    # ....................... #

    async def request_cancel(self, run_id: str) -> bool:
        return await self.runner.request_cancel(self.ctx, run_id)

    # ....................... #

    async def expire_abandoned(self, *, limit: int = 50) -> list[DurableRunRecord]:
        """Reclaim runs whose lease expired and land them `lease_expired`.

        The claim advances the fence, so the dead worker's late writes are
        no-ops; the reclaimed record's fence lands the failure."""

        claimed = await self.store.claim_abandoned(limit=limit, lease_for=timedelta(seconds=1))

        for record in claimed:
            await self.store.fail(
                record.run_id, error="lease_expired: reclaimed at reap", fence=record.attempts
            )

        return list(claimed)

    # ....................... #

    async def live_records(self) -> list[DurableRunRecord]:
        admin = resolve_durable_run_admin(self.ctx)
        page = await admin.list_runs(status=DurableRunStatus.RUNNING, limit=200)

        return list(page.records)

    # ....................... #

    async def force_fail_running(self) -> list[DurableRunRecord]:
        """Operator override for `torve reap --force`: unfenced terminal
        writes over every RUNNING record — the one deliberate use of an
        unfenced write, and it exists so a stuck system is always drainable."""

        records = await self.live_records()

        for record in records:
            await self.store.fail(record.run_id, error="forced reap by operator")

        return records
