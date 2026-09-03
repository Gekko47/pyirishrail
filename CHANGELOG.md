# Changelog

All notable changes to this project are documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] — 2026-09-03

Maintainability release. The integration has no active users, so
this release makes no migration changes. Phases A–C of the streamline
roadmap collapsed the per-station sensor surface, dropped the
`num_trains` option, and renamed three over-clever identifiers. The
plan, per-phase status, and decisions are recorded in
[`.cline/streamline-roadmap.md`](.cline/streamline-roadmap.md).

### Highlights

- **Two sensors per station.** The three per-station sensors collapse
  to two: `next_train_due` (unchanged TIMESTAMP presentation) and
  `following_train_due` (same presentation for the second train).
  `next_train_destination` and `next_train_delay` are removed.
- **Fixed attribute surface.** Each sensor carries a small fixed set
  of attributes (7 keys on next, 5 on following). The
  `upcoming_trains` attribute is removed.
- **No `num_trains` option.** The coordinator retains at most two
  trains (next + following); there is no user-configurable knob.
  First-config and re-config behave identically.
- **`pyirishrail` sub-package folded.** The client modules now live
  directly in `custom_components/irish_rail/` (`client.py`,
  `request_gate.py`, `models.py`, `errors.py`, `lib_const.py`).
- **`quality_scale.yaml` compressed** from 22.7 KB / 438 lines to
  10.4 KB / 69 lines (–54% size, –84% line count).

### Removed (Phase C)

- `next_train_destination` and `next_train_delay` sensors.
- The `upcoming_trains` attribute from all per-station sensors.
- The `num_trains` configuration option (setup + options flow).
- The `pyirishrail/` sub-package (folded into the integration
  package).

### Changed (Phase A)

- `pyirishrail/api.py` → `client.py`, `pyirishrail/_const.py` →
  `lib_const.py`, `pyirishrail/_gate.py` → `request_gate.py`,
  `pyirishrail/errors.py` → `errors.py`, `pyirishrail/models.py` →
  `models.py`.

### Renamed (Phase D)

- `IrishRailApiHealthMonitor` → `ConnectivityMonitor`
- `async_claim_global_provider` → `claim_service_entities`
- `previous_unique_id()` → `applied_unique_id()`

### Gates

- All gates green: 248 passed, 100.00% coverage, ruff clean,
  strict mypy clean (37 files).

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

## [0.3.0] — post-release amendment (2026-08-30)

### Security — XML safety policy (design 2)

The v0.3.0 release shipped a byte-level substring guard on the
response body as the *sole* XML safety policy, on the assumption
that the stdlib `xml.etree.ElementTree` parser alone is sufficient.
Empirical re-verification on 2026-08-30 found that the stdlib
parser on Python 3.14.2's bundled expat 2.7.5 does **not** reject a
real 10×10×10 billion-laughs bomb on its own: it parses in 0.4 ms
with 1000 chars of expanded content, because the default
`XML_PARAM_ENTITY_PARSING_ALWAYS` mode lets the internal subset
expand. `SetParamEntityParsing(NEVER)` only disables *external*
parameter-entity (DTD) processing, not internal subset processing,
so it does not block the billion-laughs case either. The byte-level
guard is therefore load-bearing for the billion-laughs case, not a
defensive overlay.

Final design (design 2, two-layer, no third layer):

- **stdlib `ET.fromstring` first** — catches whitespace-obfuscated
  forms (`<! DOCTYPE` etc.) via `ET.ParseError`; the integration
  wraps the parser error as `IrishRailParseError`.
- **Byte-level substring guard second** — catches the
  billion-laughs bomb via the `<!entity` keyword in the internal
  subset, *before* any entity expansion happens. The lowercased
  scan is case-insensitive on the keyword and the `&lt;`
  entity-escaped form is not a hit (no literal `<!` in the raw
  bytes).
- A **third layer (post-parse tree-walk) was tried and removed**
  because it backfires on the real RTPI shape: Irish Rail's
  serialiser emits special characters in plain text fields as
  `&lt;`, so the parsed tree's `elem.text` contains the literal text
  `<!doctype` after entity decoding, and the tree-walk rejected a
  perfectly valid response. The test
  `test_escaped_doctype_substring_in_text_parses` pins that
  success path.

### Known tradeoff — CDATA false-positive class

The byte-level guard has a known false-positive class:
`<![CDATA[...<!doctype ...]]>` sections in a station field trip
the substring check because the inert prose inside CDATA contains
the literal text `<!doctype`. Empirically verified 2026-08-30 that
Irish Rail's RTPI responses use the standard entity-escaped form
(`&lt;!doctype`) in plain text fields, not CDATA, so this case is
not observed on the real API. The CDATA case is pinned by the
regression test
`test_cdata_section_with_doctype_substring_is_rejected`, so the
documented tradeoff cannot regress silently. If a future revision
ever needs to accept CDATA-bearing payloads, it must explicitly
remove that test and document the change here.

### Tests

- New hostile-payload case `billion-laughs-real` in
  `test_dtd_or_entity_payload_is_rejected` (6 cases total).
- `test_cdata_section_with_doctype_substring_is_rejected` (was
  option A's `*_parses`) now asserts the documented tradeoff.
- `test_external_dtd_only_doc_is_rejected_by_guard` (was option
  A's `*_parses_under_option_a`) now asserts rejection at the
  byte-level guard layer.
- All gates green: 244 passed, 100.00% coverage, ruff clean,
  strict mypy clean (39 files).

## [0.3.1] — 2026-08-31

### Public surface — cross-package underscore-prefix dependency removed

The v0.3.0 release shipped a deliberate "cross-package
private-symbol contract" where the integration's
`matrix_rebuild` button and the offline
`scripts/build_stops_matrix.py` seed generator both reached into
`pyirishrail.api` for the leading-underscore helper
`_scoped_journey_stops`. The contract was documented in the
`pyirishrail` package docstring and in the integration
roadmap. With the package now vendored in the same repo under
the same commits and the same CI, the external-drift risk that
originally motivated the contract is gone, but the
underscore convention still signalled "don't build on this
shape" and was a footgun for future contributors editing
`api.py` in isolation. The contract has been replaced with a
documented public surface.

- **`IrishRailClient.scope_journey_stops(...)`** is a new public
  method on the client that delegates to the module-private
  `_scoped_journey_stops` helper. The integration's
  `matrix_rebuild` button and `scripts/build_stops_matrix.py`
  both call it via the client instance they already hold; no
  cross-package underscore imports remain in the production
  tree.
- **`strip_namespaces`** is a new public re-export at
  `pyirishrail` package level. It is an alias for
  `pyirishrail.api._strip_namespaces` (the function name is
  unchanged for `git blame` continuity). Test stubs that need
  to mimic the client's parse-side normalization now import the
  public name; the only cross-module consumer of the
  underscore helper was in `tests/components/irish_rail/test_client.py`
  and has been moved over.

### Documentation

- `pyirishrail/__init__.py` docstring rewritten: the duplicated
  "Public surface" block (a paste artifact from the v0.2.0
  → vendored transition) is collapsed to a single copy; the
  import example now includes `strip_namespaces`; the
  "Private helpers" paragraph no longer documents the
  cross-package underscore import and instead states the
  general convention. The stale reference to a non-existent
  `pyirishrail.api._DTD_DECL_RE` symbol is corrected to
  `_DTD_KEYWORDS` (the actual pre-parse-guard constant).
- `pyirishrail/README.md` "Public API" table gains a row for
  `strip_namespaces` and an annotation on the `IrishRailClient`
  row pointing at `scope_journey_stops`. The "deliberately
  reaches into one of them" paragraph is replaced with a
  description of the new public method.

### Tests

- `test_scope_journey_stops_method_delegates_to_helper` (new)
  pins the public method on a real `IrishRailClient` instance
  and confirms it returns the same answer as the helper.
- `tests/components/irish_rail/test_matrix_rebuild.py` and
  `test_button.py` switch from `patch.object(IrishRailClient,
  "scope_journey_stops", ...)` (which triggered mypy --strict
  `[method-assign]` errors) to the module-level string-path
  form `patch("custom_components.irish_rail.pyirishrail.IrishRailClient.scope_journey_stops", ...)`
  that `test_config_flow.py` already uses for class-method
  patches. `_client_mock` is updated to return a real
  `IrishRailClient(MagicMock())` instance (with `cast(MagicMock, ...)`
  on the return) so class-level patches reach the test
  client through normal attribute lookup while the static
  type remains `MagicMock` for test-body attribute
  assignment.
- The implementation note at
  `.cline/irish-rail-improvement-roadmap.md:487–494` (the
  "cross-package private-symbol contract" entry) is preserved
  for audit history and annotated with a **Resolved
  2026-08-31** closure that names the new public surface and
  explicitly says "do not reinstate" the underscore import
  pattern.

### Gates

- All gates green: 245 passed (one new test for
  `scope_journey_stops`), 100.00% coverage, ruff clean,
  strict mypy clean (39 files).
- `grep` audit: zero cross-module
  `from .pyirishrail.api import _*` or `ir_api._*` references
  remain in `custom_components/irish_rail/` and `scripts/`.
- The pre-existing 3 ruff errors in
  `tests/test_win_stubs.py` (an import-ordering issue and two
  `BLE001` blind-`except` findings) are unchanged on master
  and not introduced by this work.
