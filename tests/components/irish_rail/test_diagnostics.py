"""Tests for the Irish Rail diagnostics."""

from __future__ import annotations

import hashlib
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irish_rail.const import (
    DOMAIN,
    GLOBAL_LAST_REBUILD_KEY,
)
from custom_components.irish_rail.diagnostics import (
    _mask_identifier,
    async_get_config_entry_diagnostics,
)
from custom_components.irish_rail.matrix_rebuild import RebuildResult


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
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
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
    # Station identifiers are partially masked (so the maintainer can
    # still tell which station the user is configuring) rather than
    # fully redacted.
    assert result["entry"]["data"]["station"] == _mask_identifier(
        "Dublin Pearse"
    )
    assert result["entry"]["data"]["station_code"] == _mask_identifier("PEARS")
    # Non-sensitive configuration choices pass through unchanged.
    assert result["entry"]["data"]["direction"] == "Northbound"
    # Options are included and pass through the same masking policy.
    assert "options" in result["entry"]
    assert result["coordinator"]["last_update_success"] is True
    assert result["coordinator"]["due_trains_count"] == 0
    # The expanded coordinator diagnostic surface (issue #6) reports the
    # post-success values, not just its keys: a maintainer reading the
    # report should be able to tell the poll is healthy at a glance.
    assert result["coordinator"]["last_exception"] is None
    assert result["coordinator"]["failure_streak"] == 0
    assert result["coordinator"]["data_available"] is True


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
    # Sensitive entry data is partially masked, not fully redacted.
    assert result["entry"]["data"]["station"] == _mask_identifier("Cork Kent")
    assert result["entry"]["data"]["station_code"] == _mask_identifier("KENT")


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
        "custom_components.irish_rail.client.IrishRailClient.async_get_station_by_code",
        return_value=[],
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, entry)

    # Sensitive option keys are partially masked; non-sensitive ones
    # pass through unchanged so the maintainer can see the user's
    # actual scan interval.
    assert result["entry"]["options"]["station"] == _mask_identifier(
        "Dublin Pearse"
    )
    assert result["entry"]["options"]["station_code"] == _mask_identifier("PEARS")
    assert result["entry"]["options"]["scan_interval"] == 120
    assert result["coordinator"]["last_update_success"] is True


async def test_diagnostics_reports_last_rebuild(
    hass: HomeAssistant,
) -> None:
    """The most recent stops-matrix rebuild outcome is surfaced in the report."""
    entry = MockConfigEntry(
        domain="irish_rail",
        title="Dublin Pearse",
        data={"station": "Dublin Pearse", "station_code": "PEARS"},
        unique_id="PEARS_northbound",
    )
    entry.add_to_hass(hass)

    rebuild = RebuildResult(
        total_stations=5,
        sampled=4,
        skipped=1,
        stops_added=3,
        started="2026-01-01T00:00:00+00:00",
        finished="2026-01-01T00:01:00+00:00",
        duration_seconds=58.0,
    )
    hass.data.setdefault(DOMAIN, {})[GLOBAL_LAST_REBUILD_KEY] = rebuild

    result = await async_get_config_entry_diagnostics(hass, entry)

    # No monitor runs without a full entry setup, so API health stays None.
    assert result["api_health"] is None
    assert result["stops_matrix_rebuild"]["total_stations"] == 5
    assert result["stops_matrix_rebuild"]["stops_added"] == 3
    assert "error" not in result["stops_matrix_rebuild"]
