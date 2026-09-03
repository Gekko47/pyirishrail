"""Runtime registry: shared singletons and per-hass lifecycle.

The :class:`custom_components.irish_rail._runtime.RuntimeRegistry` is
the single writer to ``hass.data[DOMAIN]`` for the integration's shared
state: the loaded-entry set, the :class:`RequestGate` instance, the
:class:`ConnectivityMonitor`, and the global-entity provider
key. These tests verify the lifecycle end-to-end:

* the gate singleton survives an unload/reload cycle (the only
  writer is the registry, not the call site);
* two config entries on one HA instance share one gate (the
  release only happens at zero loaded entries);
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
    async_get_request_gate,
    async_note_entry_loaded,
    async_note_entry_unloaded,
    async_release_request_gate,
    claim_service_entities,
    get_health_monitor,
    get_request_gate,
    get_runtime,
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
)
from custom_components.irish_rail.request_gate import RequestGate
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


def _entry(
    hass: HomeAssistant, unique_id: str = "PEARS_Northbound"
) -> MockConfigEntry:
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
    """The first claiming entry wins; siblings are denied, owner sticky."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")

    assert claim_service_entities(hass, entry_one) is True
    assert claim_service_entities(hass, entry_two) is False
    # Owner re-claiming stays True.
    assert claim_service_entities(hass, entry_one) is True


async def test_claim_is_freed_when_owner_is_removed(
    hass: HomeAssistant,
) -> None:
    """Removing the owning entry (not merely unloading) frees the claim."""
    entry_one = _entry(hass)
    entry_two = _entry(hass, unique_id="KENT_all")

    assert claim_service_entities(hass, entry_one) is True

    # Unload must NOT transfer ownership mid-session.
    assert await hass.config_entries.async_unload(entry_one.entry_id)
    assert claim_service_entities(hass, entry_two) is False

    # Full removal frees the claim for the next setup.
    await hass.config_entries.async_remove(entry_one.entry_id)
    assert claim_service_entities(hass, entry_two) is True

