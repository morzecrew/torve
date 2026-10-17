# The build (S-0063/D-8). One target per definition under `sandboxes/`, each
# inheriting the base as a named context so the layer they used to duplicate
# is a real dependency rather than a test holding six copies in step.
#
# `just images` builds them all; `just image claude` builds one. The engine
# never builds — `RuntimePort.build_image` retired with S-0063/D-11, and
# `torve sandbox list` and `digest` are what it kept.

variable "REGISTRY" {
  # Empty builds local tags. Set it to publish: REGISTRY=ghcr.io/morzecrew
  default = ""
}

variable "TAG" {
  default = "latest"
}

variable "VERSION" {
  # S-0110/D-4: the release the tree being built is at, stamped onto every
  # image through SETUPTOOLS_SCM_PRETEND_VERSION. The build has no `.git`, so
  # without it hatch-vcs wrote a stale version and an image reported another
  # torve than the host. `just images` fills it with the release setuptools-scm
  # renders for the tree, and refuses to build when that render is empty.
  default = ""
}

function "name" {
  params = [image]
  result = REGISTRY == "" ? "${image}:${TAG}" : "${REGISTRY}/${image}:${TAG}"
}

group "default" {
  targets = ["battery", "claude", "codex", "dsh", "mimo", "opencode"]
}

# Every agent image, without the gates-side one: `just images agents`.
group "agents" {
  targets = ["claude", "codex", "dsh", "mimo", "opencode"]
}

# The context is the repository root, because this target bakes the project
# (S-0063/D-7); `.dockerignore` is what keeps the worktree and the venv out.
target "base" {
  context    = "."
  dockerfile = "sandboxes/base/Dockerfile"
  tags       = [name("sandbox-base")]
  args       = { SETUPTOOLS_SCM_PRETEND_VERSION = VERSION }
}

# A definition's context is its own directory — it holds the Dockerfile and
# whatever that harness ships beside it, and nothing else belongs in it.
target "_definition" {
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
}

target "codex" {
  inherits   = ["_definition"]
  context    = "sandboxes/codex"
  tags       = [name("codex-sandbox")]
}

target "dsh" {
  inherits   = ["_definition"]
  context    = "sandboxes/dsh"
  tags       = [name("dsh-sandbox")]
}

target "mimo" {
  inherits   = ["_definition"]
  context    = "sandboxes/mimo"
  tags       = [name("mimo-sandbox")]
}

target "opencode" {
  inherits   = ["_definition"]
  context    = "sandboxes/opencode"
  tags       = [name("opencode-sandbox")]
}
