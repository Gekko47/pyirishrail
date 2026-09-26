---
name: testing-and-ci
description: Test stack, test layer map, coverage gate, and CI expectations for the irish_rail integration. Load when writing or editing any test, adding a new test file, changing the coverage gate, touching .github/workflows/ci.yml or hacs.yml, debugging a failing or flaky test, or when a question touches "do tests cover this", "what does CI enforce", or the ruff/mypy/pytest commands.
---

# Testing and CI

The test stack, what each layer must cover, and the gates CI enforces.

---

## 1. Test stack

Use:
- pytest
- pytest-homeassistant-custom-component
- Home Assistant test fixtures
- aresponses (async HTTPS mocking)
- coverage reporting

Do not use blocking network calls in tests. Do not make real calls to the
Irish Rail service.

Official testing docs:
https://developers.home-assistant.io/docs/development_testing/

Windows note: `pyproject.toml` sets `addopts = "-p tests/win_stubs"`. The shim
must load **before** the entry-point plugins because
`pytest-homeassistant-custom-component` imports `homeassistant.runner`, which
on Windows pulls in the POSIX-only `fcntl` module. The shim is a no-op on
non-Windows.

---

## 2. Test layers

### Client tests

`test_client.py` covers:
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

Reuse/adapt the original project's static XML fixtures when their semantics
remain valid.

`test_client_gate.py` covers the `RequestGate` primitive's cancellation
safety.

**Patch targets:** the client class lives directly in `client.py`, so the
valid patch path is `custom_components.irish_rail.client.IrishRailClient.X`.
The obsolete `pyirishrail.*` target was retargeted in v0.3.0 Phase 1 and the
sub-package was removed in streamline Phase B1; CI now **fails the build** if
`patch("pyirishrail.…")` reappears in the test suite.

### Config flow tests

`test_config_flow.py` must cover:
- happy path
- API validation success
- connection failure
- invalid station
- unexpected client failure
- duplicate station/config entry
- every abort/error branch

Aim for actual full line/branch coverage of the flow module. The Bronze
`config-flow-test-coverage` rule requires complete coverage and specifically
calls out duplicate-entry coverage.

Additions:
- **Reconfigure flow**: happy path (direction changed, entry data updated);
  `cannot_connect` / `invalid_station` / `unknown` branches; abort on update
  failure; recovery so the user can retry. Full branch coverage of the new
  step.
- **Options flow**: valid interval stored in `entry.options` and honoured by
  the coordinator; out-of-range / non-numeric values rejected by the schema
  (form re-shown); update listener applies the change.

Official rule:
https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-flow-test-coverage/

### Coordinator tests

`test_coordinator.py`:
- successful refresh
- client exception → `UpdateFailed`
- data model correctness
- first refresh failure behaviour
- options-driven interval honoured
- adaptive backoff: simulate consecutive failure streaks, assert exponential
  backoff capped at ~15 minutes, and assert immediate restore of the configured
  interval on recovery

### Setup tests

`test_init.py`:
- successful setup
- initial refresh failure
- `ConfigEntryNotReady` behaviour
- platform forwarding
- `runtime_data` population
- unload + reload behaviour (`config-entry-unloading`)

### Entity tests

- unique IDs
- translated naming
- state values
- unavailable behaviour after a failed refresh (`entity-unavailable`)
- coordinator updates
- defensive reads when fewer trains exist than expected (`None` state or
  unavailable, never crash)
- empty-vs-error: API reachable with zero trains → expected state/attribute
  (`api_reachable: true`); API failure → `UpdateFailed`, entities
  `unavailable`
- transition logging asserted once per direction (`log-when-unavailable`)

### Other tests

- `test_diagnostics.py` — redaction edge cases (`diagnostics`)
- `test_runtime.py` — the `RuntimeRegistry` contract
- `test_health.py` / `test_health_suppression.py` — monitor behaviour and
  suppression
- `test_global_setup_edges.py` — global-entity providership edges
- `test_matrix_rebuild.py` / `test_stops_store.py` — the unified rebuild sweep
  and the matrix store
- `test_translations.py` / `test_icons.py` — translation keys and icon
  alignment resolve

List the directory rather than trusting this list — it drifts. Do not
artificially constrain tests to any historical file list. Add entity test
files where needed.

---

## 3. Test style

Use `async def test_...` and the HA pytest plugin's current async handling
(`asyncio_mode = "auto"` is set in `pyproject.toml`); use
`pytest.mark.asyncio` only where appropriate to the current environment.

Avoid:
- `unittest.TestCase`
- `time.sleep`
- synchronous HTTPS
- real external API calls
- test order dependencies

### Tests pin behaviour, not prose

No tests that pin docstring text (e.g. `assert "by design" in
module.__doc__`). Tests pin observable behaviour: return values, raised
exceptions, side effects on the shared registry.

When a refactor moves code, existing tests follow the move (import paths
update) and continue to pin observable behaviour.

---

## 4. Gates

### Coverage gate

**Current gate: 100% line coverage.** Enforced by CI via
`--cov-fail-under=100` and mirrored in `pyproject.toml`
(`[tool.coverage.report] fail_under = 100`).

The gate does not drop. The streamline roadmap's test phase may reduce the
*count* of tests by merging duplicate cases, but the *coverage* of the source
must not regress. The earlier ≥90% → ≥95% progression is historical; this
project is at 100%.

### The three gates

Every increment must leave all three green:

```bash
# 1. lint
ruff check custom_components/irish_rail tests/components/irish_rail scripts

# 2. types (integration + tests, strict)
mypy custom_components/irish_rail tests/components/irish_rail

# 3. tests + coverage
pytest tests/components/irish_rail tests/test_win_stubs.py \
  --cov=custom_components/irish_rail --cov-fail-under=100
```

mypy runs `strict = true` with `python_version = "3.14"`,
`explicit_package_bases = true`, and
`warn_unused_configs = true`.

### Additional CI gates

Beyond ruff/mypy/pytest, CI enforces two project-specific gates that fail the
build:

1. **Stale patch-target guard** — rejects `patch("pyirishrail.…")` in tests.
2. **Docstring density + project-internal reference gate** — measures
   docstring lines per source LOC across `custom_components/irish_rail` and
   fails above the active threshold; also rejects `Skill N` / `Phase N` /
   `roadmap N.X` cross-references in source (see the roadmap-execution skill
   for the density target and the rationale).

### Local validation sequence

After implementation:
1. run unit tests
2. run config-flow coverage
3. run lint
4. run type checking
5. run Home Assistant validation / hassfest where applicable
6. inspect generated integration metadata
7. perform a clean custom-component install test

---

## 5. CI expectations

Workflows: `.github/workflows/ci.yml` and `.github/workflows/hacs.yml`.

`ci.yml` has two jobs:
- **hassfest** — validates `manifest.json`.
- **integration** — needs hassfest; Python 3.14; installs pytest,
  pytest-asyncio, pytest-cov, aresponses, pytest-homeassistant-custom-component,
  `ruff>=0.16`, mypy; then runs the gates above.

`hacs.yml` validates HACS packaging.

CI should fail on:
- lint errors
- type errors
- failing tests
- inadequate config-flow coverage
- invalid manifest/translation structure
- coverage below the active gate

Keep CI deterministic and independent of the real Irish Rail service.

Historical note: the CI was once a two-job library + integration matrix for
the `pyirishrail` wheel. That is **reverted** — the client is internal and CI is
a single `integration` job.
