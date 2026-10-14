"""The one reading of a scope glob (S-0105/D-1, D-5).

`torve.base.globs` is the engine's whole reading of a scope glob: `matches`
answers whether a path falls under a set of globs, `overlap` whether two sets
can both match one path. Both are pinned here to the reference the scope gate
itself uses — `pathspec`'s `GitIgnoreSpec`, gitignore semantics — over a
grammar small enough to enumerate completely.

The literal alphabet is a single segment. A second literal `b` would let two
patterns disagree on a segment, and gitignore's "what is under a matched path
matches too" then pushes the shortest shared path past the four-segment bound
`overlap` is checked within (`a/` against `b/b/b` share only `b/b/b/a/x`), so
the bounded brute force would call an overlap the exact `overlap` reports.
One literal keeps every shared path within the bound and the two comparable.
"""

from __future__ import annotations

import itertools

from pathspec import GitIgnoreSpec

from torve.base.globs import has_file_in, matches, overlap

LITERALS = ("a",)
WILDCARDS = ("*", "**", "?")


def _patterns() -> list[str]:
    """Every pattern of up to three segments over the alphabet, with and
    without a leading or trailing `/` (S-0105/D-5)."""

    out: set[str] = set()

    for length in (1, 2, 3):
        for combo in itertools.product(LITERALS + WILDCARDS, repeat=length):
            base = "/".join(combo)

            for lead in ("", "/"):
                for trail in ("", "/"):
                    out.add(lead + base + trail)

    return sorted(out)


def _paths() -> list[str]:
    """Every path of up to four segments over the literal alphabet."""

    out: set[str] = set()

    for length in (1, 2, 3, 4):
        for combo in itertools.product(LITERALS, repeat=length):
            out.add("/".join(combo))

    return sorted(out)


PATTERNS = _patterns()
PATHS = _paths()


def test_matches_equals_the_scope_gate_on_every_pattern_and_path():
    for pattern in PATTERNS:
        spec = GitIgnoreSpec.from_lines([pattern])

        for path in PATHS:
            assert matches(path, [pattern]) == spec.match_file(path), (pattern, path)


def test_overlap_equals_the_brute_force_search_on_every_pair():
    hit = {pattern: {path for path in PATHS if matches(path, [pattern])} for pattern in PATTERNS}

    for left in PATTERNS:
        for right in PATTERNS:
            together = bool(hit[left] & hit[right])

            assert overlap([left], [right]) == together, (left, right)
            assert overlap([right], [left]) == together, (left, right)


# The four pairs the deleted `globs_intersect` called disjoint, each a real
# shared path (S-0105/D-2). A fence reading them the old way ran two tasks
# that write the same file.
DISJOINT_UNDER_THE_OLD_READING = [
    (["**/AGENTS.md"], ["src/torve/cli/**"]),
    (["src/**/*.py"], ["src/a/*.py"]),
    (["**/test_*.py"], ["tests/**"]),
    (["*.md"], ["docs/**"]),
]


def test_the_pairs_the_old_reading_called_disjoint_overlap():
    for left, right in DISJOINT_UNDER_THE_OLD_READING:
        assert overlap(left, right), (left, right)
        assert overlap(right, left), (left, right)


def test_what_is_under_a_matched_path_matches_too():
    assert matches("src/a/b.py", ["src/a"])
    assert matches("src/a/b.py", ["src/a/**"])


def test_a_leading_slash_anchors_to_the_root():
    assert matches("a/x", ["/a"])
    assert not matches("b/a", ["/a"])


def test_a_bracket_class_can_only_over_report_an_overlap():
    """D-2: the meet reads a class as any one character, so `overlap` may
    report a shared path `matches` does not — never the other way round."""

    assert matches("src/a.py", ["src/[abc].py"])
    assert overlap(["src/[abc].py"], ["src/a.py"])
    assert overlap(["src/[abc].py"], ["src/x.py"])


# Two literals and a part-segment wildcard (the review's minor finding on
# T-0563): segments can now disagree, and `a*` exercises the meet one
# character at a time. Disagreeing segments push the shortest shared path
# deeper, so paths run to six segments over `a`, `b` and `ab`; at five the
# bounded search misses shared paths the exact `overlap` finds.
WIDE_SEGMENTS = ("a", "b", "*", "**", "a*")
WIDE_PATH_SEGMENTS = ("a", "b", "ab")


def _wide_patterns() -> list[str]:
    return sorted(
        lead + "/".join(combo) + trail
        for length in (1, 2, 3)
        for combo in itertools.product(WIDE_SEGMENTS, repeat=length)
        for lead in ("", "/")
        for trail in ("", "/")
    )


def _wide_paths() -> list[str]:
    return sorted(
        "/".join(combo)
        for length in range(1, 7)
        for combo in itertools.product(WIDE_PATH_SEGMENTS, repeat=length)
    )


def test_two_literals_and_a_part_segment_wildcard_agree_with_the_gate():
    paths = _wide_paths()
    hit: dict[str, set[str]] = {}

    for pattern in _wide_patterns():
        spec = GitIgnoreSpec.from_lines([pattern])
        hit[pattern] = {path for path in paths if spec.match_file(path)}

        for path in paths:
            assert matches(path, [pattern]) == (path in hit[pattern]), (pattern, path)

    for left in hit:
        for right in hit:
            assert overlap([left], [right]) == bool(hit[left] & hit[right]), (left, right)


def test_a_scope_refuses_a_negated_glob():
    # gitignore's negation turns on whether a glob met the path or a directory
    # above it; the scope's readers would disagree, so a scope cannot carry one.
    import pytest
    from pydantic import ValidationError

    from torve.domain.task import Scope

    with pytest.raises(ValidationError, match="deny"):
        Scope(allow=["src/**", "!src/private/**"])

    assert Scope(allow=["src/**"], deny=["src/private/**"]).deny == ["src/private/**"]
    # A contingent entry is a literal path: `!notes.py` is a file's name.
    assert Scope(contingent=["docs/!notes.py"]).contingent == ["docs/!notes.py"]


def test_a_file_directly_in_a_directory_is_asked_exactly():
    assert has_file_in("src", ["src/foo.py"])
    assert not has_file_in("src/unrelated", ["src/foo.py"])
    # Only files below `src/foo` match, never one directly in `src`.
    assert not has_file_in("src", ["src/foo/**"])
    assert has_file_in("src/foo/bar", ["src/foo/**"])
    # A class is read as `fnmatch` reads it, not as any character.
    assert has_file_in("src/a", ["src/[ab]/*.py"])
    assert not has_file_in("src/c", ["src/[ab]/*.py"])
    assert has_file_in("", ["*.md"]) and has_file_in("deep/er", ["*.md"])
    # A glob matching the directory itself covers everything below.
    assert has_file_in("src/x", ["src/"])


def test_a_close_bracket_first_in_a_class_is_a_member():
    # `fnmatch` reads `[]]` as a class holding `]`; the overlap's reading has to
    # agree, or the fence calls two scopes disjoint that write one file.
    assert matches("a]", ["a[]]"])
    assert overlap(["a[]]"], ["a]"])
    assert overlap(["a[!]]"], ["ab"])
