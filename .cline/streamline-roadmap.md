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
| S9 | Audit finding F-02 (request gate wedges under sustained cancellation) | **Downgraded High → Medium.** Re-reading `test_client_gate.py` (685 lines, five dedicated cancellation tests plus a 50-iteration randomized stress test asserting `_in_flight == 0` after every batch) showed the release path is correctly ordered and already well covered. The residual hole is narrow: `await self._lock` inside `_release_slot` is itself cancellable. Recorded in `docs/architecture.md` §3 as an accepted window; F9 adds a test, and the `asyncio.shield` hardening lands only if that test can fail deterministically. Do not churn correct, tested code on a theoretical race. |
| S10 | Audit finding F-03 (coordinator private-API override) | **Downgraded High → Medium.** The override works today and is deliberate, not careless — `test_coordinator.py` documents that HA 2026.8+ schedules from the seconds cache. This is a forward-compatibility risk, not a current defect. F7 migrates it to the public `async_set_update_interval`. |
| S11 | Audit finding F-09 (sensor timestamp drifts continuously) | **Withdrawn as a behavioural bug.** HA does not re-read `native_value` between state writes, so the timestamp is stable between polls. The only real defect is documentation: the docstring and README claim "recomputed on every read". Corrected in F14. |
| S12 | Test-gap claims for `STOPS_STORE_INSTANCE`, `claim_service_entities`, `_update_interval_seconds` | **Corrected.** All three are already covered (`test_runtime.py:393-437,521-568`; `test_coordinator.py:264-299`). The suite is materially stronger than the audit assumed; new tests are added only where a specific behaviour is genuinely unpinned. |
| S13 | Reconfigure registry migration (F4) | **Migrate, do not delete.** Customisation capture lands first with the delete path intact so the capture is proven lossless; only then does the destructive path flip. If the migration proves unreliable, the fallback is to delete the README claim rather than ship a lossy migration. |
| S6 | `gate.py` + `health.py` consolidation | **Yes**, into a single `_runtime.py` module exposing a `RuntimeRegistry` class. Singleton lifecycles become structural (the registry is the only writer to `loaded_entry_ids` and to each subkey). The `RequestGate` primitive in `pyirishrail._gate` stays separate — it's the framework-agnostic gate, not a singleton. |
| S7 | Docstring discipline | **Three categories** (see Skill 10 §2): keep contract docstrings tight; move design history to `docs/architecture.md`; delete "what the name says" docstrings. Density target: 0.15 lines/LOC. |
| S8 | Tests for the simplify pass | **Tighten, do not just re-keep**. Several test files cover the same edge cases (e.g. matrix-rebuild tests vs build-script tests). Phase E deduplicates while preserving the 100% coverage gate. |
| S14 | Global-entity lifecycle (audit HIGH-1) | **Re-elect, do not persist.** Ownership of the "Irish Rail Services" device becomes a value *derived* from `loaded_entry_ids` rather than a fact cached at first claim. The defect was that `claim_service_entities` tested liveness with `hass.config_entries.async_entries(DOMAIN)` — which ignores load state — while `GLOBAL_PROVIDER_KEY` was cleared only at zero loaded entries, so removing the owner with a sibling still loaded orphaned the globals for the session. A sticky election is unsound; a recomputed one is self-healing. |
| S15 | Promotion mechanism (audit HIGH-1) | **Re-add entities directly; do not reload the survivor.** Reloading a live station entry makes its own sensors disappear and reappear — user-visible churn on an entry that did nothing wrong. Use `entity_platform.async_get_platforms` + `EntityPlatform.async_add_entities` on the survivor's already-running platforms. Recorded fallback: `async_schedule_reload`, which is correct but impolite. Two implementation notes proved out during G1: `async_get_platforms` is keyed by the *integration* name (the config entry's domain) and each platform carries its own `.domain`; and the dispatcher behind `ConfigEntryChange.REMOVED` passes `(change, entry)` as two positional arguments, not one `Event`. |
| S16 | Reconfigure destructiveness (audit HIGH-3, MEDIUM-8) | **Make it transactional.** The listener currently deletes the old identity's registry rows *before* knowing the reload will succeed, and restores via `entry.async_create_task` + `async_block_till_done` — which the reload's own `_async_process_on_unload` waits on, producing a mutual wait broken only by a hard-coded 10 s timeout. Replace both with a pending-restore map consumed at the end of `async_setup_entry`. Scope confirmed: same station + same direction is already a no-op (`config_flow.py:543`), so only a direction change is destructive, and the globals are never in the purge's blast radius. Three implementation notes proved out during G2: the map is a module-level dict in `__init__.py` rather than `RuntimeRegistry` state, because `async_release` runs during the *reload's* unload and would clear the capture at the exact moment it must survive; `ConfigEntryChange.REMOVED` is dispatched from `ConfigEntries._async_clean_up`, i.e. after `entry.async_unload` has already run the entry's `async_on_unload` callbacks, so only a still-loaded sibling can observe a removal and the pop on that signal is a best-effort bound rather than a guarantee; and `entity.py` rejects an entry without a unique ID, so by the time the swap runs the restore target is always a `str`. |
| S17 | Health-probe suppression of the empty-data issue (audit HIGH-2) | **Suppress only for unfiltered entries.** The probe queries a different station with no filters, so "the API answered" says nothing about whether *this* entry's filter is satisfiable. A filtered entry returning nothing for the threshold during service hours is precisely the case the issue exists to catch, and suppressing it is what made an impossible `stops_at` value permanently silent. One implementation note proved out during G3: the new `is_unfiltered` precondition reports exactly the two filters `_async_update_data` actually sends — `self.direction`, snapshotted at coordinator construction, and `resolve_stops_at(entry)`, read live — rather than re-reading both from the entry, so it can never disagree with the query it is qualifying. |
| S18 | Scope of the audit's MEDIUM-8 finding | **Narrowed, not withdrawn.** Recorded so the next reader does not re-derive it: the finding holds only for a *direction* change, and the two global entities plus the services device are outside its blast radius (their unique IDs and device identifier match neither the `f"{previous_uid}_"` prefix nor `(DOMAIN, previous_uid)`). The minimal fix stands; the blast radius is smaller than first reported. |
| S19 | Where the source-hygiene gate lives | **`scripts/`, not a heredoc in `ci.yml`.** `scripts/check_streamline_a4.py` now owns all three checks (docstring density, project-internal cross-references, backticked module pointers) so the gate is importable, typed and unit-tested rather than untested YAML. The module-pointer check is new and is what would have caught the stale `health.py` / `gate.py` pointers: a backticked `` `foo.py` `` must now name a file that exists under `custom_components/irish_rail/`. The cross-reference regex widened from `roadmap\s+\d\.\d` to a bare `\broadmap\b` — the digit requirement was exactly what let the line-wrapped `roadmap item 4.4` at `client.py:73` through. |
| S20 | Docstring density after enabling `--cov-branch` | **Trimmed, not the ceiling raised.** G10h turned on branch coverage, and running the now-runnable density gate showed 0.220 against a 0.21 ceiling: the G1–G9 increments had pushed the source past the threshold the previous progress log recorded as 0.197, so CI was already red on master. ~100 lines of contract-exceeding prose in `client.py` were compressed to one-or-two-line contracts with `docs/architecture.md §N` pointers, per S7; density is now 0.201. Note the recorded discrepancy: the roadmap's Phase A acceptance says 0.20 and the enforced ceiling has always been 0.21 — the enforced value is the stricter-contract-by-accident of the two and is unchanged, only the failure message, which used to say 0.20 while checking 0.21. |
| S21 | Unreachable branches vs untested branches | **Split, deliberately.** Enabling `--cov-branch` surfaced 18 partial branches. Seventeen are real untested behaviour and now have tests. Two are not: `sensor.py`'s inner `if time_until_arrival is not None` (the value is derived from `expected_arrival`, so it cannot be `None` there — replaced with a `cast`, which removes the branch instead of decorating it) and `matrix_rebuild.py`'s `elif document is not None` (`document` is `None` only under `gap_fill=True`, which always installs a store — marked `# pragma: no branch`). Both are provably dead rather than merely uncovered; the pragma records the proof. |
| S22 | `home-assistant/actions/hassfest` SHA pin | **OPEN — not done, not guessed.** Pinning requires the upstream commit id for the tag, and the build environment has no network access to resolve it. A fabricated SHA would break the build or, worse, pin to nothing real, so the reference stays `@master` with an in-file comment marking it as the single remaining mutable action reference. `hacs/action@main` is the same case. Recorded here and in the G10 note rather than silently dropped. |
| S23 | Build gates under test and mypy | **`scripts/` joined the strict run and the coverage gate.** The gates are part of the build, so untested shell in `ci.yml` was a hole in the same way the prose-grepping test was (see G8). `tests/test_scripts.py` drives all three scripts through their real `main()` entry points. `if __name__ == "__main__":` and `if TYPE_CHECKING:` blocks are excluded from coverage — process entry points and type-only imports are not behaviour. |

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
- [x] **C2 — Add `following_train_due`; retain only two trains.**
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
- [x] **E3 — Move `tests/win_stubs.py` into a CI-only guard.**
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

---

### Phase F — Correctness remediation from the 0.4.0 audit

A full repository audit was carried out against the Home Assistant
integration contract after v0.4.0. It confirmed several defects and,
on re-verification against the tree, **downgraded three of the
original findings** (see Decisions S9–S11). Phase F fixes the confirmed
defects, restores the `RuntimeRegistry` invariant, and removes the
integration's coupling to Home Assistant private APIs.

Work is split into three tracks so correctness lands independently
of test-only work and repository hygiene.

#### Track 1 — Correctness

- [x] F1 — `config_flow.py` options flow: preserve a data-level
      `stops_at` when the user changes only the scan interval. The
      flow currently always writes `stops_at` (normalising "All" to
      `None`), and `coordinator.resolve_stops_at` reads options
      before data, so an unrelated options save silently drops a
      filter set during setup.
- [x] F2 — `config_flow.py`: merge the stored `stops_at` value into
      the dropdown options, mirroring the existing merge in
      `_build_direction_step_schema`. A stored value absent from a
      transiently-short station list is currently not selectable and
      is dropped on submit.
- [x] F3 — `models.py` / `client.py` / `sensor.py`: widen
      `TrainDueTime.due_in_mins` to `int | None` so a malformed
      `Duein` reaches the documented `HH:MM` fallback instead of
      being coerced to `0` ("due now"). Build the fallback against
      `DUBLIN_TZ` rather than UTC.
- [x] F4 — `__init__.py`: migrate entity/device registry rows on a
      direction reconfigure instead of deleting them, so entity
      names, areas and icons survive.
- [x] F5 — `_runtime.py` / `store.py` / `button.py`: make
      `RuntimeRegistry` the sole writer of `hass.data[DOMAIN]`, as
      `docs/architecture.md` §11 already claims. Add a CI grep gate
      so the invariant is structural.
- [x] F6 — `_runtime.py`: rebind the health monitor's client when a
      new entry loads. After a full unload the monitor object is
      retained while the gate is dropped, so the probe keeps using a
      discarded gate.
- [x] F7 — `coordinator.py`: replace the `update_interval` property
      override (which calls `DataUpdateCoordinator.update_interval.fset`
      behind a `type: ignore` and writes the private
      `_update_interval_seconds`) with the public
      `async_set_update_interval`. Removes the last `type: ignore`
      in the tree and the forward-compat risk.
- [x] F8 — `entity.py` / `matrix_rebuild.py`: replace the two
      load-bearing `assert` statements with explicit errors. The
      `entity.py` assert runs *after* the value it guards is used.

#### Track 2 — Test-only

- [x] F9 — `test_client_gate.py`: cover cancellation arriving while
      `_release_slot` is blocked on the gate lock, plus a sustained
      churn test asserting `_in_flight` never leaks or goes
      negative.
- [x] F10 — `test_coordinator.py`: replace the wall-clock-tolerant
      scheduler test with a controlled-time assertion on observable
      outcomes.
- [x] F11 — `test_init.py`: adopt `verify_cleanup` so leaked timers
      and un-awaited tasks fail loudly.
- [x] F12 — Add `assert_diagnostics_logging` coverage for the
      coordinator's unavailable/recovery transitions and the health
      probe's failure logging.

#### Track 3 — Repository hygiene

- [x] F13 — `quality_scale.yaml`: correct six rows whose pointers
      name deleted code (`next_train_delay`, `next_train_destination`,
      `num_trains`) or sections that do not exist, and flip
      `action_setup` from `exempt` to `done`. Add a test that
      resolves every `done` pointer so stale evidence becomes a
      build failure.
- [x] F14 — `README.md`: remove the deleted `pyirishrail/` path
      reference, the duplicate Removal section and the orphaned table
      row; correct the "recomputed on every read" claim; replace the
      `numeric_state` examples, which cannot fire against a
      TIMESTAMP sensor, with template triggers.
- [x] F15 — `.github/workflows/ci.yml`: pin
      `pytest-homeassistant-custom-component`, and move the inline
      docstring-density gate into `scripts/check_streamline_a4.py`.
- [x] F16 — Metadata: strip trailing whitespace in `manifest.json`,
      relax the `hacs.json` floor to `2026.8.0`, ignore
      `.pytest_cache/` and `.ruff_cache/`, and drop the unreachable
      `bytes`-decode branch in `client.py`.
- [x] F17 — `matrix_rebuild.py`: bound the local movement cache used
      by the full-network sweep.
- [x] F18 — `docs/architecture.md` §4: correct the claim that the
      XML guard cannot false-positive on data; a `CDATA` section
      containing `<!doctype` is rejected by design.

---

### Phase G — Audit remediation from the 0.5.0 review

A comprehensive repository review was carried out after v0.5.0 against
the Home Assistant integration contract, the Quality Scale rules, and
the CI/release configuration. It confirmed a small number of lifecycle
and correctness defects and produced five structural recommendations
(R1–R5). Phase G lands them.

Phase G is a **behaviour-change phase**: several items are user-visible
fixes, not refactors, so ground rule 4 applies — each lands as its own
committed step with the gates green.

The detailed plan — per-item design, file touchpoints, and acceptance
criteria — lives in
[`plans/phase-g-audit-remediation.md`](../plans/phase-g-audit-remediation.md).
This section carries the ordering and the decisions; the plan file
carries the mechanics.

**Governing invariant (user-specified):** the "Irish Rail Services"
device, its two entities, and the `rebuild_stops_matrix` service exist
**if and only if** at least one station config entry is loaded. The
"own grouping, never attached to a station device" half already holds —
both globals carry the fixed `GLOBAL_SERVICES_IDENTIFIER` — and G1 adds
a test that pins it.

#### G1–G4 — P0 correctness

- [x] **G1 — Provider election derived from the loaded set.** Replace
      the sticky claim with `elect_provider` / `disown_provider_if` /
      `async_promote_provider` in `_runtime.py`, keyed on
      `loaded_entry_ids` rather than `hass.config_entries.async_entries(
      DOMAIN)`. Move the rebuild-service registration out of per-entry
      `button.py` setup so service lifetime tracks the loaded set.
      Re-point `_purge_orphan_global_entities` at a just-departed owner.
      Fixes HIGH-1. (Decisions S14, S15.)
- [x] **G2 — Transactional reconfigure.** Pending-restore map consumed
      at the end of `async_setup_entry`; delete the
      `async_create_task` + `async_block_till_done` handshake and the
      pre-reload purge. Fixes HIGH-3 and MEDIUM-8. (Decision S16.)
- [x] **G3 — Stop suppressing the empty-data issue for filtered
      entries.** Probe health only suppresses when the entry has no
      `direction` and no `stops_at`. Fixes HIGH-2a. (Decision S17.)
- [x] **G4 — Scope the `stops_at` options to reachable stops.** Learned
      matrix → bundled seed → live discovery → full station list as a
      labelled last resort. Fixes HIGH-2b, and delivers R5.

#### G5–G6 — Structural recommendations

- [x] **G5 — Return observations instead of parking them on the client.**
      `IrishRailClient.last_downstream_stop_names` is shared
      mutable state across the coordinator, the health monitor and the
      rebuild button; safe today only because the latter two pass no
      filter. Deliver R3.
- [x] **G6 — Share the movement cache on the registry.** One cache per
      HA instance instead of one per config entry. Deliver R4, and fixes
      MEDIUM-6.

#### G7–G9 — Claims, translations, remaining correctness

- [x] **G7 — Correct six inaccurate `quality_scale.yaml` rows.**
      `entity_event_setup`, `action_exceptions`,
      `exception_translations`, `docs_triggers`, `docs_conditions`,
      `docs_actions`. Fixes MEDIUM-9.
- [x] **G8 — Translated `HomeAssistantError` for the rebuild service**,
      and delete `test_no_homeassistanterror_raised_to_users`, which
      greps source prose and actively blocks the fix. Fixes MEDIUM-10
      and MEDIUM-11.
- [x] **G9 — Remaining MEDIUM items**, each its own checkbox: G9a guard
      unload side effects on success; G9b re-arm the scheduler after an
      interval change; G9c merge rather than replace `entry.data` on
      reconfigure; G9d isolate per-station rebuild failures; G9e thread a
      `DUBLIN_TZ` date through the pruning path; G9f button
      `async_cancel()`; G9g return the added-stop count from
      `async_record`; G9h restore pending stops on cancellation; G9i
      mask `stops_at` in diagnostics. **G9a was investigated and
      withdrawn** (see the progress log); G9f landed with G1.

#### G10–G11 — Hygiene and evidence

- [x] **G10 — LOW findings and repository hygiene**: stale docstring
      pointers to the deleted `health.py` / `gate.py`; the
      `roadmap item 4.4` breadcrumb and the CI regex hole that let it
      through; `hacs.json` floor to the tested `2026.8.2`; stray `-p/`
      directory; CI action SHA pins and pinned dev tooling;
      `--cov-branch`; `pytest-timeout`; a seeded RNG in the gate stress
      test; move the docstring-density gate into `scripts/`.
      **Landed with two recorded exceptions**: the
      `home-assistant/actions/hassfest` SHA pin is still open (see
      S22), and enabling branch coverage exposed a real
      docstring-density breach that had to be fixed first (S20).
- [x] **G11 — Documentation and evidence pass**: `docs/architecture.md`
      §§2/6/7/8/9/10/11/12/13, `README.md`, `quality_scale.yaml` evidence
      pointers, and a `CHANGELOG.md` v0.5.1 entry. `manifest.json` bumped
      to 0.5.1 so the release-version gate agrees with the tag.

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
- 2026-09-03 — C2 executed: added the `following_train_due` sensor
  and the two-train retention. `const.py` gains
  `MAX_RETAINED_TRAINS = 2`; `coordinator._async_update_data` slices
  the API's look-ahead result to the next two trains before the
  empty-data check and stops-at learning (neither cares about the
  tail). `sensor.py` registers `following_train_due` from the same
  class: TIMESTAMP state resolving from `data[1]`, falling back to
  `None` (→ unknown) when only one service is scheduled; `native_value`
  regains key-specific branching for the following slot. `icons.json`,
  `strings.json` / `translations/en.json`, `test_icons.py`, and
  `test_translations.py` add the new key ("Following train due"). New
  tests pin the second train's state (due later than the next), the
  unknown fallback when only one train exists, both sensors'
  unavailable+recovery round-trip, and the coordinator's two-train
  slice. Entity-count assertions in `test_config_flow.py` /
  `test_init.py` updated to the two-sensor-per-station shape (owner
  entry: 2 station sensors + 2 shared service entities = 4; plain
  entry: 2; 2 deleted per identity change). Gates green: 253 passed
  (was 250; +3 new tests), 100.00% line coverage, ruff 0,
  strict mypy 0 across 37 source files; docstring density and
  project-internal reference gates clean.
- 2026-09-03 — C3-C5 executed: fixed attribute surface, removed
  num_trains option end-to-end, updated README and architecture docs.
  See commits 419f690, bb54001, bec51a8.
- 2026-09-03 — D1-D4 executed: tightened README (removed duplicate
  sections), compressed quality_scale.yaml, renamed for clarity
  (ConnectivityMonitor, claim_service_entities, applied_unique_id),
  added v0.4.0 changelog entry. See commits 4a4498b, 39a3901, 4443aaf,
  d8ba1ba.
- 2026-09-03 — E3 executed: added win_stubs importability smoke test.
  The new test `test_win_stubs_importable_on_all_platforms` verifies
  the shim module imports cleanly on all platforms (not just Windows),
  ensuring CI on macOS/Linux does not fail at plugin load time. The
  existing Windows-only regression tests remain skipped on non-Windows
  via pytestmark. Gates green: 252 passed, 100.00% coverage.
- 2026-09-03 — MCP tooling documented in the skill pack. Reviewed
  `.roo/mcp.json` against the tools actually exposed in the session:
  `filesystem`, `context7`, and `sequentialthinking` load; **`git` does
  not**, because its entry invokes `@modelcontextprotocol/inspector` —
  a debugging proxy, not a server — so no git tools are exposed. Added
  `.roo/skills/mcp-tooling/SKILL.md` (fifth skill; inventory, the
  defect and its fix, built-in-vs-MCP tool mapping, and the rule that
  MCP servers are never a dependency channel for the integration
  source), and updated `.roo/skills/README.md`. No source change, so no
  gate is affected. **Not ticked as a roadmap checkbox** — this is
  workspace tooling, outside the A–E phase scope; logged here for
  traceability only. The `git` config fix is left to the user, since it
  changes their environment rather than the repository.
- 2026-09-05 — **Phase F executed** (audit remediation). All 18
  increments landed; gates green: **296 passed, 100.00 % coverage**,
  ruff clean, strict mypy clean on 38 files.
  - *Correctness:* the options flow no longer clobbers a data-level
    `stops_at` (F1) and its dropdown keeps the stored value selectable
    even when a transient station-list fetch omits it (F2) — both were
    silent filter-loss bugs on the happy path. `TrainDueTime.due_in_mins`
    is now `int | None` so a malformed `Duein` reaches the `HH:MM`
    fallback (resolved against `DUBLIN_TZ`, not UTC) instead of
    publishing a wrong "due now" (F3). A direction reconfigure now
    carries entity name, icon and area across the identity change
    instead of destroying them (F4). The health monitor rebinds its
    client after a full unload, so the probe stops drawing its rate
    budget from a discarded gate (F6).
  - *Architecture:* `hass.data[DOMAIN]` is now written only through
    `_runtime.py` accessors, enforced by a new CI grep gate (F5). The
    coordinator no longer shadows the base `update_interval` property
    or touches `_update_interval_seconds`; it assigns the real public
    property, which removes the last `# type: ignore` in the source
    (F7). The two load-bearing `assert`s became explicit errors (F8)
    and the rebuild's local movement cache is now bounded like the
    client's (F17).
  - *Tests:* gate churn and repeat-cancellation invariants (F9), the
    wall-clock-tolerant scheduler test replaced by a public-API
    assertion (F10), `verify_cleanup` on the lifecycle tests (F11),
    per-poll debug / single-recovery log-volume pinning (F12), and a
    new `test_quality_scale.py` that resolves every evidence pointer
    so stale evidence fails the build (F13).
  - *Docs/metadata:* README lost the deleted `pyirishrail/` path, the
    duplicate Removal section and the orphaned table row; both
    automation examples were rewritten from `numeric_state` (which
    cannot fire against a TIMESTAMP sensor) to template triggers
    (F14). CI pins `pytest-homeassistant-custom-component` (F15);
    `manifest.json` trailing whitespace, `hacs.json` floor relaxed to
    2026.8.0, tool caches ignored, and the dead bytes-decode branch in
    `client.py` removed (F16).
  - *F18 needed no change:* `docs/architecture.md` §4 already
    documents the CDATA false-positive accurately; verified rather
    than edited.
  - Two defects were found and fixed while executing the plan, both
    pre-existing rather than introduced: `quality_scale.yaml` did not
    parse as YAML (an unquoted `requirements:` in the
    `async_dependency` comment), and the coordinator's interval
    property was shadowing a base setter that had never needed it.
- 2026-09-26 — G0 executed: Phase G recorded from the 0.5.0 audit,
  with decisions S14–S18. The governing invariant is user-specified —
  the "Irish Rail Services" device, its two entities and the
  `rebuild_stops_matrix` service exist if and only if at least one
  station entry is loaded. Per-item design and acceptance criteria
  live in `plans/phase-g-audit-remediation.md`. S18 records a scope
  correction so the next reader does not re-derive it: the audit's
  MEDIUM-8 (destructive reconfigure) holds only for a direction
  change — same station and same direction is already a no-op via
  `config_flow.py:543` — and the two global entities are outside its
  blast radius. No code changed in this increment.
- 2026-09-26 — G1 executed: the "Irish Rail Services" device, its two
  entities and the `rebuild_stops_matrix` service now exist **if and
  only if** at least one station entry is loaded. HIGH-1 closed.
  *Ownership is derived, not cached:* `elect_provider` tests the
  recorded owner against `loaded_entry_ids` instead of
  `async_entries(DOMAIN)`, which ignores load state and so pinned
  ownership to a dead entry for the rest of the session.
  *Promotion is driven by `ConfigEntryChange.REMOVED`,* which HA
  dispatches only once the entry has left the store. An unload is
  ambiguous — it is the first half of a reload *and* the whole of a
  removal — and a time-based settle guess was tried and rejected: it
  misread a routine reconfigure reload as a removal and handed the
  globals to a sibling mid-reconfigure, breaking
  `test_reconfigure_leaves_sibling_direction_entries_untouched`. The
  removal signal cannot race a reload.
  *The survivor is not reloaded:* entities are added to its
  already-running platforms, so a station that did nothing wrong never
  sees its own sensors blink out.
  *Service lifetime moved off the individual entry:* registration is
  now idempotent and keyed to the loaded set, and teardown happens on
  the zero-survivors path in `async_release`.
  *Entity construction is shared:* `build_connectivity_sensor` and
  `build_rebuild_button` serve both the setup path and promotion, so
  the two cannot drift.
  Gates: ruff 0 · strict mypy 0 · **306 passed · 100.00% coverage**.
  New tests: owner removed with a sibling loaded (device, both
  entities and the service all reappear on the survivor, on their own
  device); last entry removed (device, entities and service all gone);
  a plain reload keeps ownership; election is deterministic; the purge
  runs on re-election over a departed owner; promotion declines
  without a runtime or with a vanished survivor.
- 2026-09-26 — G2 executed: the identity reconfigure is now
  transactional. HIGH-3 and MEDIUM-8 closed together, because they
  share one root cause — destructive registry work ran *before* the
  reload was known to succeed.
  *The handshake is gone.* The restore used to run as an
  `entry.async_create_task` whose first statement was
  `hass.async_block_till_done()`, while the reload's own
  `_async_process_on_unload` did `asyncio.wait([*self._tasks, ...],
  timeout=10)` and `self._tasks` contained that very task: a mutual
  wait broken only by the hard-coded timeout, after which HA logged
  `Task ... did not complete in time`. There is no task any more.
  *Nothing is removed before the replacement exists.* The update
  listener now only *captures* the outgoing identity's customisations
  into `_PENDING_IDENTITY_RESTORES` and schedules the reload;
  `_async_apply_pending_identity_restore` runs after
  `async_forward_entry_setups` and is the single place that drops the
  old rows and re-applies the customisations. A reload that ends in
  `SETUP_RETRY` therefore leaves the old entities, their
  customisations and the station device intact, and the capture
  survives so a later successful reload still carries the name, icon,
  area and disabled state across. The user's guarantee is preserved
  and now holds for the failure path too: same station + same
  direction is still a pure no-op (no listener, no reload, no
  registry touch), and the two global entities are outside the purge.
  *Cleanup on removal.* A removed entry never runs setup again, so the
  removal handler drops its capture; a removed entry's own dispatcher
  subscription is already torn down by then, so this is a best-effort
  bound observed by any still-loaded sibling, not a guarantee.
  Gates: ruff 0 · strict mypy 0 · **309 passed · 100.00% coverage**.
  New tests: a failed direction reconfigure leaves the old rows,
  customisation and device in place, the capture still pending, and a
  later successful reload completes the swap carrying the
  customisation; a successful reconfigure logs no
  "did not complete in time"; removing a sibling with an unconsumed
  capture discards it.
- 2026-09-26 — G3 executed: the health probe no longer silences the
  persistent-empty-data repair issue for an entry that filters
  anything. HIGH-2a closed. The probe queries a *different* station
  with no filters, so "the API answered" says nothing about whether
  this entry's own filter is satisfiable; suppressing on that basis is
  what let an impossible `stops_at` value stay silent for the rest of
  the session. `coordinator.is_unfiltered` is now the precondition on
  the suppression branch. *The unfiltered path is unchanged:* a
  station with nothing scheduled and a healthy API still clears a
  stale issue and resets the streak silently. *Two existing tests
  changed meaning and were corrected rather than deleted:* both built
  a `direction`-filtered entry and then asserted probe suppression,
  which is exactly the behaviour being removed; the shared fixture is
  now unfiltered by default and takes the filter as an argument.
  Gates: ruff 0 · strict mypy 0 · **313 passed · 100.00% coverage**.
  New tests: a direction-filtered and a `stops_at`-filtered entry each
  reach the threshold and raise the issue with a green probe; an
  unfiltered entry reports `is_unfiltered`; and the precondition
  responds to each filter independently.
- 2026-09-26 — G5 executed: the client no longer parks the
  downstream-stop observations on itself (R3).
  `async_get_station_by_code` / `async_get_station_by_name` /
  `_async_prune_trains` take an optional `observed_stops` set that the
  caller owns; `last_downstream_stop_names` and its reset are gone.
  Previously the same attribute was shared by the coordinator, the
  health monitor and the rebuild button, and was safe only because the
  latter two pass no `stops_at` filter - an invariant nothing enforced.
  A dead `if not self._pending_stops: return` guard in the learn path
  turned out to be unreachable and was removed rather than tested.
  Gates: ruff 0 · strict mypy 0 · **320 passed · 100.00% coverage**.
  New tests: the caller's set is populated; a pre-seeded stale set is
  replaced rather than merged; two concurrent passes on one client keep
  their observations to themselves.
- 2026-09-26 — G6 executed: one movement-history cache per Home
  Assistant instance (R4, MEDIUM-6). `RuntimeRegistry` owns
  `movement_cache`, `async_get_movement_cache` hands it out, and
  `async_release` clears it alongside the request gate. The client takes
  an optional `movement_cache` argument, so the module stays
  framework-agnostic and standalone-testable; omitted, the client keeps
  a private cache exactly as before. Previously each config entry built
  its own client with its own 1024-entry cache, which is both a memory
  spike on the constrained hosts this integration targets and pure
  duplicated fetching for a train code serving two stations.
  Gates: ruff 0 · strict mypy 0 · **321 passed · 100.00% coverage**.
  New test: two entries share one cache object, a route warmed by one is
  visible to the other, and the cache is dropped with the last entry.
- 2026-09-26 — G8 then G7 executed together, in that order, because
  the plan requires `exception_translations` to become `done` through a
  code fix rather than a YAML edit. G8: the rebuild button raises
  `HomeAssistantError(translation_key="rebuild_already_running")`
  instead of a bare `RuntimeError`, with the wording under a new
  `exceptions` section in both translation files.
  `test_no_homeassistanterror_raised_to_users` - which grepped source
  *text* for the string `HomeAssistantError` and therefore blocked the
  fix - is deleted and replaced by
  `test_exception_keys_raised_toward_users_resolve`, which asserts
  every raised `HomeAssistantError` carries a `translation_key` that
  resolves in both files. Behavioural tests cover the button press and
  the real `hass.services.async_call` path. G7: the six rows are
  corrected - `entity_event_setup` now describes the actual wiring
  (sensors inherit `CoordinatorEntity`, the binary sensor removes its
  monitor listener via `self.async_on_remove`), `action_exceptions` and
  `exception_translations` move `exempt` -> `done` on the strength of
  G8, and `docs_triggers` / `docs_conditions` / `docs_actions` move
  `done` -> `exempt` because the integration registers none of those
  three (a binary sensor is not a Quality Scale condition, and the
  rebuild *service* is not an action).
  The evidence-pointer gate caught the deleted test name exactly as
  designed, which is what forced the G7 edit in the same run.
  Gates: ruff 0 · strict mypy 0 · **322 passed · 100.00% coverage**.
- 2026-09-26 — G9 executed. **G9a was investigated and withdrawn:**
  the finding claimed a failed platform unload leaves the entry
  ``LOADED``, so releasing the shared singletons would strand it. Read
  against `config_entries.py` in HA 2026.8, a failed unload sets
  ``FAILED_UNLOAD``, which is *non-recoverable* - the entry is never
  set up again, so releasing is the correct cleanup and keeping the
  singletons would leave shared state owned by a dead entry. The
  original code was right; the finding's premise was wrong. The
  reasoning is now a comment on ``async_unload_entry`` so the next
  reader does not re-derive it.
  **G9f needed no change:** G1 already added
  ``IrishRailRebuildStopsMatrixButton.async_cancel`` and scheduled the
  sweep with ``hass.async_create_background_task``. (The plan's
  suggestion of ``entry.async_create_background_task`` was wrong: after
  a provider promotion the button deliberately outlives its original
  entry, so tying the task to that entry's lifecycle would kill a
  rebuild the survivor now owns.)
  Landed: G9b, the coordinator now re-arms the scheduler through the
  base class' reschedule pair - HA 2026.8 has no public
  ``async_set_update_interval``, and assigning ``update_interval``
  only mirrors the seconds cache, so the docstring's "applies
  immediately" claim was false; G9c, the reconfigure flow builds
  ``new_data`` from ``{**entry.data, ...}`` so a future key is not
  silently dropped; G9d, a per-station ``except Exception`` in the
  rebuild sweep logs and continues, so one bad row no longer discards
  ~150 sampled stations; G9e, the schedule date is derived in
  ``DUBLIN_TZ`` and threaded through the pruning path, because
  ``async_get_train_stops``'s own default is the *host's* local date -
  a host in another zone queried yesterday's schedule between 00:00 and
  05:00 Dublin time and pruned every train; G9g, ``async_record``
  returns the number of newly added stops instead of a bool, so
  ``RebuildResult.stops_added`` stops over-reporting re-observed
  stops; G9h, a ``BaseException`` (cancellation) around the learn
  write restores the batch it had already taken out of
  ``_pending_stops`` and re-raises; G9i, ``stops_at`` is masked in
  diagnostics alongside the station fields.
  Gates: ruff 0 · strict mypy 0 · **325 passed · 100.00% coverage**.
  New tests: the poll pins the Dublin service date; a cancelled learn
  write restores the batch without advancing the debounce clock; an
  interval change re-arms the armed timer (asserted on
  ``hass.loop.call_at``); an unexpected per-station failure does not
  end the sweep.
- 2026-09-26 — G4 executed: the options flow's "stops at" dropdown is
  scoped to the stops a train from *this* entry can actually reach.
  HIGH-2b closed and R5 delivered. The list is built from, in order of
  freshness, this install's learned matrix, the bundled seed, and a
  live sample — each keyed on the entry's own station *and* direction,
  so an option offered from them can match a train. Only when all three
  come up empty does the full station list appear, and the field's
  `data_description` now says so and warns that a station from it may
  never be reached. Previously the list was built from every station
  unconditionally, so a user could pick one upstream of their own; the
  filter then pruned every train on every poll and, until G3, the one
  diagnostic that would have said so was suppressed.
  *The stored value is still merged in* whether or not it appears in
  the reachable set, so a no-op resubmit stays valid and a filter the
  user already has is never silently dropped.
  *Two notes for the next reader.* The initial config flow already
  scoped its stops-at step this way; only the options flow had drifted.
  And `test_config_flow.py` now carries an autouse fixture that
  neutralises live stop discovery, because the options flow now reaches
  that call whenever the matrix and seed are both empty — the normal
  state for a station the seed does not cover — and the suite blocks
  sockets.
  Gates: ruff 0 · strict mypy 0 · **318 passed · 100.00% coverage**.
  New tests: the learned matrix scopes the list and the station list is
  never fetched; the seed is the second source; live discovery is the
  third; the full list is the labelled last resort; and a stored value
  outside the reachable set stays selectable and resubmittable.
- 2026-09-26 — G10 executed: the LOW findings and the repository
  hygiene pass, with one item recorded as open rather than closed
  (S22).
  *Stale pointers.* The nine backticked references to the deleted
  `health.py` / `gate.py` modules now point at `_runtime.py` /
  `request_gate.py`, and the `roadmap item 4.4` breadcrumb is gone
  from `client.py:73`. The new module-pointer check in
  `scripts/check_streamline_a4.py` makes that class of drift fail the
  build rather than survive a rename.
  *The density gate was already red.* Moving the gate out of
  `ci.yml` into a runnable script immediately reported density 0.220
  against the 0.21 ceiling — the G1–G9 work had pushed the source past
  the 0.197 the last progress log recorded, so master was failing its
  own gate. Roughly 100 lines of prose in `client.py` were compressed
  to contract docstrings with `docs/architecture.md §N` pointers, per
  S7. Density is now 0.201 and the failure message finally states the
  number it checks (S20).
  *Hygiene.* `hacs.json` floor → `2026.8.2` (the version CI actually
  installs and the tests actually run against); the stray `-p/`
  directory and the committed `config/configuration.yaml` dev artefact
  removed; every CI tool pinned exactly; `dependabot.yml` gained a
  `pip` ecosystem so a pytest or mypy release is noticed.
  One rule conflict, recorded rather than papered over: the project
  conventions file lists `config/configuration.yaml` under "Layout" as
  local dev config. A local dev config has no business in the tree — it
  is four lines of logger config, is regenerated per developer, and was
  never read by the test suite or CI. The file is deleted; the
  conventions table should drop the row, which is a
  `.roo/rules` edit and therefore outside this phase's scope.
  *Branch coverage.* `--cov-branch` is on. It exposed 18 partial
  branches, of which two proved to be dead code rather than untested
  code (S21): `sensor.py`'s `time_until_arrival` guard, removed
  outright, and `matrix_rebuild.py`'s `elif document is not None`,
  marked `# pragma: no branch` with the reason inline. The other
  sixteen now have tests — an unresolvable arrival, an empty movement
  response, a direction-filtered train, an unchanged matrix write, a
  train code shared by two stations, promotion with no global
  entities, and the teardown no-ops.
  *Build gates are code now.* `scripts/` joins the strict mypy run and
  the coverage gate, with `tests/test_scripts.py` driving all three
  scripts. A new `release` CI job runs
  `scripts/check_release_version.py` on tag pushes so a tag can never
  ship a `manifest.json` version nobody tested.
  *Not done, on purpose.* `home-assistant/actions/hassfest@master` and
  `hacs/action@main` remain the only mutable action references; pinning
  them needs the upstream commit id, which this environment cannot
  resolve. Guessing a SHA would be worse than the honest gap, so the
  reference carries a comment saying so (S22).
  Gates: ruff 0 · strict mypy 0 across 42 files ·
  **353 passed · 100.00% line and branch coverage** across the 19
  integration modules and the 3 build gates · source-hygiene gate
  clean · release-version gate clean.
- 2026-09-26 — G11 executed: the documentation and evidence pass, and
  **Phase G is complete**. No behaviour changed; every claim below was
  checked against the code that now exists.
  *`docs/architecture.md`.* §2 gained the shared movement cache; §6 was
  rewritten, because the old attribute table described a design the
  integration left two phases ago (three sensors, an `upcoming_trains`
  list, a `num_trains` option) — it now documents the two sensors, the
  fixed five-key surface and the countdown pair's all-or-nothing rule;
  §7 was reduced to a pointer at §11, which now carries the election
  model, the promotion rule and the iff invariant; §8 records the
  transactional reconfigure and the no-op-by-construction case; §9
  records the re-armed scheduler and the filtered-entry exemption from
  probe-based suppression; §10 records what `async_record` now returns;
  §12 records why the unload release is unconditional; §13 records the
  four-source order the stops-at dropdown actually uses.
  *`README.md`.* The iff invariant, the transactional-reconfigure and
  re-armed-timer claims, the filtered-station repair-issue behaviour and
  the corrected stops-at fallback order. The minimum HA version was
  already 2026.8.2 and now matches `hacs.json` for the first time.
  *`quality_scale.yaml`.* Five evidence rows re-pointed at the G1/G2/G3
  functions that now implement them (`devices`, `dynamic_devices`,
  `entity_category`, `stale_devices`, `repair_issues`) plus `test_coverage`
  and `strict_typing` for the widened gate.
  *`CHANGELOG.md`.* A v0.5.1 entry written for a user, not for the
  plan: what changed, what it means, and the one known limitation
  carried over from S22. `manifest.json` bumped to match, which the new
  release-version gate now enforces on every tag.
  Gates: ruff 0 · strict mypy 0 across 42 files ·
  **353 passed · 100.00% line and branch coverage** · source-hygiene
  gate clean (density 0.201) · release-version gate clean against
  `v0.5.1`.
- 2026-09-07 — Review findings applied, all eight verified against the
  code as it stands. Four behaviour fixes: a disabled (or already
  removed) global-provider entry is now promoted on unload rather than
  waiting for a `ConfigEntryChange.REMOVED` that never arrives, and its
  rebuild-button handle is dropped in the same branch (`_runtime.py`,
  new `_entry_will_reload`); the unfiltered station lookups clear the
  caller's `observed_stops` like the filtered path already did
  (`client.py`); the adaptive-backoff re-arm is skipped once the last
  listener is gone, so a failure can no longer restart polling for an
  entry nobody is watching (`coordinator.py`); a failed seed dump is
  reported on `result.error` with the sampled/added counts cleared
  rather than being swallowed by the per-station guard
  (`matrix_rebuild.py`); `StopsMatrixStore.async_record` counts `added`
  case-insensitively, matching `lookup_in_matrix` (`store.py`).
  Two gate/test fixes: the source-hygiene `CROSS_REF` pattern now
  recognises lettered phases with numeric suffixes (`Phase A`,
  `Phase B1`) and `test_scripts.py` asserts `check_cross_references` /
  `check_module_pointers` directly so those two tests can no longer pass
  on an unrelated density failure. `README.md`'s direction-filter row
  drops "ids" (entity IDs are regenerated from the new unique ID, as
  the following paragraph already said) and `docs/architecture.md` §9,
  §10 and §11 carry the three changed invariants.
  Gates: ruff 0 · strict mypy 0 across 38 source files ·
  **360 passed · 100.00% line and branch coverage** · source-hygiene
  gate clean (density 0.203).
- 2026-09-07 — Second review pass, all six findings verified against the
  code as it stands. Documentation accuracy: the G10 note referenced
  non-existent decisions `D19` / `D20` and now cites the real ids
  (`S22` hassfest SHA pin, `S20` docstring density); the v0.5.1
  CHANGELOG entry dropped "ids" from the carried-across list, matching
  the README's own statement that entity IDs are regenerated from the
  new unique ID; `README.md`'s reconfigure row and paragraph both list
  the same four customisation fields (`name`, `icon`, `area_id`,
  `disabled_by` — exactly what `_async_capture_identity_customisations`
  captures), and `sensor.py`'s countdown comment block was deduplicated
  with the all-or-nothing note kept. §6's `time_until_arrival` wording
  and the "Why one sensor, not three" heading were corrected to match
  the two-sensor model and the per-poll (not per-read) refresh that S11
  and F14 already established, with the same correction applied to the
  two remaining stale copies in `sensor.py`. Test hardening:
  `test_exception_keys_raised_toward_users_resolve` now walks the AST's
  `Raise` nodes instead of grepping source, so a subclass such as
  `ServiceValidationError`, a chained raise, or a `translation_key`
  nested inside another argument is covered; the promotion test for a
  disabled owner now also asserts the departed entry holds no row in
  the global registry's loaded set. No behaviour changed.
  Gates: ruff 0 · strict mypy 0 across 38 source files ·
  **344 passed · 100.00% line and branch coverage** · source-hygiene
  gate clean (density 0.203).

