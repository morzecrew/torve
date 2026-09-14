"""Turn the equipment manifest into what dsh takes (S-0063/D-3).

A real file rather than a heredoc, so the thing the image ships is the thing
a test can run.

    python3 equip_patch.py <manifest.json> <overlay.yml>
"""

import json
import os
import shutil
import subprocess
import sys

manifest, overlay = sys.argv[1], sys.argv[2]

with open(manifest, encoding="utf-8") as handle:
    items = json.load(handle).get("items", [])

# Where the engine told this harness to put equipment (S-0063/D-19). dsh's
# skill plugin watches `.agents/skills` and `.dsh/skills`; the first is the
# convention a repository keeps its *own* reviewed skills in — writing there
# overwrites them, and `git add -A` commits the overwrite before any gate can
# object. So the manifest names the second and the engine excludes it.
SKILLS = os.environ.get("TORVE_EQUIP_ROOT") or ""

# dsh's half of the hook kind (S-0072/D-3): a thin plugin on the `fs/*` event
# gate that ships in this image — `toolkit/scope-guard-policy/` lands here by
# the one COPY (S-0063/D-14) — and whose whole body shells out to the hook
# item's own `scope_guard.py`, the same script claude's settings.json names
# (S-0072/D-2). It is installed through dsh's own verb, and the `insert` below
# turns the installed package on: `--patch` only patches entries the profile
# already carries, so the row's `name` must already resolve — which is what
# the install's pnpm write provides (S-0063/D-17).
HOOK_PLUGIN = "/opt/torve/scope-guard-policy"
HOOK_PACKAGE = "@torve/scope-guard-policy"
HOOK_ENTRY = f"- insert:\n    - id: scope-guard-policy\n      name: '{HOOK_PACKAGE}'\n"

fragments = []

for item in items:
    kind, path = item["kind"], item["path"]

    if kind == "skill":
        if not SKILLS:
            raise SystemExit(
                "this harness reads skills from the workspace and the engine named no "
                "`TORVE_EQUIP_ROOT` — the manifest owes an `equip_root` (S-0063/D-19)"
            )

        target = os.path.join(SKILLS, os.path.basename(path.rstrip("/")))
        shutil.copytree(path, target, dirs_exist_ok=True)
        continue

    if kind == "plugin":
        # Installed before it is patched, because `--patch` cannot add an entry
        # (S-0063/D-17). pnpm reads the mount, never the registry: the fetch
        # already happened host-side (S-0062/D-4).
        subprocess.run(
            ["dsh", "plugin", "--profile", "headless", "add", path],
            check=True,
        )
        continue

    if kind == "hook":
        # S-0072/D-3: the hook's declaration for dsh is the scope-guard plugin
        # already in this image, installed here through dsh's own verb and then
        # patched in by the row above. The item's own bytes are not read: the
        # plugin finds its script on the mount at runtime, and the `insert`
        # directive (the same one bundle patches use to add rows) is what lets
        # a patch reach a package equipped in this same pass — the configure
        # form cannot add an entry the profile does not carry (S-0063/D-17).
        subprocess.run(
            ["dsh", "plugin", "--profile", "headless", "add", HOOK_PLUGIN],
            check=True,
        )
        fragments.append(HOOK_ENTRY)
        continue

    # Every other kind is a patch the operator wrote in dsh's own vocabulary:
    # an MCP server and an agent are plugin entries here, and a document torve
    # invented would be a schema it does not know. It must name an entry the
    # profile already carries — `equip` cannot check that without booting dsh,
    # so a wrong one fails at boot with dsh's own message, which names the
    # entry and is clearer than anything repeated here.
    with open(path, encoding="utf-8") as handle:
        fragments.append(handle.read().rstrip("\n"))

if fragments:
    with open(overlay, "w", encoding="utf-8") as handle:
        handle.write("\n".join(fragments) + "\n")
