"""Verify the integration wires a single shared :class:`RequestGate` per HA.

The ``RequestGate`` is the integration's single point of admission
against the public ``api.irishrail.ie`` endpoints, and the integration
is built around one shared gate per ``HomeAssistant`` instance: the
coordinator's client, both config-flow clients and (transitively, via
the entry's client) the health probe and the stops-matrix rebuild all
draw from that one gate. These tests pin the wiring so a future
refactor cannot silently fall back to per-client gates (which would
defeat the gate's purpose).
"""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
from custom_components.irish_rail.gate import (
    async_get_request_gate,
    async_release_request_gate,
    get_request_gate,
)
from custom_components.irish_rail.pyirishrail import RequestGate
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
        "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
        "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
        "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert isinstance(get_request_gate(hass), RequestGate)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert get_request_gate(hass) is None


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
        "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
