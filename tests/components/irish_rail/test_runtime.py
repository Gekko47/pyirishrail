"""Runtime registry: shared singletons and per-hass lifecycle.

The :class:`custom_components.irish_rail._runtime.RuntimeRegistry` is
the single writer to ``hass.data[DOMAIN]`` for the integration's shared
state: the loaded-entry set, the :class:`RequestGate` instance, the
:class:`ConnectivityMonitor`, and the global-entity provider
key. These tests verify the lifecycle end-to-end:

* the gate singleton survives an unload/reload cycle (the only
  writer is the registry, not the call site);
* two config entries on one HA instance share one gate (the
  release only happens at zero loaded entries) and one movement-history
  cache (so a train code serving both stations is resolved once);
* the lazy ``async_get_request_gate`` is safe to call before any
  entry is loaded (the config flow and options flow both rely on
  this);
* the health monitor's lifecycle tracks ``loaded_entry_ids`` as a
  set (a re-setup on retry cannot double-count);
* the global-entity provider claim is freed on full removal, not
  unload, and the orphan-purge on reclaim leaves live-owned rows
  alone.

The :class:`ConnectivityMonitor` *probe* semantics (failure
tracking, ``as_dict`` snapshot, ``recently_confirmed_healthy``) live
in ``test_health.py``; the per-bucket persistence guard for the
stops-matrix rebuild lives in ``test_matrix_rebuild.py``.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail._runtime import (
    ConnectivityMonitor,
    async_get_movement_cache,
    async_get_request_gate,
    async_note_entry_loaded,
    async_note_entry_unloaded,
    async_promote_on_removal,
    async_promote_provider,
    async_release_request_gate,
    disown_provider_if,
    elect_provider,
    get_health_monitor,
    get_request_gate,
    get_runtime,
    pop_session_value,
    set_session_value,
)
from custom_components.irish_rail.config_flow import (
    IrishRailConfigFlow,
    IrishRailOptionsFlow,
)
from custom_components.irish_rail.const import (
    CONF_DIRECTION,
    CONF_STATION,
    CONF_STATION_CODE,
    DOMAIN,
    GLOBAL_LAST_REBUILD_KEY,
    GLOBAL_PROVIDER_KEY,
    GLOBAL_REBUILD_ENTITY_KEY,
)
from custom_components.irish_rail.request_gate import RequestGate
from custom_components.irish_rail.store import (
    STOPS_STORE_INSTANCE,
    get_stops_store,
)
from custom_components.irish_rail.types import IrishRailConfigEntry


def _add_entry(
    hass: HomeAssistant, unique_id: str = "PEARS_northbound"
) -> IrishRailConfigEntry:
    """Register one minimal Irish Rail config entry on ``hass``."""
    # ``MockConfigEntry`` is a structural ``ConfigEntry`` but its
    # static return type is the bare ``ConfigEntry``; pin it to the
    # integration's typed alias so callers can use ``runtime_data``
    # without mypy noise. ``add_to_hass`` is a ``MockConfigEntry``
    # method, not on the runtime ``ConfigEntry``, so we call it
    # before the cast.
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            CONF_STATION: "Dublin Pearse",
            CONF_STATION_CODE: "PEARS",
            CONF_DIRECTION: "Northbound",
        },
        unique_id=unique_id,
    )
    mock_entry.add_to_hass(hass)
    return cast(IrishRailConfigEntry, mock_entry)


async def test_setup_creates_shared_request_gate_singleton(
    hass: HomeAssistant,
) -> None:
    """Entry setup wires its client to the per-HA shared gate.

    The coordinator's :class:`IrishRailClient` must carry the same
    :class:`RequestGate` instance the integration stashes on
    ``hass.data[DOMAIN]``, and ``get_request_gate`` must return that
    exact instance. Without this contract every client would each
    hold its own gate and the rate budget would be per-client instead
    of per-``HomeAssistant``.
    """
    assert get_request_gate(hass) is None
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    shared = get_request_gate(hass)
    assert isinstance(shared, RequestGate)
    # The coordinator's client points at the very same gate.
    assert entry.runtime_data.client._gate is shared


async def test_second_entry_reuses_the_same_shared_gate(
    hass: HomeAssistant,
) -> None:
    """Two config entries on one HA instance share one gate.

    Pinning the singleton's reuse across entries: a second entry
    setup must hand the new client the *same* ``RequestGate`` the
    first one got, not a fresh one.
    """
    entry_a = _add_entry(hass, unique_id="PEARS_northbound")
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry_a.entry_id)
        await hass.async_block_till_done()
        first_gate = get_request_gate(hass)
        assert isinstance(first_gate, RequestGate)

        # Add the second entry only after the component is loaded: HA's
        # component setup sets up every entry already registered for the
        # domain, so an entry added before the first setup would be loaded
        # by the first ``async_setup`` call itself and an explicit second
        # ``async_setup`` would raise ``OperationNotAllowed``.
        entry_b = _add_entry(hass, unique_id="PEARS_southbound")
        assert await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()
        second_gate = get_request_gate(hass)

    # Same singleton across both entries.
    assert second_gate is first_gate
    assert entry_a.runtime_data.client._gate is first_gate
    assert entry_b.runtime_data.client._gate is first_gate


async def test_second_entry_reuses_the_same_movement_cache(
    hass: HomeAssistant,
) -> None:
    """Two entries on one HA instance share one route cache.

    A train code serving both monitored stations is resolved once per
    HA instance instead of once per entry, and the cache is dropped with
    the last entry like every other shared singleton.
    """
    entry_a = _add_entry(hass, unique_id="PEARS_northbound")
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry_a.entry_id)
        await hass.async_block_till_done()
        shared = async_get_movement_cache(hass)
        assert entry_a.runtime_data.client._movement_cache is shared

        entry_b = _add_entry(hass, unique_id="PEARS_southbound")
        assert await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()

    assert entry_b.runtime_data.client._movement_cache is shared

    # A route warmed by one entry is served from cache for the other.
    shared[("E123", "01 Jan 2026")] = [MagicMock()]
    assert shared[("E123", "01 Jan 2026")] in (
        entry_a.runtime_data.client._movement_cache.values()
    )

    # The cache is released with the last entry, not the first.
    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()
    assert shared
    assert await hass.config_entries.async_unload(entry_b.entry_id)
    await hass.async_block_till_done()
    assert not shared


async def test_unload_last_entry_drops_the_shared_gate(
    hass: HomeAssistant,
) -> None:
    """The shared gate is released on the last entry's unload.

    ``async_release_request_gate`` runs from ``async_unload_entry``;
    after the last entry leaves, the singleton is gone so a fresh
    entry gets a brand-new gate (cheap, but the lifecycle is
    symmetric with the rest of the ``hass.data[DOMAIN]``-keyed
    singletons the integration owns).
    """
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert isinstance(get_request_gate(hass), RequestGate)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_request_gate(hass) is None


async def test_unloading_one_of_two_entries_keeps_the_shared_gate(
    hass: HomeAssistant,
) -> None:
    """Unloading a sibling must not drop the gate the survivor uses.

    The gate is released only when the last loaded entry leaves; releasing
    on every unload would strand the surviving entry on a dropped gate
    while new clients built a second one, splitting the shared rate
    budget. The health probe follows the same lifetime.
    """
    entry_a = _add_entry(hass, unique_id="PEARS_northbound")
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry_a.entry_id)
        await hass.async_block_till_done()
        entry_b = _add_entry(hass, unique_id="PEARS_southbound")
        assert await hass.config_entries.async_setup(entry_b.entry_id)
        await hass.async_block_till_done()

    shared = get_request_gate(hass)
    assert isinstance(shared, RequestGate)
    monitor = get_health_monitor(hass)
    assert monitor is not None
    assert monitor.as_dict()["timer_active"] is True

    assert await hass.config_entries.async_unload(entry_a.entry_id)
    await hass.async_block_till_done()
    # The sibling entry is still loaded: the gate and the probe survive.
    assert get_request_gate(hass) is shared
    assert entry_b.runtime_data.client._gate is shared
    assert get_health_monitor(hass) is monitor
    assert monitor.as_dict()["timer_active"] is True

    # Only the last unload releases both singletons.
    assert await hass.config_entries.async_unload(entry_b.entry_id)
    await hass.async_block_till_done()
    assert get_request_gate(hass) is None
    assert monitor.as_dict()["timer_active"] is False


async def test_user_config_flow_uses_the_shared_gate(
    hass: HomeAssistant,
) -> None:
    """The user config flow's lazy client shares the gate.

    The :class:`IrishRailConfigFlow._get_client` path is the one the
    user config flow takes when discovering stations and directions
    during setup. The flow does not know whether an entry is loaded
    yet, so it just calls ``async_get_request_gate(hass)`` — which
    creates the singleton on first use and reuses it on every
    subsequent call.
    """
    # No entry loaded yet: the gate is created lazily by the config
    # flow, not by entry setup.
    assert get_request_gate(hass) is None
    flow = IrishRailConfigFlow()
    flow.hass = hass
    client = flow._get_client()
    assert client._gate is get_request_gate(hass)
    # Second call returns the same singleton (not a fresh gate).
    same = flow._get_client()
    assert same._gate is client._gate
    # And the gate is the singleton the integration now owns.
    assert isinstance(get_request_gate(hass), RequestGate)
    # Drop the lazily-created gate so it does not leak across tests.
    async_release_request_gate(hass)


async def test_options_flow_uses_the_shared_gate(
    hass: HomeAssistant,
) -> None:
    """The options flow's lazy client also shares the gate.

    The :class:`IrishRailOptionsFlow._get_client` path is the one the
    reconfigure/options UI takes when re-discovering directions or
    stops-at candidates. It must share the per-HA gate for the same
    reason the user config flow does. We exercise it through HA's
    own ``async_get_options_flow`` plumbing (so ``config_entry`` is
    wired by the framework, not by hand) and then call ``_get_client``
    on the resulting flow to confirm the gate-sharing holds.
    """
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    shared = get_request_gate(hass)
    assert isinstance(shared, RequestGate)

    # ``IrishRailConfigFlow.async_get_options_flow`` is the
    # staticmethod the integration registers for its options flow.
    # It returns a freshly-constructed ``IrishRailOptionsFlow``;
    # the read-only ``config_entry`` property is set by the
    # framework when the flow is dispatched in a real request, but
    # the gate-sharing path (``_get_client``) only reads
    # ``self.hass``, which the test sets up below.
    flow = IrishRailConfigFlow.async_get_options_flow(entry)
    assert isinstance(flow, IrishRailOptionsFlow)
    flow.hass = hass
    assert flow._get_client()._gate is shared


async def test_async_get_request_gate_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """``async_get_request_gate`` returns the same gate on every call.

    Direct unit-style check of the singleton helper: successive calls
    without an intervening release must hand back the exact same
    ``RequestGate`` instance.
    """
    a = async_get_request_gate(hass)
    b = async_get_request_gate(hass)
    assert a is b
    async_release_request_gate(hass)
    c = async_get_request_gate(hass)
    # Post-release, a new gate is created (different instance).
    assert c is not a


# ── Loaded-entry lifecycle (RuntimeRegistry.ensure_health_monitor) ────────


def _entry(hass: HomeAssistant, unique_id: str = "PEARS_Northbound") -> MockConfigEntry:
    """Register one minimal Irish Rail config entry on hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    return entry


def _client() -> MagicMock:
    """Build a mock IrishRailClient whose station probe can fail."""
    client = MagicMock()
    client.async_get_station_by_code = AsyncMock(return_value=[MagicMock()])
    return client


async def test_monitor_lifecycle_tracks_loaded_entries(
    hass: HomeAssistant,
) -> None:
    """The monitor starts once, survives sibling loads and stops at zero.

    Lifecycle is tracked by a set of loaded entry ids (not a counter), so a
    re-setup - e.g. an automatic retry after ConfigEntryNotReady - cannot
    double-count and the probe can never be left running by phantom counts.
    """
    client = _client()

    # The first loaded entry returns True and starts the probe.
    assert await async_note_entry_loaded(hass, "E1", client) is True
    first = get_health_monitor(hass)
    assert isinstance(first, ConnectivityMonitor)

    # A second entry reuses the same singleton without restarting anything.
    assert await async_note_entry_loaded(hass, "E2", client) is False
    assert get_health_monitor(hass) is first
    # Internal detail, checked deliberately: one running subscription.
    assert first._unsub_interval is not None

    # Re-registering the same entry (setup retry) is idempotent.
    assert await async_note_entry_loaded(hass, "E1", client) is False
    assert get_health_monitor(hass) is first
    assert first._unsub_interval is not None

    # Unloading a sibling keeps the probe running.
    assert await async_note_entry_unloaded(hass, "E2") is False
    assert get_health_monitor(hass) is first
    assert first._unsub_interval is not None

    # Only when the last entry unloads does probing pause.
    assert await async_note_entry_unloaded(hass, "E1") is True
    assert get_health_monitor(hass) is first
    assert first._unsub_interval is None

    # And it restarts cleanly for subsequent entries.
    assert await async_note_entry_loaded(hass, "E3", client) is True
    assert first._unsub_interval is not None

    # Leave no lingering interval timer for the next test.
    await first.async_stop()


async def test_unload_without_any_registry_reports_true(
    hass: HomeAssistant,
) -> None:
    """An unload when no runtime was ever created is a no-op success.

    async_note_entry_unloaded is normally called from async_unload_entry,
    which only runs for a loaded entry, so the no-registry path is
    defensive. It must report "no entries remain" and neither create
    state nor start/stop anything.
    """
    assert await async_note_entry_unloaded(hass, "NEVER_LOADED") is True
    assert get_runtime(hass) is None


# ── Global-entity providership arbitration ──────────────────────────────────


async def test_first_setup_claims_global_provider(
    hass: HomeAssistant,
) -> None:
    """The first loaded entry wins; siblings are denied, owner sticky.

    Ownership is decided against ``loaded_entry_ids``, and
    ``async_setup_entry`` notes an entry loaded *before* forwarding
    platforms, so the first entry to reach ``elect_provider`` is always
    already in the set.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()

    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert elect_provider(hass, entry_one) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False
    assert elect_provider(hass, entry_two) is False
    # Owner re-claiming stays True.
    assert elect_provider(hass, entry_one) is True


async def test_elect_provider_sweeps_a_departed_owner_s_rows(
    hass: HomeAssistant,
) -> None:
    """Re-electing over a departed owner purges that owner's rows first.

    When the recorded owner is no longer loaded but is still recorded
    (the state a removal leaves behind), the new owner must clear the old
    entity/device rows before adding its own, or the additions collide
    with the departed owner's.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert elect_provider(hass, entry_one) is True

    # entry_one is no longer loaded, but the key still names it: the
    # state a departed-but-recorded owner leaves behind.
    await async_note_entry_unloaded(hass, entry_one.entry_id)
    set_session_value(hass, GLOBAL_PROVIDER_KEY, entry_one.entry_id)

    with patch(
        "custom_components.irish_rail._runtime._purge_orphan_global_entities"
    ) as purge:
        assert elect_provider(hass, entry_two) is True

    assert purge.call_count == 1
    assert purge.call_args.kwargs["expected_owner"] == entry_one.entry_id
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_two.entry_id


async def test_elect_provider_replaces_an_unloaded_owner(
    hass: HomeAssistant,
) -> None:
    """A loaded owner wins; an owner no longer loaded does not block.

    This is the HIGH-1 fix. The previous check asked
    ``hass.config_entries.async_entries(DOMAIN)`` whether the recorded
    owner was still *installed*, which ignores load state, so an owner
    that had been unloaded pinned the key for the rest of the session
    even though nothing owned the globals any more.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()

    assert elect_provider(hass, entry_one) is True
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False

    # entry_one is now unloaded but still installed, so the old check
    # would have kept denying the sibling.
    await async_note_entry_unloaded(hass, entry_one.entry_id)

    assert elect_provider(hass, entry_two) is True
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_two.entry_id


async def test_promotion_on_removal_ignores_unrelated_and_unknown_entries(
    hass: HomeAssistant,
) -> None:
    """Only a provider awaiting promotion triggers one, and only once.

    A removal of an entry that never owned the globals, or a removal with
    no runtime at all, must be a no-op rather than promoting a stranger.
    """
    # No registry has ever existed: nothing to do, and no state created.
    async_promote_on_removal(hass, "NEVER_LOADED")
    assert get_runtime(hass) is None

    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False
    assert elect_provider(hass, entry_one) is True

    # A sibling that was never the provider is removed: no promotion.
    async_promote_on_removal(hass, entry_two.entry_id)
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id

    # The provider unloads, which arms a pending promotion...
    await async_note_entry_unloaded(hass, entry_one.entry_id)
    registry = get_runtime(hass)
    assert registry is not None
    assert entry_one.entry_id in registry.pending_promotions

    # ...and is then removed, which promotes the survivor exactly once.
    async_promote_on_removal(hass, entry_one.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_two.entry_id

    # The pending marker is consumed, so a repeat is inert.
    async_promote_on_removal(hass, entry_one.entry_id)
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_two.entry_id


async def test_promotion_without_a_registry_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """No runtime means no owner, so promotion declines rather than raises."""
    assert await async_promote_provider(hass) is False
    assert get_runtime(hass) is None


async def test_promotion_is_skipped_when_the_owner_already_reclaimed(
    hass: HomeAssistant,
) -> None:
    """A reload that re-claims first must not be overtaken.

    ``ConfigEntryChange.REMOVED`` only fires for removals, but the
    provider key can already be repopulated by a re-entering entry; the
    guard makes that case a no-op rather than a second election.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False
    assert elect_provider(hass, entry_one) is True

    await async_note_entry_unloaded(hass, entry_one.entry_id)
    registry = get_runtime(hass)
    assert registry is not None
    assert entry_one.entry_id in registry.pending_promotions

    # The owner comes back and re-claims before the removal signal lands.
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is False
    assert elect_provider(hass, entry_one) is True

    async_promote_on_removal(hass, entry_one.entry_id)
    await hass.async_block_till_done()

    # Ownership stayed with the returning owner.
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id


async def test_promotion_gives_up_when_the_survivor_vanished(
    hass: HomeAssistant,
) -> None:
    """A loaded-set member with no config entry is reported, not crashed.

    Defensive: the loaded set and the config-entry store are updated by
    separate calls, so a mismatch must degrade to a warning rather than
    an AttributeError raised inside the removal signal.
    """
    entry_one = _entry(hass)
    client = _client()
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert elect_provider(hass, entry_one) is True

    await async_note_entry_unloaded(hass, entry_one.entry_id)
    registry = get_runtime(hass)
    assert registry is not None

    # A "loaded" survivor that has no config entry: promotion cannot
    # elect it, so it warns and leaves the globals unowned.
    registry.loaded_entry_ids.add("GHOST")
    assert await async_promote_provider(hass) is False
    assert GLOBAL_PROVIDER_KEY not in hass.data[DOMAIN]


async def test_disown_only_clears_the_named_owner(
    hass: HomeAssistant,
) -> None:
    """``disown_provider_if`` is a no-op for a non-owner."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    assert elect_provider(hass, entry_one) is True

    assert disown_provider_if(hass, entry_two.entry_id) is False
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id

    assert disown_provider_if(hass, entry_one.entry_id) is True
    assert DOMAIN in hass.data
    assert GLOBAL_PROVIDER_KEY not in hass.data[DOMAIN]


async def test_promotion_is_deterministic_and_skipped_when_owned(
    hass: HomeAssistant,
) -> None:
    """Promotion picks the lowest loaded id and never steals an owner."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()

    # No loaded entries yet: nothing to promote.
    assert await async_promote_provider(hass) is False

    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is False

    # The invariant is min() over the loaded set, so the winner does not
    # depend on which entry happened to load first.
    assert await async_promote_provider(hass) is True
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == min(
        entry_one.entry_id, entry_two.entry_id
    )

    # An existing owner is never displaced.
    assert await async_promote_provider(hass) is False
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == min(
        entry_one.entry_id, entry_two.entry_id
    )


async def test_promotion_is_skipped_when_the_departing_entry_returns(
    hass: HomeAssistant,
) -> None:
    """A plain reload re-claims for itself instead of being shunted.

    ``async_note_entry_unloaded`` fires for a reload too. The deferred
    promotion must not hand the globals to a sibling in that window; the
    returning entry simply re-claims when its platforms forward again.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()

    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False
    assert elect_provider(hass, entry_one) is True

    # Reload: unload, then the same entry re-registers before the
    # deferred promotion task gets to run.
    await async_note_entry_unloaded(hass, entry_one.entry_id)
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is False
    await hass.async_block_till_done()

    # No promotion happened, so ownership is unclaimed and entry_one
    # takes it straight back on its next platform setup.
    assert GLOBAL_PROVIDER_KEY not in hass.data[DOMAIN]
    assert elect_provider(hass, entry_one) is True
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id


async def test_claim_is_idempotent_for_the_owner(
    hass: HomeAssistant,
) -> None:
    """Repeated claims by the same entry succeed without side effects.

    The claim is checked-then-acted on shared ``hass.data`` state; the
    idempotent fast path (current owner == caller) must never purge
    entity rows or transfer ownership, no matter how often setup retries
    re-invoke it.
    """
    entry_one = _entry(hass)
    assert elect_provider(hass, entry_one) is True
    for _ in range(5):
        assert elect_provider(hass, entry_one) is True
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id

    # A *loaded* sibling can never steal the claim, however often it
    # retries - only the owner's departure frees it.
    entry_two = _entry(hass, unique_id="KENT_all")
    await async_note_entry_loaded(hass, entry_one.entry_id, _client())
    for _ in range(3):
        assert elect_provider(hass, entry_two) is False
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_one.entry_id


# ── Listener lifecycle on the connectivity monitor ──────────────────────────


async def test_monitor_stop_clears_listeners_and_resets_state(
    hass: HomeAssistant,
) -> None:
    """``async_stop`` drops every listener so entities cannot leak.

    Entities register themselves on the monitor's listener set; if
    ``async_stop`` left the set populated, entity objects (and through
    them the whole entity platform) would stay pinned after an unload,
    leaking memory on every reload cycle.
    """
    monitor = ConnectivityMonitor(hass, _client())
    assert await async_note_entry_loaded(hass, "E1", MagicMock()) is True
    registry_monitor = get_health_monitor(hass)
    assert registry_monitor is not None
    await async_note_entry_unloaded(hass, "E1")

    # Rebuild a clean monitor for the direct assertions below.
    monitor = ConnectivityMonitor(hass, _client())
    calls: list[str] = []

    def listener_a() -> None:
        calls.append("a")

    def listener_b() -> None:
        calls.append("b")

    remove_a = monitor.add_listener(listener_a)
    monitor.add_listener(listener_b)
    assert monitor.listeners == {listener_a, listener_b}

    # The removal callback drops exactly its own listener.
    remove_a()
    assert monitor.listeners == {listener_b}

    # And a double remove is a no-op, not an error.
    remove_a()
    assert monitor.listeners == {listener_b}

    monitor.listeners.add(listener_a)

    async def _noop_probe() -> None:
        """Never called: the interval is stopped before it can fire."""
        raise AssertionError("probe must not fire after async_stop")

    monitor.async_ping = _noop_probe  # type: ignore[method-assign]
    await monitor.async_stop()

    assert monitor.listeners == set()
    # State is reset so a restart starts from a clean unknown slate.
    assert monitor.healthy is None
    assert monitor.consecutive_failures == 0


async def test_monitor_add_listener_callback_removes_only_itself(
    hass: HomeAssistant,
) -> None:
    """The removal callback returned by ``add_listener`` is self-scoped."""
    monitor = ConnectivityMonitor(hass, _client())
    first: list[str] = []
    second: list[str] = []

    remove_first = monitor.add_listener(lambda: first.append("x"))
    remove_second = monitor.add_listener(lambda: second.append("y"))
    assert len(monitor.listeners) == 2

    remove_first()
    assert len(monitor.listeners) == 1

    # The second listener is untouched and its own callback still works.
    remove_second()
    assert monitor.listeners == set()


# ── hass.data[DOMAIN] cleanup at zero loaded entries ────────────────────────


async def test_last_unload_drops_session_scoped_keys(
    hass: HomeAssistant,
) -> None:
    """Unloading the final entry drops entity/result/store references.

    ``GLOBAL_REBUILD_ENTITY_KEY`` pins the live button entity and
    ``GLOBAL_LAST_REBUILD_KEY`` pins its last result: if they survived
    the last unload, the entity object (and its client) would leak for
    the rest of the session. Every dropped key is recreated lazily, and
    the stops matrix itself is persisted on disk, so the cleanup is
    lossless.
    """
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    domain_data = hass.data[DOMAIN]
    # The global handle exists while the entry is loaded (the button
    # registered it) and the store singleton is materialized lazily.
    domain_data.setdefault(GLOBAL_LAST_REBUILD_KEY, None)
    get_stops_store(hass)
    assert STOPS_STORE_INSTANCE in domain_data
    assert GLOBAL_PROVIDER_KEY in domain_data

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert GLOBAL_PROVIDER_KEY not in domain_data
    assert GLOBAL_LAST_REBUILD_KEY not in domain_data
    assert GLOBAL_REBUILD_ENTITY_KEY not in domain_data
    assert STOPS_STORE_INSTANCE not in domain_data
    # The registry itself survives so the monitor object (and its probe
    # history) can be reused by the next load cycle.
    assert get_runtime(hass) is not None
    # A subsequent load recreates the store singleton on demand.
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert STOPS_STORE_INSTANCE not in hass.data[DOMAIN]
    get_stops_store(hass)
    assert STOPS_STORE_INSTANCE in hass.data[DOMAIN]


# ── Health-monitor client rebinding across a full unload ─────────────────────


async def test_monitor_rebinds_client_after_full_unload_reload(
    hass: HomeAssistant,
) -> None:
    """A reload re-points the probe at the new client's shared gate.

    A full unload drops the shared gate but keeps the monitor object, so
    without the re-point the probe would keep drawing its rate budget from
    the discarded gate and stop being paced alongside live polling.
    """
    entry = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        first_monitor = get_health_monitor(hass)
        assert first_monitor is not None
        first_client = first_monitor.client

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        # The gate is released at zero entries but the monitor survives.
        assert get_runtime(hass) is not None
        released_gate = get_request_gate(hass)
        assert released_gate is None

        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    rebound = get_health_monitor(hass)
    assert rebound is not None
    # The same monitor object is reused ...
    assert rebound is first_monitor
    # ... but now talks through the newly created gate.
    assert rebound.client is not first_client
    assert get_request_gate(hass) is rebound.client._gate


async def test_monitor_does_not_steal_client_from_loaded_entry(
    hass: HomeAssistant,
) -> None:
    """A second loaded entry must not repoint a live monitor's client.

    Rebinding is only safe when no entry owns the monitor; while the first
    entry is loaded its client is the one pacing live traffic.
    """
    first = _add_entry(hass)
    with patch(
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        new=AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()

        monitor = get_health_monitor(hass)
        assert monitor is not None
        owner_client = monitor.client

        second = _add_entry(hass, unique_id="PEARS_southbound")
        assert await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    assert get_health_monitor(hass) is monitor
    assert monitor.client is owner_client


async def test_release_without_a_monitor_still_drops_the_gate(
    hass: HomeAssistant,
) -> None:
    """A registry that never started a probe releases cleanly.

    ``async_release`` is called on the last unload; a registry whose
    monitor was never created (setup failed after the gate was handed
    out) must not raise on the way down.
    """
    # ``async_get_request_gate`` is the public way to create the registry.
    assert isinstance(async_get_request_gate(hass), RequestGate)
    registry = get_runtime(hass)
    assert registry is not None
    registry.movement_cache[("E1", "01 Jan 2026")] = []

    await registry.async_release()

    assert registry.request_gate is None
    assert registry.movement_cache == {}
    assert registry.loaded_entry_ids == set()


async def test_popping_a_session_value_without_a_domain_bucket_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """Tearing down before anything was stored must not raise."""
    assert DOMAIN not in hass.data

    pop_session_value(hass, GLOBAL_PROVIDER_KEY)

    assert DOMAIN not in hass.data


async def test_releasing_the_gate_without_a_registry_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """An instance that never created a registry has no gate to drop."""
    assert get_runtime(hass) is None

    async_release_request_gate(hass)

    assert get_runtime(hass) is None


async def test_promotion_registers_the_service_even_without_global_entities(
    hass: HomeAssistant,
) -> None:
    """Ownership transfers even when the entity builders yield nothing.

    ``build_rebuild_button`` / ``build_connectivity_sensor`` return None
    when their platform is not loaded on the survivor. The service alias
    must still be (re)registered, otherwise a surviving station loses
    the rebuild action the removed owner used to provide.
    """
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")
    client = _client()
    assert await async_note_entry_loaded(hass, entry_one.entry_id, client) is True
    assert await async_note_entry_loaded(hass, entry_two.entry_id, client) is False
    assert elect_provider(hass, entry_one) is True
    await async_note_entry_unloaded(hass, entry_one.entry_id)

    with (
        patch(
            "custom_components.irish_rail.button.build_rebuild_button",
            return_value=None,
        ),
        patch(
            "custom_components.irish_rail.binary_sensor.build_connectivity_sensor",
            return_value=None,
        ),
        patch(
            "custom_components.irish_rail.button.register_rebuild_service"
        ) as register,
    ):
        assert await async_promote_provider(hass) is True

    register.assert_called_once_with(hass)
    assert hass.data[DOMAIN][GLOBAL_PROVIDER_KEY] == entry_two.entry_id
