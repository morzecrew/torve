<!-- torve:managed src/torve/adapters/vcs — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/vcs/`

### S-0055/D-40 — `ASSUMED` (Standing decisions)

A landing is recorded from the `Torve-Task` trailer on the commit; git log is the surviving record and the importer mints landed history from it

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/runner.py`
- Consequence: A session-authored landing enters the record the same way an agent's does

### S-0059/D-12 — `LOCKED` (One word for the document, and the tree as the record)

Every reader of a landing reads the tree through `landings` and `landed_commits` — `shipped_landings`, `shipped_ids`, `shipped_commit`, the revert leg, `status`, `shadow`, `evals`, the review's defect lookup, the PR review's `landed_tasks` and `spec cites`; no git subprocess reads a trailer or a subject, closing S-0022/A-1's exception and retiring S-0007/D-26's subject spellings; the five local tasks without a landing get one written once from their trailers, by a script not committed

- Paths: `src/torve/application/projections.py` `src/torve/application/specquality.py` `src/torve/application/review.py` `src/torve/application/session.py` `src/torve/application/ports.py` `src/torve/application/residency.py` `src/torve/application/shadow.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/workspace/git.py` `src/torve/cli/**`
- Consequence: A tree without git answers what landed; S-0022/D-5 holds again without its exception
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0080/D-6 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The forge surface answers about a branch — its pull request's state and, when merged, the merge commit — and `PrInfo` carries that commit

- Paths: `src/torve/adapters/vcs/git.py` `src/torve/application/ports.py`
- Consequence: the lane asks the one question it has, which is what happened to this task's branch, without first having to remember a pull request number

### S-0080/D-13 — `ASSUMED` (The lane opens a pull request, and a person lands it)

The forge credential stays where it is — `gh` on the host, the variable named in configuration and read at call time, the value never leaving the runner's process and never reaching a sandbox

- Paths: `src/torve/adapters/vcs/git.py`
- Consequence: a landing route that talks to the forge on every pass adds no second channel for the secret the broker exists to keep out of the sandbox

### S-0084/D-1 — `ASSUMED` (The review leg: a pull request's threads become work on its branch)

The forge surface answers about a branch with the unresolved review threads of its open pull request beside the state, on `PrInfo` and in the same call — never as a second question the lane has to remember to ask

- Paths: `src/torve/application/ports.py` `src/torve/adapters/vcs/git.py`
- Consequence: a document of six phases still costs one forge call per pass, so the leg adds a field to the read-back rather than a second rate budget nobody sized

### S-0084/D-2 — `ASSUMED` (The review leg: a pull request's threads become work on its branch)

The thread read moves to the forge's GraphQL pull request, because unresolved is a `reviewThreads` fact the REST review-comment endpoint does not carry and the identifier a resolve addresses is a GraphQL node id

- Paths: `src/torve/adapters/vcs/git.py`
- Consequence: the engine can tell a thread a reviewer already closed from one nobody has touched, which is what makes "one round per finding" checkable rather than aspirational

<!-- /torve:managed -->
