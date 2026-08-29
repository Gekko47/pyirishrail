# pyirishrail

Async client library for the **Irish Rail Realtime Passenger Information
(RTPI) API**. The Home Assistant
[`irish_rail`](https://github.com/Gekko47/pyirishrail) custom integration
re-consumes this package as an external dependency declared in its
`manifest.json`.

```python
from pyirishrail import IrishRailClient, IrishRailError
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

## Why this package is separate from the integration

The Platinum-tier `async-dependency` rule requires every declared
dependency in the integration's `manifest.json` to itself be async.
Before the extraction, the integration's client code lived in
`custom_components/irish_rail/api.py` and could not be expressed as a
`requirements:` entry, so the integration's HTTP stack was inline.
Splitting the client out into this PyPI package makes the dependency
boundary honest: the integration now declares `pyirishrail>=0.2,<1.0`
exactly the way it would declare `aiohttp` or `voluptuous`, and the
platinum rule is satisfied at the manifest level.

The library lives in this same repository (as a top-level sibling of
`custom_components/`) rather than a separate GitHub repo, by the layout
decision recorded in the project roadmap (revised 2026-08-27). The
package is still published to PyPI by CI and re-consumed as an
external dependency, so the dependency boundary is identical to a
truly external library in production.

## Public API

The package exports a deliberately small surface:

| Symbol | Kind | Notes |
|---|---|---|
| `IrishRailClient` | class | Async client; takes an injected `aiohttp.ClientSession`. |
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
deliberately via `from pyirishrail.api import _scoped_journey_stops`;
non-HA consumers should prefer the public API.

## `defusedxml` and the Platinum `async-dependency` rule

The Platinum `async-dependency` rule states that every declared
dependency must be async, and the rule has no exceptions clause. The
client declares two runtime dependencies: `aiohttp` and `defusedxml`.

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

## Type checking

The package ships [`py.typed`](https://peps.python.org/pep-0561/) and
is built with strict mypy:

```bash
pip install -e ".[dev]"
mypy pyirishrail
```

All public symbols are typed and mypy strict passes clean on every
release.

## Building the wheel

```bash
python -m build --wheel
```

The wheel is named `pyirishrail-<version>-py3-none-any.whl` and
contains only the `pyirishrail/` package plus the `py.typed` marker.
The Home Assistant integration under `custom_components/` is
intentionally **not** packaged — HACS loads it from the directory
layout on disk, not from `pip`.

## Testing

```bash
pytest tests/pyirishrail --cov=pyirishrail --cov-fail-under=95
```

The library suite has no Home Assistant dependency and no external
network access; `aresponses` is used to mock the RTPI XML responses
in-process.
