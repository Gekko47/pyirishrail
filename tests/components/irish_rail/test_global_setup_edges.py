"""Direct-setup edge coverage for the two shared global platforms.

The binary_sensor and button platforms normally run behind full config-entry
setup; these tests drive their ``async_setup_entry`` directly to pin the
client-resolution fallbacks that only fire in unusual orders (isolation
setups, monitor-less boots, exotic runtime containers).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail import (
    binary_sensor as ir_binary_sensor,
)
from custom_components.irish_rail import (
    button as ir_button,
)
from custom_components.irish_rail._runtime import (
    ensure_health_monitor_started,
    get_health_monitor,
)
from custom_components.irish_rail.const import DOMAIN
from custom_components.irish_rail.types import (
    IrishRailConfigEntry,
    IrishRailRuntimeData,
)


def _bare_entry(hass: HomeAssistant, unique_id: str) -> IrishRailConfigEntry:
    """Register a minimal entry with no runtime data attached."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Bare Entry",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    return cast(IrishRailConfigEntry, entry)


def _collector() -> tuple[list[Any], Any]:
    """Return an entity list plus an AddEntitiesCallback-compatible sink."""
    added: list[Any] = []

    def _add(entities: Any) -> None:
        added.extend(entities)

    return added, _add


# ── Binary sensor platform ──────────────────────────────────────────────────


async def test_binary_sensor_setup_bootstraps_monitor_from_runtime_client(
    hass: HomeAssistant,
) -> None:
    """A platform-only setup mints the shared monitor from runtime client."""
    entry = _bare_entry(hass, "PEARS_northbound")
    # The ``cast`` is necessary because the test deliberately bypasses
    # the ``IrishRailRuntimeData`` dataclass: the binary sensor's
    # ``async_setup_entry`` reads ``entry.runtime_data.client`` and
    # must cope with any duck-typed container, so the test exercises
    # that fallback.
    entry.runtime_data = cast(
        IrishRailRuntimeData,
        SimpleNamespace(client=MagicMock(name="runtime-client")),
    )
    added, add_entities = _collector()

    await ir_binary_sensor.async_setup_entry(hass, entry, add_entities)

    assert len(added) == 1
    monitor = get_health_monitor(hass)
    assert monitor is not None
    assert monitor.client is entry.runtime_data.client


async def test_binary_sensor_setup_without_client_warns_and_skips(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No runtime client and no monitor means no sensor, just a warning."""
    entry = _bare_entry(hass, "PEARS_southbound")
    added, add_entities = _collector()

    with caplog.at_level(logging.WARNING):
        await ir_binary_sensor.async_setup_entry(hass, entry, add_entities)

    assert added == []
    assert "global health sensor skipped" in caplog.text


# ── Button platform ─────────────────────────────────────────────────────────


async def test_button_setup_falls_back_to_monitor_client(
    hass: HomeAssistant,
) -> None:
    """A later claiming entry without its own client reuses the monitor's."""
    sentinel_client = MagicMock(name="monitor-client")
    ensure_health_monitor_started(hass, sentinel_client)

    entry = _bare_entry(hass, "KENT_all")
    # Duck-typed runtime container: see the comment in
    # ``test_binary_sensor_setup_bootstraps_monitor_from_runtime_client``.
    entry.runtime_data = cast(
        IrishRailRuntimeData,
        SimpleNamespace(client=None),
    )
    added, add_entities = _collector()

    await ir_button.async_setup_entry(hass, entry, add_entities)

    assert len(added) == 1
    button_entity = hass.data[DOMAIN]["global_rebuild_entity"]
    assert button_entity._client is sentinel_client


async def test_button_setup_without_any_client_warns_and_skips(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Neither a runtime client nor a monitor leaves nothing registered."""
    entry = _bare_entry(hass, "TARA_all")
    added, add_entities = _collector()

    with caplog.at_level(logging.WARNING):
        await ir_button.async_setup_entry(hass, entry, add_entities)

    assert added == []
    assert hass.data.get(DOMAIN, {}).get("global_rebuild_entity") is None
    assert "rebuild button skipped" in caplog.text
