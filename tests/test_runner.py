from __future__ import annotations

import json

from conftest import context_for

from torve.gates.sabotage import BASE_MANIFEST, TASK_ID, base_task, log_document
from torve.manifest import config_hash
from torve.runner import run_gates
from torve.telemetry import append_record, build_record


def manifest_with(gates: list[dict], **extra) -> dict:
    data = dict(BASE_MANIFEST, gates=gates)
    data.update(extra)
    return data


def test_cheapest_first_ordering(repo):
    repo.seed(manifest_with([
        {"name": "slow", "run": "echo slow", "timeout": 700},
        {"name": "fast", "run": "echo fast", "timeout": 5},
    ]))
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    assert [r.name for r in report.results] == ["fast", "slow"]


def test_fail_fast_skips_later_blocking_but_runs_non_blocking(repo):
    repo.seed(manifest_with([
        {"name": "red", "run": "exit 3", "timeout": 5},
        {"name": "later-blocking", "run": "echo never", "timeout": 100},
        {"name": "advisory", "run": "echo still-runs", "timeout": 200, "blocking": False},
    ]))
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    by_name = {r.name: r for r in report.results}
    assert by_name["red"].outcome == "fail"
    assert by_name["red"].exit_code == 3
    assert by_name["later-blocking"].outcome == "skipped"
    assert by_name["advisory"].outcome == "pass"
    assert report.exit_code == 1


def test_non_blocking_failure_does_not_gate(repo):
    repo.seed(manifest_with([
        {"name": "advisory", "run": "false", "timeout": 5, "blocking": False},
        {"name": "real", "run": "true", "timeout": 100},
    ]))
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    assert report.exit_code == 0


def test_quarantined_acceptance_failure_does_not_block(repo):
    repo.seed(manifest_with(
        [{"name": "acceptance", "run": "@task.acceptance",
          "commands": ["exit 7"], "timeout": 30}],
        quarantine=["exit 7"],
    ))
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    report = run_gates(context_for(repo))
    result = report.results[0]
    assert result.outcome == "pass"
    assert result.quarantined_failures == ["exit 7"]
    assert report.exit_code == 0


def test_bypass_is_appended_to_the_task_log(repo):
    repo.seed(manifest_with([{"name": "scope", "run": "@scope"}],
                            scope={"allow": ["src/**"], "deny": []}))
    repo.task(base_task(allow=["src/**"]), log_document())
    repo.write("rogue.txt", "outside\n")
    repo.git("add", "-A")
    repo.git("commit", "-q", "--no-gpg-sign", "-m",
             "widen\n\nTorve-Bypass: scope: allow list is stale")
    report = run_gates(context_for(repo))
    assert report.results[0].outcome == "bypassed"
    import yaml

    document = yaml.safe_load((repo.root / "logs" / f"{TASK_ID}.yaml").read_text())
    record = document["bypasses"][0]
    assert record["gate"] == "scope"
    assert record["reason"] == "allow list is stale"
    assert document["entries"] is not None  # the divergence list survived the append
    assert report.bypass_count_by_gate == {"scope": 1}


def test_telemetry_record_shape(repo, tmp_path):
    repo.seed()
    repo.task(base_task(allow=["src/**"],
                        decisions=[{"id": "D-1", "grade": "LOCKED",
                                    "text": "settled", "paths": []}]),
              log_document())
    repo.write("src/app.py", "print('x')\n")
    repo.commit("change")
    ctx = context_for(repo)
    report = run_gates(ctx, only={"scope"})
    record = build_record(ctx, report, config_hash(repo.root / "gates.yaml", repo.root))

    assert record["schema_version"] == 1
    assert record["config_hash"]
    assert record["task_id"] == TASK_ID
    # Denormalised, not referenced: the decision rides inside the record.
    assert record["decisions"][0]["id"] == "D-1"
    assert record["decisions"][0]["text"] == "settled"

    target = tmp_path / "telemetry.jsonl"
    append_record(target, record)
    append_record(target, record)
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["config_hash"] == record["config_hash"]
