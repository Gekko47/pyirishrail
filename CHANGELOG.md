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

### Fixed (Phase 2)

- Same-direction reconfigure no longer erases the config entry's
  `unique_id`: the flow forwards an identity to `async_update_entry` only
  when it actually claimed a new one. Home Assistant 2026.8 reindexes an
  explicit `unique_id=None` to `None`, which silently stripped the identity
  that entity/device registry linkage depends on and broke the entry after
  the next restart.
- The shared request gate is released only when the *last* config entry
  unloads. Previously any unload dropped the process-wide gate while
  sibling entries kept running on the dropped instance and new clients
  built a second one, splitting the shared rate budget.
- Entry lifecycle is tracked by a set of loaded entry ids instead of a
  counter, making setup idempotent: automatic retries after
  `ConfigEntryNotReady` can no longer leave phantom counts — and a running
  API-health probe — behind after all entries are removed.
- `async_get_station_stops_at_options` isolates unexpected route-lookup
  errors (`gather(..., return_exceptions=True)`): a bug in one lookup can
  no longer escape into the config-flow step that calls it.
- `StopsMatrixStore.async_record` serializes concurrent writers on a lock
  so each merge observes the previous merge's result and each save carries
  it.

### Changed (Phase 2)

- Entry identity helpers (`build_unique_id`, `normalized_direction`) moved
  to a dedicated `identity.py` module, removing the coordinator →
  config_flow import; `DUBLIN_TZ` is now defined once in `const.py`;
  diagnostics reads the backoff state through a new public
  `coordinator.failure_streak` property. Full suite: 229 passed at
  100.00% coverage with ruff and strict mypy clean.

### Removed (Phase 3)

- The `defusedxml` runtime dependency. The integration no longer lists
  any third-party requirements in its `manifest.json`; XML parsing is
  performed by Python's standard library `xml.etree.ElementTree`, which
  on the Home Assistant 2026.8 floor (Python 3.14.2's bundled expat
  2.7.5) already rejects entity declarations and external-entity
  resolution with `ParseError`.

### Added (Phase 3)

- An explicit pre-parse DTD/entity guard on the single XML parse choke
  point (`pyirishrail/api.py::_request`). The guard runs against a
  pre-lowered copy of the response body and rejects any `<!doctype`,
  `<!entity`, `<!element`, `<!attlist`, or `<!notation` keyword,
  closing the one gap the stdlib parser leaves open (DTDs without
  entities). The policy is now independent of the bundled expat
  version.
- Hostile-input tests parametrized over internal-entity bombs, XXE
  (http and `file://`), DTD-without-entities, and an uppercase DOCTYPE
  payload, plus a positive test pinning the namespaced-valid path.

### Changed (Phase 3)

- CI workflow no longer installs the `types-defusedxml` type stub.
- `pyirishrail` package docstrings document the zero-dependency XML
  policy and the rationale for the explicit pre-parse guard. Full
  suite: 235 passed at 100.00% coverage with ruff and strict mypy
  clean, and verified to run green with `defusedxml` uninstalled from
  the venv (the zero-dep proof).

<!-- Phases 1–5 append their entries here as they land. -->
