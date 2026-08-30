# Skill 04 — DataUpdateCoordinator and Runtime Data

## Architecture

Use one `IrishRailDataUpdateCoordinator` per config entry.

All periodic API polling happens through the coordinator.

Entities MUST NOT call the Irish Rail API directly.

Home Assistant guidance:
https://developers.home-assistant.io/docs/integration_fetching_data/

## Coordinator

Use:

class IrishRailDataUpdateCoordinator(DataUpdateCoordinator[IrishRailData]):

Constructor should receive:
- `hass`
- the typed client
- any stable configuration needed for fetching

Set:
- integration logger
- `name=DOMAIN`
- appropriate `update_interval`

## Polling interval

Irish Rail is a realtime/polling service.

Pick an interval based on:
- upstream update cadence
- API load
- useful user-visible freshness

Do not poll faster than the data can meaningfully change.

A one-minute interval is a candidate, not a fact. Validate it.

Official rule:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/appropriate-polling/

## Update method

`_async_update_data` should:
1. call the client
2. return typed data on success
3. catch the integration-specific exceptions
4. raise `UpdateFailed` with a useful message

Do not leak raw aiohttp/parse exceptions from the coordinator.

## First refresh

During `async_setup_entry`:
- construct client
- construct coordinator
- perform `async_config_entry_first_refresh()`
- if the initial refresh fails, allow Home Assistant to treat the entry as not ready
- only forward platform setup after the initial refresh succeeds

This satisfies the spirit of test-before-setup and avoids creating entities with no initial data.

## Runtime data

Store runtime objects in:

`entry.runtime_data`

Do NOT use the old pattern:

`hass.data[DOMAIN][entry.entry_id]`

The Bronze runtime-data rule explicitly requires `ConfigEntry.runtime_data`.

A useful runtime container can be a dataclass, for example:

@dataclass
class IrishRailRuntimeData:
    client: IrishRailClient
    coordinator: IrishRailDataUpdateCoordinator

This gives strong typing and keeps the runtime contract explicit.

## Entity access

In `sensor.py`, retrieve the runtime object from:

`entry.runtime_data`

Do not recreate clients in platforms.

## Unload

Even if unload is Silver rather than Bronze, implement normal unloading correctly when the integration architecture supports it.

Use:
- `async_unload_entry`
- `hass.config_entries.async_unload_platforms`

Do not manually tear down Home Assistant's shared aiohttp session.

## Options-driven update interval (roadmap 1.2)

- Read the scan interval from `entry.options` (falling back to
  `DEFAULT_SCAN_INTERVAL`) when constructing the coordinator.
- Register an options update listener in `async_setup_entry`:

  entry.async_on_unload(entry.add_update_listener(_async_update_listener))

  The listener either reloads the entry or updates
  `coordinator.update_interval` in place. Prefer the in-place update only if
  it is demonstrably safe; otherwise reload.
- Keep the base interval in one place (`const.py` / coordinator) so the
  adaptive backoff logic below does not conflict with option changes.

## Adaptive backoff polling (roadmap 4.3)

- On consecutive failures, increase the effective interval exponentially,
  capped at ~15 minutes; restore the configured interval immediately on
  success.
- Implement by adjusting `update_interval` inside `_async_update_data`
  success/failure paths (or a small helper), tracking the failure streak on
  the coordinator instance.
- Tests must simulate failure streaks and verify both the backoff and the
  immediate restore on recovery.

## Transition logging (roadmap Phase 2 — `log-when-unavailable`)

- Log exactly once when the integration transitions to unavailable and once
  when it recovers — not on every failed poll.
- Verify Home Assistant's built-in coordinator behavior against the current
  rule text; if it already satisfies the rule, make that explicit with a
  test rather than adding duplicate logging.
- Do not log the same transient failure repeatedly at every polling cycle.

Official example:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/log-when-unavailable/

## Parallel updates (roadmap Phase 2 — `parallel-updates`)

- Declare a `PARALLEL_UPDATES` constant in `sensor.py`.
- Choose a small value appropriate to how many entities share one coordinator
  refresh; justify the choice in code review/PR description.
