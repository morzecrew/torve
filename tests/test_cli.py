from __future__ import annotations

import json

from click.testing import CliRunner

from torve.cli import main
from torve.gates.sabotage import base_task, log_document
from torve.sizing import StaticThresholds


def test_gates_run_end_to_end(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("src/app.py", "print('cli')\n")
    repo.commit("change")

    result = CliRunner().invoke(
        main, ["gates", "run", "--root", str(repo.root), "--base", "main", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    outcomes = {r["name"]: r["outcome"] for r in record["results"]}
    assert outcomes["scope"] == "pass"
    assert outcomes["acceptance"] == "skipped"  # task declares no acceptance commands
    assert (repo.root / ".torve" / "telemetry.jsonl").is_file()


def test_gates_run_exit_code_is_the_outcome(repo):
    repo.seed()
    repo.write("secret.txt", "key: " + "AKIA" + "IOSFODNN7EXAMPLE" + "\n")
    repo.commit("leak")
    result = CliRunner().invoke(
        main, ["gates", "run", "--root", str(repo.root), "--base", "main", "--only", "secrets"]
    )
    assert result.exit_code == 1
    assert "secrets" in result.output


def test_malformed_manifest_exits_3(repo):
    # D-13.6: a bad file is a configuration error, distinct from red gates.
    repo.seed()
    (repo.root / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\nsope: {}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(main, ["gates", "run", "--root", str(repo.root), "--base", "main"])
    assert result.exit_code == 3
    assert "configuration error" in result.output


def test_size_estimate():
    verdict = StaticThresholds().estimate(base_task_model())
    assert verdict.size == "ok"


def base_task_model():
    from torve.models import Task

    return Task.model_validate(base_task(allow=["src/**"]) | {"acceptance": ["true"]})
