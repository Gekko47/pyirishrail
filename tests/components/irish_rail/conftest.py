"""Fixtures for Irish Rail tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest_socket

from custom_components.irish_rail.const import (
    CONF_DIRECTION,
    CONF_STATION,
    CONF_STATION_CODE,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in Home Assistant."""
    yield


@pytest.fixture(autouse=True)
def _allow_aresponses_sockets(request):
    """Re-enable sockets for tests that use the aresponses fixture.

    pytest-homeassistant-custom-component blocks all AF_INET socket
    creation, but ``aresponses`` starts a real mock server on 127.0.0.1.
    Enable sockets just for those tests and restore blocking afterwards.
    """
    if "aresponses" in getattr(request, "fixturenames", []):
        pytest_socket.enable_socket()
        yield
        pytest_socket.disable_socket(allow_unix_socket=True)
    else:
        yield


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> ConfigEntry:
    """Create a mock config entry for the Irish Rail integration."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Dublin Pearse",
        data={
            CONF_STATION: "Dublin Pearse",
            CONF_STATION_CODE: "PEARS",
            CONF_DIRECTION: "Northbound",
        },
        unique_id="PEARS_Northbound",
    )


@pytest.fixture
def mock_api_client():
    """Mock the Irish Rail API client consumed by config_flow."""
    # Patch IrishRailClient where config_flow.py imports it (relative import),
    # preventing the real client from being instantiated during config-flow tests
    # while preserving the mock behavior.
    with patch(
        "custom_components.irish_rail.config_flow.IrishRailClient"
    ) as mock_client:
        yield mock_client
