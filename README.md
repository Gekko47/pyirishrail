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
connectivity binary sensor and a stops-matrix rebuild button, and
exists exactly while at least one station entry is loaded.

| | |
|---|---|
| Domain | `irish_rail` |
| Type · IoT class | Service · cloud polling |
| Quality scale | **Platinum** — evidence in [quality_scale.yaml](custom_components/irish_rail/quality_scale.yaml) |
| Minimum HA version | 2026.8.2 |
| Runtime dependencies | none (`manifest.json` `requirements: []`) |


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
   automatically tears down the API-health probe, the "Irish Rail
   Services" device with its connectivity sensor and rebuild button,
   and the `irish_rail.rebuild_stops_matrix` service. Removing one
   of several stations moves them to a surviving entry instead.
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
| Scan interval (30 s – 10 min, default 60 s) | **Configure** | Applies immediately, no reload — the polling timer is re-armed, not left on the old spacing until the next tick |
| Stops-at filter | **Configure** | Applies immediately; `All` disables it. The dropdown offers only stops your station and direction actually reach. |
| Direction filter | **Reconfigure** | Rewrites the entry identity; one reload. Transactional: your entity names, icons, areas and disabled states carry across, and a reload that fails leaves the old entry untouched. |

Reconfiguring the direction changes the entry's identity: combinations
another entry already monitors are rejected, the previous direction's
two sensors and device are removed from the registries, and your
entity names, icons, areas and disabled states are carried over to the
new entities. Entity IDs are regenerated because they derive from the
new unique ID.

## Sensors

Each station/direction config entry creates a device named after the
station (and direction filter) with two sensors:

| Entity | Type | Notes |
|---|---|---|
| `next_train_due` | `SensorDeviceClass.TIMESTAMP` (datetime) | The API's expected arrival of the next train; HA's Time card shows "in 5 min" / "5 min ago" automatically |
| `following_train_due` | `SensorDeviceClass.TIMESTAMP` (datetime) | The API's expected arrival of the following train; `unknown` when fewer than two trains are scheduled |

Only the fields listed below are published as attributes; train type
is not currently exposed, and there is no separate dedicated entity.

### `next_train_due` attributes

| Attribute | Description |
|---|---|
| `expected_arrival_time` | Real-time expected arrival at the monitored station (`HH:MM`). |
| `scheduled_arrival_time` | Timetabled arrival at the monitored station (`HH:MM`). |
| `direction` | Travel direction of the next train. |
| `train_code` | Irish Rail identifier of the next train. |
| `api_reachable` | `True` when readable. See [Behaviour](#behaviour) for how this separates "no trains scheduled" from "API unreachable". |
| `expected_arrival` | ISO 8601 string mirroring the `next_train_due` state. |
| `time_until_arrival` | Whole-second countdown to `next_train_due`, recomputed on each poll (not continuously between polls). |

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

**This device exists if and only if at least one station entry is
configured and loaded.** Add a station and it appears; remove the last
one and it goes away with the `rebuild_stops_matrix` service. It is
never tied to a particular station: ownership moves to a surviving
entry automatically, so removing one of several stations does not take
the connectivity sensor or the rebuild button with it, and no
per-station device ever carries them.

| Entity | Type | Notes |
|---|---|---|
| `binary_sensor.status` | `BinarySensorDeviceClass.CONNECTIVITY` | Pings `api.irishrail.ie` every 5 min; `True` means the API answered the most recent probe |
| `button.rebuild_stops_matrix` | `EntityCategory.CONFIG` | One press rebuilds the "stops at" matrix (≈150 stations, several minutes, background-priority HTTP). See [Stops-at filter](#stops-at-filter). |

The `irish_rail.rebuild_stops_matrix` service is the automation-facing
alias of the rebuild button. Pressing it while a sweep is already in
flight raises a translated error rather than starting a second one.

Sensors ship with domain-appropriate default icons defined in the integration's `icons.json`; override any icon per entity from the UI as usual.

## Examples

### Departure alert

Notify when the next train is due within 10 minutes on weekdays.

`next_train_due` is a TIMESTAMP sensor, so a `numeric_state` trigger
cannot compare it to a number; use the `time_until_arrival` attribute
with a template trigger instead:

```yaml
- alias: "Irish Rail - time to leave"
  mode: single
  triggers:
    - trigger: template
      value_template: >-
        {% set eta = state_attr('sensor.dublin_pearse_northbound_next_train_due',
           'time_until_arrival') | float(0) %}
        {{ eta > 0 and eta < 600 }}
      for: "00:01:00"
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
          {{ (state_attr('sensor.dublin_pearse_northbound_next_train_due',
          'time_until_arrival') | int(0) / 60) | round(1) }} minutes.
        data:
          tag: irish-rail-departure
```

The attribute is recomputed on each poll (the default interval is 60 s),
so the countdown is accurate to within one poll rather than live.

### Following train alert

Notify when the following train is due within 15 minutes. The sensor is
`unknown` whenever fewer than two trains are scheduled, so guard the
state before converting it — `as_timestamp` raises on a non-date value:

```yaml
- alias: "Irish Rail - following train approaching"
  mode: single
  triggers:
    - trigger: template
      value_template: >-
        {% set state = states(
           'sensor.dublin_pearse_northbound_following_train_due') %}
        {% set arrival = as_timestamp(as_datetime(state)) | float(0)
           if state not in ['unknown', 'unavailable', 'none'] else 0 %}
        {{ arrival > as_timestamp(now()) and arrival <= (as_timestamp(now()) + 900) }}
      for: "00:01:00"
  actions:
    - action: notify.mobile_app_phone
      data:
        title: "Following train soon"
        message: >-
          The following
          {{ state_attr('sensor.dublin_pearse_northbound_following_train_due',
          'direction') }} service arrives shortly.
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
  real trains, or — for a station with no direction or stops-at
  filter — immediately when the shared API-health probe confirms the
  upstream is reachable. A **filtered** station is never cleared that
  way: the probe polls a different, unfiltered station, so "the API
  answered" says nothing about whether your filter is satisfiable. An
  impossible filter value is exactly the case this issue is for.

## "Stops at" filter

When configuring a station, you can enable a **"stops at"** filter
(combined with a direction filter or alone) so only trains that
actually call at a chosen downstream station are exposed. The
dropdown never offers arbitrary free text; it lists stations the
selected services genuinely reach *after* yours, scoped to your own
station **and** direction. Sources, in order of freshness:

1. **Learned matrix** — every successful discovery (including
   ordinary polling while a filter is active) is merged into a
   per-install cache that survives restarts and refreshes itself.
2. **Bundled seed** — a reference snapshot ships with the integration
   so setup still works when nothing is currently due (overnight).
3. **Live sampling** (source of truth) — trains currently due are
   resolved to their current journey; only stops reached after your
   station on that journey are offered.
4. **The full national station list** — shown only when all three
   above come up empty, and labelled in the form, because a station
   chosen from it may never be reached. Prefer the first three.

Your currently stored value stays selectable and submittable even when
it is not in the list, so a filter you already have is never silently
dropped or reset.

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

## Underlying API client

The async client lives in `custom_components/irish_rail/client.py`
as an internal, framework-agnostic module (with `request_gate.py`,
`models.py`, `errors.py` and `lib_const.py` alongside it). It uses
Python's standard
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

## AI Disclosure
Parts of this project were drafted with AI/LLM assistance. All code was reviewed,
tested, and validated by human maintainers before release.