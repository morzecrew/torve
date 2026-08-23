# Review

You are reviewing a change, not fixing it. The workspace is read-only.

## The task under review

intent:
Rename the ambiguous helper for readability.

inherited decisions:
- none

## Gate results

- (none recorded)

## The diff

```diff
diff --git a/src/naming_helper.py b/src/naming_helper.py
--- a/src/naming_helper.py
+++ b/src/naming_helper.py
@@ -1,2 +1,2 @@
-def brn(task_id):
+def derive_branch_name(task_id):
     return f"torve/{task_id}"

```

## What to produce

Judge the change: is it wrong, unsafe, or contradicting an inherited
decision marked LOCKED? A small diff after green gates is often clean —
"no findings" is a normal, frequent outcome, not a failure to work.
Severities: blocker (the change is wrong, unsafe, or contradicts a LOCKED
decision), major (a reviewer would insist before merge), minor or nit
(preferences; at most two).

Every finding needs evidence that locates: a leading `path:line` citation
(against the files in this workspace) followed by " — " and one sentence,
or a backticked command with its output. A finding whose evidence does not
locate is discarded unread.

Your final output must be exactly one JSON document, nothing after it:

{"findings": [{"severity": "major", "claim": "...", "evidence": "path.py:12 — ..."}]}

An empty list is a valid, complete review: {"findings": []}
