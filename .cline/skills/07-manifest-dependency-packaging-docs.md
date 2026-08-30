# Skill 07 — Manifest, Dependencies, Packaging, HACS, and Documentation

## Manifest

For a custom integration, include a valid semantic `version`.

Core-style fields should be current and valid:
- domain
- name
- config_flow
- quality_scale
- iot_class
- integration_type
- requirements
- codeowners
- documentation
- issue_tracker
- version

Verify each field against the current manifest documentation before finalizing.

Official manifest docs:
https://developers.home-assistant.io/docs/creating_integration_manifest/

## Dependencies

`aiohttp` is provided by Home Assistant core. Do not list it as an integration requirement merely because the client imports it.

`defusedxml` is an actual external dependency if it is not provided by the target HA environment. Declare it in the manifest requirements.

Pin/range it according to the current project's dependency policy. Do not invent a version without checking compatibility.

## Python version

Do not blindly use the old prompt's `>=3.13`.

Current Home Assistant development branch information shows Python `>=3.14.2`; for a strict 2026.8 target, verify the exact Python requirement against the 2026.8 release/tag before finalizing `pyproject.toml`.

The correct rule is:
- target the exact Python range supported by the target HA release
- do not target an older Python than HA
- do not claim 2026.8 compatibility from the dev branch alone

## pyproject.toml

Use modern PEP 621 metadata.

Do not retain:
- setup.py
- setup.cfg
- requirements.txt

unless there is a demonstrated compatibility reason.

Dev dependencies should reflect actual tools used by the project.

Do not copy Home Assistant core's complete dependency set into the custom repository.

## HACS

Keep HACS metadata minimal and valid for a custom integration.

Validate:
- name
- content_in_root
- render_readme

Check current HACS schema before finalizing.

## Code owners

Do not leave a fake GitHub handle.

If the real handle is unknown:
- use an explicit placeholder
- add a TODO
- do not claim the repository is production-ready

## README

At minimum, provide:
1. high-level integration/service description
2. installation
3. prerequisites
4. UI configuration
5. entities provided
6. removal
7. relevant limitations

Do not claim:
- API authentication is absent
- API is HTTPS
- API is official/unofficial
- a particular rate limit
unless verified.

The source prompt specifically asks to confirm whether the Irish Rail API needs an API key. Verify this from current upstream/API documentation rather than guessing.

## Documentation rules

Current Bronze rules include documentation requirements for:
- high-level description
- installation
- removal
- actions
- triggers
- conditions

If the integration has no actions/triggers/conditions, document the absence/exemption according to the rule rather than inventing fake functionality.

## Quality scale file

`quality_scale.yaml` should list every Bronze rule.

For each:
- `done`, with a pointer
or
- `status: exempt`, with a legitimate reason

Do not mark a rule done solely because code exists. The implementation must actually satisfy the rule.

## Branding

Current Bronze includes branding assets. Add them even though the old project-specific tree omitted them.

## Client library extraction (roadmap Phase 5.3 — Platinum path, in-repo sibling layout, revised 2026-08-27)

- The `pyirishrail` package lives in **this repository** as a top-level
  sibling of `custom_components/` (revised 2026-08-27; previous plans put
  it in a separate repo). Reasons: the integration is still in testing
  (no users), a single repo preserves the full history of the client
  code, and CI/HACS are simpler to maintain.
- Layout after this work:
  - `pyirishrail/` — the published library package (built as a wheel,
    ships `py.typed` per PEP-561)
  - `custom_components/irish_rail/` — the HA integration (unchanged apart
    from the import switch and `manifest.json` requirements)
  - `tests/pyirishrail/` — pure library tests, no HA imports allowed
  - `tests/components/irish_rail/` — HA integration tests, unchanged
- `pyproject.toml` becomes a real wheel build (PEP 621): `name =
  "pyirishrail"`, `packages = ["pyirishrail"]`, include `py.typed` in the
  wheel. The integration's `custom_components/irish_rail/` is **not** an
  installable package — HACS picks it up from the directory layout, not
  from `pip`.
- `custom_components/irish_rail/manifest.json` declares
  `"requirements": ["pyirishrail>=0.2,<1.0"]` and drops `defusedxml`
  (transitive via `pyirishrail`; listing transitive requirements in the
  manifest is discouraged by HA core).
- CI is a matrix: `library` job (lint + mypy strict on `pyirishrail/` +
  pytest on `tests/pyirishrail/` + build wheel + publish to TestPyPI/PyPI)
  and `integration` job (installs the built wheel + lint + mypy strict on
  `custom_components/irish_rail/` and `tests/components/irish_rail/` +
  pytest ≥95% coverage).
- The package must keep all Skill 02 rules: no Home Assistant imports,
  injected session, explicit timeouts, typed exceptions, safe XML parsing.
- The `defusedxml` decision must be recorded against the published
  package (which is in this repo, not a separate one): the official
  `async-dependency` rule has *no exceptions* ("Dependency is async"), so
  either justify parser-on-fetched-bytes usage with evidence
  (`defusedxml` is a pure XML parser invoked only on bytes already
  fetched by `aiohttp`) or replace it.
- This satisfies `async-dependency` and `inject-websession` as
  published-library rules, and ships the `py.typed` (PEP-561) sub-gap of
  `strict-typing`.

## README expansion (roadmap Phase 3 — Gold docs rules)

Beyond the Bronze minimum above, add:

- Automation examples (`docs-examples`): departure alert, delay notification.
- Use cases (`docs-use-cases`): commuter dashboard, delay alerts,
  presence-based departure reminders.
- Troubleshooting (`docs-troubleshooting`): API downtime, empty data at
  night, retry states.
- Configuration section documenting the options flow scan interval
  (roadmap 1.2) and reconfigure flow behavior (roadmap 1.1).
- Entities section updated whenever new entities are added (roadmap 1.3).

## Documentation source

Use current Home Assistant developer docs as the authority:
https://developers.home-assistant.io/docs/creating_integration_file_structure/
