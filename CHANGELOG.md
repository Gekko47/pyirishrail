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

<!-- Phases 1–5 append their entries here as they land. -->
