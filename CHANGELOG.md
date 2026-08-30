# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Remediation — v0.3.0 Clean Baseline (in progress)

Repository-wide remediation tracked in
[.cline/clean-cut-baseline-plan.md](.cline/clean-cut-baseline-plan.md). The
integration has no active users; the remediation is a clean cut with no
migration path. Entries are appended as each phase lands.

### Fixed (Phase 0)

- Completed the 2026-08-29 revert of the Phase 5.3 PyPI-package extraction:
  the async client is vendored at `custom_components/irish_rail/pyirishrail/`
  and the previously untracked `gate.py`, `pyirishrail/_gate.py`, and gate
  test files are now tracked, so a fresh clone of the integration imports
  cleanly.

### Removed (Phase 0)

- Abandoned top-level `pyirishrail/` package remnants and `tests/pyirishrail/`.
- Build/publish artifacts (`dist/`, `build/`), seed-build logs, `uv.lock`,
  and `.qodo/` local tooling state.
- Stale editable `pyirishrail` install from the development environment.

### Fixed (Phase 1)

- Test suite honest baseline restored: 117 stale `pyirishrail.*` patch and
  import targets across six test files were retargeted to the vendored
  `custom_components.irish_rail.pyirishrail` package, unmasking 64 previously
  failing tests; the gate-cancellation test's queue expectation was
  corrected, and the gate-sharing test now adds its second config entry
  after the component is loaded (Home Assistant sets up every registered
  entry of a domain during component setup, so the early-added entry made
  the explicit second `async_setup` raise `OperationNotAllowed`).
- 11 strict-mypy errors and 4 ruff findings resolved across tests and
  integration code: rebuild failures now log via `_LOGGER.exception`, the
  bundled-seed shape check raises `TypeError` (with the loader's catch
  widened to match), the health probe's deliberate broad catch carries a
  justified `noqa`, and an unused test variable was removed.
- `RequestGate`: removed an unreachable silent guard in the
  cancelled-waiter cleanup that would have leaked an admitted slot if the
  gate's lock discipline were ever broken; the queue removal now fails
  loudly instead.

### Added (Phase 1)

- Coverage tests for the sensor's degraded `HH:MM` fallback resolution and
  the stops-matrix rebuild's per-bucket persistence-failure isolation.
  Full suite: 226 passed at 100.00% coverage with ruff and strict mypy
  clean.

<!-- Phases 1–5 append their entries here as they land. -->
