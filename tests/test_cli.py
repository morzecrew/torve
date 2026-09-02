from __future__ import annotations

import json

from typer.testing import CliRunner

from torve.application import sizing
from torve.cli import app
from torve.gates.sabotage import TASK_ID, base_task, log_document


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


def _doctor_repo(tmp_path, config):
    import yaml

    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, **config}), encoding="utf-8"
    )
    return root


def test_doctor_names_the_mock_store_as_test_only(tmp_path):
    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "opensandbox"}})
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["store"]["ok"] is True
    assert "test-only" in checks["store"]["detail"]


def test_doctor_reds_on_a_postgres_store_with_no_dsn(tmp_path, monkeypatch):
    monkeypatch.delenv("TORVE_PG_DSN", raising=False)
    root = _doctor_repo(
        tmp_path, {"runtime": {"adapter": "opensandbox"}, "store": {"adapter": "postgres"}}
    )
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    assert result.exit_code == 3
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["store"]["ok"] is False
    assert "TORVE_PG_DSN" in checks["store"]["detail"]


def test_doctor_reds_on_a_postgres_store_that_does_not_answer(tmp_path, monkeypatch):
    # A DSN pointing nowhere: the unreachable database is the finding, with
    # an instruction, not a traceback.
    monkeypatch.setenv(
        "TORVE_PG_DSN", "postgresql://nobody:nothing@127.0.0.1:1/none?connect_timeout=1"
    )
    root = _doctor_repo(
        tmp_path, {"runtime": {"adapter": "opensandbox"}, "store": {"adapter": "postgres"}}
    )
    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    assert result.exit_code == 3
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["store"]["ok"] is False
    assert "did not answer" in checks["store"]["detail"]


def _fake_docker_runtime(digest):
    class _FakeRuntime:
        def resolve_image(self, image: str) -> str | None:
            return digest

    return lambda config, override: _FakeRuntime()


def test_doctor_names_an_eval_ledger_verdict_for_the_configured_digest(tmp_path, monkeypatch):
    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "docker"}})
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr("torve.cli.options.runtime_for", _fake_docker_runtime(digest))

    ledger = root / ".torve" / "evals.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "kind": "config-eval",
                "at": "2026-08-20T11:04:12Z",
                "digests": {"incumbent": digest, "candidate": "sha256:" + "cd" * 32},
                "candidate_matched": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image python:3.13-slim"]
    assert image_check["ok"] is True
    assert "incumbent verdict from 2026-08-20T11:04:12Z" in image_check["detail"]
    assert "candidate_matched=True" in image_check["detail"]


def test_doctor_image_check_is_unchanged_with_no_eval_ledger(tmp_path, monkeypatch):
    root = _doctor_repo(tmp_path, {"runtime": {"adapter": "docker"}})
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr("torve.cli.options.runtime_for", _fake_docker_runtime(digest))

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image python:3.13-slim"]
    assert image_check["ok"] is True
    assert image_check["detail"] == f"python:3.13-slim = {digest[:19]}"


def test_doctor_prints_the_registry_digest_for_a_remote_reference(tmp_path, monkeypatch):
    # RFC 0033's phase-3 line: a registry reference the runtime cannot
    # resolve (an unpulled remote image) prints the digest the registry
    # itself resolves — the same line a local image already gets.
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "docker", "image": "ghcr.io/morzecrew/torve-agent:0.1.1"}},
    )
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr("torve.cli.options.runtime_for", _fake_docker_runtime(None))
    monkeypatch.setattr("torve.cli.doctor._registry_digest", lambda image: digest)

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image ghcr.io/morzecrew/torve-agent:0.1.1"]
    assert image_check["ok"] is True
    assert image_check["detail"] == f"ghcr.io/morzecrew/torve-agent:0.1.1 = {digest[:19]}"


def test_doctor_does_not_ask_the_registry_when_the_runtime_resolves(tmp_path, monkeypatch):
    # The runtime's answer is authoritative and local — the registry leg
    # only speaks when the runtime cannot.
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "docker", "image": "ghcr.io/morzecrew/torve-agent:0.1.1"}},
    )
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr("torve.cli.options.runtime_for", _fake_docker_runtime(digest))
    asked: list[str] = []
    monkeypatch.setattr(
        "torve.cli.doctor._registry_digest", lambda image: asked.append(image) or digest
    )

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["image ghcr.io/morzecrew/torve-agent:0.1.1"]["ok"] is True
    assert asked == []


def test_doctor_keeps_the_runtime_red_when_the_registry_cannot_resolve(tmp_path, monkeypatch):
    # No new check: an unpulled reference the registry cannot answer keeps
    # the existing docker red, with the same words.
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "docker", "image": "ghcr.io/morzecrew/torve-agent:0.1.1"}},
    )
    monkeypatch.setattr("torve.cli.options.runtime_for", _fake_docker_runtime(None))
    monkeypatch.setattr("torve.cli.doctor._registry_digest", lambda image: None)

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image ghcr.io/morzecrew/torve-agent:0.1.1"]
    assert image_check["ok"] is False
    assert image_check["detail"] == (
        "ghcr.io/morzecrew/torve-agent:0.1.1: not present in the runtime — "
        "build it (torve sandbox build) or pull it"
    )


def test_doctor_prints_the_registry_digest_under_opensandbox(tmp_path, monkeypatch):
    # The digest rule is runtime-independent (RFC 0017 §2): under the
    # opensandbox runtime — whose server pulls from a registry — a
    # registry reference gets the same line, resolved from the registry.
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "opensandbox", "image": "ghcr.io/morzecrew/torve-agent:0.1.1"}},
    )
    digest = "sha256:" + "ab" * 32
    monkeypatch.setattr("torve.cli.doctor._registry_digest", lambda image: digest)

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    image_check = checks["image ghcr.io/morzecrew/torve-agent:0.1.1"]
    assert image_check["ok"] is True
    assert image_check["detail"] == f"ghcr.io/morzecrew/torve-agent:0.1.1 = {digest[:19]}"


def test_doctor_opensandbox_stays_silent_when_the_registry_cannot_resolve(tmp_path, monkeypatch):
    # No new failure mode under opensandbox: an unresolved reference gets
    # no image line at all, never a red doctor.
    root = _doctor_repo(
        tmp_path,
        {"runtime": {"adapter": "opensandbox", "image": "ghcr.io/morzecrew/torve-agent:0.1.1"}},
    )
    monkeypatch.setattr("torve.cli.doctor._registry_digest", lambda image: None)

    result = CliRunner().invoke(app, ["doctor", "--root", str(root), "--format", "json"])
    names = [c["name"] for c in json.loads(result.stdout)["checks"]]
    assert not any(name.startswith("image ") for name in names)


def test_registry_digest_only_queries_explicit_registry_hosts(monkeypatch):
    from torve.cli.doctor import _registry_digest

    asked: list[tuple[str, str, str]] = []

    def fake_manifest(host: str, repository: str, reference: str) -> str:
        asked.append((host, repository, reference))
        return "sha256:" + "ab" * 32

    monkeypatch.setattr("torve.cli.doctor._registry_manifest_digest", fake_manifest)
    digest = "sha256:" + "ab" * 32

    # A host-less tag is not a registry reference — the runtime's business.
    assert _registry_digest("python:3.13-slim") is None
    assert _registry_digest("morzecrew/torve-agent:0.1.1") is None
    assert _registry_digest("ghcr.io/") is None
    assert asked == []

    assert _registry_digest("ghcr.io/morzecrew/torve-agent:0.1.1") == digest
    assert asked[-1] == ("ghcr.io", "morzecrew/torve-agent", "0.1.1")
    assert _registry_digest("ghcr.io/morzecrew/torve-agent") == digest
    assert asked[-1] == ("ghcr.io", "morzecrew/torve-agent", "latest")
    assert _registry_digest("ghcr.io/morzecrew/torve-agent@sha256:abcd") == digest
    assert asked[-1] == ("ghcr.io", "morzecrew/torve-agent", "sha256:abcd")
    # A digest pin wins over the tag it rides with.
    assert _registry_digest("ghcr.io/morzecrew/torve-agent:0.1.1@sha256:abcd") == digest
    assert asked[-1] == ("ghcr.io", "morzecrew/torve-agent", "sha256:abcd")
    # docker.io is an accepted alias for Docker Hub's registry.
    assert _registry_digest("docker.io/library/python:3.13") == digest
    assert asked[-1] == ("registry-1.docker.io", "library/python", "3.13")


def test_run_missing_contract_is_a_config_error(tmp_path):
    result = CliRunner().invoke(app, ["run", "T-0000", "--root", str(tmp_path)])
    assert result.exit_code == 3
    assert "configuration error" in result.stderr


def test_size_estimate():
    verdict = sizing.estimate(base_task_model())
    assert verdict.size == "ok"


def test_run_blocked_awaiting_decomposition_without_override(repo):
    # RFC 0026 D-26.7: a too_large contract awaits decomposition — dispatch
    # refuses it by name unless the operator overrides explicitly.
    repo.seed()
    repo.task(base_task(allow=["src/a/**", "docs/a/**"]), None)
    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert result.exit_code == 3
    assert "awaiting decomposition" in result.stderr
    assert "--oversize" in result.stderr


def test_run_oversize_override_dispatches_and_is_recorded(repo):
    # The override bypasses the block and is recorded on the run (D-26.7) —
    # asserted from telemetry alone, independent of whatever the dispatched
    # attempt itself goes on to do.
    repo.seed()
    repo.task(base_task(allow=["src/a/**", "docs/a/**"]), None)
    CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root), "--oversize"])
    events = [
        json.loads(line)
        for line in (repo.root / ".torve" / "telemetry.jsonl").read_text().splitlines()
        if line.strip()
    ]
    recorded = [e for e in events if e.get("event") == "oversize_dispatch"]
    assert recorded and recorded[0]["task"] == TASK_ID


def base_task_model():
    from torve.domain.task import Task

    return Task.model_validate(base_task(allow=["src/**"]) | {"acceptance": ["true"]})


def test_doctor_warns_when_the_reviewer_shares_the_executors_model(tmp_path):
    """D-5.1's bias warning: legal, warned, never refused."""
    from torve.cli.doctor import _review_bias_check

    (tmp_path / ".torve").mkdir()
    (tmp_path / ".torve" / "config.yaml").write_text(
        "schema_version: 1\n"
        'review: {"on": [task_gated]}\n'
        "tiers:\n"
        "  planner: {adapter: fake}\n"
        "  executor: {adapter: harness, command: c, provider: p, model: m-1}\n"
        "  reviewer: {adapter: harness, command: c, provider: p, model: m-1}\n",
        encoding="utf-8",
    )
    checks = _review_bias_check(tmp_path, None)
    assert len(checks) == 1 and checks[0][1] is True
    assert "own model" in checks[0][2]

    (tmp_path / ".torve" / "config.yaml").write_text(
        "schema_version: 1\n"
        'review: {"on": [task_gated]}\n'
        "tiers:\n"
        "  planner: {adapter: fake}\n"
        "  executor: {adapter: harness, command: c, provider: p, model: m-1}\n"
        "  reviewer: {adapter: harness, command: c, provider: q, model: m-2}\n",
        encoding="utf-8",
    )
    assert _review_bias_check(tmp_path, None) == []


# ....................... #
# Retry rungs join the dispatch-time provider check on every axis
# (D-34.6, D-4.8): a repository's denial must reach the surface that reads
# the full mapping, not only the scalar's functional mirror.


def _rung_routing_config(rungs: str, providers: str) -> str:
    return (
        "schema_version: 1\n"
        "tiers:\n"
        f"  executor: {{retry_variants: {{{rungs}}}}}\n"
        "  executor.deep: {adapter: harness, command: c, provider: deepseek, model: m}\n"
        f"{providers}\n"
    )


def test_run_refuses_a_provider_only_a_compliance_rung_needs_with_exit_3(repo):
    """No retry may run under a provider the repository denies — and the
    rung being keyed on an axis other than the scalar's does not hide it
    from the dispatch check."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    repo.write(
        ".torve/config.yaml",
        _rung_routing_config("compliance: executor.deep", "providers: {default: []}"),
    )

    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert result.exit_code == 3
    assert "not permitted" in result.stderr
    assert "deepseek" in result.stderr


def test_run_dispatches_when_a_nonfunctional_rungs_provider_is_allowed(repo):
    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    repo.write(
        ".torve/config.yaml",
        _rung_routing_config(
            "compliance: executor.deep", "providers: {default: [deepseek]}"
        ),
    )

    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert "not permitted" not in result.stderr + result.output


def test_run_refuses_a_form_rung_the_repository_denies(repo):
    """Every axis of the map, not a chosen few: the form rung's provider is
    checked exactly like the functional one's."""
    repo.seed()
    repo.task(base_task(allow=["src/**"]), None)
    repo.write(
        ".torve/config.yaml",
        _rung_routing_config("functional: executor, form: executor.deep", "providers: {default: []}"),
    )

    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert result.exit_code == 3
    assert "not permitted" in result.stderr
