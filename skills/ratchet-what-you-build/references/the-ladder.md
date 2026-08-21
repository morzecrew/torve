# The enforcement ladder, and how to design the rung above

The half-shipped taxonomy in full, and what a gate needs to be worth wiring in.
`SKILL.md` carries the closing question and the calibration.

## The half-shipped taxonomy

Recognize the shapes — each is a mechanism whose enforcement was deferred and forgotten:

- **Opt-in safety:** the accounting/verification/strict mode exists behind a flag nobody sets. Flip the default; make opting *out* the declared act.
- **Check without a gate:** the battery/linter/floor runs locally or on demand but not in CI. Wire it in; a check that can be forgotten will be.
- **Unsafe default beside a safe mechanism:** `ttl=None`, `verify=False`, permissive fallback — the safe value exists and isn't the default. Flip it; grandfather existing callers explicitly if needed.
- **Enrollment gap:** the battery/manifest covers today's implementations, but a *new* implementation can ship without enrolling and nothing fails. This is the subtlest — absence doesn't fail. Derive the required set from the codebase and gate on it (below).
- **Verdict over-claim:** the mechanism reports stronger guarantees than it checks ("covered" that was never verified). Downgrade the claim or upgrade the check — an over-claiming gate is worse than none, because it spends trust.

## Designing the gate itself

A ratchet is code; it has its own failure modes. Three rules, each learned the hard way:

- **Prove the gate can fail — in both directions, end-to-end.** Before trusting it: remove an enrollment, watch it fail naming the gap; add a typo'd declaration, watch it fail naming the typo. A gate never seen red is rung-5 convention wearing a gate's clothes (the same verified-red rule as `reproduce-then-fix`).
- **An empty derivation satisfies every subset check while proving nothing.** "Derived ⊆ declared" only ratchets when the derivation actually found something — if the census of implementations returns empty (wrong key, moved registry), the check passes vacuously and the hole silently reopens. Assert non-emptiness; make a derivation source that resolves to nothing a *hard error*, not an empty set.
- **Waivers must be verified against reality.** Exemptions ("single-engine", "not applicable here") are claims; re-check them in the gate so a stale waiver fails when reality changes. An unverified waiver is a permanent hole with paperwork.

Keep the gate fast and offline (seconds, no network) — a slow ratchet gets removed from CI, which is the decay it existed to prevent.

**A genuinely per-deployment choice is configuration, not a missing ratchet** —
but the *unsafe* setting is still the one that has to be spelled out. Leaving
both options equally silent means the risky one gets chosen by whoever read the
defaults least carefully.
