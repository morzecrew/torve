# The build (S-0063/D-8). One target per definition under `sandboxes/`, each
# inheriting the base as a named context so the layer they used to duplicate
# is a real dependency rather than a test holding six copies in step.
#
# `just images` builds them all; `just image claude` builds one. The engine
# never builds — `RuntimePort.build_image` retired with S-0063/D-11, and
# `torve sandbox list` and `digest` are what it kept.
#
# S-0112/D-3: every target, the base included, is tagged with the version the
# build stamps, written as a tag; `:latest` is gone, so what `just images`
# produces is exactly what a `{torve}` reference resolves to. S-0112/D-5:
# every image carries the source, version and revision labels, and a seat
# image the version its own harness prints.

variable "REGISTRY" {
  # Empty builds local tags. Set it to publish: REGISTRY=ghcr.io/morzecrew
  default = ""
}

variable "VERSION" {
  # S-0110/D-4: the release the tree being built is at, stamped onto every
  # image through SETUPTOOLS_SCM_PRETEND_VERSION. The build has no `.git`, so
  # without it hatch-vcs wrote a stale version and an image reported another
  # torve than the host. `just images` fills it with the release setuptools-scm
  # renders for the tree, and refuses to build when that render is empty.
  default = ""
}

variable "REVISION" {
  # S-0112/D-5: the commit the images are built from — `just images` passes
  # `git rev-parse HEAD`. The label is what a tag that moves cannot carry.
  default = ""
}

variable "SOURCE" {
  # S-0112/D-5: the repository every image names, so a pulled image says where
  # it was built without its definition beside it.
  default = "https://github.com/morzecrew/torve"
}

# S-0112/D-5: the version each seat's harness prints, carried into its label
# so a pulled seat says which harness it carries. The definition's own ARG
# (S-0112/D-7) is the one source: every entry point that bakes passes it in
# through `.github/scripts/harness-versions.sh`, so a Renovate bump of the ARG
# moves the label too. A bake run without them labels an empty version.
variable "CLAUDE_VERSION" {
  default = ""
}

variable "CODEX_VERSION" {
  default = ""
}

variable "DSH_VERSION" {
  default = ""
}

variable "MIMO_VERSION" {
  default = ""
}

variable "OPENCODE_VERSION" {
  default = ""
}

function "name" {
  params = [image]
  result = REGISTRY == "" ? "${image}:${replace(VERSION, "+", "-")}" : "${REGISTRY}/${image}:${replace(VERSION, "+", "-")}"
}

group "default" {
  targets = ["base", "battery", "claude", "codex", "dsh", "mimo", "opencode"]
}

# Every agent image, without the gates-side one: `just images agents`.
group "agents" {
  targets = ["claude", "codex", "dsh", "mimo", "opencode"]
}

# S-0112/D-5: the three labels every image carries.
target "_labelled" {
  labels = {
    "org.opencontainers.image.source"   = SOURCE
    "org.opencontainers.image.version"  = VERSION
    "org.opencontainers.image.revision" = REVISION
  }
}

# The context is the repository root, because this target bakes the project
# (S-0063/D-7); `.dockerignore` is what keeps the worktree and the venv out.
target "base" {
  inherits   = ["_labelled"]
  context    = "."
  dockerfile = "sandboxes/base/Dockerfile"
  tags       = [name("sandbox-base")]
  args       = { SETUPTOOLS_SCM_PRETEND_VERSION = VERSION }
}

# A definition's context is its own directory — it holds the Dockerfile and
# whatever that harness ships beside it, and nothing else belongs in it.
target "_definition" {
  inherits = ["_labelled"]
  contexts = { base = "target:base" }
  args     = { BASE = "base" }
}

target "battery" {
  inherits   = ["_definition"]
  context    = "."
  dockerfile = "sandboxes/battery/Dockerfile"
  tags       = [name("battery-sandbox")]
}

target "claude" {
  inherits   = ["_definition"]
  context    = "sandboxes/claude"
  tags       = [name("claude-sandbox")]
  labels     = { "io.morze.torve.harness.version" = CLAUDE_VERSION }
}

target "codex" {
  inherits   = ["_definition"]
  context    = "sandboxes/codex"
  tags       = [name("codex-sandbox")]
  labels     = { "io.morze.torve.harness.version" = CODEX_VERSION }
}

target "dsh" {
  inherits   = ["_definition"]
  context    = "sandboxes/dsh"
  tags       = [name("dsh-sandbox")]
  labels     = { "io.morze.torve.harness.version" = DSH_VERSION }
}

target "mimo" {
  inherits   = ["_definition"]
  context    = "sandboxes/mimo"
  tags       = [name("mimo-sandbox")]
  labels     = { "io.morze.torve.harness.version" = MIMO_VERSION }
}

target "opencode" {
  inherits   = ["_definition"]
  context    = "sandboxes/opencode"
  tags       = [name("opencode-sandbox")]
  labels     = { "io.morze.torve.harness.version" = OPENCODE_VERSION }
}