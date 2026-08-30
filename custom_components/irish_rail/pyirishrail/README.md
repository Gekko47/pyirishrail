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

The Platinum-tier `async-dependency` rule requires every declared
dependency in the integration's `manifest.json` to itself be async.
Inline client code inside `custom_components/irish_rail/api.py` cannot
be expressed as a `requirements:` entry, so the integration's HTTP
stack was first extracted into a separate `pyirishrail` PyPI package
(roadmap Phase 5.3, implemented 2026-08-28) and then **moved back
inside the integration directory** on 2026-08-29 because the
`pyirishrail` name on PyPI is owned by an unrelated project. Bundling
the client inside the integration keeps HACS installs self-contained
and preserves the `inject-websession` / `async-dependency` Platinum
rules at the integration level: the package still never creates its
own event loop, and the `defusedxml` parser is invoked only on bytes
already fetched by `aiohttp`.

## Public API

The package exports a deliberately small surface:

| Symbol | Kind | Notes |
|---|---|---|
| `IrishRailClient` | class | Async client; takes an injected `aiohttp.ClientSession` and an optional shared `RequestGate`. |
| `RequestGate` | class | Concurrency-and-pacing gate. The integration passes one gate per `HomeAssistant` instance to every client it creates (see `custom_components/irish_rail/gate.py`). |
| `IrishRailError` | exception | Base class. |
| `IrishRailConnectionError` | exception | Network / non-200 responses. |
| `IrishRailTimeoutError` | exception | `aiohttp` / `asyncio` timeouts. |
| `IrishRailParseError` | exception | XML parse / defusedxml security errors. |
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

## `defusedxml` and the Platinum `async-dependency` rule

The Platinum `async-dependency` rule states that every declared
dependency must be async, and the rule has no exceptions clause. The
client uses two runtime libraries: `aiohttp` and `defusedxml`.

* `aiohttp` is the async HTTP client; every network call goes through
  `await session.get(...)`. The session is injected by the caller
  (`inject-websession`), so the library itself does not own the event
  loop.
* `defusedxml` is a **pure XML parser** that performs no network I/O.
  The client fetches the response body with `await response.text()` on
  the injected aiohttp session (the async transport) and then passes
  the already-fetched string to
  `defusedxml.ElementTree.fromstring(...)`. The parser runs in
  microseconds on the small XML documents the RTPI endpoints return
  and never touches the event loop or a socket.

This pattern — async transport, then synchronous parsing on the
already-fetched payload — is the standard one used by Home Assistant
core itself for its XML integrations. The dependency is async at the
transport boundary; only the parser, which is deliberately synchronous
for security reasons, sees the bytes.

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

