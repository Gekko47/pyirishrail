# Skill 10 — Streamline Roadmap Execution

## Purpose

The v0.3.0 Clean Baseline (`.cline/clean-cut-baseline-plan.md`) is
complete; the prior improvement roadmap
(`.cline/irish-rail-improvement-roadmap.md`) is also complete. The
next active plan is **`.cline/streamline-roadmap.md`**, which is a
maintainability pass: simplify the source, compress the
documentation, eliminate duplicated logic, **without** losing
Platinum quality-scale compliance or the 100% line coverage gate.

This skill governs how that work is executed. It complements
`09-roadmap-execution.md` (which governs the now-complete v0.3.0
work) by adding the rules specific to the streamline pass.

## Authority

- **Active plan:** `.cline/streamline-roadmap.md`. Read it first;
  this skill is the per-step *how*, the roadmap is the *what* and
  *order*.
- **Design source of truth:** `docs/architecture.md`. The
  long-form invariants behind every non-trivial decision in the
  integration live there. When refactoring, the new code must
  continue to satisfy every section in that file. If a refactor
  invalidates a section, update the section in the same commit.
- **Skills 00–09** still apply for the parts of the work they
  govern (architecture, config flow, testing, etc.). This skill
  only adds streamline-specific rules; it does not supersede them.

## 1. Docstring discipline

The single biggest contributor to the bloated source is
docstring-density (~0.5 lines per source LOC today). The target
density is **0.15 lines per source LOC**; Phase A's intermediate
gate is 0.20. Three categories govern every docstring:

| Category | Disposition | Examples |
|---|---|---|
| **Contract docstring** — describes what the function returns or what the class holds, in one or two lines. | **Keep tight.** | `"""Return the per-hass request gate, creating it on first call."""` |
| **Non-obvious invariant** — explains a subtle correctness requirement the next reader would have to rediscover. | **Keep, with a 1–3 line summary + a `See docs/architecture.md §N` pointer** if the rationale is more than a sentence. | `RequestGate.acquire` — "exactly one wait on exactly one event; see docs/architecture.md §3 for the cancellation-safety reasoning" |
| **Design history / narration** — "earlier designs considered…", "by design…", "this layer was removed because…", "Skill N says…", "roadmap 1.X decided…". | **Delete from source. Move to `docs/architecture.md` if it carries real information; delete outright if it does not.** | The 80-line XML-policy essay in `pyirishrail/api.py`'s module docstring; the "earlier designs considered a third layer" paragraph; all `Skill 02` / `Phase 1` / `roadmap 1.3` references. |

**Anti-patterns to delete on sight:**

- `"""Initialize the monitor bound to a shared API client."""` on a
  method named `__init__` of a class named `IrishRailApiHealthMonitor`.
- Module docstrings that re-explain what the module's filename
  already says.
- Docstrings that narrate the change history of a function
  ("Originally this used `defusedxml`, but…").
- "Decision" prose in source. Decisions belong in the
  `Decisions (resolved)` table of the active roadmap.

**Refactor rule:** when a docstring exceeds three lines, ask
"is the second half design history or contract?" If design
history, move it to `docs/architecture.md` and keep the contract
half in the source.

## 2. Module boundaries

After Phase B the integration should have no `pyirishrail/`
sub-package and no `gate.py` / `health.py` split. The target
shape is:

```
custom_components/irish_rail/
├── __init__.py
├── _runtime.py        # NEW — RuntimeRegistry: shared singletons
├── binary_sensor.py
├── button.py
├── client.py          # was pyirishrail/api.py
├── config_flow.py
├── const.py
├── coordinator.py
├── diagnostics.py
├── entity.py
├── errors.py          # was pyirishrail/errors.py
├── identity.py
├── lib_const.py       # was pyirishrail/_const.py
├── manifest.json
├── matrix_rebuild.py  # owns the unified sample_stops_matrix loop
├── models.py          # was pyirishrail/models.py
├── request_gate.py    # was pyirishrail/_gate.py (the primitive)
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
└── stops_matrix.seed.example.json   # smoke fixture only
```

**`scripts/build_stops_matrix.py` becomes a thin CLI wrapper
(≤ 60 lines)** that calls `sample_stops_matrix` from
`matrix_rebuild.py`. Its only remaining job is argument parsing
and the atomic temp-file write.

**The `RequestGate` primitive is framework-agnostic** and stays
in its own file (`request_gate.py`). It is not a singleton; the
per-HA instance lives on `_runtime.RuntimeRegistry`.

## 3. The runtime registry

The Phase B3 consolidation replaces the current
`gate.py` + `health.py` pair with a single `_runtime.py` module.
The contract:

```python
@dataclass
class RuntimeRegistry:
    """Owns the per-hass shared singletons for the integration."""

    loaded_entry_ids: set[str] = field(default_factory=set)
    request_gate: RequestGate | None = None
    health_monitor: ConnectivityMonitor | None = None
    stops_store: StopsMatrixStore | None = None
    service_entity_owner: str | None = None
    rebuild_entity: RebuildButton | None = None
    last_rebuild_result: RebuildResult | None = None

    def register_entry(self, hass, entry_id, client) -> bool: ...
    async def deregister_entry(self, hass, entry_id) -> bool: ...
    def claim_service_entities(self, hass, entry) -> bool: ...
```

**`__init__.py` becomes:**

```python
async def async_setup_entry(hass, entry):
    client = IrishRailClient(async_get_clientsession(hass))
    coordinator = IrishRailDataUpdateCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = IrishRailRuntimeData(client=client, coordinator=coordinator)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    registry = get_runtime(hass)
    registry.register_entry(hass, entry.entry_id, client)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass, entry):
    ir.async_delete_issue(hass, DOMAIN, empty_data_issue_id(entry))
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    registry = get_runtime(hass)
    last = await registry.deregister_entry(hass, entry.entry_id)
    return unloaded
```

**Discipline:**

- All reads and writes to `hass.data[DOMAIN]` go through the
  registry. The keys are private to `_runtime.py`; no other
  module reaches into `hass.data[DOMAIN]` directly.
- `register_entry` is the only place that creates the gate and
  the health monitor. `deregister_entry` is the only place that
  releases them, and only when the entry set goes empty. This
  is structural, not commented-in.
- The `_runtime.py` module exposes `get_runtime(hass)` for
  read-only access and the methods above for mutating access.
  No other functions are part of the public surface.

## 4. Sensor consolidation

The Phase C1/C2/C3 work collapses three sensors into one. The
discipline:

- One `IrishRailNextTrainSensor` class, not three.
- All user-visible state lives on that one sensor. The two
  removed sensors are not re-introduced as deprecation aliases
  (the integration has no users; migration shims are forbidden
  by the v0.3.0 baseline's ground rules).
- The attribute surface is fixed and small. New attributes
  require a `quality_scale.yaml` evidence update, a `strings.json`
  translation key, and a test that pins the new key. Adding
  attributes ad-hoc is a regression.

## 5. Quality scale discipline

`quality_scale.yaml` is the contract that proves Platinum. The
streamline work must not weaken it. The rules:

- Every `done` keeps a working file/function pointer. If a
  refactor moves code, the pointer moves with it.
- Every `exempt` keeps its justification.
- The YAML shrinks by **compressing the prose in each comment**,
  not by removing evidence.
- The `docs/architecture.md` cross-link is added to the most
  chatty rules (the XML policy rule, the gate singleton rule,
  the runtime-data rule) so the rationale lives in one place.

## 6. Test discipline

The 100% line coverage gate does not drop. New tests are added
only when a refactor introduces a new branch; the streamline
plan does not call for new features and so should not need many
new tests. Phase E may reduce the *count* of tests by merging
duplicate cases, but the *coverage* of the source must not
regress.

When a refactor moves code:

- Existing tests follow the move (import path updates).
- Tests that pinned a docstring (e.g. `assert
  "by design" in module.__doc__`) are deleted; they pinned
  prose, not behaviour.
- The test must continue to pin the observable behaviour
  (return value, raised exception, side effect on the shared
  registry), not the docstring text.

## 7. Standing requirements for every increment

- ruff clean · strict mypy clean · 100% line coverage
- No new public attributes on existing classes
  (the only new public type is `RuntimeRegistry`)
- No behaviour changes during refactors
- Tick the corresponding checkbox in `.cline/streamline-roadmap.md`
  before moving to the next step
- Append one line to the Progress log of the streamline roadmap
  per increment
- Update `docs/architecture.md` if the refactor changes an
  invariant the doc covers

## 8. Anti-patterns

- **Re-introducing the `pyirishrail/` sub-package.** The
  streamlining reason for the rename is to delete the
  sub-package. Anyone re-adding it must override this skill in
  the active roadmap.
- **Re-adding a "by design" essay to a module docstring.** If
  the design needs explaining, link to `docs/architecture.md`,
  do not re-narrate in source.
- **Re-introducing the `pyirishrail` package to PyPI.** Decision
  S2 in the streamline roadmap explicitly keeps the client
  internal; the v0.3.0 baseline already reversed the 2026-08-28
  PyPI extraction.
- **Adding a third stops-matrix implementation.** The
  unification in B2 is the source of truth; if a new
  caller needs the data, it goes through `sample_stops_matrix`.
- **Splitting the runtime registry back into `gate.py` and
  `health.py`.** The consolidation is structural; do not
  re-split.