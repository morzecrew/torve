"""The dispatch object and its life (S-0046).

Three of these existed before only as consequences of a whole run: the
credential refusal, the ordering that keeps a refusal from leaking a live
broker, and the one-record-two-carriers round trip. A step that can be
called with a dispatch and nothing else is the point of the restructuring,
so each of them is now a test with no runtime, no agent and no store in it.
"""

import json

import pytest
from pydantic import ValidationError

from torve.application.dispatch import (
    RunDeps,
    attempt_row,
    open_broker,
    open_dispatch,
)
from torve.config.providers import Provider
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
    nobody revokes (S-0046/D-4)."""

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


def _record(models: dict | None = None):
    """A provider record for `p`, which every seat below is on. A seat resolves
    against the roster now (S-0064/D-5), so a configuration built in code owes
    the same record one read from files would have."""

    from torve.config.providers import Model, Provider, Route

    return Provider(
        name="p",
        key_env=KEY_ENV,
        routes={"openai": Route(base_url="https://p.example")},
        models=models if models is not None else {"m": Model()},
    )


def _brokered(models: dict | None = None, **tiers: TierConfig) -> RunnerConfig:
    """A validated brokered configuration, seat and record together."""

    return RunnerConfig(
        tiers={
            "executor": TierConfig(adapter="api", provider="p", api=["openai"], model="m"),
            **tiers,
        },
        broker=BrokerConfig(
            adapter="local",
            providers={"p": BrokerProvider(upstream="https://p.example", key_env=KEY_ENV)},
        ),
        provider_records={"p": _record(models)},
    )


# ....................... #


def test_a_brokered_dispatch_hands_the_sandbox_no_provider_key(tmp_path):
    """Two refusals used to stand here: the validator's, and the dispatch's own
    so a programmatically-built configuration could not slip a key name past it
    into a sandbox's env. S-0064/D-9 removed the field both were about — a
    credential is the provider's — so what is left to assert is the guarantee
    rather than the guard."""

    from torve.config.runconfig import credential_names

    config = _brokered()

    assert credential_names(config, config.tiers["executor"]) == ()

    broker = _CountingBroker()
    open_dispatch(
        tmp_path, Task(id=TASK_ID, decisions=[]), config, _deps(broker=broker), tmp_path / "wt"
    )

    # `open_dispatch` performs every fallible step of setup and the broker opens
    # afterwards, so a refusal can never leak a live credential route.
    assert broker.opens == 0


# ....................... #


def test_open_dispatch_opens_no_broker_of_its_own(tmp_path):
    """The broker is a live credential route, so it opens last, in its own
    call, after every fallible step of setup (S-0046/D-4). A dispatch that
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


# ....................... #
# The price the seat resolves (S-0064/D-12): three different facts, and the
# attempt's cost computed from the record rather than believed from a harness
# that may not recognise the model it was pointed at.


def _priced(roster: dict) -> RunnerConfig:
    return RunnerConfig(
        tiers={"executor": TierConfig(adapter="api", provider="p", api=["openai"], model="m")},
        provider_records={
            "p": Provider.model_validate(
                {
                    "key_env": KEY_ENV,
                    "routes": {"openai": {"base_url": "https://p.example"}},
                    "models": roster,
                }
            )
        },
    )


def _agent_block(tmp_path, config: RunnerConfig) -> dict:
    run = open_dispatch(
        tmp_path,
        Task(id=TASK_ID, decisions=[]),
        config,
        _deps(),
        tmp_path / "wt",
    )

    return run.meta


def test_the_seats_rate_card_is_resolved_once_at_dispatch(tmp_path):
    meta = _agent_block(tmp_path, _priced({"m": {"price": {"input": 0.3, "output": 1.2}}}))

    assert meta["price"] == {"input": 0.3, "output": 1.2, "cache_read": None, "cache_write": None}


def test_a_listed_model_with_no_price_resolves_to_no_price(tmp_path):
    """A subscription seat genuinely has no per-token cost, and a null price is
    the record saying so — not the same fact as a roster that says nothing."""

    meta = _agent_block(tmp_path, _priced({"m": {"context_window": 200000}}))

    assert meta["price"] is None


def test_a_seat_naming_a_model_the_roster_does_not_list_never_reaches_dispatch():
    """Phase 1 resolved no price for an unlisted model and let the attempt keep
    whatever the harness reported. Phase 2 makes that state unreachable for a
    real seat (S-0064/D-5): the roster is what a seat may reach, so the question
    of how to price something undeclared stops being asked."""

    with pytest.raises(ValidationError, match="never one it does not"):
        _priced({"other": {}})


def test_the_engines_arithmetic_is_the_cost_and_the_harnesss_is_its_claim(tmp_path):
    """Measured, claude emits `unrecognized_model` for qwen3.8-flash and then
    prices the attempt off its own Anthropic table. The claim is kept beside
    the number and is never the number (S-0064/D-12)."""

    from torve.application.telemetry import priced

    block = priced(
        {
            "price": {"input": 0.3, "output": 1.2},
            "cost_usd": 47.0,
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }
    )

    assert block["cost_usd"] == 1.5
    assert block["adapter_cost_usd"] == 47.0


def test_a_seat_the_roster_says_nothing_about_keeps_its_harnesss_number():
    from torve.application.telemetry import priced

    block = priced({"cost_usd": 47.0, "input_tokens": 1_000_000})

    assert block["cost_usd"] == 47.0
    assert "adapter_cost_usd" not in block


def test_a_priced_seat_that_reported_no_counts_stays_unreported():
    """Unreported is never zero (S-0004/D-6): an adapter that counted nothing
    leaves the cost absent, even where the rate card is known."""

    from torve.application.telemetry import priced

    block = priced({"price": {"input": 0.3}, "cost_usd": 47.0})

    assert block["cost_usd"] is None
    assert block["adapter_cost_usd"] == 47.0
