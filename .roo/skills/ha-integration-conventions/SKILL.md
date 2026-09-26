---
name: ha-integration-conventions
description: Project context, Home Assistant Quality Scale rules, and packaging conventions for the irish_rail custom integration. Load when touching manifest.json, quality_scale.yaml, pyproject.toml, hacs.json, README.md, strings.json, translations, branding assets, or when any question touches "is this rule satisfied", "which quality tier", "what counts as done", "what must not be reintroduced", or the overall project state. Also load before claiming a quality-scale rule is satisfied or before adding a dependency.
---

# Integration Conventions: Context, Quality Scale, Packaging

Operational knowledge for the `irish_rail` Home Assistant custom integration:
what the project is, what the current Home Assistant Quality Scale rules
require, and what the packaging and documentation surface must look like.

Design invariants live in `docs/architecture.md`. The source code carries the
contract, not the narrative.

---

## 1. Project context

### Mission

The `irish_rail` integration has completed its Bronze migration, the v0.3.0
Clean Baseline, and the pre-v0.3.0 improvement roadmap. The **active** plan is
the Streamline Roadmap (`.cline/streamline-roadmap.md`) — a maintainability
pass that preserves Platinum quality-scale compliance and the 100% line
coverage gate.

Historical origin (context only): https://github.com/ttroy50/pyirishrail

Target integration:
- domain: `irish_rail`
- name: `Irish Rail`
- custom integration, HACS-installable
- tier: **Platinum** (preserved through the streamline work)
- governance: 3-tier — roadmap → skill → architecture doc

### Current state

- Async client is integration-internal at
  `custom_components/irish_rail/client.py` (framework-agnostic, no Home
  Assistant imports). There is **no PyPI package**: the name `pyirishrail` is
  owned by an unrelated project. The Phase 5.3 extraction was reverted
  2026-08-29 and the v0.3.0 Clean Baseline keeps the client internal.
- Zero third-party runtime dependencies: `manifest.json` `requirements: []`.
  XML parsing is stdlib `xml.etree.ElementTree` with an explicit pre-parse
  DTD/entity guard.
- One `DataUpdateCoordinator` per config entry + `entry.runtime_data` +
  first-refresh fail-fast; set-based entry lifecycle keeps the gate and health
  probe idempotent across retries.
- Config flow with cached station fetch, direction / stops-at / train-count
  filters, unique-ID duplicate protection, reconfigure flow (identity
  preserved on same-direction reconfigure).
- Consolidating per-station sensors into a rich sensor; train detail lives on
  the device's `extra_state_attributes`.
- One integration-level "Irish Rail Services" device with the connectivity
  binary sensor and the stops-matrix rebuild button.
- Diagnostics module with partially-masked identifiers; brand assets
  conforming.
- CI gate on Python 3.14: ruff + strict mypy + pytest at 100% line coverage.

### Superseded: pre-baseline state (historical only)

> **Do not treat this as current.** Retained because roadmap items and older
> commits reference it. The numbers below describe the repo *before* the
> v0.3.0 Clean Baseline and *before* the streamline pass, and several are now
> wrong (sensor count, test count, coverage floor).

- 4 sensors per station/direction with translation keys and stable unique IDs
- 4 sensors per station/direction, train type on `extra_state_attributes`
- Test suite: 36 tests, 99.23% coverage, no deprecation/async warnings
- CI gate: pytest ≥90% coverage
- Async client vendored at `custom_components/irish_rail/pyirishrail/`
- Async client extracted to a top-level `pyirishrail/` package and published
  to PyPI as an external `manifest.json` requirement
- `gate.py` + `health.py` as two separate singleton-management modules
- Bundled `stops_matrix.seed.json` in the tree (~247 KB as of this writing;
  the roadmap target is 0 — generate at release)
- Two stops-matrix rebuild implementations (script + button)
- README ~370 lines; `quality_scale.yaml` 21 KB / 426 lines

### Legacy implementation details that MUST NOT be reintroduced

- `requests`
- blocking I/O
- `xml.dom.minidom`
- untyped plain dictionaries
- swallowed exceptions
- old unittest-only architecture
- Travis CI
- `setup.py` / `requirements.txt` as the primary packaging model

### Required architectural direction (unchanged)

- a standalone async, typed Irish Rail client
- safe XML parsing
- Home Assistant shared web session
- one DataUpdateCoordinator per config entry
- `ConfigEntry.runtime_data` for runtime objects
- modern config flow (extended with reconfigure/options flows)
- modern entities and translations
- pytest-based tests
- modern CI
- HACS metadata
- `quality_scale.yaml`

### Do not invent upstream semantics

If Irish Rail XML fields have uncertain meanings, inspect the original
fixtures/tests and the actual API behaviour. Never silently guess:

- negative `Duein`
- cancellation markers
- missing fields
- date formats
- direction values
- station code semantics
- HTTPS availability

When uncertain:

1. state the uncertainty,
2. implement defensive parsing,
3. add a test,
4. add a TODO if the uncertainty cannot be resolved.

### Scope discipline

New work is governed by the **Streamline Roadmap**
(`.cline/streamline-roadmap.md`). It is the only source of truth for what
is planned, in what order, and what is already done — read it rather than
relying on any phase summary copied here, which drifts out of date as
increments land. The roadmap carries:

- **Phases A–E** — the maintainability pass: docstrings (A), module
  consolidation (B), sensor consolidation (C), cosmetics (D), test
  deduplication (E).
- **Phase F** — correctness remediation from the 0.4.0 audit: options-flow
  `stops_at` preservation and dropdown merge, malformed `Duein` handling,
  reconfigure registry migration, the `RuntimeRegistry` single-writer
  invariant with a CI grep gate, the public
  `async_set_update_interval` migration, and the test-only and repository
  hygiene tracks. Phase F's decisions are recorded as S9–S13 in the
  roadmap's decision table.

Platinum compliance is preserved through every phase: no `done` or `exempt`
rule loses its file/function pointer. The 100% line coverage gate does not
drop. Do not implement items outside the active streamline roadmap without
recording them there.

---

## 2. Quality Scale authority

### Always verify the current rules

Fetch these before implementing or claiming compliance:

- https://developers.home-assistant.io/docs/core/integration-quality-scale/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/

**Never answer a Quality Scale question from memory or from a copied
historical checklist.** The rules change between releases. For a 2026.8-era
migration, use the rules live for the 2026.8 documentation.

If the live documentation conflicts with anything written here, the live
documentation wins — and record the discrepancy.

### Current Bronze rule IDs

The published Bronze checklist contains:

1. `action-setup`
2. `appropriate-polling`
3. `brands`
4. `common-modules`
5. `config-flow-test-coverage`
6. `config-flow`
7. `dependency-transparency`
8. `docs-actions`
9. `docs-triggers`
10. `docs-conditions`
11. `docs-high-level-description`
12. `docs-installation-instructions`
13. `docs-removal-instructions`
14. `entity-event-setup`
15. `entity-unique-id`
16. `has-entity-name`
17. `runtime-data`
18. `test-before-configure`
19. `test-before-setup`
20. `unique-config-entry`

`config-flow` also requires:
- useful `data_description`
- correct use of `ConfigEntry.data`
- correct use of `ConfigEntry.options`

### Rule handling

For every rule:
- `done` only when implemented **and** tested where applicable
- `exempt` only when the official rule permits exemption **and** the
  integration genuinely does not provide that feature
- never use `exempt` as a shortcut
- include a concise file/function pointer

Never write `rule-id: done — probably satisfied`. If something cannot be
verified, mark it unresolved rather than falsely claiming compliance.

### Important consequences for Irish Rail

**`action-setup` / `docs-actions` / `docs-triggers` / `docs-conditions`**
If Irish Rail exposes no service actions, triggers, or conditions, determine
the official rule's exemption semantics and document the exemption. Do not
invent useless services solely to make a checklist item appear implemented.

**`appropriate-polling`**
Irish Rail is a polling integration. Choose an interval based on upstream
data freshness and service/API load. A one-minute interval is a reasonable
candidate only if validated against current API behaviour and rate
expectations.

**`brands`**
Current Bronze includes branding assets. Add the appropriate `brand/`
directory assets and validate them using current Home Assistant branding
guidance. Do not invent logos or use copyrighted assets without permission.

**`config-flow-test-coverage`**
Stronger than "there are some config flow tests". The config flow must have
full coverage, including error recovery and duplicate-entry behaviour.

**`dependency-transparency`**
Every non-core dependency must be declared transparently. Do not list
dependencies already provided by Home Assistant core.

**docs rules**
Documentation must satisfy the applicable rule, not merely contain generic
prose. At minimum cover: what the service is, installation/prerequisites,
removal, any actual actions/triggers/conditions, any applicable
configuration information.

**`entity-event-setup`**
Subscriptions must be attached to the correct entity lifecycle. Prefer
`CoordinatorEntity`'s normal wiring rather than hand-rolled listeners unless
there is a real need.

**`entity-unique-id`**
IDs must be stable and independent of mutable user-visible text.

**`has-entity-name`**
Use `_attr_has_entity_name = True` and the current naming/translation model.

**`runtime-data`**
Runtime client/coordinator objects belong in `ConfigEntry.runtime_data`, not
`hass.data[DOMAIN][entry_id]`.

**`test-before-configure`**
The config flow must make a real validation call before creating the entry.

**`test-before-setup`**
Initial setup must verify the service can be reached and the configured
resource initialized. Failure should result in the appropriate
setup-not-ready behaviour rather than a half-configured integration.

**`unique-config-entry`**
Use a stable upstream identifier such as station code. Never use station
display name as the unique ID if it can change.

### Bronze vs stronger tiers

Do not confuse these. The project implements several stronger-tier practices;
keep them, but label them accurately in review documentation.

| Tier | Character |
|---|---|
| **Bronze** | baseline requirements |
| **Silver** | robustness, ownership, unavailability, coverage |
| **Gold** | devices, diagnostics, discovery, extensive docs, translations, icons, repair issues |
| **Platinum** | async dependency, injected web session, strict typing |

**Silver rules** (targeted by the improvement roadmap — verify each against
the live rules pages before implementing):
- `parallel-updates` — declare `PARALLEL_UPDATES` in the sensor platform;
  justify the value.
- `log-when-unavailable` — exactly one log line on transition to unavailable
  and one on recovery; make coordinator behaviour explicit and tested.
- `entity-unavailable` — explicit test asserting entities report
  `unavailable` after a failed refresh.
- `config-entry-unloading` — verify/document unload+reload with a test.
- `test-coverage` — coverage gate raised to ≥95% (this project went further,
  to 100%).

**Gold rules:**
- `reconfiguration-flow` — `async_step_reconfigure` changing the direction
  filter in place, station fixed.
- `docs-examples` / `docs-use-cases` / `docs-troubleshooting` — README
  automation examples, use cases, troubleshooting sections.
- `icon-translations` — `icons.json` with per-entity icons aligned to
  translation keys.
- `exception-translations` — user-facing errors become translation-keyed
  `HomeAssistantError`s.
- `repair-issues` — raise a repair issue for persistently empty station data
  during service hours.
- `diagnostics` — redaction edge-case tests.
- `entity-device-class` — re-confirm device classes as entities grow.

**Platinum path:**
- `async-dependency` / `inject-websession` — satisfied by local code. The
  earlier plan to publish `pyirishrail` to PyPI is **reverted**; the client
  stays internal. See §3.

For every rule touched by a roadmap item: update `quality_scale.yaml` status,
include a file/function pointer, and only mark `done` with implementation
plus tests.

### Contradictions to catch

These are known traps where an older project prompt disagrees with current
Home Assistant reality. Verify each against live docs; the right-hand answer
is the current one.

| Prompt claim | Reality |
|---|---|
| Python `>=3.13` | Current HA dev metadata is at `>=3.14.2`. Verify the exact 2026.8 release requirement. Do not claim 2026.8 compatibility from the dev branch alone. |
| File tree omits `brand/` | Current Bronze contains `brands`. The target tree must be expanded. |
| Strict typing and async dependency are Bronze | These are **Platinum**-level rules. Good engineering, but do not present them as Bronze. |
| Device info is a Bronze requirement | `devices` is **Gold**. Do not claim device creation is required for Bronze. |
| Diagnostics are optional | Consistent — `diagnostics` is Gold, not Bronze. |
| 100% config-flow tests | **Correct** and mandatory for Bronze. |

Also: do not create a fake physical device just to satisfy a checklist. Device
creation is a stronger-quality concern than merely having entities.

---

## 3. Manifest, dependencies, packaging, docs

### Manifest

`custom_components/irish_rail/manifest.json` must have current, valid values
for: `domain`, `name`, `config_flow`, `quality_scale`, `iot_class`,
`integration_type`, `requirements`, `codeowners`, `documentation`,
`issue_tracker`, `version`.

Verify each field against the current manifest documentation before finalizing:
https://developers.home-assistant.io/docs/creating_integration_manifest/

Current values: `quality_scale: platinum`, `iot_class: cloud_polling`,
`integration_type: service`. The `version` field is release metadata
owned by the maintainer at release time — read it from
`manifest.json` rather than copying it from here, so this skill cannot
go stale.

### Dependencies

`aiohttp` is provided by Home Assistant core. Do not list it as an integration
requirement merely because the client imports it.

**Active policy: zero third-party runtime dependencies.** `requirements: []`.
XML parsing is stdlib `xml.etree.ElementTree` with an explicit pre-parse
DTD/entity guard — no third-party XML parser is required on the Home
Assistant 2026.8 floor.

The `defusedxml` question is **closed**: the pre-parse guard with its
`defusedxml` rationale is preserved as historical context, but the active
policy is stdlib-only with an explicit guard. If you are tempted to add
`defusedxml`, that is a roadmap-level decision, not a local one.

### Python version

- target the exact Python range supported by the target HA release
- do not target an older Python than HA
- do not claim 2026.8 compatibility from the dev branch alone

This project targets **3.14** (`pyproject.toml` `python_version = "3.14"`,
CI `python-version: "3.14"`).

### pyproject.toml

Use modern PEP 621 metadata. Do not retain `setup.py`, `setup.cfg`, or
`requirements.txt` unless there is a demonstrated compatibility reason.

**This project keeps `pyproject.toml` tooling-only**: `[tool.coverage.report]`,
`[tool.mypy]`, `[tool.pytest.ini_options]`. It does not build a wheel — the
integration is consumed by HACS from the directory layout, not from `pip`.

Dev dependencies should reflect actual tools used. Do not copy Home
Assistant core's complete dependency set into the custom repository.

`pytest.ini_options` carries a load-bearing detail: `addopts = "-p
tests.win_stubs"`. The `win_stubs` shim must load **before** the entry-point
plugins because `pytest-homeassistant-custom-component` imports
`homeassistant.runner`, which on Windows pulls in the POSIX-only `fcntl`
module. Do not remove it.

### HACS

Keep HACS metadata minimal and valid for a custom integration. Validate `name`,
`content_in_root`, `render_readme` against the current HACS schema.

### Code owners

Do not leave a fake GitHub handle. If the real handle is unknown: use an
explicit placeholder, add a TODO, and do not claim the repository is
production-ready. (Current value: `@Gekko47`.)

### README

Bronze minimum:
1. high-level integration/service description
2. installation
3. prerequisites
4. UI configuration
5. entities provided
6. removal
7. relevant limitations

Do **not** claim — unless verified against current upstream documentation:
- that API authentication is absent
- that the API is HTTPS
- that the API is official/unofficial
- a particular rate limit

Whether the Irish Rail API needs an API key must be verified from current
upstream/API documentation, not guessed.

Beyond the Bronze minimum (Gold docs rules), the README also carries:
- **Automation examples** (`docs-examples`): departure alert, delay
  notification.
- **Use cases** (`docs-use-cases`): commuter dashboard, delay alerts,
  presence-based departure reminders.
- **Troubleshooting** (`docs-troubleshooting`): API downtime, empty data at
  night, retry states.
- **Configuration**: the options-flow scan interval and reconfigure-flow
  behaviour.
- **Entities**: updated whenever new entities are added.

The streamline roadmap targets ~180 lines from the current ~370.

### quality_scale.yaml

Should list every Bronze rule. For each: `done` with a pointer, or
`status: exempt` with a legitimate reason. Do not mark a rule done solely
because code exists — the implementation must actually satisfy the rule.

During the streamline pass the YAML shrinks by **compressing the prose in each
comment**, never by removing evidence. Every `done` keeps a working
file/function pointer (pointers move with the code when a refactor moves it),
every `exempt` keeps its justification, and the `docs/architecture.md`
cross-link is added to the most chatty rules (XML policy, gate singleton,
runtime-data) so the rationale lives in one place.

### Documentation source

Use current Home Assistant developer docs as the authority:
https://developers.home-assistant.io/docs/creating_integration_file_structure/
