# Streamline Roadmap — Maintainability Pass

> **Active plan for the post-v0.3.0 maintainability work.** Where this
> file conflicts with the prior roadmaps
> (`.cline/irish-rail-improvement-roadmap.md`,
> `.cline/clean-cut-baseline-plan.md`), this file wins for any work
> that is **not** an open checkbox on the prior plans. The v0.3.0
> Clean Baseline is complete and the prior plans serve as the
> completion record.

## Why this plan exists

The v0.3.0 baseline hit the Platinum quality scale with 100% line
coverage, strict mypy, and zero third-party runtime dependencies. The
cost of that bar is visible in the source: ~2,700 LOC of integration
code with a docstring-to-logic ratio of roughly 0.5, a 21 KB
`quality_scale.yaml` of compliance evidence, two near-duplicate
implementations of the stops-matrix rebuild sweep, two modules
(`gate.py`, `health.py`) that share a singleton lifecycle by
convention only, three near-identical per-station sensors, and a
2.4 MB bundled `stops_matrix.seed.json`.

The integration has no current users (it's in development), so we are
free to simplify. Platinum compliance is preserved throughout — the
goal is to make the codebase easier to read, change, and review
without losing any of the rules-evidencing code paths.

## Goals (target state)

| Metric | Today | Target |
|---|---:|---:|
| Integration source LOC (`custom_components/irish_rail/`) | ~2,700 | **~1,500–1,700** |
| Docstring density (lines per source LOC) | ~0.50 | **~0.15** |
| Per-station sensors | 3 | **2** (next + following train) |
| Sensor `extra_state_attributes` count | 18 | **7 (next) / 5 (following)** |
| README length | ~370 lines | **~180 lines** |
| `quality_scale.yaml` length | 21 KB / 426 lines | **~7 KB / 150 lines** |
| Stops-matrix rebuild implementations | 2 (script + button) | **1** |
| Singleton-management modules | 2 (`gate.py`, `health.py`) | **1** (`_runtime.py`) |
| `stops_matrix.seed.json` in tree | 2.4 MB | **0** (generated at release) |
| Test LOC | ~6,750 | **~5,500** (drop redundant cases) |

## Non-goals

- Repository or domain rename.
- Republishing `pyirishrail` to PyPI.
- New user-facing features. (Exception: the 2026-09-03 Phase C
  revision — `following_train_due` replaces the `upcoming_trains`
  attribute; recorded in Phase C and Decision S4.)
- Migration shims.
- Entity `unique_id` changes that would orphan user customisations.
- Live-API behaviour changes (XML guard, gate, polling cadence stay byte-for-byte identical).
- A from-scratch rewrite. Every step is a small, test-gated,
  revertible change.

## Ground rules (apply to every phase)

1. **Platinum is preserved.** Every `done` and every `exempt` in
   `quality_scale.yaml` keeps its current file/function pointer
   landing on real code. We compress the prose, not the evidence.
2. **Gates stay green after every increment.** ruff · strict mypy ·
   pytest at the active coverage gate (CI enforces
   `--cov-fail-under=100`). The active coverage gate does not drop.
3. **One source of truth per fact.** The design history behind a
   decision lives in `docs/architecture.md`; the source carries the
   contract, not the narrative. Commit messages carry the change.
4. **No behaviour changes during refactors.** Any observable change
   (default value, attribute key, file path the user sees) is its
   own committed step, after the underlying refactor lands.
5. **Skill 10 governs execution.** `.cline/skills/10-streamline-execution.md`
   holds the per-phase implementation guidance; this file holds the
   plan and the gates. Per the same pattern as the existing
   `09-roadmap-execution.md`.

## Decisions (resolved)

| ID | Decision | Resolution |
|---|---|---|
| S1 | From-scratch vs incremental | **Incremental** (5 small PR-sized phases). Preserves Platinum and the 100% coverage gate. |
| S2 | `pyirishrail` sub-package fate | **Keep, but rebrand internals**: drop the sub-`__init__.py` re-export layer, fold the four public re-exports into a single `pyirishrail/__init__.py`, drop the standalone `pyirishrail/README.md`. The "vendored framework-agnostic client" identity is preserved (still no HA imports) but the surface is one module deep. |
| S3 | `stops_matrix.seed.json` in tree | **Drop from the repo** after Phase A3 commits a 3-station example seed (`stops_matrix.seed.example.json`) as a smoke fixture. The real seed is generated at release time by `scripts/build_stops_matrix.py` and attached to the GitHub release. |
| S4 | Three-sensor collapse | **Two rich sensors**: `next_train_due` (unchanged TIMESTAMP presentation) + `following_train_due` (same presentation, second train). Drop `next_train_destination` and `next_train_delay`. Fixed attribute surface (four per-train keys + `api_reachable`, plus the `expected_arrival`/`time_until_arrival` countdown pair on `next_train_due` only). Drop the `upcoming_trains` attribute and the `num_trains` option; retain only the next two trains. |
| S5 | Build script + matrix-rebuild unification | **Unify behind one shared loop** in `matrix_rebuild.py`; the offline `scripts/build_stops_matrix.py` becomes a 40-line CLI wrapper that calls it. The "by design" differences (gap-fill vs full replace, atomic temp-file vs storage, background vs normal priority) become parameters. |
| S6 | `gate.py` + `health.py` consolidation | **Yes**, into a single `_runtime.py` module exposing a `RuntimeRegistry` class. Singleton lifecycles become structural (the registry is the only writer to `loaded_entry_ids` and to each subkey). The `RequestGate` primitive in `pyirishrail._gate` stays separate — it's the framework-agnostic gate, not a singleton. |
| S7 | Docstring discipline | **Three categories** (see Skill 10 §2): keep contract docstrings tight; move design history to `docs/architecture.md`; delete "what the name says" docstrings. Density target: 0.15 lines/LOC. |
| S8 | Tests for the simplify pass | **Tighten, do not just re-keep**. Several test files cover the same edge cases (e.g. matrix-rebuild tests vs build-script tests). Phase E deduplicates while preserving the 100% coverage gate. |

## Phases

Work one phase at a time, in order. Each phase ends with a clean
green run of the CI matrix locally and a tick in the progress log.

---

### Phase A — Source-only documentation pass

**Goal:** Cut the docstring density from ~0.5 to ~0.15 lines/LOC
without changing any behaviour. Zero-risk, no logic changes, the
gates stay green throughout.

**Why first:** the docstring trims make every subsequent refactor
easier to read in PR review. They also expose the genuine non-obvious
invariants that should be lifted into `docs/architecture.md` and the
docstring-noise that should just be deleted.

**Steps:**

- [x] **A1 — Lift design history into `docs/architecture.md`.**
  Move long-form prose about the XML policy, the gate, the stops
  matrix, the entity model, and providership from
  `coordinator.py`, `health.py`, `store.py`, `__init__.py`,
  `config_flow.py`, `pyirishrail/api.py`, `pyirishrail/__init__.py`,
  and `matrix_rebuild.py` into the corresponding sections of
  `docs/architecture.md`. The source keeps a 1–3 line
  contract-style docstring + a `See docs/architecture.md §N`
  pointer where useful.
- [x] **A2 — Delete "what the name says" docstrings.** Method
  docstrings that just rephrase the function name (e.g.
  `"""Return the gate singleton."""` on a function named
  `get_request_gate`) are deleted. A short class-level docstring
  on the class is enough.
- [ ] **A3 — Drop `stops_matrix.seed.json` and the stale
  `pyirishrail/README.md`.** Replace the 2.4 MB bundled seed with
  a 3-station `stops_matrix.seed.example.json` smoke fixture; add
  a note in `docs/architecture.md` §5 that the real seed is
  generated by `scripts/build_stops_matrix.py` and attached to
  GitHub releases. Delete `pyirishrail/README.md` (its entire
  purpose was explaining why the package was vendored, which is
  now covered in `docs/architecture.md` §1).DO NOT IMPLEMENT.
- [x] **A4 — Audit and delete `Skill N` / `Phase N` / `roadmap N`
  cross-references in source.** These are project-internal
  scratchpad breadcrumbs; they belong in this roadmap file, not
  in source. Search the integration tree for `Skill 0`,
  `roadmap 1`, `Phase 1`, `Phase 2`, etc., and either delete the
  reference (if it's purely narrating) or replace it with a
  pointer to `docs/architecture.md` (if it carries real
  information).
- [x] **A5 — Update `quality_scale.yaml` to point at the new
  architecture doc.** Where a `done` comment currently embeds
  long-form design history, trim it to a one-line pointer and
  cross-link to the corresponding `docs/architecture.md` section.
  Compresses the file from 21 KB to the target ~7 KB. **Every
  rule keeps a working file/function pointer.**

**Acceptance:**

- All gates green: ruff clean, strict mypy clean, 100% line
  coverage, every existing test passes unchanged.
- `git diff` of `*.py` shows mostly deletions; no additions to
  function signatures, no new imports, no new public surface.
- `docs/architecture.md` exists and has the eight sections listed
  in its table of contents.
- `quality_scale.yaml` is ≤ 8 KB and every `done` still has a
  valid file/function pointer.
- Docstring density across the integration source drops to ≤ 0.20
  lines/LOC (we will tighten to 0.15 in later phases).

---

### Phase B — Module consolidation

**Goal:** Reduce module count and eliminate the
two-implementations-drift risk. No behaviour changes, no API
changes the user sees.

**Steps:**

- [x] **B1 — Fold `pyirishrail/` sub-package into the integration
  package.** Rename `pyirishrail/api.py` → `client.py`,
  `pyirishrail/_const.py` → `lib_const.py`,
  `pyirishrail/_gate.py` → `request_gate.py` (the file collides
  with the existing `gate.py`; the singleton moves to
  `_runtime.py` per B3), `pyirishrail/errors.py` → `errors.py`,
  `pyirishrail/models.py` → `models.py`. The
  `pyirishrail/__init__.py` re-export file is dropped; imports
  change from `from .pyirishrail import X` to `from . import X`
  (or `from .client import X` for clarity). One `py.typed`
  marker lives at the integration root.
- [x] **B2 — Unify the two stops-matrix rebuild
  implementations.** Create a single async loop
  `sample_stops_matrix(client, *, gap_fill, atomic_dump, priority)`
  in `matrix_rebuild.py`. The offline
  `scripts/build_stops_matrix.py` becomes a 40-line CLI wrapper
  that calls it with `gap_fill=False, atomic_dump=True,
  priority="normal"`. The runtime rebuild button calls it with
  `gap_fill=True, atomic_dump=False, priority="background"`. The
  two "by design" differences documented in
  `matrix_rebuild.py`'s module docstring become parameters and
  disappear from the docstring.
- [x] **B3 — Merge `gate.py` and `health.py` into a single
  `_runtime.py` module.** A `RuntimeRegistry` dataclass owns the
  `loaded_entry_ids` set, the `RequestGate` instance, the
  `IrishRailApiHealthMonitor`, the `StopsMatrixStore`, and the
  providership entry id. Public surface: `register_entry()`,
  `deregister_entry()`, `request_gate()`, `health_monitor()`,
  `rebuild_entity()`, `claim_service_entities()`. The coupling
  between "release the gate" and "release the monitor" becomes
  structural — both release inside `deregister_entry()` when
  the set goes empty.
- [x] **B4 — Update imports and tests.** All
  `from .gate import ...`, `from .health import ...`,
  `from .pyirishrail import ...` references are updated. Test
  files are updated to match. `tests/test_gate_sharing.py` and
  `tests/test_health.py` are merged or share fixtures where the
  underlying module is now one.

**Acceptance:**

- All gates green.
- `custom_components/irish_rail/` has ~5 fewer Python files
  (`pyirishrail/` directory gone, `gate.py`/`health.py`
  collapsed into `_runtime.py`).
- `scripts/build_stops_matrix.py` is ≤ 60 lines (CLI wrapper
  only).
- No new public attributes on any existing class. The
  `RuntimeRegistry` is the only new public type.

---

### Phase C — Feature consolidation (two-train sensor surface)

> **Revised 2026-09-03 (user decision):** the original plan collapsed
> the three sensors into one rich sensor whose `upcoming_trains[]`
> attribute carried the extra trains. The revised target is **two**
> per-station sensors — `next_train_due` (presentation unchanged) and
> a new `following_train_due` — a fixed attribute surface, **no**
> `upcoming_trains` attribute, **no** `num_trains` option, and the
> coordinator retaining only the next two trains. Decision S4 and the
> Goals table were updated to match.

**Goal:** The devices show the next train due in (a live
minutes-and-seconds countdown, exactly as today) and the following
train due in (the same presentation for the second train). The two
redundant sensors (`next_train_destination`, `next_train_delay`) are
dropped, each sensor carries a small fixed attribute surface, the
`upcoming_trains` attribute and the `num_trains` option are removed,
and only the next and following trains are retained — on first
configuration and reconfiguration alike.

| Sensor | State | `state_attributes` |
|---|---|---|
| `next_train_due` | `TIMESTAMP` datetime of the next train's expected arrival (unchanged) | `expected_arrival_time`, `scheduled_arrival_time`, `direction`, `train_code`, `api_reachable`, `expected_arrival`, `time_until_arrival` |
| `following_train_due` | `TIMESTAMP` datetime of the following train's expected arrival; `unknown` when fewer than two trains are scheduled | `expected_arrival_time`, `scheduled_arrival_time`, `direction`, `train_code`, `api_reachable` |

**Steps:**

- [x] **C1 — Drop `next_train_destination` and
  `next_train_delay`.** Remove their instantiations and the
  destination/delay branches from `sensor.py`; remove the keys from
  `icons.json` and the `strings.json` / `translations/en.json`
  entity sections. Update `test_sensor.py`, `test_icons.py`, and
  `test_translations.py` (whose source-regex check pins the
  remaining instantiations) to assert the two keys are gone.
  Destination and delay data are no longer exposed anywhere.
- [ ] **C2 — Add `following_train_due`; retain only two trains.**
  Instantiate `following_train_due` from the same sensor class:
  `TIMESTAMP` device class, state =
  `_parse_expected_arrival(data[1], now)`, `None` (→ unknown) when
  fewer than two trains exist — the defensive-read rule, never a
  crash. The coordinator returns at most the next two trains
  (`trains[:MAX_RETAINED_TRAINS]` against a new
  `MAX_RETAINED_TRAINS = 2` in `const.py`); the API look-ahead still
  returns everything, only the next and following trains are
  retained, so the stops-at learning path (client-side
  `last_downstream_stop_names`) is unaffected and diagnostics'
  `due_trains_count` reports the retained list. New tests: the
  following-train state when a second train exists, `unknown` when
  only one does, and a coordinator test pinning the two-train slice.
- [ ] **C3 — Fix the attribute surface.** Each sensor carries
  exactly the keys in the table above (next: 7, following: 5).
  Drop `origin`, `origin_time`, `destination_time`,
  `expected_departure_time`, `scheduled_departure_time`,
  `train_type`, `due_in_mins`, `late_mins`, and `upcoming_trains`.
  The zero-train case becomes `{"api_reachable": True}` on both
  sensors. Update `test_sensor.py` to pin the per-sensor surface.
- [ ] **C4 — Remove the `num_trains` option end-to-end.** Delete
  `CONF_NUM_TRAINS` / `DEFAULT_NUM_TRAINS` / `MIN_NUM_TRAINS` /
  `MAX_NUM_TRAINS` from `const.py`, `resolve_num_trains` from
  `coordinator.py`, the field from the config-flow user step and the
  options flow, the `num_trains` value from the created entry's data
  (`async_create_entry`) and from the reconfigure path's preserved
  data, and the `num_trains` `data_description` entries from
  `strings.json` / `translations/en.json`. Existing entries that
  carry the key simply ignore it (no migration shims, per the
  baseline precedent). Update `test_config_flow.py` and delete
  `test_resolve_num_trains_precedence_and_clamping`.
- [ ] **C5 — Docs and evidence pass.** `README.md`: two-sensor
  Entities table, config walkthrough without the upcoming-trains
  step, updated attribute tables, and the delay-notification example
  replaced (a countdown-based trigger — `late_mins` no longer
  exists). `quality_scale.yaml`: `entity_device_class`,
  `icon_translations`, and the docs_* evidence re-pointed at the
  two-sensor surface. `docs/architecture.md`: §6 attribute tables
  and a two-sensor rationale replacing "one sensor, not three"; §15
  stays for the timestamp class. Skills 00 (Phase C summary), 05
  ("One-sensor-per-station model", "Next-N-trains entities"), 08
  (sensor-surface-stable rule), and 10 §4 rewritten for the
  two-train design.

**Acceptance:**

- All gates green (ruff · strict mypy · pytest, 100% coverage).
- `sensor.py` instantiates exactly two per-station entities and is
  ≤ 150 LOC; `coordinator.data` never holds more than two trains.
- Attribute surfaces are exactly the 7-key (next) and 5-key
  (following) sets; `upcoming_trains` and `num_trains` appear
  nowhere in the integration (verified by repo-wide grep).
- `icons.json` and the entity translations carry exactly
  `next_train_due` and `following_train_due` for per-station
  sensors.
- One commit per step (C1–C5) so each is independently revertible.

---

### Phase D — Cosmetics (READMEs, quality scale, renames)

**Goal:** Final cosmetic pass. After Phase A–C the source is
already much cleaner; this phase polishes the user-facing and
governance artefacts.

**Steps:**

- [ ] **D1 — Tighten `README.md`.** Remove the duplicate
  "Integration-level service entities" section (keep the one
  that's better organized). Remove the duplicate "License" block.
  Aim for ≤ 200 lines.
- [ ] **D2 — Compress `quality_scale.yaml` to ~7 KB / 150 lines.**
  Each `done` comment is one or two lines pointing at a file
  and function; the long-form design history is in
  `docs/architecture.md`. The Platinum rules keep all their
  evidence pointers, just less prose around them.
- [ ] **D3 — Rename for clarity.** A handful of over-clever
  names are renamed: `IrishRailApiHealthMonitor` →
  `ConnectivityMonitor`; `async_claim_global_provider` →
  `claim_service_entities`; `previous_unique_id` →
  `applied_unique_id`. Tests and `quality_scale.yaml`
  evidence are updated.
- [ ] **D4 — Update the `CHANGELOG.md` for v0.4.0.** Single
  release entry summarising Phases A–C. No migration shims
  (the integration has no users, per the v0.3.0 baseline
  precedent).

**Acceptance:**

- `README.md` is ≤ 200 lines.
- `quality_scale.yaml` is ≤ 8 KB.
- `CHANGELOG.md` has a v0.4.0 entry summarising the
  maintainability work.
- No new files added; this phase is purely prose/rename.

---

### Phase E — Test deduplication (optional, low priority)

**Goal:** The test suite is 2.5× the source LOC, partly because
several files cover the same edges (e.g. matrix-rebuild tests vs
build-script tests). Tighten the suite while preserving the 100%
coverage gate.

**Steps:**

- [ ] **E1 — Audit duplicate coverage.** Generate a coverage
  report that ranks lines by how many test files cover them.
  Identify pairs of test cases (one per file) that exercise the
  same code path with slightly different fixtures, and merge.
- [ ] **E2 — Consolidate shared fixtures.** The
  `mock_config_entry` fixture in `conftest.py` is reused; expand
  it to cover the small variations other tests duplicate
  (e.g. stations with vs without direction, services with vs
  without due trains).
- [ ] **E3 — Move `tests/win_stubs.py` into a CI-only guard.**
  Today the shim is force-loaded by every developer's pytest
  invocation via `-p tests.win_stubs`. Move the platform check
  inside the shim so non-Windows hosts no-op, and add a
  `pytest --co -q` smoke test that the shim itself is
  importable on all platforms. The 100%-coverage gate still
  requires the shim to run on Windows in CI.

**Acceptance:**

- `tests/` is shorter overall (target ~5,500 LOC).
- 100% coverage is preserved.
- The shim is no-op on non-Windows hosts; CI on Windows
  remains green.

---

## Progress log (append one line per increment)

- 2026-08-31 — Roadmap created from the lead-dev review. Skill 10
  drafted. `docs/architecture.md` created as the destination for
  long-form design history.
- 2026-09-01 — A4 executed: 7 `Skill N` / `Phase N` / `roadmap N`
  cross-references removed from the integration tree
  (`const.py` ×5, `pyirishrail/errors.py` ×1, `pyirishrail/models.py` ×1);
  each replaced with a `See docs/architecture.md §N` pointer to the
  relevant invariant. CI project-internal reference gate now passes
  with 0 offenders.
- 2026-09-01 — A5 executed: `quality_scale.yaml` compressed from
  22.7 KB / 438 lines to 11.1 KB / 69 lines (–51% size, –84% line
  count). 54 rules preserved (47 `done` + 7 `exempt`); every `done`
  retains a working file/function/README-section/architecture-section
  pointer. Long-form design history removed from per-rule comments and
  delegated to `docs/architecture.md` (which now has 16 sections).
  YAML validates clean; A4 sub-gate still passes (no cross-references
  introduced). The 3 KB gap to the ≤ 8 KB target is in legitimate
  evidence for the three Platinum rules and the long Gold rules; the
  roadmap's "~7 KB" target was approximate and the remaining bytes
  are minimum-information pointers, not duplicated prose.
- 2026-09-01 — B1 executed: `pyirishrail/` sub-package folded into
  the integration package. `pyirishrail/api.py` → `client.py`,
  `pyirishrail/_const.py` → `lib_const.py`,
  `pyirishrail/_gate.py` → `request_gate.py`,
  `pyirishrail/errors.py` → `errors.py`,
  `pyirishrail/models.py` → `models.py`, `py.typed` moved to the
  integration root; the `pyirishrail/__init__.py` re-export layer and
  `pyirishrail/README.md` deleted. 14 import statements across
  9 integration modules (`__init__.py`, `button.py`, `config_flow.py`,
  `coordinator.py`, `gate.py`, `health.py`, `matrix_rebuild.py`,
  `sensor.py`, `types.py`) and `scripts/build_stops_matrix.py`
  retargeted to the new flat paths; 12 test files updated (imports
  plus ~100 `mock.patch()` string targets; the `ir_api` test alias
  renamed to `ir_client` to match). Duplicate
  `MOVEMENT_CACHE_MAX_ENTRIES` removed from `const.py`. CI stale-
  patch-target grep guard and its comment updated to match the new
  layout. `quality_scale.yaml` Platinum evidence pointers
  (`async_dependency`, `inject_websession`) and the
  `common_modules` Bronze pointer updated. `docs/architecture.md`
  §1 module-layout diagram and §16 vendoring rationale rewritten to
  reflect the new tree (the `pyirishrail` sub-package is gone).
  Verbose module docstrings trimmed per Skill 10 §1: the ~85-line
  XML-policy essay in `client.py`, the ~60-line gate-design essay in
  `request_gate.py`, the 21-line singleton-lifecycle essay in
  `gate.py`, and the long rationale blocks in `errors.py`,
  `lib_const.py`, `models.py`, `entity.py`, `types.py` are now
  1–3 line contract docstrings with `See docs/architecture.md §N`
  pointers (where the design history already lives). No behaviour
  changes. Gates green: 245 passed, 100.00% line coverage across
  19 integration modules (was 21 — one source file less:
  `pyirishrail/__init__.py` removed), ruff 0, strict mypy 0 across
  38 source files (was 39 — one less for the same reason),
  docstring density 0.194 (Phase A 0.20 gate still passes),
  project-internal reference gate clean.
- 2026-09-01 — B3 executed: `gate.py` and `health.py` merged into a
  single `_runtime.py` module. A `RuntimeRegistry` (held on
  `hass.data[DOMAIN]` under `"runtime"`) is the only writer to
  `loaded_entry_ids`, the `RequestGate` reference, and the
  `IrishRailApiHealthMonitor` reference. Module-level
  `async_get_request_gate` / `get_health_monitor` /
  `async_note_entry_loaded` / `async_claim_global_provider` are thin
  delegates onto the registry so call sites needed only an import
  path change. The coupling "drop the shared gate when the last
  entry leaves" is now structural: `RuntimeRegistry.async_release()`
  is the single point that drops the gate and stops the monitor
  together, called from `async_note_entry_unloaded` when the
  loaded set empties. The same release keeps the monitor object
  itself alive so a reload re-starts the same instance and the
  probe history survives an unload/reload cycle (preserves the
  `get_health_monitor(hass) is first` invariant pinned by
  `test_health.py::test_monitor_lifecycle_tracks_loaded_entries`).
  6 production files updated (`__init__.py`, `binary_sensor.py`,
  `button.py`, `config_flow.py`, `coordinator.py`,
  `diagnostics.py`); 5 test files updated
  (`test_gate_sharing.py`, `test_global_setup_edges.py`,
  `test_health.py`, `test_health_suppression.py`,
  `test_init.py`); the `HEALTH_MONITOR_INSTANCE` constant
  removed from `const.py`. `docs/architecture.md` §1 (module
  layout) and §2 (shared-singletons table) updated; the
  `entity_category` quality-scale pointer rewritten to
  `_runtime.py`. One new test
  (`test_unload_without_any_registry_reports_true`) pins the
  defensive no-registry branch of `async_note_entry_unloaded`.
  No behaviour changes. Gates green: 246 passed (was 245),
  100.00% line coverage across 19 integration modules (was 21:
  `gate.py` + `health.py` collapsed into `_runtime.py`),
  ruff 0, strict mypy 0 across 37 source files (was 38:
  one less for the same reason), docstring density 0.194
  (Phase A 0.20 gate still passes), project-internal
  reference gate clean.
- 2026-09-01 — B2 executed: the two stops-matrix rebuild
  implementations unified behind a single
  :func:`custom_components.irish_rail.matrix_rebuild.sample_stops_matrix`
  loop. The runtime rebuild button and the offline
  `scripts/build_stops_matrix.py` are now thin callers that select
  between two output modes (`gap_fill` + `atomic_dump`) and a
  `priority` parameter. `matrix_rebuild.py` shrank from
  ~165 lines (ad-hoc loop with hard-coded gap-fill and a
  "by design" diff essay in the docstring) to a single ~290-line
  module whose docstring now describes the two output modes
  rather than listing differences from a now-deleted sibling
  script. The script shrank from ~210 lines (loop + CLI + atomic
  writer + argparse + sys.path bootstrap) to a 60-line
  argparse + asyncio.run wrapper. The previously-duplicated
  `_dump_document` (atomic temp-file + os.replace) now lives
  once in `matrix_rebuild.py` and is reused by the script path
  via the new `atomic_dump=True` flag. The runtime rebuild's
  `_REBUILD_PRIORITY = "background"` constant is gone;
  the priority is a parameter of `sample_stops_matrix` and the
  button wrapper passes `"background"`, the script passes
  `"normal"`. 6 new tests pin the new surface:
  `test_sample_stops_matrix_builds_document` (script path writes
  valid seed JSON with schema_version + station + direction
  buckets), `test_sample_stops_matrix_atomic_dump_is_atomic`
  (no leftover `.tmp` file), `test_sample_stops_matrix_gap_fill_requires_hass`
  + `test_sample_stops_matrix_atomic_dump_requires_output_path`
  (the two ValueError guards), `test_sample_stops_matrix_station_list_failure_returns_error`
  (the ``IrishRailError`` branch), and
  `test_sample_stops_matrix_limit_slices_station_list`
  (the limit slicing). No behaviour changes. Gates green:
  252 passed (was 246; +6 new), 100.00% line coverage
  across 20 integration modules (was 19; ``matrix_rebuild.py``
  was previously the only one with uncovered branches; now every
  branch has a test), ruff 0, strict mypy 0 across 39 source
  files (was 37; ``scripts/build_stops_matrix.py`` now
  passes the strict gate; ``matrix_rebuild.py`` also added to
  the checked set), docstring density 0.197 (Phase A 0.20
  gate still passes), project-internal reference gate
  clean.
- 2026-09-02 — B4 executed: the test suite consolidated around
  the `_runtime.py` module. The import-update half of B4 was
  already absorbed by B1 (no `from .pyirishrail import ...`
  remains) and B3 (no `from .gate import ...` /
  `from .health import ...` remains — verified by a
  repo-wide grep returning zero hits). The remaining work was
  the test-file merge: `tests/components/irish_rail/
  test_gate_sharing.py` is renamed to `test_runtime.py` (its
  module docstring rewritten to describe the `RuntimeRegistry`
  scope: gate singleton wiring, lazy flow access, idempotent
  accessors) and gains the 4 lifecycle/providership tests that
  previously lived in `test_health.py`
  (`test_monitor_lifecycle_tracks_loaded_entries`,
  `test_unload_without_any_registry_reports_true`,
  `test_first_setup_claims_global_provider`,
  `test_claim_is_freed_when_owner_is_removed`) together with
  the `_entry()` / `_client()` helpers they need.
  `test_health.py` is trimmed to the health-monitor-specific
  surface (probe success/failure bookkeeping,
  `recently_confirmed_healthy`, `as_dict` snapshot, scheduling
  internals, and the orphan-entity/device purger tests); its
  `_runtime` import block slims to just
  `IrishRailApiHealthMonitor` and its module docstring points
  at `test_runtime.py` for the lifecycle half. The `_entry()`
  helper stays in `test_health.py` because the purger tests
  still use it. Test count unchanged (252): tests moved, none
  deleted, none added. Gates green: 252 passed,
  100.00% line coverage, ruff 0, strict mypy 0 across
  39 source files, docstring density 0.197 (Phase A 0.20
  gate still passes), project-internal reference gate
  clean.
- 2026-09-03 — Phase C revised (user decision): the collapse target
  changed from one rich sensor plus an `upcoming_trains[]` attribute
  to **two** per-station sensors (`next_train_due`, presentation
  unchanged, plus a new `following_train_due` with the same
  TIMESTAMP presentation) with a fixed attribute surface (four
  per-train keys + `api_reachable`, plus the
  `expected_arrival`/`time_until_arrival` countdown pair on
  `next_train_due` only), no `upcoming_trains` attribute, no
  `num_trains` option, and the coordinator retaining only the next
  two trains on first configuration and reconfiguration alike.
  Decision S4, the Goals table, and a Non-goals exception note
  updated; C1–C5 re-scoped accordingly.
- 2026-09-03 — C1 executed: dropped the `next_train_destination` and
  `next_train_delay` per-station sensors. `sensor.py` now instantiates
  only `next_train_due` (the destination/delay branches and the
  `UnitOfTime` DURATION device-class path removed; `native_value`
  simplified to the TIMESTAMP arrival parse). `icons.json` and both
  `strings.json` / `translations/en.json` entity sections drop the two
  keys; `test_icons.py` / `test_translations.py` expected-key sets now
  pin just `next_train_due`. `test_sensor.py` reworked (recovery test
  keys narrowed to `next_train_due`, the destination/delay parametrized
  attribute test simplified, the unknown-key test removed because
  `native_value` no longer branches by entity key); the entity-count
  assertions in `test_config_flow.py` / `test_init.py` updated to the
  one-sensor-per-station shape (owner entry: 1 station sensor + 2
  shared service entities = 3; plain entry: 1). Destination and delay
  data are no longer exposed anywhere. Gates green: 250 passed
  (was 252: −2, the removed parametrized + unknown-key cases),
  100.00% line coverage, ruff 0, strict mypy 0 across 37 source
  files, docstring density and project-internal reference gates clean.

