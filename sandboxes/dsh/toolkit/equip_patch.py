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

    # Every other kind is a patch the operator wrote in dsh's own vocabulary:
    # an MCP server, a hook and an agent are all plugin entries here, and a
    # document torve invented would be a schema it does not know. It must name
    # an entry the profile already carries — `equip` cannot check that without
    # booting dsh, so a wrong one fails at boot with dsh's own message, which
    # names the entry and is clearer than anything repeated here.
    with open(path, encoding="utf-8") as handle:
        fragments.append(handle.read().rstrip("\n"))

if fragments:
    with open(overlay, "w", encoding="utf-8") as handle:
        handle.write("\n".join(fragments) + "\n")
