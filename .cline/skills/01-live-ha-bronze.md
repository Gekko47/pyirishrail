# Skill 01 — Live Home Assistant Quality Scale Rules (Bronze baseline + roadmap tiers)

## Authority

Always verify the current rules before implementation:
- https://developers.home-assistant.io/docs/core/integration-quality-scale/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist/

For a 2026.8 migration, use the rules that are live for the 2026.8-era documentation. Do not rely on a copied historical checklist.

## Current Bronze rule IDs

The current published Bronze checklist contains:

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

## Rule handling

For every rule:
- `done` only when implemented and tested where applicable
- `exempt` only when the official rule permits exemption and the integration genuinely does not provide that feature
- never use `exempt` as a shortcut
- include a concise file/function pointer

## Important consequences for Irish Rail

### action-setup / docs-actions / docs-triggers / docs-conditions

If Irish Rail exposes no service actions, triggers, or conditions, determine the official rule's exemption semantics and document the exemption. Do not invent useless services solely to make a checklist item appear implemented.

### appropriate-polling

Irish Rail is a polling integration. Choose an interval based on upstream data freshness and service/API load. A one-minute interval is a reasonable candidate only if validated against current API behavior and rate expectations.

### brands

Current Bronze includes branding assets. The old requested tree is incomplete if it omits the required brand assets. Add the appropriate brand directory/assets and validate them using current Home Assistant branding guidance.

### config-flow-test-coverage

This is stronger than "there are some config flow tests". The config flow must have full coverage, including error recovery and duplicate-entry behavior.

### dependency-transparency

Every non-core dependency must be declared transparently. If using `defusedxml`, it belongs in the integration manifest requirements. Do not list dependencies already provided by HA core unless current manifest rules say otherwise.

### docs rules

Documentation must satisfy the applicable rule, not merely contain generic prose.

At minimum, cover:
- what the service is
- installation/prerequisites
- removal
- any actual actions/triggers/conditions
- any applicable configuration information

### entity-event-setup

Subscriptions must be attached to the correct entity lifecycle. Prefer `CoordinatorEntity`'s normal wiring rather than hand-rolled listeners unless there is a real need.

### entity-unique-id

IDs must be stable and independent of mutable user-visible text.

### has-entity-name

Use `_attr_has_entity_name = True` and the current naming/translation model.

### runtime-data

Runtime client/coordinator objects belong in `ConfigEntry.runtime_data`, not `hass.data[DOMAIN][entry_id]`.

### test-before-configure

The config flow must make a real validation call before creating the entry.

### test-before-setup

Initial setup must verify that the service can be reached and the configured resource can be initialized. Failure should result in the appropriate setup-not-ready behavior rather than a half-configured integration.

### unique-config-entry

Use a stable upstream identifier such as station code. Never use station display name as the unique ID if it can change.

## Bronze vs stronger tiers

Do not confuse:
- Bronze: baseline requirements
- Silver: robustness/ownership/unavailability/etc.
- Gold: devices, diagnostics, discovery, extensive docs, translations, etc.
- Platinum: async dependency, injected web session, strict typing

The project prompt may intentionally implement some stronger-tier practices. Keep them, but label them accurately in review documentation.

## Roadmap-targeted Silver/Gold/Platinum rule IDs

Bronze is complete. The improvement roadmap (`.cline/irish-rail-improvement-roadmap.md`)
targets these stronger-tier rules. Always verify each against the live rules
pages before implementing; do not rely on this list alone.

### Silver

- `parallel-updates` — declare `PARALLEL_UPDATES` in `sensor.py`; justify the value.
- `log-when-unavailable` — exactly one log line on transition to unavailable and one on recovery; make coordinator behavior explicit and tested.
- `entity-unavailable` — explicit test asserting entities report `unavailable` after a failed refresh.
- `config-entry-unloading` — verify/document unload+reload behavior with a test.
- `test-coverage` — raise the CI coverage gate to ≥95% when Silver is targeted.

### Gold

- `reconfiguration-flow` — `async_step_reconfigure` changing direction filter in place, station fixed.
- `docs-examples` / `docs-use-cases` / `docs-troubleshooting` — README automation examples, use cases, troubleshooting sections.
- `icon-translations` — add `icons.json` with per-entity icons aligned to translation keys.
- `exception-translations` — user-facing errors become translation-keyed `HomeAssistantError`s.
- `repair-issues` — raise a repair issue for persistently empty station data during service hours.
- `diagnostics` — add redaction edge-case tests (module already exists).
- `entity-device-class` — re-confirm device classes as entities grow.

### Platinum path

- `async-dependency` / `inject-websession` — satisfied today by local code; roadmap Phase 5.3 moves `api.py` into a top-level `pyirishrail/` package in this same repo and publishes it to PyPI (in-repo sibling layout, revised 2026-08-27; the package is still consumed by the integration as an external `manifest.json` requirement).

For every rule touched by a roadmap item: update `quality_scale.yaml` status,
include a file/function pointer, and only mark `done` with implementation plus
tests. See `09-roadmap-execution.md` for per-item guidance.
