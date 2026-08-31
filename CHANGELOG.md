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
