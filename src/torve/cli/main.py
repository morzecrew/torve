"""`torve` — Typer app assembly (S-0011, S-0011/D-1; S-0015/target-tree).

    torve gates run --base origin/main       # all gates
    torve gates run --only scope,acceptance
    torve gates list                         # what the battery will run
    torve gates check                        # the sabotage suite
    torve size .torve/tasks/T-0002.yaml

Commands live one file per verb group (S-0015/D-6) and register here; the shared
plumbing is `torve.cli.console` and `torve.cli.options`.

References: S-0013/A-3.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

import torve
from torve.cli import (
    brief,
    console,
    context,
    decisions,
    doctor,
    equip,
    evals,
    feedback,
    fleet,
    gates,
    init,
    intake,
    ledger,
    log,
    manager,
    mcp,
    merge,
    migrate,
    night,
    plan,
    review,
    run,
    sandbox,
    serve,
    shadow,
    sources,
    spec,
    status,
    survey,
    why,
)
from torve.cli.options import load_dotenv
from torve.domain.states import EXIT_OK

# ----------------------- #

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Deterministic gates for agent and human pull requests.",
)
gates_app = typer.Typer(no_args_is_help=True, help="Run or verify the gate set.")
app.add_typer(gates_app, name="gates")
app.add_typer(sandbox.sandbox_app, name="sandbox")
app.add_typer(equip.equip_app, name="equip")
app.add_typer(review.review_app, name="review")
app.add_typer(fleet.fleet_app, name="fleet")
app.add_typer(log.log_app, name="log")
app.add_typer(manager.manager_app, name="manager")
app.add_typer(night.night_app, name="night")
app.add_typer(decisions.decisions_app, name="decisions")
app.add_typer(spec.spec_app, name="spec")
app.add_typer(sources.source_app, name="source")
app.command("init")(init.init_cmd)


# ....................... #


def _version(value: bool) -> None:
    if value:
        sys.stdout.write(torve.__version__ + "\n")
        raise typer.Exit(EXIT_OK)


# ....................... #


@app.callback()
def root_options(
    plain: Annotated[
        bool,
        typer.Option(
            "--plain",
            help="No colour, spinners or live redraw; implied by CI, "
            "a non-TTY stdout, or --format json.",
        ),
    ] = False,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    console.set_plain(plain)


# ....................... #

gates_app.command("run")(gates.gates_run)
gates_app.command("list")(gates.gates_list)
gates_app.command("check")(gates.gates_check)
app.command("size")(gates.size)
app.command("plan")(plan.plan_cmd)
app.command("intake")(intake.intake_cmd)
app.command("decompose")(intake.decompose_cmd)
app.command("adopt")(intake.adopt_cmd)
app.command("lint-contract")(intake.lint_contract_cmd)
app.command("brief")(brief.brief_cmd)
app.command("context")(context.context_cmd)
app.command("run")(run.run_cmd)
app.command("shadow")(shadow.shadow_cmd)
app.command("survey")(survey.survey_cmd)
app.command("serve")(serve.serve_cmd)
app.command("eval")(evals.eval_cmd)
app.command("cancel")(run.cancel)
app.command("kill")(run.kill)
app.command("mcp")(mcp.mcp_cmd)
app.command("merge")(merge.merge_cmd)
app.command("approve")(merge.approve_cmd)
app.command("migrate")(migrate.migrate_cmd)
app.command("doctor")(doctor.doctor)
app.command("feedback")(feedback.feedback)
app.command("status")(status.status)
app.command("reap")(status.reap_cmd)
app.command("why")(why.why_cmd)
app.command("ledger")(ledger.ledger_cmd)


# ....................... #


def main() -> None:
    """The console script's entry, and the one place `.env` is read.

    Here rather than in the callback on purpose: this runs for a
    person at a terminal, and a test driving the same app through
    `CliRunner` gets the environment the test set and not the operator's
    own secrets. A name the environment already carries always wins over
    the file either way.
    """

    load_dotenv()
    app()


# ....................... #

if __name__ == "__main__":
    main()
