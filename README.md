# Irish Rail — Home Assistant Integration

[![CI](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/ci.yml)
[![HACS Validate](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml/badge.svg)](https://github.com/Gekko47/pyirishrail/actions/workflows/hacs.yml)
[![Release](https://img.shields.io/github/v/release/Gekko47/pyirishrail)](https://github.com/Gekko47/pyirishrail/releases)
[![License](https://img.shields.io/github/license/Gekko47/pyirishrail)](LICENSE.txt)

A Home Assistant integration that monitors live Irish Rail train
departures from the public, unauthenticated RTPI feed
(`api.irishrail.ie`). Each configured station/direction becomes a
device with two sensors showing the next and following train due in;
one integration-level device ("Irish Rail Services") exposes an API
connectivity binary sensor and a stops-matrix rebuild button.

| | |
|---|---|
| Domain | `irish_rail` |
| Type · IoT class | Service · cloud polling |
| Quality scale | **Platinum** — evidence in [quality_scale.yaml](custom_components/irish_rail/quality_scale.yaml) |
| Minimum HA version | 2026.8.2 |
| Runtime dependencies | none (`manifest.json` `requirements: []`) |

| | |
|---|---|
| Domain | `irish_rail` |
| Type · IoT class | Service · cloud polling |
| Quality scale | **Platinum** — every Bronze/Silver/Gold/Platinum rule done or exempt; evidence in [quality_scale.yaml](custom_components/irish_rail/quality_scale.yaml) |
| Minimum HA version | 2026.8.2 |

## Install

### HACS

1. Install [HACS](https://hacs.xyz/).
2. **HACS → Integrations** → ⋮ menu → **Custom repositories**.
3. Repository: `https://github.com/Gekko47/pyirishrail`; category
   **Integration**; **Add**.
4. **HACS → Irish Rail → Download**, then restart Home Assistant.

### Manual

Copy `custom_components/irish_rail` into your HA `config/custom_components`
directory and restart Home Assistant.

## Removal

1. **Settings → Devices & Services → Irish Rail** → click each station
   entry → ⋮ menu → **Delete**. Removing the last station entry
   automatically tears down the API-health probe, the stops-matrix
   rebuild button, and the `irish_rail.rebuild_stops_matrix` service.
2. Optional: delete `irish_rail.stops_matrix.json` from HA storage to
   drop the per-install learned matrix. Keep the bundled
   `stops_matrix.seed.json` inside the integration folder.
3. For a HACS install, remove the **Irish Rail** entry from HACS.

## Configuration

Add one entry per station from **Settings → Devices & Services → Add
Integration → Irish Rail**. The connection is validated up front; the
station list is fetched live.

1. **Pick a station.** Per-word, case-insensitive prefix match (try
   `pearse` or `galw`). Leave the box empty to browse all stations; a
   single match skips straight to filters.
2. **Filters** — leave both unticked to monitor every service:
   - **Direction**: only directions Irish Rail currently reports for
     your station are offered (`Northbound`/`Southbound` on the
     Dundalk–Rosslare and Sligo–Dublin corridors; free-text values
     such as `To Cork` elsewhere). If nothing is due right now the
     field becomes free text — leave `All` or copy the exact wording
     from irishrail.ie.
   - **Stops at**: only stations the selected services actually call
     at *after* yours are offered. Falls back to the cached matrix,
     then the bundled seed, then the full station list.
3. **Submit**. Adding the same station with the same direction is
   rejected (duplicate protection); the same station with a different
   direction creates an additional, independent entry.

### Changing settings later

| Setting | Menu action | Behaviour |
|---|---|---|
| Scan interval (30 s – 10 min, default 60 s) | **Configure** | Applies immediately, no reload |
| Stops-at filter | **Configure** | Applies immediately; `All` disables it |
| Direction filter | **Reconfigure** | Rewrites the entry identity; one reload |

Reconfiguring the direction changes the entry's identity: combinations
another entry already monitors are rejected, the previous direction's
two sensors and device are removed from the registries, and the
original entity IDs return (with names and areas kept) if you switch
back.

## Sensors

Each station/direction config entry creates a device named after the
station (and direction filter) with two sensors:

| Entity | Type | Notes |
|---|---|---|
| `next_train_due` | `SensorDeviceClass.TIMESTAMP` (datetime) | The API's expected arrival of the next train; HA's Time card shows "in 5 min" / "5 min ago" automatically |
| `following_train_due` | `SensorDeviceClass.TIMESTAMP` (datetime) | The API's expected arrival of the following train; `unknown` when fewer than two trains are scheduled |

Train type (DART, Suburban, Intercity, etc.) lives on the device's
`extra_state_attributes` — no separate dedicated entity.

### `next_train_due` attributes

| Attribute | Description |
|---|---|
| `expected_arrival_time` | Real-time expected arrival at the monitored station (`HH:MM`). |
| `scheduled_arrival_time` | Timetabled arrival at the monitored station (`HH:MM`). |
| `direction` | Travel direction of the next train. |
| `train_code` | Irish Rail identifier of the next train. |
| `api_reachable` | `True` when readable. See [Behaviour](#behaviour) for how this separates "no trains scheduled" from "API unreachable". |
| `expected_arrival` | ISO 8601 string mirroring the `next_train_due` state. |
| `time_until_arrival` | Whole-second countdown to `next_train_due`; recomputed on every read. |

### `following_train_due` attributes

| Attribute | Description |
|---|---|
| `expected_arrival_time` | Real-time expected arrival of the following train at the monitored station (`HH:MM`). |
| `scheduled_arrival_time` | Timetabled arrival of the following train at the monitored station (`HH:MM`). |
| `direction` | Travel direction of the following train. |
| `train_code` | Irish Rail identifier of the following train. |
| `api_reachable` | `True` when readable. |

Icons are defined in `icons.json` at the integration root and
overridable per entity from the UI.

### Irish Rail Services device

One device per Home Assistant instance, independent of how many
station entries are configured:

| Entity | Type | Notes |
|---|---|---|
| `binary_sensor.status` | `BinarySensorDeviceClass.CONNECTIVITY` | Pings `api.irishrail.ie` every 5 min; `True` means the API answered the most recent probe |
| `button.rebuild_stops_matrix` | `EntityCategory.CONFIG` | One press rebuilds the "stops at" matrix (≈150 stations, several minutes, background-priority HTTP). See [Stops-at filter](#stops-at-filter). |

The `irish_rail.rebuild_stops_matrix` service is the automation-facing
alias of the rebuild button.
| `scheduled_arrival_time`, `scheduled_departure_time` | Timetabled times at the monitored station. |

Sensors ship with domain-appropriate default icons defined in the integration's `icons.json`; override any icon per entity from the UI as usual.

## Examples

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

### Following train alert

Notify when the following train is due within 15 minutes:

```yaml
- alias: "Irish Rail - following train approaching"
  mode: single
  triggers:
    - trigger: numeric_state
      entity_id: sensor.dublin_pearse_northbound_following_train_due
      below: 15
  actions:
    - action: notify.mobile_app_phone
      data:
        title: "Following train soon"
        message: >-
          The following {{ state_attr('sensor.dublin_pearse_northbound_following_train_due',
          'direction') }} service arrives in about
          {{ states('sensor.dublin_pearse_northbound_following_train_due') }}
          minutes.
        data:
          tag: irish-rail-following
```

Replace the entity IDs with those of your own station/direction entries.

## Behaviour

- **Startup / reload** — an immediate first refresh runs when HA
  starts or the entry is loaded; reload re-runs setup and restores the
  same sensors under the same entity IDs. If the API is unreachable,
  the entry enters `SETUP_RETRY` and HA retries with exponential
  backoff.
- **Polling** — a `DataUpdateCoordinator` fetches fresh due-train
  data every **60 s** by default (matching the once-a-minute cadence
  of Irish Rail's real-time feed). Configurable 30 s – 10 min; applied
  live via the options flow.
- **Adaptive backoff** — consecutive failed polls double the
  effective interval, capped at ~15 minutes, so an outage never
  hammers the public API. The first successful poll restores the
  configured interval immediately.
- **Failed poll** — the coordinator keeps last-known data, sensors
  become **Unavailable** immediately, and polling resumes on the
  next scheduled cycle. The coordinator's built-in transition logger
  emits one error per outage and one info line on recovery.
- **No trains due** — a legitimately empty feed (e.g. late at night)
  leaves sensors *available* with state `unknown` and attribute
  `api_reachable: true`. **Unavailable = API unreachable;
  available-but-unknown = quiet timetable.**
- **Persistent empty feed** — a station returning nothing for
  ~10 minutes during Dublin-time service hours (06:00–midnight)
  raises a *No train data received for {station}* repair issue
  pointing at this README's [Troubleshooting](#troubleshooting)
  section. The issue clears itself on the first refresh that returns
  real trains, or immediately when the shared API-health probe
  confirms the upstream is reachable.

## "Stops at" filter

When configuring a station, you can enable a **"stops at"** filter
(combined with a direction filter or alone) so only trains that
actually call at a chosen downstream station are exposed. The
dropdown never offers arbitrary free text; it lists stations the
selected services genuinely reach *after* yours. The matrix behind
it has three sources, applied in order of freshness:

1. **Live sampling** (source of truth) — trains currently due are
   resolved to their current journey; only stops reached after your
   station on that journey are offered.
2. **Learned matrix** — every successful discovery (including
   ordinary polling while a filter is active) is merged into a
   per-install cache that survives restarts and refreshes itself.
3. **Bundled seed** — a reference snapshot ships with the integration
   so setup still works when no services are currently due (overnight).

To refresh the matrix without the integration's normal live learning,
press the **Rebuild stops at matrix** button on the Irish Rail Services
device (or call the `irish_rail.rebuild_stops_matrix` service). A
press samples every station in-process at background priority and
merges the new knowledge back in. While the rebuild is in flight, the
button greys out; the outcome appears in its state attributes and a
persistent notification.

The offline snapshot generator for the bundled seed lives at
[`scripts/build_stops_matrix.py`](scripts/build_stops_matrix.py):

```sh
python scripts/build_stops_matrix.py            # full rebuild
python scripts/build_stops_matrix.py --limit 5  # smoke test
```

## Troubleshooting

Download diagnostics from the ⋮ menu on a config entry: the report
contains redacted entry data/options plus coordinator health (update
interval, last-update success flag, number of due trains). Station
names and codes are partially masked (short prefix + hash suffix),
so it is safe to attach to bug reports.

| Symptom | Cause / action |
|---|---|
| Sensors show `unavailable` | The last poll failed (downtime, timeout, or malformed response). One error per outage lands in **Settings → System → Logs**; polling continues and sensors recover on the first successful poll. |
| Entry stuck in retry after startup/reload | The API was unreachable during setup. It completes on its own; **Reload** forces an immediate attempt. |
| `unknown` states late at night | Quiet timetable, not a fault — sensors stay available with `api_reachable: true`. |
| *No train data received for {station}* repair issue | Persistent empty responses during service hours may indicate an API or schedule-data change. Check whether other stations report data, reload the entry, and if it persists remove/re-add it or update the integration. Clears itself once real trains return. |
| `binary_sensor.status` is `off` | The Irish Rail API itself is unreachable. Sensors may also be unavailable; check **Settings → System → Logs** for the probe's reason. |

## Remove

1. **Settings → Devices & Services → Irish Rail** → click each station
   entry → ⋮ menu → **Delete**. Removing the last station entry
   automatically tears down the API-health probe, the stops-matrix
   rebuild button, and the `irish_rail.rebuild_stops_matrix` service.
2. Optional: delete `irish_rail.stops_matrix.json` from HA storage to
   drop the per-install learned matrix. Keep the bundled
   `stops_matrix.seed.json` inside the integration folder.
3. For a HACS install, remove the **Irish Rail** entry from HACS.

## Underlying API client

The async client lives in `custom_components/irish_rail/pyirishrail/`
as an internal, framework-agnostic module. It uses Python's standard
library `xml.etree.ElementTree` for parsing (an explicit pre-parse
DTD/entity guard rejects any hostile DTD before the parser is
invoked), accepts an injected `aiohttp.ClientSession`, raises a
typed exception hierarchy, and shares a single `RequestGate` per
Home Assistant instance. Its public surface:

- `IrishRailClient.async_get_all_stations()` — all stations, optionally
  filtered by type (mainline / suburban / DART).
- `IrishRailClient.async_get_station_by_name()` /
  `async_get_station_by_code()` — due trains at a station, with
  optional direction, destination and "stops at" filtering.
- `IrishRailClient.async_get_station_directions()` — distinct live
  direction values for one station (the source of the config flow's
  per-station dropdown).
- `IrishRailClient.async_get_all_current_trains()` — real-time
  positions of all running trains, optionally filtered by type or
  direction.
- `IrishRailClient.async_get_train_stops()` — full route/stop
  history for a given train code and date (cached per train/day).

Only the station-by-code due-trains endpoint is used by the
integration's normal polling; the rest power the config and options
flows and the stops-matrix rebuild.

## Development

Requires Python 3.14+ and the dev tooling pinned in
`pyproject.toml` / CI:

```bash
pip install pytest pytest-asyncio pytest-cov aresponses \
            pytest-homeassistant-custom-component ruff mypy
pytest tests/components/irish_rail
ruff check custom_components/irish_rail tests/components/irish_rail scripts
mypy custom_components/irish_rail tests/components/irish_rail
pytest tests/components/irish_rail \
       --cov=custom_components/irish_rail --cov-fail-under=100
```

The test suite uses
[pytest-homeassistant-custom-component](https://pypi.org/project/pytest-homeassistant-custom-component/)
with `aresponses` for HTTP mocking. The full remediation plan,
per-phase status and rationale are in
[`.cline/clean-cut-baseline-plan.md`](.cline/clean-cut-baseline-plan.md).

## Known limitations

- The Irish Rail RTPI feed is an unofficial public service; there is
  no authentication and no documented rate limit. The shared
  `RequestGate` paces the integration at 2 concurrent / 0.25 s spacing
  per Home Assistant instance; large multi-station installs should
  prefer background-priority service work (the matrix rebuild is
  already background-priority) and avoid polling more often than the
  default 60 s.
- Direction filter values are *exactly* what the API reports for the
  current services; for stations reporting free-text directions
  (`To Cork` and similar) the dropdown falls back to free text when
  nothing is due. A filter can never silently match nothing.
- Service hours used to suppress the persistent-empty-feed repair
  issue follow Europe/Dublin civil time across IST/GMT DST shifts;
  empty responses between 00:00 and 06:00 are treated as a normal
  quiet period regardless of where Home Assistant itself runs.
- The "stops at" filter fetches each candidate train's movement
  history: one extra API request per newly seen train per day,
  served from a per-day cache afterwards. Lookups run concurrently
  with a small concurrency cap, keeping the added latency per poll
  bounded even at busy stations.

## License

Apache License 2.0 — see [LICENSE.txt](LICENSE.txt).

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
  storage. It receives writes from the coordinator's live learning path,
  the config flow's live discovery, AND the rebuild button's network-wide
  sweep — all routed through the same `StopsMatrixStore` so the two
  paths reconcile by construction. HACS updates never touch it, so a
  runtime rebuild is preserved across upgrades. The bundled seed is
  `stops_matrix.seed.json` inside the integration folder; that file *is*
  overwritten on each HACS update by design.

## License
This project is licensed under the **Apache License 2.0** — the same license used by
[Home Assistant core](https://www.home-assistant.io/developers/license/). See
[LICENSE.txt](LICENSE.txt) for the full text.

## AI Disclosure
Parts of this project were drafted with AI/LLM assistance. All code was reviewed,
tested, and validated by human maintainers before release.