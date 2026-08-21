from __future__ import annotations

import json

from typer.testing import CliRunner

from torve.cli import app
from torve.gates.sabotage import base_task, log_document
from torve.sizing import StaticThresholds


def test_gates_run_end_to_end(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("src/app.py", "print('cli')\n")
    repo.commit("change")

    result = CliRunner().invoke(
        app, ["gates", "run", "--root", str(repo.root), "--base", "main", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    outcomes = {r["name"]: r["outcome"] for r in record["results"]}
    assert outcomes["scope"] == "pass"
    assert outcomes["acceptance"] == "skipped"  # task declares no acceptance commands
    assert (repo.root / ".torve" / "telemetry.jsonl").is_file()


def test_gates_run_exit_code_is_the_outcome(repo):
    repo.seed()
    repo.write("secret.txt", "key: " + "AKIA" + "IOSFODNN7EXAMPLE" + "\n")
    repo.commit("leak")
    result = CliRunner().invoke(
        app, ["gates", "run", "--root", str(repo.root), "--base", "main", "--only", "secrets"]
    )
    assert result.exit_code == 1
    assert "secrets" in result.output


def test_malformed_manifest_exits_3(repo):
    # D-13.6: a bad file is a configuration error, distinct from red gates.
    repo.seed()
    (repo.root / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\nsope: {}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["gates", "run", "--root", str(repo.root), "--base", "main"])
    assert result.exit_code == 3
    assert "configuration error" in result.stderr


def test_json_is_exactly_one_document_on_stdout(repo):
    # D-11.6: machine output is one JSON document, diagnostics never mix in.
    repo.seed()
    repo.write("src/app.py", "print('json')\n")
    repo.commit("change")
    result = CliRunner().invoke(
        app, ["gates", "run", "--root", str(repo.root), "--base", "main", "--format", "json"]
    )
    json.loads(result.stdout)  # would raise on any stray line


def test_gates_check_json_is_schema_versioned():
    result = CliRunner().invoke(app, ["gates", "check", "--format", "json"])
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    assert result.exit_code == 0, document["cases"]


def test_size_json(tmp_path):
    task_file = tmp_path / "task.yaml"
    import yaml

    task_file.write_text(yaml.safe_dump(base_task(allow=["src/**"])))
    result = CliRunner().invoke(app, ["size", str(task_file), "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["size"] == "ok"


def test_status_json_carries_persisted_records(tmp_path):
    result = CliRunner().invoke(app, ["status", "--root", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"schema_version": 1, "runs": []}


def test_doctor_json_and_exit():
    result = CliRunner().invoke(app, ["doctor", "--format", "json"])
    document = json.loads(result.stdout)
    assert document["checks"][0]["name"] == "forze-pin"
    assert result.exit_code in (0, 3)  # 3 = configuration error, never 1


def test_run_missing_contract_is_a_config_error(tmp_path):
    result = CliRunner().invoke(app, ["run", "T-0000", "--root", str(tmp_path)])
    assert result.exit_code == 3
    assert "configuration error" in result.stderr


def test_size_estimate():
    verdict = StaticThresholds().estimate(base_task_model())
    assert verdict.size == "ok"


def base_task_model():
    from torve.models import Task

    return Task.model_validate(base_task(allow=["src/**"]) | {"acceptance": ["true"]})
