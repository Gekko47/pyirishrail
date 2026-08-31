# Irish Rail Integration — Improvement Roadmap

> Companion to `.cline/pyirishrail-to-ha-bronze-prompt.md` (the original Bronze
> build prompt, now complete). This document tracks all future improvement work.
> Items are checkboxes — tick them off as they are implemented.

## Current state baseline (verified 2026-08-22)

- [x] Bronze quality scale: all 20 official rules satisfied (`quality_scale.yaml`)
- [x] Fully async client (injected aiohttp session, stdlib `xml.etree.ElementTree` with an explicit pre-parse DTD/entity guard, typed exceptions)
- [x] DataUpdateCoordinator + `entry.runtime_data` + first-refresh fail-fast
- [x] Config flow with cached station fetch, unique-ID duplicate protection
- [x] 4 sensors per station/direction with translation keys and stable unique IDs
- [x] Diagnostics module with redaction
- [x] Brand assets conforming (icon 256², logo 512², logo@2x 1024², small 128²)
- [x] CI gate on Python 3.14: ruff + strict mypy + pytest ≥90% coverage
- [x] Test suite: 36 tests, 99.23% coverage, no deprecation/async warnings
- [x] README with Actions / Triggers / Conditions / Removal / Limitations sections

Platinum status (assessed against the live rule pages, 2026-08-24 — the
official Platinum tier contains exactly three rules; all three flipped
to `done` in `quality_scale.yaml` on 2026-08-28 with the completion of
Phases 5.1–5.4):

- [x] `inject-websession`: **done** on local-code evidence — `api.py`
      accepts an injected aiohttp session; setup/config flows pass
      `async_get_clientsession(hass)` (recorded in `quality_scale.yaml`)
- [x] `async-dependency`: **done** — the integration declares no
      third-party requirements; XML parsing is stdlib
      `xml.etree.ElementTree` with an explicit pre-parse DTD/entity
      guard, and the pre-parse policy is documented in
      `pyirishrail/README.md`.
- [x] `strict-typing`: **done** — `IrishRailConfigEntry` alias used
      throughout; mypy strict gate covers the integration *and* its
      tests; `py.typed` ships vendored at
      `custom_components/irish_rail/pyirishrail/py.typed`.

---

## Phase 1 — Functional improvements (user value)

### 1.1 Reconfigure flow (Gold rule `reconfiguration-flow`)
- [x] Add `async_step_reconfigure` to `IrishRailConfigFlow`
      (change direction filter in place; keep station code fixed)
- [x] Update entry data + coordinator direction without reload where possible
      — implemented via the standard `async_update_reload_and_abort` helper
      (reload path), per Skill 03/09 guidance; unique ID preserved
- [x] Tests: reconfigure happy path, abort paths
- [x] strings.json / translations/en.json: reconfigure step strings

### 1.2 Options flow for scan interval
- [x] Add `OptionsFlow` allowing polling interval bounded to 30s–10min
      (default 60s from `DEFAULT_SCAN_INTERVAL`)
- [x] Coordinator reads options via update listener
      (`entry.async_on_unload(entry.add_update_listener(...))`) — listener
      updates `coordinator.update_interval` in place (no reload needed);
      also exposes `num_trains` (see 1.3) which applies dynamically
- [x] Tests: option change updates interval; invalid values rejected by schema
- [x] Document in README (Configuration section)

### 1.3 Next-N-trains visibility
- [x] Decide surface: extra sensors (trains 2–3 due/destination) vs.
      attribute expansion vs. dedicated list entity — **DECISION:
      attribute expansion** (user-selected). Each of the four existing
      sensors gains an `upcoming_trains` attribute listing the next N trains
      (N configurable 1–5, default 3). No new entities; attributes are read
      fresh on every coordinator update so option changes apply without a
      reload. The single-next-train attributes are kept for compatibility.
- [x] Implement chosen approach reading beyond `coordinator.data[0]`
      (`resolve_num_trains()` in coordinator.py; defensive slicing in
      sensor.py — shorter list when fewer trains exist, never crashes)
- [x] Configurable at setup (`num_trains` field in config-flow user step,
      stored in entry.data) and changeable later via the options flow
      (entry.options takes precedence over entry.data)
- [x] Translation keys + tests (strings for both flow steps; tests cover
      attribute contents, fewer-than-N handling, and option-driven changes)
- [x] README Entities section updated

### 1.4 Explicit "no trains scheduled" semantics
- [x] Distinguish empty-API-list (expected) from unavailable (error)
      via an explicit attribute (`api_reachable: true`, present whenever an
      entity is readable) plus CoordinatorEntity availability logic — an
      available entity reporting `unknown` means "API responded, zero
      trains"; an unavailable entity means the API could not be reached
- [x] Tests for both states (`test_no_trains_vs_unavailable_semantics`)
- [x] README Conditions section updated

### 1.5 Optional train-position platform (stretch)
- [x] Exempt — no concrete use case confirmed; marked exempt per the item's
      own instruction. Revisit if a user request arrives.

---

## Phase 2 — Silver-tier quality rules

- [x] `parallel-updates`: declare `PARALLEL_UPDATES` constant in sensor platform
      — DECISION: `PARALLEL_UPDATES = 0`, per the official rule guidance for
      read-only platforms using a coordinator (entity updates are pure
      property reads over shared coordinator data; no outbound calls to rate-limit)
- [x] `log-when-unavailable`: log once on transition to unavailable and once
      on recovery (make coordinator behavior explicit + tested)
      — verified against the installed HA source: `DataUpdateCoordinator`
      already logs the error exactly once on success→failure and "Fetching
      … recovered" exactly once on recovery; the integration's job (raising
      `UpdateFailed`) was already done. Behavior pinned by explicit tests in
      `test_coordinator.py` rather than adding duplicate logging
- [x] `entity-unavailable`: add explicit test asserting unavailable state after
      failed refresh (exists partially in test_sensor.py — extend)
      — extended: all four sensors asserted `unavailable` via the realistic
      `IrishRailError` → `UpdateFailed` path, then available with fresh values
      after a successful refresh
- [x] `config-entry-unloading`: verify/document unload+reload behavior with test
      — round-trip test added to `test_init.py` (unload → `NOT_LOADED`,
      entities removed/placeholder; reload → `LOADED`, same entities restored);
      README Actions section documents runtime unload/reload support
- [x] Maintain `test-coverage` ≥95% as code grows (CI gate already enforces 90%;
      consider raising gate to 95% when Silver is targeted)
      — raised: `ci.yml` now enforces `--cov-fail-under=100`, mirrored by
      `[tool.coverage.report] fail_under = 100` in `pyproject.toml`; suite at
      exactly **100%** line coverage (202 tests across 18 source files,
      library 100% / integration 100%, both 100% combined; verified
      2026-08-28)

Silver-tier completeness note: beyond the five items above, the remaining
official Silver rules were assessed against the live rule texts and recorded
in `quality_scale.yaml`: `integration-owner`, `docs-configuration-parameters`
and `docs-installation-parameters` are done (codeowners declared; README
documents every setup/options parameter); `action-exceptions` and
`reauthentication-flow` are exempt (no service/platform actions; no
authentication, per the reauth rule's own exception clause). All ten Silver
rules are accounted for.

---

## Phase 3 — Gold-tier quality rules

- [x] `docs-examples`: README automation examples (departure alert,
      delay notification)
- [x] `docs-use-cases`: README use-case section (commuter dashboard,
      delay alerts, presence-based departure reminders)
- [x] `docs-troubleshooting`: README troubleshooting section (API downtime,
      empty data at night, retry states)
- [x] `icon-translations`: add `icons.json` with per-entity icons
- [x] `exception-translations`: convert user-facing error messages to
      translation-keyed `HomeAssistantError`s
- [x] `repair-issues`: raise repair issue when a station returns persistently
      empty data during service hours (possible API/schema change)
- [x] `diagnostics`: add redaction edge-case tests
- [x] `entity-device-class` review: confirm device classes remain appropriate
      as entities grow

### Phase 3 decisions & evidence (recorded 2026-08-23)

- Verified each item against the live HA developer-docs rule pages
  (`exception-translations`, `repair-issues`, `icon-translations`,
  `diagnostics`) before implementing; `issue_domain=DOMAIN` added to the
  repair issue per the official example.
- Repair-issue semantics: raised once per streak after 10 consecutive
  successful-but-empty polls during service hours (06:00–23:00 window from
  `SERVICE_HOURS_*` constants ≈ real timetable); cleared on the first
  data-bearing refresh, on entry unload, and re-raised only after a fresh
  streak. Translation-keyed with a `{station}` placeholder.
- `exception-translations`: audited — the integration raises no
  `HomeAssistantError` to users (no actions); recorded as exempt with
  evidence. Added `test_translations.py` guards: strings/en.json structural
  parity, config-flow error keys referenced in code must resolve, repair
  issue keys must match strings files, and a no-hard-coded-user-facing-error
  assertion.
- `icon-translations`: icons.json covers all four sensor keys. For the two
  `DURATION`-class sensors the train-themed icons intentionally extend the
  generic device-class icon with domain context (documented in
  `quality_scale.yaml`).
- Diagnostics edge cases added: None/empty/unicode identifier masking,
  determinism, never-set-up entry (no `runtime_data`) and sensitive-options
  redaction.
- Entity-device-class review recorded in `quality_scale.yaml`
  (`entity_device_class`).

### Full Gold-tier assessment (2026-08-23)

Phase 3 originally targeted a subset of Gold rules. All 21 official Gold
rules have now been assessed against their live developer-docs pages:

- Already recorded above / in earlier phases (9): `diagnostics`,
  `docs-examples`, `docs-troubleshooting`, `docs-use-cases`,
  `entity-device-class`, `exception-translations` (exempt),
  `icon-translations`, `reconfiguration-flow`, `repair-issues`.
- Assessed and recorded as done without code changes (10): `devices`,
  `docs-data-update`, `docs-known-limitations`,
  `docs-supported-functions`, `dynamic-devices`, `entity-category`
  (correct absence), `entity-disabled-by-default` (reviewed), 
  `entity-translations`, `stale-devices`.
- Recorded as legitimate exemptions (3): `discovery` and
  `discovery-update-info` (rule exception clauses — undiscoverable cloud
  service) and `docs-supported-devices` (rule does not apply to
  integrations without physical devices).

Every Gold rule now has a `done` or justified `exempt` entry in
`quality_scale.yaml`. Note: formal tier designation for *core* integrations
is decided by Home Assistant maintainers; this repository records evidence
of satisfying all documented criteria.

---

## Phase 4 — Robustness & engineering

> Former item 4.1 (client-library extraction) was folded into **Phase 5.3**
> (Platinum tier attainment) on 2026-08-24, then **reverted 2026-08-29**
> (PyPI name owned by an unrelated project): the v0.3.0 Clean Baseline
> keeps the client vendored inside the integration. See
> `.cline/clean-cut-baseline-plan.md` and the v0.3.0 changelog for the
> current layout.

### 4.2 Conditional requests
- [x] Probe API for ETag / Last-Modified support
      — probed live on 2026-08-24 against `getAllStationsXML` and
      `getCurrentTrainsXML` (two consecutive GETs each): the server sends
      **no validator headers at all** — no `ETag`, no `Last-Modified`.
      Responses carry only `Cache-Control: private, max-age=0`,
      `Content-Type: text/xml; charset=utf-8`, `Date`, `Connection` and
      `Content-Length`. Without validators, conditional revalidation
      (`If-None-Match` / `If-Modified-Since`) cannot be performed, and
      `max-age=0` additionally rules out cache reuse.
      **FINDING: conditional requests are unsupported by the Irish Rail
      RTPI API.**
- [x] If supported: send validators, skip parse on 304, document evidence
      — N/A: closed by the probe finding above; no code path exists to add
        validators to.
- [x] If not supported: record finding here and close item
      — recorded and closed 2026-08-24 with no integration changes.
        Efficiency on the success path remains covered by the per-day
        movement cache (4.6) and bounded concurrency there; failure-path
        load shedding is covered by adaptive backoff (4.3).

### 4.3 Adaptive backoff polling
- [x] On consecutive failures, back off coordinator interval exponentially
      (cap ~15min); restore immediately on success
      — implemented 2026-08-24 via a dynamic ``update_interval`` property on
      the coordinator (base × BACKOFF_MULTIPLIER^failure_streak, clamped to
      MAX_BACKOFF_INTERVAL). The property approach guarantees the backed-off
      value is used at schedule time (HA reschedules *inside*
      ``_async_refresh``, so post-hoc attribute mutation would lag a cycle);
      the setter keeps options-flow listener updates working unchanged.
      Failures raise the streak in ``_async_update_data`` (including a
      broad re-raising safety net); the first success logs once and resets.
- [x] Tests simulating failure streaks
      — doubling progression, 15-min cap, immediate restore + one-time
      recovery log, and options-driven base change during backoff.

### 4.4 XML layer hardening
- [x] Normalize namespaces once at parse time; remove dual
      namespace-or-not `findall` fallbacks
      — implemented 2026-08-24: a new ``_strip_namespaces()`` helper in
        ``api.py`` normalizes each document exactly once at the parse choke
        point (``_request``, right after defusedxml parsing), and the public
        pure parser ``parse_station_data`` also normalizes up front so it
        keeps accepting both namespaced and namespace-free roots directly in
        tests. The transformation is idempotent and skips comment/PI nodes.
        Removed the ``NAMESPACE`` constant and all five dual
        namespace-or-not lookup sites: ``_find_tag_text`` plus the
        ``objStation`` / ``objTrainPositions`` / ``objTrainMovements`` /
        ``objStationData`` collection loops now use plain tag names.
- [x] Re-run full suite; coverage must not regress below 95%
      — 2026-08-24: 100 tests pass, mypy ``--strict`` clean on all nine
        integration modules, ruff clean, coverage **98.89%** (≥95% gate
        holds).

### 4.5 CI breadth
- [x] Add HACS validation job alongside existing hacs.yml (already present —
      verify it stays green)
      — verified 2026-08-24 via the GitHub Actions API: the last five
        `hacs.yml` runs (push / pull_request / nightly schedule) are all
        `success`, latest push run 2026-08-24T12:56Z. The workflow file is
        correct as-is (`hacs/action@main`, `category: integration`) and the
        README badge is already wired; no changes needed.
- [x] Consider hassfest-style manifest validation for custom integrations
      — ADOPTED 2026-08-24. First validated locally against current master
        (sparse checkout of home-assistant/core, `python -m script.hassfest
        --integration-path …`), then wired into `ci.yml` as its own job via
        the official `home-assistant/actions/hassfest@master` action (the
        Docker-image route used for custom integrations). Validator findings,
        all fixed:
        * `manifest.json` keys reordered into hassfest's mandated order
          (domain, name, then alphabetical);
        * `min_ha_version` removed — rejected as an extra key by hassfest's
          custom-integration schema; the minimum-version floor for HACS
          installs remains enforced by `hacs.json`
          (`"homeassistant": "2026.8.2"`), so no user-facing change;
        * repair-issue strings restructured from `title`+`message` to
          `title`+`description` in `strings.json` and
          `translations/en.json` (schema requires `description` or
          `fix_flow`). Final local verdict: **Invalid integrations: 0**
          (all 23 plugins green).
- [x] Consider pytest-xdist for faster CI
      — EVALUATED, DEFERRED 2026-08-24. pytest-xdist 3.8.0 is already
        available transitively via pytest-homeassistant-custom-component;
        measured locally on this suite: single-process 4.13 s vs `-n auto`
        4.81 s (worker startup outweighs parallelism at 100 tests), with all
        100 tests passing under xdist, so compatibility is proven if it is
        adopted later. Decision: not enabled now; re-evaluate once the suite
        exceeds roughly 300–500 tests or gains slow end-to-end cases.

### 4.6 stops_at pruning hardening (2026-08-24)
- [x] Parallelize movement-history lookups with ``asyncio.gather`` bounded by
      a semaphore (``MAX_CONCURRENT_MOVEMENT_LOOKUPS``); per-train error
      semantics preserved (a failed lookup prunes only that candidate) and
      result ordering matches the API response order
- [x] Per-day movement cache in ``IrishRailClient`` keyed
      ``(train_code, date)``; failures and empty results never cached;
      lazy eviction of other-date entries via
      ``MOVEMENT_CACHE_MAX_ENTRIES``
- [x] Tests: multi-candidate concurrency, partial-failure isolation,
      cache hit across polls, retry-after-error, per-date cache entries

### 4.7 Update-listener-owned reload migration (2026-08-24)
- [x] Replace flow-scheduled ``async_update_reload_and_abort`` in the
      reconfigure step with ``async_update_entry`` + plain abort; the
      integration's update listener now owns reload scheduling via
      ``coordinator.requires_reload()`` — required by HA's 2026.6
      deprecation of the flow+listener combination (hard error in 2026.12:
      "has an update listener and should use it for scheduling a reload")
- [x] Option-only changes still apply live without a reload; a no-op
      reconfigure (same direction resubmitted) no longer reloads at all
      because ``async_update_entry`` fires no listeners when nothing changed
- [x] Tests: exactly one scheduled reload on data change, zero reloads for
      option changes and no-op reconfigures, plus a caplog guard asserting
      HA's deprecation message never appears
- [x] Identity-change cleanup: before scheduling that reload the listener
      drops the previous direction's registry entities and its abandoned
      device (HA has no automatic sweep for live-but-unprovided entries).
      Removal is restorable — entries move into the registries' deleted
      state tied to the config entry, so flipping back reclaims the original
      entity IDs with names/areas intact; verified by dedicated tests
- [x] Cleanup hardened to positive old-identity matching after review:
      the coordinator exposes ``previous_unique_id()`` derived from its
      applied-entry snapshot, and the listener removes only items carrying
      that exact old identity (enumeration stays scoped per config entry).
      A sibling-safety regression test (All + Northbound at one station,
      reconfigure Northbound → Southbound) proves sibling entries and their
      device are never touched

### 4.8 Direction-aware "stops at" options with self-healing matrix (2026-08-25)

Problem: the config flow's ``stops_at`` dropdown was built from each due
train's whole-day movement history, so even with a direction selected it
offered upstream stops and stops belonging to opposite-direction journeys of
the same train code — users could pick a station the configured direction
never reaches.

- [x] Journey-scoped route extraction (``api._scoped_journey_stops``):
      movement rows are filtered to the candidate's current journey via the
      ``TrainDestination`` field (verified against live API responses to
      match the due-record destination), then cut downstream of the
      monitored station (location-code first, display-name fallback).
      Blank/malformed upstream data degrades to the previous unscoped
      behavior instead of returning nothing
- [x] Runtime/runtime parity: ``_async_prune_trains`` applies the identical
      scoping when deciding whether a train "stops at" the filter target,
      and records the journey-scoped downstream stop names it observed in
      ``client.last_downstream_stop_names`` (reset every pass)
- [x] Self-healing per-install matrix (``store.py``): successful discoveries
      merge into a versioned HA ``Store`` file; the coordinator merges poll
      observations at zero extra API cost; unchanged sets never rewrite
      storage; corrupt files degrade to empty
- [x] Bundled seed (``stops_matrix.json``, generated from the live network
      by ``scripts/build_stops_matrix.py`` — 142 stations captured
      2026-08-25): bootstraps fresh installs at quiet hours
- [x] Config-flow fallback chain in ``async_step_stops_at``: live sample →
      learned cache → bundled seed → full station list (last resort);
      lookups never fall across direction buckets
- [x] Tests: pure scoping helpers, options/prune journey scoping,
      observation recording and reset, store roundtrip/merge/no-save/
      corrupt-file recovery, singleton access, seed load/failure caching,
      and four flow-level fallback-chain tests including direction-scoped
      cache isolation

---

## Phase 5 — Platinum tier attainment (official 🏆 rules)

The official Platinum tier contains exactly three rules (`async-dependency`,
`inject-websession`, `strict-typing`; verified against the live developer-docs
pages 2026-08-24). Bronze, Silver and Gold are fully recorded already, so the
items below are the complete remaining set for full Platinum:

### 5.1 Typed config entry (`strict-typing`, sub-gap 1 of 3)
- [x] Add `type IrishRailConfigEntry = ConfigEntry[IrishRailRuntimeData]`
      (or an equivalent explicit subclass) to `types.py` — implemented
      2026-08-28 as a PEP-695 ``type`` alias in
      ``custom_components/irish_rail/types.py``; the coordinator import
      is guarded by ``TYPE_CHECKING`` to break the
      ``coordinator -> config_flow -> types`` import cycle while
      preserving the static narrowing.
- [x] Use it throughout every entry-taking signature:
      `__init__.py` (setup/listener/unload + registry helper),
      `coordinator.py` (`empty_data_issue_id`, `resolve_scan_interval`,
      coordinator constructor), `config_flow.py`, `sensor.py`,
      `entity.py`, `diagnostics.py`
      — the rule warns explicitly that runtime-data integrations MUST use a
      custom typed `MyIntegrationConfigEntry` throughout; today every
      signature takes bare `ConfigEntry`, so `entry.runtime_data` is not
      statically narrowed anywhere — implemented 2026-08-28 across all
      eight files; ``entity.py`` already used the coordinator
      (``coordinator.config_entry``) and needed no signature change.
- [x] Adjust tests/mocks only if signature checks demand it; suite must stay
      green with coverage ≥95% — implemented 2026-08-28: only the
      ``mock_config_entry`` fixture needed a cast target change to
      ``IrishRailConfigEntry``; the existing ``MockConfigEntry`` is a
      structural ``ConfigEntry`` so no behavioral change was required.
      Suite remains 198 tests green at 100% line coverage.

### 5.2 Strict-typing gate breadth (`strict-typing`, sub-gap 2 of 3)
- [x] Extend `.github/workflows/ci.yml` mypy step from
      `mypy custom_components/irish_rail` to also check
      `tests/components/irish_rail` (mirrors home-assistant/core's
      `.strict-typing` convention of covering the component and its
      tests) — implemented 2026-08-28: the ``integration`` job now
      runs ``mypy custom_components/irish_rail tests/components/irish_rail``
      and the ``library`` job runs ``mypy pyirishrail`` for the
      published package.
- [x] Fix any findings; keep ruff clean — implemented 2026-08-28.
      The initial ``mypy tests/components/irish_rail`` pass surfaced
      54 errors. After the Phase 5.3 test re-organisation (which
      moved ``test_api.py`` to ``tests/pyirishrail/`` and rewired the
      remaining test imports to ``pyirishrail.api``) the count
      dropped to 46. The remaining 46 were addressed in one focused
      pass: added return-type annotations to test helpers
      (``_read_persisted``, ``_walk``, ``_reset_seed_cache``); added
      ``assert ... is not None`` narrowing to ``hass.states.get(...)``
      sites; replaced ``MagicMock()`` and ``SimpleNamespace``
      duck-typed runtime_data assignments with ``cast(``) to the new
      ``IrishRailConfigEntry`` / ``IrishRailRuntimeData`` aliases;
      fixed the import-order issue in
      ``scripts/build_stops_matrix.py``; annotated all
      ``fake_scoped`` callbacks in ``test_matrix_rebuild.py`` with
      ``list[TrainMovement]`` parameters. Final state: ``ruff check``
      clean, ``mypy custom_components/irish_rail
      tests/components/irish_rail pyirishrail`` reports ``Success: no
      issues found in 33 source files``. Suite remains 198 tests
      green at 99.32% combined coverage.

### 5.3 Published async client library (`async-dependency` + PEP-561;
strict-typing sub-gap 3 of 3)
- [x] Formerly Phase 4 item 4.1 (folded into this phase on 2026-08-24; this is
  now the canonical task). It completes three Platinum requirements at once.
- [x] **Layout decision (revised 2026-08-27):** the `pyirishrail` package lives
  in this repository as a sibling of `custom_components/`, not in a separate
  repo. Rationale: the integration is still in testing (no external users),
  so a single-repo layout preserves the full git history of the client code
  and avoids a hollow second repo. The package is still published to PyPI
  as `pyirishrail`; HACS and the integration's `manifest.json` still consume
  it as an external dependency once shipped.

> **2026-08-29 — REVERTED.** The 2026-08-28 extraction above was rolled
> back in commit `1dd240b` ("Complete revert to vendored client and
> remove Phase 5.3 artifacts"). The PyPI name `pyirishrail` is owned
> by an unrelated project, so the v0.3.0 Clean Baseline keeps the
> client **vendored at `custom_components/irish_rail/pyirishrail/`**
> and the integration's `manifest.json` declares zero third-party
> requirements. The reasoning, current layout, and the zero-dep
> proof are recorded in `.cline/clean-cut-baseline-plan.md` and the
> v0.3.0 `CHANGELOG.md`. The vendored client is still framework-
> agnostic, still ships `py.typed`, and the `inject-websession` rule
> is still satisfied at the integration level. The bullet items
> below describe the 2026-08-28 implementation that the revert
> unwound; they remain in the file as a historical record.
- Concrete shape (after this item lands):
  - [x] Move `custom_components/irish_rail/api.py` (+ any helpers worth
        exposing — e.g. model dataclasses, exception types) to a top-level
        `pyirishrail/` package in this repo. Use `git mv` so history follows.
        — implemented 2026-08-28 via `git mv custom_components/irish_rail/api.py
        pyirishrail/api.py` (also `tests/components/irish_rail/test_api.py`
        → `tests/pyirishrail/test_api.py`); the inline errors/models were
        split out into `pyirishrail/errors.py`, `pyirishrail/models.py`,
        `pyirishrail/_const.py`; `git status` shows both as `R` (proper
        rename) so `git log --follow` will trace history once committed.
  - [x] Rewrite `pyproject.toml` to build the `pyirishrail` wheel with
        PEP 621 metadata, `py.typed` (PEP-561) shipped in the wheel, and the
        integration's `custom_components/irish_rail/` declared as
        non-installable (unchanged from today). — implemented 2026-08-28:
        project name is now `pyirishrail` 0.2.0; `packages =
        ["pyirishrail"]`; `[tool.setuptools.package-data] pyirishrail =
        ["py.typed"]`; `aiohttp` and `defusedxml` declared as runtime
        dependencies; `python -m build --wheel` produces a wheel whose
        only contents are the `pyirishrail/` package and the `py.typed`
        marker (no `custom_components/`).
  - [x] Update integration imports: `from .api import ...` →
        `from pyirishrail import ...` across `__init__.py`, `coordinator.py`,
        `config_flow.py`, and any other consumer. — implemented 2026-08-28
        across eight consumer files; the private helper
        `_scoped_journey_stops` is reached deliberately via
        `from pyirishrail.api import _scoped_journey_stops` in
        `matrix_rebuild.py` (cross-package private-symbol contract,
        documented in `pyirishrail/api.py`).
        — **Resolved 2026-08-31:** the cross-package underscore-prefix
        contract documented above is no longer the active pattern. The
        helper is now reached through the public method
        `IrishRailClient.scope_journey_stops(...)`, which the rebuild
        button and the offline `scripts/build_stops_matrix.py` seed
        generator consume directly; `_scoped_journey_stops` remains the
        module-private implementation in `pyirishrail/api.py` and the
        in-class `_async_prune_trains` call site still uses it, but no
        cross-module consumer reaches for the leading-underscore symbol
        anymore. Do not reinstate the cross-package underscore import
        described in the implementation note above; if you need the
        scoping logic, call `client.scope_journey_stops(...)` on an
        `IrishRailClient` instance, or import the helper from
        `pyirishrail.api` only as part of an in-class or in-module
        refactor. The README's "Public API" table and the
        `pyirishrail/__init__.py` docstring were updated in the same
        pass to reflect the new public surface.
  - [x] Update `custom_components/irish_rail/manifest.json`:
        `"requirements": ["pyirishrail>=0.2,<1.0"]`; remove `defusedxml`
        (it becomes a transitive dep of `pyirishrail` and listing transitive
        requirements in `manifest.json` is discouraged by HA core).
        — implemented 2026-08-28; integration version also bumped to
        `0.2.0` and `quality_scale` to `"platinum"`.
  - [x] Split `tests/` into `tests/pyirishrail/` (pure library tests, no HA
        imports allowed) and `tests/components/irish_rail/` (HA integration
        tests, unchanged). Coverage targets (≥95%) apply per suite.
        — implemented 2026-08-28; `tests/pyirishrail/test_api.py` (43 tests,
        100% line coverage) imports only `pyirishrail`; the integration
        suite (155 tests) lives in `tests/components/irish_rail/` with
        HA fixtures intact.
  - [x] Reshape CI as a matrix: `library` job (ruff, mypy strict on
        `pyirishrail/`, pytest on `tests/pyirishrail/`, build wheel,
        publish to TestPyPI on PRs / PyPI on tag pushes) and `integration`
        job (installs the built wheel, runs ruff + mypy strict on
        `custom_components/irish_rail/` and `tests/components/irish_rail/`,
        pytest with ≥95% coverage). This naturally lands the test-inclusive
        mypy gate for Phase 5.2. — implemented 2026-08-28 in
        `.github/workflows/ci.yml`. Both jobs share the `hassfest`
        validation pass. The `library` job's "publish" steps were left
        to the operator to add once the PyPI/TestPyPI credentials
        secrets are configured; the wheel build + zipfile inspection
        run unconditionally so any broken wheel is caught in CI.
  - [x] Record the `defusedxml` decision in this repo (the rule applies to
        the published package, which lives here): the official rule has *no
        exceptions* ("Dependency is async"), so either justify
        parser-on-fetched-bytes usage with evidence (`defusedxml` is a pure
        XML parser, not an HTTP client, and is invoked only on bytes already
        fetched by `aiohttp`) or replace it. — implemented 2026-08-28 in
        `pyirishrail/README.md` (full "defusedxml and the Platinum
        async-dependency rule" section + cross-link from
        `quality_scale.yaml::async_dependency`).
- Note: `inject-websession` needs no code change — the package keeps the
  injected-session constructor (already recorded `done` in
  `quality_scale.yaml`).
- Out of scope for this item (intentionally deferred): splitting the package
  into its own GitHub repo. Reopen if a future contributor needs an
  independent release cadence or wants to onboard non-HA consumers via a
  dedicated repo.

### 5.4 Evidence recording & tier claim (after 5.1–5.3)
- [x] Flip `async_dependency` and `strict_typing` to `done` in
      `quality_scale.yaml` with file/function pointers (do this only when
      genuinely satisfied — skill-pack rule) — implemented 2026-08-28;
      `inject_websession` was already `done`; the other two
      Platinum rules (`async_dependency`, `strict_typing`) are now
      `done` with file/function pointers and links to the
      `defusedxml` decision in `pyirishrail/README.md`.
- [x] Bump `manifest.json` `"quality_scale"` from `"gold"` to `"platinum"`
- [x] Update the README quality-scale paragraph and the quick-facts list
      — implemented 2026-08-28: the README quick-facts table now reads
      "**Platinum** — every Bronze/Silver/Gold/Platinum rule done or
      exempt; evidence in [quality_scale.yaml]".
- [x] Re-run standing gates: ruff clean · strict mypy clean · pytest ≥95%
      — verified 2026-08-28 (final state of the implementation):
        * ``ruff check .`` → "All checks passed!"
        * ``mypy custom_components/irish_rail tests/components/irish_rail
          pyirishrail`` → "Success: no issues found in 33 source files"
        * ``pytest tests/components/irish_rail tests/pyirishrail
          --cov=custom_components/irish_rail --cov=pyirishrail
          --cov-fail-under=95`` → "198 passed" at 99.32% combined
          coverage (library 100% / integration 99.13%).

---

## Execution order & acceptance criteria

| Order | Item | Acceptance |
|---|---|---|
| 1 | 1.1 Reconfigure flow | Flow works in UI; tests pass; docs updated |
| 2 | 1.2 Options flow | Interval changeable 30s–10min; coordinator honors it |
| 3 | 1.3 Next-N-trains | New entities live; translations; tests |
| 4 | 1.4 No-trains semantics | Attribute/logic distinguishes empty vs error; tests |
| 5 | Phase 2 items | Each rule demonstrably satisfied; coverage ≥95% |
| 6 | Phase 3 docs | README sections added; icons.json; repair flow |
| 7 | 4.2–4.7 robustness & engineering | Evidence recorded per item |
| 8 | Phase 5.1–5.2 (strict typing in-repo) | Typed IrishRailConfigEntry used throughout; mypy gates integration *and* tests |
| 9 | Phase 5.3–5.4 (tier completion; absorbs former 4.1) | **`pyirishrail` package vendored at `custom_components/irish_rail/pyirishrail/`** (the 2026-08-28 PyPI extraction was reverted 2026-08-29; see the Phase 5.3 subsection); all three 🏆 rules `done` in `quality_scale.yaml`; `manifest.json` declares `requirements: []`; README claims Platinum |

**Standing requirements for every phase:** ruff clean · strict mypy clean ·
all tests passing · coverage ≥90% (target ≥95%) · no blocking calls in async
paths · `quality_scale.yaml` updated whenever a rule's status changes.