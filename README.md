# Irish Rail Home Assistant Integration

[![CI](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml)
[![HACS Validate](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml)
[![Release](https://img.shields.io/github/v/release/Gekko47/pyirishrail)](https://github.com/Gekko47/pyirishrail/releases)
[![License](https://img.shields.io/github/license/Gekko47/pyirishrail)](LICENSE.txt)

Monitor live trains at any Irish Rail station from Home Assistant. This custom integration polls the unofficial [Irish Rail RTPI](https://www.irishrail.ie/) API and exposes due times, destinations, delays, and service types as sensors — configured entirely through the UI, no API key required. Typical uses: commuter dashboards, delay alerts, and presence-based departure reminders.

| | |
|---|---|
| Domain | `irish_rail` |
| Type · IoT class | Service · cloud polling |
| Quality scale | **Platinum** — every Bronze/Silver/Gold/Platinum rule done or exempt; evidence in [quality_scale.yaml](custom_components/irish_rail/quality_scale.yaml) |
| Minimum HA version | 2026.8.2 |

## Installation

### HACS (recommended)

1. Install [HACS](https://hacs.xyz/).
2. Go to **HACS → Integrations**, open the ⋮ menu and pick **Custom repositories**.
3. Paste `https://github.com/Gekko47/pyirishrail`, set category **Integration**, click **Add**.
4. Find **Irish Rail** and click **Download**, then restart Home Assistant.

### Manual

1. Download the latest release.
2. Copy `custom_components/irish_rail` into your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

## Removal

1. Go to **Settings → Devices & Services → Irish Rail**, click each station entry, and use the ⋮ menu's **Delete** option. Removing the last station entry automatically tears down the API-health probe, the stops-matrix rebuild button, and the `irish_rail.rebuild_stops_matrix` service.
2. Optional: delete `irish_rail.stops_matrix.json` from HA storage to drop the per-install runtime rebuild data. Keep the bundled `stops_matrix.seed.json` inside the integration folder.
3. For a HACS install, remove the **Irish Rail** entry from HACS.

## Configuration

Add one entry per station via **Settings → Devices & Services → Add Integration → Irish Rail**:

1. **Pick a station.** Start typing to narrow the list — matching is per-word and case-insensitive, exactly like irishrail.ie (try `pearse` or `galw`). Leave the box empty to browse all stations; a single match skips ahead. The list is fetched live from the API, so the connection is validated up front.
2. **Optional filters** — anything left unticked simply means *monitor everything*:
   - **Direction**: only directions Irish Rail actually reports for your station are offered (**Northbound/Southbound** on the Dundalk–Rosslare and Sligo–Dublin corridors, free-text values such as **To Cork** elsewhere), so a filter can never silently match nothing. If no trains are due right now the field becomes free text — leave **All** or copy the exact wording from irishrail.ie.
   - **Stops at**: show only trains calling at a selected stop. The dropdown lists stops actually served by the currently selected services (your own station excluded), falling back to the full station list if nothing live could be sampled.
3. **Upcoming trains**: how many trains (1–5, default 3) appear in each sensor's `upcoming_trains` attribute.
4. Click **Submit**. Adding the same station with the same direction again is prevented (duplicate protection); the same station with a *different* direction creates an additional, independent entry.

### Changing settings later

Use the ⋮ menu on the config entry (the station itself cannot be changed). Values saved here always win over ones picked during initial setup:

| Setting | Menu action | Behaviour |
|---|---|---|
| Scan interval — 30 s – 10 min, default 60 s | **Configure** | Applies immediately, no reload needed |
| Number of upcoming trains — 1–5, default 3 | **Configure** | Applies immediately |
| Stops-at filter | **Configure** | Applies immediately; **All** disables it |
| Direction filter | **Reconfigure** | Rewrites the entry identity and triggers exactly one reload |

Changing the direction changes the entry's identity: combinations another entry already monitors are rejected, and the previous direction's four sensors and device are removed from the registries automatically — switch back later and the original entity IDs return, with your names and areas kept. Entries created before dynamic direction discovery that hold `Northbound`/`Southbound` at a non-corridor station should be reconfigured once here; those legacy filters never matched real services at such stations.

## Entities

Each entry creates four sensors grouped under a device named after the station (and direction filter):

| Sensor | Description |
|---|---|
| **Next train due** | Minutes until the next train arrives (`DURATION`, minutes, measurement — supports long-term statistics) |
| **Next train destination** | Destination of the upcoming train |
| **Next train delay** | Delay in minutes (`DURATION`, minutes, measurement) |
| **Next train type** | Service type (e.g. DART, Suburban, Intercity) |

Every sensor also carries these attributes:

| Attribute | Description |
|---|---|
| `upcoming_trains` | The next N trains (count configurable during setup/options), each with due-in minutes, destination, delay, service type, train code, and scheduled origin/destination times. Shorter than requested if fewer trains are scheduled. |
| `api_reachable` | Always `true` when readable — see [Data updates & availability](#data-updates--availability) for how this separates "no trains scheduled" from "API unreachable". |
| `origin`, `direction`, `train_code` | Origin station, travel direction, and Irish Rail identifier of the next train. |
| `origin_time`, `destination_time` | Scheduled departure/arrival times for the next train's full journey. |
| `expected_arrival_time`, `expected_departure_time` | Real-time expected times at the monitored station. |
| `scheduled_arrival_time`, `scheduled_departure_time` | Timetabled times at the monitored station. |

Sensors ship with domain-appropriate default icons defined in the integration's `icons.json`; override any icon per entity from the UI as usual.

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

## Data updates & availability

- **Startup / reload**: an immediate first refresh runs when Home Assistant starts or the entry is loaded — **Reload** re-runs setup and restores the same sensors under the same entity IDs. If the API is unreachable, the entry enters `SETUP_RETRY` and Home Assistant retries with exponential backoff.
- **Polling**: a `DataUpdateCoordinator` fetches fresh due-train data every **60 seconds** by default, matching the once-a-minute cadence of Irish Rail's real-time feed while keeping load on the public API sustainable. The interval is configurable (30 s – 10 min) and applies live.
- **Adaptive backoff**: consecutive failed polls double the effective interval, capped at ~15 minutes, so an outage never hammers the public API; the first successful poll restores the configured interval. Manual refreshes stay available throughout.
- **Failed poll**: the coordinator keeps last-known data, logs one error per outage, and sensors become **Unavailable** immediately — recovering automatically on the next successful poll.
- **No trains due**: a legitimately empty feed (e.g. late at night) leaves sensors *available* reporting `unknown` with `api_reachable: true`. **Unavailable = API unreachable; available-but-unknown = quiet timetable.**
- **Persistent empty feed**: if a station keeps returning nothing during service hours (roughly 06:00–midnight), a repair issue is raised under **Settings → System → Repairs**; it clears itself on the first refresh that returns real trains. While the shared [API connectivity probe](#global-irish-rail-api-device-health-sensor-and-matrix-rebuild-button) confirms the API itself is reachable, an empty board is treated as *no scheduled services inside the look-ahead window* instead: no issue is raised and any open one is cleared automatically.

## "Stops at" filter

When configuring a station you can optionally enable a **"stops at"** filter (optionally combined with a direction filter) so only trains that actually call at a chosen downstream station are exposed. The dropdown never offers arbitrary free text: it only lists stations that the selected services genuinely reach *after* your station.

Because Irish Rail's realtime API publishes no static route directory, option lists are built from three layers, in order:

1. **Live sampling** (source of truth): trains currently due at your station are resolved to their current journey, and only stops reached after your station on that journey are offered.
2. **Learned matrix**: every successful discovery — including ordinary polling while a filter is active — is merged into a per-install cache that survives restarts and refreshes itself automatically.
3. **Bundled seed**: a reference snapshot ships with the integration so setup still works when no services are currently due (e.g. overnight).

Only if all three come up empty (fresh install during dead hours) does the form fall back to the full national station list. To regenerate the bundled snapshot from the live network, run:

```shell
python scripts/build_stops_matrix.py
```

## Diagnostics & troubleshooting

Download diagnostics from the ⋮ menu on a config entry: the report contains redacted entry data/options plus coordinator health (update interval, last-update success flag, number of due trains). Station names and codes are partially masked (short prefix + hash suffix), so it is safe to attach to bug reports.

| Symptom | Meaning |
|---|---|
| Sensors show `unavailable` | The last poll failed (downtime, timeout, or malformed response). One error per outage lands in **Settings → System → Logs**; polling continues and sensors recover on the first successful poll. |
| Entry stuck in retry after startup/reload | The API was unreachable during setup. It completes on its own; **Reload** forces an immediate attempt. |
| `unknown` states late at night | Quiet timetable, not a fault — sensors stay available with `api_reachable: true`. |
| *"No train data received"* repair issue | Persistent empty responses during service hours may indicate an API or schedule-data change. Check whether other stations report data, reload the entry, and if it persists remove/re-add it or update the integration. Clears itself once real trains return. |

## Removal

Delete the entry from its ⋮ menu: the four sensors and device are removed immediately, any pending repair issue is cleared, and nothing is written outside Home Assistant's own config-entry storage. To remove the integration entirely, uninstall it via HACS (or delete the `custom_components/irish_rail` folder for manual installs) and restart Home Assistant.

Re-adding the same station/direction later restores the original entity IDs; Home Assistant's registries keep any names, icons, and area assignments you made.

## Underlying API client
The bundled `IrishRailClient` wraps the public RTPI XML endpoints over HTTPS with safe XML parsing ([defusedxml](https://pypi.org/project/defusedxml/)) and a 10-second timeout per request. It exposes methods that may be useful for custom automations/scripts built on top of the library:

- `async_get_all_stations()` — all stations, optionally filtered by type (mainline/suburban/DART).
- `async_get_station_by_name()` / `async_get_station_by_code()` — due trains at a station, with optional direction, destination, and "stops at" filtering.
- `async_get_station_directions()` — distinct live direction values for one station (the source of the config flow's per-station dropdown).
- `async_get_all_current_trains()` — real-time positions of all running trains, optionally filtered by type or direction.
- `async_get_train_stops()` — full route/stop history for a given train code and date (cached per train/day).

Only the station-by-code due-trains endpoint is used by the integration itself; the remaining methods power the config/options flows and are available for future expansion.

## Development
Requires Python 3.14+.

```bash
pip install -e ".[dev]"                    # installs test/lint tooling only
pytest                                     # run the test suite
ruff check .                               # lint
mypy custom_components/irish_rail          # strict type checking (as CI)
pytest --cov=custom_components/irish_rail --cov-fail-under=100  # CI coverage gate
```

The test suite lives under `tests/` and uses
[pytest-homeassistant-custom-component](https://pypi.org/project/pytest-homeassistant-custom-component/)
with `aresponses` for HTTP mocking.

## Known Limitations
- The Irish Rail RTPI API is an unofficial public service and may occasionally experience downtime.
- The service uses plain HTTP/HTTPS and does not require authentication.
- Data is specific to the Republic of Ireland and Northern Ireland rail network.
- The "stops at" filter fetches each candidate train's route: one extra API request per newly seen train per day, served from a per-day cache afterwards. Lookups run concurrently with a small concurrency cap, keeping the added latency per poll bounded even at busy stations.

## Integration-level service entities: health sensor and matrix rebuild button

Two integration-wide entities exist exactly once no matter how many station
config entries you install. They share a single **Irish Rail Services**
device card (separate from the per-station devices) on the integration
page, so they appear together as one service unit rather than as detached
rows in the Entities tab:

- The connectivity binary sensor is tagged `EntityCategory.DIAGNOSTIC`.
- The rebuild button is tagged `EntityCategory.CONFIG`.

The first config entry to set up claims providership and registers both
entities plus the `irish_rail.rebuild_stops_matrix` service. They vanish
when that entry is removed and return when the next entry claims first;
a reloaded existing entry picks them back up.

### API connectivity sensor (`binary_sensor.irish_rail_api_connectivity`)
A lightweight probe (`getAllStationsXML`) pings the RTPI API every 5 minutes
(plus once at startup). While the probe confirms the API is reachable, an
empty board at your station is treated as *no scheduled services inside the
RTPI look-ahead window* instead of a fault:

- no persistent `empty_data_during_service_hours` repair issue is raised,
- any issue raised before the API was confirmed healthy is cleared
  automatically.

If the health probe itself starts failing, the original warning behaviour
returns so genuine outages are still surfaced.

### Rebuild stops-at matrix button (`button.irish_rail_rebuild_stops_matrix`)
Pressing this runs the equivalent of `scripts/build_stops_matrix.py`
in-process, without a Home Assistant restart:

> Warning: one press polls ~150 stations plus per-train movement lookups
> against the public Irish Rail RTPI API and can take several minutes. The
> same caution is logged at WARNING level for every run.

Results are gap-fill merged: existing stops in `stops_matrix.json` are never
removed; newly observed station-direction-stops knowledge is unioned in,
timestamps refreshed, and the bundled-seed cache invalidated so the config
flow immediately benefits. A press while a rebuild is already running is
rejected rather than queued. Progress and outcome (stations sampled, stops
added, duration, errors) appear in the button's state attributes. The same
action is available as the service `irish_rail.rebuild_stops_matrix` for
automations.

Notes:
- The per-install learning cache is `irish_rail.stops_matrix.json` in HA
  storage (the runtime file the rebuild button writes). HACS updates
  never touch it, so a runtime rebuild is preserved across upgrades.
  The bundled seed is `stops_matrix.seed.json` inside the integration
  folder; that file *is* overwritten on each HACS update by design.

## License
This project is licensed under the **Apache License 2.0** — the same license used by
[Home Assistant core](https://www.home-assistant.io/developers/license/). See
[LICENSE.txt](LICENSE.txt) for the full text.

## AI Disclosure
Parts of this project were drafted with AI/LLM assistance. All code was reviewed,
tested, and validated by human maintainers before release.