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
