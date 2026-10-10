# Finishing a phase by hand

Use this when an attempt's work is right but cannot land on its own, for
example when it is red on scope only or blocked by a finding outside its
reach, or when a phase is easier to write in a session than to serve. The
result looks to the engine exactly like a served landing.

1. Work on the document branch's remote tip, in a fresh branch or worktree:
   `git fetch && git switch -c hand/<task> origin/torve/S-NNNN`.
2. Bring the work in. An attempt's diff is in its worktree, so
   `git -C .wt/<task> diff <base>` piped to `git apply` recovers it.
   Otherwise, write the change yourself.
3. `torve spec project`, then run the phase's own acceptance commands. Not
   only the tests of the files you touched: a hand commit that breaks a test
   the phase owns turns the pull request red.
4. `torve gates run --base origin/main --task .torve/tasks/<task>/contract.yaml`.
5. Commit the work. If the branch lacks them, copy in
   `.torve/tasks/<task>/contract.yaml` and `log.yaml` first.
6. Write the landing, naming the work commit and the attempt that produced
   it, and commit the landing file on its own:
   `torve log land <task> --commit <work-sha> --attempt <n>`.
7. Push as a fast-forward to `torve/S-NNNN`, and only when no night is
   working that document.
8. Tell the record:
   `torve manager resolve <partition> <task> --resolution landed --sha <landing-sha>`,
   then run `torve reap --escalated`.
9. Remove your worktree.
