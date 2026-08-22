"""The `user-facing-text` gate's discriminations (RFC 0011 §5a, D-11.11):
what counts as a corpus identifier and which docstrings are exempt. The
behavioural cases live in the sabotage suite; these pin the edges."""

from __future__ import annotations

from pathlib import Path

from torve.gates.user_facing_text import _check_file

CLI = "src/torve/cli/thing.py"
GATES = "src/torve/gates/thing.py"


def test_public_standard_rfc_numbers_are_not_corpus_identifiers():
    # Corpus numbers are zero-padded; "RFC 3339" is a public standard.
    assert _check_file(GATES, 'MSG = "at is not an RFC 3339 timestamp"\n') == []
    assert _check_file(GATES, 'MSG = "minted per RFC 0007"\n') != []


def test_section_mark_and_corpus_path_are_flagged():
    assert _check_file(CLI, 'HELP = "see \u00a73 for details"\n') != []
    assert _check_file(CLI, 'HELP = "documented in rfcs/0011-cli-contract.md"\n') != []


def test_cli_public_function_docstring_is_help_text_but_private_is_not():
    flagged = _check_file(CLI, 'def cmd() -> None:\n    """Sizes tasks (D-2.9)."""\n')
    assert flagged and "D-2.9" in flagged[0]
    assert _check_file(CLI, 'def _cmd() -> None:\n    """Sizes tasks (D-2.9)."""\n') == []
    # Outside the cli package no function docstring is rendered as help.
    assert _check_file(GATES, 'def cmd() -> None:\n    """Sizes tasks (D-2.9)."""\n') == []


def test_module_and_class_docstrings_are_exempt_everywhere():
    body = '"""Module (D-1, RFC 0002 \u00a74)."""\n\nclass C:\n    """Class (D-2.9)."""\n'
    assert _check_file(CLI, body) == []


def test_the_gate_scans_its_own_module_clean():
    # The pattern is assembled from fragments precisely so its own constants
    # never match it; a regression here would redden every future gate edit.
    source = Path("src/torve/gates/user_facing_text.py")
    assert _check_file(str(source), source.read_text(encoding="utf-8")) == []
