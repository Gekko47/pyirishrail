# Changelog

All notable changes to this project are documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] — 2026-08-30

The v0.3.0 Clean Baseline. The integration has no active users, so
this release is a clean cut with no migration path: every claim in
the docs and quality scale was either proven against the
implementation or removed. The plan, per-phase status, and
decisions are recorded in
[`.cline/clean-cut-baseline-plan.md`](.cline/clean-cut-baseline-plan.md).

### Highlights

- **Zero third-party runtime dependencies.** The integration no
  longer lists any requirements in `manifest.json`. XML parsing is
  stdlib `xml.etree.ElementTree` guarded by an explicit pre-parse
  DTD/entity policy (`pyirishrail/api.py::_DTD_KEYWORDS`).
- **`pyirishrail` package vendored.** The 2026-08-28 PyPI extraction
  was reverted 2026-08-29 (the name is owned by an unrelated
  project). The client lives at
  `custom_components/irish_rail/pyirishrail/`, framework-agnostic
  and framework-import-free, ships `py.typed` (PEP 561).
- **All gates green.** 235 tests, 100.00% line coverage
  (`--cov-fail-under=100`), ruff clean, strict mypy clean.
- **Every `done` in `quality_scale.yaml` is now evidenced** with a
  current, accurate file/function pointer. The previously
  incorrect `dependency_transparency` claim about a
  `pyirishrail>=0.2,<1.0` pin is gone.

### Fixed (Phase 2)

- **Reconfigure `unique_id` erasure.** The reconfigure flow no
  longer forwards `unique_id=None` to `async_update_entry`, which
  HA 2026.8 reindexes to `None` and silently strips the entity /
  device registry linkage. Same-direction reconfigures now
  preserve the existing unique ID verbatim.
- **Shared-gate lifecycle.** The request gate is released only when
  the *last* entry unloads; previously any unload dropped the
  process-wide gate while siblings ran on a dropped instance and
  new clients built a second one, splitting the shared rate budget.
- **Idempotent setup.** Loaded entries are tracked by a set of
  entry ids, so a `ConfigEntryNotReady` retry can no longer leave
  phantom counts (and a running health probe) behind.
- **`async_get_station_stops_at_options` isolates unexpected route-
  lookup errors** via `gather(..., return_exceptions=True)`: a bug
  in one lookup can no longer escape into the config-flow step.
- **`StopsMatrixStore.async_record` serializes concurrent writers**
  on an `asyncio.Lock` so each merge observes the previous merge's
  result and each save carries it.

### Changed (Phase 2)

- Entry identity helpers (`build_unique_id`, `normalized_direction`)
  moved to `identity.py`; the coordinator no longer imports
  `config_flow`. `DUBLIN_TZ` is defined once in `const.py`.
  Diagnostics reads the backoff state through a new public
  `coordinator.failure_streak` property.

### Removed (Phase 3)

- **`defusedxml` runtime dependency.** The integration's
  `manifest.json` declares no third-party requirements. XML parsing
  is stdlib `xml.etree.ElementTree`, which on the Home Assistant
  2026.8 floor (Python 3.14.2's bundled expat 2.7.5) already
  rejects entity declarations and external-entity resolution with
  `ParseError`.
- `types-defusedxml` CI dependency. `pyirishrail/api.py` no longer
  imports `defusedxml`.

### Added (Phase 3)

- **Pre-parse DTD/entity guard** on the single XML parse choke
  point (`pyirishrail/api.py::_request`). Runs against a
  pre-lowered copy of the response and rejects any of `<!doctype`,
  `<!entity`, `<!element`, `<!attlist`, `<!notation`. The policy is
  independent of the bundled expat version.
- **Hostile-input tests** parametrized over internal-entity bombs,
  XXE (HTTP and `file://`), DTD-without-entities and an uppercase
  DOCTYPE payload, plus a positive test pinning the namespaced-
  valid path.

### Fixed (Phase 1)

- 117 stale `pyirishrail.*` patch / import targets retargeted to
  the vendored `custom_components.irish_rail.pyirishrail` package
  (unmasked 64 previously failing tests).
- Gate-cancellation test now expects the queue to be empty after
  cancelling the only queued waiter; the gate-sharing test adds
  the second entry after the component is loaded so the second
  `async_setup` does not raise `OperationNotAllowed`.
- 11 strict-mypy errors and 4 ruff findings resolved across tests
  and integration code; the dead `except ValueError: pass` guard
  in the gate's cancelled-waiter cleanup was removed in favour of
  a loud failure that catches a future refactor breaking the lock
  discipline.
- Two coverage tests added: the sensor's degraded `HH:MM` fallback
  and the stops-matrix rebuild's per-bucket persistence-failure
  isolation.

### Removed (Phase 0)

- The abandoned top-level `pyirishrail/` package remnants and
  `tests/pyirishrail/`.
- Build / publish artifacts (`dist/`, `build/`), seed-build logs,
  `uv.lock` and `.qodo/` local tooling state.
- The stale editable `pyirishrail` install from the development
  environment.
- Dead `.cline/implementation_plan.md` (Phase 4).

### Documentation (Phase 4)

- Full `README.md` rewrite: accurate quick-facts, 3 sensors per
  station/direction, two integration-level entities on the *Irish
  Rail Services* device, professional examples / use cases /
  behaviour / stops-at / troubleshooting sections; fluff removed.
- `pyirishrail/README.md` rewritten to the zero-dep XML story
  with the explicit pre-parse guard documented.
- `services.yaml` + `strings.json` no longer describe the global
  entities as device-less — they share the *Irish Rail Services*
  device.
- `quality_scale.yaml` pointers updated to current, accurate
  file/function references; obsolete defusedxml / PyPI
  descriptions removed.
- Skills 00 / 07 / 08 and the plan file truth-passed: the
  "2026-08-28 PyPI extraction" section in the roadmap is marked
  REVERTED with a pointer to this changelog and the plan file.
