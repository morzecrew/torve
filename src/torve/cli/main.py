"""`torve` — Typer app assembly (RFC 0011, D-11.1; RFC 0015 §3).

    torve gates run --base origin/main       # all gates
    torve gates run --only scope,acceptance
    torve gates check                        # the sabotage suite
    torve size .torve/tasks/T-0002.yaml

Commands live one file per verb group (D-15.6) and register here; the shared
plumbing is `torve.cli.console` and `torve.cli.options`.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

import torve
from torve.cli import console, doctor, feedback, gates, migrate, plan, rfc, run, shadow, status
from torve.domain.states import EXIT_OK

# ----------------------- #

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Deterministic gates for agent and human pull requests.")
gates_app = typer.Typer(no_args_is_help=True, help="Run or verify the gate set.")
app.add_typer(gates_app, name="gates")
app.add_typer(rfc.rfc_app, name="rfc")


def _version(value: bool) -> None:
    if value:
        sys.stdout.write(torve.__version__ + "\n")
        raise typer.Exit(EXIT_OK)


@app.callback()
def root_options(
    plain: Annotated[bool, typer.Option(
        "--plain", help="No colour, spinners or live redraw; implied by CI, "
                        "a non-TTY stdout, or --format json.")] = False,
    version: Annotated[bool, typer.Option(
        "--version", callback=_version, is_eager=True,
        help="Print the version and exit.")] = False,
) -> None:
    console.set_plain(plain)


gates_app.command("run")(gates.gates_run)
gates_app.command("check")(gates.gates_check)
app.command("size")(gates.size)
app.command("plan")(plan.plan_cmd)
app.command("run")(run.run_cmd)
app.command("shadow")(shadow.shadow_cmd)
app.command("cancel")(run.cancel)
app.command("migrate")(migrate.migrate_cmd)
app.command("doctor")(doctor.doctor)
app.command("feedback")(feedback.feedback)
app.command("status")(status.status)
app.command("reap")(status.reap_cmd)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
