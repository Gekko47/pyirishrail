# Skill 00 — Project Context

## Mission

The `irish_rail` Home Assistant custom integration has completed its Bronze
migration (Home Assistant 2026.8 Bronze Integration Quality Scale — all 20
rules satisfied). Current work is driven by
`.cline/irish-rail-improvement-roadmap.md`: functional improvements, Silver and
Gold quality rules, and engineering robustness.

Historical origin (for context only):
- Source project: https://github.com/ttroy50/pyirishrail
- The legacy synchronous library was modernized into the current integration.

Target integration:
- domain: `irish_rail`
- name: `Irish Rail`
- custom integration, HACS-installable
- baseline tier: Bronze (complete); roadmap targets Silver, Gold, and Platinum-path items

## Current state (2026-08-30)

> **Active work is governed by `.cline/clean-cut-baseline-plan.md` (the
> v0.3.0 Clean Baseline).** The snapshot below is the **post-Phase 4**
> state of the repo; the plan file is the source of truth for phase
> status, decisions, and the changelog. Treat the plan file as the
> authority; this skill preserves the architecture invariants only.

- Async client vendored at `custom_components/irish_rail/pyirishrail/`
  (no PyPI package: the name is owned by an unrelated project; Phase 5.3
  was reverted 2026-08-29 and the v0.3.0 Clean Baseline keeps the
  client internal).
- Zero third-party runtime dependencies: `manifest.json` `requirements:
  []`. XML parsing is stdlib `xml.etree.ElementTree` with an explicit
  pre-parse DTD/entity guard.
- One `DataUpdateCoordinator` per config entry + `entry.runtime_data` +
  first-refresh fail-fast; set-based entry lifecycle keeps gate and
  health probe idempotent across retries.
- Config flow with cached station fetch, direction / stops-at / train-
  count filters, unique-ID duplicate protection, reconfigure flow
  (identity preserved on same-direction reconfigure).
- 3 sensors per station/direction (next train due, destination, delay);
  train type lives on the device's `extra_state_attributes`. One
  integration-level "Irish Rail Services" device with the connectivity
  binary sensor and the stops-matrix rebuild button.
- Diagnostics module with partially-masked identifiers; brand assets
  conforming.
- CI gate on Python 3.14: ruff + strict mypy + pytest at 100% line
  coverage.
- One DataUpdateCoordinator per config entry + `entry.runtime_data` + first-refresh fail-fast
- Config flow with cached station fetch, unique-ID duplicate protection
- 4 sensors per station/direction with translation keys and stable unique IDs
- Diagnostics module with redaction; brand assets conforming
- CI gate on Python 3.14: ruff + strict mypy + pytest ≥90% coverage
- Test suite: 36 tests, 99.23% coverage, no deprecation/async warnings

## Legacy implementation details that MUST NOT be reintroduced

- `requests`
- blocking I/O
- `xml.dom.minidom`
- untyped plain dictionaries
- swallowed exceptions
- old unittest-only architecture
- Travis CI
- setup.py / requirements.txt as the primary packaging model

## Required architectural direction (unchanged)

- a standalone async, typed Irish Rail client
- safe XML parsing
- Home Assistant shared web session
- one DataUpdateCoordinator per config entry
- ConfigEntry.runtime_data for runtime objects
- modern config flow (now extended with reconfigure/options flows per roadmap 1.1/1.2)
- modern entities and translations
- pytest-based tests
- modern CI
- HACS metadata
- quality_scale.yaml

## Do not invent upstream semantics

If Irish Rail XML fields have uncertain meanings, inspect the original fixtures/tests and the actual API behavior. Never silently guess:
- negative `Duein`
- cancellation markers
- missing fields
- date formats
- direction values
- station code semantics
- HTTPS availability

When uncertain:
1. state the uncertainty,
2. implement defensive parsing,
3. add a test,
4. add a TODO if the uncertainty cannot be resolved.

## Scope discipline

Work is bounded by the roadmap phases. Do not claim a rule is satisfied without
implementation plus tests. Classify each item by tier:
- Phase 1: functional improvements (reconfigure flow = Gold rule, options flow,
  next-N-trains entities, no-trains semantics)
- Phase 2: Silver rules (`parallel-updates`, `log-when-unavailable`,
  `entity-unavailable`, `config-entry-unloading`, `test-coverage`)
- Phase 3: Gold rules (docs expansion, `icon-translations`,
  `exception-translations`, `repair-issues`, diagnostics edge cases)
- Phase 4: engineering (Platinum-path client extraction, conditional requests,
  adaptive backoff, XML hardening, CI breadth)
- Phase 5: Platinum tier attainment (typed config entry, test-inclusive mypy
  gate, extract `api.py` into a top-level `pyirishrail/` sibling package in
  this same repo and publish to PyPI, evidence & tier-claim paperwork)

Do not turn a stronger-tier requirement into a fake lower-tier rule, and do not
implement items outside the roadmap without recording them in it.

## Actual project tree (post-Phase 5.3, Platinum tier)

pyirishrail/  (NEW — the published library; pip-installs as a wheel)
- __init__.py        # public API re-exports + version
- _const.py          # library-only constants (timeouts, semaphore, cache cap)
- api.py             # IrishRailClient + parse_station_data + private helpers
- errors.py          # exception hierarchy
- models.py          # frozen dataclasses
- py.typed           # PEP-561 marker

custom_components/irish_rail/  (HA integration; non-installable)
- __init__.py
- config_flow.py
- const.py           # integration-only constants (CONF_*, service hours, etc.)
- coordinator.py
- diagnostics.py
- entity.py
- binary_sensor.py
- button.py
- health.py
- matrix_rebuild.py
- store.py
- sensor.py
- manifest.json      # "requirements": ["pyirishrail>=0.2,<1.0"]; no defusedxml
- quality_scale.yaml
- strings.json
- types.py           # IrishRailRuntimeData + IrishRailConfigEntry
- icon.png
- brand/ (icon.png, logo.png, logo@2x.png, small.png)
- icons.json
- services.yaml
- stops_matrix.seed.json
- translations/en.json

tests/
- pyirishrail/                       # NEW — pure library tests, no HA imports
  - conftest.py
  - test_api.py
- components/irish_rail/             # HA integration tests, unchanged otherwise
  - __init__.py
  - conftest.py
  - test_button.py
  - test_config_flow.py
  - test_coordinator.py
  - test_diagnostics.py
  - test_global_setup_edges.py
  - test_health.py
  - test_health_suppression.py
  - test_icons.py
  - test_init.py
  - test_matrix_rebuild.py
  - test_sensor.py
  - test_stops_store.py
  - test_translations.py
- __init__.py
- test_win_stubs.py
- win_stubs.py

Repository root
- pyproject.toml      # builds the pyirishrail wheel; HA integration not pip-installed
- README.md
- LICENSE.txt
- .github/workflows/ci.yml     # two-job matrix: library + integration
- .github/workflows/hacs.yml
- hacs.json
- scripts/build_stops_matrix.py  # uses pyirishrail.api

Add any files required by the current Home Assistant tooling or by roadmap
items (e.g., `icons.json` for Phase 3) rather than blindly adhering to an
outdated file list.

## Post-Phase 5.3 target tree (Platinum tier, revised 2026-08-27)

The `pyirishrail` package becomes a top-level sibling of `custom_components/`
in this **same** repository (no separate repo). The current `api.py` is moved
out of `custom_components/irish_rail/` and re-homed as a proper installable
Python wheel. Git history follows via `git mv`.

pyirishrail/  (NEW — the published library)
- __init__.py
- api.py  (moved from custom_components/irish_rail/api.py)
- models.py  (dataclasses currently embedded in api.py)
- errors.py  (exception hierarchy currently embedded in api.py)
- py.typed  (PEP-561 marker — empty file)

custom_components/irish_rail/  (HA integration, imports change only)
- … (everything above except api.py)
- manifest.json  ("requirements": ["pyirishrail>=0.2,<1.0"], no defusedxml)

tests/
- pyirishrail/  (NEW — pure library tests, no HA imports)
- components/irish_rail/  (HA integration tests, unchanged)

Repository:
- pyproject.toml
- README.md
- LICENSE.txt
- .github/workflows/ci.yml
- hacs.json

Add any files required by the current Home Assistant tooling or by roadmap
items (e.g., `icons.json` for Phase 3) rather than blindly adhering to an
outdated file list.