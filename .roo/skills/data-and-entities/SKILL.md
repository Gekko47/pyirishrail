---
name: data-and-entities
description: The runtime data path for the irish_rail integration - the async Irish Rail client, config flow, DataUpdateCoordinator, and entity/translation surface. Load when touching client.py, models.py, errors.py, request_gate.py, config_flow.py, coordinator.py, __init__.py, entity.py, sensor.py, binary_sensor.py, button.py, strings.json, translations, icons.json, or when adding/changing a config flow step, polling behaviour, runtime object, entity, unique_id, device class, or user-facing string.
---

# Data Path: Client → Config Flow → Coordinator → Entities

How data flows through the integration and the constraints at each layer.
The long-form invariants live in `docs/architecture.md`; the source carries the
contract, not the narrative.

Layer order:

```
Home Assistant
  -> config flow
  -> client creation
  -> coordinator
  -> entities
```

The client knows about **none** of the layers above it.

---

## 1. Async client architecture

### Core rule

**Never perform blocking network I/O in the Home Assistant event loop.**

For HTTP:
- use async HTTP
- use Home Assistant's shared client session
- inject the session into the client
- use explicit request timeouts
- translate low-level failures into integration-specific typed exceptions

Official guidance:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/

### The client is framework-agnostic

`client.py` must NOT import:
- `homeassistant.*`
- `hass`
- Home Assistant registries
- Home Assistant entity classes

It SHOULD contain:
- typed dataclasses
- endpoint construction
- async HTTP
- XML parsing
- exception hierarchy
- normalization/validation of upstream values

The client must be independently unit-testable: its tests must not require a
running Home Assistant instance.

The framework-agnostic modules are `client.py`, `request_gate.py`,
`models.py`, `errors.py`, `lib_const.py`. `request_gate.py` stays its own file
because the `RequestGate` primitive is framework-agnostic; it is *not* a
singleton (the per-HA instance lives on the runtime registry).

### Exception hierarchy

```
IrishRailError
├── IrishRailConnectionError
├── IrishRailTimeoutError
├── IrishRailHTTPError
├── IrishRailParseError
└── IrishRailInvalidResponseError
```

Use exceptions to distinguish failure modes. **Never return an empty list
merely because the server failed** — that conflates "no trains scheduled" with
"API down", which is exactly the distinction the entity surface must preserve.

### HTTP behaviour

Use an injected `aiohttp.ClientSession` obtained via
`async_get_clientsession(hass)`.

Every request should:
- use `await session.get(...)`
- have an explicit `aiohttp.ClientTimeout`
- validate HTTP status
- read/parse the response safely
- convert aiohttp exceptions to typed integration exceptions

Do not:
- instantiate a session per request
- use `requests`
- use `asyncio.to_thread` as a way to keep a legacy blocking client
- hide connection failures

### XML security

Treat XML as hostile/untrusted input.

**Active policy: stdlib `xml.etree.ElementTree` with an explicit pre-parse
DTD/entity guard.** No `defusedxml` dependency.

Do not use:
- `xml.dom.minidom`
- standard XML parsing patterns that leave the integration exposed to entity
  expansion / XXE concerns

The pre-parse guard is load-bearing: the streamline roadmap's non-goals state
live-API behaviour stays byte-for-byte identical, so the guard must not be
weakened or reordered during refactors.

Test:
- valid XML
- malformed XML
- missing expected elements
- unexpected/malformed values

When refactoring the XML layer, normalize namespaces once at parse time;
remove dual namespace-or-not `findall` fallbacks.

### Typed data model

Prefer immutable or straightforward dataclasses. Concepts in play: `Station`,
`TrainDueTime`, `TrainMovement`, `TrainStop`, plus an aggregate coordinator
data model.

Do not leak raw XML nodes or untyped dictionaries into Home Assistant
entities. Use optional fields where upstream data can be absent.

### Parsing rules

Parsing should:
- normalize whitespace
- handle absent elements
- validate types
- preserve upstream meaning
- avoid guessing

For time handling, determine whether a field represents scheduled time,
expected time, actual time, or due minutes. **Do not convert values to
timestamps until the semantic meaning is established.**

Conditional requests: if implementing `ETag` / `Last-Modified` support,
probe the API first, send validators, skip parsing on HTTP 304, and record the
evidence. **Never treat a 304 as an error.**

---

## 2. Config flow

### Goal and handler

Fully configurable from the Home Assistant UI.

```python
class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1
```

Official guidance:
https://developers.home-assistant.io/docs/core/integration/config_flow/

### User experience

The flow allows the user to:
1. search/select a station, or enter the supported station identifier
2. validate the station against the live Irish Rail service
3. reject invalid/unreachable selections cleanly
4. prevent duplicate configuration
5. create the config entry only after successful validation

### Validation

The flow must test the connection/service **before** creating the entry.

Catch the client exception hierarchy and map failures to translation keys such
as `cannot_connect`, `invalid_station`, `unknown`. No network exception may
escape the flow.

### Unique ID

Use a stable station code or other stable API identifier:

```python
await self.async_set_unique_id(station_code)
self._abort_if_unique_id_configured()
```

Do not use: station display name, translated station name, arbitrary user
input, mutable API labels.

### ConfigEntry data vs options

- connection-defining data goes in `ConfigEntry.data`
- optional user-adjustable settings go in `ConfigEntry.options`

Do not create unnecessary options if the integration has none.

### strings.json

Provide: step title/description, field labels, `data_description`, errors,
abort reasons, flow title if needed. `data_description` is explicitly part of
the config-flow Bronze rule.

Keep `strings.json` and `translations/en.json` structurally aligned with
current Home Assistant conventions.

### Reconfigure flow (Gold `reconfiguration-flow`)

- The station code is fixed; only the direction filter is editable.
- Use `self._get_reconfigure_entry()` to obtain the entry being reconfigured.
- On successful submit, update entry data with
  `self.async_update_reload_and_abort(entry, data=...)`. Only deviate from a
  reload if updating the coordinator direction in place is demonstrably safe.
- Preserve the existing unique ID; do **not** run duplicate abort against
  other entries for the same station.
- Map client exceptions to the same translation keys as the user step
  (`cannot_connect`, `invalid_station`, `unknown`).
- Add a `reconfigure` step to `strings.json` and `translations/en.json`.

### Options flow (scan interval)

- Single `init` step with a `scan_interval` field bounded by
  `vol.All(vol.Coerce(int), vol.Range(min=30, max=600))` (30s–10min),
  defaulting to `DEFAULT_SCAN_INTERVAL` (60s) from `const.py`.
- Store the value in `entry.options`; never mix it into `entry.data`.
- Register an update listener in `async_setup_entry`:
  `entry.async_on_unload(entry.add_update_listener(_async_update_listener))`.
  The listener either reloads the entry (simplest correct approach) or
  updates `coordinator.update_interval` in place.
- Add the `init` step to strings/translations with `data_description`.

### Config-flow anti-patterns

Do not:
- write YAML configuration for a modern service integration
- create the entry before validation
- use station name as unique ID
- hard-code user-facing English strings
- bypass translation infrastructure
- catch all errors and pretend setup succeeded
- let reconfigure/options flows bypass the typed exception mapping used by
  the user step

---

## 3. Coordinator and runtime data

### Architecture

One `IrishRailDataUpdateCoordinator` per config entry. All periodic API
polling happens through the coordinator. **Entities MUST NOT call the Irish
Rail API directly.**

```python
class IrishRailDataUpdateCoordinator(DataUpdateCoordinator[IrishRailData]):
```

Constructor receives: `hass`, the typed client, any stable configuration
needed for fetching. Set the integration logger, `name=DOMAIN`, and an
appropriate `update_interval`.

Common modules belong in `coordinator.py` and `entity.py`:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules/

### Polling interval

Pick an interval based on upstream update cadence, API load, and useful
user-visible freshness. Do not poll faster than the data can meaningfully
change. A one-minute interval is a candidate, not a fact — validate it.

Official rule:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling/

### Update method

`_async_update_data` should:
1. call the client
2. return typed data on success
3. catch the integration-specific exceptions
4. raise `UpdateFailed` with a useful message

Do not leak raw aiohttp/parse exceptions from the coordinator.

### First refresh

During `async_setup_entry`:
- construct client
- construct coordinator
- perform `async_config_entry_first_refresh()`
- if the initial refresh fails, allow Home Assistant to treat the entry as not
  ready
- only forward platform setup after the initial refresh succeeds

This satisfies the spirit of `test-before-setup` and avoids creating entities
with no initial data.

### Runtime data

Store runtime objects in `entry.runtime_data`. Do **not** use the old
`hass.data[DOMAIN][entry.entry_id]` pattern — the Bronze `runtime-data` rule
explicitly requires `ConfigEntry.runtime_data`.

```python
@dataclass
class IrishRailRuntimeData:
    client: IrishRailClient
    coordinator: IrishRailDataUpdateCoordinator
```

Platforms retrieve the runtime object from `entry.runtime_data`. Do not
recreate clients in platforms.

### Unload

Implement normal unloading correctly:

- `async_unload_entry`
- `hass.config_entries.async_unload_platforms`

Do not manually tear down Home Assistant's shared aiohttp session.

### Options-driven update interval

- Read the scan interval from `entry.options` (falling back to
  `DEFAULT_SCAN_INTERVAL`) when constructing the coordinator.
- Register the options update listener in `async_setup_entry`; prefer the
  in-place update only if demonstrably safe, otherwise reload.
- Keep the base interval in one place (`const.py` / coordinator) so the
  adaptive backoff logic does not conflict with option changes.

### Adaptive backoff polling

- On consecutive failures, increase the effective interval exponentially,
  capped at ~15 minutes; restore the configured interval immediately on
  success.
- Implement by adjusting `update_interval` in the coordinator's
  success/failure paths, tracking the failure streak on the coordinator
  instance.
- Tests must simulate failure streaks **and** recovery.

### Transition logging (`log-when-unavailable`)

- Log exactly once when the integration transitions to unavailable and once
  when it recovers — not on every failed poll.
- Verify Home Assistant's built-in coordinator behaviour against the current
  rule text; if it already satisfies the rule, make that explicit with a test
  rather than adding duplicate logging.

Official example:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable/

### Parallel updates (`parallel-updates`)

Declare a `PARALLEL_UPDATES` constant in the sensor platform. Choose a small
value appropriate to how many entities share one coordinator refresh; justify
the choice in code review / PR description.

---

## 4. Entities, naming, translations

### Base entity

Create a shared `IrishRailEntity(CoordinatorEntity[IrishRailDataUpdateCoordinator])`
with:
- `_attr_has_entity_name = True`
- stable device/entity metadata where appropriate
- coordinator-based updates

Common patterns belong in `entity.py`.

### Entity naming

Use `_attr_translation_key`, `strings.json`, and `translations/en.json`. Avoid
hard-coded user-facing English entity names.

Translation keys should exist for config-flow labels, descriptions, errors,
and entity names.

### Unique IDs

Every entity needs a stable unique ID.

Good: `f"{entry.unique_id}_next_due"`

Bad: station display name, destination text, translated strings, current train
number if the train changes, or anything that shifts meaning between refreshes
beyond a fixed slot.

The unique ID must remain stable across restarts and data updates. Changing
one orphans user customisations and is forbidden by the roadmap's non-goals.

### Coordinator wiring

Prefer `CoordinatorEntity`. Do not manually add listeners unless necessary. If
you do:
- subscribe in `async_added_to_hass`
- register cleanup with `async_on_remove`
- **never** subscribe during `__init__`

### Sensor design

Expose only useful, well-defined entities. Do not create sensors for every raw
XML field.

Use the most appropriate sensor metadata: native unit, device class, state
class. Only assign a device class/state class when semantically correct. Do
not force `measurement` onto values that are not measurements.

### Device metadata

Service-style device with manufacturer `Iarnród Éireann / Irish Rail`. Note
that `devices` is a **Gold** rule, not Bronze — do not create a fake physical
device just to satisfy a checklist.

### Branding

Current Bronze requires branding assets in `brand/`. Validate them against
current Home Assistant branding instructions. Do not invent logos or use
copyrighted assets without permission.

### Consolidated sensor surface

The design exposes a **rich per-station sensor** whose `extra_state_attributes`
carry the arrival context. The pre-streamline design had three
near-identical sensors; the streamline work collapses them.

- Do **not** add another per-station sensor. New arrival detail goes on the
  existing sensor's `extra_state_attributes`, and every new key needs a
  `strings.json` translation entry, a `quality_scale.yaml` evidence note, and
  a test that pins the new key.
- Do **not** add a per-train list entity. The `upcoming_trains[]` array on the
  primary sensor's attributes is the contract for "show me the next N trains".
- See `docs/architecture.md` §6 for the rationale (one rich sensor beats three
  thin sensors; HA dashboards consume attributes as easily as separate
  entities).

### Defensive reads

When fewer trains exist than expected, report `None` state or become
unavailable — **never crash**. Use fixed-slot unique IDs
(`f"{entry.unique_id}_train2_due"`), never IDs whose meaning shifts between
refreshes.

Every new entity needs a translation key in `strings.json` /
`translations/en.json` plus tests, and a README Entities entry.

### Empty-vs-error semantics

Distinguish "API reachable, zero trains scheduled" (expected, e.g. overnight)
from "API unreachable" (coordinator failure → unavailable). Implement an
explicit attribute (e.g. `api_reachable: true/false`) or availability logic
driven by coordinator success. **Never conflate an empty list with a failure.**

### Icon translations

`icons.json` at the integration root with per-entity icons keyed by translation
key / entity component. Keep icons aligned with entity translation keys;
validate against current Home Assistant icon-translations guidance.

### Exception translations

Convert user-facing error messages to translation-keyed
`HomeAssistantError`s. No hard-coded English in raised errors.

### Repair issues

Raise a repair issue (`ir.async_create_issue`, translation-keyed) when a
station returns persistently empty data during service hours (possible
API/schema change). Ensure the issue is created **once**, not per poll.

### Device-class review

Re-confirm device classes / state classes remain semantically correct as new
entities are added.

Official integration file structure:
https://developers.home-assistant.io/docs/creating_integration_file_structure/
