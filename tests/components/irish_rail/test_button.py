"""Tests for the global stops-matrix rebuild button."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.button import (
    SERVICE_REBUILD,
    IrishRailRebuildStopsMatrixButton,
    _runtime_client,
)
from custom_components.irish_rail.const import (
    DOMAIN,
    GLOBAL_HEALTH_UNIQUE_ID,
    GLOBAL_REBUILD_UNIQUE_ID,
)
from custom_components.irish_rail.pyirishrail import IrishRailClient, TrainMovement
from custom_components.irish_rail.types import IrishRailConfigEntry


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
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_all_stations",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
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
    tmp_path: Path,
) -> None:
    """Pressing the button samples the network and publishes a result."""
    await _setup_entry(hass)
    entity_id = _rebuild_entity_id(hass)
    assert entity_id is not None
    initial_state = hass.states.get(entity_id)
    assert initial_state is not None
    assert initial_state.state == "unknown"

    patches = _successful_rebuild_patches()

    def apply_scoped(
        movements: list[TrainMovement],
        destination: str | None,
        station_code: str | None,
        station_name: str | None,
    ) -> list[TrainMovement]:
        return list(patches["scoped"])

    with (
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_all_stations",
            new=patches["get_all"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
            new=patches["by_code"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_train_stops",
            new=patches["stops"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.IrishRailClient.scope_journey_stops",
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
    assert state is not None
    # Modern HA buttons report the last-press timestamp once pressed.
    assert dt_util.parse_datetime(state.state) is not None
    attributes = state.attributes
    assert attributes["stations_sampled"] == 1
    assert attributes["total_stations"] == 1
    assert "error" not in attributes
    assert attributes["stops_added"] >= 1

    registry = er.async_get(hass)
    assert registry.entities[entity_id].unique_id == GLOBAL_REBUILD_UNIQUE_ID
    # The button is attached to the shared "Irish Rail Services" device
    # (alongside the API connectivity binary sensor) so the two
    # integration-level entities appear together on a single device
    # card rather than as orphan rows in the Entities tab.
    button_device_id = registry.entities[entity_id].device_id
    assert button_device_id is not None
    device_registry = dr.async_get(hass)
    button_device = device_registry.async_get(button_device_id)
    assert button_device is not None
    assert (DOMAIN, "irish_rail_global_services") in button_device.identifiers
    assert button_device.name == "Irish Rail Services"
    # The connectivity sensor must land on the same device; look it
    # up by iterating the registry. The ``async_get_entity_id`` lookup
    # is unreliable here because the unique_id is set on the entity
    # *during* async_added_to_hass (HA internal), not at registry
    # create time; the resulting entity_id is then built from the
    # device name + translation key, not the unique_id.
    connectivity_entity_id: str | None = None
    for candidate in registry.entities.values():
        if candidate.unique_id == GLOBAL_HEALTH_UNIQUE_ID:
            connectivity_entity_id = candidate.entity_id
            break
    assert connectivity_entity_id is not None
    connectivity_device_id = (
        registry.entities[connectivity_entity_id].device_id
    )
    assert connectivity_device_id == button_device_id
    assert hass.data[DOMAIN]["global_last_result"].total_stations == 1


def test_button_is_unavailable_while_running() -> None:
    """The button greys out in the UI while a rebuild is in flight.

    Without this, the only signal that a press is being processed is the
    ``status: "running"`` attribute, which requires opening the entity.
    Setting ``available`` to ``False`` while ``running`` is set makes the
    UI render the button as unpressable, which is the standard idiom for
    "I am busy, wait" feedback. The matching ``extra_state_attributes``
    branch must publish a ``status: "running"`` payload so the entity
    panel shows the same context the UI badge implies.
    """
    button = IrishRailRebuildStopsMatrixButton(MagicMock(), MagicMock())
    assert button.available is True
    # No prior result and no in-flight job: the "never run" placeholder.
    assert button.extra_state_attributes == {"status": "never run since startup"}
    button.running = True
    assert button.available is False
    # The running branch advertises the heavy request so users can read it
    # in the entity panel rather than just infer it from the greyed button.
    running_attrs = button.extra_state_attributes
    assert running_attrs is not None
    assert running_attrs["status"] == "running"
    assert "Sampling every station" in running_attrs["note"]
    button.running = False
    assert button.available is True


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
        # ``async_press`` returns ``None``; this await is purely so the
        # first rebuild actually finishes before the test moves on.
        await first
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
    # ``cast`` to the typed client pins the static type while the runtime
    # value is a plain string: this is exactly the duck-typed shape the
    # ``_runtime_client`` fallback guards against in real deployments.
    duck_entry = cast(
        IrishRailConfigEntry,
        SimpleNamespace(
            runtime_data=SimpleNamespace(
                client=cast(IrishRailClient, "duck-client"),
            )
        ),
    )
    # ``comparison-overlap`` suppression: the static type is
    # ``IrishRailClient | None`` but the runtime value is intentionally
    # a string to exercise the duck-typed fallback. Mypy cannot prove
    # the comparison is meaningful; the test asserts the runtime result.
    assert _runtime_client(duck_entry) == "duck-client"  # type: ignore[comparison-overlap]

    bare = cast(IrishRailConfigEntry, object())
    assert _runtime_client(bare) is None


async def test_service_call_drives_the_loaded_button(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """``irish_rail.rebuild_stops_matrix`` presses the live button."""
    await _setup_entry(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_REBUILD)

    patches = _successful_rebuild_patches()

    def apply_scoped(
        movements: list[TrainMovement],
        destination: str | None,
        station_code: str | None,
        station_name: str | None,
    ) -> list[TrainMovement]:
        return list(patches["scoped"])

    with (
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_all_stations",
            new=patches["get_all"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_station_by_code",
            new=patches["by_code"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.api.IrishRailClient.async_get_train_stops",
            new=patches["stops"],
        ),
        patch(
            "custom_components.irish_rail.pyirishrail.IrishRailClient.scope_journey_stops",
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
