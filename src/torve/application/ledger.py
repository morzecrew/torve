"""`torve ledger` — the record folded into rates rather than rows (S-0065/D-1).

Every later comparison this engine makes — one seat against another, a hook
against no hook, an arm against its incumbent — is a ratio, and until this
module existed not one of them was reachable from a verb. The four the
engine's case rests on are computed here, per seat and per gate, from the
telemetry stream and the landings in the tree.

Three rules carry the whole module:

- A seat is a tier and the image it was pointed at, together (S-0065/D-2). One tier
  name has been aimed at three images over this record, and a rate blended
  across them describes nothing that exists.
- A rate counts only attempts that ran a model (S-0065/D-5, LOCKED): `fake` adapters
  and shadow replays are excluded, and the exclusion is printed rather than
  left to whoever reads the denominator.
- An unreported cost is never zero (S-0065/D-1, S-0004/D-6, S-0064/D-12). A subscription seat
  genuinely has no per-token cost, and a zero would be a lie that averages.

The contract an attempt was fenced by is read at the attempt's own sha
through git (S-0065/D-3), so a task directory deleted under S-0056/D-10 costs a
`git cat-file` rather than a fact. Rows carrying no sha at all can be joined
to nothing and are reported as unjoinable and excluded from every rate
(S-0065/D-4); the writer refuses to add more of them.

Rows are the renderers' business (S-0065/D-1): nothing here lists an attempt, a task
or a gate run that `torve why`, `torve status` or the projection feed
already prints.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from torve.application.projections import shipped_ids, stream_rows
from torve.base.clock import parse
from torve.config import layout

if TYPE_CHECKING:
    from collections.abc import Iterable

# ----------------------- #

LEDGER_SCHEMA_VERSION = 1

# The exclusions S-0065/D-5 locks, by the reason each row was dropped. `fake` is
# simulation — twenty-four such records went red on every gate in six
# minutes and would make a seat look catastrophic for free. A shadow replay
# measures a regime and merges nothing, so it lands nothing and belongs to
# no landing rate. `review` and `intake` are another task's spend under this
# task's id, the same exclusion `projections` draws one step earlier.
EXCLUDED_KINDS = frozenset({"shadow", "skill-eval", "engine", "review", "intake"})

# A conviction is a blocking gate that failed (the pairing `projections`
# uses): a shadow gate's red convicts nobody, and an `error` is the battery
# breaking rather than the work being wrong.
CONVICTION_OUTCOMES = frozenset({"fail"})
CONVICTION_STATES = frozenset({"blocking", ""})

# Where a contract was found for an attempt. `tree` is the working tree,
# `history` is `git cat-file` at the attempt's own sha, and `absent` covers
# the attempts that ran before their contract was committed — a fact about
# those attempts, not a defect (S-0065/D-3).
CONTRACT_SOURCES = ("tree", "history", "absent")


# ....................... #


@dataclass
class _Seat:
    """One tier on one image, accumulating as rows are folded into it."""

    tier: str
    image: str
    attempts: int = 0
    cost_usd: float | None = None
    wall_time_s: float = 0.0
    # The per-line numerators D-3 folds beside cost and wall (S-0075/D-3): the
    # seat's whole spend against the work it produced, where a production a
    # harness never measured stays unreported rather than zero (S-0004/D-6).
    cache_read_tokens: float | None = None
    tool_calls: float | None = None
    # The changed lines the seat's landed tasks committed, per path — the
    # denominator S-0075/D-3 divides by, from the diff the landing commits.
    files: dict[str, int] = field(default_factory=dict)
    tasks: set[str] = field(default_factory=set)
    convictions_by_task: dict[str, int] = field(default_factory=dict)
    first_at: str = ""
    last_at: str = ""


# ....................... #


def _agent(row: dict[str, Any]) -> dict[str, Any]:
    block: Any = row.get("agent")

    return cast("dict[str, Any]", block) if isinstance(block, dict) else {}


def _number(value: Any) -> float | None:
    """A recorded number, or None. `bool` is an int in Python and never a
    measurement here."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        return None

    return float(value)


def _tool_calls(agent: dict[str, Any]) -> float | None:
    """The tool calls one attempt made, as the burn profile counted them
    (S-0039): the harness's own turn count, or the classified profile's call
    count where the stream named only that. Absent stays absent — a profile
    never derived reads as unreported, never as zero (S-0004/D-6)."""

    burn = agent.get("burn")

    if not isinstance(burn, dict):
        return None

    calls: Any = burn.get("tool_calls")

    if isinstance(calls, (int, float)) and not isinstance(calls, bool):
        return float(calls)

    profile = burn.get("profile")

    if isinstance(profile, dict):
        calls = profile.get("calls")

        if isinstance(calls, (int, float)) and not isinstance(calls, bool):
            return float(calls)

    return None


def _convictions(row: dict[str, Any]) -> list[str]:
    """The blocking gates this row records as failed, by name."""

    results: Any = row.get("results")

    if not isinstance(results, list):
        return []

    found: list[str] = []

    for entry in cast("list[object]", results):
        if not isinstance(entry, dict):
            continue

        result = cast("dict[str, Any]", entry)

        if (
            str(result.get("outcome", "")) in CONVICTION_OUTCOMES
            and str(result.get("state", "")) in CONVICTION_STATES
        ):
            found.append(str(result.get("name", "?")))

    return found


# ....................... #


def counted_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The rows a rate may count, beside the tally of what was dropped and
    why (S-0065/D-5: the exclusion is reported, never silent).

    `no_base_sha` is S-0065/D-4's eighteen: an empty `merge_base` and an empty
    `head` join to no contract, no diff and no landing, so they are reported
    as unjoinable rather than quietly moving an average. The writer refuses
    to add more.
    """

    counted: list[dict[str, Any]] = []
    dropped = {"not_an_attempt": 0, "fake_adapter": 0, "shadow_replay": 0, "no_base_sha": 0}

    for row in rows:
        kind = str(row.get("kind", ""))
        agent = _agent(row)

        if kind in {"shadow", "skill-eval"}:
            dropped["shadow_replay"] += 1
        elif kind in EXCLUDED_KINDS or not agent:
            dropped["not_an_attempt"] += 1
        elif agent.get("adapter") == "fake":
            dropped["fake_adapter"] += 1
        elif agent.get("shadow") is True:
            dropped["shadow_replay"] += 1
        elif not row.get("merge_base") and not row.get("head"):
            dropped["no_base_sha"] += 1
        else:
            counted.append(row)

    return counted, dropped


# ....................... #


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        # A contract is committed text, but a repository holds whatever bytes
        # someone put in it, and a ledger read must not die on one.
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _diff_numstat(root: Path, base: str, head: str) -> dict[str, int] | None:
    """One attempt's work, sized the way the landing commits it (S-0075/D-3):
    per-path changed lines, additions plus deletions, read from `git diff
    --numstat` between the attempt's base and head.

    None where git cannot resolve the pair — a diff that cannot be read has
    no line count, and its task reports no per-line rate rather than a
    fabricated one. Binary files carry no line count and are left out; a
    change that only touched them reports zero changed lines, which the S-0075
    tests section says shares the no-rate of a change that changed nothing.
    """

    result = _git(root, "diff", "--no-renames", "--numstat", "--ignore-submodules", base, head)

    if result.returncode != 0:
        return None

    per_file: dict[str, int] = {}

    for line in result.stdout.splitlines():
        parts = line.split("\t")

        if len(parts) < 3:
            continue

        added, deleted = parts[0], parts[1]

        if added == "-" or deleted == "-":
            continue

        try:
            lines = int(added) + int(deleted)
        except ValueError:
            continue

        path = "\t".join(parts[2:])
        per_file[path] = per_file.get(path, 0) + lines

    return per_file


def _newest_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The newest counted attempt of each task, by its own clock. A task's
    work is one diff whatever it took to land, so the diff is read from the
    attempt that finished it — the newest — not multiplied by the attempts
    that led to it (S-0069/D-4's repair starts from a convicted tree)."""

    newest: dict[str, dict[str, Any]] = {}

    for row in rows:
        task = str(row.get("task_id") or "")

        if not task:
            continue

        latest = newest.get(task)

        if latest is None or str(row.get("at") or "") > str(latest.get("at") or ""):
            newest[task] = row

    return newest


def _task_diffs(root: Path, rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per task, the diff the landing commits (S-0075/D-3), as per-path
    changed lines, from the newest counted attempt's own base and head. A
    task whose newest attempt names no resolvable pair is absent — no diff, no
    lines, no rate."""

    found: dict[str, dict[str, int]] = {}

    for task, row in _newest_rows(rows).items():
        base = str(row.get("merge_base") or "")
        head = str(row.get("head") or "")

        if not base or not head:
            continue

        per_file = _diff_numstat(root, base, head)

        if per_file is not None:
            found[task] = per_file

    return found


def _attach_diff_lines(
    seats: dict[tuple[str, str], _Seat],
    diffs: dict[str, dict[str, int]],
    rows: list[dict[str, Any]],
    landed: set[str],
) -> None:
    """The S-0075/D-3 denominator: a landed task's changed lines land on the
    seat that finished it, so a per-line rate is a rate over work that
    shipped. A task that never landed changes lines no landing commits, and
    D-3's text says the diff the landing already commits — so it contributes
    none. A task with no resolvable diff contributes none either."""

    for task, row in _newest_rows(rows).items():
        if task not in landed:
            continue

        per_file = diffs.get(task)

        if per_file is None:
            continue

        agent = _agent(row)
        key = (str(agent.get("tier") or "unnamed"), str(agent.get("image") or "unnamed"))
        seat = seats.get(key)

        if seat is None:
            continue

        for path, lines in per_file.items():
            seat.files[path] = seat.files.get(path, 0) + lines


def contract_at(
    root: Path,
    task_id: str,
    sha: str,
    cache: dict[tuple[str, str], dict[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    """What an attempt was fenced by and judged against, read at the
    attempt's own sha (S-0065/D-3) — the inherited rows, the scope and the
    acceptance, whether or not the contract is still in the working tree.

    S-0056/D-10 deletes a task directory whose contract names an archived
    document and claims git keeps the history; this is the reader that makes
    that claim true rather than merely stated. Measured over the record as
    it stands, it resolves 243 of the 264 attempts whose contract has left
    the tree.

    None where neither the tree nor history holds one — an attempt that ran
    before its contract was committed. `cache` is keyed by sha and path
    because one sha answers for every attempt that built on it, which is
    what keeps a long record from paying a subprocess per row.
    """

    rel = layout.task_file(Path(), task_id)
    key = (sha, str(rel))

    if cache is not None and key in cache:
        return cache[key]

    found = _read_contract(root, rel, sha)

    if cache is not None:
        cache[key] = found

    return found


def _read_contract(root: Path, rel: Path, sha: str) -> dict[str, Any] | None:
    """The tree first, history second: a contract still on disk is the one
    the attempt ran against and costs no subprocess."""

    on_disk = root / rel
    text: str | None = None

    if on_disk.is_file():
        text = on_disk.read_text(encoding="utf-8")
    elif sha:
        shown = _git(root, "cat-file", "-p", f"{sha}:{rel.as_posix()}")

        if shown.returncode == 0:
            text = shown.stdout

    if text is None:
        return None

    try:
        parsed: Any = yaml.safe_load(text)

    except yaml.YAMLError:
        return None

    if not isinstance(parsed, dict):
        return None

    contract = cast("dict[str, Any]", parsed)

    return {
        "task": contract.get("id") or None,
        "spec": contract.get("spec") or None,
        "scope": contract.get("scope") or {},
        "acceptance": contract.get("acceptance") or [],
        "decisions": contract.get("decisions") or [],
        "found_in_tree": on_disk.is_file(),
    }


# ....................... #


def _fold_seats(rows: list[dict[str, Any]]) -> dict[tuple[str, str], _Seat]:
    seats: dict[tuple[str, str], _Seat] = {}

    for row in rows:
        agent = _agent(row)
        # An unnamed tier or image is reported as unnamed rather than merged
        # into a neighbour: a seat the record cannot name is its own seat.
        key = (str(agent.get("tier") or "unnamed"), str(agent.get("image") or "unnamed"))
        seat = seats.setdefault(key, _Seat(tier=key[0], image=key[1]))

        seat.attempts += 1

        cost = _number(agent.get("cost_usd"))

        if cost is not None:
            # Present costs sum; a seat where none was ever reported keeps
            # None, which prints as unreported and never as zero (S-0064/D-12).
            seat.cost_usd = cost if seat.cost_usd is None else seat.cost_usd + cost

        wall = _number(agent.get("wall_time_s"))

        if wall is not None:
            seat.wall_time_s += wall

        # The per-line numerators S-0075/D-3 folds beside cost and wall,
        # with the same unreported-stays-unreported regime: a seat whose
        # harness never carried token counts or a burn profile reports no
        # per-line rate for them, never a zero (S-0004/D-6).
        tokens = _number(agent.get("cache_read_tokens"))

        if tokens is not None:
            seat.cache_read_tokens = (
                tokens if seat.cache_read_tokens is None else seat.cache_read_tokens + tokens
            )

        calls = _tool_calls(agent)

        if calls is not None:
            seat.tool_calls = calls if seat.tool_calls is None else seat.tool_calls + calls

        task_id = str(row.get("task_id") or "")

        if task_id:
            seat.tasks.add(task_id)
            seat.convictions_by_task[task_id] = seat.convictions_by_task.get(task_id, 0) + len(
                _convictions(row)
            )

        at = str(row.get("at") or "")

        if at:
            seat.first_at = min(seat.first_at, at) if seat.first_at else at
            seat.last_at = max(seat.last_at, at)

    return seats


def _span_seconds(first: str, last: str) -> float | None:
    if not first or not last:
        return None

    try:
        return (parse(last) - parse(first)).total_seconds()

    except ValueError:
        return None


def _ratio(numerator: float | None, denominator: float) -> float | None:
    """A rate, or None where the denominator would invent one. A seat that
    has landed nothing has no cost per landing — not a zero, and not an
    infinity."""

    if numerator is None or denominator <= 0:
        return None

    return numerator / denominator


def _seat_entry(seat: _Seat, landed: set[str]) -> dict[str, Any]:
    landings = float(len(seat.tasks & landed))

    # One numerator basis for all three rates: everything the seat did, over
    # what it landed. Restricting the numerator to landed tasks would price
    # a landing at what its own attempts cost and hide the abandoned work
    # beside them, which is the number the engine's case has to survive.
    convictions = float(sum(seat.convictions_by_task.values()))
    span_s = _span_seconds(seat.first_at, seat.last_at)
    lines = float(sum(seat.files.values()))

    def per_line(total: float | None, denominator: float) -> float | None:
        # S-0075's tests section: a change that changed nothing has no rate,
        # not an infinity — and an absent numerator is an unreported spend,
        # never a zero (S-0004/D-6).
        if total is None or denominator <= 0:
            return None

        return total / denominator

    return {
        "tier": seat.tier,
        "image": seat.image,
        "attempts": seat.attempts,
        "tasks": len(seat.tasks),
        "landed_tasks": int(landings),
        "cost_usd": seat.cost_usd,
        "cost_per_landed_task_usd": _ratio(seat.cost_usd, landings),
        "attempts_per_landing": _ratio(float(seat.attempts), landings),
        "convictions_before_landing": _ratio(convictions, landings),
        # Both sides of the ratio ride beside it: the denominator of this
        # rate is the one the specification left open, so a reader must be
        # able to see what was divided rather than trust the word.
        "wall_time_s": round(seat.wall_time_s, 3),
        "span_s": None if span_s is None else round(span_s, 3),
        "duty_cycle": _ratio(seat.wall_time_s, span_s or 0.0),
        # The S-0075/D-3 rates divide by the work rather than by the task: a
        # fixed overhead that only hurts a small change is visible as a high
        # per-line figure instead of averaging away. The numerators ride
        # beside the rates like the per-landing ones do — a reader sees what
        # was divided, and the seat's changed lines with it. Per-file rates
        # divide the same numerators by the file's own lines, so the path
        # that carried the cost is the path that says so.
        "cache_read_tokens": seat.cache_read_tokens,
        "tool_calls": seat.tool_calls,
        "changed_lines": int(lines),
        "cache_read_tokens_per_line": per_line(seat.cache_read_tokens, lines),
        "wall_time_s_per_line": per_line(seat.wall_time_s, lines),
        "tool_calls_per_line": per_line(seat.tool_calls, lines),
        "cost_usd_per_line": per_line(seat.cost_usd, lines),
        "files": [
            {
                "path": path,
                "lines": count,
                "cache_read_tokens_per_line": per_line(seat.cache_read_tokens, float(count)),
                "wall_time_s_per_line": per_line(seat.wall_time_s, float(count)),
                "tool_calls_per_line": per_line(seat.tool_calls, float(count)),
                "cost_usd_per_line": per_line(seat.cost_usd, float(count)),
            }
            for path, count in sorted(seat.files.items())
        ],
    }


# ....................... #


def _fold_gates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per gate, the wall time it spent and the convictions it produced —
    the pair that says whether a gate is worth what it costs to run
    (S-0065/D-1). Counts and seconds only: the gate runs themselves are the
    renderers' rows."""

    runs: dict[str, dict[str, float]] = {}

    for row in rows:
        results: Any = row.get("results")

        if not isinstance(results, list):
            continue

        for entry in cast("list[object]", results):
            if not isinstance(entry, dict):
                continue

            result = cast("dict[str, Any]", entry)
            gate = runs.setdefault(
                str(result.get("name", "?")), {"runs": 0.0, "wall_time_s": 0.0, "convictions": 0.0}
            )

            gate["runs"] += 1
            gate["wall_time_s"] += _number(result.get("duration_s")) or 0.0

            if (
                str(result.get("outcome", "")) in CONVICTION_OUTCOMES
                and str(result.get("state", "")) in CONVICTION_STATES
            ):
                gate["convictions"] += 1

    return sorted(
        (
            {
                "gate": name,
                "runs": int(gate["runs"]),
                "wall_time_s": round(gate["wall_time_s"], 3),
                "convictions": int(gate["convictions"]),
                "seconds_per_conviction": _ratio(gate["wall_time_s"], gate["convictions"]),
            }
            for name, gate in runs.items()
        ),
        key=lambda entry: (-cast("int", entry["convictions"]), cast("str", entry["gate"])),
    )


# ....................... #


def _fold_contracts(root: Path, rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many counted attempts can be joined to what they were agreed
    against, and from where (S-0065/D-3). The count is the honesty statement the
    join is worth: a record whose contracts have left the tree is not a
    record that lost them."""

    tally = dict.fromkeys(CONTRACT_SOURCES, 0)
    cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    for row in rows:
        task_id = str(row.get("task_id") or "")

        if not task_id:
            tally["absent"] += 1
            continue

        # Either sha joins; `merge_base` is what the attempt built on and is
        # preferred, `head` is the fallback for a row that recorded only one.
        sha = str(row.get("merge_base") or row.get("head") or "")
        found = contract_at(root, task_id, sha, cache)

        if found is None:
            tally["absent"] += 1
        elif found["found_in_tree"]:
            tally["tree"] += 1
        else:
            tally["history"] += 1

    return tally


# ....................... #


def ledger_report(root: Path, spec_dir: Path | None = None) -> dict[str, Any]:
    """The record folded into rates, per seat and per gate (S-0065/D-1).

    A landing is a landing file in the tree, read through the reader
    S-0059/D-12 made the one reader of a landing — the carrier that answers in
    a clone with no event store, which is what makes a rate reproducible
    from a checkout (S-0065/D-7, decided here and logged).
    """

    rows, excluded = counted_rows(stream_rows(root))
    landed = shipped_ids(root, spec_dir)
    seats = _fold_seats(rows)
    # The work-shaped denominators (S-0075/D-3), read from the diffs the
    # landings already commit — the same carrier S-0059/D-12 makes the one
    # reader of a landing, sized between the attempt's own base and head.
    _attach_diff_lines(seats, _task_diffs(root, rows), rows, landed)

    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "attempts": len(rows),
        "landed_tasks": len(landed),
        "excluded": excluded,
        "contracts": _fold_contracts(root, rows),
        "seats": sorted(
            (_seat_entry(seat, landed) for seat in seats.values()),
            key=lambda entry: (cast("str", entry["tier"]), cast("str", entry["image"])),
        ),
        "gates": _fold_gates(rows),
    }


# ....................... #


def ledger_json(root: Path, spec_dir: Path | None = None) -> str:
    return json.dumps(ledger_report(root, spec_dir), indent=2, sort_keys=True)
