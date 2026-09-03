from __future__ import annotations

import json
import subprocess

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
    return root


class _FakeRuntime:
    """The build is recorded, not executed: the push path's contract is
    that the runtime still builds and the CLI still pushes."""

    def __init__(self):
        self.builds = []

    def build_image(self, context, tag):
        self.builds.append((str(context), tag))
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
    assert runtime.builds == [(str(root / ".torve" / "sandbox" / "probe"), "torve-agent:probe")]
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
