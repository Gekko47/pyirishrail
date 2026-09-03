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
| `loaded_entry_ids` | `_runtime.py` | Authoritative count of loaded entries; the gate and the health monitor are released when this set goes empty. |
| `request_gate` | `_runtime.py` (`RuntimeRegistry`) | One `RequestGate` shared by every `IrishRailClient` the integration constructs. |
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

Each station/direction config entry creates one device with **one
sensor** (`sensor.<station>_<direction>_next_train_due`) that exposes
the full arrival context as `extra_state_attributes`:

| Attribute | Meaning |
|---|---|
| `expected_arrival` | Full datetime of expected arrival (HA `TIMESTAMP` device class). |
| `destination` | Final destination of the next train. |
| `origin` | Origin station. |
| `due_in_mins` | Signed minutes until departure (negative = departed). |
| `late_mins` | Minutes late. |
| `train_type` | Train category (DART, Commuter, InterCity, …). |
| `train_code` | Irish Rail train identifier. |
| `direction` | Reported direction string. |
| `upcoming_trains[]` | Next N trains (1–5, default 3). |
| `api_reachable` | Always `true` when readable; absence means the coordinator marked the sensor `unavailable`. |

**Why one sensor, not three:** the previous design had three
near-identical sensors (`next_train_due`, `next_train_destination`,
`next_train_delay`) and 18 attributes on the primary. Three sensors
duplicated state for no dashboard benefit, and the duplicate
`due_in_mins` + `late_mins` attributes were redundant with
`extra_state_attributes`. One sensor with rich attributes is the
idiomatic HA pattern (see the `weather` integration) and lets
templates read a single attribute key for any arrival detail.

The integration also exposes two **service** entities on a fixed
"Irish Rail Services" device:

- `binary_sensor.irish_rail_api_connectivity`
  (`EntityCategory.DIAGNOSTIC`, `BinarySensorDeviceClass.CONNECTIVITY`)
- `button.irish_rail_rebuild_stops_matrix`
  (`EntityCategory.CONFIG`)

## 7. Global-entity providership

The two service entities exist exactly once per Home Assistant
session regardless of how many station config entries are installed.
The first entry to set up "claims" providership; subsequent entries
become no-ops for the service entities.

When the owning entry is removed, its orphan entity-registry rows for
the two global unique IDs are wiped (along with the matching
"Irish Rail Services" device row) so the new claiming entry's
`async_add_entities` does not collide and the user does not see a
stray "entity not available" badge.

## 8. Reconfigure identity preservation

Since HA 2026.6 an integration with an update listener must own
reload scheduling itself — a flow-scheduled reload alongside the
listener can double-reload or race. The pattern:

1. Config-entry `data` changes (station/direction identity) trigger
   `entry.async_update_reload_and_abort()` in the reconfigure flow;
   the update listener schedules exactly one reload afterwards.
2. Option-only changes are applied in place
   (`coordinator.update_interval = resolve_scan_interval(entry)`)
   without a reload.
3. Before the scheduled reload, the previous identity's
   entities/device are positively removed from the entity/device
   registries (matching on the previous unique ID) so the new
   direction's entities are the only ones registered.

The coordinator snapshots the entry data it was built from
(`_applied_entry_data`) and exposes:

- `requires_reload()` — compares the snapshot to the current
  `entry.data`; the update listener uses this to distinguish
  data/identity changes (reload) from option-only changes
  (apply in place).
- `previous_unique_id()` — derived from the snapshot, identifies
  the pre-reconfigure identity even after
  `config_entry.unique_id` has been rewritten. The reconfigure
  flow uses this to target the registry cleanup at exactly the
  old station/direction entities.

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
- The registry is authoritative: a coordinator reconstructed
  after a reload may see `_empty_issue_reported = False` while
  the issue is still registered, so the clear path checks the
  registry directly.

### Downstream-stops learning

While a "stops at" filter is active, pruning already fetches
each candidate's movement history. The client records the
journey-scoped downstream stops it saw in
`client.last_downstream_stop_names` at zero extra API cost;
the coordinator's `_async_learn_downstream_stops` merges them
into `StopsMatrixStore` on every successful poll. This keeps
the config flow's option list current without any additional
requests. Persistence failures are logged and never fail the
poll: the matrix is an optimization over live sampling, not a
data source of record.

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
unions before persisting. A no-op write returns `False`
without writing; a successful merge returns `True`.

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

The connectivity binary sensor and the stops-matrix rebuild
button exist exactly once per Home Assistant session
regardless of how many station config entries are installed.
The first entry to set up claims providership; subsequent
entries become no-ops for these two entities.

When the previous owner disappears entirely, its orphan
entity-registry rows for the two global unique IDs are wiped
before the new claim is granted, along with the matching
"Irish Rail Services" device row. The membership check is the
same as for entity rows: only items whose `config_entry_id`
references the dead owner are removed, so a live co-owned
device is left alone. The new `async_get_device(identifiers=...)`
returns at most one device (identifiers are unique per
device), so a direct `async_remove_device` replaces the old
`for dev in registry.devices.values()` iteration: equivalent
semantics on the single device that can match the
integration's `GLOBAL_SERVICES_IDENTIFIER`, and one call to a
public API instead of iterating a private mapping that the
modern registry no longer exposes.

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
  - **Data/identity change** (station or direction): schedule
    one reload. Before scheduling, positively remove the
    previous identity's entity and device rows from the
    registries so post-reload setup registers only the new
    direction's entities.
  - **Option-only change**: apply the new
    `coordinator.update_interval` in place; no reload.

`resolve_scan_interval()` defends against invalid or
non-numeric stored option values, falling back to the default
instead of raising.

### Unload

`async_unload_entry` deletes any pending empty-data repair
issue for the entry, unloads platforms, and deregisters the
entry from the loaded-entry set. If the deregistration was
the last, the shared request gate is released at the same
moment so a subsequent load gets a fresh gate. Releasing
only here keeps the one-gate-per-HA contract intact while
sibling entries stay loaded — releasing on every unload
would strand those siblings on a dropped gate while new
clients built a second one, splitting the shared rate
budget.

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

The `stops_at` step (when requested) narrows on the selected
direction's services. Options come from services currently
due; when none can be sampled, the per-install cache and the
bundled seed are used instead. Only as a last resort is the
full national station list offered. A `stops_at` filter can
never silently match nothing.

### Reconfigure flow

The station is fixed; only the direction filter is editable.
The relevant options are discovered live for that one
station. On success the entry data (and identity) are
updated in place and the integration's update listener
schedules the single required reload.

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

