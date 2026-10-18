# Answering a document pull request's review

The engine's thread leg answers some bot findings during a night by minting
review rounds. What it leaves, and every person's comment, comes to you.

## List what is open

```bash
gh api graphql -f query='query { repository(owner:"<org>", name:"<repo>") {
  pullRequest(number:<n>) { reviewThreads(first:100) { nodes {
    id isResolved isOutdated path line
    comments(first:1) { nodes { author { login } body } } } } } } }' \
  --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved|not)'
```

## Answer each thread

1. Read the claim and check it in the code: run the case, grep the callers,
   read the test. A bot is wrong often enough that the check is never
   optional.
2. Decide what to do:
   - valid and inside the document's scope: fix it on the document branch;
   - valid but outside it: file a source or a follow-up and reply with where
     it went;
   - wrong: reply with the evidence.
3. Reply to the thread, saying what changed and in which commit, or why
   nothing did. Then resolve it, but **only if a bot opened it**.

```bash
gh api graphql -f query='mutation { addPullRequestReviewThreadReply(input:{
  pullRequestReviewThreadId:"<id>", body:"<answer>"}) { comment { id } } }'
gh api graphql -f query='mutation { resolveReviewThread(input:{threadId:"<id>"}) {
  thread { isResolved } } }'
```

GitHub's API sometimes fails with a TLS or EOF error. Retry it.

## Threads that arrive after the night

The thread leg answers what the bots have raised when the night drains; a bot
that reviews the new head afterwards, or a person who comments the next
morning, leaves findings the night never saw. They are the same work: list
them with the query above on the head the day's work left, and answer each one
no differently from a thread the leg minted. A thread a person opened is never
resolved by you.

## After a fix

- Run the phase's acceptance, not only the tests of the files you touched.
- Push as a fast-forward, then look at the threads again: the bots review the
  new head.
- Wait for CI and for the bots' re-review before calling the pull request
  ready. `gh pr checks --watch` blocks until every check on the head finishes,
  and exits non-zero when one fails — the wait without polling by hand.
- If a bot still requests changes after all its threads are resolved, ask it
  to re-review in its own convention, and only when the owner has granted
  that.
- Never merge. The owner merges.
