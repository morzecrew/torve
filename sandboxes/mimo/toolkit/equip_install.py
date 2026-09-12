"""Install the manifest's equipment the way mimo takes it (S-0063/D-3).

mimo has no session channel, so equipment is installed state rather than
flags. A real file rather than a heredoc, so the thing the image ships is the
thing a test can run.

    python3 equip_install.py <manifest.json>
"""

import json
import os
import shutil
import subprocess
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    items = json.load(handle).get("items", [])

# Where the engine told this harness to put equipment (S-0063/D-19). mimo reads
# skills from `<project>/.mimocode/skill/<name>/SKILL.md` — project-scoped, since
# a global one is not read (S-0063/D-16) — and the manifest names that root so the
# engine can exclude it from the commit.
SKILLS = os.environ.get("TORVE_EQUIP_ROOT") or ""

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

    if kind != "plugin":
        # The manifest refused every other kind before the sandbox existed, so
        # one here is a bug in the refusal rather than an operator's mistake.
        raise SystemExit(f"mimo takes no {kind!r} equipment: {item['source']}")

    # From the mount, never the registry: the fetch already happened host-side
    # (S-0062/D-4), and an attempt that reaches npm is an attempt whose
    # failures include npm's.
    subprocess.run(["mimo", "plugin", path], check=True)
