# pages/

Source for the published documentation site.

## What this is

User-facing documentation for a released version of Torve: how to install it,
how to configure gates, how to write a task contract, what each command does,
what to do when something fails.

Written independently, in its own voice, for a reader who wants to use Torve
and has no interest in how its design was arrived at.

## What this is not

Not a rendering of `rfcs/`. The decision corpus and the documentation answer
different questions, are read by different people, and move on different axes:

| | `rfcs/` | `pages/` |
| --- | --- | --- |
| Answers | why it was decided, what was rejected | how to use it |
| Over time | accumulates; amendments are appended, nothing is deleted | replaced; one released version, no history |
| Versioned by | nothing — continuous numbering | release |

A page that reads like an RFC summary is a page that has failed. If a reader
needs the reasoning, link to the RFC; do not paraphrase it.

## The one hard rule

**A page may not contradict an accepted decision** (D-A.1a). That is the entire
coupling between the two directories — a constraint, not a pipeline.

Rationale, rejected alternatives, risks and out-of-scope sections stay in
`rfcs/` and never appear here. Installation, cookbooks, troubleshooting and
reference appear here and in no RFC.

## Versioning

The site is versioned per release (`0.3/`, `0.4/`, `latest`). `rfcs/` is not
versioned and has no release branches. Do not try to align them: cutting a
release freezes a snapshot of these pages and leaves the corpus untouched.

## Release checklist

- Pages for changed behaviour updated to describe the new state — not to record
  that it changed. Changes belong in the changelog.
- No page contradicts an accepted decision.
- Any RFC that moved to `superseded` this cycle: check the pages that cite it.
  A reminder, not an automated check.

## Structure

Empty until the first user-facing page exists. Nothing else is parked here —
one-off procedures go to `ops/`, decisions go to `rfcs/`.
