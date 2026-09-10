"""Plugin seeding, rendered from the declaration (S-0061/D-6).

A profile says which plugins its agent loads, as a source and a ref
(S-0061/D-5). Every harness reads that from files of its own shape, in its
own paths, and torve's contribution is that those files stop being kept by
hand beside a `git checkout` that pins the same ref a third time.

One renderer per harness, keyed by the image — harness identity *is* the
image (S-0017/D-4), and `.torve/sandbox/<name>/` is where that harness's
seeding shape is defined, so the tag's name is the key. A harness with no
renderer and a non-empty list is a refusal: an agent quietly missing its
equipment makes the telemetry lie, which is S-0009/D-2's rule for skills read
for plugins.

Nothing here fetches anything. The image clones at its pinned refs during the
build, because a fetch at dispatch would put the network inside every attempt;
what this writes is only the bookkeeping that says which of those clones are
installed and enabled.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Callable, Sequence

from torve.config.agents import Plugin

# ----------------------- #

# Where a harness's HOME seed lives in the image (S-0033's toolkit path). The
# command copies it into HOME at run time, so writing here reaches the agent
# without the adapter having to know what HOME resolved to.
SEED_ROOT = "/opt/torve/seed"


SANDBOX_SUFFIX = "-sandbox"


def harness_kind(image: str) -> str:
    """The harness an image is: the definition directory under `sandboxes/`
    it was built from (S-0063/D-6).

    `claude-sandbox:2.1.252` and `ghcr.io/morzecrew/claude-sandbox:2.1.252`
    are the same harness — publishing changes the repository prefix and the
    version, and the name in the middle is what survives the move.

    The `-sandbox` suffix is what makes this answerable at all. The old
    `torve-agent:<name>` spelling put the name in the version position, so
    every other image published under a `torve-agent` repository — the
    engine's own among them — read as a harness called by its version. An
    image that is not a sandbox this repository defines answers nothing,
    which is the right answer for a stock base or a third party's image.
    """

    if not image:
        return ""

    tag = image.rsplit("@", 1)[0]  # a digest pin carries the tag before it
    repository = posixpath.basename(tag).partition(":")[0]

    return repository.removesuffix(SANDBOX_SUFFIX) if repository.endswith(SANDBOX_SUFFIX) else ""


# ....................... #


def plugin_name(plugin: Plugin) -> str:
    """What the harness calls this plugin: the last segment of its source.

    `github:JuliusBrussee/caveman` is `caveman`. The source's own spelling is
    never parsed for anything else — torve does not resolve it, and the ref
    is whatever the source pins with (S-0061/D-5).
    """

    return plugin.source.rstrip("/").rsplit("/", 1)[-1]


def _marketplace_repo(plugin: Plugin) -> str:
    """The `owner/repo` half of a github source, for the marketplace entry."""

    return plugin.source.split(":", 1)[1] if ":" in plugin.source else plugin.source


# ....................... #


def _claude_seed(plugins: Sequence[Plugin]) -> dict[str, str]:
    """Claude Code's three files, from the list.

    They were kept by hand until now, and the install path and version string
    in `installed_plugins.json` had to agree with a `cp` target and an `ARG`
    in the Dockerfile with nothing checking that they did — ponytail's ref was
    `v4.9.0` and its cache directory `4.9.0`. Both come off the same `ref`
    here, so they cannot disagree.
    """

    claude = f"{SEED_ROOT}/.claude"
    cache = f"{claude}/plugins/cache"
    marketplaces = f"{claude}/plugins/marketplaces"

    installed: dict[str, list[dict[str, str]]] = {}
    known: dict[str, dict[str, object]] = {}
    enabled: dict[str, bool] = {}

    for plugin in plugins:
        name = plugin_name(plugin)
        key = f"{name}@{name}"
        enabled[key] = True
        installed[key] = [
            {
                "scope": "user",
                "installPath": f"{cache}/{name}/{name}/{plugin.ref}",
                "version": plugin.ref,
            }
        ]
        known[name] = {
            "source": {"source": "github", "repo": _marketplace_repo(plugin)},
            "installLocation": f"{marketplaces}/{name}",
        }

    return {
        f"{claude}/settings.json": _json({"enabledPlugins": enabled}),
        f"{claude}/plugins/installed_plugins.json": _json({"version": 2, "plugins": installed}),
        f"{claude}/plugins/known_marketplaces.json": _json(known),
    }


def _json(payload: object) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# ....................... #

RENDERERS: dict[str, Callable[[Sequence[Plugin]], dict[str, str]]] = {
    "claude": _claude_seed,
}


class UnseededHarness(RuntimeError):
    """A seat declares plugins for a harness nothing knows how to seed.

    Refused rather than run without them (S-0061/D-6): an attempt that
    quietly lost its equipment measures a regime nobody configured, and the
    record would say it ran with plugins it never had.
    """


def seed_files(image: str, plugins: Sequence[Plugin]) -> dict[str, str]:
    """Container path -> contents, for every file this harness's own installer
    reads. Empty when nothing is declared, whatever the harness."""

    if not plugins:
        return {}

    kind = harness_kind(image)
    render = RENDERERS.get(kind)

    if render is None:
        named = ", ".join(sorted(RENDERERS)) or "none"
        raise UnseededHarness(
            f"the image {image!r} is not a harness torve can seed plugins for "
            f"(it can seed: {named}) — drop the profile's plugins, or teach this "
            "harness its own seeding format"
        )

    return render(plugins)
