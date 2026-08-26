"""Tests for the global stops-matrix rebuild button."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.button import (
    SERVICE_REBUILD,
    IrishRailRebuildStopsMatrixButton,
    _runtime_client,
)
from custom_components.irish_rail.const import DOMAIN, GLOBAL_REBUILD_UNIQUE_ID


class _FakeMovement:
    """Attribute-compatible stand-in for a scoping result row."""

    def __init__(self, location: str) -> None:
        self.location = location


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up an entry whose APIs answer successfully (empty data)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_Northbound",
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            new=AsyncMock(return_value=[]),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _rebuild_entity_id(hass: HomeAssistant) -> str | None:
    """Return the global rebuild button's entity id, if registered."""
    registry = er.async_get(hass)
    for entry in registry.entities.values():
        if entry.unique_id == GLOBAL_REBUILD_UNIQUE_ID:
            return entry.entity_id
    return None


def _successful_rebuild_patches() -> dict[str, Any]:
    """Patch every dependency of one successful rebuild sweep."""
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    trains = [
        MagicMock(
            code="E001",
            destination="Bray",
            direction="Northbound",
        )
    ]
    return {
        "get_all": AsyncMock(return_value=stations),
        "by_code": AsyncMock(return_value=trains),
        "stops": AsyncMock(return_value=[]),
        "scoped": [_FakeMovement("Howth")],
    }


async def test_press_runs_rebuild_and_reports_attributes(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """Pressing the button samples the network and publishes a result."""
    await _setup_entry(hass)
    entity_id = _rebuild_entity_id(hass)
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "unknown"

    patches = _successful_rebuild_patches()

    def apply_scoped(movements, destination, station_code, station_name):
        return list(patches["scoped"])

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            tmp_path / "stops_matrix.json",
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            new=patches["get_all"],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            new=patches["by_code"],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_train_stops",
            new=patches["stops"],
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild._scoped_journey_stops",
            side_effect=apply_scoped,
        ),
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id},
            blocking=True,
        )
        await hass.async_block_till_done()

    # The matching automation service exists alongside the button.
    assert hass.services.has_service(DOMAIN, SERVICE_REBUILD)

    state = hass.states.get(entity_id)
    # Modern HA buttons report the last-press timestamp once pressed.
    assert dt_util.parse_datetime(state.state) is not None
    attributes = state.attributes
    assert attributes["stations_sampled"] == 1
    assert attributes["total_stations"] == 1
    assert "error" not in attributes
    assert attributes["stops_added"] >= 1

    registry = er.async_get(hass)
    assert registry.entities[entity_id].unique_id == GLOBAL_REBUILD_UNIQUE_ID
    assert hass.data[DOMAIN]["global_last_result"].total_stations == 1


async def test_press_serializes_concurrent_invocations(
    hass: HomeAssistant,
) -> None:
    """A second press while one runs raises instead of double-sampling."""
    button = IrishRailRebuildStopsMatrixButton(hass, MagicMock())
    running = {"flag": False}
    release = asyncio.Event()
    entered = asyncio.Event()

    async def fake_rebuild(_hass: object, _client: object) -> None:
        if running["flag"]:
            raise RuntimeError("rebuild already running")
        running["flag"] = True
        entered.set()
        try:
            await release.wait()
        finally:
            running["flag"] = False

    with patch(
        "custom_components.irish_rail.button.async_run_matrix_rebuild",
        side_effect=fake_rebuild,
    ):
        first = hass.async_create_task(button.async_press())
        # Wait until the first press is provably inside the guarded
        # rebuild, then fire the second.
        await entered.wait()
        second = hass.async_create_task(button.async_press())

        # The duplicate press must surface its guard error straight away;
        # it never touches ``release``, so awaiting it cannot deadlock.
        with pytest.raises(RuntimeError, match="already running"):
            await second

        release.set()
        assert await first is None
        await hass.async_block_till_done()


# ── Failure handling and service wiring ─────────────────────────────────────


async def test_press_failure_records_error_attributes(hass: HomeAssistant) -> None:
    """A crashing rebuild surfaces an error payload, not a lost press."""
    button = IrishRailRebuildStopsMatrixButton(hass, MagicMock())
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["global_rebuild_entity"] = button

    with patch(
        "custom_components.irish_rail.button.async_run_matrix_rebuild",
        new=AsyncMock(side_effect=ValueError("network exploded")),
    ), pytest.raises(ValueError, match="network exploded"):
        await button.async_press()

    assert button.running is False
    attributes: dict[str, Any] = button.extra_state_attributes or {}
    assert attributes["error"] == "ValueError: network exploded"
    assert domain_data["global_last_result"].error == "ValueError: network exploded"


def test_runtime_client_supports_duck_typed_runtime_data() -> None:
    """Non-typed runtime containers still expose their client attr."""
    duck_entry = cast(
        ConfigEntry,
        SimpleNamespace(runtime_data=SimpleNamespace(client="duck-client")),
    )
    assert _runtime_client(duck_entry) == "duck-client"

    bare = cast(ConfigEntry, object())
    assert _runtime_client(bare) is None


async def test_service_call_drives_the_loaded_button(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """``irish_rail.rebuild_stops_matrix`` presses the live button."""
    await _setup_entry(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REBUILD)

    patches = _successful_rebuild_patches()

    def apply_scoped(movements, destination, station_code, station_name):
        return list(patches["scoped"])

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            tmp_path / "stops_matrix.json",
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_all_stations",
            new=patches["get_all"],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
            new=patches["by_code"],
        ),
        patch(
            "custom_components.irish_rail.api.IrishRailClient.async_get_train_stops",
            new=patches["stops"],
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild._scoped_journey_stops",
            side_effect=apply_scoped,
        ),
    ):
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD, {}, blocking=True)
        await hass.async_block_till_done()

    result = hass.data[DOMAIN]["global_last_result"]
    assert result.total_stations == 1
    assert result.stops_added >= 1
    assert result.error is None


async def test_service_call_without_button_warns_and_returns(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A service call while no button is loaded degrades to a warning."""
    await _setup_entry(hass)
    hass.data[DOMAIN]["global_rebuild_entity"] = None

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(DOMAIN, SERVICE_REBUILD, {}, blocking=True)
        await hass.async_block_till_done()

    assert "No Irish Rail rebuild button" in caplog.text
