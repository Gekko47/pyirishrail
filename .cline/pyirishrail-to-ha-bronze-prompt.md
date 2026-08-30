# Task: Rewrite pyirishrail as a Home Assistant Bronze-tier integration

## Role

You are a senior Home Assistant core/HACS integration developer. You will take the
existing `pyirishrail` Python library (source: https://github.com/ttroy50/pyirishrail)
and turn it into a complete, installable Home Assistant **custom integration** named
`irish_rail` that satisfies every rule in the **Bronze** tier of the Home Assistant
Integration Quality Scale, as defined at
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/ and
https://developers.home-assistant.io/docs/core/integration-quality-scale/.

Before writing any code, fetch and read that rules page (and the "Creating your first
integration" + "Config flow" + "Data update coordinator" HA developer docs) to confirm
the current, exact list of Bronze rules and their acceptance criteria, since the scale
is occasionally revised. Do not rely purely on this prompt's summary of the rules below
— treat it as a checklist to verify against the live docs, not a substitute for them.

## Source material to convert

The current repo is a synchronous library with this shape:

- `pyirishrail/pyirishrail.py` — a single class `IrishRailRTPI` that calls
  `https://api.irishrail.ie/realtime/realtime.asmx/` endpoints via the blocking
  `requests` library, parses the XML response with `xml.dom.minidom`, and returns
  plain `dict` objects. Key methods: `get_all_stations(station_type=None)`,
  `get_all_current_trains(train_type=None, direction=None)`,
  `get_station_by_name(station_name, num_minutes=None, direction=None,
  destination=None, stops_at=None)`, `get_station_by_code(...)` (same signature keyed
  by code instead of name), and `get_train_stops(train_code, date=None)`.
- `setup.py` / `requirements.txt` — legacy packaging, version `0.0.2`, only
  dependency is `requests`, no type hints, no `python_requires`.
- `test/test_pyirishrail.py` — old-style `unittest` tests against static XML
  fixtures.
- `.travis.yml` — dead CI.

Use this as the reference for *what data the integration needs to expose*
(station lookup, upcoming train due times, delays, origin/destination, scheduled vs.
expected times), but do not reuse any of the synchronous/blocking code as-is. Rebuild
the client from scratch to the standards below.

## Non-negotiable architectural decisions

1. **Fully async.** Replace `requests` with `aiohttp`. Never call blocking I/O from
   the event loop. Use Home Assistant's shared client session via
   `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)` — do not open
   a new `aiohttp.ClientSession` per call, and do not create one at all inside the
   library if it can instead accept an injected session.
2. **Safe XML parsing.** Replace `xml.dom.minidom` with `defusedxml.ElementTree` (or
   another defusedxml-based parser) to eliminate XXE/entity-expansion risk. Add
   `defusedxml` to the integration's `requirements` in `manifest.json`.
3. **Standalone client library.** Put all HTTP/XML/parsing logic in a small, fully
   type-hinted, framework-agnostic module (e.g. `custom_components/irish_rail/api.py`)
   with no Home Assistant imports, so it is unit-testable in isolation and could later
   be split into its own PyPI package. It must raise a small hierarchy of typed
   exceptions (e.g. `IrishRailError` base, `IrishRailConnectionError`,
   `IrishRailTimeoutError`, `IrishRailParseError`) instead of returning `[]` on
   failure. Never swallow errors silently.
4. **DataUpdateCoordinator.** All polling goes through a single
   `IrishRailDataUpdateCoordinator(DataUpdateCoordinator[...])` per config entry. No
   entity should call the API directly.
5. **Typed everywhere.** Full type hints on every function/method/class in the new
   code (target `mypy --strict` cleanliness). Use `from __future__ import annotations`
   consistently with current HA core style.

## Required file tree

Produce the complete contents of every file below (not summaries — full working code):

```
custom_components/irish_rail/
├── __init__.py
├── api.py
├── config_flow.py
├── const.py
├── coordinator.py
├── diagnostics.py            (optional but recommended; not required for Bronze)
├── entity.py
├── manifest.json
├── quality_scale.yaml
├── sensor.py
├── strings.json
├── translations/
│   └── en.json
└── brand/
    ├── icon.png              # 72×72 px, transparent PNG (HA icon)
    ├── logo.png              # 168×168 px, transparent PNG (HA large logo)
    └── small.png             # 72×72 px, transparent PNG (HA small logo)

tests/components/irish_rail/
├── __init__.py
├── conftest.py
├── test_api.py
├── test_config_flow.py
├── test_coordinator.py
└── test_init.py

pyproject.toml
README.md
LICENSE (keep existing MIT license, update copyright holder only if instructed)
.github/workflows/ci.yml
hacs.json
```

## Per-file requirements

### `manifest.json`
- `"domain": "irish_rail"`, `"name": "Irish Rail"`.
- `"config_flow": true`.
- `"quality_scale": "bronze"`.
- `"iot_class": "cloud_polling"`.
- `"integration_type": "service"`.
- `"requirements": ["defusedxml>=X.Y.Z"]` (aiohttp is already provided by HA core —
  do not list it).
- `"codeowners": ["@<placeholder-github-handle>"]` — leave a clear `TODO` comment in
  your response telling the user to replace this with their real GitHub handle.
- `"documentation"` and `"issue_tracker"` URLs pointing at the new repo.
- Valid semantic `"version"` (e.g. `"1.0.0"`) since this is a custom (non-core)
  integration distributed via HACS.

### `const.py`
- `DOMAIN = "irish_rail"`.
- Config keys (`CONF_STATION`, `CONF_STATION_CODE`, `CONF_DIRECTION`, etc. as needed).
- Default scan interval constant — **must not assume a fixed cadence**. Before selecting
  a value, verify Irish Rail's actual service update cadence by inspecting the upstream
  API response headers or metadata (e.g., `ETag`, `Last-Modified`, or any advertised
  refresh period). Consider server-load implications: overly frequent polling increases
  API load and is unsustainable. Select the shortest interval that is justified by the
  verified cadence, and document the evidence and rationale for the chosen interval
  in a code comment (this satisfies the `appropriate-polling` rule).

### `api.py`
- `IrishRailClient` class, constructed with an injected `aiohttps.ClientSession`.
- Async methods mirroring the original library's functionality:
  `async_get_all_stations`, `async_get_station_by_name`,
  `async_get_station_by_code`, `async_get_train_stops`, etc. Keep parameter names
  close to the original for traceability, but they must all be `async def` and use
  `await session.get(...)` with an explicit timeout via `aiohttp.ClientTimeout`.
- Parse responses with `defusedxml`.
- Return typed `dataclass` (or `TypedDict`) models, not bare `dict`s — e.g.
  `@dataclass` `Station`, `TrainDueTime`, `TrainMovement` — so downstream code and
  tests get type checking.
- Raise the typed exceptions described above on non-200 responses, timeouts,
  connection errors, and XML parse failures — never return an empty list to mean
  "something went wrong."

### `config_flow.py`
- `class IrishRailConfigFlow(ConfigFlow, domain=DOMAIN)`, `VERSION = 1`.
- `async_step_user`: present a form to search/select a station (satisfies
  `config-flow`). Validate the entered station by calling the API client
  (`test-before-setup`) inside `async_step_user`, catching the typed exceptions from
  `api.py` and mapping them to form errors (`errors["base"] = "cannot_connect"`, etc.)
  rather than letting exceptions propagate.
- Call `await self.async_set_unique_id(...)` using the station code (a stable,
  API-assigned identifier, not the free-text station name) and
  `self._abort_if_unique_id_configured()` before creating the entry — this satisfies
  `unique-config-entry`.
- `async_create_entry(title=<station name>, data={...})`.
- Add a full docstring on the class and each step method.

### `coordinator.py`
- `class IrishRailDataUpdateCoordinator(DataUpdateCoordinator[<YourDataType>])`.
- `update_interval = timedelta(...)` from `const.py`.
- `_async_update_data` calls the injected `IrishRailClient`, catches the typed
  exceptions, and re-raises as `UpdateFailed` with a clear message — never lets a raw
  exception bubble into the coordinator's polling loop.
- Store the client/coordinator via `entry.runtime_data` (the modern
  `ConfigEntry.runtime_data` pattern), not `hass.data[DOMAIN][entry.entry_id]` — this
  satisfies `runtime-data`.

### `__init__.py`
- `PLATFORMS: list[Platform] = [Platform.SENSOR]`.
- `async_setup_entry`: build the shared `aiohttps` session via
  `async_get_clientsession(hass)`, construct `IrishRailClient`, construct the
  coordinator, call `await coordinator.async_config_entry_first_refresh()` (this is
  what actually satisfies `test-before-setup` at runtime, in addition to the
  config-flow-time check), store it on `entry.runtime_data`, then
  `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)`.
- `async_unload_entry`: standard unload via
  `hass.config_entries.async_unload_platforms`.
- Full type hints; return `bool`.

### `entity.py`
- A shared `IrishRailEntity(CoordinatorEntity[IrishRailDataUpdateCoordinator])`
  base class.
- Set `_attr_has_entity_name = True` on the base class (satisfies `has-entity-name`).
- Populate `_attr_device_info` with at least `identifiers`, `name`, `manufacturer`
  ("Iarnród Éireann / Irish Rail"), and `entry_type = DeviceEntryType.SERVICE` since
  there is no physical device.

### `sensor.py`
- `async_setup_entry` reads the coordinator from `entry.runtime_data` and creates one
  or more `IrishRailDueTrainSensor(IrishRailEntity, SensorEntity)` entities per
  configured station (e.g. next train due, minutes late, destination).
- Every entity sets a stable `_attr_unique_id` built from the config entry's unique
  ID plus a static per-sensor suffix (e.g. `f"{entry.unique_id}_next_due"`) — never
  derived from user-visible/mutable text (satisfies `entity-unique-id`).
- Use `_attr_translation_key` per entity (paired with `strings.json`/`translations`)
  instead of hardcoded English names, so names are translatable and consistent with
  `has-entity-name`.
- Set appropriate `device_class` / `state_class` / `native_unit_of_measurement`
  where applicable (e.g. a "minutes due" numeric sensor with `state_class:
  measurement`, unit "min").
- Subscribe to coordinator updates only inside `async_added_to_hass` via
  `self.async_on_remove(self.coordinator.async_add_listener(...))` if you don't rely
  purely on `CoordinatorEntity`'s built-in wiring — do this correctly either way
  (satisfies `entity-event-setup`).

### `strings.json` + `translations/en.json`
- Config flow step titles/descriptions/field labels and error messages
  (`cannot_connect`, `invalid_station`, `unknown`).
- Entity translation keys matching what you set in `sensor.py`.

### `quality_scale.yaml`
- List every Bronze rule from the official rules page and mark each with its
  official underscore-separated rule key (e.g. `config_flow`, `docs_high_level_description`,
  `docs_advanced_setup`). Use the exact key names from the rules page — do not use
  hyphenated keys such as `config-flow` or `docs-high-level-description`.
- Mark each rule `status: done` with a one-line pointer to the file/function that
  satisfies it (mirror the format used by core integrations).
- For any Bronze rule that is genuinely not applicable to this integration (e.g. if
  there truly are no custom service actions), mark it `status: exempt` with a
  one-sentence `comment:` justifying the exemption — do not mark anything `exempt`
  just to avoid implementing it.
- If implementation work for a rule has not yet begun and there is no evidence of
  progress, keep that rule `status: pending` until an implementation commit or file
  change provides the required evidence. Do not skip rules or leave them `pending`
  indefinitely without a clear plan.

### `pyproject.toml`
- Replace `setup.py`/`setup.cfg`/`requirements.txt` entirely.
- PEP 517/621 metadata, `requires-python = ">=3.14.2"` — use Home Assistant Core 2026.8.1
  metadata as the source of truth for the minimum Python version.
- Dev dependencies: `pytest`, `pytest-homeassistant-custom-component`, `pytest-asyncio`,
  `ruff`, `mypy`, `defusedxml`.
- `[tool.ruff]` and `[tool.mypy]` sections configured close to HA core's own
  `pyproject.toml` conventions (strict-ish mypy, ruff rule set covering at least
  pyflakes/pycodestyle/bugbear/async lint rules).

### Tests (`tests/components/irish_rail/`)
- Use `pytest-homeassistant-custom-component` fixtures (`hass`, `MockConfigEntry`,
  `aioclient_mock` or `aresponses`/`respx` for mocking the Irish Rail HTTP endpoint).
- `test_api.py`: unit tests for `api.py` against saved XML fixture files (reuse/adapt
  the original repo's test fixtures), covering success, HTTP error, timeout, and
  malformed-XML paths.
- `test_config_flow.py`: cover the happy path, `cannot_connect`, invalid station, and
  duplicate-entry abort (`unique-config-entry`) — this is what the `config-flow` and
  duplicate-prevention Bronze rules are graded on.
- `test_init.py`: `async_setup_entry` success and `ConfigEntryNotReady`/failure path
  when the coordinator's first refresh fails.
- `test_coordinator.py`: successful update and `UpdateFailed` path.
- All tests must be `async def test_...` using `pytest.mark.asyncio` (or the HA
  pytest plugin's auto-async handling) — no blocking calls in tests either.
- Do not chase 100% coverage for this pass, but every new public function/branch in
  `api.py`, `config_flow.py`, `coordinator.py`, and `__init__.py` needs at least one
  test, since "automated tests that guard this integration can be configured
  correctly" is itself a Bronze characteristic.

### `.github/workflows/ci.yml`
- Replace `.travis.yml`. On push/PR: set up Python (matrix on the single currently
  supported HA core Python version), install `pyproject.toml` dev deps, run `ruff
  check`, `mypy custom_components/irish_rail`, and `pytest --cov`.

### `hacs.json`
- Minimal HACS manifest (`name`, `content_in_root: false`, `render_readme: true`) so
  the result is installable as a HACS custom repository while the Bronze work is
  validated, ahead of any eventual core PR.

## Working method

1. First, fetch the live Bronze rules list and paste the exact rule IDs you are
   targeting before writing code, flagging any discrepancy with the list implied by
   this prompt.
2. Produce the files in this order: `const.py` → `api.py` → `coordinator.py` →
   `config_flow.py` → `__init__.py` → `entity.py` → `sensor.py` → `strings.json`/
   translations → `manifest.json` → `quality_scale.yaml` → tests → `pyproject.toml` →
   CI workflow → `hacs.json` → `README.md`. This order lets later files depend
   correctly on earlier ones.
3. **Brand/branding validation:** Before marking the integration as complete, verify that
    the local branding assets in `custom_components/irish_rail/brand/` comply with
    Home Assistant's branding requirements (see HA developer docs for the current icon/logo
    specifications). At minimum, confirm each asset file exists, is a valid PNG image, has
    the correct dimensions (72×72 px for icon.png and small.png, 168×168 px for logo.png), and has a
    transparent background (no fully opaque background pixels). If any asset is
    missing or non-conforming, create placeholder files that meet the dimensions and format
    requirements rather than leaving the directory empty.
4. After producing all files, self-review against the full Bronze checklist rule by
    rule and state explicitly, rule by rule, how each one is satisfied — this
    self-review is what goes into `quality_scale.yaml`.
5. Do not invent Irish Rail API behavior you're not sure of — if a field's exact
    semantics (e.g. whether `Duein` can be negative, or how cancellations are
    represented) is unclear from the original library/tests, say so explicitly rather
    than guessing silently, and add a defensive check + test for the uncertain case.
6. Flag anything you could not fully complete (e.g. you were unable to verify the
    live HA Python version, or unsure whether the upstream API supports HTTPS) as an
    explicit TODO in both the code comments and your final summary — do not mark a
    Bronze rule `done` in `quality_scale.yaml` if you're not confident it's actually
    satisfied.

### `README.md`
Rewrite completely. Must include, in this order:

1. High-level description of what the integration does and which service it talks to
   (`docs-high-level-description`).
2. Step-by-step installation instructions covering both HACS install and manual
   `custom_components` copy, including any prerequisites (none expected, since the
   Irish Rail API needs no API key — confirm this and state it explicitly)
   (`docs-installation-instructions`).
3. Configuration instructions: how to add via **Settings → Devices & Services → Add
   Integration → Irish Rail**, what the station-search form asks for.
4. What entities are created and what they represent.
5. Removal instructions: how to remove the integration from the UI and, if
   applicable, any manual cleanup steps (`docs-removal-instructions`).
6. A "Known limitations" section (e.g. unofficial API, no authentication, plain-HTTP
   upstream if that turns out to be the case, Ireland-only).

Additionally, include explicit, separate sections for the following documentation
treatments, each describing the Bronze-required documentation rules and how they
apply to this integration:

- **`docs-actions`**: Document all observable user-facing actions (e.g. adding a
  station, changing direction filter, removing the entry) with their expected
  effects on entities, state changes, and any side-effects. The Irish Rail
  integration has limited user actions (only *Add* and *Remove*), but the README
  must still describe both in the context of the `docs-actions` rule.

- **`docs-triggers`**: Document the events that cause the integration to run or
  refresh data. The coordinator runs at `DEFAULT_SCAN_INTERVAL` (1 minute per
  `const.py`), triggered by Home Assistant's background update loop on config
  entry load and reload. The README must explain this periodic trigger and any
  manual triggers (e.g. reload).

- **`docs-conditions`**: Document the conditions under which the integration
  operates normally versus degraded or unavailable states. The integration
  handles `UpdateFailed` for API errors (raises `ConfigEntryNotReady` on first
  refresh failure), XML parse errors, and connection/timeout failures. The README
  must describe what happens to entities under each condition and how the user
  is notified.
