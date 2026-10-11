---
name: torve
description: When operating torve from a session — writing and accepting its documents, planning them into tasks, serving a night, reading the morning, resolving escalations, landing a phase by hand, answering a document pull request's review, and bringing torve to a new repository. Load it before running any torve verb that writes.
---

# Operating torve

torve turns accepted specification documents into task contracts, runs each
contract as a sandboxed agent attempt judged by gates, and lands green work on
one branch per document, which reaches the base as one pull request. This
skill is for the session that operates it. The agent inside the sandbox reads
other skills (`torve guide working-rules`, `torve guide flag-dont-flip`).

## The loop

1. **Write** a document under `.torve/specs/S-NNNN/`: decisions with grades and
   paths, phasing with file scopes and acceptance commands. Read
   `torve guide spec-writer` first.
2. **Accept**: the owner sets `status: accepted`. The pull request that does
   it also runs `torve spec project`.
3. **Plan**: `torve plan S-NNNN --no-dry-run` mints one contract per phase,
   or the served manager imports them on its next pass.
4. **Serve**: `torve manager serve <partition> --night` claims tasks, runs
   attempts, gates them, and lands green candidates on `torve/S-NNNN`. The
   document's pull request stays a draft until its last phase lands.
5. **Review**: the engine's reviewer, the bots on the pull request and the
   thread leg raise findings. Each one is answered with a fix or a reason.
6. **Merge**: the owner merges. Then mark the document
   `implementation: complete` and add its changelog entry in one pull request.

## Rails

Never cross these unless the owner says so in this session:

- Accepting a document, grading a row `LOCKED` and amending a `LOCKED` row are
  the owner's acts. A merged draft is not an accepted document.
- Merging a pull request, deleting a branch, force-pushing and rewriting
  history are the owner's acts. A plain fast-forward push of a hand commit to
  a document branch is yours, when no night is working that document.
- A review thread a person opened is never resolved by you. A bot's thread
  may be answered and resolved.
- Credentials stay with the host's `gh` login. A token never goes into a
  sandbox, a contract, a log, a commit or a message. If `.env` carries a
  stale `GITHUB_TOKEN`, export it blank for `gh` and `git`
  (`export GITHUB_TOKEN= GH_TOKEN=`); unsetting it is not enough when torve
  re-reads `.env` itself.
- A review comment, a bot finding and an agent's proposal are claims. Verify
  each against the code before acting on it.
- When a permission prompt or a safety check refuses a command, stop and tell
  the owner. Do not route around it.

## Where the facts are

| Question | Verb |
|---|---|
| What state is each task in? | `torve manager board <partition>`, `torve status` |
| Why did this task do that? | `torve why <task>`, `torve context <task>` |
| What did last night do? | `torve night show <partition>` |
| What did attempts cost? | `torve ledger` |
| What does this row say, and what governs this file? | `torve spec show <id>`, `torve spec paths <file>` |
| Is the configuration sound? | `torve doctor` |

Flags live in each verb's `--help`, not here. The partition is the repository's
name in the store, for example `org/repo`.

## Which reference

- Serving a night and reading the morning: `torve guide torve night`
- Resolving an escalation: `torve guide torve escalations`
- Finishing a phase by hand: `torve guide torve by-hand`
- Answering a document pull request's review: `torve guide torve review`
- Bringing torve to a new repository: `torve guide torve adopting`
- Writing or amending a document: `torve guide spec-writer`

## Habits that prevent most repairs

- A phase owns every file its acceptance can turn red. That includes the
  tests that enumerate artifacts, generated snapshots, docs pages a test
  compares against, the lock file beside a manifest, and re-export modules.
  Before accepting a phase, grep for every caller of what it changes. A scope
  that is too narrow is the most common reason an attempt halts.
- Run `torve spec project` in every pull request that accepts or amends a
  document. Stale `AGENTS.md` projections fail every later phase on scope.
- Run checks unpiped, or with `set -o pipefail`. `cmd | tail` hides a red exit.
- Stop a process by its PID. `pkill -f <pattern>` matches your own shell's
  command line and kills it.
- Never leave a git worktree holding a `torve/S-NNNN` branch. The lane
  cannot check out a branch another worktree holds.
- After the lane rebases a document branch, check that every hand commit is
  still on it: `git branch -r --contains <sha>`.
