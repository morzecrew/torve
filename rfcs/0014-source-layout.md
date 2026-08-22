---
id: "0014"
title: Source file layout
kind: convention
status: accepted
implementation: complete
depends_on: []
informed_by: ["0002", "0011"]
supersedes: []
superseded_by: null
amended_by: []
owner: Lev Litvinov
description: >-
  Semantic separators and module preamble structure for Torve's Python
  source, extracted from forze by reading and counting; the checkable half
  ships as the source-layout gate.
schema_version: 1
---

# RFC 0014 — Source file layout

- **Scope:** Semantic separators and module preamble structure for Torve's Python source. Defines the two separator forms, where each goes, the spacing around them, per-layer conventions, and which parts are machine-checked. Excludes naming, typing and architectural layering, which are the substrate's conventions and `agent-skills` practice respectively.
- **Related:** RFC 0002 (this document's gate ships there) · RFC 0011 (`print` belongs to the CLI layer) · `ratchet-what-you-build`

## 1. Why this is written down

The convention already exists — it is what forze does, consistently, across every module. Writing it down is not an act of design but of extraction, so that agents writing Torve's source produce code that reads like the substrate it sits on, and so that the mechanical parts can be enforced rather than reviewed.

Two people already read both codebases. A file that observes a different rhythm costs a beat of attention on every read, forever, and no single instance is ever worth raising in review — which is exactly how a convention erodes.

The rules below were derived by reading forze at `main` and counting, not by preference.

## 2. Two separators, both 27 characters

```python
# ----------------------- #        23 dashes — structural
# ....................... #        23 dots   — rhythmic
```

Identical width, deliberately. They are read by *texture*, not by measuring: a dashed line is a wall, a dotted line is a comma. Never resize either to fit content — a landmark whose width varies stops being scannable, which is the only reason it exists.

Observed density across the modules read:

| Module | Lines | Dash | Dot | of which indented |
| --- | --- | --- | --- | --- |
| `base/lazy.py` | 42 | 1 | 0 | — |
| `base/logging/__init__.py` | 35 | 1 | 0 | — |
| `forze/__init__.py` | 129 | 1 | 0 | — |
| `application/execution/__init__.py` | 176 | 1 | 0 | — |
| `base/primitives/time_source.py` | 136 | 1 | 9 | 6 |
| `base/primitives/entropy_source.py` | 291 | 2 | 13 | 1 |

**The dot is the common one.** Dashes are rare — one per module in most files, two in the largest. Re-export modules and thin utility modules have no dots at all, because they have nothing to pace.

## 3. The dash — structural

### 3.1 After the import block, always

Every module with imports gets exactly one. It closes the preamble: above is machinery for reading the file, below is the file.

```python
from typing import Protocol, final, runtime_checkable
from uuid import UUID

import attrs

# ----------------------- #


@runtime_checkable
class TimeSource(Protocol): ...
```

One blank line before, two after (the normal spacing for a following top-level `class` or `def`).

### 3.2 Between major sections — with a label

A second dash appears only when a module holds genuinely distinct concerns, and it **carries a one-line label on the next line**:

```python
# ----------------------- #
# Durable-secret entropy — a separate seam a seeded source cannot satisfy.


@runtime_checkable
class SecretEntropy(Protocol): ...
```

The label is the point, and note its form: not `# SecretEntropy` — that is visible on the next line — but *why this is a separate seam*. An unlabelled mid-file dash announces that something changes without saying what.

**A label is a claim about structure.** If it will not fit on one line, the module is doing too much and wants splitting rather than labelling.

### 3.3 Before `__all__`

Re-export modules separate imports from the public-surface declaration:

```python
from .renderers import ForzeConsoleRenderer

# ----------------------- #

__all__ = [
    "Logger",
    ...
]
```

One blank line after here, because a statement follows rather than a definition.

## 4. The dot — rhythmic

The dot separates **peers**: one definition from the next, one member from the next. It carries no label, ever — the thing it introduces is on the following line and names itself.

### 4.1 Between top-level definitions

```python
    def monotonic(self) -> float:
        """Return a monotonic clock reading in fractional seconds."""
        ...  # pragma: no cover


# ....................... #


@final
@attrs.define(slots=True, frozen=True)
class SystemTimeSource: ...
```

Column 0, two blank lines each side.

Also used before a module-level constant that follows a definition, with one blank line after instead of two, since a statement follows:

```python
# ....................... #

_TIME_SOURCE: ContextVar[TimeSource] = ContextVar(...)
```

### 4.2 Inside a class body, between members

This is the use I would have missed and the one that gives the style its feel — six of nine dots in `time_source.py` are indented.

```python
@final
@attrs.define(slots=True)
class FrozenTimeSource:
    """A fixed clock for tests: a constant ``now`` and deterministic, ordered ids."""

    instant: datetime

    # ....................... #

    _counter: int = attrs.field(default=0, init=False)

    # ....................... #

    def now(self) -> datetime:
        return self.instant

    # ....................... #

    def uuid(self) -> UUID:
        from .uuid import uuid7

        base_ns = int(self.instant.timestamp() * 1_000_000_000)
        result = uuid7(timestamp_ns=base_ns + self._counter)
        self._counter += 1

        return result
```

At the member's indent, one blank line each side. Between attributes, between an attribute and the first method, and between methods alike — every member boundary, not only method boundaries.

**Why this earns its keep:** in a class of one-line methods, blank lines alone give no visual weight, and the members blur into a wall. The dotted line restores separation without implying the structural break a dash would.

### 4.3 Where the dot does not appear

Not inside a function body. Not between the members of a Protocol whose methods are all `...  # pragma: no cover` — those read as a list already and get plain blank lines (see `TimeSource` and `EntropySource`, both dot-free inside).

That second rule is worth stating because it is a judgement, not a mechanic: **the dot is for members with bodies.** Signatures without bodies are a declaration, and a declaration reads as one block.

## 5. Spacing, stated once

Blank lines around a separator follow the construct that comes *after* it, not a rule of their own.

| Position | Before | After |
| --- | --- | --- |
| Dash after imports | 1 | 2 (a definition follows) |
| Dash before `__all__` | 1 | 1 (a statement follows) |
| Dash, labelled section | 2 | label line, then 2 |
| Dot, top level, before a definition | 2 | 2 |
| Dot, top level, before a statement | 2 | 1 |
| Dot, inside a class | 1 | 1 |

## 6. Where it does not go

- **Not around every function at top level** where the module is a flat list of small helpers — the dot paces peers within a section, and a module that is nothing but peers does not need pacing on every one of them. Use judgement; `lazy.py` has none.
- **Not to compensate for a module that should be split.** Three or more dashes is a package, not a layout choice.
- **Not inside function bodies.** Extract instead.

## 7. Module preamble

The separators only pay off on a consistent preamble, so both are specified together.

```python
"""One line saying what this module is.

Two or three sentences on why it exists and what constrains it — the decisions a
reader needs in order to change it safely. Name the governing RFC decision when
one applies.

Cross-references in ``double backticks``; Sphinx roles where they resolve.
"""

import time                              # stdlib
from datetime import UTC, datetime
from typing import Protocol, final

import attrs                             # third party

from torve.domain.models import Task     # first party

# ----------------------- #
```

Import grouping is handled by `ruff` (`I`), so it is not a matter of discipline. Match forze's configuration: `line-length = 100` and the same rule selection — `E`, `W`, `F`, `I`, `UP`, `B`, `RUF`, `ASYNC`, `C4`, `ISC`, `PIE`, `T20`, `SIM`.

`T20` matters more here than in most projects: `print` is the CLI's output contract (RFC 0011), so it belongs in the presentation layer and nowhere else. The lint rule enforces that boundary for free.

**Docstrings carry the reasoning; separators carry the shape.** Both, or a file is unreadable in six months for one of two different reasons.

## 8. Layer conventions

| Layer | Dashes | Dots |
| --- | --- | --- |
| `domain/` | 1 — models and invariants belong together | between models, and between members of a model |
| `application/` | 1, or 2 when a module holds two ports | between services and between their methods |
| `adapters/` | 1, plus a labelled one if configuration precedes the adapter | between adapter methods |
| `gates/` | 1 — **one gate, one file, one section** | between helpers, if any |
| `cli/` | 1 | between commands |

`gates/` is the strictest: if a gate module wants a labelled dash, it is doing two checks — and two checks are two gates, with two names and two sabotage cases.

## 9. Enforcement — the `source-layout` gate

Under `ratchet-what-you-build`, a convention that only lives in a document is an open finding. The checkable half ships as a gate in the package (RFC 0002), not as review discipline.

```yaml
- name: source-layout
  run: "@source-layout"
  state: shadow              # enters per D-2.18 (A-8); promoted under D-2.23
  origin: rfc/0014
  added: 2026-08-21
  input: diff
  timeout: 30
```

*(The entry follows RFC 0002 §7.5 as amended by A-8 — `state` and `origin`, not `blocking: true`.)*

**Checked:**

| Check | Fails on |
| --- | --- |
| Separator form | any comment line of only dashes or dots that is not exactly the 27-character form |
| Post-import dash | a module with imports and no dash immediately after the import block |
| Dash ceiling | three or more dashes in one module |
| Dash label | a dash other than the post-import one with no label line following |
| Dot label | a dot separator carrying a label |

**Left to review**, because checking it would produce a linter nobody trusts: whether a label says something useful, whether a two-section module should have been split, and whether dot placement helps or is decoration.

**Sabotage cases** (the `source-layout` cases in `src/torve/gates/sabotage.py`, per A-2), one per check, asserted red in CI: a 20-dash separator, a module missing its post-import dash, a three-dash module, an unlabelled second dash, a labelled dot. A gate never observed to fail is not a check.

### 9.1 Adopt on the whole repository at once

The gate will fire on existing source, which was written before this document existed.

Do the sweep now rather than scoping the gate to changed files only. At the current size it is an hour; in a month it is a day, and for that whole month the gate reddens on files the task never touched — which teaches people to ignore it, and an ignored gate is worse than none.

## 10. Decisions

| # | Grade | Decision | Paths | Consequence |
| --- | --- | --- | --- | --- |
| D-14.1 | `LOCKED` | Two separators, both exactly 27 characters: 23 dashes structural, 23 dots rhythmic | `src/torve/**` | Equal width is what makes them readable as texture; resizing destroys it |
| D-14.2 | `LOCKED` | One dash after the import block in every module with imports | `src/torve/**` | The universal baseline in forze |
| D-14.3 | `LOCKED` | A dash that is not the post-import one carries a one-line label saying why the section is separate | `src/torve/**` | An unlabelled mid-file dash says "something changes" without saying what |
| D-14.4 | `LOCKED` | Dots never carry labels | `src/torve/**` | They separate peers; the peer names itself on the next line |
| D-14.5 | `LOCKED` | Dots go between class members — attributes and methods alike — not only between top-level definitions | `src/torve/**` | The indented use is the majority of them and gives the style its feel |
| D-14.6 | `ASSUMED` | No dots between the members of a body-less Protocol | `src/torve/application/ports.py` | Signatures without bodies read as one block |
| D-14.7 | `ASSUMED` | More than two dashes in a module is a split, not a layout choice | `src/torve/**` | Observed maximum in forze is two, in a 291-line file |
| D-14.8 | `LOCKED` | One gate, one file, one section | `src/torve/gates/**` | A gate wanting a second section is two gates |
| D-14.9 | `ASSUMED` | Lint configuration matches forze: line length 100, same rule selection | `pyproject.toml` | Two codebases read by the same people should not differ in mechanics |
| D-14.10 | `ASSUMED` | Width, placement and labelling are script-checked; usefulness is review | `src/torve/gates/source_layout.py` | Checking what cannot be checked yields a linter nobody trusts |
| D-14.11 | `LOCKED` | The checkable half ships as the `source-layout` gate with a sabotage case per check | `src/torve/gates/source_layout.py` `.torve/gates.yaml` | Otherwise this document sits at convention level, which its own neighbour forbids |
| D-14.12 | `ASSUMED` | The whole repository is swept once on adoption, not scoped to changed files | `src/torve/**` | A gate that reddens on untouched files trains people to ignore it |
| D-14.13 | `ASSUMED` | The dash-placement check: the first dash falls after the last top-level import (a trailing TYPE_CHECKING block counts into the preamble) and before the first definition, over changed `.py` files under `src/`; blank-line spacing stays with review (D-14.10). Added by execution 2026-08-22 — see .torve/tasks/T-0011 | `src/torve/gates/source_layout.py` | — |
