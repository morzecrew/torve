# Review

You are reviewing a change, not fixing it. The workspace is read-only.

## The task under review

intent:
Add retry handling to the fetch path.

inherited decisions:
- D-90.1 [LOCKED] Errors surface to the caller; swallowing an exception without record is forbidden

## Gate results

- (none recorded)

## The diff

```diff
diff --git a/src/fetching.py b/src/fetching.py
--- a/src/fetching.py
+++ b/src/fetching.py
@@ -1,2 +1,7 @@
-def fetch(url, download):
-    return download(url)
+def fetch(url, download, attempts=3):
+    for _ in range(attempts):
+        try:
+            return download(url)
+        except Exception:
+            pass
+    return None

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
