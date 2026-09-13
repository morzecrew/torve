"""Turn the equipment manifest into the session flags claude takes (S-0063/D-3).

A real file rather than a heredoc, so the thing the image ships is the thing
a test can run.

    python3 equip_flags.py <manifest.json> <args-file>
"""

import json
import os
import shlex
import shutil
import sys

# One flag per kind, for the kinds a session flag actually reaches. A kind that
# is not here is a kind this harness has no channel for, and the engine refused
# the profile that declared it before the sandbox existed (S-0063/D-4) — so an
# unknown kind here is a bug, not input.
#
# `skill` is deliberately absent. `--add-dir` adds a directory to the working
# set and does *not* load skills: measured, a session given five skills through
# it listed only claude's built-ins. Skills load from a skills root, so they are
# copied to the one the engine named.
FLAG = {
    "plugin": "--plugin-dir",
    "mcp": "--mcp-config",
    "hook": "--settings",
    "agent": "--agents",
}

manifest, args = sys.argv[1], sys.argv[2]
root = os.path.expanduser(os.environ.get("TORVE_EQUIP_ROOT", ""))

with open(manifest, encoding="utf-8") as handle:
    items = json.load(handle).get("items", [])

words = []

for item in items:
    kind, path = item["kind"], item["path"]

    if kind == "skill":
        if not root:
            raise SystemExit(
                "this harness loads skills from a skills root and the engine named no "
                "`TORVE_EQUIP_ROOT` — the manifest owes an `equip_root` (S-0063/D-19)"
            )

        os.makedirs(root, exist_ok=True)
        shutil.copytree(path, os.path.join(root, os.path.basename(path)), dirs_exist_ok=True)
        continue

    if kind == "hook":
        # `--settings` reads a file; every other flag here reads the directory the
        # item was fetched into. A hook item is a directory because the settings
        # reference scripts beside them by name, so the flag points at the one
        # file and the scripts stay reachable from it.
        path = os.path.join(path, "settings.json")

        if not os.path.isfile(path):
            raise SystemExit(
                f"hook item {item['kind']!r} has no settings.json at {path} — a hook is a "
                "directory holding the settings and the scripts they name"
            )

    words += [FLAG[kind], path]

with open(args, "w", encoding="utf-8") as handle:
    handle.write(" ".join(shlex.quote(word) for word in words))
