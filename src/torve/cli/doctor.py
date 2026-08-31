"""`torve doctor` — preflight checks, rendered per RFC 0011 §5: each check
names what it looked for, what it found, and what to do about it. The forze
schema pin is D-12.7: a mismatch must be a check, not a symptom. Image
existence is D-17.2: a configured image the runtime cannot resolve, or a
torve-agent image with no definition directory, is a configuration error
before it becomes a mid-run surprise. The store check is D-3.6 made
operational: a postgres store must name a DSN, answer a connection, and
carry every substrate step (D-12.7's sibling question) before a run
depends on it — and a mock store states plainly that it is the
in-process, test-only regime.

The broker check is RFC 0021 D-21.9: the adapter in force is named, and the
`none` adapter — legal and the phase-1 default — is stated plainly to leave
the credential-custody requirement (D-4b) unmet, so that opting out is a
decision someone can be shown making, never a silent default.

D-27.10 (OPEN, decided here): rather than have a sandbox definition carry a
pointer to the verdict that installed it — a write to `.torve/sandbox/**`
out of this task's scope — doctor reads the eval ledger directly and
matches on the digest it already resolved. Read-only, no new record shape.

The profile check is RFC 0028 D-28.7: each tier that resolved through a
profile (`TierConfig.profile`, set by `load_runner_config`'s raw-mapping
merge) gets one provenance line naming it — no check attached, so this can
never turn doctor red, and a tier or profile file nobody referenced gets
no line and no warning. A-74: a tier composed from a list of profiles
carries its chain, in order, in that same field and line.

The equipment check is RFC 0029 D-29.5: each tier whose resolved `skills`
or `prompt_extras` differ from its role default gets one provenance line —
no check attached, dispatch already owns the refusals (D-29.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import typer
from rich.text import Text

from torve.cli.console import STYLE_FAIL, Format, emit_json, mark, out
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


def _config_eval_verdict(root: Path, digest: str) -> dict[str, Any] | None:
    """The eval ledger's most recent config-eval record citing `digest` as
    either arm — the same digest a paired replay (D-27.7) measured, whether
    it won or lost. `None` when the ledger has no such record: an unmeasured
    digest is not a finding, just a fact doctor cannot add to."""

    from torve.application.evals import EVAL_LEDGER

    path = root / layout.TORVE_DIR / EVAL_LEDGER

    if not path.is_file():
        return None

    found: dict[str, Any] | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)

        if row.get("kind") != "config-eval":
            continue

        if digest in row.get("digests", {}).values():
            found = row  # append-only ledger — the latest citation wins

    return found


def _image_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    from torve.cli.options import runtime_for
    from torve.cli.sandbox import definitions_root
    from torve.config.runconfig import configured_images

    config = load_config(root, config_path)
    checks: list[tuple[str, bool, str]] = []

    if config.runtime.adapter != "docker":
        return checks

    try:
        runtime = runtime_for(config, None)

        for image in configured_images(config):
            digest = runtime.resolve_image(image)

            if digest is None:
                checks.append(
                    (
                        f"image {image}",
                        False,
                        (
                            f"{image}: not present in the runtime — build it "
                            "(torve sandbox build) or pull it"
                        ),
                    )
                )

                continue

            detail = f"{image} = {digest[:19]}"
            verdict = _config_eval_verdict(root, digest)

            if verdict is not None:
                role = "incumbent" if verdict["digests"].get("incumbent") == digest else "candidate"
                detail += (
                    f" — eval ledger holds a {role} verdict from {verdict['at']} "
                    f"(candidate_matched={verdict['candidate_matched']})"
                )

            prefix = "torve-agent:"

            if image.startswith(prefix):
                name = image.removeprefix(prefix)

                if not (definitions_root(root) / name / "Dockerfile").is_file():
                    checks.append(
                        (
                            f"image {image}",
                            False,
                            (
                                f"{image}: exists but has no definition under "
                                f"{definitions_root(root) / name} — an image without "
                                "a reviewed definition is an ambient regime"
                            ),
                        )
                    )

                    continue

                detail += " (definition present)"

            checks.append((f"image {image}", True, detail))

    except Exception as error:  # an unusable runtime is the finding
        return [("images", False, f"runtime unavailable: {error}")]

    return checks


# ....................... #


def _store_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    from torve.adapters.store.durable import resolve_dsn
    from torve.application.migrate import MigrateError, pending_count

    config = load_config(root, config_path)

    if config.store.adapter != "postgres":
        return [
            (
                "store",
                True,
                (
                    f"store: {config.store.adapter} — in-process and test-only; "
                    "real runs take a postgres store"
                ),
            )
        ]

    try:
        dsn = resolve_dsn(config.store)

    except RuntimeError as error:
        return [("store", False, str(error))]

    try:
        pending = pending_count("substrate", dsn)

    except MigrateError as error:
        return [("store", False, str(error))]

    except Exception as error:  # the unreachable database is the finding
        return [
            (
                "store",
                False,
                (
                    f"store: postgres named by ${config.store.dsn_env} did not "
                    f"answer: {error} — start the database or fix the DSN"
                ),
            )
        ]

    if pending:
        return [
            (
                "store",
                False,
                (
                    f"store: postgres reachable but {pending} substrate "
                    "step(s) pending — run: torve migrate substrate"
                ),
            )
        ]

    return [("store", True, "store: postgres reachable, substrate schema current")]


# ....................... #


def _broker_check(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """D-21.9: the broker adapter in force is named, and `none` — legal and
    the phase-1 default — is stated plainly to leave the credential-custody
    requirement unmet. What is not legal is `none` by accident."""

    config = load_config(root, config_path)

    if config.broker.adapter == "none":
        return [
            (
                "broker",
                True,
                (
                    "broker: none — provider keys pass through to the sandbox, with "
                    "no metering and no wire routing; this is the default and it "
                    "leaves the credential-custody requirement unmet. Configure "
                    "broker.adapter: local to close it."
                ),
            )
        ]

    routed = ", ".join(sorted(config.broker.providers)) or "none routed"

    return [
        (
            "broker",
            True,
            (
                f"broker: {config.broker.adapter} ({config.broker.mode}) — the runner "
                f"holds the keys and serves one loopback route per routed provider "
                f"({routed}); the sandbox holds none"
            ),
        )
    ]


# ....................... #


def _review_bias_check(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """A reviewer sharing the executor's model reviews its own kind —
    models are biased toward output that looks like theirs, and D-5.1's
    cross-model recommendation exists for exactly this. A warning, never a
    refusal: same-model review is legal and still better than none."""

    config = load_config(root, config_path)

    if not config.review.on:
        return []

    executor = config.tiers.get("executor")
    reviewer = config.tiers.get("reviewer")

    if executor is None or reviewer is None:
        return []

    if executor.adapter == "fake" or reviewer.adapter == "fake":
        return []

    if executor.model and executor.model == reviewer.model:
        return [
            (
                "review",
                True,
                (
                    f"reviewer runs the executor's own model ({executor.model}) — "
                    "a model reviewing its own kind shares its blind spots; "
                    "cross-model review (a different vendor or model on the "
                    "reviewer tier) is the recommended regime"
                ),
            )
        ]

    return []


def _profile_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """D-28.7: provenance only — a resolved profile is named per tier, and no
    check is attached, so this can never turn doctor red. A tier that names
    no profile, or an unreferenced profile file, gets no line at all. A-74:
    `tier.profile` already carries a composed tier's chain in order
    (`"a -> b"`), so the same line renders it with no extra formatting."""

    config = load_config(root, config_path)

    return [
        (
            f"profile {name}",
            True,
            f"tier {name}: adapter, model and command resolved from profile '{tier.profile}'",
        )
        for name, tier in sorted(config.tiers.items(), key=lambda item: item[0])
        if tier.profile
    ]


# ....................... #


def _equipment_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """RFC 0029 D-29.5: provenance only — a tier's resolved equipment is named
    when it differs from its role default (`skills` set, or any
    `prompt_extras`), and no check is attached, so this can never turn doctor
    red. A tier that inherits its role's default set and carries no extras
    gets no line."""

    config = load_config(root, config_path)
    checks: list[tuple[str, bool, str]] = []

    for name, tier in sorted(config.tiers.items()):
        parts: list[str] = []

        if tier.skills is not None:
            parts.append(f"skills [{', '.join(tier.skills)}] (override)")

        if tier.prompt_extras:
            n = len(tier.prompt_extras)
            parts.append(f"+{n} prompt extra{'s' if n != 1 else ''}")

        if not parts:
            continue

        checks.append((f"equipment {name}", True, f"tier {name}: {', '.join(parts)}"))

    return checks


# ....................... #


def doctor(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Preflight checks: configuration and environment readiness — the forze
    schema pin, the run store (a postgres store must name a DSN, answer, and
    be migrated), and every configured sandbox image resolvable in the
    runtime with its definition present. A failed check is a configuration
    error (exit 3), not a red gate."""

    from torve.application.migrate import check_forze_pin

    root = root.resolve()
    ok, message = check_forze_pin()
    checks: list[tuple[str, bool, str]] = [("forze-pin", ok, message)]
    checks += _store_checks(root, config_path)
    checks += _broker_check(root, config_path)
    checks += _review_bias_check(root, config_path)
    checks += _profile_checks(root, config_path)
    checks += _equipment_checks(root, config_path)
    checks += _image_checks(root, config_path)
    healthy = all(passed for _, passed, _ in checks)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "ok": healthy,
                "checks": [
                    {"name": name, "ok": passed, "detail": detail}
                    for name, passed, detail in checks
                ],
            }
        )
    else:
        console = out(fmt)

        for _name, passed, detail in checks:
            verdict = mark("pass" if passed else "fail")
            console.print(verdict + Text(f" {detail}", "" if passed else STYLE_FAIL))

    raise typer.Exit(EXIT_OK if healthy else EXIT_CONFIG)
