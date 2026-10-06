"""The shared `schema_version` field (S-0095/D-5): every model torve reads
from YAML carries it through `torve.base.model.SchemaVersion`. Absent reads
as current; a foreign value is refused naming the version found and the
version this build reads, "a newer torve" for a newer file and the release
notes' conversion for an older one.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from torve.config.agents import SCHEMA_VERSION as AGENT_SCHEMA_VERSION
from torve.config.agents import AgentProfile, HarnessManifest
from torve.config.fleet import SCHEMA_VERSION as FLEET_SCHEMA_VERSION
from torve.config.fleet import FleetManifest
from torve.config.manifest import SCHEMA_VERSION as MANIFEST_SCHEMA_VERSION
from torve.config.manifest import Manifest
from torve.config.providers import SCHEMA_VERSION as PROVIDER_SCHEMA_VERSION
from torve.config.providers import Provider
from torve.config.runconfig import SCHEMA_VERSION as RUNCONFIG_SCHEMA_VERSION
from torve.config.runconfig import RunnerConfig
from torve.domain.spec import SCHEMA_VERSION as DOCUMENT_SCHEMA_VERSION
from torve.domain.spec import Document
from torve.domain.task import CONTRACT_SCHEMA_VERSION, Task

# ----------------------- #

# Task carries its own schema_version (CONTRACT_SCHEMA_VERSION) but not through
# the shared type: `gates/sabotage.py::base_task`, a fixture well outside this
# task's scope, still writes the pre-S-0059 `schema_version: 1` into every
# contract it builds, and a long tail of in-repo tests (test_cli.py among them)
# construct contracts through it. Wiring the refusal onto `Task` turns every one
# of those into a red it was not this task's to fix (S-0095/D-5, departed).
MODELS = [
    (Manifest, MANIFEST_SCHEMA_VERSION, {}),
    (AgentProfile, AGENT_SCHEMA_VERSION, {}),
    (HarnessManifest, AGENT_SCHEMA_VERSION, {}),
    (
        Provider,
        PROVIDER_SCHEMA_VERSION,
        {"key_env": "ACME_API_KEY", "routes": {"openai": {"base_url": "https://api.acme.test"}}},
    ),
    (FleetManifest, FLEET_SCHEMA_VERSION, {}),
    (RunnerConfig, RUNCONFIG_SCHEMA_VERSION, {}),
    (
        Document,
        DOCUMENT_SCHEMA_VERSION,
        {"id": "S-0001", "title": "A document", "status": "draft", "owner": "Someone"},
    ),
]


def test_task_still_reads_its_own_version_absent_or_current() -> None:
    fields = {"id": "T-0001", "decisions": []}

    assert Task.model_validate(fields).schema_version == CONTRACT_SCHEMA_VERSION
    assert (
        Task.model_validate({**fields, "schema_version": CONTRACT_SCHEMA_VERSION}).schema_version
        == CONTRACT_SCHEMA_VERSION
    )


# ....................... #


@pytest.mark.parametrize(("model", "current", "fields"), MODELS)
def test_absent_schema_version_reads_as_current(model, current, fields) -> None:
    built = model.model_validate(fields)

    assert built.schema_version == current


@pytest.mark.parametrize(("model", "current", "fields"), MODELS)
def test_the_current_schema_version_loads(model, current, fields) -> None:
    built = model.model_validate({**fields, "schema_version": current})

    assert built.schema_version == current


@pytest.mark.parametrize(("model", "current", "fields"), MODELS)
def test_a_newer_schema_version_is_refused_naming_a_newer_torve(model, current, fields) -> None:
    with pytest.raises(ValidationError, match="a newer torve") as caught:
        model.model_validate({**fields, "schema_version": current + 1})

    assert str(current + 1) in str(caught.value)
    assert str(current) in str(caught.value)


@pytest.mark.parametrize(("model", "current", "fields"), MODELS)
def test_an_older_schema_version_is_refused_naming_the_release_notes(
    model, current, fields
) -> None:
    older = current - 1

    with pytest.raises(ValidationError, match="the release notes") as caught:
        model.model_validate({**fields, "schema_version": older})

    assert str(older) in str(caught.value)
    assert str(current) in str(caught.value)
