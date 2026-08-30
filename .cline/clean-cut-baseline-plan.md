# v0.3.0 Clean Baseline — Execution & Tracking Plan

> **Status authority for all remediation work.** Created 2026-08-30 from the
> lead-developer repo review. Work proceeds in small increments: one checkbox
> at a time, phase gates after each increment, tick + log before moving on.
> Companion pack: `.cline/skills/`. Where this file conflicts with a skill
> (e.g. the defusedxml guidance in Skill 07, the PyPI-extraction sections),
> **this file wins** until Phase 4 rewrites the stale sections.
>
> **Current status:** Phases 0–4 COMPLETE — next up: **Phase 5**.

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
- 2026-08-30 — Phase 1 executed (commit 2): 117 stale patch targets retargeted across 6 test files; gate-test expectation and gate-sharing setup-order fixed; 4 ruff + 11 mypy findings cleared; dead `_gate.py` silent guard removed; 2 coverage tests added. Gates green: 226 passed, 100.00% coverage, ruff 0, strict mypy 0.
- 2026-08-30 — Phase 2 executed (commit 3): reconfigure `unique_id` erasure fixed (identity forwarded only when claimed); gate + health lifecycle unified on the `loaded_entry_ids` set (gate released on last unload only; idempotent setup); stops-at options fan-out isolates unexpected errors; store record lock; `failure_streak` property; `DUBLIN_TZ` dedupe; `identity.py` extraction. Committed as `2fe7ad1`. Gates green: 229 passed, 100.00% coverage, ruff 0, strict mypy 0.
- 2026-08-30 — Phase 3 executed (commit 4): `defusedxml` dropped; pre-parse DTD/entity guard via a keyword `in` scan against a pre-lowered copy of the response (regex avoided because Python `re` treats `<name>` as a silent named-group); CI no longer installs `types-defusedxml`; 6 new hostile-input tests pin the policy. Zero-dep proof: `pip uninstall defusedxml` then full suite green. Gates: 235 passed, 100.00% coverage, ruff 0, strict mypy 0.
- 2026-08-30 — Phase 4 executed (commit 5): `quality_scale.yaml` rewritten with accurate pointers; `README.md` full professional rewrite (3 sensors, Irish Rail Services device, no fluff); `services.yaml` + `strings.json` aligned; `pyirishrail/__init__.py` + standalone `README.md` rewritten to zero-dep XML story; skills 00/07/08 truth-passed; roadmap Phase 5.3 REVERTED note added and acceptance table fixed; dead `implementation_plan.md` deleted; `CHANGELOG.md` closed. Gates: 235 passed, 100.00% coverage, ruff 0, strict mypy 0.

## Phase 0 — Workspace & git reset — **Status: COMPLETE (commit 1, 2026-08-30)**

- [x] Delete dead artifacts: root `pyirishrail/` (namespace-shadow `__pycache__` only), root `__pycache__/`, `dist/`, `build/`, `seed_build.err.log`, `seed_build.out.log`, `uv.lock`, `.qodo/`
- [x] `.gitignore`: ignore `uv.lock`, `.qodo/`, `.ignore/`; un-ignore `.cline/` (D1)
- [x] Uninstall the stale editable `pyirishrail` install from `.venv`
- [x] Track previously-untracked `custom_components/irish_rail/gate.py`, `pyirishrail/_gate.py`, `tests/components/irish_rail/test_client_gate.py`, `test_gate_sharing.py` (a fresh clone must import)
- [x] Single commit containing the completed revert + plan/changelog seeding
- [x] Verify: `git status` clean · `import pyirishrail` fails (shadow gone) · `import custom_components.irish_rail.pyirishrail` succeeds

## Phase 1 — Honest test baseline — **Status: COMPLETE (commit 2, 2026-08-30)**

- [x] Retarget stale patch/import paths `pyirishrail.*` → `custom_components.irish_rail.pyirishrail.*` in all test files (117 quoted references across 6 files: test_button, test_config_flow, test_diagnostics, test_health_suppression, test_init, test_sensor)
- [x] Fix `test_gate_cancelled_queued_waiter_dequeues_cleanly` expectation: queue must be `[]` after cancelling the only queued waiter (the gate code is correct)
- [x] Fix `test_second_entry_reuses_the_same_shared_gate`: add the second config entry after the component is loaded — HA's component setup loads every entry already registered for the domain, so the early-added entry made the explicit second `async_setup` raise `OperationNotAllowed` (discovered once the patch-target noise was gone)
- [x] ruff (0.16 default rule set): `G201` button.py → `_LOGGER.exception` (then `TRY401` → drop the redundant arg); `BLE001` health.py:131 → justified `noqa`; `TRY004` store.py:206 → `TypeError` (caller catch widened to match); `F841` test_stops_store.py:178 → remove unused `release`
- [x] strict mypy: all 11 errors resolved (test_client.py `blocking_request`/`filler`/`flaky` annotations + `TrainMovement`-typed movement-cache fixtures; `_scoped_factory` return annotation in test_matrix_rebuild.py)
- [x] `_gate.py`: remove the unreachable `except ValueError: pass` queue-removal guard — the lock discipline (never held across an await) makes the interleave impossible, and a loud `remove()` fails tests instead of silently leaking an admitted slot; this closed the last coverage gap honestly
- [x] Coverage tests added: fallback `HH:MM` resolution in `_parse_expected_arrival` (sensor.py 99–100) and per-bucket persistence-failure isolation in the rebuild (`_FlakyRecordingStore`, matrix_rebuild.py 185–193)
- [x] Gates: **226 passed · 100.00% coverage (`--cov-fail-under=100`) · ruff 0 · strict mypy 0**

## Phase 2 — Correctness fixes — **Status: COMPLETE (commit 3, 2026-08-30)**

- [x] Reconfigure `unique_id` erasure fix in `config_flow.py`: the flow forwards an identity to `async_update_entry` only when it actually claimed one (`updates` dict; never `unique_id=None`); regression assertion added to `test_reconfigure_unchanged_direction_skips_reload` (`entry.unique_id` preserved verbatim; the direction-change test already pinned the claimed-identity path)
- [x] Gate + health lifecycle unified on one `loaded_entry_ids: set[str]` under `hass.data[DOMAIN]` (`health.py::LOADED_ENTRY_IDS_KEY`): `async_note_entry_loaded/unloaded` take the entry id and return first/last booleans; `__init__.py` releases the request gate only when the last entry unloads; the monitor stops at the same moment. Idempotent setup fixes the split rate budget on partial unload and the `ConfigEntryNotReady` retry double-count. Tests: `test_unloading_one_of_two_entries_keeps_the_shared_gate` (gate survives sibling unload, both released at last) and the rewritten `test_monitor_lifecycle_tracks_loaded_entries` (idempotent re-registration)
- [x] Hardening: `gather(..., return_exceptions=True)` in `async_get_station_stops_at_options` with a per-train skip + warning (`test_station_stops_at_options_isolate_unexpected_lookup_errors`); `asyncio.Lock` around `StopsMatrixStore.async_record` (`test_concurrent_records_serialize_and_preserve_every_stop`); `coordinator.failure_streak` public property used by diagnostics; `DUBLIN_TZ` defined once in `const.py` (coordinator imports it, local duplicate and the `ZoneInfo` import removed); `build_unique_id`/`normalized_direction` moved to `identity.py` (coordinator → config_flow import gone)
- [x] Gates: **229 passed · 100.00% coverage · ruff 0 · strict mypy 0 (39 files)**

## Phase 3 — Zero-dependency XML (defusedxml removal) — **Status: COMPLETE (commit 4, 2026-08-30)**

Evidence base (probed 2026-08-30 on Python 3.14.2 / expat 2.7.5 — the HA 2026.8
floor): stdlib `xml.etree.ElementTree` already rejects entity declarations and
external-entity resolution with `ParseError`; only a DTD *without* entities
parses silently. The pre-parse guard closes that gap explicitly and makes the
policy version-independent.

- [x] `pyirishrail/api.py::_request`: drop `defusedxml`/`DefusedXmlException`; replace the regex with a simple `in`-scan over the keyword set `<!doctype`/`<!entity`/`<!element`/`<!attlist`/`<!notation` against a pre-lowered copy of the response body; stdlib `ET.fromstring` parses the rest; `_strip_namespaces` + the `ParseError` mapping unchanged. (Regex was first tried but Python's `re` silently treats `<name>` as a named group; documented in the comment so a future hand-edit doesn't repeat the footgun.)
- [x] New hostile-input tests beside `test_api_parse_error`: `test_dtd_or_entity_payload_is_rejected` (parametrized over internal-entity bomb, XXE-over-http, XXE-over-file, DTD-without-entities, uppercase-DOCTYPE) and `test_valid_namespaced_response_still_parses` (pins the success path)
- [x] Remove `types-defusedxml` from the `ci.yml` pip line
- [x] **Zero-dep proof**: `pip uninstall defusedxml` from `.venv`, then the full suite runs green; `importlib.util.find_spec('defusedxml')` returns `None` in the same environment that ran the suite
- [x] Gates: **235 passed · 100.00% coverage · ruff 0 · strict mypy 0**

## Phase 4 — Documentation & claims truth-pass — **Status: COMPLETE (commit 5, 2026-08-30)**

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