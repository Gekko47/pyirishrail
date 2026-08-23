# Irish Rail Home Assistant Integration

[![CI](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml)
[![HACS Validate](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml)
[![Release](https://img.shields.io/github/v/release/Gekko47/pyirishrail)](https://github.com/Gekko47/pyirishrail/releases)
[![License](https://img.shields.io/github/license/Gekko47/pyirishrail)](LICENSE.txt)

A modern Home Assistant custom integration for the Irish Rail Realtime Passenger Information (RTPI) service. It satisfies every Bronze and Silver rule of the [Home Assistant Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/), and every Gold-tier rule is assessed and satisfied (done or justified exempt) — see [quality_scale.yaml](custom_components/irish_rail/quality_scale.yaml) for per-rule evidence.

## Description
This integration allows you to monitor upcoming trains at any Irish Rail station directly from Home Assistant. It provides real-time data on due times, destinations, delays, and more, using the unofficial Irish Rail RTPI API.

- **Domain**: `irish_rail`
- **Integration type**: Service
- **IoT class**: Cloud polling
- **Quality scale**: Gold
- **Minimum HA version**: 2026.8.2
- **API key required**: No

## Installation

### Via HACS (Recommended)
1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Go to **HACS** -> **Integrations**.
3. Click the three dots in the top right corner and select **Custom repositories**.
4. Paste the URL of this repository: `https://github.com/Gekko47/pyirishrail`.
5. Select **Integration** as the category and click **Add**.
6. Find **Irish Rail** in the list and click **Download**.
7. Restart Home Assistant.

### Manual Installation
1. Download the latest release.
2. Copy the `custom_components/irish_rail` directory into your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

## Configuration
1. In Home Assistant, go to **Settings** -> **Devices & Services**.
2. Click **Add Integration**.
3. Search for **Irish Rail** and select it.
4. Choose the station you wish to monitor from the dropdown list (the station
   list is fetched live from the API before you can submit — the connection is
   validated up front).
5. Optionally, select a direction filter (**All**, **Northbound**, or **Southbound**).
6. Optionally, set how many upcoming trains (1–5, default 3) are exposed in
   each sensor's `upcoming_trains` attribute.
7. Click **Submit**.

*Note: No API key is required to use this service.*

### Changing settings later
- **Direction filter**: use the three-dot menu on the config entry →
  **Reconfigure**. The station cannot be changed; only the direction filter
  can be updated in place. Changing the direction changes the entry's unique
  ID, so reconfiguring is rejected if another entry already monitors that
  station/direction combination.
- **Scan interval, number of trains & "stops at" filter**: use the three-dot
  menu → **Configure** (options). All three apply without reloading the entry:
  - **Scan interval**: polling interval between 30 seconds and 10 minutes
    (default 60 seconds).
  - **Number of upcoming trains**: between 1 and 5 (default 3).
  - **Stops at filter**: optionally show only trains that stop at a selected
    station (chosen from a dropdown of all stations). Selecting **All**
    disables the filter. When active, each candidate train's route history is
    fetched from the API to check whether it calls at the chosen station.

## Entities
The integration creates the following sensors for each configured station,
grouped under a device named after the station (and direction filter):

- **Next train due**: Minutes until the next train arrives (`DURATION`
  device class, minutes, measurement state class — supports long-term
  statistics).
- **Next train destination**: The destination of the upcoming train.
- **Next train delay**: Delay in minutes (if any) (`DURATION` device class,
  minutes, measurement state class).
- **Next train type**: The type of service (e.g., DART, Suburban, Intercity).

Each station/direction config entry also appears in the device registry as a
service-type device attributed to *Iarnród Éireann / Irish Rail*, with stable
unique IDs derived from the station code, direction, and entity key.

Each sensor includes additional attributes:

| Attribute | Description |
|---|---|
| `upcoming_trains` | A list of the next N trains (configurable 1–5, default 3), each with due-in minutes, destination, delay, service type, train code, and scheduled origin/destination times. If fewer trains are scheduled than requested, the list simply comes back shorter. |
| `api_reachable` | Always `true` when readable — see Conditions below for how this distinguishes "no trains scheduled" from "API unreachable". |
| `origin` | Origin station of the next train. |
| `origin_time` / `destination_time` | Scheduled departure/arrival times for the next train's full journey. |
| `expected_arrival_time` / `expected_departure_time` | Real-time expected times at the monitored station. |
| `scheduled_arrival_time` / `scheduled_departure_time` | Timetabled times at the monitored station. |
| `direction` | Direction of travel of the next train. |
| `train_code` | Irish Rail train identifier of the next train. |

Each sensor ships with a domain-appropriate default icon (train, map marker, delay clock, ticket) defined in the integration's `icons.json` ([icon translations](https://developers.home-assistant.io/docs/core/entity/#icons)). As with any entity, you can override the icon per entity from the UI without touching the integration.

## Use cases
Typical ways to put the integration to work:

- **Commuter dashboard**: build a dashboard card for your home station around *Next train due* and *Next train destination*, and render the `upcoming_trains` attribute through a Markdown card (or auto-entities) for a live departure board.
- **Delay alerts**: watch *Next train delay* during your usual commute window and get notified as soon as disruption starts, so you can catch an earlier service or switch route.
- **Presence-based departure reminders**: combine the sensors with person tracking — when you enter a zone near the office/station and the next suitable train is due within a few minutes, remind yourself to head to the platform immediately.
- **Service-health awareness**: dashboards can tell "no trains scheduled" (sensor *available*, reporting `unknown`, `api_reachable: true`) apart from "API unreachable" (sensor `unavailable`), so a quiet late-evening timetable never looks like an outage.

## Example automations
### Departure alert
Notify when the next train is due within 10 minutes on weekdays:

```yaml
- alias: "Irish Rail - time to leave"
  mode: single
  triggers:
    - trigger: numeric_state
      entity_id: sensor.dublin_pearse_northbound_next_train_due
      below: 10
  conditions:
    - condition: time
      weekday: [mon, tue, wed, thu, fri]
  actions:
    - action: notify.mobile_app_phone
      data:
        title: "Train arriving soon"
        message: >-
          The {{ state_attr('sensor.dublin_pearse_northbound_next_train_due',
          'direction') }} service departs in about
          {{ states('sensor.dublin_pearse_northbound_next_train_due') }}
          minutes.
        data:
          tag: irish-rail-departure
```

### Delay notification
Notify when the monitored service is more than 5 minutes late:

```yaml
- alias: "Irish Rail - delay warning"
  mode: single
  triggers:
    - trigger: numeric_state
      entity_id: sensor.dublin_pearse_southbound_next_train_delay
      above: 5
  actions:
    - action: notify.mobile_app_phone
      data:
        title: "Train delayed"
        message: >-
          Service {{ state_attr('sensor.dublin_pearse_southbound_next_train_delay',
          'train_code') }} towards
          {{ state_attr('sensor.dublin_pearse_southbound_next_train_delay',
          'direction') }} is running
          {{ states('sensor.dublin_pearse_southbound_next_train_delay') }}
          minutes late.
        data:
          tag: irish-rail-delay
```

Replace the entity IDs with those of your own station/direction entries.

## Actions
This integration is read-only and exposes no service actions. The user-facing actions are limited to configuration management:

- **Add a station**: via **Settings → Devices & Services → Add Integration → Irish Rail**. Selecting a station (and optionally a direction filter) creates a config entry and four sensors for that station. Adding the same station with the same direction filter again is prevented (duplicate protection); the same station with a *different* direction filter creates an additional, independent entry.
- **Remove an entry**: deleting a config entry (see Removal below) unloads its coordinator and removes all four sensors belonging to that station/direction. No manual cleanup is required — no files or persistent state are written outside Home Assistant's own config-entry storage.
- **Reload an entry**: entries fully support unloading and reloading at runtime without restarting Home Assistant. Reloading (three-dot menu → Reload) re-runs setup, performing a fresh first refresh against the API and restoring the same sensors under the same entity IDs. If the API is unreachable at that moment, the entry enters a retry state and the sensors remain unavailable until it succeeds.

## Triggers (data updates)
The integration polls the Irish Rail RTPI API periodically; there are no user-configurable triggers:

- **On startup / entry load**: when Home Assistant starts or the config entry is loaded/reloaded, an immediate first refresh is performed. If it fails, setup is retried with backoff (`ConfigEntryNotReady`).
- **Periodic polling**: after the first refresh, a `DataUpdateCoordinator` fetches fresh due-train data every **60 seconds** by default (`DEFAULT_SCAN_INTERVAL`), matching the roughly once-a-minute cadence at which Irish Rail's real-time feed changes, while keeping load on the public API sustainable. The interval is user-configurable between 30 seconds and 10 minutes via the entry options (see Configuration above) and applies without a reload.
- **On coordinator failure**: if a poll fails (connection error, timeout, or malformed response), the coordinator keeps the last known data, logs the failure once per outage (not per failed poll), and retries on the next cycle; sensors become **Unavailable** immediately after the failed refresh and recover automatically on the next successful poll.

## Conditions (normal vs. degraded operation)
- **Normal**: the coordinator returns a list of due trains; sensors report live values (minutes due, destination, delay, train type) plus schedule attributes.
- **No trains due**: the API may legitimately return an empty list (e.g. late at night). Sensors then report `unknown`/`None` rather than an error — this is an expected condition, not a failure. The entity remains *available* and its attributes (including `api_reachable: true`) stay readable, so automations can distinguish "nothing scheduled" from "cannot reach the API".
- **API unreachable / timeout / malformed response**: the poll raises `UpdateFailed`. Entities become **Unavailable**, the failure is logged once per occurrence, and polling continues automatically. No user intervention is needed; entities recover on their own when the API responds again. An unavailable entity is the explicit signal that the API could not be reached; an available entity reporting `unknown` means the API responded but no trains are scheduled.
- **First-refresh failure at setup**: if the API is unreachable when the entry is first set up (or reloaded), the entry enters `SETUP_RETRY` and Home Assistant retries with exponential backoff until the API is reachable.

## Diagnostics
The integration supports Home Assistant diagnostics downloads: from the
three-dot menu on a config entry, choose **Download diagnostics**. The report
contains the (redacted) entry data/options plus coordinator health information
(update interval, last-update success flag, number of due trains). Station
names and codes are partially masked (short prefix + hash suffix) so reports
remain useful for debugging without exposing identifying details.

## Troubleshooting
- **Sensors show `unavailable`**: the most recent API poll failed (service downtime, timeout, or malformed response). One error per outage is written to **Settings → System → Logs**. No intervention is needed — polling continues and the sensors recover automatically on the first successful poll.
- **Entry stuck in retry after startup/reload**: if the API was unreachable during setup, the entry enters `SETUP_RETRY` and Home Assistant keeps retrying with backoff. Setup completes on its own once the API answers; use the three-dot menu → **Reload** to force an immediate attempt.
- **`unknown` states late at night**: outside operating hours Irish Rail legitimately returns no trains. The sensors remain *available* with `api_reachable: true` — this is a quiet timetable, not a fault.
- **"No train data received" repair issue**: when a station keeps returning an empty response during service hours (roughly 06:00–23:00), the integration raises a repair issue under **Settings → System → Repairs**, since a persistent empty result may indicate an API or schedule-data change. Check whether other stations still report data, reload the entry, and — if it persists — remove and re-add it or update the integration. The issue clears itself on the first refresh that returns real trains.
- **Reporting a bug**: include the entry diagnostics download (see above); station identifiers are already masked in it.

## Underlying API client
The bundled `IrishRailClient` wraps the public RTPI XML endpoints over HTTPS
with safe XML parsing ([defusedxml](https://pypi.org/project/defusedxml/)) and
a 10-second timeout per request. It exposes methods that may be useful for
custom automations/scripts built on top of the library:

- `async_get_all_stations()` — all stations, optionally filtered by type (mainline/suburban/DART).
- `async_get_station_by_name()` / `async_get_station_by_code()` — due trains at a station, with optional direction, destination, and "stops at" filtering.
- `async_get_all_current_trains()` — real-time positions of all running trains, optionally filtered by type or direction.
- `async_get_train_stops()` — full route/stop history for a given train code and date.

Only the station-by-code due-trains endpoint is used by the integration itself;
the remaining methods power the config/options flows and are available for
future expansion.

## Development
Requires Python 3.14+.

```bash
pip install -e ".[dev]"   # installs test/lint tooling only
pytest                    # run the test suite
ruff check .              # lint
mypy .                    # strict type checking
```

The test suite lives under `tests/` and uses
[pytest-homeassistant-custom-component](https://pypi.org/project/pytest-homeassistant-custom-component/)
with `aresponses` for HTTP mocking.

## Known Limitations
- The Irish Rail RTPI API is an unofficial public service and may occasionally experience downtime.
- The service uses plain HTTP/HTTPS and does not require authentication.
- Data is specific to the Republic of Ireland and Northern Ireland rail network.
- The "stops at" filter performs one extra API request per candidate train per poll, which slightly increases load when enabled.

## License
This project is licensed under the **Apache License 2.0** — the same license used by
[Home Assistant core](https://www.home-assistant.io/developers/license/). See
[LICENSE.txt](LICENSE.txt) for the full text.

## AI Disclosure
This integration was developed with the assistance of AI/LLM tooling (large language
models) in the making of this code. All code was reviewed, tested, and validated by
human maintainers before release.