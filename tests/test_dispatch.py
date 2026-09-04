"""The dispatch object and its life (RFC 0046).

Three of these existed before only as consequences of a whole run: the
credential refusal, the ordering that keeps a refusal from leaking a live
broker, and the one-record-two-carriers round trip. A step that can be
called with a dispatch and nothing else is the point of the restructuring,
so each of them is now a test with no runtime, no agent and no store in it.
"""

import json

import pytest

from torve.application.dispatch import (
    RunDeps,
    attempt_row,
    open_broker,
    open_dispatch,
)
from torve.config.runconfig import BrokerConfig, BrokerProvider, RunnerConfig, TierConfig
from torve.domain.task import Task

# ----------------------- #

TASK_ID = "T-0001"
KEY_ENV = "SOME_PROVIDER_API_KEY"


# ....................... #


class _StubRuntime:
    def resolve_image(self, image: str) -> str | None:
        return "sha256:whatever"


# ....................... #


class _CountingBroker:
    """Records that it was asked to open. The refusals below must never
    reach it — a broker opened before a refusal is a live credential route
    nobody revokes (D-46.4)."""

    name = "counting"

    def __init__(self) -> None:
        self.opens = 0

    def open(self, *args: object, **kwargs: object) -> object:
        self.opens += 1

        return object()


# ....................... #


def _deps(**overrides: object) -> RunDeps:
    fields: dict = {
        "workspace": None,
        "runtime": _StubRuntime(),
        "agent": object(),
        "vcs": object(),
        "scm": None,
        "store": None,
    }
    fields.update(overrides)

    return RunDeps(**fields)  # type: ignore[arg-type]


# ....................... #


def _brokered(**tiers: TierConfig) -> RunnerConfig:
    """A validated brokered configuration. The refusals below then reach in
    and name a credential on a tier, which is the case the dispatch's own
    check exists for: the validator has already run, and a configuration
    built in code rather than read from a file never passed it."""

    return RunnerConfig(
        tiers={"executor": TierConfig(adapter="api", provider="p", command="run"), **tiers},
        broker=BrokerConfig(
            adapter="local",
            providers={"p": BrokerProvider(upstream="https://p.example", key_env=KEY_ENV)},
        ),
    )


# ....................... #


def test_a_brokered_tier_naming_a_credential_is_refused_before_the_broker_opens(tmp_path):
    """D-21.1's second line: the validator refuses this, and the dispatch
    refuses it again so a programmatically-built configuration cannot slip a
    key name past the validator into the sandbox's env."""

    broker = _CountingBroker()
    config = _brokered()
    config.tiers["executor"].api_key_env = [KEY_ENV]

    with pytest.raises(ValueError, match="names no credential"):
        open_dispatch(
            tmp_path,
            Task(id=TASK_ID, decisions=[]),
            config,
            _deps(broker=broker),
            tmp_path / "wt",
        )

    assert broker.opens == 0


# ....................... #


def test_a_retry_rung_naming_a_credential_is_refused_too(tmp_path):
    """A run never dispatches under a regime it has not already validated
    (D-27.11, D-34.6) — the rung the next attempt would route to is checked
    at open, not when the conviction arrives."""

    broker = _CountingBroker()
    config = _brokered(
        executor=TierConfig(
            adapter="api", provider="p", command="run", retry_variants={"functional": "heavy"}
        ),
        heavy=TierConfig(adapter="api", provider="p", command="run"),
    )
    config.tiers["heavy"].api_key_env = [KEY_ENV]

    with pytest.raises(ValueError, match="names no credential"):
        open_dispatch(
            tmp_path,
            Task(id=TASK_ID, decisions=[]),
            config,
            _deps(broker=broker, retry_agent=lambda tier: object()),
            tmp_path / "wt",
        )

    assert broker.opens == 0


# ....................... #


def test_open_dispatch_opens_no_broker_of_its_own(tmp_path):
    """The broker is a live credential route, so it opens last, in its own
    call, after every fallible step of setup (D-46.4). A dispatch that
    opened one on the way out would make that ordering unenforceable."""

    broker = _CountingBroker()
    run = open_dispatch(
        tmp_path,
        Task(id=TASK_ID, decisions=[]),
        _brokered(),
        _deps(broker=broker),
        tmp_path / "wt",
    )

    assert run.broker_handle is None
    assert broker.opens == 0

    open_broker(run)

    assert run.broker_handle is not None
    assert broker.opens == 1


# ....................... #


def test_the_attempt_row_is_rendered_from_the_record_it_reports(tmp_path):
    """One record, both carriers (A-85). The stream is written from the
    record rather than built beside it, so a field added to one carrier and
    not the other stops being possible — this is the round trip that pins
    it, at the step instead of through a whole run."""

    from torve.application.telemetry import record_payload

    run = open_dispatch(
        tmp_path,
        Task(id=TASK_ID, decisions=[]),
        RunnerConfig(),
        _deps(),
        tmp_path / "wt",
    )
    run.meta["attempt"] = 2

    record = attempt_row(run, "agent_error", exit_code=3, timed_out=False)

    rows = [
        json.loads(line)
        for line in (tmp_path / ".torve" / "telemetry.jsonl").read_text().splitlines()
    ]

    assert len(rows) == 1
    assert rows[0]["task_id"] == TASK_ID
    assert rows[0]["verdict"] == "agent_error"
    assert rows[0]["agent"]["image_digest"] == "sha256:whatever"

    # Every payload field reaches the row unchanged: the row is the payload
    # plus the envelope, and nothing in between reinterprets it.
    payload = record_payload(record, 2)

    for key, value in payload.items():
        if key != "attempt":
            assert rows[0][key] == value
