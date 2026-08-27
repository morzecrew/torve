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
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.text import Text

from torve.cli.console import STYLE_FAIL, Format, emit_json, mark, out
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


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

    except Exception as error:  # an unusable runtime is the finding
        return [("images", False, f"runtime unavailable: {error}")]

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
