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

COMMANDS = ("retry", "abandon", "unblock", "approve", "revise", "adopt")


# ....................... #


def _title(root: Path, task_id: str) -> str:
    task_file = layout.task_file(root, task_id)

    if task_file.is_file():
        try:
            from torve.gates.context import load_task

            intent = load_task(task_file).intent.strip()

            if intent:
                head = intent.splitlines()[0]

                return (
                    f"{task_id}: {head[:71].rstrip()}…" if len(head) > 72 else f"{task_id}: {head}"
                )

        except ValueError:
            pass

    return f"{task_id}: task"


# ....................... #


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
            # The forge rule (T-0084): a host-absolute path says nothing
            # on the board — its basename names the artefact; a URI stays.
            trace = str(agent["trace_ref"])

            if "://" not in trace:
                trace = Path(trace).name

            parts.append(f"trace {trace}")

        parts.append(f"config {record.get('config_hash', '')}")
        bodies[attempt_no] = " · ".join(parts)

    return bodies


# ....................... #


def _task(root: Path, task_id: str) -> Any | None:
    contract = layout.task_file(root, task_id)

    if not contract.is_file():
        return None

    try:
        from torve.gates.context import load_task

        return load_task(contract)

    except ValueError:
        return None


# ....................... #


def _role(root: Path, task_id: str) -> str:
    task = _task(root, task_id)
    return task.role if task is not None else ""


# ....................... #


def project_approval_gap(root: Path, task_id: str, sha: str, need: int) -> bool:
    """The board says where the human is needed (D-8.13): a candidate the
    lane refused for want of approvals prompts on its thread — keyed on
    the tip, so one prompt per tip and a superseded tip prompts afresh."""

    return stage(
        root,
        Effect(
            key=f"{task_id}:needs-approval:{sha}",
            kind="approval_needed",
            payload={"task": task_id, "title": _title(root, task_id), "sha": sha, "need": need},
        ),
    )


# ....................... #


def _review_effects(
    root: Path, state: RunState, task_id: str, target: str, notify_login: str
) -> list[Effect]:
    """A review projects onto its target's thread (D-8.16, A-33): the
    board row belongs to the work, and the review's milestones are
    comments there — an escalation notifies where the retry or abandon
    decision lives. Keys keep the review's own id, so replays dedupe."""

    title = _title(root, target)
    effects: list[Effect] = []

    if state.escalation is not None:
        route = escalation_route(str(state.escalation.reason))
        detail = f"review {task_id}: {state.escalation.detail}"

        effects.append(
            Effect(
                key=f"{task_id}:escalated:{state.attempts}:{state.escalation.reason}",
                kind="escalation",
                payload={
                    "task": target,
                    "title": title,
                    "reason": state.escalation.reason,
                    "detail": detail,
                },
            )
        )

        if notify_login and route in ("notify", "harness owner"):
            effects.append(
                Effect(
                    key=f"{task_id}:notify:{state.attempts}:{state.escalation.reason}",
                    kind="notify",
                    payload={
                        "task": target,
                        "title": title,
                        "login": notify_login,
                        "route": route,
                        "reason": state.escalation.reason,
                        "detail": detail,
                    },
                )
            )

    for attempt_no, body in _attempt_bodies(root, task_id).items():
        effects.append(
            Effect(
                key=f"{task_id}:attempt:{attempt_no}",
                kind="attempt",
                payload={"task": target, "title": title, "body": f"review {task_id} · {body}"},
            )
        )

    return effects


# ....................... #


def project(root: Path, notify_login: str = "") -> int:
    """Derive effects from run state and stage them idempotently; returns
    how many were newly staged. Re-running against unchanged state stages
    nothing — the state file is the transaction the effects derive from."""

    staged = 0
    prompted = staged_keys(root)

    for state in RunState.load_all(root / naming.WORKTREE_DIR):
        task_id = state.task_id

        # Shadow runs are measurement, never work (RFC 0004 §5, the A-57
        # visibility family): a replay's state file must not become a board
        # issue — eight "shadow-T-nnnn: task" issues taught this.
        if task_id.startswith("shadow-"):
            continue

        task = _task(root, task_id)

        if task is not None and task.role == "review":
            # No issue for the machine's own work (D-8.16): its milestones
            # reach the target's thread; targetless reviews project nothing.
            if task.targets:
                staged += sum(
                    1
                    for e in _review_effects(root, state, task_id, task.targets[0], notify_login)
                    if stage(root, e)
                )

            continue

        title = _title(root, task_id)

        effects = [
            Effect(
                key=f"{task_id}:created", kind="created", payload={"task": task_id, "title": title}
            )
        ]

        effects += [
            # The transition ordinal joins the key (A-30): a state revisited
            # at the same attempt is a new fact; a replay between
            # transitions is not.
            Effect(
                key=(f"{task_id}:{state.state}:{state.attempts}:{len(state.history)}"),
                kind="state",
                payload={"task": task_id, "state": str(state.state), "title": title},
            )
        ]

        if state.state is not TaskState.READY and any(
            k.startswith(f"{task_id}:needs-approval:") for k in prompted
        ):
            # The label follows the gap (D-8.17, A-36): a run outside ready
            # has no approval to want — cleared once per transition (the
            # A-30 ordinal), and only for tasks the ledger shows were ever
            # prompted, so unworn labels stage nothing.
            effects.append(
                Effect(
                    key=f"{task_id}:na-clear:{len(state.history)}",
                    kind="unlabel",
                    payload={"task": task_id, "name": "needs:approval"},
                )
            )

        if state.escalation is not None:
            effects.append(
                Effect(
                    key=f"{task_id}:escalated:{state.attempts}:{state.escalation.reason}",
                    kind="escalation",
                    payload={
                        "task": task_id,
                        "title": title,
                        "reason": state.escalation.reason,
                        "detail": state.escalation.detail,
                    },
                )
            )

            # The notifier (RFC 0003 D-3.18, policy D-6.4): interrupt-class
            # routes page a person; batch stays board-visible only. Keyed on
            # (task, attempt, reason) — one escalation event notifies once
            # however often projection or relay replays.
            route = escalation_route(str(state.escalation.reason))

            if notify_login and route in ("notify", "harness owner"):
                effects.append(
                    Effect(
                        key=f"{task_id}:notify:{state.attempts}:{state.escalation.reason}",
                        kind="notify",
                        payload={
                            "task": task_id,
                            "title": title,
                            "login": notify_login,
                            "route": route,
                            "reason": state.escalation.reason,
                            "detail": state.escalation.detail,
                        },
                    )
                )

        for attempt_no, body in _attempt_bodies(root, task_id).items():
            effects.append(
                Effect(
                    key=f"{task_id}:attempt:{attempt_no}",
                    kind="attempt",
                    payload={"task": task_id, "title": title, "body": body},
                )
            )

        staged += sum(1 for e in effects if stage(root, e))

    return staged


# ....................... #


def _discharged(contract: Path, task_id: str, landed: Callable[[str], bool]) -> bool:
    """Repo-recorded doneness (D-8.11): an executable task's own landing
    trailer — or, for a review, its every target's (T-0066): a review
    never lands, so its discharge is the landing of what it reviewed."""

    try:
        from torve.gates.context import load_task

        task = load_task(contract)

    except ValueError:
        return False

    if task.role == "review":
        return bool(task.targets) and all(landed(t) for t in task.targets)

    return landed(task_id)


# ....................... #


def project_landings(root: Path, landed: Callable[[str], bool]) -> int:
    """The close-out the state-driven projection cannot see (D-8.11): a
    landed task's run state is swept after the landing, so only the
    landing trailer knows the task is done. One effect per task, ever —
    the ledger check keeps the git ask off already-closed history, and a
    task with a live state still owns its own projection."""

    staged = 0
    seen = staged_keys(root)
    tasks_dir = root / layout.TORVE_DIR / "tasks"

    def _clear_prompt(task_id: str) -> int:
        # The backstop clear (D-8.17, A-36) — including tasks that landed
        # before the amendment: the ledger remembers who was prompted.
        if not any(k.startswith(f"{task_id}:needs-approval:") for k in seen):
            return 0

        return int(
            stage(
                root,
                Effect(
                    key=f"{task_id}:na-clear:landed",
                    kind="unlabel",
                    payload={"task": task_id, "name": "needs:approval"},
                ),
            )
        )

    for contract in sorted(tasks_dir.glob("T-*/contract.yaml")):
        task_id = contract.parent.name

        if f"{task_id}:landed" in seen:
            staged += _clear_prompt(task_id)
            continue

        if naming.state_file(root, task_id).exists():
            continue

        if not _discharged(contract, task_id, landed):
            continue

        if stage(
            root,
            Effect(
                key=f"{task_id}:landed",
                kind="landed",
                payload={"task": task_id, "title": _title(root, task_id)},
            ),
        ):
            staged += 1

        staged += _clear_prompt(task_id)

    return staged


# ....................... #


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
        elif effect.kind == "approval_needed":
            tracker.label(task_id, "needs:approval")

            result = tracker.comment(
                task_id,
                (
                    f"candidate {str(payload['sha'])[:10]} is ready to land and "
                    f"needs {payload['need']} approval(s) — reply with "
                    "`/torve approve`"
                ),
                effect.key,
            )
        elif effect.kind == "escalation":
            body = (
                f"escalated: {payload['reason']} — {payload['detail']}\n\n"
                "authority: the run store; this board is a projection"
            )

            tracker.reflect(task_id, f"escalated:{payload['reason']}", title)
            result = tracker.comment(task_id, body, effect.key)
        elif effect.kind == "unlabel":
            result = tracker.unlabel(task_id, str(payload["name"]))
        elif effect.kind == "notify":
            body = (
                f"escalated: {payload['reason']} — {payload['detail']}\n"
                f"route: {payload['route']} (D-6.4 — this class "
                "interrupts; the queue's age is the primary alert)\n\n"
                "authority: the run store; this board is a projection"
            )

            result = tracker.notify(task_id, str(payload["login"]), body, effect.key)
        else:
            result = tracker.comment(task_id, str(payload["body"]), effect.key)

        if result.outcome != "applied":
            engine_event(
                root,
                "tracker_divergence",
                {"key": effect.key, "outcome": result.outcome, "detail": result.detail},
            )

    return relay(root, deliver)


# ....................... #


@dataclass
class CommandOutcome:
    verb: str
    task_id: str
    actor: str
    applied: bool
    detail: str = ""


# ....................... #


@dataclass
class PollReport:
    outcomes: list[CommandOutcome] = field(default_factory=list)


# ....................... #


def _apply(
    root: Path,
    command: TrackerCommand,
    requeue: Callable[[str], str] | None = None,
    approve_tip: Callable[[str], str | None] | None = None,
    adopt_drafts: Callable[[str], list[str]] | None = None,
    draft_feedback: Callable[[str, str], str] | None = None,
) -> CommandOutcome:
    verb, task_id = command.verb, command.task_id

    if verb not in COMMANDS:
        return CommandOutcome(
            verb,
            task_id,
            command.actor,
            False,
            f"unknown command {verb!r} — the vocabulary is fixed",
        )

    if verb == "adopt":
        # Before the state guard (RFC 0020, D-20.1): a swept READY draft
        # is legitimately stateless — the drafts file is the evidence of
        # its green run, and the application refuses everything else.
        if _role(root, task_id) != "draft":
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                "adopt consumes a drafting run's output — "
                f"this task's role is {_role(root, task_id) or 'unknown'!r}",
            )

        if adopt_drafts is None:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                "no adoption wiring — the poller carries no config",
            )

        try:
            adopted = adopt_drafts(task_id)

        except (ValueError, RuntimeError) as exc:
            return CommandOutcome(verb, task_id, command.actor, False, str(exc))

        return CommandOutcome(verb, task_id, command.actor, True, f"adopted: {', '.join(adopted)}")

    state_path = naming.state_file(root, task_id)

    if not state_path.exists():
        return CommandOutcome(
            verb, task_id, command.actor, False, "no run state for this task — nothing to act on"
        )

    state = RunState.load(state_path)

    if verb == "retry":
        if state.state is not TaskState.ESCALATED:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                f"retry needs an escalated run; this one is {state.state}"
                + (
                    " — a ready candidate re-enters with /torve revise"
                    if state.state is TaskState.READY
                    else ""
                ),
            )

        # The mechanical re-queue (T-0059): the stale remote branch goes
        # under the commander's explicit authority — a ref deletion, never
        # a rewrite — BEFORE the state moves, so a failed cleanup leaves
        # the escalation standing and the command retryable.
        cleanup = "no re-queue cleanup wired"

        if requeue is not None:
            try:
                cleanup = requeue(task_id)

            except Exception as exc:  # refused, never half-applied
                return CommandOutcome(
                    verb, task_id, command.actor, False, f"re-queue cleanup failed: {exc}"
                )

        state.transition(TaskState.QUEUED, f"tracker command retry from {command.actor}")
        state.save()

        return CommandOutcome(verb, task_id, command.actor, True, f"re-queued ({cleanup})")

    if verb == "abandon":
        if _role(root, task_id) == "draft" and state.state is TaskState.READY:
            # RFC 0020 §5.4: refusing a request discards its drafts — the
            # legal route to the terminal verdict runs through escalated,
            # and the drafts must not outlive the refusal (a swept state
            # would otherwise leave them adoptable, D-20.10).
            from torve.application.intake import drafts_file

            state.transition(TaskState.ESCALATED, f"tracker command abandon from {command.actor}")
            state.transition(TaskState.ABANDONED, f"tracker command abandon from {command.actor}")
            state.save()
            drafts_file(root, task_id).unlink(missing_ok=True)

            return CommandOutcome(
                verb, task_id, command.actor, True, "request refused — drafts discarded"
            )

        if TaskState.ABANDONED not in TRANSITIONS[state.state]:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                f"abandon is not a legal exit from {state.state}",
            )

        state.transition(TaskState.ABANDONED, f"tracker command abandon from {command.actor}")
        state.save()

        return CommandOutcome(verb, task_id, command.actor, True, "abandoned")

    if verb == "revise":
        # A-40 (D-8.18): the commander's re-queue of a ready candidate —
        # a review finding worth another attempt before it lands. Same
        # capture-first cleanup as retry; the branch persists (D-10.10).
        if _role(root, task_id) == "review":
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                "a review is never revised — it re-runs with its target",
            )

        if state.state is not TaskState.READY:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                f"revise needs a ready candidate; this one is {state.state}",
            )

        if _role(root, task_id) == "draft":
            # RFC 0020 (D-20.6): the commander's comment IS the feedback —
            # its text reaches the drafter, and the run re-queues for the
            # intake leg. No branch, no capture: drafts have neither.
            if draft_feedback is None:
                return CommandOutcome(
                    verb,
                    task_id,
                    command.actor,
                    False,
                    "no drafting-feedback wiring — the poller carries no config",
                )

            note = draft_feedback(task_id, command.text)
            state.transition(TaskState.QUEUED, f"tracker command revise from {command.actor}")
            state.save()

            return CommandOutcome(
                verb, task_id, command.actor, True, f"re-queued for re-drafting ({note})"
            )

        cleanup = "no re-queue cleanup wired"

        if requeue is not None:
            try:
                cleanup = requeue(task_id)

            except Exception as exc:  # refused, never half-applied
                return CommandOutcome(
                    verb, task_id, command.actor, False, f"revision capture failed: {exc}"
                )

        state.transition(TaskState.QUEUED, f"tracker command revise from {command.actor}")
        state.save()

        return CommandOutcome(
            verb, task_id, command.actor, True, f"re-queued for revision ({cleanup})"
        )

    if verb == "approve":
        if _role(root, task_id) == "review":
            # A review is never landed (D-8.14): approving one would bind
            # a sha nothing will ever count.
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                "this is a review task — it is never landed, so there is nothing to approve",
            )

        if state.state is not TaskState.READY:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                f"approve needs a ready candidate; this one is {state.state}",
            )

        if approve_tip is None:
            return CommandOutcome(
                verb,
                task_id,
                command.actor,
                False,
                "no approval wiring — the poller carries no vcs",
            )

        tip = approve_tip(task_id)

        if tip is None:
            return CommandOutcome(
                verb, task_id, command.actor, False, "no branch to approve — nothing would land"
            )

        from torve.application.lane import record_approval

        # Sha-bound at apply time (RFC 0006 §3): the approval covers the
        # tip as it stands now; a later push supersedes it silently.
        if not record_approval(root, task_id, command.actor, tip):
            return CommandOutcome(
                verb, task_id, command.actor, True, f"already approved {tip[:10]}"
            )

        # The label follows the gap (D-8.17, A-36): the approval this
        # prompt asked for arrived — its removal rides the same outbox.
        stage(
            root,
            Effect(
                key=f"{task_id}:na-clear:approved:{tip}",
                kind="unlabel",
                payload={"task": task_id, "name": "needs:approval"},
            ),
        )

        return CommandOutcome(verb, task_id, command.actor, True, f"approved {tip[:10]}")

    # unblock: dependency holds are checked at dispatch, so the command
    # validates and informs — it never mutates state it does not hold.
    task_file = layout.task_file(root, task_id)

    if task_file.is_file():
        from torve.gates.context import load_task

        for dep in load_task(task_file).depends_on:
            dep_path = naming.state_file(root, dep)
            dep_state = RunState.load(dep_path).state if dep_path.exists() else None

            if dep_state is not TaskState.READY:
                return CommandOutcome(
                    verb,
                    task_id,
                    command.actor,
                    False,
                    f"dependency {dep} is not ready — still holds",
                )

    return CommandOutcome(
        verb, task_id, command.actor, True, "no active hold — dispatch re-checks at run time"
    )


# ....................... #


def poll_and_apply(
    root: Path,
    tracker: Tracker,
    commanders: tuple[str, ...] = (),
    requeue: Callable[[str], str] | None = None,
    approve_tip: Callable[[str], str | None] | None = None,
    adopt_drafts: Callable[[str], list[str]] | None = None,
    draft_feedback: Callable[[str, str], str] | None = None,
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
                command.verb,
                command.task_id,
                command.actor,
                False,
                f"actor {command.actor} is not a configured commander (tracker.commanders)",
            )
        else:
            outcome = _apply(root, command, requeue, approve_tip, adopt_drafts, draft_feedback)

        word = "applied" if outcome.applied else "refused"

        tracker.comment(
            command.task_id,
            f"{word}: {command.verb} — {outcome.detail}\n\n"
            "authority: the run store; this board is a projection",
            f"cmd:{command.source}",
        )

        engine_event(
            root,
            "tracker_command",
            {
                "verb": command.verb,
                "task": command.task_id,
                "actor": command.actor,
                "applied": outcome.applied,
                "detail": outcome.detail,
            },
        )

        report.outcomes.append(outcome)

    return report
