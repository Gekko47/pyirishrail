# v0.3.0 Clean Baseline — Execution & Tracking Plan

> **Status authority for all remediation work.** Created 2026-08-30 from the
> lead-developer repo review. Work proceeds in small increments: one checkbox
> at a time, phase gates after each increment, tick + log before moving on.
> Companion pack: `.cline/skills/`. Where this file conflicts with a skill
> (e.g. the defusedxml guidance in Skill 07, the PyPI-extraction sections),
> **this file wins** until Phase 4 rewrites the stale sections.
>
> **Current status:** Phase 0 COMPLETE (commit 1) — next up: **Phase 1**.

## Ground rules

- No migration shims or deprecations: the repo has no users; wrong or dead
  code is deleted outright.
- One source of truth per fact: every `done` in `quality_scale.yaml` must
  carry a currently-true file pointer.
- Every increment lands green: ruff · strict mypy · pytest at the active
  coverage gate (CI enforces `--cov-fail-under=100`).
- Decisions are recorded here as they are made (Skill 09 discipline).
- The final `CHANGELOG.md` entry is compiled from the per-phase entries below;
  the README is rewritten (fluff removed) in Phase 4.

## Decisions (resolved)

| ID | Decision | Resolution |
|---|---|---|
| D1 | Track `.cline/` in git | **Yes** — the skill pack + this plan are governance; un-ignored in Phase 0 |
| D2 | Vendored package naming | **Keep** `custom_components/irish_rail/pyirishrail/`, marked internal; no rename churn |
| D3 | `uv.lock` | **Delete + gitignore** (tooling-only `pyproject.toml`, no `[project]` table) |
| D4 | CI stale-patch-target guard | **Yes** — grep step added with the Phase 5 CI work |

## Non-goals

Repo/domain rename · PyPI re-extraction · new user-facing features · migration
shims · entity `unique_id` changes · live-API behavior changes.

## Progress log (append one line per increment)

- 2026-08-30 — Plan created from the lead-dev review; Phase 0 executed (commit 1).

## Phase 0 — Workspace & git reset — **Status: COMPLETE (commit 1, 2026-08-30)**

- [x] Delete dead artifacts: root `pyirishrail/` (namespace-shadow `__pycache__` only), root `__pycache__/`, `dist/`, `build/`, `seed_build.err.log`, `seed_build.out.log`, `uv.lock`, `.qodo/`
- [x] `.gitignore`: ignore `uv.lock`, `.qodo/`, `.ignore/`; un-ignore `.cline/` (D1)
- [x] Uninstall the stale editable `pyirishrail` install from `.venv`
- [x] Track previously-untracked `custom_components/irish_rail/gate.py`, `pyirishrail/_gate.py`, `tests/components/irish_rail/test_client_gate.py`, `test_gate_sharing.py` (a fresh clone must import)
- [x] Single commit containing the completed revert + plan/changelog seeding
- [x] Verify: `git status` clean · `import pyirishrail` fails (shadow gone) · `import custom_components.irish_rail.pyirishrail` succeeds

## Phase 1 — Honest test baseline — **Status: PENDING**

- [ ] Retarget stale patch/import paths `pyirishrail.*` → `custom_components.irish_rail.pyirishrail.*` in all test files (mechanical; ~134 references across 12 files)
- [ ] Fix `test_gate_cancelled_queued_waiter_dequeues_cleanly` expectation: queue must be `[]` after cancelling the only queued waiter (the gate code is correct)
- [ ] ruff (0.16 default rule set): `G201` button.py:165 → `_LOGGER.exception(...)`; `BLE001` health.py:131 → narrow the catch or justified `noqa`; `TRY004` store.py:206 → `TypeError`; `F841` test_stops_store.py:178 → remove unused `release`
- [ ] strict mypy: 11 errors in 2 files (test_client.py annotations / `list[str]` vs `list[TrainMovement]`; one further file)
- [ ] Gates: pytest full suite green · ruff 0 · mypy 0

## Phase 2 — Correctness fixes — **Status: PENDING**

- [ ] Reconfigure `unique_id` erasure fix in `config_flow.py`: pass `unique_id=` to `async_update_entry` only when the flow actually claimed one; regression tests (same-direction reconfigure preserves `entry.unique_id` across reload/restart; direction-change still claims + aborts on duplicates)
- [ ] Unify gate + health lifecycle on one `loaded_entry_ids: set[str]` under `hass.data[DOMAIN]`; release the request gate / stop the monitor only when the set empties; idempotent setup (fixes the split rate budget on partial unload and the `ConfigEntryNotReady` retry double-count); tests: unload one of two entries, failed-first-refresh retry, last unload stops both
- [ ] Hardening: `gather(..., return_exceptions=True)` in `async_get_station_stops_at_options`; `asyncio.Lock` around `StopsMatrixStore.async_record`; `coordinator.failure_streak` public property for diagnostics; `DUBLIN_TZ` defined once in `const.py`; move `build_unique_id`/`normalized_direction` to `identity.py` (removes the coordinator → config_flow import)
- [ ] Gates: full suite green incl. new tests · ruff 0 · mypy 0

## Phase 3 — Zero-dependency XML (defusedxml removal) — **Status: PENDING**

Evidence base (probed 2026-08-30 on Python 3.14.2 / expat 2.7.5 — the HA 2026.8
floor): stdlib `xml.etree.ElementTree` already rejects entity declarations and
external-entity resolution with `ParseError`; only a DTD *without* entities
parses silently. The pre-parse guard closes that gap explicitly and makes the
policy version-independent.

- [ ] `pyirishrail/api.py::_request`: drop `defusedxml`/`DefusedXmlException`; add `_DTD_DECL_RE` pre-parse guard (`<!doctype|entity|element|attlist|notation`, case-insensitive) raising `IrishRailParseError`; parse with stdlib `ET.fromstring`; keep `_strip_namespaces` + the `ParseError` mapping (single choke point — script, rebuild button, and flows inherit it)
- [ ] New hostile-input tests beside `test_api_parse_error`: entity bomb; XXE-over-http; XXE-over-file; DTD-without-entities (fail-closed); valid namespaced + namespace-free docs still parse
- [ ] Remove `types-defusedxml` from the `ci.yml` pip line
- [ ] **Zero-dep proof**: `pip uninstall defusedxml` from `.venv`, then the full suite runs green
- [ ] Gates: full suite green · ruff 0 · mypy 0

## Phase 4 — Documentation & claims truth-pass — **Status: PENDING**

- [ ] `quality_scale.yaml`: rewrite `dependency_transparency` (current text falsely claims a `pyirishrail>=0.2,<1.0` manifest pin — requirements are intentionally empty, zero third-party runtime deps); update `async_dependency` wording (stdlib parser + explicit guard); fix `strict_typing` (3) pointer (py.typed vendored; wheel/library-CI-job references removed); fix `inject_websession` README path
- [ ] `README.md` — full professional rewrite, fluff removed: accurate quick-facts; 3 sensors per entry (not 4 — train type is a device attribute); entity-ids vs unique-ids (`binary_sensor.status`, `button.rebuild_stops_matrix`); "Underlying API client" line → hardened stdlib parsing; keep the Platinum claim only because the gates evidence it
- [ ] `services.yaml` + `strings.json`: remove the "(no device)" wording — the two global entities share the *Irish Rail Services* device; align both texts
- [ ] `pyirishrail/__init__.py` docstring + `pyirishrail/README.md`: replace the defusedxml justification with the internal / zero-dependency / hardened-stdlib rationale
- [ ] Roadmap: record the revert decision (supersedes the Phase 5.3 records), the zero-dep decision, and this remediation; fix the baseline (4 sensors → 3; "published package" → vendored)
- [ ] Skills 00/07/08/09: rewrite stale tree / defusedxml / PyPI-acceptance sections
- [ ] Delete the dead `.cline/implementation_plan.md`
- [ ] Finalize `CHANGELOG.md` covering the full remediation
- [ ] Gates: full suite green · ruff 0 · mypy 0

## Phase 5 — Version & release — **Status: PENDING**

- [ ] `manifest.json` → `0.3.0`; `pyirishrail.__version__` → `0.3.0`
- [ ] CI: pin `ruff>=0.16`; add the stale-patch-target grep guard (D4)
- [ ] Push; tag `v0.3.0` only after CI is green (hassfest + HACS validate + integration job)
- [ ] Release notes = the finalized `CHANGELOG.md` section

## Definition of done

- [ ] `git status` clean; a fresh clone runs the suite
- [ ] `ruff check` 0 · strict `mypy` 0 · `pytest --cov-fail-under=100` green
- [ ] Suite green with **no defusedxml and no `pyirishrail` install** in the venv
- [ ] `grep -r defusedxml` → docs/history only; stale `patch("pyirishrail…` targets → 0
- [ ] CI green on master for the release commit; tag `v0.3.0`
- [ ] Every `done` in `quality_scale.yaml` carries a correct, current pointer
- [ ] Roadmap decisions recorded; changelog complete; README professional and lean