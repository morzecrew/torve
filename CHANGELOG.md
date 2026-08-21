# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- RFC corpus 0001–0010 under `rfcs/` with index.
- `torve` package: the RFC 0002 gates-library increment.
- Six builtin gates (`scope`, `acceptance`, `no-test-tampering`,
  `decisions-reported`, `self-audit`, `secrets`) plus shell gates via
  `gates.yaml`; cheapest-first ordering with fail-fast on blocking failures.
- `flaky` gate outcome with per-command counters and a reviewed quarantine
  list; `Torve-Bypass` commit-trailer bypass, counted and logged, with the
  secrets gate exempt.
- `torve gates run` / `torve gates check` (17-case sabotage suite) and
  `torve size`; JSONL telemetry stamped with `config_hash` and denormalised
  decisions.
- Dogfood wiring for this repository: `gates.yaml`, task contract
  `tasks/T-0002.yaml`, execution log `logs/T-0002.md`, GitHub Actions CI.
- Runner core (RFC 0003 phase 1): `torve run` — one task, synchronous, exit
  code is the outcome — with the state machine and enumerated escalation
  reasons, git-worktree workspaces, and JSON run state beside the worktree.
- Two Runtime adapters behind one "workspace in, changed files out" contract:
  Docker (bind mount, `--init`, platform-bounded lifecycle) and OpenSandbox
  (tar sync over the files API; SDK as the `torve[opensandbox]` extra), with a
  shared conformance battery.
- `FakeAgent` scripted scenarios (always sandboxed), `torve status`, and
  `torve reap` — convention-driven sweep that expires stale runs as
  `lease_expired`, proven after `kill -9`.
- Shell gates in `torve run` execute in a fresh sandbox the agent never
  touched, via a new executor seam in the gate runner; `torve.yaml` carries
  runner configuration.
- Durability (RFC 0003 phase 2): the attempt loop runs as one durable
  function over forze's run store — real leases, fenced terminal writes,
  cancellation riding the lease heartbeat, and recovery via
  `claim_abandoned`. Mock store for tests and simulation, Postgres for real
  runs (`torve[postgres]`), with torve-owned DDL and `torve store provision`.
- `torve cancel` (cooperative, fail-closed on backend capability) and a
  durable reap path that replaces the heartbeat heuristic under Postgres.
- Deterministic simulation (forze_dst): the real attempt loop and real
  TaskStore driven concurrently under one master seed set — four invariants,
  four reachability targets, and four deliberately broken twins the oracle
  must catch.

### Changed

- `requires-python` is now `>=3.13,<3.15` (the forze substrate's floor).
