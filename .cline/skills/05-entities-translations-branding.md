# Skill 05 — Entities, Naming, Translation, and Branding

## Base entity

Create a shared:

`IrishRailEntity(CoordinatorEntity[IrishRailDataUpdateCoordinator])`

Use:
- `_attr_has_entity_name = True`
- stable device/entity metadata where appropriate
- coordinator-based updates

Common patterns belong in `entity.py`.

## Entity naming

Use:
- `_attr_translation_key`
- `strings.json`
- `translations/en.json`

Avoid hard-coded user-facing English entity names.

## Unique IDs

Every entity needs a stable unique ID.

Good:
`f"{entry.unique_id}_next_due"`

Bad:
- station display name
- destination text
- translated strings
- current train number if the train changes

The unique ID must remain stable across restarts and data updates.

## Coordinator wiring

Prefer `CoordinatorEntity`.

Do not manually add listeners unless necessary.

If adding listeners yourself:
- subscribe in `async_added_to_hass`
- register cleanup with `async_on_remove`

Never subscribe during `__init__`.

## Sensor design

Expose only useful, well-defined entities.

Possible entities:
- next train due
- minutes late
- destination
- origin
- scheduled time
- expected time

Do not create sensors for every raw XML field.

## State semantics

Use the most appropriate Home Assistant sensor metadata:
- native unit
- device class
- state class

Only assign a device class/state class when semantically correct.

Do not force `measurement` onto values that are not measurements.

## Device metadata

The project prompt requests a service device:

- manufacturer: `Iarnród Éireann / Irish Rail`
- service-style device type where supported

However, device creation is a stronger-quality concern than merely having entities. Review the current rules before claiming Bronze compliance.

Do not create a fake physical device just to satisfy a checklist.

## Branding

Current Bronze requires branding assets.

The requested original file tree omitted the brand directory. Add the current supported branding assets and verify them with the current Home Assistant branding instructions.

Do not invent logos or use copyrighted assets without permission.

## Translations

Translation keys should exist for:
- config flow labels
- descriptions
- errors
- entity names

Keep `strings.json` and `translations/en.json` structurally aligned with current Home Assistant conventions.

## Next-N-trains entities (roadmap 1.3)

- Decide the surface before implementing and record the decision in the
  roadmap file:
  - extra sensors (trains 2–3 due/destination) — recommended default;
  - attribute expansion on existing sensors — not state-friendly for
    automations;
  - dedicated list/entity-per-train — most flexible, most code.
- Read beyond `coordinator.data[0]` defensively: when fewer trains exist,
  the entity reports `None` state or becomes unavailable — never crash.
- Fixed-slot unique IDs only (`f"{entry.unique_id}_train2_due"`); never IDs
  that shift meaning between refreshes.
- Every new entity needs a translation key in strings.json /
  translations/en.json plus tests, and a README Entities entry.

## Empty-vs-error semantics (roadmap 1.4)

- Distinguish "API reachable, zero trains scheduled" (expected, e.g.
  overnight) from "API unreachable" (coordinator failure → unavailable).
- Implement an explicit attribute (e.g. `api_reachable: true/false`) or
  availability logic driven by coordinator success; never conflate an empty
  list with a failure.
- Tests for both states; README Conditions section updated.

## Icon translations (roadmap Phase 3 — `icon-translations`)

- Add `icons.json` at the integration root with per-entity icons keyed by
  translation key/entity component.
- Keep icons aligned with entity translation keys; validate against current
  Home Assistant icon-translations guidance.

## Exception translations (roadmap Phase 3 — `exception-translations`)

- Convert user-facing error messages to translation-keyed
  `HomeAssistantError`s; no hard-coded English in raised errors.

## Repair issues (roadmap Phase 3 — `repair-issues`)

- Raise a repair issue (`ir.async_create_issue`, translation-keyed) when a
  station returns persistently empty data during service hours (possible
  API/schema change). Ensure the issue is created once, not per poll.

## Device-class review (roadmap Phase 3 — `entity-device-class`)

- Re-confirm device classes/state classes remain semantically correct as new
  entities are added (next-N-trains sensors, etc.).

Official integration file structure:
https://developers.home-assistant.io/docs/creating_integration_file_structure/
