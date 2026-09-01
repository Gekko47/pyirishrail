# Skill 02 — Architecture, Async I/O, and Safe Parsing

## Core rule

Never perform blocking network I/O in the Home Assistant event loop.

For HTTP:
- use async HTTP
- use Home Assistant's shared client session where appropriate
- inject the session into the standalone client
- use explicit request timeouts
- translate low-level failures into integration-specific typed exceptions

Home Assistant documents injected aiohttp sessions here:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/inject-websession/

## Standalone client

`api.py` should contain a framework-agnostic client.

It must NOT import:
- `homeassistant.*`
- `hass`
- Home Assistant registries
- Home Assistant entity classes

It SHOULD contain:
- typed dataclasses
- endpoint construction
- async HTTP
- XML parsing
- exception hierarchy
- normalization/validation of upstream values

Suggested exception hierarchy:

IrishRailError
- IrishRailConnectionError
- IrishRailTimeoutError
- IrishRailHTTPError
- IrishRailParseError
- IrishRailInvalidResponseError

Use exceptions to distinguish failure modes. Never return an empty list merely because the server failed.

## HTTP behavior

Use an injected `aiohttp.ClientSession`.

Every request should:
- use `await session.get(...)`
- have an explicit `aiohttp.ClientTimeout`
- validate HTTP status
- read/parse the response safely
- convert aiohttp exceptions to typed integration exceptions

Do not:
- instantiate a session per request
- use `requests`
- use `asyncio.to_thread` as a way to keep the legacy blocking client
- hide connection failures

## XML security

Do not use:
- `xml.dom.minidom`
- standard XML parsing patterns that leave the integration exposed to entity expansion/XXE concerns

Use `defusedxml.ElementTree` or another currently supported defusedxml parser.

Treat XML as hostile/untrusted input.

Test:
- valid XML
- malformed XML
- missing expected elements
- unexpected/malformed values

## Typed data model

Prefer immutable or straightforward dataclasses.

Example concepts:
- `Station`
- `TrainDueTime`
- `TrainMovement`
- `TrainStop`
- aggregate coordinator data model

Do not leak raw XML nodes or untyped dictionaries into Home Assistant entities.

Use optional fields where upstream data can be absent.

## Parsing rules

Parsing should:
- normalize whitespace
- handle absent elements
- validate types
- preserve upstream meaning
- avoid guessing

For time handling, determine whether a field represents:
- scheduled time
- expected time
- actual time
- due minutes

Do not convert values to timestamps until the semantic meaning is established.

## Dependency principle

The client should be independently unit-testable. Tests for `api.py` should not require a running Home Assistant instance.

## Home Assistant boundary

The integration boundary is:

Home Assistant
  -> config flow
  -> client creation
  -> coordinator
  -> entities

The client should not know about any of these layers.

## Current HA guidance

Common modules such as a coordinator and base entity belong in:
- `coordinator.py`
- `entity.py`

See:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules/

And:
https://developers.home-assistant.io/docs/integration_fetching_data/

## Roadmap-driven architecture work

The improvement roadmap adds these engineering concerns. Apply the
same async, typed, framework-agnostic principles to each.

**The long-form design notes for every non-trivial invariant in the
integration live in `docs/architecture.md`.** When a docstring in
source carries design history, lift it into that file and keep the
source docstring short (contract-only). The streamline roadmap's
Phase A and Skill 10 §1 govern this discipline.

### Adaptive backoff polling (roadmap 4.3)

- On consecutive update failures, back the coordinator interval off
  exponentially (cap ~15 minutes); restore the configured interval
  immediately on success.
- Implement by adjusting `update_interval` inside the coordinator's
  success/failure paths; keep the configured base interval in one place so
  the options flow (1.2) and backoff logic do not fight each other.
- Tests must simulate failure streaks and recovery.

### Conditional requests (roadmap 4.2)

- Probe the Irish Rail API for `ETag` / `Last-Modified` support before
  implementing anything.
- If supported: send validators on requests, skip parsing on HTTP 304, and
  record the evidence in the roadmap file.
- If not supported: record the finding in the roadmap and close the item.
- Never treat a 304 as an error.

### XML layer hardening (roadmap 4.4)

- Normalize namespaces once at parse time; remove dual namespace-or-not
  `findall` fallbacks from `api.py`.
- Re-run the full suite after refactoring; coverage must not regress below
  95%.

### Client extraction (roadmap Phase 5.3, Platinum path)

- The `pyirishrail` package lives as a top-level sibling of
  `custom_components/` in **this** repository (revised 2026-08-27; previous
  plans put it in a separate repo). It is built as a wheel and published to
  PyPI from this repo's CI, then re-consumed by the integration as an
  external dependency in `manifest.json`. No second GitHub repo, no
  duplicated CI.
- When moving `api.py` into the `pyirishrail/` package, keep this skill's
  rules intact: no Home Assistant imports, injected session, explicit
  timeouts, typed exceptions, safe parsing.
- The integration's CI installs the published wheel (from TestPyPI on PRs,
  PyPI on release branches) before running the integration tests, so the
  dependency boundary is honest in CI as well as at install time.
- See also Skill 07 for the manifest/Pyproject/CI side of this transition.
