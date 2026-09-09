from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_context import seed_why_facts
from test_plan import plan_repo  # noqa: F401  (fixture)
from typer.testing import CliRunner

from torve.application import sizing
from torve.application.projections import why_report
from torve.cli import app
from torve.cli import sandbox as sandbox_cli
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
    # S-0013/D-6: a bad file is a configuration error, distinct from red gates.
    repo.seed()
    (repo.root / ".torve" / "gates.yaml").write_text(
        "schema_version: 1\nsope: {}\n", encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["gates", "run", "--root", str(repo.root), "--base", "main"])
    assert result.exit_code == 3
    assert "configuration error" in result.stderr


def test_json_is_exactly_one_document_on_stdout(repo):
    # S-0011/D-6: machine output is one JSON document, diagnostics never mix in.
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
    # S-0033's phase-3 line: a registry reference the runtime cannot
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
    # The digest rule is runtime-independent (S-0017/the-image-is-an-input-not-an-environment): under the
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
    # S-0026 S-0026/D-7: a too_large contract awaits decomposition — dispatch
    # refuses it by name unless the operator overrides explicitly.
    repo.seed()
    repo.task(base_task(allow=["src/a/**", "docs/a/**"]), None)
    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert result.exit_code == 3
    assert "awaiting decomposition" in result.stderr
    assert "--oversize" in result.stderr


def test_run_oversize_override_dispatches_and_is_recorded(repo):
    # The override bypasses the block and is recorded on the run (S-0026/D-7) —
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
    """S-0005/D-1's bias warning: legal, warned, never refused."""
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
# (S-0034/D-6, S-0004/D-8): a repository's denial must reach the surface that reads
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
        _rung_routing_config("compliance: executor.deep", "providers: {default: [deepseek]}"),
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
        _rung_routing_config(
            "functional: executor, form: executor.deep", "providers: {default: []}"
        ),
    )

    result = CliRunner().invoke(app, ["run", TASK_ID, "--root", str(repo.root)])
    assert result.exit_code == 3
    assert "not permitted" in result.stderr


# ----------------------- #
# `torve why` — the per-task history renderer. Content asserted, never
# layout; the json format emits the projection's envelope unchanged; the
# exit code reports the read, not history's fortunes.


def test_why_json_emits_the_envelope_verbatim(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)

    result = CliRunner().invoke(app, ["why", "T-0001", "--root", str(root), "--format", "json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == why_report(root, "T-0001")


def test_why_reads_a_red_history_successfully(plan_repo):  # noqa: F811
    """A red history read successfully is a successful read: exit 0
    whatever the history says."""
    root, _, _ = plan_repo
    seed_why_facts(root)

    result = CliRunner().invoke(app, ["why", "T-0001", "--root", str(root)])

    assert result.exit_code == 0, result.output
    for content in (
        "T-0001",
        "gates_red",
        "agent_timeout",
        "decisions-reported",
        "pre-verdict record",
        "poison_ceiling",
        "oversize_dispatch",
        "T-7001",
        "quasi-experiment",
    ):
        assert content in result.output


def test_why_unknown_task_exits_3(plan_repo):  # noqa: F811
    """3 is the configuration family: a typo must not read as a task with
    no history."""
    root, _, _ = plan_repo
    seed_why_facts(root)

    result = CliRunner().invoke(app, ["why", "T-9999", "--root", str(root)])

    assert result.exit_code == 3
    assert "no task T-9999" in result.stderr


def test_why_unknown_task_json_still_emits_its_envelope(plan_repo):  # noqa: F811
    root, _, _ = plan_repo
    seed_why_facts(root)

    result = CliRunner().invoke(app, ["why", "T-9999", "--root", str(root), "--format", "json"])

    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "task": "T-9999",
        "found": False,
    }


def test_why_help_carries_no_corpus_coordinates():
    result = CliRunner().invoke(app, ["why", "--help"])

    assert result.exit_code == 0
    assert "D-40" not in result.output
    assert "RFC" not in result.output.upper()


# ....................... #
# `torve sandbox build --push` — publishing an image and printing its pin


def _push_repo(tmp_path, names):
    root = tmp_path / "repo"

    for name in names:
        definition = root / ".torve" / "sandbox" / name
        definition.mkdir(parents=True)
        definition.joinpath("Dockerfile").write_text("FROM python:3.13-slim\n", encoding="utf-8")

    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    # The project the build stages into every context (A-84): without it the
    # definitions build without the engine's CLI, which is legal but is not
    # what this repository is asserting about.
    (root / "pyproject.toml").write_text("[project]\nname = 'probe'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    return root


class _FakeRuntime:
    """The build is recorded, not executed: the push path's contract is
    that the runtime still builds and the CLI still pushes."""

    def __init__(self):
        self.builds = []

    def build_image(self, context, tag):
        # What the runtime is handed is a staged context, not the definition
        # directory, and it is gone by the time the assertion runs — so what
        # it carried is recorded here, while it exists.
        self.builds.append((sorted(one.name for one in Path(context).iterdir()), tag))
        return "sha256:locallayer"


class _FakeDocker:
    """Records every docker call; the daemon answers a push by recording
    the manifest digest for the reference that was pushed."""

    def __init__(self, fail_on=""):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, *args, timeout):
        self.calls.append(list(args))

        if self.fail_on and args[0] == self.fail_on:
            return subprocess.CompletedProcess(
                list(args), 1, "", "denied: requested access to the resource is denied"
            )

        if args[:2] == ("image", "inspect"):
            repository = args[-1].partition(":")[0]
            return subprocess.CompletedProcess(list(args), 0, f"{repository}@sha256:cafe\n", "")

        return subprocess.CompletedProcess(list(args), 0, "", "")


def _invoke_push(root, *extra):
    return CliRunner().invoke(
        app, ["sandbox", "build", *extra, "--root", str(root), "--format", "json"]
    )


def test_sandbox_build_push_publishes_and_prints_the_pinned_reference(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["probe"])
    runtime, docker = _FakeRuntime(), _FakeDocker()
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: runtime)
    monkeypatch.setattr(sandbox_cli, "_docker", docker)

    result = _invoke_push(root, "probe", "--push", "registry.example.com/org/torve-agent")
    assert result.exit_code == 0, result.output

    image = json.loads(result.stdout)["images"][0]
    assert image["digest"] == "sha256:locallayer"
    # The pin carries the registry's manifest digest — what a pull platform
    # resolves — and the repository, not the tag.
    assert image["pinned"] == "registry.example.com/org/torve-agent@sha256:cafe"
    # The context carries the definition's own files and the project the
    # image installs the engine's CLI from.
    staged, tag = runtime.builds[0]
    assert tag == "torve-agent:probe"
    assert "Dockerfile" in staged
    assert "pyproject.toml" in staged
    assert docker.calls == [
        ["tag", "torve-agent:probe", "registry.example.com/org/torve-agent:probe"],
        ["push", "registry.example.com/org/torve-agent:probe"],
        [
            "image",
            "inspect",
            "--format",
            "{{index .RepoDigests 0}}",
            "registry.example.com/org/torve-agent:probe",
        ],
    ]


def test_sandbox_build_without_push_never_talks_to_the_registry(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["probe"])
    runtime = _FakeRuntime()
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: runtime)

    def never_called(*args, timeout):
        raise AssertionError(f"docker called without --push: {args}")

    monkeypatch.setattr(sandbox_cli, "_docker", never_called)

    result = _invoke_push(root, "probe")
    assert result.exit_code == 0, result.output

    image = json.loads(result.stdout)["images"][0]
    assert image == {"name": "probe", "tag": "torve-agent:probe", "digest": "sha256:locallayer"}


def test_sandbox_build_push_tags_each_definition_by_name(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["agent", "gates"])
    docker = _FakeDocker()
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: _FakeRuntime())
    monkeypatch.setattr(sandbox_cli, "_docker", docker)

    result = _invoke_push(root, "--push", "registry.example.com/org/torve-agent")
    assert result.exit_code == 0, result.output

    images = json.loads(result.stdout)["images"]
    assert [image["pinned"] for image in images] == [
        "registry.example.com/org/torve-agent@sha256:cafe",
        "registry.example.com/org/torve-agent@sha256:cafe",
    ]
    pushed = [call[1] for call in docker.calls if call[0] == "push"]
    assert pushed == [
        "registry.example.com/org/torve-agent:agent",
        "registry.example.com/org/torve-agent:gates",
    ]


def test_sandbox_build_push_refuses_a_reference_that_already_carries_a_tag(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["probe"])
    runtime, docker = _FakeRuntime(), _FakeDocker()
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: runtime)
    monkeypatch.setattr(sandbox_cli, "_docker", docker)

    for bad in ("registry.example.com/org/torve-agent:latest", "registry.example.com/x@sha256:ab"):
        result = _invoke_push(root, "probe", "--push", bad)
        assert result.exit_code == 3, result.output
        assert "not a registry repository" in result.stderr

    # The refusal comes before the build burns anything.
    assert runtime.builds == []
    assert docker.calls == []


def test_sandbox_build_push_failure_is_an_infrastructure_error(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["probe"])
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: _FakeRuntime())
    monkeypatch.setattr(sandbox_cli, "_docker", _FakeDocker(fail_on="push"))

    result = _invoke_push(root, "probe", "--push", "registry.example.com/org/torve-agent")
    assert result.exit_code == 4, result.output
    # The build succeeded; the message names the leg that failed, and the
    # registry's own denial survives verbatim.
    assert "push failed for 'probe'" in result.stderr
    assert "denied: requested access to the resource is denied" in result.stderr


def test_sandbox_build_push_prints_the_pinned_reference_as_text(tmp_path, monkeypatch):
    root = _push_repo(tmp_path, ["probe"])
    monkeypatch.setattr(sandbox_cli, "runtime_for", lambda config, override: _FakeRuntime())
    monkeypatch.setattr(sandbox_cli, "_docker", _FakeDocker())

    result = CliRunner().invoke(
        app,
        ["sandbox", "build", "probe", "--root", str(root), "--push", "reg/to"],
    )
    assert result.exit_code == 0, result.output
    assert "pinned reference" in result.output
    assert "reg/to@sha256:cafe" in result.output


def test_sandbox_build_help_carries_no_corpus_coordinates():
    result = CliRunner().invoke(app, ["sandbox", "build", "--help"])
    assert result.exit_code == 0
    assert "D-41" not in result.output
    assert "RFC" not in result.output.upper()


# ----------------------- #
# The composition root (S-0042 phase 1): one place turns `(root, config)`
# into dep bundles. These builder tests assert each bundle's composition
# against a fixture config — the tests the three old wiring copies never
# had; the verb scenario tests above pin the behaviour unchanged.


def _assembly_root(tmp_path):
    root = tmp_path / "proj"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "gates.yaml").write_text("schema_version: 1\ngates: []\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    return root


def _write_config(root, body: str):
    (root / ".torve" / "config.yaml").write_text(body, encoding="utf-8")


def test_build_run_deps_bundles_the_configured_adapters(tmp_path):
    from torve.adapters.broker.none import NoneBroker
    from torve.adapters.runtime.docker import DockerRuntime
    from torve.adapters.store.durable import open_store
    from torve.adapters.vcs.git import GitVcs, NullScm
    from torve.adapters.workspace.git import GitWorkspace
    from torve.application.dispatch import RunDeps
    from torve.cli.assembly import build_run_deps
    from torve.config.runconfig import RunnerConfig

    root = _assembly_root(tmp_path)
    agent = object()
    deps = build_run_deps(root, RunnerConfig(), agent=agent)

    assert isinstance(deps, RunDeps)
    assert isinstance(deps.workspace, GitWorkspace)
    assert isinstance(deps.runtime, DockerRuntime)
    assert isinstance(deps.vcs, GitVcs)
    assert isinstance(deps.scm, NullScm)  # open_pr off: no forge leg
    assert deps.store is open_store
    assert isinstance(deps.broker, NoneBroker)
    assert deps.agent is agent
    assert deps.review_agent is None
    assert deps.retry_agent is None


def test_build_run_deps_opens_the_forge_and_keeps_shared_parts(tmp_path):
    from torve.adapters.broker.local import LocalBroker
    from torve.adapters.vcs.git import GhScm, GitVcs
    from torve.adapters.workspace.git import GitWorkspace
    from torve.cli.assembly import build_run_deps
    from torve.config.runconfig import RunnerConfig

    root = _assembly_root(tmp_path)
    config = RunnerConfig.model_validate(
        {"scm": {"open_pr": True, "repo": "o/r"}, "broker": {"adapter": "local"}}
    )
    workspace, vcs = GitWorkspace(root), GitVcs()
    make_agent = object()
    reviewer = object()
    deps = build_run_deps(
        root,
        config,
        agent=object(),
        workspace=workspace,
        vcs=vcs,
        review_agent=reviewer,
        retry_agent=make_agent,  # type: ignore[arg-type]
    )

    assert isinstance(deps.scm, GhScm)  # open_pr on: the forge leg is wired
    assert isinstance(deps.broker, LocalBroker)
    assert deps.workspace is workspace  # the tick's legs share one handle
    assert deps.vcs is vcs
    assert deps.review_agent is reviewer
    assert deps.retry_agent is make_agent


def test_dispatch_agent_factory_applies_the_run_verbs_rules(tmp_path):
    from torve.adapters.agent.fake import FakeAgent
    from torve.adapters.agent.harness import HarnessAgent
    from torve.cli.assembly import dispatch_agent_factory
    from torve.config.runconfig import TierConfig

    fake = TierConfig(adapter="fake")
    real = TierConfig(adapter="harness", command="c", provider="p", model="m")

    plain = dispatch_agent_factory()
    assert isinstance(plain(fake), FakeAgent)
    assert isinstance(plain(real), HarnessAgent)

    override = dispatch_agent_factory(agent_name="fake")
    assert isinstance(override(real), FakeAgent)  # the door's override wins

    scenario = tmp_path / "scenario.yaml"
    scenario.write_text("attempts:\n  - exit_code: 0\n", encoding="utf-8")

    replay = dispatch_agent_factory(agent_name="fake", scenario=scenario)
    assert isinstance(replay(real), FakeAgent)

    with pytest.raises(ValueError, match="FakeAgent-only"):
        dispatch_agent_factory(scenario=scenario)(real)


def test_route_dispatch_providers_refuses_a_rung_provider(tmp_path):
    from torve.cli.assembly import route_dispatch_providers
    from torve.config.runconfig import ProviderDenied, load_runner_config, tier_for

    root = _assembly_root(tmp_path)
    _write_config(
        root,
        "schema_version: 1\n"
        "tiers:\n"
        "  executor: {retry_variants: {compliance: executor.deep}}\n"
        "  executor.deep: {adapter: harness, command: c, provider: deepseek, model: m}\n"
        "providers: {default: []}\n",
    )
    config = load_runner_config(root)

    with pytest.raises(ProviderDenied, match="deepseek"):
        route_dispatch_providers(config, root, tier_for(config, "executor"))

    _write_config(
        root,
        "schema_version: 1\n"
        "tiers:\n"
        "  executor: {retry_variants: {compliance: executor.deep}}\n"
        "  executor.deep: {adapter: harness, command: c, provider: deepseek, model: m}\n"
        "providers: {default: [deepseek]}\n",
    )
    config = load_runner_config(root)
    route_dispatch_providers(config, root, tier_for(config, "executor"))  # must not raise


# ....................... #


def test_dotenv_fills_in_names_the_environment_does_not_carry(tmp_path, monkeypatch):
    """The operator exports eight names into every shell or writes them once
    (A-111). What they wrote is never allowed to overrule what they typed:
    a name already in the environment stays as it is."""

    from torve.cli.options import load_dotenv

    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "TORVE_TEST_DSN=postgresql://from-file/db\n"
        "export TORVE_TEST_QUOTED='quoted'\n"
        'TORVE_TEST_ALREADY="from-file"\n'
        "not an assignment\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TORVE_TEST_ALREADY", "from-the-shell")
    monkeypatch.delenv("TORVE_TEST_DSN", raising=False)
    monkeypatch.delenv("TORVE_TEST_QUOTED", raising=False)

    taken = load_dotenv(tmp_path)

    assert set(taken) == {"TORVE_TEST_DSN", "TORVE_TEST_QUOTED"}
    assert os.environ["TORVE_TEST_DSN"] == "postgresql://from-file/db"
    # `export ` and surrounding quotes are shell noise, not part of a value.
    assert os.environ["TORVE_TEST_QUOTED"] == "quoted"
    # The shell wins, and the name it set is not in what the file took.
    assert os.environ["TORVE_TEST_ALREADY"] == "from-the-shell"


def test_no_dotenv_is_not_an_error(tmp_path):
    from torve.cli.options import load_dotenv

    assert load_dotenv(tmp_path) == []


def test_a_write_refuses_the_mock_the_configuration_did_not_ask_for(tmp_path, monkeypatch):
    """A read that finds nothing is recoverable; a write that lands nowhere
    is gone. `torve manager resolve` with no --dsn stood up a mock — "a real
    log for the life of the process and nothing afterwards" — closed the
    escalation against it, and printed that it had. An operator's triage
    cannot be a no-op that reports success."""

    import typer

    from torve.cli.options import dsn_to_write
    from torve.domain.states import EXIT_CONFIG

    (tmp_path / ".torve").mkdir(parents=True, exist_ok=True)
    config = tmp_path / ".torve" / "config.yaml"
    config.write_text(
        "schema_version: 1\nstore:\n  adapter: postgres\n  dsn_env: TORVE_TEST_PG\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TORVE_TEST_PG", raising=False)

    with pytest.raises(typer.Exit) as refused:
        dsn_to_write(tmp_path)

    assert refused.value.exit_code == EXIT_CONFIG

    # With the variable set, the write resolves exactly as a read does.
    monkeypatch.setenv("TORVE_TEST_PG", "postgresql://configured/db")
    assert dsn_to_write(tmp_path) == "postgresql://configured/db"

    # A repository whose store really is mock still gets one: that is what
    # it asked for, and refusing it would break every mock-store write.
    config.write_text("schema_version: 1\nstore:\n  adapter: mock\n", encoding="utf-8")
    assert dsn_to_write(tmp_path) == ""


def test_no_module_imports_a_dependency_private_at_runtime() -> None:
    """T-0265: `torve review` imported `typer._click.core` at module level
    for two annotations, and the root app imports that module eagerly — so
    every `torve` command failed to import on any resolution honouring the
    declared `typer>=0.16` floor, where `typer._click` does not exist. Only
    the lockfile's 0.27.1 hid it; a fresh `pip install torve` was broken.

    The rule is general because the next one will be a different package: a
    private submodule of a dependency is not part of its contract, and
    nothing may need one to *import*. Annotations belong under
    `if TYPE_CHECKING:`, where they are never evaluated.
    """

    import ast

    offenders: list[str] = []

    for path in sorted(Path("src/torve").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            # Anything guarded by an `if` — TYPE_CHECKING above all — never
            # runs at import, which is exactly where these belong.
            if isinstance(node, ast.If):
                continue

            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.level == 0:
                    names = [sub.module or ""]

                elif isinstance(sub, ast.Import):
                    names = [alias.name for alias in sub.names]

                else:
                    continue

                for name in names:
                    parts = name.split(".")

                    if parts[0] in {"torve", ""}:
                        continue

                    if any(part.startswith("_") for part in parts[1:]):
                        offenders.append(f"{path}: {name}")

    assert not offenders, (
        "a dependency's private submodule is not part of its contract, and "
        f"importing one at runtime breaks the package at its own declared floor: {offenders}"
    )


def test_dsn_defaults_to_the_configured_variable(tmp_path, monkeypatch):
    """Naming a partition and omitting --dsn read an empty mock, found
    nothing and fell back to the files — the safe direction, and silent
    about a misconfiguration the operator could not see (A-123)."""

    from torve.cli.options import dsn_for

    (tmp_path / ".torve").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".torve" / "config.yaml").write_text(
        "schema_version: 1\nstore:\n  adapter: postgres\n  dsn_env: TORVE_TEST_PG\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TORVE_TEST_PG", "postgresql://configured/db")

    assert dsn_for(tmp_path) == "postgresql://configured/db"
    # What the caller typed always wins over what the configuration names.
    assert dsn_for(tmp_path, "postgresql://typed/db") == "postgresql://typed/db"

    # A mock store has no DSN, and says so rather than guessing at a name.
    (tmp_path / ".torve" / "config.yaml").write_text(
        "schema_version: 1\nstore:\n  adapter: mock\n", encoding="utf-8"
    )
    assert dsn_for(tmp_path) == ""


def test_owed_reports_a_row_covered_by_its_check(repo):
    """S-0054 S-0054/D-3: `torve log owed` names the three states — owed,
    pathless, covered by a check — so the executor sees why a row is or is
    not on its list."""

    repo.seed()
    decisions = [
        {
            "id": "S-0009/D-1",
            "grade": "LOCKED",
            "text": "checked",
            "paths": ["src/**"],
            "check": "true",
        },
        {"id": "S-0009/D-2", "grade": "LOCKED", "text": "silent", "paths": ["src/**"]},
    ]
    repo.task(base_task(allow=["src/**"], decisions=decisions), log_document())

    result = CliRunner().invoke(
        app,
        [
            "log",
            "owed",
            TASK_ID,
            "--root",
            str(repo.root),
            "--touched",
            "src/app.py",
            "--format",
            "json",
        ],
    )
    reported = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert [p for p in reported["owed"] if "S-0009/D-2" in p]
    assert reported["skipped"] == ["S-0009/D-1: covered by its check, which runs as a gate"]

    text = CliRunner().invoke(
        app, ["log", "owed", TASK_ID, "--root", str(repo.root), "--touched", "src/app.py"]
    )

    assert "covered by its check" in text.output


# ----------------------- #
# S-0057 S-0057/D-5: `torve init` writes what the code derives, and only that


def _bare_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".torve").mkdir(parents=True)
    (root / ".torve" / "config.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / ".torve" / "gates.yaml").write_text(
        "# the manifest\nschema_version: 1\ngates: []\n", encoding="utf-8"
    )
    return root


def test_init_writes_the_schemas_the_ignore_file_and_the_schema_lines_once(tmp_path):
    root = _bare_repo(tmp_path)

    first = CliRunner().invoke(app, ["init", "--root", str(root)])

    assert first.exit_code == 0, first.output
    schemas = root / ".torve" / "schemas"
    assert sorted(p.name for p in schemas.iterdir()) == [
        "amendments.json",
        "config.json",
        "contract.json",
        "decisions.json",
        "document.json",
        "execution.json",
        "gates.json",
        "log.json",
    ]
    assert json.loads((schemas / "contract.json").read_text(encoding="utf-8"))["title"] == "Task"
    assert (root / ".torve" / "specs").is_dir()  # an empty corpus checks clean
    ignore = (root / ".torve" / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignore == [
        "tasks/",
        "context/",
        "telemetry.jsonl",
        "feedback.jsonl",
        "regimes/",
        "traces/",
        "skills/",
        "tmp/",
    ]
    config_lines = (root / ".torve" / "config.yaml").read_text(encoding="utf-8").splitlines()
    gates_lines = (root / ".torve" / "gates.yaml").read_text(encoding="utf-8").splitlines()
    assert config_lines[0] == "# yaml-language-server: $schema=schemas/config.json"
    assert gates_lines[:2] == [
        "# yaml-language-server: $schema=schemas/gates.json",
        "# the manifest",
    ]

    before = {p: p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}
    second = CliRunner().invoke(app, ["init", "--root", str(root)])

    assert second.exit_code == 0, second.output
    assert "0 file(s) written" in second.output
    assert {p: p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()} == before


def test_init_appends_a_missing_pattern_below_the_operators_lines_and_rewrites_a_stale_schema(
    tmp_path,
):
    root = _bare_repo(tmp_path)
    assert CliRunner().invoke(app, ["init", "--root", str(root)]).exit_code == 0
    ignore = root / ".torve" / ".gitignore"
    ignore.write_text("# mine\nscratch/\ntasks/\ncontext/\n", encoding="utf-8")
    stale = root / ".torve" / "schemas" / "log.json"
    stale.write_text("{}\n", encoding="utf-8")

    again = CliRunner().invoke(app, ["init", "--root", str(root)])

    assert again.exit_code == 0, again.output
    assert "log.json  written" in again.output
    assert "6 pattern(s) added" in again.output
    assert stale.read_text(encoding="utf-8") != "{}\n"
    lines = ignore.read_text(encoding="utf-8").splitlines()
    assert lines[:4] == ["# mine", "scratch/", "tasks/", "context/"]  # the operator's, untouched
    assert lines[4:] == [
        "telemetry.jsonl",
        "feedback.jsonl",
        "regimes/",
        "traces/",
        "skills/",
        "tmp/",
    ]
