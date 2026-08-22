"""DecisionSource over an RFC corpus (RFC 0007 §6a): the tables `rfc-writer`
already produced — a markdown table parse, nothing more. Deterministic by
construction (D-7.6); one of the three names allowed to know the RFC format
(D-7.18): `torve plan`, `torve rfc *`, and this adapter.
"""

from __future__ import annotations

from pathlib import Path

from torve.application.planner import globs_intersect
from torve.config import rfc_parse
from torve.domain.rfc import GRADES
from torve.domain.task import InheritedDecision

# ----------------------- #


class RfcDirectory:
    def __init__(self, rfc_dir: Path) -> None:
        self.rfc_dir = rfc_dir

    def standing(self, repo: str, paths: list[str]) -> list[InheritedDecision]:
        """Rows from accepted documents whose declared paths overlap the
        given area. `repo` is accepted for the port's sake; a directory
        serves exactly one repository's corpus."""
        found: list[InheritedDecision] = []
        for _number, path in sorted(rfc_parse.rfc_files(self.rfc_dir).items()):
            text = path.read_text(encoding="utf-8")
            fm = rfc_parse.parse_frontmatter(text)
            if fm is None or str(fm.get("status", "")) != "accepted":
                continue
            for row in rfc_parse.decision_table(text):
                if row.grade not in GRADES or not row.paths:
                    continue
                if globs_intersect(row.paths, paths):
                    found.append(InheritedDecision(
                        id=row.identifier, grade=row.grade,
                        text=row.text.strip(), paths=row.paths,
                    ))
        return found
