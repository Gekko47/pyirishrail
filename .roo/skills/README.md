# Roo Skill Pack — Irish Rail Integration

Roo loads skills **on demand** by description match, not as an ordered pack.
The four skills here are named and trigger-rich so the right one loads for the
work at hand. Anything that must never be violated lives in
[`.roo/rules/`](../rules/) instead, because rules apply whether or not a skill
loads.

Read a skill when its `description` matches the task. Read
[`.roo/rules/`](../rules/) always.

---

## The four skills

| Skill | Covers |
|---|---|
| **ha-integration-conventions** | Project context, live Quality Scale rules, manifest/packaging/docs. Load before adding a dependency or claiming a rule is satisfied. |
| **data-and-entities** | The runtime data path: async client, config flow, coordinator, entities, translations. Load when touching any of those modules. |
| **testing-and-ci** | Test stack, test layer map, coverage gate, CI workflows. Load when writing tests or touching CI. |
| **roadmap-execution** | The active plan, increment protocol, streamline discipline, review order, acceptance. Load before multi-step work or a PR. |

---

## Mapping: 11 Cline skills → 4 Roo skills

| Cline skill | → Roo skill | Where it landed |
|---|---|---|
| `00-project-context.md` | **ha-integration-conventions** | §1 Project context, forbidden-legacy list, do-not-invent-semantics, scope discipline |
| `01-live-ha-bronze.md` | **ha-integration-conventions** | §2 Quality Scale authority: live rule IDs, tier labelling, contradiction traps |
| `07-manifest-dependency-packaging-docs.md` | **ha-integration-conventions** | §3 Manifest, zero-dependency policy, pyproject, HACS, README/docs, `quality_scale.yaml` |
| `02-architecture-and-async.md` | **data-and-entities** | §1 Async client architecture, XML policy, typed models, HA boundary |
| `03-config-flow.md` | **data-and-entities** | §2 Config flow: user/reconfigure/options, unique ID, `data_description` |
| `04-coordinator-runtime-data.md` | **data-and-entities** | §3 Coordinator, `entry.runtime_data`, first refresh, backoff, unload |
| `05-entities-translations-branding.md` | **data-and-entities** | §4 Entities, naming, unique IDs, translations, sensor surface, repair issues |
| `06-testing-ci.md` | **testing-and-ci** | §1-§5: stack, layer map, style, gates, CI |
| `08-review-and-acceptance.md` | **roadmap-execution** (review order + acceptance) **+ testing-and-ci** (§9 tests, §10 tooling) | The one skill that split — see note below |
| `09-roadmap-execution.md` | **roadmap-execution** | §1 plan authority, §6 historical item table |
| `10-streamline-execution.md` | **roadmap-execution** | §3 streamline discipline: docstrings, boundaries, registry, sensors, anti-patterns |

### Note on Skill 08

`08-review-and-acceptance.md` was the only Cline file that **split** across two
Roo skills. Its content is not one concern:

- **The 11-step review order and the acceptance statement** are a
  roadmap-phase activity — a gate you apply before accepting an increment back
  to the plan. These went to **roadmap-execution** §4-§5.
- **Steps 9 (Tests) and 10 (Tooling)** are verification of the test layer and
  the CI gate, which is the testing-and-ci skill's domain. These went to
  **testing-and-ci** §2 and §4.

Nothing was dropped. The division is by *when you apply the rule* (before
accepting an increment vs. while writing a test), not by topic overlap.

### Why the numeric prefixes were dropped

Cline's `00`–`10` implied a mandatory read order — the pack README said
"use these skills together, in this order." Roo has no such contract: skills
are selected by description match. Numeric prefixes would falsely imply an
ordering that does not exist, so they were replaced by descriptive names.

### What changed beyond merging

Three substantive corrections, each because the source was stale or
self-contradictory:

1. **Coverage gate is 100%, not ≥90%/≥95%.** Cline 06 still documented the old
   90% → 95% progression. `pyproject.toml` (`fail_under = 100`) and CI
   (`--cov-fail-under=100`) are the authority. The progression is retained as a
   historical note.
2. **Single CI job, not a two-job library + integration matrix.** Cline 07 and
   09 described a matrix for the `pyirishrail` wheel, which was reverted. CI is
   one `integration` job plus `hassfest`.
3. **Client is internal; there is no `pyirishrail/` sub-package.** Cline 00,
   02, 07, 08, 09 all described the extraction in various states. Decision S2
   keeps it internal; `client.py` is the valid patch path. The stale-patch-target
   CI guard now enforces this.

Cline 00's contradictory "Current state" block (pre-baseline sensor/test counts
alongside post-baseline ones) is preserved in
[ha-integration-conventions §1](../skills/ha-integration-conventions/SKILL.md)
as an explicitly labelled **superseded / historical** block, because roadmap
items and older commits still reference those numbers.

---

## Companion plans (not skills)

These are referenced, never duplicated:

| File | Role |
|---|---|
| [`.cline/streamline-roadmap.md`](../../.cline/streamline-roadmap.md) | **Active plan.** Read it before any multi-step work. |
| [`.cline/clean-cut-baseline-plan.md`](../../.cline/clean-cut-baseline-plan.md) | Completion record, v0.3.0 Clean Baseline. |
| [`.cline/irish-rail-improvement-roadmap.md`](../../.cline/irish-rail-improvement-roadmap.md) | Completion record, pre-v0.3.0. |
| [`docs/architecture.md`](../../docs/architecture.md) | Long-form design invariants the source points to. |

---

## Precedence when sources disagree

1. **Live Home Assistant developer documentation** — always authoritative.
   Never answer a Quality Scale question from memory or from a copied
   historical checklist.
2. **The active roadmap** (`.cline/streamline-roadmap.md`).
3. **The skills** in this pack.
4. **The superseded `.cline/skills/` pack** and prior plans — historical
   context only.

If the live documentation or the active roadmap conflicts with anything
written here, the higher source wins — **and record the discrepancy** in the
roadmap file rather than silently editing the skill.

---

## Status of the Cline pack

[`.cline/skills/`](../../.cline/skills/) and the three `.cline/*.md` plan files
are **left in place, unmodified**, as the historical completion record. They
are superseded for new work.

The one place their content remains load-bearing is the CI comments and gate
messages in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), which
still reference `.cline/skills/10-streamline-execution.md` for the docstring
density rationale. That reference is left alone deliberately: rewriting CI
comments is a roadmap-level change, and the file is correct as written.
