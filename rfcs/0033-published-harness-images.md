---
id: "0033"
title: Published harness images
kind: design
status: draft
depends_on: ["0017"]
informed_by: ["0003", "0004", "0021", "0028"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  The in-repo sandbox definitions for claude, dsh and mimo published to ghcr.io with immutable version tags and a thin baked-in toolkit — a convenience reference, never a requirement, with the digest staying the only identity.
schema_version: 1
---

# RFC 0033 — Published harness images

- **Scope:** Publishing the harness sandbox images this repository already
  defines — `claude`, `dsh`, `mimo` — to `ghcr.io/morzecrew/`, and the
  conventions that keep publishing honest: the tag scheme, harness version
  pins as build arguments, the `/opt/torve/` utilities layer the images
  bake in, the CI publish job, and the doctor line that resolves a remote
  reference to the digest that actually governs. Changes no engine
  behavior: `tier.image` accepts any reference today and identity is
  already the digest (RFC 0017 D-17.1); this document adds a pipeline and
  a doctrine, not a mechanism. Excludes the battery and opencode images
  (unpublished until their history earns a slot), image signing (§8), and
  any engine feature conditioned on an in-house image — that exclusion is
  a LOCKED row, not a phase.
- **Related:** [`0017`](0017-sandbox-provisioning.md) §2–3 (identity is
  the image; digest joins the hash) · [`0003`](0003-runner-isolation.md)
  (the OpenSandbox runtime cannot build — it pulls) ·
  [`0028`](0028-agent-profiles.md) (a ghcr reference in a host profile is
  the zero-config story) · `.torve/sandbox/` · `.github/workflows/ci.yml` ·
  [morzecrew/platform-images](https://github.com/morzecrew/platform-images)
  (prior art for the org's publish pipelines)
- **Origin:** Two live incidents in one day: an unpinned `npm install -g
  @deepseek-ai/dsh` made a routine rebuild a silent harness upgrade, and
  the OpenSandbox live integration hit the adapter's own refusal —
  "build with the docker runtime and push to a registry the server can
  pull from" — with nothing published to pull.

---

## 1. Summary

CI builds the three harness definitions this repository already tests and
pushes them to `ghcr.io/morzecrew/torve-agent-<name>` with immutable
`<harness-version>-r<image-rev>` tags plus a convenience `latest`. Harness
versions become pinned-default build arguments, so one definition serves
many versions and no rebuild silently upgrades anything. Each image bakes
a thin `/opt/torve/` toolkit — the usage reporter and seed helper that
already exist, formalized — which shortens the command templates a profile
carries. A consuming configuration points `image:` at the ghcr reference
and configures nothing else; a suspicious operator builds the identical
image locally from the identical definition with one command. The digest
remains the only identity the engine trusts.

## 2. Motivation

- **The OpenSandbox runtime requires a registry.** Its adapter refuses
  `build_image` by design; live-server work (RFC 0003, condition fired
  2026-08-31) is image-delivery-blocked until something is published.
- **Zero-config onboarding is otherwise a lie.** RFC 0028's profiles made
  the wiring one reference — but the reference points at a local image the
  operator must build from a definition they must copy. A ghcr reference
  plus a profile is the first genuinely configuration-free tier.
- **Version drift is live, not hypothetical.** The dsh image rebuilt with
  an unpinned npm install and silently jumped to 0.1.1-rc.2 mid-campaign;
  the pin landed as a hotfix (f68f4e7). Build arguments with pinned
  defaults are that fix as a convention instead of a memory.
- **The toolkit already exists, undeclared.** `report-usage.js` and the
  claude-seed copy ship in images and are load-bearing for telemetry —
  they deserve a named home and a versioning story instead of living as
  incidental COPY lines.

## 3. Current state

- Five definitions under `.torve/sandbox/` (`battery`, `claude`, `dsh`,
  `mimo`, `opencode`); `torve sandbox build` builds them and reports
  digests; the digest joins `config_hash` at dispatch (D-17.1), so a
  mutated tag cannot silently merge regimes — verified doctrine, not
  intent.
- `tier.image` and `runtime.image` accept arbitrary references already;
  the docker runtime uses what the daemon resolves. No engine change is
  needed for a remote reference to work.
- dsh's Dockerfile pins `@deepseek-ai/dsh@0.1.1-rc.2` inline (the
  hotfix); claude's and mimo's harness versions are unpinned installs.
- `.github/workflows/ci.yml` exists; no publish job.
- The images already carry utilities ad hoc: `/opt/dsh/report-usage.js`,
  `/opt/dsh/*.yml` overlays, `/opt/claude-seed/`.

## 4. Goals / Non-goals

**Goals**

- A consuming repository (or this one) configures a tier with a ghcr
  reference and builds nothing.
- Every published tag is immutable and names its harness version; every
  rebuild is a deliberate, diffable bump commit.
- The toolkit is a named, versioned convention rather than incidental
  COPY lines.
- Local build parity: the published image and the local build come from
  one definition.

**Non-goals**

- **Requiring these images.** No engine behavior may ever be conditioned
  on an in-house image (D-33.1). A stock upstream image plus a longer
  command template must always work.
- **Publishing the battery.** It mounts the docker socket in dogfood and
  its contents are repository-shaped; publishing it invites using it as a
  base image, which is a support surface this document refuses.
- **Signing and attestation.** Named in §8 with the trust statement made
  plainly instead; a signature ceremony without a verification consumer
  is theater.
- **Auto-rebuild on upstream releases.** A harness release becomes an
  image only through a human bump commit — a regime change is chosen,
  never scheduled.

## 5. Design

### 5.1 Names and tags

`ghcr.io/morzecrew/torve-agent-claude`, `…-dsh`, `…-mimo`.

Tags: `<harness-version>-r<image-rev>` (e.g. `0.1.1-rc.2-r1`), immutable
by convention and by CI refusing to re-push an existing tag; `latest`
floats as a convenience. The image revision bumps when the definition
changes under an unchanged harness version (a toolkit fix, a base bump).
The engine's identity story is untouched: whatever tag a configuration
names, the digest is resolved at dispatch and joins `config_hash` —
D-17.1 already guarantees two digests under one tag are two regimes.

### 5.2 Versions as build arguments

Each Dockerfile takes its harness version as an `ARG` with a pinned
default:

```dockerfile
ARG DSH_VERSION=0.1.1-rc.2
RUN npm install -g @deepseek-ai/dsh@${DSH_VERSION}
```

The default is the published version; CI passes nothing. A bump is a
one-line diff a reviewer reads as "this is a harness upgrade". claude and
mimo gain the same shape for their installers (D-33.3) — closing the
unpinned installs §3 names.

### 5.3 The `/opt/torve/` toolkit

The existing utilities move under one directory with one convention:

- `/opt/torve/report-usage` — the usage reporter (dsh's today; a
  per-harness implementation where a harness needs one).
- `/opt/torve/seed` — session seeding (claude's `cp -r /opt/claude-seed`
  line, named).
- Overlay/profile fragments stay beside them (`/opt/torve/overlays/…`).

Rules that keep the toolkit thin: every utility is a single file, does
one thing a command template would otherwise inline, and takes its
variability from environment — never from parsing the contract or
touching `.torve/`. A utility that wants task context has become a
harness feature and belongs upstream or in the engine, not in an image
(D-33.4). Command templates in profiles shorten accordingly; the old
paths keep working through the transition via symlinks in the same image
revision that introduces the new ones.

### 5.4 The publish job

A CI workflow in this repository (the definitions' home — publishing from
platform-images would fork the source of truth): buildx on tag-shaped
triggers or manual dispatch, `linux/amd64` only until the lab needs more,
push to ghcr with the §5.1 scheme, and a job step that fails on tag
reuse. The sandbox conformance battery remains the gate: what CI
publishes is what the tests built.

### 5.5 Doctor

`torve doctor` already prints local image digests; a tier naming a remote
reference gains the same line with the resolved digest, so "what exactly
will run" has one answer for both kinds of reference. No new check —
resolution failure already fails dispatch loudly.

### Alternatives considered

- **Publishing from platform-images.** The org's image repo is the
  natural publisher — and the wrong one here: the definitions are tested
  by this repository's conformance battery, and moving the Dockerfiles
  splits build-source from test-source permanently. platform-images
  remains prior art and base-image supplier.
- **One mega-image with all harnesses.** Smaller matrix, and it destroys
  the identity doctrine — harness identity is *the image* (D-17.4), and a
  shared image makes every harness upgrade a regime change for all three.
- **Mutable version tags (`:0.1.1`).** Refused: D-17.1 protects the
  engine from tag mutation, but humans read tags, and a re-pushed tag
  lies to them even when the hash catches it.

## 6. Tests

- The conformance battery keeps gating the definitions (unchanged — it is
  the publish gate).
- A definition test asserting each Dockerfile's harness install goes
  through a pinned-default `ARG` (a regex-level check, cheap and honest
  about what it is).
- Toolkit contract test per image: the container answers
  `/opt/torve/report-usage --help` (or equivalent existence checks) so a
  publish cannot drop a utility a profile depends on.
- The publish workflow itself is exercised by publishing — CI dry-run
  modes are a simulation nobody maintains.

## 7. Docs

README configuration section gains the ghcr reference example beside the
profile example — the two together are the zero-config quickstart. The
trust statement, plainly: pulling these images trusts this repository's CI
and the ghcr account; operators who prefer not to build locally from the
same definitions with `torve sandbox build`.

## 8. Out of scope

- **Signing/attestation** — the escape hatch is named: it arrives when a
  consumer exists that verifies, likely alongside the first external
  adopter.
- **arm64** — one flag in the buildx step when the first arm host
  appears.
- **Publishing battery or opencode** — battery for the §4 reason;
  opencode when its shadow rerun earns a telemetry-backed slot.
- **A torve-side pull/cache verb** — the daemon and the server already
  own pulling; a wrapper verb would be ceremony.

## 9. Risks

- **The toolkit calcifies into an API.** Profiles across machines start
  depending on `/opt/torve/*` paths; a rename breaks them silently.
  Mitigated by the symlink transition rule (§5.3) and by the toolkit
  contract test (§6); accepted residual: the toolkit is versioned by the
  image revision, and that is what image revisions are for.
- **`latest` misread as a pin.** The README example uses a full version
  tag, never `latest`; `latest` exists for kicking tires.
- **ghcr outage blocks dispatch** for configurations naming remote
  references. Accepted plainly: the mitigation is the one-command local
  build parity (D-33.1's consequence), not a mirror.

## 10. Unresolved questions

- D-33.6 (below): whether published images embed their definition's git
  sha as a label for provenance-by-inspection; the first debugging
  session against a pulled image settles how much it is worth.

## 11. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-33.1 | `LOCKED` | Published images are a convenience, never a requirement: no engine behavior conditions on an in-house image, every published image builds locally from its in-repo definition with one command, and a stock upstream image plus a longer command template always works | `.torve/sandbox/**` | The moment an engine feature needs an in-house image, the images stop being infrastructure and become a moat — including for ourselves |
| D-33.2 | `LOCKED` | Tags are immutable `<harness-version>-r<image-rev>` and CI refuses to re-push an existing tag; `latest` floats as convenience; the digest remains the only identity the engine trusts (D-17.1 unchanged) | `.github/workflows/**` | Humans read tags even though the engine reads digests; a re-pushed tag lies to the reader the hash cannot protect |
| D-33.3 | `ASSUMED` | Every harness install in a published definition goes through an `ARG` with a pinned default; a version bump is a one-line reviewed diff, never a rebuild side effect | `.torve/sandbox/**` | The f68f4e7 incident as a convention: a rebuild that upgrades a harness silently is a regime change nobody chose |
| D-33.4 | `ASSUMED` | The `/opt/torve/` toolkit is thin by rule: single-file utilities, variability from environment only, no reading of contracts or `.torve/`; a utility wanting task context has outgrown the image | `.torve/sandbox/**` | Toolkit-as-API is the calcification risk; the rule keeps the interface small enough to version by image revision |
| D-33.5 | `ASSUMED` | Publishing happens from this repository's CI, gated by the same conformance battery that tests the definitions; platform-images supplies bases and prior art, never these definitions | `.github/workflows/**` | Splitting build-source from test-source is permanent drift; the battery gating the publish is what makes a pulled image mean something |
| D-33.6 | `OPEN` | Whether images carry their definition's git sha as an OCI label for provenance-by-inspection; the first debugging session against a pulled image settles it | `.torve/sandbox/**` | Cheap to add, worthless until someone actually inspects one |

## 12. Phasing

```yaml
- phase: 1
  title: toolkit-and-args
  intent: >-
    The three publishable definitions converge on the conventions: harness installs behind pinned-default ARGs (D-33.3), the existing utilities moved under /opt/torve with symlinks at their old paths for one transition revision (D-33.4), and the definition-level tests — the ARG-pin check and the toolkit existence checks — joining the sandbox battery. Profile command templates in the README examples shorten to the toolkit paths.
  scope:
    - ".torve/sandbox/claude/**"
    - ".torve/sandbox/dsh/**"
    - ".torve/sandbox/mimo/**"
    - "tests/test_sandbox_images.py"
    - "README.md"
  acceptance:
    - "uv run pytest tests/test_sandbox_images.py"
    - "uv run ruff check ."
  depends_on: []
- phase: 2
  title: publish-workflow
  intent: >-
    The ghcr publish job: buildx over the three definitions on manual or tag-shaped triggers, the immutable tag scheme with re-push refusal (D-33.2), conformance battery as the gate (D-33.5), linux/amd64 only. Doctor's image line covers remote references with their resolved digest. README gains the ghcr quickstart with a full version tag.
  scope:
    - ".github/workflows/**"
    - "src/torve/cli/doctor.py"
    - "tests/test_cli.py"
    - "README.md"
  acceptance:
    - "uv run pytest tests/test_cli.py"
    - "uv run mypy src"
    - "uv run basedpyright src"
    - "uv run ruff check ."
  depends_on: [1]
```
