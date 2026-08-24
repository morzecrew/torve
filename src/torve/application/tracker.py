"""The tracker projection (RFC 0008): outbound effects derived from run
state and staged through the outbox keyed on (task_id, state, attempt) —
D-8.2 — then relayed to the Tracker port; inbound is the fixed command
vocabulary — retry, abandon, unblock, approve (D-8.3; approve records a
sha-bound promotion approval, T-0061) — validated against the real store,
refusals posted back. The board holds no authoritative state (D-8.1): a
projection the tracker refuses is a logged divergence (D-8.6), and the
engine stays right either way. Task contracts are never editable from a
tracker (D-8.4); tracker text is untrusted input everywhere (D-8.5) — the
parse is allow-listed, never free-text interpretation.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from torve.application.outbox import Effect, RelayReport, relay, stage, staged_keys
from torve.application.ports import Tracker, TrackerCommand
from torve.application.projections import escalation_route
from torve.application.runstate import RunState
from torve.application.telemetry import engine_event
from torve.base import naming
from torve.config import layout
from torve.domain.states import TRANSITIONS, TaskState

# ----------------------- #

COMMANDS = ("retry", "abandon", "unblock", "approve")


def _title(root: Path, task_id: str) -> str:
    task_file = layout.task_file(root, task_id)
    if task_file.is_file():
        try:
            from torve.gates.context import load_task

            intent = load_task(task_file).intent.strip()
            if intent:
                head = intent.splitlines()[0]
                return f"{task_id}: {head[:71].rstrip()}…" if len(head) > 72 \
                    else f"{task_id}: {head}"
        except ValueError:
            pass
    return f"{task_id}: task"


def _attempt_bodies(root: Path, task_id: str) -> dict[int, str]:
    """One comment per attempt, never per gate (D-8.7), composed from the
    telemetry records — data, not the agent's prose."""
    manifest = root / layout.TORVE_DIR / "telemetry.jsonl"
    bodies: dict[int, str] = {}
    if not manifest.is_file():
        return bodies
    attempt_no = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = cast("dict[str, Any]", json.loads(line))
        if record.get("task_id") != task_id or record.get("kind") == "engine":
            continue
        attempt_no += 1
        results = cast("list[dict[str, Any]]", record.get("results", []))
        gates = ", ".join(f"{r.get('name')}={r.get('outcome')}" for r in results) or "none"
        agent = cast("dict[str, Any]", record.get("agent") or {})
        parts = [f"attempt {attempt_no}: gates {gates}"]
        if agent.get("cost_usd") is not None:
            parts.append(f"cost ${agent['cost_usd']}")
        if agent.get("trace_ref"):
            parts.append(f"trace {agent['trace_ref']}")
        parts.append(f"config {record.get('config_hash', '')}")
        bodies[attempt_no] = " · ".join(parts)
    return bodies


def project(root: Path, notify_login: str = "") -> int:
    """Derive effects from run state and stage them idempotently; returns
    how many were newly staged. Re-running against unchanged state stages
    nothing — the state file is the transaction the effects derive from."""
    staged = 0
    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        task_id = state.task_id
        title = _title(root, task_id)
        effects = [Effect(key=f"{task_id}:created", kind="created",
                          payload={"task": task_id, "title": title})]
        effects += [
            Effect(key=f"{task_id}:{state.state}:{state.attempts}", kind="state",
                   payload={"task": task_id, "state": str(state.state), "title": title})]
        if state.escalation is not None:
            effects.append(Effect(
                key=f"{task_id}:escalated:{state.attempts}:{state.escalation.reason}",
                kind="escalation",
                payload={"task": task_id, "title": title,
                         "reason": state.escalation.reason,
                         "detail": state.escalation.detail}))
            # The notifier (RFC 0003 D-3.18, policy D-6.4): interrupt-class
            # routes page a person; batch stays board-visible only. Keyed on
            # (task, attempt, reason) — one escalation event notifies once
            # however often projection or relay replays.
            route = escalation_route(str(state.escalation.reason))
            if notify_login and route in ("notify", "harness owner"):
                effects.append(Effect(
                    key=f"{task_id}:notify:{state.attempts}:{state.escalation.reason}",
                    kind="notify",
                    payload={"task": task_id, "title": title,
                             "login": notify_login, "route": route,
                             "reason": state.escalation.reason,
                             "detail": state.escalation.detail}))
        for attempt_no, body in _attempt_bodies(root, task_id).items():
            effects.append(Effect(key=f"{task_id}:attempt:{attempt_no}",
                                  kind="attempt",
                                  payload={"task": task_id, "title": title,
                                           "body": body}))
        staged += sum(1 for e in effects if stage(root, e))
    return staged


def project_landings(root: Path, landed: Callable[[str], bool]) -> int:
    """The close-out the state-driven projection cannot see (D-8.11): a
    landed task's run state is swept after the landing, so only the
    landing trailer knows the task is done. One effect per task, ever —
    the ledger check keeps the git ask off already-closed history, and a
    task with a live state still owns its own projection."""
    staged = 0
    seen = staged_keys(root)
    tasks_dir = root / layout.TORVE_DIR / "tasks"
    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        task_id = contract.parent.name
        if f"{task_id}:landed" in seen:
            continue
        if naming.state_file(root, task_id).exists():
            continue
        if not landed(task_id):
            continue
        if stage(root, Effect(key=f"{task_id}:landed", kind="landed",
                              payload={"task": task_id,
                                       "title": _title(root, task_id)})):
            staged += 1
    return staged


def relay_to_tracker(root: Path, tracker: Tracker) -> RelayReport:
    """Deliver pending effects. A refused or unsupported reflection is a
    logged divergence and the effect is DONE — retrying a refusal forever
    is how a queue rots; persistent divergence surfaces in the events."""

    def deliver(effect: Effect) -> None:
        payload = effect.payload
        task_id, title = str(payload["task"]), str(payload.get("title", ""))
        if effect.kind == "created":
            result = tracker.reflect(task_id, "created", title)
        elif effect.kind == "state":
            result = tracker.reflect(task_id, str(payload["state"]), title)
        elif effect.kind == "landed":
            result = tracker.reflect(task_id, "landed", title)
        elif effect.kind == "escalation":
            body = (f"escalated: {payload['reason']} — {payload['detail']}\n\n"
                    "authority: the run store; this board is a projection")
            tracker.reflect(task_id, f"escalated:{payload['reason']}", title)
            result = tracker.comment(task_id, body, effect.key)
        elif effect.kind == "notify":
            body = (f"escalated: {payload['reason']} — {payload['detail']}\n"
                    f"route: {payload['route']} (D-6.4 — this class "
                    "interrupts; the queue's age is the primary alert)\n\n"
                    "authority: the run store; this board is a projection")
            result = tracker.notify(task_id, str(payload["login"]), body,
                                    effect.key)
        else:
            result = tracker.comment(task_id, str(payload["body"]), effect.key)
        if result.outcome != "applied":
            engine_event(root, "tracker_divergence", {
                "key": effect.key, "outcome": result.outcome,
                "detail": result.detail})

    return relay(root, deliver)


@dataclass
class CommandOutcome:
    verb: str
    task_id: str
    actor: str
    applied: bool
    detail: str = ""


@dataclass
class PollReport:
    outcomes: list[CommandOutcome] = field(default_factory=list)


def _apply(root: Path, command: TrackerCommand,
           requeue: Callable[[str], str] | None = None,
           approve_tip: Callable[[str], str | None] | None = None) -> CommandOutcome:
    verb, task_id = command.verb, command.task_id
    if verb not in COMMANDS:
        return CommandOutcome(verb, task_id, command.actor, False,
                              f"unknown command {verb!r} — the vocabulary is fixed")
    state_path = naming.state_file(root, task_id)
    if not state_path.exists():
        return CommandOutcome(verb, task_id, command.actor, False,
                              "no run state for this task — nothing to act on")
    state = RunState.load(state_path)

    if verb == "retry":
        if state.state is not TaskState.ESCALATED:
            return CommandOutcome(verb, task_id, command.actor, False,
                                  f"retry needs an escalated run; this one is {state.state}")
        # The mechanical re-queue (T-0059): the stale remote branch goes
        # under the commander's explicit authority — a ref deletion, never
        # a rewrite — BEFORE the state moves, so a failed cleanup leaves
        # the escalation standing and the command retryable.
        cleanup = "no re-queue cleanup wired"
        if requeue is not None:
            try:
                cleanup = requeue(task_id)
            except Exception as exc:  # refused, never half-applied
                return CommandOutcome(verb, task_id, command.actor, False,
                                      f"re-queue cleanup failed: {exc}")
        state.transition(TaskState.QUEUED, f"tracker command retry from {command.actor}")
        state.save()
        return CommandOutcome(verb, task_id, command.actor, True,
                              f"re-queued ({cleanup})")

    if verb == "abandon":
        if TaskState.ABANDONED not in TRANSITIONS[state.state]:
            return CommandOutcome(verb, task_id, command.actor, False,
                                  f"abandon is not a legal exit from {state.state}")
        state.transition(TaskState.ABANDONED,
                         f"tracker command abandon from {command.actor}")
        state.save()
        return CommandOutcome(verb, task_id, command.actor, True, "abandoned")

    if verb == "approve":
        if state.state is not TaskState.READY:
            return CommandOutcome(verb, task_id, command.actor, False,
                                  f"approve needs a ready candidate; this one is {state.state}")
        if approve_tip is None:
            return CommandOutcome(verb, task_id, command.actor, False,
                                  "no approval wiring — the poller carries no vcs")
        tip = approve_tip(task_id)
        if tip is None:
            return CommandOutcome(verb, task_id, command.actor, False,
                                  "no branch to approve — nothing would land")
        from torve.application.lane import record_approval

        # Sha-bound at apply time (RFC 0006 §3): the approval covers the
        # tip as it stands now; a later push supersedes it silently.
        if not record_approval(root, task_id, command.actor, tip):
            return CommandOutcome(verb, task_id, command.actor, True,
                                  f"already approved {tip[:10]}")
        return CommandOutcome(verb, task_id, command.actor, True,
                              f"approved {tip[:10]}")

    # unblock: dependency holds are checked at dispatch, so the command
    # validates and informs — it never mutates state it does not hold.
    task_file = layout.task_file(root, task_id)
    if task_file.is_file():
        from torve.gates.context import load_task

        for dep in load_task(task_file).depends_on:
            dep_path = naming.state_file(root, dep)
            dep_state = RunState.load(dep_path).state if dep_path.exists() else None
            if dep_state is not TaskState.READY:
                return CommandOutcome(verb, task_id, command.actor, False,
                                      f"dependency {dep} is not ready — still holds")
    return CommandOutcome(verb, task_id, command.actor, True,
                          "no active hold — dispatch re-checks at run time")


def poll_and_apply(root: Path, tracker: Tracker,
                   commanders: tuple[str, ...] = (),
                   requeue: Callable[[str], str] | None = None,
                   approve_tip: Callable[[str], str | None] | None = None,
                   ) -> PollReport:
    """Inbound commands: authorization precedes validation — a command
    applies only when its actor is a configured commander, and an empty
    list refuses everyone (T-0054; the board is an unattended channel once
    the loop polls it). Every outcome — applied or refused — is answered
    on the thread it came from."""
    report = PollReport()
    for command in tracker.poll_commands():
        if command.actor not in commanders:
            outcome = CommandOutcome(
                command.verb, command.task_id, command.actor, False,
                f"actor {command.actor} is not a configured commander "
                "(tracker.commanders)")
        else:
            outcome = _apply(root, command, requeue, approve_tip)
        word = "applied" if outcome.applied else "refused"
        tracker.comment(
            command.task_id,
            f"{word}: {command.verb} — {outcome.detail}\n\n"
            "authority: the run store; this board is a projection",
            f"cmd:{command.source}")
        engine_event(root, "tracker_command", {
            "verb": command.verb, "task": command.task_id,
            "actor": command.actor, "applied": outcome.applied,
            "detail": outcome.detail})
        report.outcomes.append(outcome)
    return report
