"""Tests for the Irish Rail diagnostics."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.diagnostics import (
    _mask_identifier,
    async_get_config_entry_diagnostics,
)


async def test_config_entry_diagnostics(hass: HomeAssistant) -> None:
    """Test diagnostics redact station data and report coordinator state."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "direction": "Northbound",
        },
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    # Entry identifiers are partially masked, not hidden entirely.
    assert result["entry"]["title"] == _mask_identifier("Dublin Pearse")
    assert result["entry"]["unique_id"] == _mask_identifier("PEARS_northbound")
    assert result["entry"]["title"] != "**REDACTED**"
    assert result["entry"]["unique_id"] != "**REDACTED**"
    # Station identifiers are redacted.
    assert result["entry"]["data"]["station"] == "**REDACTED**"
    assert result["entry"]["data"]["station_code"] == "**REDACTED**"
    # Options are included and pass through the same redaction policy.
    assert "options" in result["entry"]
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["due_trains_count"] == 0


# ── Redaction edge cases (roadmap Phase 3, Gold rule ``diagnostics``) ────────


def test_mask_identifier_handles_none_and_empty() -> None:
    """None passes through; an empty value masks to a bare stable hash."""
    assert _mask_identifier(None) is None
    assert _mask_identifier("") == f"...{hashlib.sha256(b'').hexdigest()[:8]}"


def test_mask_identifier_structure_is_stable_and_unicode_safe() -> None:
    """Masked output keeps a 3-char prefix plus an 8-hex deterministic suffix."""
    masked = _mask_identifier("Dublin Pearse")
    expected_digest = hashlib.sha256(b"Dublin Pearse").hexdigest()[:8]
    assert masked == f"Dub...{expected_digest}"
    # Deterministic across calls so reports stay correlatable.
    assert _mask_identifier("Dublin Pearse") == masked
    # Distinct identifiers never share a mask.
    assert _mask_identifier("Cork Kent") != masked
    # Multi-byte characters are sliced per code point, not per byte.
    masked_unicode = _mask_identifier("Dún Laoghaire")
    assert masked_unicode is not None
    assert masked_unicode.startswith("Dún...")


async def test_diagnostics_without_runtime_data(hass: HomeAssistant) -> None:
    """A never-set-up entry yields no coordinator info and does not crash."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Cork Kent",
        data={"station": "Cork Kent", "station_code": "KENT"},
        unique_id="KENT_all",
    )
    entry.add_to_hass(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["coordinator"] == {}
    # Entry data still passes through redaction.
    assert result["entry"]["data"]["station"] == "**REDACTED**"
    assert result["entry"]["data"]["station_code"] == "**REDACTED**"


async def test_diagnostics_redacts_sensitive_options(hass: HomeAssistant) -> None:
    """Options pass through the same redaction policy as entry data."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        options={
            "station": "Dublin Pearse",
            "station_code": "PEARS",
            "scan_interval": 120,
        },
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.irish_rail.api.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    # Sensitive option keys are redacted, non-sensitive ones kept useful.
    assert result["entry"]["options"]["station"] == "**REDACTED**"
    assert result["entry"]["options"]["station_code"] == "**REDACTED**"
    assert result["entry"]["options"]["scan_interval"] == 120
    assert result["coordinator"]["last_update_success"] is True
