# Phase G — Audit Remediation (0.5.0 review)

Derived from the comprehensive repository review. This plan covers the
**R1–R5 structural recommendations** plus the **P0 defects** they
resolve. Work lands as one checkbox at a time, in the order below.

The plan file lives here; the roadmap records the phase (see **G0**).

---

## Invariant this phase is built around

> The "Irish Rail Services" device, its two entities, and the
> `rebuild_stops_matrix` service exist **if and only if** at least one
> station config entry is loaded.

The "own grouping, not attached to a station device" half is **already
satisfied** — both globals carry `GLOBAL_SERVICES_IDENTIFIER`
(`const.py:109`), a fixed identifier independent of any station's
`entry.unique_id`. G1 adds a test pinning that, so it cannot regress.

The `iff` half is the gap.

---

## G0 — Record the phase in the active roadmap

Append a **Phase G** section to `.cline/streamline-roadmap.md` with
checkboxes `G1`–`G11` mirroring this file, add the new decisions
(`S14`–`S18`) to the resolved-decisions table, and note in the phase
intro that it supersedes the review's prioritised list.

**Why first:** the roadmap protocol forbids implementing work that is
not recorded in the active plan. Also record the **MEDIUM-8 scope
correction** there — same station + same direction is already a no-op
(`config_flow.py:543`), so only a direction change is destructive.

---

## G1 — Provider election derived from the loaded set (fixes HIGH-1)

**Files:** `_runtime.py`, `binary_sensor.py`, `button.py`
**Tests:** `test_health.py`, `test_runtime.py`, new cases in `test_init.py`

### Design

Replace the sticky "claim once, never revisit" model with an election
computed from `RuntimeRegistry.loaded_entry_ids`, which is already the
single source of truth for the shared singletons.

The current defect is two lines in `claim_service_entities`
(`_runtime.py:454-460`): the liveness check consults
`hass.config_entries.async_entries(DOMAIN)`, which ignores load state,
and `GLOBAL_PROVIDER_KEY` is cleared only inside `async_release()`, which
runs only at **zero** loaded entries. So removing the owner while a
sibling survives leaves a key naming a dead entry, and the sibling's
platform setup already returned early at `binary_sensor.py:133`.

### Steps

1. Add to `_runtime.py`, beside the existing lifecycle helpers
   (`async_note_entry_loaded` / `async_note_entry_unloaded`):

   - `elect_provider(hass, entry_id) -> bool` — returns `True` when
     `entry_id` owns the globals after the call. Keeps the existing
     sticky fast path (`current == entry_id` → `True`) so an owner that
     re-registers is not disturbed. Otherwise, if a *loaded* entry
     already holds it, return `False`. Otherwise elect `entry_id`.

   - `disown_provider_if(hass, entry_id) -> None` — clears
     `GLOBAL_PROVIDER_KEY` when it names `entry_id`.

   - `promote_provider(hass) -> bool` — when no provider is set and
     `loaded_entry_ids` is non-empty, pick `min(loaded_entry_ids)` for
     determinism, clear any rows still pinned to a departed owner, set
     the key, and re-add the two entities to that entry's live
     platforms. Returns whether a promotion happened.

2. Change the liveness test inside the claim path from
   `hass.config_entries.async_entries(DOMAIN)` membership to
   `current in registry.loaded_entry_ids`. **This one line is the
   actual HIGH-1 fix**; the rest makes the lifecycle self-healing.

3. In `async_note_entry_unloaded` (`_runtime.py:403`): after
   `discard`, if the departing entry held providership, disown it and
   call `promote_provider`. The existing zero-entries `async_release()`
   branch is unchanged and continues to own gate release, monitor stop
   and `_drop_session_keys`.

4. **Promotion must not fight an in-flight reload.** A reload also
   passes through `async_unload_entry`. Do not promote synchronously.
   Instead, after the unload settles, check whether the departed id is
   back in `loaded_entry_ids` or whether a provider has since been
   claimed; only then promote. This mirrors what
   `hass.config_entries.async_schedule_reload` itself does.

5. **Re-add entities directly; do not reload the survivor.** Reloading a
   live station entry makes its own sensors disappear and reappear, which
   is user-visible churn on an entry that did nothing wrong. Use the
   public `homeassistant.helpers.entity_platform.async_get_platforms`
   to locate the survivor's live `binary_sensor` and `button` platforms
   (filter on `platform.config_entry.entry_id`), and call
   `EntityPlatform.async_add_entities` with freshly constructed
   entities.

   This requires the construction logic currently inline in
   `binary_sensor.async_setup_entry` and `button.async_setup_entry` to
   be extracted into module-level builders
   (`build_connectivity_sensor(hass, monitor)` and
   `build_rebuild_button(hass, client)`), so the setup path and the
   promotion path share one source of truth.

   **Fallback if `async_get_platforms` proves insufficient:** fall back
   to `async_schedule_reload(survivor_id)`. Correct either way; the
   reload is only less polite.

6. Register the `rebuild_stops_matrix` service from the **runtime
   layer**, not from `button.async_setup_entry` (`button.py:314`).
   Today the service is created and destroyed by one entry's platform
   lifecycle, which is why it dies with the owner even when a sibling
   survives. Move registration into the promote/claim path and removal
   into the zero-survivors path, so service lifetime tracks the loaded
   set.

7. Re-point `_purge_orphan_global_entities` (`_runtime.py:472`) so it
   also handles rows still pinned to a *just-departed* owner at
   promotion time, not only a fully-removed one. Without this the
   survivor's `async_add_entities` collides with rows HA has not swept.

### Acceptance

- Remove the owner while a sibling stays loaded → device, both entities
  and the service reappear, all pinned to the sibling.
- Remove the last entry → device, both entities and the service are
  gone; `GLOBAL_PROVIDER_KEY` cleared.
- Owner unloaded then reloaded (not removed) → no promotion, no
  duplicate registration, provider unchanged.
- Election is deterministic — same survivor regardless of unload order.
- Both globals carry `GLOBAL_SERVICES_IDENTIFIER` and **no** station
  device identifier (pins the grouping requirement).

---

## G2 — Transactional reconfigure (fixes HIGH-3 and MEDIUM-8)

**Files:** `__init__.py`
**Tests:** `test_config_flow.py`, `test_init.py`

### Design

Two defects share one root cause: the update listener performs
destructive registry work *before* knowing the reload will succeed.

- **HIGH-3 (10 s stall).** `_async_restore` is created with
  `entry.async_create_task`, which puts it in `entry._tasks`. Its first
  statement is `await hass.async_block_till_done()`, which waits for the
  reload task created on the next line. The reload's
  `_async_process_on_unload` does `asyncio.wait([*self._tasks, ...],
  timeout=10)` — and `self._tasks` contains the restore task. Mutual
  wait, broken only by the hard-coded timeout, after which HA logs
  `WARNING: Unloading ... Task ... did not complete in time`.
- **MEDIUM-8 (destructive on failure).**
  `_async_drop_stale_identity_registries` removes the old rows and the
  station device synchronously; if the reload then raises
  `ConfigEntryNotReady`, the entry sits in `SETUP_RETRY` with two
  sensors and a device already gone.

Per the user's constraint, **same station + same direction must keep the
entity name** — already true today, because
`config_flow.py:543` only reclaims a unique ID when it actually differs,
and an identical `async_update_entry` makes `requires_reload()` false.
Only a direction change is destructive, and the globals are unaffected
either way (their unique IDs and device identifier never match the
`f"{previous_uid}_"` prefix or `(DOMAIN, previous_uid)` pair that the
purge targets). This work must preserve that guarantee.

### Steps

1. Add a module-level pending-restore map in `__init__.py`, keyed by
   `entry_id`, holding `(previous_uid, captured_customisations)`.

2. In `_async_update_listener`, on the data/identity branch: capture
   customisations, stash them in the map, then
   `async_schedule_reload`. **Remove the `entry.async_create_task` +
   `async_block_till_done` handshake entirely** — that is the HIGH-3
   fix, and the map replaces it.

3. In `async_setup_entry`, after `async_forward_entry_setups`: if
   `entry.entry_id` is in the map, run
   `_async_drop_stale_identity_registries` then
   `_async_restore_identity_customisations`, and pop the entry. The
   operation becomes transactional — either the new identity comes up
   carrying the user's customisations, or the old one stays intact.

4. Leave the option-only branch untouched.

### Acceptance

- A direction reconfigure whose reload fails leaves the old entities and
  device intact and still restorable.
- A direction reconfigure that succeeds preserves name, icon, area and
  disabled state (existing tests must keep passing).
- No `WARNING: Task ... did not complete in time` in `caplog` for any
  reconfigure test.
- A same-direction reconfigure is a pure no-op.

---

## G3 — Stop suppressing the repair issue for filtered entries (HIGH-2a)

**Files:** `coordinator.py`
**Tests:** `test_coordinator.py`, `test_health_suppression.py`

`_async_update_empty_data_issue` (`coordinator.py:252-271`) clears and
suppresses the persistent-empty-data issue whenever the shared probe
succeeded. The probe queries a **different** station (`PEARS`) with
**no** filters, so "API answered" says nothing about whether *this*
entry's filter is satisfiable.

1. Only apply the health-probe suppression when the entry has **no**
   `direction` and **no** `stops_at` filter. A filtered entry that
   returns nothing for `EMPTY_DATA_ISSUE_THRESHOLD` polls during
   service hours is exactly the case the issue exists to catch.
2. Distinguish the two causes in the issue's translation placeholder so
   the user is told which one fired.

### Acceptance

- Unfiltered entry + healthy probe + empty → suppressed (existing
  behaviour preserved).
- `stops_at`-filtered entry + healthy probe + empty → issue raised.
- `direction`-filtered entry + healthy probe + empty → issue raised.

---

## G4 — Scope the `stops_at` options to reachable stops (HIGH-2b / R5)

**Files:** `config_flow.py`
**Tests:** `test_config_flow.py`

The options flow builds the dropdown from the **full station list**
(`config_flow.py:647`), so a user can select a station upstream of
their own. That value can never match, every train is pruned every
poll, and per G3 the diagnostic that would say so is suppressed.

1. Build the options from the learned matrix
   (`get_stops_store(hass).async_lookup(station_code, direction)`),
   where `direction` is read from `entry.data.get(CONF_DIRECTION)`.
2. If that is empty, fall back to the bundled seed via
   `async_load_bundled_stops_matrix()` + `lookup_in_matrix`.
3. If that is empty, fall back to live discovery via
   `async_get_station_stops_at_options`.
4. Only if all three are empty, fall back to the full station list — and
   say so in the field's `data_description` so the UI marks the
   degraded case.
5. Keep the existing stored-value merge (`build_stops_at_schema_field`
   already does this) so a no-op resubmit stays valid.

This aligns the code with the three-source fallback order the README
already claims.

### Acceptance

- The options dropdown offers only stations reachable downstream, or is
  explicitly labelled as a degraded full list.
- A stored `stops_at` value is always selectable.
- The free-text fallback is retained for the connection-error path and
  still preserves the stored value.

---

## G5 — Return observations instead of parking them on the client (R3)

**Files:** `client.py`, `coordinator.py`
**Tests:** `test_client.py`, `test_coordinator.py`

`IrishRailClient.last_downstream_stop_names` (`client.py:217`) is reset
by `_async_prune_trains` and read by the coordinator after its poll. The
same client instance is also handed to the health monitor and the
rebuild button. It is safe today only because neither of those passes a
`stops_at` filter — an invariant that is undocumented and unenforced.

1. Add an optional `observations: set[str] | None = None` out-parameter
   to `async_get_station_by_code` / `async_get_station_by_name`, filled
   by `_async_prune_trains` instead of writing the client attribute.
   This preserves the existing `list[TrainDueTime]` return type and the
   documented public client surface.
2. Delete `last_downstream_stop_names` and its reset.
3. In `_async_learn_downstream_stops`, pass a per-poll set and merge
   from it.

Result: `_async_prune_trains` becomes pure with respect to client state,
and no ordering assumption is needed.

### Acceptance

- `test_client.py` gains a case asserting the caller's set is populated
  and that two concurrent calls cannot cross-contaminate.
- No `last_downstream_stop_names` reference remains outside
  `docs/architecture.md`.

---

## G6 — Share the movement cache on the registry (R4 / MEDIUM-6)

**Files:** `client.py`, `_runtime.py`, `__init__.py`, `config_flow.py`

Each config entry builds its own `IrishRailClient`, so each holds its own
`_movement_cache` capped at 1024 `list[TrainMovement]` entries — tens of
MB per station on a constrained host, never released on unload.

1. Add `movement_cache: dict[tuple[str, str], list[TrainMovement]]` to
   `RuntimeRegistry`, so one cache is shared per HA instance. This also
   raises the hit rate, since stations share trains.
2. Add an optional `movement_cache` constructor parameter to
   `IrishRailClient`; when omitted it keeps its own private dict, so the
   module stays framework-agnostic and standalone-testable (no Home
   Assistant types in the signature).
3. Pass `registry.movement_cache` from `__init__.py` and from both
   config flows.
4. Clear it in `RuntimeRegistry.async_release` alongside the gate.

### Acceptance

- Two entries with the same train code produce **one** upstream request
  (pinned by a test using `aresponses`).
- Memory is released when the last entry unloads.

---

## G7 — Correct the six inaccurate `quality_scale.yaml` rows (MEDIUM-9)

**Files:** `quality_scale.yaml`
**Tests:** `test_quality_scale.py` (existing gate must stay green)

| Row | Correction |
|---|---|
| `entity_event_setup` | Currently claims all three platforms extend `CoordinatorEntity`. They do not — the binary sensor and button extend `BinarySensorEntity` / `ButtonEntity`. Rewrite the evidence to describe the actual, correct listener wiring. |
| `action_exceptions` | Currently `exempt` citing "mirrors the `action_setup` exemption", while `action_setup` is `done` and a service is registered. Self-contradictory. |
| `exception_translations` | Currently `exempt` citing "no service actions and no `HomeAssistantError`". Both false. |
| `docs_triggers` | Integration exposes no triggers. → `exempt`. |
| `docs_conditions` | A binary sensor is not a Quality Scale condition. → `exempt`. |
| `docs_actions` | Cites README "Examples", which documents automations, not the service. |

Make `exception_translations` `done` by fixing the code (G8), not by
editing the YAML.

The four existing exemptions (`reauthentication_flow`, `discovery`,
`discovery_update_info`, `docs_supported_devices`) are legitimate —
leave them.

### Acceptance

- `test_readme_sections_cited_by_docs_rules_exist` still passes for
  every remaining `docs_*` row.
- `test_every_exempt_rule_states_a_reason` still passes for the new
  `exempt` rows.

---

## G8 — Translated `HomeAssistantError` for the rebuild service (MEDIUM-10/11)

**Files:** `button.py`, `strings.json`, `translations/en.json`
**Tests:** `test_button.py`, `test_translations.py`

`async_press` raises a bare `RuntimeError` (`button.py:148`). The UI
path is guarded by `available`, but the service path
(`button.py:312`) calls `async_press()` directly and bypasses it.

1. Raise `HomeAssistantError` with a `translation_key` instead.
2. Add the key to both translation files (they must stay structurally
   aligned — `test_strings_and_translations_are_structurally_aligned`).
3. **Delete** `test_no_homeassistanterror_raised_to_users`
   (`test_translations.py:56`). It greps source text for the string
   `"HomeAssistantError"`, which is the "test pins prose" pattern the
   roadmap forbids — and it actively blocks this fix.
4. Replace it with a behavioural test: press the button during a running
   rebuild via the service, assert the raised exception is a
   `HomeAssistantError` whose `translation_key` resolves in both
   translation files.

### Acceptance

- The new test passes whether or not the code currently raises anything,
  and fails on regression.
- `test_no_homeassistanterror_raised_to_users` no longer exists.

---

## G9 — Guard the remaining MEDIUM correctness items

Each is small and independent; land them as separate checkboxes.

| ID | File | Change |
|---|---|---|
| G9a | `__init__.py` | `async_unload_entry`: guard the repair-issue delete **and** `async_note_entry_unloaded` on `if unloaded:`. Today a failed platform unload still drops the gate and stops the probe while the entry stays `LOADED`. |
| G9b | `coordinator.py` | Re-arm the timer after `async_set_configured_interval`. The base `update_interval` setter (HA `update_coordinator.py:246-250`) does not call `_schedule_refresh`, so the armed `loop.call_at` keeps the old interval. The docstring, CHANGELOG and README all claim "applies immediately". |
| G9c | `config_flow.py` | `async_step_reconfigure`: build `new_data` from `{**entry.data, ...}` instead of a three-key literal, so a future key is not silently dropped. |
| G9d | `matrix_rebuild.py` | Add a per-station `except Exception` that logs and continues, matching the pattern already used at `client.py:736` and `client.py:501`. One bad row currently discards ~154 sampled stations. |
| G9e | `client.py` / `coordinator.py` | Thread an explicit `date=` derived from `DUBLIN_TZ` through the pruning path. `async_get_train_stops` defaults to host-local date, so a non-Irish host between 00:00–05:00 Irish time queries yesterday's schedule and prunes every train. |
| G9f | `button.py` | Add `async_cancel()` to the button; stop reaching into `entity._rebuild_task` from the `_async_cleanup` closure. Use `entry.async_create_background_task` so the task lifetime is tied to the entry automatically. |
| G9g | `store.py` | Return the count of newly added stops from `async_record` instead of `bool`, so `RebuildResult.stops_added` stops over-reporting. |
| G9h | `coordinator.py` | Catch `BaseException` around the learn write, restore `_pending_stops`, re-raise. A `CancelledError` at the `await` currently loses the batch. |
| G9i | `diagnostics.py` | Add `CONF_STOPS_AT` to `_SENSITIVE_KEYS`. It holds a station name and is passed through in cleartext while `station` is masked. |

---

## G10 — LOW findings and repository hygiene

| ID | Change |
|---|---|
| G10a | Repoint the six stale docstring references to `health.py` / `gate.py` at `_runtime.py` and `request_gate.py`. Extend the CI grep to reject backticked `.py` names absent from the integration tree. |
| G10b | Delete the `roadmap item 4.4` breadcrumb at `client.py:73`. Widen the CI regex to `\broadmap\b` — the current `\d` requirement is why the line-wrapped form evades it. |
| G10c | Set `hacs.json` `homeassistant` floor to the tested `2026.8.2`, matching the README and the CI pin. |
| G10d | Remove the stray `-p/` directory at the repo root (mistyped shell redirect) and the committed `config/configuration.yaml` dev artefact. |
| G10e | Pin `home-assistant/actions/hassfest` and `hacs/action` to commit SHAs; pin `mypy` and the pytest family exactly; add a `pip` ecosystem to `dependabot.yml` (it currently watches only `github-actions`). |
| G10f | Add a release-workflow check that `manifest.json` `version` matches the top CHANGELOG entry and the git tag. |
| G10g | Bring `scripts/` under mypy and coverage; it is currently ruff-only. |
| G10h | Add `--cov-branch` to the pytest gate. 100% **line** coverage over `if/else` bodies gives false confidence — several findings above live in branches that execute but assert nothing. |
| G10i | Add `pytest-timeout` so a hung gate or sleep test cannot block CI indefinitely. Pin the RNG seed in `test_gate_stress_random_cancellations`. |
| G10j | Move the inline docstring-density gate out of `ci.yml` into `scripts/check_streamline_a4.py` alongside its sibling check; fix the message (says 0.20, checks 0.21). |

---

## G11 — Documentation and evidence pass

Required in the same change as the code they describe, per the roadmap
protocol.

- **`docs/architecture.md` §11** — rewrite "Global-entity
  providership" for the election model, the promotion rule, and the
  iff invariant. Add the service-lifetime change.
- **`docs/architecture.md` §8** — record the transactional reconfigure
  (pending-restore map) and that same station + same direction is a
  no-op by construction.
- **`docs/architecture.md` §9** — record that probe health no longer
  suppresses the empty-data issue for a filtered entry.
- **`README.md`** — "Irish Rail Services" section: state the iff
  invariant. "Configuration" table: correct the scan-interval
  immediacy claim. "Stops at" filter: correct the fallback order to
  match G4. Minimum HA version to 2026.8.2.
- **`quality_scale.yaml`** — re-point evidence for `entity_category`,
  `devices` and `stale_devices` at the new G1/G2 functions; apply the
  G7 corrections.
- **`CHANGELOG.md`** — v0.5.1 entry summarising the user-visible
  changes: globals survive sibling removal, reconfigure is
  transactional, `stops_at` options are scoped, and the repair issue
  now fires for filtered entries.

---

## Execution order

```
G0  record phase in roadmap
G1  provider election            (HIGH-1)
G2  transactional reconfigure    (HIGH-3, MEDIUM-8)
G3  repair-issue suppression     (HIGH-2a)
G4  stops_at option scoping      (HIGH-2b, R5)
G5  return observations          (R3)
G6  shared movement cache        (R4)
G7  quality_scale corrections    (MEDIUM-9)
G8  translated service error     (MEDIUM-10, MEDIUM-11)
G9  remaining MEDIUM items       (MEDIUM-1..7)
G10 LOW + hygiene
G11 docs + evidence
```

G1–G4 are the P0 set. G3 and G4 are independent of each other but both
belong with G1 in one release, since together they close HIGH-2.

G5 and G6 touch the client's public surface and are best landed as
their own increment so a regression is easy to bisect.

---

## Gates

Every increment must leave all three green before the next begins:

```bash
ruff check custom_components/irish_rail tests/components/irish_rail scripts
mypy custom_components/irish_rail tests/components/irish_rail
pytest tests/components/irish_rail tests/test_win_stubs.py \
  --cov=custom_components/irish_rail --cov-fail-under=100
```

The coverage gate does not drop. The XML guard, request gate and
polling cadence stay byte-for-byte identical — none of G1–G11 touches
them.

---

## Out of scope

- Changing any `unique_id` scheme. The roadmap's non-goals forbid it
  without sign-off, and the user's same-name requirement is satisfied
  by the current scheme.
- `entity_event_setup`'s underlying wiring. The row's *evidence* is
  wrong (G7); the wiring itself is correct.
- MEDIUM-3's larger variant — making entity IDs stable across a
  direction change by keying them on station code alone. That is a
  behaviour change needing its own roadmap decision, not a bug fix.
