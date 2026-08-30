# Irish Rail Integration — LLM Skill Pack

Purpose: give an LLM the operational knowledge, current-rule checklist, architecture constraints, validation workflow, and source references needed to work on the `irish_rail` Home Assistant custom integration.

The Bronze migration (Home Assistant 2026.8 Bronze Integration Quality Scale) is **complete** — see the baseline section of `.cline/irish-rail-improvement-roadmap.md`. These skills now govern all post-Bronze improvement work defined in that roadmap: Silver/Gold quality rules, functional improvements, and engineering robustness.

Use these skills together, in this order:

1. `00-project-context.md`
2. `01-live-ha-bronze.md`
3. `02-architecture-and-async.md`
4. `03-config-flow.md`
5. `04-coordinator-runtime-data.md`
6. `05-entities-translations-branding.md`
7. `06-testing-ci.md`
8. `07-manifest-dependency-packaging-docs.md`
9. `08-review-and-acceptance.md`
10. `09-roadmap-execution.md` — item-by-item guidance for implementing `.cline/irish-rail-improvement-roadmap.md`
11. `../clean-cut-baseline-plan.md` — **ACTIVE execution plan (v0.3.0 Clean
    Baseline)**. All remediation work is governed there: small-increment
    protocol, per-phase gates, decisions, and status tracking. Where this
    pack conflicts with the plan (e.g. the defusedxml guidance in Skill 07
    and the PyPI-extraction references in 00/07/08), the plan file wins
    until the plan's Phase 4 rewrites those sections.

Important:
- Treat the live Home Assistant developer documentation as authoritative.
- The supplied migration prompt is project-specific guidance, not a substitute for the live rules.
- Do not mark any quality-scale rule `done` merely because a prompt or roadmap says to implement it.
- Distinguish Bronze requirements from stronger Silver/Gold/Platinum practices, and label them accurately.
- If current documentation conflicts with an older prompt, follow the current documentation and record the discrepancy.
- When executing roadmap items, follow the execution order in `09-roadmap-execution.md`, tick checkboxes in the roadmap as work lands, and record decisions there.

Official sources:
- Integration Quality Scale overview
- Quality Scale rules
- Quality Scale checklist
- Config flow
- Creating integrations
- Integration file structure
- Fetching data / DataUpdateCoordinator
- Testing