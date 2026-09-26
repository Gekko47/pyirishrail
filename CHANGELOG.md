# Changelog

All notable changes to this project are documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versioning follows [Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-09-05

Correctness release driven by a full audit of the integration against
the Home Assistant integration contract. Phase F of the streamline
roadmap; the plan, per-increment status and the five decisions taken
during execution are recorded in
[`.cline/streamline-roadmap.md`](.cline/streamline-roadmap.md).

**The integration has no active users**, so this release carries no
config-entry migration. Two behaviour changes are user-visible and are
called out under **Fixed** below.

### Fixed

- **The "stops at" filter was silently lost on any options save.**
  `resolve_stops_at` reads `entry.options` before `entry.data`, and the
  options flow always wrote `stops_at` — normalising "All" to `None` —
  even when the user had only changed the scan interval. A filter
  configured during setup was therefore cleared the first time anyone
  opened **Configure**. The key is now omitted only when `entry.data`
  already supplies that exact value, so an unrelated save is
  non-destructive while an explicit "All" still clears the filter.
- **A stored filter could vanish from the options dropdown.** The
  `stops_at` options were built purely from the live station list, so a
  transient station-list fetch (or any list that omitted the stored
  value) rendered a select whose default was not one of its options;
  submitting dropped the filter. The stored value is now merged into
  the options, mirroring what the direction step already did.
- **A malformed `Duein` reported the train as "due now".** The value
  was coerced to `0`, and `due_in_mins` was typed `int`, so the
  documented `HH:MM` fallback could never run. `TrainDueTime.due_in_mins`
  is now `int | None`; a missing or unparseable value resolves against
  the supplied `Exparrival` — interpreted as **Irish civil time**
  (`Europe/Dublin`) rather than UTC, which the previous draft of that
  path would have done an hour wrong during IST.
- **A direction reconfigure destroyed entity customisations.** Entity
  names, icons and areas were deleted with the old identity's registry
  rows and could not be recovered. They are now captured before the
  purge and re-applied to the re-created entities. Entity IDs are still
  regenerated (they derive from the new unique ID) — the README claim
  that the original IDs "return with names and areas kept" was
  inaccurate and has been corrected.
- **The health probe could keep drawing from a discarded gate.** A
  full unload releases the shared `RequestGate` but retains the
  `ConnectivityMonitor`, which was never re-pointed, so the 5-minute
  probe stopped being paced alongside live polling after a reload. The
  monitor now rebinds the incoming entry's client when no entry owns it.

### Changed

- **`hass.data[DOMAIN]` has a single writer.** The providership claim,
  the session-scoped keys and the stops-store handle all went through
  `_runtime.py` accessors, matching what `docs/architecture.md` §11
  already claimed. A new **CI grep gate** fails the build if any other
  module touches that mapping, so the invariant is structural rather
  than conventional.
- **The coordinator no longer shadows a base-class property.** It
  overrode `DataUpdateCoordinator.update_interval`, called the base
  property's `fset` behind a `# type: ignore`, and wrote the private
  `_update_interval_seconds`; it now assigns the real public property,
  whose setter already maintains HA's own scheduler cache. This
  removes the last `# type: ignore` from the source (making the
  `strict_typing` quality-scale claim true) and the forward-compat
  coupling to two private internals.
- **The two load-bearing `assert`s became explicit errors.** The
  entity base class formatted `config_entry.unique_id` *before*
  asserting it was non-`None`, so a missing ID produced a
  `"None_next_train_due"` unique ID and a `(DOMAIN, None)` device
  identifier instead of failing.
- **The stops-matrix rebuild bounds its movement cache.** A full sweep
  retains every movement row for the whole run; it is now capped like
  the client's own cache (other dates evicted first, then oldest), which
  matters on the SD-card and Pi hosts this integration targets.
- **CI pins `pytest-homeassistant-custom-component`.** The plugin
  tracks Home Assistant core versions strictly, so an unpinned install
  could break CI on any release day with no code change.
- **The `hacs.json` Home Assistant floor is `2026.8.0`** rather than
  the exact tested patch, since the field is a minimum.

### Added

- **`tests/components/irish_rail/test_quality_scale.py`** resolves
  every evidence pointer in `quality_scale.yaml` — file names, symbol
  references and the README headings the `docs_*` rules cite — so
  stale evidence fails the build instead of a review. It immediately
  caught three further stale citations.
- **Request-gate churn coverage:** a 200-cycle admit/cancel loop
  asserting the in-flight counter never leaks, never goes negative and
  never exceeds `max_concurrent`, plus a repeat-cancellation test.
- **Lifecycle cleanup verification:** `verify_cleanup` on the setup /
  unload / reload tests, so leaked timers and un-awaited tasks fail
  loudly.
- **Log-volume pinning:** one debug line per failed poll, exactly one
  info line on the transition back to success, and no repeat
  announcement on subsequent successes.

### Tests

- The scheduler test that compared `loop.call_at` against wall-clock
  time with a ±1 s tolerance is replaced by an assertion on the public
  `update_interval` — the value HA actually arms its timer from — so it
  cannot flake on a loaded runner.
- `verify_cleanup` and the per-poll logging assertions close the two
  gaps the audit identified as genuinely unpinned.

### Fixed (pre-existing defects found while executing the plan)

- **`quality_scale.yaml` did not parse as YAML.** An unquoted
  `requirements:` inside the `async_dependency` comment broke the flow
  mapping, so the file proving the Platinum claim was unreadable by a
  standard parser. Nothing had been validating it.
- **`action_setup` was marked `exempt`** with the justification "no
  service actions to register", while `button.py` registers
  `irish_rail.rebuild_stops_matrix`. Corrected to `done` with a
  pointer.
- Five more evidence rows pointed at code deleted in 0.4.0
  (`next_train_delay`, `next_train_destination`, the `num_trains`
  option) or at README sections that never existed. All corrected.

### Documentation

- Removed the reference to the `pyirishrail/` package deleted in
  0.4.0, the duplicate **Removal** section, an orphaned table row, and
  the claim that train type is exposed as an attribute (it is not).
- **Both automation examples were rewritten.** They used
  `numeric_state` triggers with `below:` against a **TIMESTAMP**
  sensor, which never fires; they are now template triggers. The
  "recomputed on every read" claim for `time_until_arrival` was also
  corrected — it is recomputed per poll, not continuously.
- `docs/architecture.md` §4 already documented the XML guard's CDATA
  false-positive accurately and was verified rather than edited.

### Gates

- All gates green: **296 passed** (up from 267), **100.00 % coverage**,
  ruff clean, strict mypy clean across 38 files, project-internal
  reference gate clean.

### Known limitations

- The narrow window in which a caller cancelled *while* the request
  gate's slot release is blocked on its own lock remains un-hardened.
  It is documented in `docs/architecture.md` §3 and covered by the
  churn test, but no production change was made: the race could not be
  reproduced deterministically, and the existing cancellation paths are
  well covered.
- `hassfest` and the HACS validator run only in CI; the pinned
  `pytest-homeassistant-custom-component` version should be confirmed
  against the first CI run.

## [0.4.0] — 2026-09-04

Maintainability release. The integration has no active users, so
this release makes no migration changes. Phases A–C of the streamline
roadmap collapsed the per-station sensor surface, dropped the
`num_trains` option, and renamed three over-clever identifiers. After
Phase D, the health monitor gained a listener API with
shutdown/reload hygiene, and one raising listener can no longer break
the notify loop. The plan, per-phase status, and decisions are
recorded in
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
- **Health-monitor listeners.** Entities register callbacks for
  connectivity-state changes via `ConnectivityMonitor.add_listener()`
  (a removal callback is returned); shutdown clears the listeners and
  resets the health state so a reload starts clean.

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

### Added (post-D4)

- **`ConnectivityMonitor` listener system.** `add_listener()`
  registers a health-state-change callback and returns a removal
  callback, so entities can register for connectivity transitions and
  detach cleanly.
- **Runtime wiring for entity restore.** The rebuild/last-rebuild
  store keys and the shared stops store are reachable through
  `IrishRailRuntimeData` for improved entity-restore handling.

### Changed (post-D4)

- **Shutdown/reload hygiene.** `ConnectivityMonitor.async_stop()`
  clears listeners and resets health state;
  `RuntimeRegistry.loaded_entry_ids` is reset on shutdown so a reload
  starts from a clean state.
- **Formatting normalized.** A black formatting pass joined the
  wrapped signatures across the integration and its tests.
- Exception-suppression `noqa` comments consolidated: the `BLE001`
  markers now sit on the two deliberate-collection handlers in
  `tests/test_win_stubs.py`; the `_runtime.py` listener guard keeps
  its rationale as a plain comment.

### Fixed (post-D4)

- **Raising listeners are isolated.** `notify_listeners()` guards
  each listener: a single misbehaving callback can no longer abort
  the notify loop or escape the fire-and-forget ping task as an
  unretrieved-task traceback. The failure is logged at warning level
  and the remaining listeners still fire.

### Tests and CI

- New all-platform smoke test pins that the `tests/win_stubs` plugin
  shim imports cleanly on non-Windows hosts, so CI on macOS/Linux
  does not fail at plugin load time (Phase E3).
- CI docstring-density gate raised from 0.20 to 0.21 (Phase A
  closed).

### Gates

- All gates green: 267 passed, 100.00% coverage, ruff clean,
  strict mypy clean (37 files).

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


