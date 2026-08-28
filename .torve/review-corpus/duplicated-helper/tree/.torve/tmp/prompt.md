# Review

You are reviewing a change, not fixing it. The workspace is read-only.

## The task under review

intent:
Add a checksum module with Luhn validity and the Damm algorithm.

inherited decisions:
- D-90.2 [LOCKED] One implementation per algorithm; a helper that already exists in the package is reused, never re-implemented

## Gate results

- (none recorded)

## The diff

```diff
diff --git a/src/lab/checksum.py b/src/lab/checksum.py
new file mode 100644
--- /dev/null
+++ b/src/lab/checksum.py
@@ -0,0 +1,37 @@
+"""Digit-string checksums: Luhn validity and the Damm check digit."""
+
+_DAMM = (
+    (0, 3, 1, 7, 5, 9, 8, 6, 4, 2),
+    (7, 0, 9, 2, 1, 5, 4, 8, 6, 3),
+    (4, 2, 0, 6, 8, 7, 1, 3, 5, 9),
+    (1, 7, 5, 0, 9, 8, 3, 4, 2, 6),
+    (6, 1, 2, 3, 0, 4, 5, 9, 7, 8),
+    (3, 6, 7, 4, 2, 0, 9, 5, 8, 1),
+    (5, 8, 6, 9, 7, 2, 0, 1, 3, 4),
+    (8, 9, 4, 5, 3, 6, 2, 0, 1, 7),
+    (9, 4, 3, 8, 6, 1, 7, 2, 0, 5),
+    (2, 5, 8, 1, 4, 3, 6, 7, 9, 0),
+)
+
+
+def luhn_valid(digits: str) -> bool:
+    """Return True when the digit string passes the Luhn checksum."""
+    if not all(ch.isdigit() for ch in digits):
+        raise ValueError("digits must contain only digit characters")
+    total = 0
+    for index in range(len(digits) - 1, -1, -1):
+        value = int(digits[index])
+        if (len(digits) - index) % 2 == 0:
+            value = value * 2 - 9 if value > 4 else value * 2
+        total += value
+    return total % 10 == 0
+
+
+def damm_check_digit(digits: str) -> int:
+    """Return the Damm check digit for the digit string."""
+    if not all(ch.isdigit() for ch in digits):
+        raise ValueError("digits must contain only digit characters")
+    interim = 0
+    for ch in digits:
+        interim = _DAMM[interim][int(ch)]
+    return interim

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
