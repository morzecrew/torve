# Security policy

## Reporting a vulnerability

Do not open a public issue. Report it privately through GitHub's private
vulnerability reporting:
<https://github.com/morzecrew/torve/security/advisories/new>.

Include:

- what you found;
- how to reproduce it;
- the Torve version or commit;
- the relevant part of your `.torve/config.yaml`, with secrets removed.

## Supported versions

Only the latest 0.x release receives fixes. Torve is an alpha, and fixes
are not backported.

## The trust boundary

Torve runs coding agents, which execute whatever code a model chose, on the
operator's machine. The operator's credentials sit close by. This section
says what 0.2 defends and what it does not. Read both halves: a reader who
believes a boundary is stronger than it is ends up worse off than one who
knows it is absent.

### What Torve defends

- **Agent code runs in a container, never on the host.** Each attempt runs
  in a Docker container as the invoking user's uid, not root. The container
  mounts the task's worktree read-write and, when declared, a read-only
  equipment cache and a cache volume. During a run, gates and acceptance
  commands run in a container too.
- **Provider keys stay with the runner when the broker is on.** With
  `broker.adapter: local`, the runner holds each provider key. A sandbox
  gets only a per-run token for a loopback route the broker serves. The
  broker enforces provider routing at the wire and meters spend from the
  provider's own responses.
- **Forge tokens and signing keys stay with the runner.** The forge token
  (`scm.token_env`) and the commit signing key (`vcs.signing_key`) stay in
  the runner's process and are never mounted into a sandbox.
- **Configuration names a credential and never holds it.** Credentials
  appear in configuration only as the *names* of environment variables:
  `key_env`, `dsn_env`, `token_env` and `url_env`.
- **Declared files can be withheld from every provider.** Files matching
  `providers.never_send` are left out of the worktree the agent sees.
- **The `secrets` gate refuses a diff that adds a high-confidence secret
  pattern.** A `Torve-Bypass` trailer cannot waive it.
- **The `scope` gate refuses changes outside the task's scope.** Any changed
  path outside the contract's allowed paths turns it red.
- **Forge review threads are fenced.** Text from a pull request's review
  threads reaches an agent only inside a fence that marks it as third-party
  claims. The fence is delimited by a per-run nonce that the text cannot
  close (S-0084/D-8).
- **A sandbox reads only declared inputs.** Each image shuts out the files
  a worktree carries for other readers: `.mcp.json`, `AGENTS.md` and the
  repository's own skill directories. An agent gets what its seat's profile
  declares (S-0066).

### What Torve does not defend

- **Exfiltration, in the default broker mode.** In `endpoint` mode the
  sandbox keeps Docker's default bridge network. With
  `runtime.network: host`, it shares the host's network stack. Either way,
  an agent can send the worktree anywhere it can reach.
  `broker.mode: sealed` puts the sandbox on an internal network whose only
  reachable address is the broker, with declared pass-through hosts. That
  mode has seen far less use than `endpoint`.
- **Provider keys, without a broker.** With `broker.adapter: none`, the
  default, the variable a provider's `key_env` names is passed into the
  sandbox's environment, and the agent can read it. `torve doctor` says so.
- **Prompt injection from the repository itself.** Files in the worktree,
  such as code, comments, fixtures and documents, reach the agent unfenced.
  Only forge review threads are fenced.
- **What a commit signature means.** Agent commits are signed with the key
  in `vcs.signing_key`, which is usually the operator's. A forge shows them
  as "Verified". The signature attests that Torve produced the commit under
  its task, not that a person reviewed it.
- **Secrets in traces.** Attempt traces and telemetry under `.torve/` are
  written on the host and can hold whatever the agent read or printed.
  `torve init` gitignores them. No retention period is enforced yet.
- **Sandbox images.** Images are built locally from the definitions under
  `sandboxes/`, which install pinned harness versions from npm. Nothing
  signs or attests them.
- **More than one operator.** Torve assumes one trusted operator per host.
  Anyone who can run `torve` there, or push an accepted document to the
  repository, directs what agents do.

### What an operator must not do

- **Do not set `runtime.docker: socket` for work you would not run on the
  host yourself.** It mounts the host's Docker socket into every sandbox of
  the run, and a container started over that socket can mount any host
  path.
- **Do not put a credential's value in a committed file.** Name the variable
  instead.
- **Do not point `vcs.signing_key` at a key whose signature people read as
  personal review.** Use a key for Torve's commits alone.
- **Do not run Torve against a repository you do not control without
  `broker.adapter: local` and `providers.never_send`.** Consider
  `broker.mode: sealed` as well.
