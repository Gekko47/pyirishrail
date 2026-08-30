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

## Client library layout (v0.3.0 Clean Baseline, 2026-08-30)

> **Note:** the PyPI-extraction plan described in earlier versions of
> this skill was implemented on 2026-08-28 and reverted on 2026-08-29
> because the name `pyirishrail` on PyPI is owned by an unrelated
> project. The v0.3.0 Clean Baseline keeps the client **internal**;
> the v0.3.0 changelog and `.cline/clean-cut-baseline-plan.md` are the
> authority on this decision.

- The `pyirishrail` package lives **inside the integration** at
  `custom_components/irish_rail/pyirishrail/` (vendored). It is a
  framework-agnostic, framework-import-free Python module that ships
  with `py.typed` (PEP-561) and a deliberate `__init__.py` re-export
  surface.
- `custom_components/irish_rail/manifest.json` declares
  `"requirements": []`. The only transport dependency is `aiohttp`,
  which Home Assistant core provides. XML parsing is the standard
  library `xml.etree.ElementTree` with an explicit pre-parse
  DTD/entity guard (no third-party XML parser is required on the
  Home Assistant 2026.8 floor).
- `pyproject.toml` stays tooling-only: `[tool.coverage.report]`,
  `[tool.mypy]`, `[tool.pytest.ini_options]`. It does not build a
  wheel; the integration is consumed by HACS from the directory
  layout, not from `pip`.
- CI is a single `integration` job (Python 3.14, ruff, strict mypy,
  pytest at 100% line coverage, plus `hassfest` for manifest
  validation). See `.github/workflows/ci.yml` for the canonical
  commands.
- The package keeps the Skill 02 rules: no Home Assistant imports,
  injected session, explicit timeouts, typed exceptions, hardened
  stdlib XML parsing.
- The defusedxml question is closed: the package declares no
  third-party runtime dependencies. The pre-parse DTD/entity guard
  (with the `defusedxml` "defusedxml" rationale in
  `pyirishrail/README.md`) is preserved as historical context; the
  active policy is **stdlib-only with an explicit guard**.

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
