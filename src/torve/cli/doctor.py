"""`torve doctor` — preflight checks, rendered per RFC 0011 §5: each check
names what it looked for, what it found, and what to do about it. The forze
schema pin is D-12.7: a mismatch must be a check, not a symptom. Image
existence is D-17.2: a configured image the runtime cannot resolve, or a
torve-agent image with no definition directory, is a configuration error
before it becomes a mid-run surprise. The store check is D-3.6 made
operational: a postgres store must name a DSN, answer a connection, and
carry every substrate step (D-12.7's sibling question) before a run
depends on it — and a mock store states plainly that it is the
in-process, test-only regime.

The broker check is RFC 0021 D-21.9: the adapter in force is named, and the
`none` adapter — legal and the phase-1 default — is stated plainly to leave
the credential-custody requirement (D-4b) unmet, so that opting out is a
decision someone can be shown making, never a silent default.

D-27.10 (OPEN, decided here): rather than have a sandbox definition carry a
pointer to the verdict that installed it — a write to `.torve/sandbox/**`
out of this task's scope — doctor reads the eval ledger directly and
matches on the digest it already resolved. Read-only, no new record shape.

The profile check is RFC 0028 D-28.7: each tier that resolved through a
profile (`TierConfig.profile`, set by `load_runner_config`'s raw-mapping
merge) gets one provenance line naming it — no check attached, so this can
never turn doctor red, and a tier or profile file nobody referenced gets
no line and no warning. A-74: a tier composed from a list of profiles
carries its chain, in order, in that same field and line.

The equipment check is RFC 0029 D-29.5: each tier whose resolved `skills`
or `prompt_extras` differ from its role default gets one provenance line —
no check attached, dispatch already owns the refusals (D-29.2).

The image line also covers remote references (RFC 0033 §5.5): a tier
naming a registry reference — an image with an explicit registry host —
that the runtime cannot resolve asks the registry itself for the digest,
anonymously and best-effort, and prints the same line a local image
gets, so "what exactly will run" has one answer for both kinds of
reference. The registry leg is informational: an unreachable registry or
unknown reference keeps the runtime's existing answer (the docker red,
or silence under the opensandbox runtime, whose server pulls from the
registry), because resolution failure already fails dispatch loudly.
"""

from __future__ import annotations

import http.client
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

import typer
from rich.text import Text

from torve.cli.console import STYLE_FAIL, Format, emit_json, mark, out
from torve.cli.options import ConfigOption, FormatOption, RootOption, load_config
from torve.config import layout
from torve.domain.states import EXIT_CONFIG, EXIT_OK

# ----------------------- #


def _config_eval_verdict(root: Path, digest: str) -> dict[str, Any] | None:
    """The eval ledger's most recent config-eval record citing `digest` as
    either arm — the same digest a paired replay (D-27.7) measured, whether
    it won or lost. `None` when the ledger has no such record: an unmeasured
    digest is not a finding, just a fact doctor cannot add to."""

    from torve.application.evals import EVAL_LEDGER

    path = root / layout.TORVE_DIR / EVAL_LEDGER

    if not path.is_file():
        return None

    found: dict[str, Any] | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record: Any = json.loads(line)

        except json.JSONDecodeError:
            continue

        if not isinstance(record, dict):
            continue

        row = cast("dict[str, Any]", record)

        if row.get("kind") != "config-eval":
            continue

        if digest in row.get("digests", {}).values():
            found = row  # append-only ledger — the latest citation wins

    return found


def _image_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    from torve.cli.options import runtime_for
    from torve.cli.sandbox import definitions_root
    from torve.config.runconfig import configured_images

    config = load_config(root, config_path)
    checks: list[tuple[str, bool, str]] = []

    try:
        # The runtime's word covers local images (docker daemon, D-17.2).
        # The opensandbox runtime sees no local images — its server pulls
        # from a registry — so only the registry leg speaks for it.
        runtime = runtime_for(config, None) if config.runtime.adapter == "docker" else None

        for image in configured_images(config):
            digest = runtime.resolve_image(image) if runtime is not None else None

            if digest is None:
                # RFC 0033 §5.5: a registry reference the runtime cannot
                # resolve answers from the registry itself. Best-effort and
                # informational — None here keeps the runtime's answer.
                digest = _registry_digest(image)

            if digest is None:
                if runtime is not None:
                    checks.append(
                        (
                            f"image {image}",
                            False,
                            (
                                f"{image}: not present in the runtime — build it "
                                "(torve sandbox build) or pull it"
                            ),
                        )
                    )

                continue

            detail = f"{image} = {digest[:19]}"
            verdict = _config_eval_verdict(root, digest)

            if verdict is not None:
                role = "incumbent" if verdict["digests"].get("incumbent") == digest else "candidate"
                detail += (
                    f" — eval ledger holds a {role} verdict from {verdict['at']} "
                    f"(candidate_matched={verdict['candidate_matched']})"
                )

            prefix = "torve-agent:"

            if image.startswith(prefix):
                name = image.removeprefix(prefix)

                if not (definitions_root(root) / name / "Dockerfile").is_file():
                    checks.append(
                        (
                            f"image {image}",
                            False,
                            (
                                f"{image}: exists but has no definition under "
                                f"{definitions_root(root) / name} — an image without "
                                "a reviewed definition is an ambient regime"
                            ),
                        )
                    )

                    continue

                detail += " (definition present)"

            checks.append((f"image {image}", True, detail))

    except Exception as error:  # an unusable runtime is the finding
        return [("images", False, f"runtime unavailable: {error}")]

    return checks


# ....................... #


_REGISTRY_TIMEOUT_S = 10.0

# Docker Hub's registry is registry-1.docker.io; the grammar's accepted
# aliases resolve there.
_REGISTRY_HOST_ALIASES = {
    "docker.io": "registry-1.docker.io",
    "index.docker.io": "registry-1.docker.io",
}

# The manifest media types a pull would accept, in preference order, so the
# `Docker-Content-Digest` header names the content the sandbox would run.
_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


def _registry_digest(image: str) -> str | None:
    """A registry reference's content digest, asked of the registry itself
    (RFC 0033 §5.5): a tier naming a registry reference prints the resolved
    digest beside it, the same line a local image already gets.

    Only a reference naming an explicit registry host is queried — a
    host-less tag (`python:3.13-slim`) is the runtime's business and keeps
    its existing answer. Best-effort and informational: any failure
    (unreachable registry, unknown tag, private image, malformed
    reference) returns None and no new check appears — resolution failure
    already fails dispatch loudly.
    """

    host, _, name = image.partition("/")

    if not name or not (host == "localhost" or "." in host or ":" in host):
        return None

    repository, reference = _registry_reference(name)

    if reference is None:
        return None

    host = _REGISTRY_HOST_ALIASES.get(host, host)

    return _registry_manifest_digest(host, repository, reference)


def _registry_reference(name: str) -> tuple[str, str | None]:
    """(repository, tag-or-digest) from the name part of a registry
    reference. A bare name means `latest`, as the docker reference grammar
    prescribes; a digest pin wins over a tag; an empty repository or
    reference is a malformed name."""

    repository, _, digest = name.partition("@")

    if ":" in repository:
        repository, _, tag = repository.rpartition(":")
    else:
        tag = ""

    reference = digest or tag or "latest"

    if not repository or not reference:
        return repository, None

    return repository, reference


def _registry_manifest_digest(host: str, repository: str, reference: str) -> str | None:
    """One anonymous registry v2 manifest fetch, honouring the standard
    bearer-token challenge the first request earns. `None` on any failure
    — the caller's check already has the runtime's answer."""

    path = f"/v2/{repository}/manifests/{reference}"
    status, headers = _registry_request(host, path, token=None)

    if status == 401:
        token = _registry_bearer_token(headers.get("www-authenticate", ""))

        if token is None:
            return None

        status, headers = _registry_request(host, path, token=token)

    if status != 200:
        return None

    digest = headers.get("docker-content-digest", "")

    return digest if digest.startswith("sha256:") else None


def _registry_request(host: str, path: str, token: str | None) -> tuple[int, dict[str, str]]:
    """One TLS GET against a registry host, drained and closed, headers
    lower-cased. Transport failure returns (0, {}); never raises — the
    registry leg must not take the doctor down with it."""

    try:
        connection = http.client.HTTPSConnection(host, timeout=_REGISTRY_TIMEOUT_S)

    except Exception:
        return 0, {}

    try:
        headers = {"Accept": _MANIFEST_ACCEPT}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}

    except Exception:
        return 0, {}

    finally:
        connection.close()


def _registry_bearer_token(challenge: str) -> str | None:
    """The token leg of a registry bearer challenge: GET the realm echoing
    the service and scope back, anonymously. Public images are all the
    doctor's informational line needs; a private image's challenge stays
    unanswered and the runtime's answer stands. Never raises."""

    fields: dict[str, str] = {}

    for key, value in re.findall(r'(\w+)="([^"]*)"', challenge):
        fields[key] = value

    realm, service, scope = (
        fields.get("realm", ""),
        fields.get("service", ""),
        fields.get("scope", ""),
    )

    if not realm or not scope:
        return None

    try:
        parsed = urlsplit(realm)

        if not parsed.hostname:
            return None

        connection = http.client.HTTPSConnection(
            parsed.hostname, parsed.port or 443, timeout=_REGISTRY_TIMEOUT_S
        )

    except Exception:
        return None

    try:
        query = urlencode({"service": service, "scope": scope})
        target = f"{parsed.path}?{parsed.query}&{query}" if parsed.query else f"{parsed.path}?{query}"
        connection.request("GET", target, headers={"Accept": "application/json"})
        response = connection.getresponse()

        if response.status != 200:
            return None

        payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("token") or payload.get("access_token")

        return token if isinstance(token, str) and token else None

    except Exception:
        return None

    finally:
        connection.close()


# ....................... #


def _store_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    from torve.adapters.store.durable import resolve_dsn
    from torve.application.migrate import MigrateError, pending_count

    config = load_config(root, config_path)

    if config.store.adapter != "postgres":
        return [
            (
                "store",
                True,
                (
                    f"store: {config.store.adapter} — in-process and test-only; "
                    "real runs take a postgres store"
                ),
            )
        ]

    try:
        dsn = resolve_dsn(config.store)

    except RuntimeError as error:
        return [("store", False, str(error))]

    try:
        pending = pending_count("substrate", dsn)

    except MigrateError as error:
        return [("store", False, str(error))]

    except Exception as error:  # the unreachable database is the finding
        return [
            (
                "store",
                False,
                (
                    f"store: postgres named by ${config.store.dsn_env} did not "
                    f"answer: {error} — start the database or fix the DSN"
                ),
            )
        ]

    if pending:
        return [
            (
                "store",
                False,
                (
                    f"store: postgres reachable but {pending} substrate "
                    "step(s) pending — run: torve migrate substrate"
                ),
            )
        ]

    return [("store", True, "store: postgres reachable, substrate schema current")]


# ....................... #


def _broker_check(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """D-21.9: the broker adapter in force is named, and `none` — legal and
    the phase-1 default — is stated plainly to leave the credential-custody
    requirement unmet. What is not legal is `none` by accident."""

    config = load_config(root, config_path)

    if config.broker.adapter == "none":
        return [
            (
                "broker",
                True,
                (
                    "broker: none — provider keys pass through to the sandbox, with "
                    "no metering and no wire routing; this is the default and it "
                    "leaves the credential-custody requirement unmet. Configure "
                    "broker.adapter: local to close it."
                ),
            )
        ]

    routed = ", ".join(sorted(config.broker.providers)) or "none routed"

    return [
        (
            "broker",
            True,
            (
                f"broker: {config.broker.adapter} ({config.broker.mode}) — the runner "
                f"holds the keys and serves one loopback route per routed provider "
                f"({routed}); the sandbox holds none"
            ),
        )
    ]


# ....................... #


def _review_bias_check(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """A reviewer sharing the executor's model reviews its own kind —
    models are biased toward output that looks like theirs, and D-5.1's
    cross-model recommendation exists for exactly this. A warning, never a
    refusal: same-model review is legal and still better than none."""

    config = load_config(root, config_path)

    if not config.review.on:
        return []

    executor = config.tiers.get("executor")
    reviewer = config.tiers.get("reviewer")

    if executor is None or reviewer is None:
        return []

    if executor.adapter == "fake" or reviewer.adapter == "fake":
        return []

    if executor.model and executor.model == reviewer.model:
        return [
            (
                "review",
                True,
                (
                    f"reviewer runs the executor's own model ({executor.model}) — "
                    "a model reviewing its own kind shares its blind spots; "
                    "cross-model review (a different vendor or model on the "
                    "reviewer tier) is the recommended regime"
                ),
            )
        ]

    return []


def _profile_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """D-28.7: provenance only — a resolved profile is named per tier, and no
    check is attached, so this can never turn doctor red. A tier that names
    no profile, or an unreferenced profile file, gets no line at all. A-74:
    `tier.profile` already carries a composed tier's chain in order
    (`"a -> b"`), so the same line renders it with no extra formatting."""

    config = load_config(root, config_path)

    return [
        (
            f"profile {name}",
            True,
            f"tier {name}: adapter, model and command resolved from profile '{tier.profile}'",
        )
        for name, tier in sorted(config.tiers.items(), key=lambda item: item[0])
        if tier.profile
    ]


# ....................... #


def _equipment_checks(root: Path, config_path: Path | None) -> list[tuple[str, bool, str]]:
    """RFC 0029 D-29.5: provenance only — a tier's resolved equipment is named
    when it differs from its role default (`skills` set, or any
    `prompt_extras`), and no check is attached, so this can never turn doctor
    red. A tier that inherits its role's default set and carries no extras
    gets no line."""

    config = load_config(root, config_path)
    checks: list[tuple[str, bool, str]] = []

    for name, tier in sorted(config.tiers.items()):
        parts: list[str] = []

        if tier.skills is not None:
            parts.append(f"skills [{', '.join(tier.skills)}] (override)")

        if tier.prompt_extras:
            n = len(tier.prompt_extras)
            parts.append(f"+{n} prompt extra{'s' if n != 1 else ''}")

        if not parts:
            continue

        checks.append((f"equipment {name}", True, f"tier {name}: {', '.join(parts)}"))

    return checks


# ....................... #


def doctor(
    config_path: ConfigOption = None,
    root: RootOption = Path("."),
    fmt: FormatOption = Format.TEXT,
) -> None:
    """Preflight checks: configuration and environment readiness — the forze
    schema pin, the run store (a postgres store must name a DSN, answer, and
    be migrated), and every configured sandbox image resolvable in the
    runtime with its definition present. A failed check is a configuration
    error (exit 3), not a red gate."""

    from torve.application.migrate import check_forze_pin

    root = root.resolve()
    ok, message = check_forze_pin()
    checks: list[tuple[str, bool, str]] = [("forze-pin", ok, message)]
    checks += _store_checks(root, config_path)
    checks += _broker_check(root, config_path)
    checks += _review_bias_check(root, config_path)
    checks += _profile_checks(root, config_path)
    checks += _equipment_checks(root, config_path)
    checks += _image_checks(root, config_path)
    healthy = all(passed for _, passed, _ in checks)

    if fmt is Format.JSON:
        emit_json(
            {
                "schema_version": 1,
                "ok": healthy,
                "checks": [
                    {"name": name, "ok": passed, "detail": detail}
                    for name, passed, detail in checks
                ],
            }
        )
    else:
        console = out(fmt)

        for _name, passed, detail in checks:
            verdict = mark("pass" if passed else "fail")
            console.print(verdict + Text(f" {detail}", "" if passed else STYLE_FAIL))

    raise typer.Exit(EXIT_OK if healthy else EXIT_CONFIG)
