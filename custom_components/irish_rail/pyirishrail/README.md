# pyirishrail

Async client library for the **Irish Rail Realtime Passenger Information
(RTPI) API**, bundled inside the `irish_rail`
[Home Assistant](https://github.com/Gekko47/pyirishrail) custom
integration as `custom_components/irish_rail/pyirishrail/`.

```python
from custom_components.irish_rail.pyirishrail import (
    IrishRailClient,
    IrishRailError,
)

# In real HA code the session comes from
# ``async_get_clientsession(hass)``; the example uses a stand-alone
# session for clarity.
import aiohttp

async with aiohttp.ClientSession() as session:
    client = IrishRailClient(session)
    try:
        trains = await client.async_get_station_by_code("PEARS")
        for train in trains:
            print(train.due_in_mins, train.destination, train.late_mins)
    except IrishRailError as err:
        # Typed failures: connection, timeout, or parse.
        ...
```

## Why this package is bundled inside the integration

The package is internal to the integration. The Platinum-tier
`async-dependency` rule requires every declared dependency in
`manifest.json` to be async, and the integration declares **none**:
transport is `aiohttp` (Home Assistant core), XML parsing is the
standard library, and the rest of the client is framework-agnostic
Python. The PyPI name `pyirishrail` is owned by an unrelated project
— the v0.3.0 Clean Baseline therefore vendors the client at
`custom_components/irish_rail/pyirishrail/`, where it ships with
`py.typed` (PEP 561) and is exercised by the same suite as the
integration.

## Public API

The package exports a deliberately small surface:

| Symbol | Kind | Notes |
|---|---|---|
| `IrishRailClient` | class | Async client; takes an injected `aiohttp.ClientSession` and an optional shared `RequestGate`. |
| `RequestGate` | class | Concurrency-and-pacing gate. The integration passes one gate per `HomeAssistant` instance to every client it creates (see `custom_components/irish_rail/gate.py`). |
| `IrishRailError` | exception | Base class. |
| `IrishRailConnectionError` | exception | Network / non-200 responses. |
| `IrishRailTimeoutError` | exception | `aiohttp` / `asyncio` timeouts. |
| `IrishRailParseError` | exception | XML parse errors (also raised by the pre-parse DTD/entity guard). |
| `Station` | frozen dataclass | Station metadata. |
| `TrainDueTime` | frozen dataclass | Due-train record. |
| `TrainMovement` | frozen dataclass | Train route / movement record. |
| `TrainPosition` | frozen dataclass | Real-time position record. |
| `parse_station_data` | function | Pure parser; unit-testable without a session. |
| `DEFAULT_TIMEOUT` | constant | The 10 s per-request timeout used internally. |

Private helpers in `pyirishrail.api` (prefixed with `_`, e.g.
`_scoped_journey_stops`) are intentionally **not** re-exported. The
integration's stops-matrix rebuild button reaches into one of them
deliberately via
`from custom_components.irish_rail.pyirishrail.api import _scoped_journey_stops`;
non-HA consumers should prefer the public API.

## Zero-dependency XML

The Platinum `async-dependency` rule has no exceptions clause, and the
client declares no third-party dependencies. XML parsing is performed
by Python's standard library `xml.etree.ElementTree` on bytes already
fetched by `aiohttp`. On the Home Assistant 2026.8 floor (Python
3.14.2's bundled expat 2.7.5) `ET.fromstring` already rejects entity
declarations and external-entity resolution with `ParseError`; the
one gap the stdlib parser leaves open — silently allowing a DTD
*without* entities — is closed by an explicit pre-parse guard
(`pyirishrail/api.py::_DTD_KEYWORDS`) that rejects any document
containing `<!doctype`, `<!entity`, `<!element`, `<!attlist` or
`<!notation` and raises `IrishRailParseError` before the parser is
invoked. The policy is therefore independent of the bundled expat
version and applies uniformly to every consumer of the client.

## The `RequestGate`: one shared admission gate per `HomeAssistant`

The public `api.irishrail.ie` endpoints are shared infrastructure. The
:class:`RequestGate` enforces two coupled limits on every outbound
request a client makes:

* `max_concurrent` — at most N requests in flight at any instant.
* `min_interval_seconds` — minimum wall-clock spacing between two
  consecutive gate exits (the moment an admitted request actually
  starts its HTTP call).

The integration's `gate.py` exposes a single `RequestGate` per
`HomeAssistant` instance, stored on `hass.data[DOMAIN]`, and every
`IrishRailClient` the integration creates (entry setup, both config
flows, rebuild button, health probe) is constructed with that shared
gate. The shared gate gives the integration a single rate budget for
the public API instead of one budget per client, and the
`priority="background"` knob the rebuild uses yields to live polling
on a shared budget.

## Type checking

The package ships `py.typed` (PEP 561). Strict mypy passes clean:

```bash
mypy custom_components/irish_rail tests/components/irish_rail
```

No `# type: ignore` remains in either the integration or the package.

## Testing

```bash
pytest tests/components/irish_rail --cov=custom_components/irish_rail --cov-fail-under=100
```

The package's gate is exercised by
`tests/components/irish_rail/test_client_gate.py` in isolation
(manual clock + injectable sleep, no HTTP) and by
`tests/components/irish_rail/test_client.py` end-to-end through the
client. `aresponses` mocks the RTPI XML responses in-process; the
shared-gate wiring is pinned by
`tests/components/irish_rail/test_gate_sharing.py`.

