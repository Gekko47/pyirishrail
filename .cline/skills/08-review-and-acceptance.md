# Skill 08 — Final Review and Acceptance

## Review order

Review in this order:

### 1. Live rules

Fetch and inspect:
- Quality Scale overview
- Quality Scale rules
- Quality Scale checklist

Record:
- current Bronze rule IDs
- any rule changes from the supplied migration prompt

### 2. Source behavior

Compare:
- old pyirishrail API
- XML fixtures
- new typed models
- new entities

Ensure no required functionality was silently dropped.

### 3. Async safety

Search the new repository for:
- `requests`
- blocking HTTP
- synchronous file/network calls in async paths
- `time.sleep`
- `hass.helpers`

Any occurrence needs review.

### 4. HTTP client

Confirm:
- injected shared session
- explicit timeout
- typed exceptions
- safe XML parser
- no session-per-call

### 5. Config flow

Confirm:
- UI setup
- `data_description`
- validation before entry creation
- stable unique ID
- duplicate prevention
- translated errors
- complete tests

### 6. Runtime architecture

Confirm:
- one coordinator per entry
- entities do not call API directly
- initial refresh before forwarding platforms
- runtime objects in `entry.runtime_data`

### 7. Entity architecture

Confirm:
- stable entity unique IDs
- `_attr_has_entity_name = True`
- translation keys
- coordinator lifecycle
- no mutable user-facing text in unique IDs

### 8. Branding

Confirm the required current branding assets exist and are valid.

### 9. Tests

Confirm:
- API failure paths
- malformed XML
- config-flow complete coverage
- duplicate config
- coordinator failure
- setup failure

### 10. Tooling

Run current:
- pytest
- lint
- type checks
- Home Assistant validation/hassfest where applicable

### 11. Quality scale

For every Bronze rule, produce:

`rule-id: done — path/function`

or:

`rule-id: exempt — reason`

Never:
`rule-id: done — probably satisfied`

## Specific contradictions to catch

### Prompt says Python >=3.13

Do not blindly accept it. Current Home Assistant development metadata is already at Python >=3.14.2. Verify the exact 2026.8 release requirement.

### Prompt omits brand/

Current Bronze checklist explicitly contains `brands`. The target tree must therefore be expanded if branding assets are required by the current rules.

### Prompt includes strict typing and async dependency

Those correspond to Platinum-level rules in the current checklist. They are good engineering choices but should not be represented as Bronze rules.

### Prompt includes device info

The current checklist places `devices` under Gold. Do not claim that device creation is required for Bronze.

### Prompt says diagnostics are optional

That is consistent with diagnostics being Gold rather than Bronze.

### Prompt says 100% config-flow tests

That is correct and should be treated as mandatory for Bronze.

## Roadmap-phase acceptance

Bronze is complete. For each roadmap item, review against the acceptance
criteria in `.cline/irish-rail-improvement-roadmap.md` (see also
`09-roadmap-execution.md`):

| Item | Review focus |
|---|---|
| 1.1 Reconfigure flow | Flow works in UI; full branch coverage; strings/translations; docs updated |
| 1.2 Options flow | Interval bounded 30s–10min; coordinator honors change; schema rejects invalid values |
| 1.3 Next-N-trains | Surface decision recorded; new entities live; translations; defensive when data absent |
| 1.4 No-trains semantics | Empty vs error distinguished (attribute/logic); both states tested |
| Phase 2 | Each Silver rule demonstrably satisfied; coverage gate ≥95% |
| Phase 3 | README sections added; icons.json valid; repair issue raised once; exception translations |
| 4.1 / 5.3 Client layout (in-repo vendored) | `pyirishrail/` package vendored at `custom_components/irish_rail/pyirishrail/`; `manifest.json` requires `[]`; no third-party XML dependency (stdlib `ET` + pre-parse DTD guard); `py.typed` ships in the vendored layout; `git log --follow` still walks the moved files back to their original commits |
| 4.2–4.5 | Evidence recorded per item in the plan file |

After each item:
- tick the roadmap checkbox,
- update `quality_scale.yaml` if a rule's status changed,
- confirm the standing requirements still hold.

## Final acceptance statement

The implementation is acceptable only when:
- all applicable Bronze rules remain actually satisfied,
- the roadmap item under review meets its stated acceptance criteria,
- all legitimate exemptions are documented,
- tests pass at the active coverage gate,
- validation passes,
- no unresolved blocking TODO remains,
- roadmap checkboxes and `quality_scale.yaml` agree with the implementation.

If something cannot be verified, mark it unresolved rather than falsely claiming compliance.
