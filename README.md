# Irish Rail Home Assistant Integration

A modern, Home Assistant Bronze-tier custom integration for the Irish Rail Realtime Passenger Information (RTPI) service.

## Description
This integration allows you to monitor upcoming trains at any Irish Rail station directly from Home Assistant. It provides real-time data on due times, destinations, delays, and more, using the unofficial Irish Rail RTPI API.

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
4. Choose the station you wish to monitor from the dropdown list.
5. Optionally, select a direction filter (Northbound or Southbound).
6. Click **Submit**.

*Note: No API key is required to use this service.*

## Entities
The integration creates the following sensors for each configured station:
- **Next train due**: Minutes until the next train arrives.
- **Next train destination**: The destination of the upcoming train.
- **Next train delay**: Delay in minutes (if any).
- **Next train type**: The type of service (e.g., DART, Suburban, Intercity).

Each sensor also includes additional attributes such as scheduled/expected times, origin, and train code.

## Actions
This integration is read-only and exposes no service actions. The user-facing actions are limited to configuration management:

- **Add a station**: via **Settings → Devices & Services → Add Integration → Irish Rail**. Selecting a station (and optionally a direction filter) creates a config entry and four sensors for that station. Adding the same station with the same direction filter again is prevented (duplicate protection); the same station with a *different* direction filter creates an additional, independent entry.
- **Remove an entry**: deleting a config entry (see Removal below) unloads its coordinator and removes all four sensors belonging to that station/direction. No manual cleanup is required — no files or persistent state are written outside Home Assistant's own config-entry storage.
- **Reload an entry**: reloading (three-dot menu → Reload) re-runs setup, performing a fresh first refresh against the API. If the API is unreachable at that moment, the entry enters a retry state and the sensors remain unavailable until it succeeds.

## Triggers (data updates)
The integration polls the Irish Rail RTPI API periodically; there are no user-configurable triggers:

- **On startup / entry load**: when Home Assistant starts or the config entry is loaded/reloaded, an immediate first refresh is performed. If it fails, setup is retried with backoff (`ConfigEntryNotReady`).
- **Periodic polling**: after the first refresh, a `DataUpdateCoordinator` fetches fresh due-train data every **60 seconds** (`DEFAULT_SCAN_INTERVAL`), matching the roughly once-a-minute cadence at which Irish Rail's real-time feed changes, while keeping load on the public API sustainable.
- **On coordinator failure**: if a poll fails (connection error, timeout, or malformed response), the coordinator keeps the last known data, logs the failure, and retries on the next cycle; sensors become **Unavailable** after repeated failures.

## Conditions (normal vs. degraded operation)
- **Normal**: the coordinator returns a list of due trains; sensors report live values (minutes due, destination, delay, train type) plus schedule attributes.
- **No trains due**: the API may legitimately return an empty list (e.g. late at night). Sensors then report `unknown`/`None` rather than an error — this is an expected condition, not a failure.
- **API unreachable / timeout / malformed response**: the poll raises `UpdateFailed`. Entities become **Unavailable**, the failure is logged once per occurrence, and polling continues automatically. No user intervention is needed; entities recover on their own when the API responds again.
- **First-refresh failure at setup**: if the API is unreachable when the entry is first set up (or reloaded), the entry enters `SETUP_RETRY` and Home Assistant retries with exponential backoff until the API is reachable.

## Removal
1. Go to **Settings** -> **Devices & Services**.
2. Find the **Irish Rail** integration entry you wish to remove.
3. Click the three dots and select **Delete**.
4. To completely remove the integration from your system, delete the `irish_rail` folder from `custom_components` (if manually installed) or remove it via HACS.

## Known Limitations
- The Irish Rail RTPI API is an unofficial public service and may occasionally experience downtime.
- The service uses plain HTTP/HTTPS and does not require authentication.
- Data is specific to the Republic of Ireland and Northern Ireland rail network.

## License
This project is licensed under the **Apache License 2.0** — the same license used by
[Home Assistant core](https://www.home-assistant.io/developers/license/). See
[LICENSE.txt](LICENSE.txt) for the full text.

## AI Disclosure
This integration was developed with the assistance of AI/LLM tooling (large language
models) in the making of this code. All code was reviewed, tested, and validated by
human maintainers before release.
