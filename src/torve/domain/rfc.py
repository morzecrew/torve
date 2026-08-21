"""RFC vocabularies (RFC 0007 §3a, D-7.13): `Grade`, `Status`, `Kind` and
`Implementation` are defined here once and imported everywhere. A duplicated
vocabulary eventually gains a member in one copy only.

The format itself — frontmatter fields, the decision-table shape — is parsed
and validated in `torve.config.rfc_parse` (D-7.12); this module owns only the
words.
"""

from __future__ import annotations

from typing import Literal

# ----------------------- #

Grade = Literal["LOCKED", "ASSUMED", "OPEN"]
Status = Literal["draft", "accepted", "superseded"]
Kind = Literal["design", "convention"]
Implementation = Literal["none", "partial", "complete", "abandoned"]

GRADES: tuple[Grade, ...] = ("LOCKED", "ASSUMED", "OPEN")
STATUSES: tuple[Status, ...] = ("draft", "accepted", "superseded")
KINDS: tuple[Kind, ...] = ("design", "convention")
# A judgement, never progress (D-A.11): progress is store-derived and would
# diverge on the first escalation.
IMPLEMENTATIONS: tuple[Implementation, ...] = ("none", "partial", "complete", "abandoned")
