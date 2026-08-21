# INDEX.md — generated, never authored

`INDEX.md` is **output** (charter D-A.6): `rfc_index.py generate` builds it
from each RFC's YAML frontmatter, and `rfc_index.py check` fails CI when the
committed file differs from what `generate` writes — the same discipline as a
lockfile. There is no skeleton to copy: initializing an RFC directory means
writing the first RFC (with frontmatter) and running `generate`.

What the generated file contains, all derived:

- the **next free number**, recomputed from the files actually present;
- one row per `NNNN-*.md` file: number linked to the file, `title`, `status`,
  `depends_on`, amendment count, and the frontmatter `description` as the
  one-line routing description.

## Notes

- **The description routes, it does not summarise.** One sentence naming the
  problem and the shape of the answer — enough to tell this design apart from
  the others, and no more. Aim for 200 characters, ceiling 300. What the RFC
  decides, how it works and what it excluded belong in the RFC.
- **Never record history in the index.** No shipped dates, no phase progress,
  no defects found. The status field carries state; the RFC carries its own
  story. History in an index turns every lookup into reading a changelog.
- **Task logs never get rows.** They are not designs and have no numbers;
  `rfc_index.py` ignores them — only `NNNN-*.md` files are RFCs.
- **Hand edits are a CI failure by design.** If the index looks wrong, fix the
  frontmatter it is derived from and regenerate.
