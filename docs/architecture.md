# Irish Rail — Architecture Notes

> Long-form design notes for the `irish_rail` Home Assistant integration.
> Source-of-truth for non-obvious invariants; the code's docstrings
> stay short and contract-focused. New contributors should read this
> alongside `README.md` and the active execution plan in
> `.cline/streamline-roadmap.md`.

## Contents

1. [Module layout](#module-layout)
2. [Shared singletons](#shared-singletons)
3. [Request gate](#request-gate)
4. [XML safety policy](#xml-safety-policy)
5. [Stops-at matrix](#stops-at-matrix)
6. [Entity model](#entity-model)
7. [Global-entity providership](#global-entity-providership)
8. [Reconfigure identity preservation](#reconfigure-identity)
9. [Coordinator: adaptive backoff and empty-data](#coordinator-adaptive-backoff-and-empty-data)
10. [Stops-matrix store](#stops-matrix-store)
11. [Health monitor and global providership](#health-monitor-and-global-providership)
12. [Entry setup, update listener, and unload](#entry-setup-update-listener-and-unload)
13. [Config flow: user, reconfigure, options](#config-flow-user-reconfigure-options)
14. [Stops-matrix rebuild](#stops-matrix-rebuild)
15. [Sensor: timestamp device class and attribute model](#sensor-timestamp-device-class-and-attribute-model)
16. [Async-client placement: integration-internal modules](#async-client-placement-integration-internal-modules)

---

## 1. Module layout

```
custom_components/irish_rail/
├── __init__.py             # entry setup/unload, update listener
├── binary_sensor.py        # connectivity sensor (global)
├── button.py               # rebuild stops matrix button (global)
├── client.py               # IrishRailClient + parse helpers  (was pyirishrail/api.py)
├── config_flow.py          # user / reconfigure / options flows
├── const.py                # integration constants
├── coordinator.py          # DataUpdateCoordinator + backoff + empty-data issue
├── diagnostics.py          # redacted config-entry diagnostics
├── entity.py               # IrishRailEntity base
├── errors.py               # exception hierarchy  (was pyirishrail/errors.py)
├── icons.json
├── identity.py             # build_unique_id / normalized_direction
├── lib_const.py            # library-only constants  (was pyirishrail/_const.py)
├── manifest.json
├── matrix_rebuild.py       # in-process rebuild sweep  ← unify with scripts/build_stops_matrix.py
├── models.py               # frozen dataclasses  (was pyirishrail/models.py)
├── quality_scale.yaml
├── request_gate.py         # RequestGate primitive  (was pyirishrail/_gate.py)
├── _runtime.py             # RuntimeRegistry: gate + health singleton lifecycles  (was gate.py + health.py)
├── sensor.py               # per-station sensors
├── services.yaml
├── store.py                # stops-matrix store + bundled seed loader
├── strings.json
├── translations/en.json
├── types.py                # IrishRailRuntimeData + IrishRailConfigEntry alias
├── py.typed                # PEP 561 marker
└── stops_matrix.seed.json  # bundled seed snapshot
```

The async client lives in the integration package itself (no
`pyirishrail/` sub-package). It is **framework-agnostic** — the five
modules `client.py`, `request_gate.py`, `models.py`, `errors.py`,
`lib_const.py` carry zero Home Assistant imports, so they remain
unit-testable in isolation and a future contributor could split them
into a separate package if the integration ever grew non-HA consumers.

## 2. Shared singletons

Three long-lived objects live on `hass.data[DOMAIN]` and share a
single lifecycle: the set of loaded config entry ids.

| Key | Owner | Purpose |
|---|---|---|
| `loaded_entry_ids` | `_runtime.py` | Authoritative count of loaded entries; the gate, the movement cache and the health monitor are released when this set goes empty. |
| `request_gate` | `_runtime.py` (`RuntimeRegistry`) | One `RequestGate` shared by every `IrishRailClient` the integration constructs. |
| `movement_cache` | `_runtime.py` (`RuntimeRegistry`) | One `{(train_code, date): [TrainMovement]}` route cache shared by every client, so a train serving two stations is resolved once. |
| `api_health_monitor` | `_runtime.py` (`RuntimeRegistry`) | One `IrishRailApiHealthMonitor`; backs the connectivity binary sensor and classifies empty polls. |
| `stops_matrix_store` | `store.py` | One `StopsMatrixStore`; the gap-fill merge for live, config-flow, and rebuild writes. |
| `global_provider_entry_id` | `_runtime.py` | Which entry owns the global connectivity sensor and rebuild button. |
| `global_rebuild_entity` | `button.py` | Live handle to the rebuild button so the service alias can reach it. |
| `global_last_result` | `button.py` / `diagnostics.py` | Most recent `RebuildResult`, exposed in diagnostics. |

**Why a set, not a counter:** `ConfigEntryNotReady` retries re-run
`async_setup_entry`; an `int` counter double-counts and leaks a
running health probe on every failure.

**Single writer, structural not by-convention (B3, 2026-09-01):** all
of the lifecycle state above now lives on one
`_runtime.RuntimeRegistry` instance keyed on `hass.data[DOMAIN]`, and
every read and write goes through it. The old `gate.py` + `health.py`
split kept the two singletons' lifecycles coupled only by an ordering
convention in `__init__.py`; `_runtime.py` makes the coupling
structural — `RuntimeRegistry.async_release()` drops the shared gate
and stops the monitor as one operation when `loaded_entry_ids` empties.
Module-level functions (`async_get_request_gate`, `get_health_monitor`,
`async_note_entry_loaded`, …) are thin delegates over the registry, so
call sites did not need to change beyond their import path.

## 3. Request gate

`request_gate.RequestGate` is a concurrency-and-pacing primitive
that mediates every outbound HTTP call the integration makes against
the unauthenticated public `api.irishrail.ie` endpoints. It enforces
two coupled limits:

- `max_concurrent` — at most N requests in flight at any instant.
- `min_interval_seconds` — minimum spacing between two gate exits.

A single `asyncio.Lock` protects the shared state (`_waiters`,
`_in_flight`, `_next_exit`). Admission is a two-phase operation:

1. **Register then admit.** `acquire()` appends a `_Waiter` to
   `_waiters` and calls `_admit_eligible_waiters_locked()` under the
   lock, which iterates waiters in priority order and increments
   `_in_flight` for each one that fits in the budget. The admitted
   waiter's `event` is set.
2. **Wait once.** `acquire()` awaits exactly one event per caller. If
   the sweep above admitted the caller the event is already set and
   the wait returns immediately; otherwise a future release's sweep
   sets it. There is no retry loop, no second wait.

Exit-time reservations (`_next_exit`) are written from exactly two
places — the admission sweep (per admission) and `acquire()` itself
(per actual exit) — so a caller whose sleep returned late still
leaves the gate properly spaced from the next caller.

### Priority

`priority="background"` callers are admitted only when no `"normal"`
caller is queued. A background caller that has already crossed the
gate is allowed to finish its current call (priority governs admission
order, not preemption). The matrix-rebuild sweep uses `priority="background"`
so it never delays a live poll.

### Cancellation safety

A cancelled caller either gives back the slot it owns (releasing it
for the next eligible waiter) or removes itself from the queue before
it was admitted. The event being set is exactly the marker that the
sweep incremented `_in_flight` on the caller's behalf, so the cleanup
branch can be chosen without races.

## 4. XML safety policy

Two layers, by design (a third tree-walk layer was rejected after it
backfired on the real RTPI shape):

1. **Pre-parse byte-level substring guard** on the raw response body.
   Looks for any of the five XML 1.0 DTD/enabling keywords
   (`<!doctype`, `<!entity`, `<!element`, `<!attlist`, `<!notation`).
   Catches the billion-laughs bomb before the parser is invoked.
2. **Stdlib `ET.fromstring`** for well-formedness. Any `ET.ParseError`
   is wrapped as `IrishRailParseError`. Catches whitespace-obfuscated
   forms (`<! DOCTYPE` etc.) that the byte-level guard misses.

The third layer (post-parse tree-walk over `elem.text`/`elem.tail`/
`elem.attrib`) was removed because Irish Rail's serialiser emits
special characters in plain-text fields as `&lt;` rather than CDATA,
so the parsed tree contains the literal text `<!doctype` in element
text and the tree-walk rejected a perfectly valid response.

The byte-level guard has a known false-positive class: `<![CDATA[...<!doctype ...]]>`
sections in a station field trip the substring check because the
inert prose inside CDATA contains the literal text. Irish Rail's
RTPI responses use the standard entity-escaped form in plain-text
fields, not CDATA, so this case is not observed on the real API. The
regression test `test_cdata_doctype_substring_rejected` pins the
current reject-on-CDATA behaviour.

## 5. Stops-at matrix

The Irish Rail RTPI API exposes no static route directory. Three
derived layers bridge that gap so a station's "stops at" filter is
always available, even at quiet hours:

```
Live sampling (IrishRailClient.async_get_station_stops)
    ├── coordinator's _async_learn_downstream_stops on every poll
    └── config flow's _async_discover_directions / stops_at step
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
   Per-install cache         Bundled seed
   StopsMatrixStore          stops_matrix.seed.json
   .storage/irish_rail.      ships in the integration
     stops_matrix.json       directory; HACS refresh
   (HA storage; survives     replaces it on update
    HACS upgrades)
   Gap-fill merge on write   Read-only at runtime;
                             replaced by build script
                │                   │
                └─────────┬─────────┘
                          ▼
            config-flow stops_at dropdown
```

**One writer, one file.** Every observation (live coordinator,
config-flow live discovery, rebuild sweep) routes through
`StopsMatrixStore.async_record`, which gap-fill merges into the
per-install cache. The bundled seed is read-only at runtime and is
replaced wholesale by the offline `scripts/build_stops_matrix.py`
during release.

**No cross-direction reads.** Lookups for a specific direction never
fall back across directions; an option list can never contain a
station the selected direction does not serve.

## 6. Entity model

Each station/direction config entry creates one device with **two
sensors** — `sensor.<station>_<direction>_next_train_due` and
`..._following_train_due` — holding the next and the following service.
Each exposes the same fixed attribute surface:

| Attribute | Meaning |
|---|---|
| `expected_arrival_time` | The API's `HH:MM` expected arrival, verbatim. |
| `scheduled_arrival_time` | The API's `HH:MM` scheduled arrival, verbatim. |
| `direction` | Reported direction string. |
| `train_code` | Irish Rail train identifier. |
| `api_reachable` | Always `true` when readable; absence means the coordinator marked the sensor `unavailable`. |

`next_train_due` additionally carries the live countdown pair
`expected_arrival` (ISO 8601 mirror of its own state) and
`time_until_arrival` (whole seconds until arrival, recomputed on every
read so it is genuinely live while the state is frozen at the last poll
instant). **Both appear together or not at all**: the two are derived
from the same optional arrival, so a template reading one never has to
guard on the other. A service the API reports with neither `Duein` nor
`HH:MM` resolves to no arrival and therefore carries neither key — the
other four attributes keep the card readable and the absence is the
signal that no arrival time is known.

The sensor state itself is a `datetime` under the `TIMESTAMP` device
class, resolved from the signed `due_in_mins` offset rather than from
`HH:MM` (see §15).

**Why one sensor, not three:** the previous design had three
near-identical sensors (`next_train_due`, `next_train_destination`,
`next_train_delay`) and 18 attributes on the primary. Three sensors
duplicated state for no dashboard benefit, and the duplicate
`due_in_mins` + `late_mins` attributes were redundant with
`extra_state_attributes`. One sensor with rich attributes is the
idiomatic HA pattern (see the `weather` integration) and lets
templates read a single attribute key for any arrival detail.

The integration also exposes two **service** entities on a fixed
"Irish Rail Services" device — see §11 for the invariant governing when
they exist:

- `binary_sensor.irish_rail_api_connectivity`
  (`EntityCategory.DIAGNOSTIC`, `BinarySensorDeviceClass.CONNECTIVITY`)
- `button.irish_rail_rebuild_stops_matrix`
  (`EntityCategory.CONFIG`)

## 7. Global-entity providership

Superseded by §11, which records the election model. The short form:
providership is *derived* from the loaded-entry set, not cached at first
claim, and the globals exist if and only if at least one station entry
is loaded.

## 8. Reconfigure identity preservation

Since HA 2026.6 an integration with an update listener must own
reload scheduling itself — a flow-scheduled reload alongside the
listener can double-reload or race. The pattern:

1. Config-entry `data` changes (station/direction identity) trigger
   `entry.async_update_reload_and_abort()` in the reconfigure flow;
   the update listener schedules exactly one reload afterwards.
2. Option-only changes are applied in place
   (`coordinator.async_set_configured_interval(...)`, which re-arms the
   armed timer — see §9) without a reload.
3. The reconfigure flow **merges** rather than replaces:
   `new_data` is built from `{**entry.data, ...}` with only the three
   identity keys overwritten, so a key added to the entry later is
   carried across the reconfigure instead of silently dropped.

**The reconfigure is transactional.** The old identity's registry rows
are *not* removed when the reload is scheduled, because nothing at that
point knows whether the reload will succeed. Instead:

- The update listener **captures** the previous identity's
  customisations (entity id, name, icon, disabled-by) into a
  module-level pending-restore map keyed by entry id, and only then
  schedules the reload.
- `async_setup_entry` consumes that capture at its very end, after the
  platforms have built their entities: the previous rows are dropped and
  the new ones written back with the user's customisations. Setup
  reaching that line is itself proof the new identity took — the entity
  base class rejects an entry with no unique ID — so the restore target
  is never missing.
- A reload that fails leaves the old entities and device exactly as
  they were, still restorable on the next attempt.
- `ConfigEntryChange.REMOVED` pops any unconsumed capture, so removing a
  sibling mid-flight cannot leak it for the session.

The map lives in `__init__.py`, not on `RuntimeRegistry`, because
`async_release` runs during the *reload's own* unload and would clear
the capture at the exact moment it must survive.

**Same station + same direction is a no-op by construction.** The
reconfigure flow compares the normalized direction against the current
value and aborts early without touching `entry.data`, so the identity
cannot change and the transactional machinery never runs. Only a
direction change is destructive, and the two global entities plus the
services device are outside its blast radius: their unique IDs and
device identifier match neither the `f"{previous_uid}_"` prefix nor
`(DOMAIN, previous_uid)`.

The coordinator snapshots the entry data it was built from
(`_applied_entry_data`) and exposes:

- `requires_reload()` — compares the snapshot to the current
  `entry.data`; the update listener uses this to distinguish
  data/identity changes (reload) from option-only changes
  (apply in place).
- `applied_unique_id()` — derived from the snapshot, identifies the
  identity currently on the registries even after
  `config_entry.unique_id` has been rewritten, and is what the capture
  step targets.

---

## 9. Coordinator: adaptive backoff and empty-data

### Adaptive backoff polling

On consecutive failed refreshes the effective polling interval
grows geometrically from the user-configured value, capped at
`MAX_BACKOFF_INTERVAL` (15 minutes); a successful refresh restores
the configured interval immediately.

- `update_interval` is a property; the getter derives the effective
  backed-off interval from `_configured_interval` and the
  `_failure_streak`, the setter updates `_configured_interval`
  and then delegates to the base class so HA's
  `_update_interval_seconds` cache stays in sync.
- **An interval change re-arms the armed timer.** HA 2026.8 exposes no
  public re-arm; assigning the property only mirrors the seconds cache,
  so the already-scheduled `loop.call_at` keeps the old spacing until
  something else reschedules. `_async_apply_effective_interval` uses
  the base class' own `_unschedule_refresh` / `_schedule_refresh` pair —
  the same two calls its add/remove-listener paths make. This is what
  makes "applies immediately" true for an options change.
- **…but only while something is listening.** The base class
  unschedules when its last listener is removed, precisely so nothing
  keeps polling an entry nobody is watching. The backoff path must not
  undo that, so `_async_apply_effective_interval` still records
  `update_interval` but skips the reschedule pair with no listeners; a
  coordinator that gains one again re-arms from the recorded value.
- `_schedule_refresh()` mirrors the property into
  `_update_interval_seconds` before calling the base method,
  because HA 2026.8+ schedules from the cached value, not by
  re-reading the property at schedule time. With no failure
  streak the cache equals the configured interval (no
  behavioural change); while backing off it widens the spacing
  and recovery narrows it at the next schedule point.
- The base method's `_retry_after` handling still wins.

### Persistent-empty-data repair issue

A station that keeps returning an empty list during service
hours suggests the API or its schema changed rather than a
genuine quiet period. The issue is created exactly once per
streak (`EMPTY_DATA_ISSUE_THRESHOLD`, default 10 consecutive
empty polls) and removed on the first refresh that returns
actual trains, on entry unload, and re-raised only after a
fresh streak.

- Service hours are 06:00–23:00 Europe/Dublin (`DUBLIN_TZ`).
  Empty polls outside that window reset the streak, so an
  overnight accumulation cannot pre-seed the next morning's
  count.
- When the shared API-health monitor has recently confirmed the
  API is healthy, an empty result is classified as "no
  scheduled services within the look-ahead window": no issue is
  raised, any already-open one is cleared immediately, and the
  streak resets.
- **…but only for an entry that filters nothing.** The probe queries a
  different station with no filters, so "the API answered" says nothing
  about whether *this* entry's filter is satisfiable. `is_unfiltered`
  reports exactly the two filters the poll actually sends — the
  direction snapshotted at coordinator construction and
  `resolve_stops_at(entry)`, read live — so it can never disagree with
  the query it qualifies. A filtered entry that comes back empty for
  the whole threshold during service hours is precisely the case the
  issue exists to catch, and suppressing it is what made an impossible
  `stops_at` value permanently silent.
- The registry is authoritative: a coordinator reconstructed
  after a reload may see `_empty_issue_reported = False` while
  the issue is still registered, so the clear path checks the
  registry directly.

### Downstream-stops learning

While a "stops at" filter is active, pruning already fetches
each candidate's movement history. The **caller** owns the
observation set: the poll passes an empty `set[str]` down to
`async_get_station_by_code`, which clears and fills it with the
journey-scoped downstream stops it resolved, and hands it to
`_async_learn_downstream_stops` to merge into `StopsMatrixStore` on
every successful poll. The client keeps no copy of its own — two
entries sharing a client must not observe each other's stations.
This keeps the config flow's option list current without any
additional requests.

Two guards on the write path:

- `async_record` returns the count of **newly added** stops, so a poll
  that re-observes what the matrix already holds flushes its batch
  (otherwise the same stops would be rewritten every poll forever) but
  reports no progress. The comparison is **case-insensitive** — the same
  convention `lookup_in_matrix` reads back with — so a stop re-reported
  as `BRAY` against a stored `Bray` is one stop, not a new one, and the
  first-seen casing survives.
- A `CancelledError` at the storage `await` restores the batch to
  `_pending_stops` and re-raises. The batch was already taken out of
  the pending set, so without this an unload or shutdown mid-write
  would lose the poll's observations for good.

Persistence failures are logged and never fail the poll: the matrix is
an optimization over live sampling, not a data source of record.

---

## 10. Stops-matrix store

### Layered truth

The RTPI API exposes no static route directory. Three derived
layers bridge that gap so a station's "stops at" filter is
always available, even at quiet hours:

- **Live sampling** (the coordinator's
  `_async_learn_downstream_stops` and the config flow's
  discovery) is the authoritative source.
- **Per-install cache** (`StopsMatrixStore` in `.storage/`) is
  the gap-fill merger: every successful live observation is
  unioned in, never removed. HACS updates do not touch it
  because it lives outside the integration folder.
- **Bundled seed** (`stops_matrix.seed.json` shipped inside the
  integration directory) bootstraps fresh installs at quiet
  hours so setup does not degrade to the full national station
  list. HACS updates refresh it from upstream.

### Lookups never fall back across directions

A specific direction's bucket is read on its own. There is no
fallback to `_all` (the directionless union). The
`ALL_DIRECTIONS_KEY` bucket exists only so directionless
entries ("All") still get persistence.

### Lock and gap-fill

`StopsMatrixStore.async_record` is serialized on an
`asyncio.Lock` so two concurrent writers see each other's
unions before persisting. It returns the number of **newly
added** stops — `0` for a no-op write, so a caller can tell
"nothing new" from "everything new" without diffing the matrix
itself. That is what `RebuildResult.stops_added` reports, instead of
a bool that counted re-observed stops as progress.

### Bundled seed double-checked load

`async_load_bundled_stops_matrix` populates a process-wide
cache (`_SEED_CACHE`) under a lazily-created `asyncio.Lock` so
two coroutines arriving while the cache is empty do not both
read the file: the second waits for the first to publish and
returns the same object. The double-checked
`if _SEED_CACHE is None` inside the critical section handles
the case where the second arrival acquires the lock after the
first has already populated the cache.

A missing, unreadable, or malformed seed must never block
configuration; the failure is logged once and an empty matrix
is cached so subsequent lookups skip straight to the live
sampling path.

The runtime rebuild button calls `reset_bundled_seed_cache`
after it finishes, so the next config-flow lookup reflects
improvements the rebuild made to the per-install matrix
instead of being masked by a stale pre-rebuild seed.

---

## 11. Health monitor and global providership

### Probe

A single lightweight station poll (the integration uses
`HEALTH_PROBE_STATION_CODE = "PEARS"`) stands in for
reachability: it exercises the same HTTP/XML path as station
polling without the full ~155-record station-list payload
every interval. A successful response — even one with no
trains currently due — is treated as healthy; the probe never
depends on a service actually being scheduled.

The monitor runs as a periodic task scheduled at
`HEALTH_CHECK_INTERVAL` (5 minutes) plus one immediate
`async_ping()` at startup. The `schedule_ping` method
coalesces overlapping pings: if a probe is in flight when the
next tick fires, the new ping is suppressed (the in-flight
one already covers it).

`healthy: bool | None` starts as `None` ("not yet probed") so
consumers conservatively fall back to the legacy behaviour
until the very first probe has landed. The binary-sensor
entity's `available` property returns `False` while
`healthy is None`, which HA renders as the "unavailable" grey
badge — the correct visual cue during the first five-minute
window after startup, rather than the "Off" state with the
disconnect icon that `is_on = None` would otherwise produce.

### Singleton lifecycle

A set of loaded config-entry ids under `hass.data[DOMAIN]`
(`LOADED_ENTRY_IDS_KEY`) is the single source of truth for
the shared singletons' lifetime. The API-health monitor runs
while the set is non-empty and stops when the last entry
deregisters; the shared request gate is released at the same
moment by `__init__.py`.

A set, not a counter, keeps setup idempotent: an automatic
retry after `ConfigEntryNotReady` re-runs `async_setup_entry`
and re-adds the same id without double counting, so a failed
first refresh can never leave phantom counts (and a running
probe) behind.

### Global-entity providership

**The invariant:** the connectivity binary sensor, the stops-matrix
rebuild button, their shared "Irish Rail Services" device and the
`rebuild_stops_matrix` service exist **if and only if at least one
station config entry is loaded**.

Providership is therefore a value *derived* from
`registry.loaded_entry_ids`, not a fact cached at first claim. That
distinction is the whole fix: the previous check asked
`hass.config_entries.async_entries(DOMAIN)` whether the recorded owner
was still *installed*, which ignores load state, so removing the owner
while a sibling stayed loaded orphaned the globals for the rest of the
session. A sticky election is unsound; a recomputed one is
self-healing.

- `elect_provider(hass, entry)` runs from `async_setup_entry` and
  grants the key only to an entry that is in the loaded set. A second
  loaded entry is a no-op, so the globals exist exactly once.
- `async_promote_provider` re-elects onto a survivor — the lowest
  loaded entry id, so the same survivor wins regardless of unload
  order — and re-adds the two entities to that entry's *already
  running* platforms. Reloading the survivor instead would make its
  own sensors blink out and back on an entry that did nothing wrong.
- An unload is the first half of a reload, so a departing owner is not
  promoted from the unload path unconditionally. `async_note_entry_unloaded`
  clears `GLOBAL_REBUILD_ENTITY_KEY` either way (the handle would point
  at an entity going away with its owner's platform), then branches:
  * the entry can still come back (`_entry_will_reload`: present in the
    store and not `disabled_by`) — the id is parked in
    `pending_promotions`;
  * it cannot — a **disabled** entry, which HA unloads and never sets
    up again without ever dispatching `ConfigEntryChange.REMOVED`, or
    one already gone from the store. Waiting on the removal signal here
    would strand the globals unowned for the rest of the session, so a
    survivor is elected immediately (as a task, so the departing entry
    finishes its own teardown first).
- `async_promote_on_removal` is driven by `ConfigEntryChange.REMOVED`,
  which HA dispatches only once the entry has left the store, so it
  never races a reload. It is a no-op unless the removed entry was the
  owner waiting for a survivor, and it declines if the key was
  re-claimed in the meantime.
- Ownership transfers mid-session, so the **service lifetime is now
  session-scoped, not owner-scoped**: the `rebuild_stops_matrix`
  alias is (re-)registered on every promotion, and the rebuild task
  is deliberately owned by `hass.async_create_background_task` rather
  than by the owning entry, so a promotion that happens mid-rebuild
  cannot cancel a sweep that is still useful.

On every claim change the departing owner's orphan entity-registry
rows for the two global unique IDs are wiped, along with the matching
"Irish Rail Services" device row, so the new owner's
`async_add_entities` does not collide and the user does not see a
stray "entity not available" badge. The membership check is the same
as for entity rows: only items whose `config_entry_id` references the
departed owner are removed, so a live co-owned device is left alone.
`async_get_device(identifiers=...)` returns at most one device
(identifiers are unique per device), so a direct `async_remove_device`
replaces the old `for dev in registry.devices.values()` iteration:
equivalent semantics on the single device that can match the
integration's `GLOBAL_SERVICES_IDENTIFIER`, and one call to a public
API instead of iterating a private mapping that the modern registry no
longer exposes.

---

## 12. Entry setup, update listener, and unload

### First refresh

`async_setup_entry` runs
`coordinator.async_config_entry_first_refresh()` before
forwarding to platforms. A failed first refresh triggers
`ConfigEntryNotReady`, satisfying the runtime half of
`test-before-setup` and preventing half-configured entities.

### Update listener (HA 2026.6+)

Since HA 2026.6 an integration with an update listener must
own reload scheduling itself — a flow-scheduled reload
alongside the listener can double-reload or race. The pattern:

- The reconfigure flow uses
  `entry.async_update_reload_and_abort()` to update the entry
  data; the update listener, not the flow, schedules the
  reload that follows.
- The update listener compares the entry data snapshot taken
  at coordinator construction (`_applied_entry_data`) to the
  current `entry.data` via `coordinator.requires_reload()`:
  - **Data/identity change** (station or direction): capture the
    previous identity's customisations into the pending-restore
    map (§8) and schedule one reload. Nothing is removed here, so
    a reload that fails leaves the old entities intact.
  - **Option-only change**: apply the new interval in place via
    `async_set_configured_interval`; no reload.

`resolve_scan_interval()` defends against invalid or
non-numeric stored option values, falling back to the default
instead of raising.

### Unload

`async_unload_entry` deletes any pending empty-data repair
issue for the entry, unloads platforms, and deregisters the
entry from the loaded-entry set. If the deregistration was
the last, the shared request gate, the shared movement-history
cache and the session-scoped keys are released at the same
moment so a subsequent load starts clean. Releasing only here
keeps the one-gate-per-HA contract intact while sibling entries
stay loaded — releasing on every unload would strand those
siblings on a dropped gate while new clients built a second
one, splitting the shared rate budget.

The issue delete and the shared-state release are **deliberately
unconditional**, not guarded on the platform unload succeeding. HA
marks a failed platform unload `FAILED_UNLOAD`, which is
non-recoverable: the entry is never set up again, so leaving the gate
and the probe running would leak them for the rest of the session.
Verified against `config_entries.py` in HA 2026.8.

---

## 13. Config flow: user, reconfigure, options

### User flow

Step one narrows the station list with an optional free-text
filter using the same word-prefix semantics as irishrail.ie's
own search (verified against `getStationsFilterXML`):
case-insensitively, every whitespace-separated term must be
a prefix of some whitespace-delimited word of the station
name or alias. Blank text matches everything so the full
list stays browsable; there is deliberately no fuzziness.

A single match skips straight ahead, otherwise a pick screen
lists the candidates. The final step offers only the
direction values that are actually valid *for the chosen
station*, discovered live from its due-trains list:
`Northbound` / `Southbound` on the Dundalk-Rosslare and
Sligo-Dublin corridors, free-text values such as `To Cork`
elsewhere. When nothing is currently due (overnight) the
field degrades to free text so setup never blocks.

### Stops-at step

The `stops_at` step (when requested) offers only stops a train from
*this* station and *this* direction can actually reach. Sources, in
order of freshness:

1. The per-install learned matrix, for this station **and** direction.
2. The bundled seed, likewise scoped.
3. A live sample of the station's current services in that direction.
4. Only when all three come up empty, the full national station list —
   labelled as such, because a station chosen from it may never be
   reached. Its `data_description` says so in the form.

The stored value is merged back into the options whether or not it
appears in the reachable set, so a no-op resubmit stays valid and a
filter the user already has is never silently dropped. A `stops_at`
filter can never silently match nothing.

The options flow uses the same four-source order. It previously built
the list from *every* station unconditionally, so a user could pick a
stop upstream of their own; the filter then pruned every train on every
poll and, until the repair-issue scoping fix, the one diagnostic that
would have said so was suppressed.

### Reconfigure flow

The station is fixed; only the direction filter is editable.
The relevant options are discovered live for that one
station. On success the entry data (and identity) are
updated in place — merged from `{**entry.data, ...}` — and the
integration's update listener schedules the single required
reload, transactionally (§8). A resubmit of the *same* direction is
a no-op: nothing is written, nothing is reloaded.

When rebuilding the form, the stored value is merged back
into the options so resubmitting the current setting always
validates, even when that direction has no trains within
the look-ahead window.

### Options flow

The `init` step offers `scan_interval` (30s–10min, default
60s) and `stops_at`. The `"All"` sentinel and blank values are
normalized to `None` when storing, so the `stops_at` key explicitly
means "no filter" rather than "absent option". A value seeded into
`entry.data` by the initial config flow cannot resurface afterwards:
whatever was saved here last always wins.

---

## 14. Stops-matrix rebuild

The runtime rebuild button and the offline
`scripts/build_stops_matrix.py` share a single
:func:`custom_components.irish_rail.matrix_rebuild.sample_stops_matrix`
loop. The two callers select between output modes via flags:

| Knob | Button wrapper (`async_run_matrix_rebuild`) | Script (`scripts/build_stops_matrix.py`) |
|---|---|---|
| `gap_fill` | `True` (union into the running `StopsMatrixStore`) | `False` (wholesale-replace via the script's caller-managed dump) |
| `atomic_dump` | `False` (the store owns persistence) | `True` (atomic temp-file + `os.replace`) |
| `priority` | `"background"` (yields to live polling) | `"normal"` (one-shot CLI) |
| `hass` | required (the store lives on `hass.data[DOMAIN]`) | not used |
| `output_path` | not used | required |
| `limit` | not used | CLI flag (smoke testing) |

Both callers share the same underlying invariants: per-station
movement cache, journey scoping via
`IrishRailClient.scope_journey_stops`, polite
`REBUILD_DELAY_SECONDS` between stations, the per-station
``IrishRailError`` skip + warning, and the per-bucket persistence
guard.

The button rejects a press while a rebuild is already
running (no queue). Progress and outcome — stations
sampled, stops added, duration, errors — appear in the
button's `extra_state_attributes`.

---

## 15. Sensor: timestamp device class and attribute model

### Timestamp class

The primary per-station sensor uses HA's `TIMESTAMP` device
class. The Irish Rail API exposes the wall-clock
`expected_arrival_time` (`HH:MM`) **and** a signed
`due_in_mins` offset measured from the API's server clock.
The offset is the canonical source of truth for the *date
direction*:

- A future service (positive offset) lands in the future,
  regardless of whether its `HH:MM` is before or after the
  current wall-clock time — a 00:30 service polled at 23:55
  correctly resolves to the next day 00:30 rather than being
  misread as today 00:30 in the past.
- An overdue service (negative offset) lands in the past,
  which HA's "Time" card renders as a relative "X min ago".
  A 23:55 service observed at 00:05 yields a 23:55
  timestamp on the previous calendar day, and the UI shows
  it as "departed 10 min ago".

`expected_arrival_time` (`HH:MM`) is retained as a defensive
fallback: if the API omits `due_in_mins` but still reports
an arrival time, the parser falls back to the `HH:MM` +
today date combination, so a partial API payload still
produces a timestamp rather than `None`. Returns `None`
when both fields are blank or unparseable, so the sensor
state can fall back to `None` rather than publish a bogus
datetime.

### Parallel updates

`PARALLEL_UPDATES = 0` is the explicit declaration per the
Silver `parallel-updates` rule for coordinator-based
read-only platforms: every entity shares a single
`DataUpdateCoordinator` refresh, so per-entity updates are
pure in-memory property reads with no outbound calls; 0
declares that no artificial serialization limit is needed.

---

## 16. Async-client placement: integration-internal modules

The async client is a set of framework-agnostic modules
(`client.py`, `request_gate.py`, `models.py`, `errors.py`,
`lib_const.py`) sitting next to the Home Assistant integration
code under `custom_components/irish_rail/`. They do **not**
import Home Assistant, so they remain unit-testable in
isolation and a future contributor could split them into a
separate package if the integration ever grew non-HA consumers.

The PEP 561 `py.typed` marker ships at the integration root.
Strict mypy passes clean on the bundled surface via
`mypy custom_components/irish_rail tests/components/irish_rail`.

The PyPI name `pyirishrail` is owned by an unrelated project;
the streamline roadmap does not plan to re-publish the client.
Anyone re-adding a PyPI requirement to `manifest.json` would
re-introduce the v0.3.0 baseline's reverted decision.

