---
name: roadmap-execution
description: How to execute increments against the active Streamline Roadmap, plus the review order, acceptance criteria, and phase-specific discipline. Load before starting any multi-step work, when picking the next roadmap checkbox, when deciding how to refactor a module, when a PR needs review, or when any question touches the active plan, phases A-F, docstring density, RuntimeRegistry, module boundaries, sensor consolidation, audit remediation, or acceptance.
---

# Roadmap Execution and Acceptance

How work lands. The active plan is the Streamline Roadmap; the prior roadmaps
are completion records.

---

## 1. Which plan is active

| Plan | Status |
|---|---|
| **`.cline/streamline-roadmap.md`** | **ACTIVE** — maintainability pass (A–E) plus Phase F audit remediation |
| `.cline/clean-cut-baseline-plan.md` | complete (v0.3.0 Clean Baseline) |
| `.cline/irish-rail-improvement-roadmap.md` | complete (pre-v0.3.0) |

The streamline roadmap wins for any work that is **not** an open checkbox on
the prior plans. The prior plans serve as the completion record; where they
conflict with the streamline roadmap, the streamline roadmap wins.

Tick checkboxes in the **streamline** roadmap when executing new work, not in
the old ones.

### Precedence when sources disagree

1. **Live Home Assistant developer documentation** — always authoritative
2. **The active roadmap** (`.cline/streamline-roadmap.md`)
3. **This skill / the other skills**
4. The superseded `.cline/` plans — historical context only

If a skill or an old prompt says to do something the current documentation
contradicts, follow the documentation and record the discrepancy.

### Authority within the roadmap work

- **Active plan:** `.cline/streamline-roadmap.md` — the *what* and the *order*.
- **This skill:** the per-step *how*.
- **Design source of truth:** `docs/architecture.md` — the long-form
  invariants behind every non-trivial decision. When a refactor changes an
  invariant the doc covers, the new code must continue to satisfy every
  section; if a refactor invalidates a section, update that section in the
  same commit. Rewrite, do not delete without replacement.
- The other skills still apply for the parts of the work they govern
  (architecture, config flow, entities, testing). This skill **adds**
  streamline-specific rules; it does not supersede them.

### The roadmap is a target, not a snapshot

The roadmap describes what the streamline pass *should* produce. Parts of it
have landed; parts have not. **Read the actual source before relying on any
shape described here** — file names, class names, function signatures, and
which phases are done all drift. When this skill and the code disagree, the
code is the current truth and the roadmap is the plan; reconcile them in the
roadmap file rather than assuming either.

---

## 2. Increment protocol

For every increment:

1. Work **one checkbox at a time**, in the plan's phase order.
2. Run the phase's gates after each increment (ruff · strict mypy · pytest at
   the active coverage gate; phase-specific proofs where stated).
3. Tick the checkbox in the plan file and append **one line** to its Progress
   Log.
4. Record decisions in the plan's `Decisions (resolved)` table.
5. Update `docs/architecture.md` if the refactor changes an invariant the doc
   covers.
6. If a Quality Scale rule's status changed, update `quality_scale.yaml` with
   a working file/function pointer.

Do not move to the next step before ticking the current one.

---

## 3. Streamline discipline

### 3.1 Docstring density

The single biggest contributor to the bloated source is docstring density
(~0.5 lines per source LOC before the pass). Target density is **0.15**; the
Phase A intermediate gate is 0.20. CI currently enforces the Phase A
threshold.

Three categories govern every docstring:

| Category | Disposition | Example |
|---|---|---|
| **Contract** — what the function returns or the class holds, in one or two lines. | **Keep tight.** | `"""Return the per-hass request gate, creating it on first call."""` |
| **Non-obvious invariant** — a subtle correctness requirement the next reader would have to rediscover. | **Keep**, with a 1–3 line summary + a `See docs/architecture.md §N` pointer if the rationale exceeds a sentence. | `RequestGate.acquire` — "exactly one wait on exactly one event; see docs/architecture.md §3 for the cancellation-safety reasoning" |
| **Design history / narration** — "earlier designs considered…", "by design…", "this layer was removed because…", "Skill N says…", "roadmap 1.X decided…". | **Delete from source.** Move to `docs/architecture.md` if it carries real information; delete outright if not. | The 80-line XML-policy essay; the "earlier designs considered a third layer" paragraph. |

**Anti-patterns to delete on sight:**
- A `"""Initialize the monitor bound to a shared API client."""`-style
  docstring sitting on `__init__` of a class whose name already says that.
- Module docstrings that re-explain what the filename already says.
- Docstrings that narrate change history ("Originally this used
  `defusedxml`, but…").
- "Decision" prose in source. Decisions belong in the `Decisions (resolved)`
  table of the active roadmap.

**Refactor rule:** when a docstring exceeds three lines, ask "is the second
half design history or contract?" If design history, move it to
`docs/architecture.md` and keep the contract half in source.

**No project-internal cross-references in source.** CI fails the build on
`Skill N` / `Phase N` / `roadmap N.X` references in source files. Those
breadcrumbs belong in the active roadmap file. Replace with a
`docs/architecture.md` pointer.

### 3.2 Module boundaries

After Phase B the integration has **no** `pyirishrail/` sub-package and **no**
`gate.py` / `health.py` split. The target shape:

```
custom_components/irish_rail/
├── __init__.py
├── _runtime.py        # RuntimeRegistry: shared singletons
├── binary_sensor.py
├── button.py
├── client.py          # framework-agnostic client
├── config_flow.py
├── const.py
├── coordinator.py
├── diagnostics.py
├── entity.py
├── errors.py
├── identity.py
├── lib_const.py
├── manifest.json
├── matrix_rebuild.py  # owns the unified sample_stops_matrix loop
├── models.py
├── request_gate.py    # the RequestGate primitive
├── sensor.py
├── services.yaml
├── store.py
├── strings.json
├── translations/en.json
├── types.py
├── py.typed
├── icons.json
├── quality_scale.yaml
├── brand/
└── stops_matrix.seed.json          # target: 0 in tree, generated at release
```

Verify the current tree with `list_files custom_components/irish_rail` before
assuming any of this has landed — the roadmap is a *target*, not a snapshot.

`scripts/build_stops_matrix.py` becomes a thin CLI wrapper (≤ 60 lines) that
calls `sample_stops_matrix` from `matrix_rebuild.py`. Its only remaining job is
argument parsing and the atomic temp-file write.

The `RequestGate` primitive is framework-agnostic and stays in its own file
(`request_gate.py`). It is **not** a singleton; the per-HA instance lives on
the runtime registry.

### 3.3 The runtime registry

Phase B3 replaced the `gate.py` + `health.py` pair with a single `_runtime.py`.
**Verify the current shape in `_runtime.py` before relying on this summary** —
the API below is the as-landed surface, but the file is the authority.

`_runtime.py` holds `RuntimeRegistry` and `ConnectivityMonitor`, plus the
module-level accessors that are the *only* sanctioned way to touch
`hass.data[DOMAIN]`:

| Symbol | Purpose |
|---|---|
| `get_runtime(hass)` | read-only access to the registry (`None` if absent) |
| `get_request_gate(hass)` / `async_get_request_gate(hass)` | read / get-or-create the shared `RequestGate` |
| `async_release_request_gate(hass)` | drop the gate when the entry set empties |
| `get_health_monitor(hass)` / `ensure_health_monitor_started(hass, client)` | read / start the shared `ConnectivityMonitor` |
| `claim_service_entities(hass, entry)` | claim providership of the global entities |
| `async_note_entry_loaded(hass, entry_id, client)` | register a loaded entry |
| `ensure_health_monitor_started` | idempotent start of the periodic probe |

`RuntimeRegistry` owns exactly three pieces of state: `loaded_entry_ids`,
`request_gate`, and `health_monitor`. Note the monitor object is **kept** across
an unload/reload so a reload restarts the same instance and its probe history
survives; only the gate is dropped so the next user gets a fresh rate budget.

`async_setup_entry` follows this shape:

```python
session = async_get_clientsession(hass)
client = IrishRailClient(session, gate=async_get_request_gate(hass))
coordinator = IrishRailDataUpdateCoordinator(hass, client, entry)
await coordinator.async_config_entry_first_refresh()
entry.runtime_data = IrishRailRuntimeData(client=client, coordinator=coordinator)
entry.async_on_unload(entry.add_update_listener(_async_update_listener))
await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
```

`async_unload_entry` deletes the repair issue first, unloads the platforms, and
releases the shared singletons when the entry set empties.

**Discipline:**
- All reads and writes to `hass.data[DOMAIN]` go through the registry
  accessors. The keys are private to `_runtime.py`; no other module reaches
  into `hass.data[DOMAIN]` directly.
- The registry is the **only** place that creates the gate and the health
  monitor, and the only place that releases them. This is structural, not
  commented-in.
- `get_runtime` is for reads; the accessor functions are for mutating access.
  Nothing else is public surface.

### 3.4 Sensor surface

The active roadmap (Decision S4) settled on **two** per-station sensors, both
instantiated from the one `IrishRailDueTrainSensor` class:

- `next_train_due` — the next train's expected arrival (TIMESTAMP).
- `following_train_due` — the following train's expected arrival (TIMESTAMP),
  `unknown` when fewer than two trains are scheduled.

Both are created in `sensor.py`'s `async_setup_entry`. The dropped
`next_train_destination` and `next_train_delay` sensors are not re-introduced
as deprecation aliases — the integration has no users, and migration shims are
forbidden by the baseline's ground rules. Do not add a third per-station
sensor; new arrival detail goes on an existing sensor's
`extra_state_attributes`.

- The attribute surface is fixed and small (7 keys on `next_train_due`, 5 on
  `following_train_due`). New attributes require a `quality_scale.yaml`
  evidence update, a `strings.json` translation key, and a test that pins the
  new key. Adding attributes ad-hoc is a regression.

### 3.5 Quality scale discipline

`quality_scale.yaml` is the contract that proves Platinum. The streamline work
must not weaken it:
- Every `done` keeps a working file/function pointer. If a refactor moves
  code, the pointer moves with it.
- Every `exempt` keeps its justification.
- The YAML shrinks by **compressing the prose in each comment**, not by
  removing evidence.
- The `docs/architecture.md` cross-link is added to the most chatty rules (XML
  policy, gate singleton, runtime-data) so the rationale lives in one place.

### 3.6 Test discipline

The 100% line coverage gate does not drop. Coverage is the floor, not the
trigger: a new test is required whenever **behaviour changes or a roadmap
acceptance item is met** — including a behaviour fix that adds no new branch
(the changed line may already be covered, but the corrected behaviour is not
yet pinned). New branches, new entities, new config-flow branches and
behaviour fixes all need tests. The streamline plan calls for no new
features and so should need few new tests beyond those. The test phase may
reduce the *count* of tests by merging duplicate cases, but the *coverage* of
the source must not regress.

When a refactor moves code:
- Existing tests follow the move (import path updates).
- Tests that pinned a docstring (e.g. `assert "by design" in module.__doc__`)
  are deleted — they pinned prose, not behaviour.
- The test must continue to pin observable behaviour (return value, raised
  exception, side effect on the shared registry), not docstring text.

### 3.7 Anti-patterns

- **Re-introducing the `pyirishrail/` sub-package.** The streamlining reason
  for the rename is to delete the sub-package. Anyone re-adding it must
  override this skill in the active roadmap.
- **Re-adding a "by design" essay to a module docstring.** If the design
  needs explaining, link to `docs/architecture.md`; do not re-narrate in
  source.
- **Re-introducing the `pyirishrail` package to PyPI.** Decision S2 keeps the
  client internal; the v0.3.0 baseline already reversed the 2026-08-28
  extraction.
- **Adding a third stops-matrix implementation.** The unification in B2 is the
  source of truth; a new caller goes through `sample_stops_matrix`.
- **Splitting the runtime registry back into `gate.py` and `health.py`.** The
  consolidation is structural; do not re-split.
- **No new public attributes on existing classes.** The only new public type
  introduced by the streamline work is `RuntimeRegistry`.
- **No behaviour changes during refactors.** Any observable change (default
  value, attribute key, file path the user sees) is its own committed step,
  after the underlying refactor lands.

### 3.8 Standing requirements for every increment

- ruff clean · strict mypy clean · 100% line coverage
- No new public attributes on existing classes
- No behaviour changes during refactors
- Tick the corresponding checkbox in `.cline/streamline-roadmap.md` before
  moving to the next step
- Append one line to the Progress log of the streamline roadmap per increment
- Update `docs/architecture.md` if the refactor changes an invariant the doc
  covers

---

## 4. Review order

Work through this order when reviewing a change.

### 1. Live rules
Fetch and inspect the Quality Scale overview, rules, and checklist pages.
Record the current rule IDs and any change from what this skill claims.

### 2. Source behaviour
Compare the old pyirishrail API, the XML fixtures, the new typed models, and
the new entities. Ensure no required functionality was silently dropped.

### 3. Async safety
Search the repository for `requests`, blocking HTTP, synchronous file/network
calls in async paths, `time.sleep`, and `hass.helpers`. Any occurrence needs
review.

### 4. HTTP client
Confirm: injected shared session, explicit timeout, typed exceptions, safe
XML parser, no session-per-call.

### 5. Config flow
Confirm: UI setup, `data_description`, validation before entry creation,
stable unique ID, duplicate prevention, translated errors, complete tests.

### 6. Runtime architecture
Confirm: one coordinator per entry, entities do not call the API directly,
initial refresh before forwarding platforms, runtime objects in
`entry.runtime_data`, registry discipline for `hass.data[DOMAIN]`.

### 7. Entity architecture
Confirm: stable entity unique IDs, `_attr_has_entity_name = True`, translation
keys, coordinator lifecycle, no mutable user-facing text in unique IDs.

### 8. Branding
Confirm the required current branding assets exist and are valid.

### 9. Tests
Confirm: API failure paths, malformed XML, complete config-flow coverage,
duplicate config, coordinator failure, setup failure. See the testing-and-ci
skill for the full layer map.

### 10. Tooling
Run current pytest, lint, type checks, and Home Assistant validation /
hassfest where applicable.

### 11. Quality scale
For every rule, produce:

```
rule-id: done — path/function
```

or:

```
rule-id: exempt — reason
```

Never `rule-id: done — probably satisfied`.

---

## 5. Streamline acceptance

When a PR targets the streamline roadmap, this checklist applies. **If any item
is violated, the increment is not accepted back to the active plan's gate,
even if ruff/mypy/coverage are green.**

1. **Platinum still proven.** Every `done` and every `exempt` in
   `quality_scale.yaml` lands on a real file/function. The YAML may shrink
   (compressed prose + `See docs/architecture.md §N`), but evidence is not
   removed.
2. **Docstring density gate.** The active phase's gate is met. CI measures it
   on every build.
3. **No "what the name says" docstrings added.** Any new docstring either
   describes a non-obvious invariant (with a `docs/architecture.md` pointer)
   or describes the contract in one or two lines.
4. **No `Skill N` / `Phase N` / `roadmap N` references in source.** CI enforces
   this.
5. **Module boundaries respected.** No `pyirishrail/` re-introduction, no
   split of `_runtime.py` back into `gate.py`/`health.py`, no third
   stops-matrix rebuild implementation.
6. **`RuntimeRegistry` discipline.** Reads and writes to `hass.data[DOMAIN]`
   go through the registry; its keys are private to `_runtime.py`.
7. **Sensor surface stable.** The two per-station sensors
   (`next_train_due`, `following_train_due`) are the fixed surface — no third
   per-station sensor. New arrival detail goes on an existing sensor's
   `extra_state_attributes` and requires the full attribute pipeline (string
   translation, test, evidence note).
8. **`docs/architecture.md` updated alongside code** in the same commit when
   an invariant changes.
9. **Test discipline.** No tests pinning prose; every behaviour change and
   roadmap acceptance item is pinned by a test (including fixes that add no
   new branch); the 100% coverage gate is preserved; test *count* may drop but
   test *coverage* may not regress.
10. **No new public attributes on existing classes** beyond
    `RuntimeRegistry`.

### Final acceptance statement

The implementation is acceptable only when:
- all applicable Quality Scale rules remain actually satisfied
- the roadmap item under review meets its stated acceptance criteria
- all legitimate exemptions are documented
- tests pass at the active coverage gate
- validation passes
- no unresolved blocking TODO remains
- roadmap checkboxes and `quality_scale.yaml` agree with the implementation

If something cannot be verified, mark it **unresolved** rather than falsely
claiming compliance.

---

## 6. Historical item-by-item guidance

The pre-streamline roadmap items are complete. This table is retained so the
per-item *how* remains findable. Where it conflicts with a plan file — notably
the Phase 5.3 PyPI extraction, reverted 2026-08-29 — the plan file wins.

| Item | Focus | Primary files | Status |
|---|---|---|---|
| 1.1 Reconfigure flow | Gold `reconfiguration-flow` | `config_flow.py`, strings/translations, `test_config_flow.py` | complete |
| 1.2 Options flow (scan interval) | 30s–10min, coordinator honours it | `config_flow.py`, `__init__.py`, `coordinator.py`, `const.py`, tests | complete |
| 1.3 Next-N-trains visibility | Surface decision recorded defensively | `sensor.py`, `entity.py`, tests | complete |
| 1.4 No-trains semantics | Empty vs error distinguished | `sensor.py`, `coordinator.py`, `test_sensor.py` | complete |
| Phase 2 | Silver rules; coverage gate ≥95% | `sensor.py`, `coordinator.py`, `__init__.py`, tests | complete |
| Phase 3 | Gold rules: README, icons, repairs, exception translations | `README.md`, `icons.json`, `diagnostics.py`, tests | complete |
| 4.2 Conditional requests | Probe ETag/Last-Modified; record finding | client | closed |
| 4.3 Adaptive backoff | Exponential, cap ~15min, restore on success | `coordinator.py` | complete |
| 4.4 XML layer hardening | Normalize namespaces once | client | complete |
| 4.5 CI breadth | HACS + hassfest jobs | CI workflows | complete |
| 5.3 Package extraction | PyPI wheel | — | **reverted**; client stays internal |
