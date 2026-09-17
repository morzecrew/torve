<!-- torve:managed src/torve/adapters/workspace — rendered from the corpus; do not edit by hand -->

## Decisions governing `src/torve/adapters/workspace/`

### S-0059/D-12 — `LOCKED` (One word for the document, and the tree as the record)

Every reader of a landing reads the tree through `landings` and `landed_commits` — `shipped_landings`, `shipped_ids`, `shipped_commit`, the revert leg, `status`, `shadow`, `evals`, the review's defect lookup, the PR review's `landed_tasks` and `spec cites`; no git subprocess reads a trailer or a subject, closing S-0022/A-1's exception and retiring S-0007/D-26's subject spellings; the five local tasks without a landing get one written once from their trailers, by a script not committed

- Paths: `src/torve/application/projections.py` `src/torve/application/specquality.py` `src/torve/application/review.py` `src/torve/application/session.py` `src/torve/application/ports.py` `src/torve/application/residency.py` `src/torve/application/shadow.py` `src/torve/adapters/vcs/git.py` `src/torve/adapters/workspace/git.py` `src/torve/cli/**`
- Consequence: A tree without git answers what landed; S-0022/D-5 holds again without its exception
- Touching these paths owes a divergence entry: `torve log owed <task> --touched <files>` before you finish

### S-0080/D-10 — `ASSUMED` (The lane opens a pull request, and a person lands it)

In `pull_request` mode a worktree is cut from the remote's `main` after a fetch, never from the local one

- Paths: `src/torve/adapters/workspace/git.py` `src/torve/gates/context.py`
- Consequence: an attempt builds on the base its pull request will be merged against, rather than on a copy that is stale from the first merge onward

<!-- /torve:managed -->
