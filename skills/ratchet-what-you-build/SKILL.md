---
name: ratchet-what-you-build
description: Finishing any guard, check, or safe default — what makes it the only path, and the Torve obligation that a new gate ships with a sabotage case or does not ship.
roles: [implement, review]
gate: gates-sabotage
---

> **Specialisation.** Derived from `agent-skills/ratchet-what-you-build`,
> specialised for artefacts that Torve parses. Divergence from upstream is
> expected and intentional — **do not reconcile**. Improvements of general
> value flow upstream, not the reverse.

# Ratchet What You Build

The most common way good engineering work dies is not deletion — it's
optionality. The mechanism is built, works, and protects nothing, because the
next change ships without it and CI stays green. **"True now" is not "stays
true." Only a ratchet converts one into the other.** The closing move is
almost always small — a default flipped, a manifest check, a `require_x()`
call; the expensive part was building the mechanism, and that is already paid.

## The closing question

After building X, ask: **what makes X the only path?** Rank on the ladder —
each rung strictly weaker than the one above:

1. **Impossible to skip** — the unsafe path no longer exists
2. **On by default, declared opt-out** — a named, greppable flag, itself reviewable
3. **CI gate** — the build fails when the mechanism is skipped, stale, or unenrolled
4. **Runtime fail-closed** — refused at startup naming what's missing
5. **Convention** — documented, remembered, reviewed by humans. **This rung decays.**

Anything at rung 5 that protects something important is an open finding. The
taxonomy of ways a mechanism stops short is in
[references/the-ladder.md](references/the-ladder.md).

## The Torve obligation: no sabotage case, no gate

In this repository the skill has one concrete, non-negotiable form (D-2.2):
**a new gate ships with a sabotage case, or it does not ship.**

- The case lives in the sabotage suite (`src/torve/gates/sabotage.py`,
  historically `gates/sabotage/`): one deliberately bad change per gate,
  applied to a scratch repository, asserted **red** in CI — plus a clean twin
  asserted green, because a gate that cannot pass is as broken as one that
  cannot fail.
- The reason is observability, not ceremony: a gate that has never been
  observed to fail is indistinguishable from a gate that never fires because
  the code is clean.
- The same rule applies to the simulation's invariants (D-3.5): every
  invariant ships with a reachability target and a deliberately broken twin
  the oracle must catch. A simulation that cannot fail proves nothing.

The enforcing mechanism is the `gates-sabotage` suite itself (`torve gates
check`), run in the package's CI on every change and against consuming
repositories once per sprint.

## The periodic sweep

List every protective mechanism beside its ladder rung. Everything at rung 5 —
or at rung 2–4 with a known vacuous-pass risk — is the backlog, usually one
small change per item. Ratchet one level at a time: rung 5 → 3 today beats the
perfect rung-1 rewrite that never lands. Not everything deserves a ratchet: a
style preference doesn't need a CI gate; a security default or a data-integrity
invariant does, and a gate that fires false positives weekly gets deleted along
with its protection.
