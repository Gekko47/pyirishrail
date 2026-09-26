# Home Assistant Integration Rules — `irish_rail`

Always-applied rules for this integration's Home Assistant surface. These hold
whether or not a skill is loaded. If a rule here conflicts with a skill, the
rule wins.

## Verify documentation live, never from memory

The Home Assistant Quality Scale rules change between releases. **Never answer
a Quality Scale, manifest, config-flow, or entity-naming question from memory
or from a copied historical checklist.** Fetch the live pages:

- https://developers.home-assistant.io/docs/core/integration-quality-scale/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/
- https://developers.home-assistant.io/docs/creating_integration_file_structure/
- https://developers.home-assistant.io/docs/integration_fetching_data/

If live documentation contradicts any skill, rule, or old prompt, the
documentation wins — and record the discrepancy in the active roadmap.

## Never block the event loop

No blocking I/O anywhere in an async path. No `requests`, no
`time.sleep`, no synchronous file or network calls inside coroutines. Do not
use `asyncio.to_thread` as a way to keep a legacy blocking client.

## Use the injected shared session

The HTTP client receives Home Assistant's shared `aiohttp` session via
`async_get_clientsession(hass)`. Never instantiate a session per request, and
never manually tear down the shared session. Every request carries an explicit
`aiohttp.ClientTimeout` and validates HTTP status.

## The XML guard is load-bearing

The pre-parse DTD/entity guard in the client is a live-API behaviour guarantee.
The streamline roadmap's non-goals state that XML guard, request gate, and
polling cadence stay **byte-for-byte identical**. Do not weaken, reorder, or
remove the guard as part of a refactor.

Treat XML as hostile input. stdlib `xml.etree.ElementTree` only — no
`xml.dom.minidom`, no entity expansion exposure.

## One coordinator per config entry

All periodic polling goes through `IrishRailDataUpdateCoordinator`.
**Entities must never call the Irish Rail API directly.** One
`DataUpdateCoordinator` per config entry, not per platform and not global.

## Runtime objects live in `entry.runtime_data`

Client, coordinator, and other runtime objects belong in
`ConfigEntry.runtime_data` — **not** `hass.data[DOMAIN][entry_id]`.

For integration-wide singletons (`hass.data[DOMAIN]`), all reads and writes go
through `RuntimeRegistry` in `_runtime.py`. Its keys are private to that
module; no other module reaches into `hass.data[DOMAIN]` directly. The registry
is the only place that creates the request gate and health monitor, and the
only place that releases them.

## First refresh before forwarding platforms

`async_setup_entry` must construct client and coordinator, call
`async_config_entry_first_refresh()`, and only forward platform setup after it
succeeds. A failed first refresh means Home Assistant treats the entry as not
ready — never a half-configured integration.

## Entity naming and identity

- `_attr_has_entity_name = True` on every entity.
- User-facing strings via `_attr_translation_key` + `strings.json` +
  `translations/en.json`. No hard-coded English in entity names, config-flow
  labels, or raised `HomeAssistantError`s.
- `ConfigEntry.data` holds connection-defining values; `ConfigEntry.options`
  holds optional user-adjustable settings. Do not mix them.
- The config flow must validate against the live service **before** creating
  the entry.

## Unique IDs are permanent

`unique_id` values are derived from stable upstream identifiers (the station
code, a fixed slot suffix) — never from station display name, destination
text, translated strings, or mutable labels.

**Changing an existing `unique_id` orphans user customisations and is forbidden
by the roadmap's non-goals.** If a refactor would move or rename a
`unique_id`, that is a behaviour change and needs its own committed step with
roadmap sign-off.

## Config flow covers every branch

Every new config-flow step (including `reconfigure` and options flows) needs
full branch coverage in `test_config_flow.py`: happy path, each error branch,
each abort path, and recovery so the user can retry. `data_description` is
required by the `config-flow` Bronze rule.

No network exception may escape the flow — map the client's typed exceptions
to translation keys (`cannot_connect`, `invalid_station`, `unknown`).

## Empty is not unavailable

Distinguish "API reachable, zero trains scheduled" (expected, e.g. overnight)
from "API unreachable" (coordinator `UpdateFailed` → entities unavailable).
Never return an empty list merely because the server failed, and never
conflate an empty result with a failure in the entity layer.

## Entity behaviour

- Prefer `CoordinatorEntity`. Never subscribe to events in `__init__`; if a
  manual listener is genuinely needed, subscribe in `async_added_to_hass` and
  register cleanup with `async_on_remove`.
- Do not recreate clients in platforms — read `entry.runtime_data`.
- Assign device class / state class / native unit only when semantically
  correct. Do not force `measurement` onto non-measurements.
- Defensive reads: when fewer trains exist than expected, report `None` or
  become unavailable. Never crash.
- Do not add another per-station sensor. New arrival detail goes on the
  existing sensor's `extra_state_attributes` and requires a `strings.json`
  translation key, a `quality_scale.yaml` evidence note, and a test pinning
  the new key.
