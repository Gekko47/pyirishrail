# Skill 09 — Improvement Roadmap Execution

## Purpose

The Bronze migration is complete. This skill governs all post-Bronze work defined in
`.cline/irish-rail-improvement-roadmap.md`. Every roadmap item must be implemented
against the current Home Assistant developer documentation, not against memory.

## Active execution: v0.3.0 Clean Baseline (2026-08-30)

Current work is governed by `.cline/clean-cut-baseline-plan.md`. Protocol:

1. Work one checkbox at a time, in the plan's phase order.
2. Run the phase's gates after each increment (ruff · strict mypy · pytest at
   the active coverage gate; phase-specific proofs where stated).
3. Tick the checkbox in the plan file and append one line to its Progress Log.
4. Where this skill or the roadmap conflicts with the plan file — notably the
   Phase 5.3 PyPI extraction, reverted 2026-08-29 in favour of the vendored
   client — the plan file wins. The remaining stale references are rewritten
   in the plan's Phase 4.

## Execution order (from the roadmap)

| Order | Item | Primary files | Acceptance |
|---|---|---|---|
| 1 | 1.1 Reconfigure flow | `config_flow.py`, `strings.json`, `translations/en.json`, `test_config_flow.py` | Flow works in UI; tests pass; docs updated |
| 2 | 1.2 Options flow (scan interval) | `config_flow.py`, `__init__.py`, `coordinator.py`, `const.py`, strings/translations, `test_config_flow.py`, `test_coordinator.py` | Interval changeable 30s–10min; coordinator honors it |
| 3 | 1.3 Next-N-trains | `sensor.py` (or new entity module), `entity.py`, strings/translations, `test_sensor.py` | New entities live; translations; tests |
| 4 | 1.4 No-trains semantics | `sensor.py`/`entity.py`, `coordinator.py`, `test_sensor.py` | Attribute/logic distinguishes empty vs error; tests |
| 5 | Phase 2 Silver rules | `sensor.py`, `coordinator.py`, `__init__.py`, tests | Each rule demonstrably satisfied; coverage ≥95% |
| 6 | Phase 3 Gold rules | `README.md`, `icons.json`, `diagnostics.py`, `__init__.py`, tests | README sections added; icons.json; repair flow |
| 7 | 5.3 Package extraction (in-repo sibling) | `pyirishrail/` package in this repo, `pyproject.toml`, `manifest.json`, integration imports, CI matrix, `tests/pyirishrail/` | Wheel published to PyPI with `py.typed`; integration depends on it via `manifest.json`; full history preserved |
| 8 | 4.2–4.5 | `api.py`/package, `coordinator.py`, CI workflows | Evidence recorded per item in the roadmap |

Work strictly in this order unless the user directs otherwise. Tick the roadmap
checkboxes as items land, and record decisions (e.g., the 1.3 surface choice,
4.2 conditional-request findings) directly in the roadmap file.

## Item-by-item implementation guidance

### 1.1 Reconfigure flow (Gold `reconfiguration-flow`)

- Add `async_step_reconfigure` to `IrishRailConfigFlow`.
- The station code is fixed; only the direction filter is editable.
- Use `self._get_reconfigure_entry()`; on submit, update `entry.data` via
  `self.async_update_reload_and_abort(entry, data=...)` (or update + targeted
  coordinator refresh if a no-reload path is genuinely safe — prefer the
  standard reload helper unless proven otherwise).
- Preserve the existing unique ID; do not re-run duplicate abort against other
  entries for the same station.
- Strings: add a `reconfigure` step to `strings.json` and
  `translations/en.json` with labels, `data_description`, errors, aborts.
- Tests: happy path, `cannot_connect`, `invalid_station`, `unknown`, abort on
  update failure. Full branch coverage of the new step.

### 1.2 Options flow for scan interval

- Add `IrishRailOptionsFlow(OptionsFlow)` returning a `vol.Schema` form with
  `scan_interval` bounded via `vol.All(vol.Coerce(int), vol.Range(min=30,
  max=600))`; default `DEFAULT_SCAN_INTERVAL` (60s) from `const.py`.
- Register an update listener in `async_setup_entry`:
  `entry.async_on_unload(entry.add_update_listener(_async_update_listener))`.
  The listener reloads the entry (simplest correct approach) or updates
  `coordinator.update_interval` in place.
- Connection data stays in `entry.data`; the interval belongs in
  `entry.options`.
- Strings: `init` step in strings/translations.
- Tests: valid value stored and honored by the coordinator; out-of-range
  values rejected by the schema (form re-shown with error); listener applied.

### 1.3 Next-N-trains visibility

- Before implementing, decide the surface and record the decision in the
  roadmap:
  - extra sensors (trains 2–3 due/destination) — simplest, consistent with
    existing entity model;
  - attribute expansion on existing sensors — no new entities, but attributes
    are not state-friendly for automations;
  - dedicated list/entity-per-train — most flexible, most code.
- Default recommendation: extra sensors with translation keys, reading
  `coordinator.data[1]` / `[2]` defensively (absent when fewer trains exist —
  entity becomes unavailable or reports `None` state, never crashes).
- Stable unique IDs: `f"{entry.unique_id}_train2_due"` etc. Never index-based
  naming that shifts meaning between refreshes beyond the fixed 2nd/3rd slot.
- Update README Entities section.

### 1.4 Explicit "no trains scheduled" semantics

- Distinguish "API reachable, zero trains" (expected, e.g. overnight) from
  "API unreachable" (error → coordinator `UpdateFailed` → entities
  unavailable).
- Implement an explicit attribute (e.g. `api_reachable: true/false`) or
  availability logic driven by coordinator success; do not conflate an empty
  list with a failure.
- Tests for both states. Update README Conditions section.

### Phase 2 — Silver rules

- `parallel-updates`: declare `PARALLEL_UPDATES` in `sensor.py` (a small
  constant such as 1–5; justify the value).
- `log-when-unavailable`: ensure exactly one log line on transition to
  unavailable and one on recovery. The coordinator's default behavior may
  suffice — verify against the current rule text and make the behavior
  explicit and tested.
- `entity-unavailable`: extend `test_sensor.py` with an explicit test that
  entities report `unavailable` after a failed refresh.
- `config-entry-unloading`: add a test for unload + reload; document behavior.
- `test-coverage`: raise the CI coverage gate to ≥95% when Silver is
  targeted; keep it there as code grows.

### Phase 3 — Gold rules

- `docs-examples` / `docs-use-cases` / `docs-troubleshooting`: add README
  sections — departure alert and delay notification automations; commuter
  dashboard, delay alerts, presence-based reminders; API downtime, empty
  data at night, retry states.
- `icon-translations`: add `icons.json` with per-entity icons; keep entity
  translation keys aligned.
- `exception-translations`: convert user-facing error messages to
  translation-keyed `HomeAssistantError`s.
- `repair-issues`: raise a repair issue when a station returns persistently
  empty data during service hours (possible API/schema change). Use
  `ir.async_create_issue` with a translation key.
- `diagnostics`: add redaction edge-case tests.
- `entity-device-class`: re-confirm device classes as entities grow.

### Phase 5.3 Package extraction (Platinum path, in-repo sibling layout, revised 2026-08-27)

- The package lives in this same repo at the top level as `pyirishrail/`
  (revised 2026-08-27; previous plans put it in a separate repo). It is
  built as a wheel from this repo's `pyproject.toml` and published to PyPI
  by CI on tag pushes. The integration then re-consumes it as an external
  `manifest.json` requirement.
- Move (do not rewrite) `custom_components/irish_rail/api.py` to
  `pyirishrail/api.py` with `git mv` so history follows. Also move any
  helpers worth exposing (model dataclasses, exception types) into the
  package.
- Update integration imports: `from .api import ...` →
  `from pyirishrail import ...` across `__init__.py`, `coordinator.py`,
  `config_flow.py`, etc.
- Update `manifest.json` to `"requirements": ["pyirishrail>=0.2,<1.0"]`
  and remove the now-transitive `defusedxml`.
- Reshape CI as a two-job matrix (library + integration); see Skill 07.
- Split `tests/` into `tests/pyirishrail/` (no HA imports) and the existing
  `tests/components/irish_rail/`. The 95% coverage gate applies per suite.
- Record the `defusedxml` decision in the new package's `README.md` (and
  cross-link from `quality_scale.yaml`).
- Satisfies `async-dependency`, the PEP-561 sub-gap of `strict-typing`,
  and indirectly lands the test-inclusive mypy gate from Phase 5.2.
- `inject-websession` is unchanged — already `done` in
  `quality_scale.yaml`.

### 4.2 Conditional requests

- Probe the Irish Rail API for `ETag` / `Last-Modified` support.
- If supported: send validators, skip parsing on 304, record evidence in the
  roadmap. If not: record the finding and close the item.

### 4.3 Adaptive backoff polling

- On consecutive failures, back the coordinator interval off exponentially
  (cap ~15 minutes); restore the configured interval immediately on success.
- Implement inside the coordinator (adjust `update_interval` in
  `_async_update_data` failure/success paths). Tests simulate failure
  streaks and recovery.

### 4.4 XML layer hardening

- Normalize namespaces once at parse time; remove dual namespace-or-not
  `findall` fallbacks in `api.py`.
- Re-run the full suite; coverage must not regress below 95%.

### 4.5 CI breadth

- Keep the HACS validation job green; add a hassfest-style manifest
  validation job for the custom integration; consider pytest-xdist for
  faster CI.

## Standing requirements for every item

- ruff clean · strict mypy clean · all tests passing
- coverage ≥90% (target ≥95% once Silver work begins)
- no blocking calls in async paths
- `quality_scale.yaml` updated whenever a rule's status changes
- roadmap checkboxes ticked and decisions recorded in the roadmap file
- strings.json and translations/en.json kept structurally aligned