# Skill 06 — Testing and CI

## Testing stack

Prefer:
- pytest
- pytest-homeassistant-custom-component
- Home Assistant test fixtures
- aresponse/appropriate async HTTPS mocking supported by the current project
- coverage reporting

Do not use blocking network calls in tests.

Official testing docs:
https://developers.home-assistant.io/docs/development_testing/

## API tests

`test_api.py` should cover:
- successful station response
- successful train response
- successful train-stops response
- HTTPS error
- timeout
- connection error
- malformed XML
- missing XML fields
- malformed field values
- edge cases found in the legacy fixtures

Reuse/adapt the original project's static XML fixtures when their semantics remain valid.

## Config flow tests

`test_config_flow.py` must cover:
- happy path
- API validation success
- connection failure
- invalid station
- unexpected client failure
- duplicate station/config entry
- every abort/error branch

Aim for actual full line/branch coverage of the flow module.

The official Bronze rule requires complete config-flow coverage.

## Coordinator tests

`test_coordinator.py`:
- successful refresh
- client exception -> `UpdateFailed`
- data model correctness
- first refresh failure behavior where appropriate

## Setup tests

`test_init.py`:
- successful setup
- initial refresh failure
- `ConfigEntryNotReady` behavior
- platform forwarding
- runtime_data population

## Entity tests

Even though the requested minimum tree did not list a dedicated entity test file, add entity tests if needed to verify:
- unique IDs
- translated naming
- state values
- unavailable behavior where relevant
- coordinator updates

Do not artificially constrain tests to the old requested tree.

## Test style

Use:
`async def test_...`

Use the HA pytest plugin's current async handling or `pytest.mark.asyncio` only where appropriate to the current environment.

Avoid:
- unittest.TestCase
- `time.sleep`
- synchronous HTTPS
- real external API calls
- test order dependencies

## CI

Run at minimum:
- formatting/lint checks
- mypy/type checks appropriate to the project
- pytest
- coverage

The exact commands should match the actual 2026.8 development environment. Do not assume that an old prompt's Ruff/mypy command is still the canonical Home Assistant command.

## Local validation

After implementation:
1. run unit tests
2. run config-flow coverage
3. run lint
4. run type checking
5. run Home Assistant validation/hassfest where applicable
6. inspect generated integration metadata
7. perform a clean custom-component install test

## Roadmap-driven test requirements

### Reconfigure flow tests (roadmap 1.1)

`test_config_flow.py` additions:
- reconfigure happy path (direction changed, entry data updated)
- `cannot_connect`, `invalid_station`, `unknown` error branches
- abort on update failure; recovery so the user can retry

Full branch coverage of the new step is required.

### Options flow tests (roadmap 1.2)

- valid interval stored in `entry.options` and honored by the coordinator
- out-of-range / non-numeric values rejected by the schema (form re-shown)
- update listener applies the change (reload or in-place, per implementation)

### Next-N-trains tests (roadmap 1.3)

- entities for trains 2–3 with correct states and unique IDs
- fewer-than-N trains available → `None` state or unavailable, no crash
- translation keys resolve

### Empty-vs-error tests (roadmap 1.4)

- API reachable with zero trains → expected state/attribute
  (`api_reachable: true`)
- API failure → coordinator `UpdateFailed`, entities `unavailable`

### Silver-rule tests (Phase 2)

- explicit test that entities report `unavailable` after a failed refresh
  (`entity-unavailable`) — extend the partial coverage in `test_sensor.py`
- unload + reload behavior (`config-entry-unloading`)
- transition logging asserted once per direction (`log-when-unavailable`)

### Backoff tests (roadmap 4.3)

- simulate consecutive failure streaks; assert exponential backoff capped at
  ~15 minutes
- assert immediate restore of the configured interval on recovery

### Diagnostics edge cases (Phase 3)

- redaction edge-case tests in `test_diagnostics.py`

## Coverage gate

- Current gate: ≥90%.
- Raise the gate to ≥95% when Silver-tier work begins (roadmap Phase 2) and
  keep it there as code grows.

## Roadmap-driven CI additions (roadmap 4.5)

- Keep the existing HACS validation job green alongside hacs.yml.
- Add a hassfest-style manifest validation job appropriate for custom
  integrations.
- Consider pytest-xdist for faster CI runs; ensure tests remain
  order-independent before enabling it.

## CI philosophy

CI should fail on:
- lint errors
- type errors
- failing tests
- inadequate config-flow coverage
- invalid manifest/translation structure
- coverage below the active gate

Keep CI deterministic and avoid dependence on the real Irish Rail service.
