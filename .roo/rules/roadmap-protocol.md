# Roadmap Protocol — `irish_rail`

Always-applied process rules. These govern *how* work lands, not what the code
looks like. They hold whether or not a skill is loaded.

## Identify the active plan before editing

The active plan is **`.cline/streamline-roadmap.md`**. Read it first.

| Plan | Role |
|---|---|
| `.cline/streamline-roadmap.md` | **active** — the *what* and the *order* |
| `.cline/clean-cut-baseline-plan.md` | completion record (v0.3.0) |
| `.cline/irish-rail-improvement-roadmap.md` | completion record (pre-v0.3.0) |
| `docs/architecture.md` | long-form design invariants |

Do not implement work outside the active roadmap without recording it there.
Do not tick checkboxes in the superseded plans.

## Precedence when sources disagree

1. Live Home Assistant developer documentation
2. The active roadmap
3. The skills in `.roo/skills/`
4. The superseded `.cline/skills/` pack and prior plans — historical only

Follow the higher source and **record the discrepancy** in the roadmap file.
Do not silently edit a skill to paper over a conflict.

## Increment protocol

One checkbox at a time, in the plan's phase order. For each increment:

1. Implement the increment.
2. Run all three gates (ruff · strict mypy · pytest at 100% coverage) plus any
   phase-specific proof the plan states.
3. **Tick the checkbox** in `.cline/streamline-roadmap.md`.
4. **Append one line** to the roadmap's Progress Log.
5. **Record decisions** in the `Decisions (resolved)` table — especially
   judgement calls, reversed decisions, and closed items with a finding.
6. Update `quality_scale.yaml` if a rule's status changed.
7. Update `docs/architecture.md` in the same commit if an invariant it covers
   changed.

Do not move to the next step before ticking the current one.

## Quality-scale evidence discipline

`quality_scale.yaml` is the contract that proves Platinum. It is not a
checklist to tick off.

- Mark `done` only when implemented **and** tested where applicable.
- Include a working file/function pointer for every `done`. If a refactor moves
  code, the pointer moves with it.
- Use `exempt` only when the official rule permits it **and** the integration
  genuinely lacks the feature. Never as a shortcut. Keep the reason.
- **Never** write `done — probably satisfied`. If it cannot be verified, mark
  it unresolved.
- The YAML shrinks by compressing comment prose, never by removing evidence.

Never invent a service, trigger, or condition to make a checklist item appear
implemented. Document the exemption instead.

## Refactors carry no behaviour change

A refactor and a behaviour change are separate increments. If a refactor would
change an observable value — a default, an attribute key, a `unique_id`, a file
path the user sees — the behaviour change is its own committed step, after the
refactor lands, and needs roadmap sign-off.

This extends to the live-API surface: the XML guard, request gate, and polling
cadence stay byte-for-byte identical.

## Tests pin behaviour, not prose

Tests assert observable behaviour: return values, raised exceptions, side
effects. A test that pins docstring text is deleted, not kept.

Test *count* may drop when duplicate cases merge. Test *coverage* of the source
may not regress — the gate is 100% line coverage.

## Documentation is part of the increment

Update in the same change, not as a follow-up:

- `docs/architecture.md` — when an invariant it covers changed
- `README.md` — when entities, configuration, or behaviour changed
- `CHANGELOG.md` — when a user-visible change lands
- `strings.json` + `translations/en.json` — kept structurally aligned whenever
  a new key appears
- `quality_scale.yaml` — whenever a rule's status changed

## Finish honestly

If an item cannot be completed as specified, do not tick it. Record the blocker
and the reason in the roadmap, and state it plainly in the summary. An
unresolved item that is honestly reported is acceptable; a ticked checkbox over
unfinished work is not.
