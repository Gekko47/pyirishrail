# Irish Rail Integration — LLM Skill Pack

Purpose: give an LLM the operational knowledge, current-rule checklist, architecture constraints, validation workflow, and source references needed to work on the `irish_rail` Home Assistant custom integration.

The Bronze migration (Home Assistant 2026.8 Bronze Integration Quality Scale) is **complete** — see the baseline section of `.cline/irish-rail-improvement-roadmap.md`. The v0.3.0 Clean Baseline (`.cline/clean-cut-baseline-plan.md`) is also complete. The currently active plan is the **Streamline Roadmap** (`.cline/streamline-roadmap.md`) — a maintainability pass that preserves Platinum quality-scale compliance.

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
10. `09-roadmap-execution.md` — item-by-item guidance for the completed
    v0.3.0 work; **superseded by Skill 10 for new work**.
11. `10-streamline-execution.md` — **ACTIVE**. Per-step guidance for the
    Streamline Roadmap (`.cline/streamline-roadmap.md`): docstring
    discipline, module boundaries, the `RuntimeRegistry` pattern, sensor
    consolidation, quality-scale evidence discipline, and test discipline.
    Read this before touching the source for any new work.
12. `../docs/architecture.md` — long-form design notes that the source
    code's short docstrings point to. Update this when a refactor changes
    an invariant the doc covers.
13. `../clean-cut-baseline-plan.md` — completion record for the v0.3.0
    Clean Baseline. Where this pack conflicts with that plan (e.g. the
    defusedxml guidance in Skill 07 and the PyPI-extraction references
    in 00/07/08), the plan file wins for historical context.
14. `../irish-rail-improvement-roadmap.md` — completion record for the
    pre-v0.3.0 improvement work. Tick checkboxes in the **streamline**
    roadmap when executing new work, not in the old one.

Important:
- Treat the live Home Assistant developer documentation as authoritative.
- The supplied migration prompt is project-specific guidance, not a substitute for the live rules.
- Do not mark any quality-scale rule `done` merely because a prompt or roadmap says to implement it.
- Distinguish Bronze requirements from stronger Silver/Gold/Platinum practices, and label them accurately.
- If current documentation conflicts with an older prompt, follow the current documentation and record the discrepancy.
- When executing roadmap items, follow the execution order in the active
  roadmap file. For the streamline pass, follow Skill 10's discipline
  (docstring density gate, module-boundary rules, quality-scale
  evidence discipline). Tick checkboxes in the streamline roadmap as
  work lands, and record decisions in its `Decisions (resolved)` table.

Official sources:
- Integration Quality Scale overview
- Quality Scale rules
- Quality Scale checklist
- Config flow
- Creating integrations
- Integration file structure
- Fetching data / DataUpdateCoordinator
- Testing