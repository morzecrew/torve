"""The live channel (RFC 0045 §5.2, §5.3).

The sandbox holds no store credential and never will (D-45.1), so what is
tested here is the route that replaces one: the broker appends on the run's
behalf, stamps who and what the record is about from the run it was opened
for, and refuses at the boundary anything the authority table does not give
an agent — before a service behind it is asked.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import HOSTILE
from forze.application.execution import DepsRegistry, ExecutionRuntime
from test_broker import (
    KEY_ENV,
    PROVIDER,
    broker_config,
    broker_post,
    routing_for,
)

from torve.adapters.broker.local import LocalBroker
from torve.adapters.eventstore.document import mock_module
from torve.application.channel import Channel, ChannelRefused, open_channel, seed
from torve.application.eventlog import RunLogChannel, event_log
from torve.application.ports import BrokerBudget
from torve.domain.events import ActorKind, EventKind

PARTITION = "morzecrew/torve"
TASK_ID = "T-9001"

ENTRY = {
    "attempt": 1,
    "decision_id": "D-44.10",
    "grade": "LOCKED",
    "entry_kind": "resolved",
    "entry_class": "spec-gap",
    "claim": "the intake posts through the broker now",
    "evidence": "src/torve/application/channel.py:1 — the sandbox's end of it",
    "action": "decided",
}


class Recorder:
    """A channel that remembers instead of persisting — the broker's side of
    the contract is what these cases are about."""

    def __init__(self, refuse: str = "") -> None:
        self.records: list[tuple[str, dict]] = []
        self.refuse = refuse
        self.sent: list[dict] = []

    def record(self, kind: str, payload: dict) -> None:
        if self.refuse:
            raise ValueError(self.refuse)

        self.records.append((kind, payload))

    def notes(self) -> list[dict]:
        return self.sent


@pytest.fixture
def broker(upstream, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k-123-secret")
    _, upstream_url = upstream

    return LocalBroker(broker_config(upstream_url), host="127.0.0.1"), upstream_url


# ....................... #


def test_a_record_reaches_the_store_through_the_broker(broker):
    local, upstream_url = broker
    channel = Recorder()
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)

    status, body = broker_post(
        handle.channel_url + "/records",
        handle.token,
        json.dumps({"kind": "divergence.recorded", "payload": ENTRY}).encode(),
    )

    assert status == 201
    assert json.loads(body) == {"recorded": True}
    assert channel.records == [("divergence.recorded", ENTRY)]

    local.close(handle)


def test_a_request_without_the_run_token_reaches_nothing(broker):
    local, upstream_url = broker
    channel = Recorder()
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)

    status, _ = broker_post(
        handle.channel_url + "/records",
        "not-the-token",
        json.dumps({"kind": "divergence.recorded", "payload": ENTRY}).encode(),
    )

    assert status == 401
    assert channel.records == []

    usage = local.close(handle)
    assert usage.refusals == {"auth": 1}


def test_a_kind_the_agent_may_not_write_is_refused_at_the_boundary(broker):
    local, upstream_url = broker
    # The service behind the boundary is what refuses; the boundary is where
    # the refusal is turned into an answer, and nothing is appended either way.
    channel = Recorder(refuse="agent may not write landing.recorded")
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)

    status, body = broker_post(
        handle.channel_url + "/records",
        handle.token,
        json.dumps({"kind": "landing.recorded", "payload": {"sha": "a" * 40}}).encode(),
    )

    assert status == 403
    assert "may not write" in json.loads(body)["error"]["message"]
    assert channel.records == []

    assert local.close(handle).refusals == {"authority": 1}


def test_a_run_without_a_channel_advertises_none_and_serves_none(broker):
    local, upstream_url = broker
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget())

    assert handle.channel_url == ""

    # The path is still refused rather than silently accepted: a sandbox
    # posting into a run with no channel learns so.
    status, _ = broker_post(
        "http://" + handle.url_for(PROVIDER).split("//", 1)[1].split("/", 1)[0] + "/_torve/records",
        handle.token,
        json.dumps({"kind": "divergence.recorded", "payload": ENTRY}).encode(),
    )

    assert status == 404

    local.close(handle)


def test_notes_are_a_poll(broker):
    local, upstream_url = broker
    channel = Recorder()
    channel.sent = [
        {"at": "2026-09-04T00:00:00Z", "to_role": "implement", "topic": "flake", "body": "known"}
    ]
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)

    sandbox = Channel(url=handle.channel_url, token=handle.token)

    assert sandbox.notes() == channel.sent

    local.close(handle)


# ....................... #
# The sandbox's end


def test_the_channel_file_is_written_only_when_there_is_one(tmp_path):
    assert seed(tmp_path, "", "") is None
    assert open_channel(tmp_path) is None

    seed(tmp_path, "http://127.0.0.1:9/_torve", "t-1")
    channel = open_channel(tmp_path)

    assert channel is not None
    assert channel.url.endswith("/_torve")


def test_a_channel_naming_an_unusable_scheme_refuses_rather_than_reads(tmp_path):
    (tmp_path / "secret").write_text("nothing to see")
    seed(tmp_path, f"file://{tmp_path}", "t-1")
    channel = open_channel(tmp_path)

    assert channel is not None

    # The URL comes from a file inside the worktree; a `file:` scheme would
    # turn a post into a local read.
    with pytest.raises(ChannelRefused, match="unusable URL"):
        channel.record("divergence.recorded", ENTRY)


def test_the_host_side_channel_stamps_identity_it_is_never_given():
    async def scenario():
        runtime = ExecutionRuntime(deps=DepsRegistry.from_modules(mock_module()).freeze())

        async with runtime.scope():
            log = event_log(runtime.get_context())
            channel = RunLogChannel(
                log=log,
                loop=asyncio.get_running_loop(),
                partition=PARTITION,
                task_id=TASK_ID,
                seat="worker-1",
            )

            # The broker calls from its request thread.
            await asyncio.to_thread(channel.record, "divergence.recorded", ENTRY)

            recorded = await log.history(TASK_ID, partition=PARTITION)

            assert [event.kind for event in recorded] == [EventKind.DIVERGENCE_RECORDED]
            # Nothing in the request said any of this.
            assert recorded[0].actor_kind is ActorKind.AGENT
            assert recorded[0].partition == PARTITION
            assert recorded[0].subject_id == TASK_ID

            # And a kind the table does not give an agent never reaches the store.
            with pytest.raises(Exception, match="may not write"):
                await asyncio.to_thread(
                    channel.record, "landing.recorded", {"sha": "a" * 40, "attempt": 1}
                )

            assert len(await log.history(TASK_ID, partition=PARTITION)) == 1

    asyncio.run(scenario())


def test_the_verb_posts_through_the_channel_and_writes_no_file(worktree, broker):
    from typer.testing import CliRunner

    from torve.cli.main import app
    from torve.config import layout
    from torve.gates.sabotage import TASK_ID as SABOTAGE_TASK

    local, upstream_url = broker
    channel = Recorder()
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)
    seed(worktree.root, handle.channel_url, handle.token)

    result = CliRunner().invoke(
        app,
        [
            "log",
            "divergence",
            SABOTAGE_TASK,
            "--root",
            str(worktree.root),
            "--decision",
            "D-1",
            "--grade",
            "LOCKED",
            "--kind",
            "resolved",
            "--class",
            "spec-gap",
            "--claim",
            "the entry travels the channel",
            "--evidence",
            HOSTILE,
            "--action",
            "decided",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["channel"] is True
    assert [kind for kind, _ in channel.records] == ["divergence.recorded"]
    assert channel.records[0][1]["claim"] == "the entry travels the channel"
    # Nothing was written into the worktree: with a channel the engine
    # writes the log from the record, and the sandbox writes nothing.
    assert not layout.log_file(worktree.root, SABOTAGE_TASK).exists()

    local.close(handle)


def test_the_notes_verb_reads_what_the_engine_said(worktree, broker):
    from typer.testing import CliRunner

    from torve.cli.main import app

    local, upstream_url = broker
    channel = Recorder()
    channel.sent = [{"at": "2026-09-04T00:00:00Z", "topic": "flake", "body": "known, do not chase"}]
    handle = local.open("run-1", routing_for(upstream_url), BrokerBudget(), channel=channel)
    seed(worktree.root, handle.channel_url, handle.token)

    result = CliRunner().invoke(
        app, ["log", "notes", "--root", str(worktree.root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["notes"] == channel.sent

    local.close(handle)


def test_a_run_with_no_channel_has_no_notes_and_that_is_not_an_error(worktree):
    from typer.testing import CliRunner

    from torve.cli.main import app

    result = CliRunner().invoke(
        app, ["log", "notes", "--root", str(worktree.root), "--format", "json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"channel": False, "notes": []}
