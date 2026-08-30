# Skill 03 — Config Flow

## Goal

The integration must be fully configurable from the Home Assistant UI.

Official guidance:
https://developers.home-assistant.io/docs/core/integration/config_flow/

## Handler

Use:

class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN)

Set:
- `VERSION = 1`

Use a typed, modern Home Assistant config flow.

## User experience

The flow should allow the user to:
1. search/select a station, or enter the supported station identifier
2. validate the station against the live Irish Rail service
3. reject invalid/unreachable selections cleanly
4. prevent duplicate configuration
5. create the config entry only after successful validation

## Validation

The flow must test the connection/service before creating the entry.

Catch the client exception hierarchy and map failures to translation keys such as:
- `cannot_connect`
- `invalid_station`
- `unknown`

Do not let network exceptions escape the flow.

## Unique ID

Use a stable station code or other stable API identifier.

Pattern:

await self.async_set_unique_id(station_code)
self._abort_if_unique_id_configured()

Do not use:
- station display name
- translated station name
- arbitrary user input
- mutable API labels

## ConfigEntry data/options

Follow the current Bronze rule:
- connection-defining data goes in `ConfigEntry.data`
- optional user-adjustable settings go in `ConfigEntry.options`

Do not create unnecessary options if the integration has none.

## strings.json

Provide:
- step title/description
- field labels
- `data_description`
- errors
- abort reasons
- flow title if needed

`data_description` is explicitly part of the current config-flow Bronze rule.

## Testing requirements

Tests must cover every branch of the flow:
- happy path
- connection failure
- invalid station
- unexpected failure
- duplicate configuration
- any abort path
- recovery from an error so the user can continue

The official rule explicitly requires full config-flow coverage and specifically calls out duplicate-entry coverage.

See:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage/

## Reconfigure flow (roadmap 1.1 — Gold `reconfiguration-flow`)

Add `async_step_reconfigure` to `IrishRailConfigFlow`:

- The station code is fixed; only the direction filter is editable.
- Use `self._get_reconfigure_entry()` to obtain the entry being reconfigured.
- On successful submit, update entry data with
  `self.async_update_reload_and_abort(entry, data=...)`. Only deviate from a
  reload if updating the coordinator direction in place is demonstrably safe.
- Preserve the existing unique ID; do not run duplicate abort against other
  entries for the same station.
- Map client exceptions to the same translation keys as the user step
  (`cannot_connect`, `invalid_station`, `unknown`). No network exception may
  escape the flow.

Strings: add a `reconfigure` step to `strings.json` and
`translations/en.json` (labels, `data_description`, errors, aborts).

Tests must cover every branch: happy path, connection failure, invalid
station, unexpected failure, abort on update failure, and recovery so the
user can retry. Full branch coverage of the new step is required.

## Options flow for scan interval (roadmap 1.2)

Add an `OptionsFlow`:

- Single `init` step with a `scan_interval` field bounded by
  `vol.All(vol.Coerce(int), vol.Range(min=30, max=600))` (30 seconds to
  10 minutes), defaulting to `DEFAULT_SCAN_INTERVAL` (60s) from `const.py`.
- Store the value in `entry.options`; never mix it into `entry.data`
  (connection-defining data stays in data).
- Register an update listener in `async_setup_entry`:
  `entry.async_on_unload(entry.add_update_listener(_async_update_listener))`.
  The listener either reloads the entry (simplest correct approach) or
  updates `coordinator.update_interval` in place; see Skill 04.
- Strings: `init` step in strings/translations with `data_description`.

Tests: valid value stored and honored by the coordinator; out-of-range or
non-numeric values rejected by the schema (form re-shown with error); the
update listener applies the change.

## Anti-patterns

Do not:
- write YAML configuration for a modern service integration
- create the entry before validation
- use station name as unique ID
- hard-code user-facing English strings
- bypass translation infrastructure
- catch all errors and pretend setup succeeded
- let reconfigure/options flows bypass the typed exception mapping used by
  the user step
