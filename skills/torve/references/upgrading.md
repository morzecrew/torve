# Upgrading a repository's torve

A repository installed torve as a tool, not a project dependency, and named
the release it needs with `engine`. Upgrading is installing the new release
over the old one, moving `engine` to it, and re-deriving what the release
changed. One release to the next, in order:

1. **Install the release.** The configuration's `engine` range is below
   `X.(Y+1).0`, so a pin is what keeps an upgrade deliberate:

   ```bash
   uv tool install --force 'torve[postgres,migrate]==X.Y.Z'
   ```

   `--force` is the point: `uv tool upgrade torve` keeps the exact pin a
   repository installed, so it never crosses a release. Name the version the
   release note names, with the extras the repository uses.
2. **Raise `engine`.** In `.torve/config.yaml`, set `engine: "X.Y.Z"` to the
   release just installed. A command that loads the configuration under a
   torve outside the range stops, naming the installed version, the range and
   the install command for a release inside it — so a stale install fails
   before it serves a night rather than after.
3. **Rebuild the seat images.** `just images` on a checkout of the release
   stamps every image with that tree's version. `torve doctor` runs each
   seat image's `torve --version` and goes red when it differs from the
   host's; rebuild before a night spends an attempt finding out.
4. **Run `torve init`.** It rewrites `.torve/schemas/` from the engine you
   just installed and adds any ignore pattern minted since. It never
   overwrites a file that exists: a configuration, a gate manifest or a role
   profile a repository owns is left alone, even under `--starter`.
5. **Migrate the record** when the release's Upgrade note names one:
   `torve migrate --all` against the store.
6. **Run `torve doctor`** and read the red lines: the version lock, the
   schemas lagging the engine, the seat images, the store.
7. **Commit** what `torve init` and the configuration changed, so every later
   run and every CI job reads the schemas the installed engine wrote.

A repository upgrading from 0.3 or earlier copied the hooks into
`.torve/agents/hooks/implement/`, and its implement profile names that copy
(`source: local:.torve/agents/hooks/implement`). The hooks now ship in the
package, and `torve init` does not touch an existing profile, so edit the
profile's hook item by hand:

```yaml
- kind: hook
  source: torve:hooks/implement
```

and delete the copied `.torve/agents/hooks/implement/` directory.

A release whose changelog entry carries no Upgrade note moves no format, adds
no record migration and changes no hooks; steps 1, 2, 4, 6 and 7 are then the
whole upgrade. A release that moves a `schema_version`, adds a record
migration or changes the hooks carries an Upgrade note naming what moved on
either side and the conversion.

The stability page on the documentation site is the same steps for an adopter;
`torve guide torve` lists this reference beside the others.