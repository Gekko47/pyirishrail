"""Runtime rebuild of the bundled "stops at" seed matrix."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.irish_rail.api import IrishRailConnectionError
from custom_components.irish_rail.matrix_rebuild import (
    RebuildResult,
    _dump_document,
    _load_base_document,
    _merge_station,
    async_run_matrix_rebuild,
)
from custom_components.irish_rail.store import (
    STOPS_STORE_VERSION,
    normalize_direction_key,
)


class _FakeMovement:
    """Attribute-compatible stand-in for a scoping result row."""

    def __init__(self, location: str) -> None:
        self.location = location


_FIXTURE_DOC = {
    "schema_version": STOPS_STORE_VERSION,
    "generated": "2026-08-01T06:00:00+00:00",
    "note": "seed",
    "stations": {
        "PEARS": {
            "updated": "2026-08-01T06:00:00+00:00",
            "directions": {
                normalize_direction_key("Northbound"): [
                    "Cherrywood",
                    "Dublin Pearse",
                ],
                "_bogus": "not-a-list",
            },
        },
    },
}


def _write(path: Path, document: object) -> Path:
    """Write ``document`` as JSON to ``path``."""
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# ── Pure gap-fill merge ─────────────────────────────────────────────────────


def test_merge_adds_new_stops_without_removing_existing() -> None:
    """Observed stops union into buckets; nothing is ever dropped."""
    now = "2026-08-24T12:00:00+00:00"
    document = json.loads(json.dumps(_FIXTURE_DOC))

    touched, added = _merge_station(
        document,
        "PEARS",
        {
            normalize_direction_key("Northbound"): {"Greystones", "Dublin Pearse"},
            normalize_direction_key("Southbound"): {"Greystones"},
        },
        now,
    )

    assert (touched, added) == (2, 2)
    north = document["stations"]["PEARS"]["directions"]["northbound"]
    assert north == ["Cherrywood", "Dublin Pearse", "Greystones"]
    assert document["stations"]["PEARS"]["directions"]["southbound"] == [
        "Greystones"
    ]
    assert document["stations"]["PEARS"]["updated"] == now


def test_merge_no_change_keeps_timestamps_and_counts_zero() -> None:
    """A fully-known snapshot refreshes nothing and reports zero deltas."""
    document = json.loads(json.dumps(_FIXTURE_DOC))
    original_updated = document["stations"]["PEARS"]["updated"]

    touched, added = _merge_station(
        document,
        "PEARS",
        {normalize_direction_key("Northbound"): {"cherrywood"}},  # dup casing
        "2099-01-01T00:00:00+00:00",
    )

    assert (touched, added) == (0, 0)
    assert document["stations"]["PEARS"]["updated"] == original_updated


def test_load_base_document_repairs_corrupt_files() -> None:
    """Malformed seeds degrade to skeletons carrying the runtime note."""
    now = "2026-08-24T12:00:00+00:00"

    empty = _load_base_document(None, now)
    assert empty["schema_version"] == STOPS_STORE_VERSION
    assert empty["stations"] == {}
    assert empty["generated"] == now

    from_broken_shape = _load_base_document('{"stations": []}', now)
    assert from_broken_shape["stations"] == {}

    from_valid = _load_base_document(
        json.dumps(_FIXTURE_DOC),
        now,
    )
    assert from_valid["schema_version"] == STOPS_STORE_VERSION
    assert "runtime" in from_valid["note"].lower()


@pytest.fixture(name="matrix_path")
def matrix_path_fixture(tmp_path: Path) -> Path:
    """Redirect the seed target into a temporary directory."""
    path = tmp_path / "stops_matrix.json"
    _write(path, _FIXTURE_DOC)
    return path



def _client_mock(stations: list[MagicMock]) -> MagicMock:
    """Build an API client mock over the given station records."""
    client = MagicMock()
    client.async_get_all_stations = AsyncMock(return_value=stations)
    client.async_get_station_by_code = AsyncMock(return_value=[])
    client.async_get_train_stops = AsyncMock(return_value=[])
    return client


def _read_persisted(path: Path) -> dict[str, object]:
    """Blocking JSON read kept out of async scope (ASYNC240 hygiene)."""
    return json.loads(path.read_text(encoding="utf-8"))


async def test_rebuild_samples_network_and_gap_fills(
    hass: HomeAssistant,
    matrix_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path: healthy stations merge, errors skip, file stays valid."""
    stations = [
        MagicMock(code="PEARS", name="Dublin Pearse"),
        MagicMock(code="KENT", name="Cork Kent"),
    ]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        side_effect=[
            [MagicMock(code="E001", destination="Bray", direction="Northbound")],
            IrishRailConnectionError("timed out"),
        ]
    )

    def fake_scoped(movements, destination, station_code, station_name):
        return [_FakeMovement("Craughill")]

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            matrix_path,
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild._scoped_journey_stops",
            side_effect=fake_scoped,
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.total_stations == 2
    assert result.sampled == 1
    assert result.skipped == 1
    assert result.buckets_updated == 2  # northbound + the fresh _all union
    assert result.stops_added == 2
    assert result.error is None
    # The documented heavy-request warning accompanies every run.
    assert any("samples every" in record.getMessage() for record in caplog.records)
    assert any("timed out" in record.getMessage() for record in caplog.records)

    persisted = _read_persisted(matrix_path)
    pears = persisted["stations"]["PEARS"]["directions"]
    assert pears[normalize_direction_key("Northbound")] == [
        "Cherrywood",
        "Craughill",
        "Dublin Pearse",
    ]
    assert pears["_all"] == ["Craughill"]
    assert persisted["note"].startswith("Snapshot maintained")
    # The skipped station gains no entry at all.
    assert "KENT" not in persisted["stations"]


async def test_rebuild_bootstraps_missing_seed(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """With no seed on disk the run writes a valid skeleton document."""
    missing = tmp_path / "absent.json"
    client = _client_mock([MagicMock(code="PEARS", name="Dublin Pearse")])

    with patch(
        "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
        missing,
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.sampled == 1
    persisted = json.loads(missing.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == STOPS_STORE_VERSION
    assert persisted["generated"]
    assert "runtime" in persisted["note"].lower()
    assert persisted["stations"]["PEARS"]["directions"] == {}


async def test_rebuild_paces_itself_between_stations(
    hass: HomeAssistant,
    matrix_path: Path,
) -> None:
    """One polite pause happens per sampled station."""
    stations = [
        MagicMock(code=f"S{index:03d}", name=f"Stop {index}")
        for index in range(3)
    ]
    client = _client_mock(stations)
    pauses: list[float] = []

    real_sleep = asyncio.sleep

    async def tracking_sleep(delay: float) -> None:
        pauses.append(delay)
        await real_sleep(0)

    from custom_components.irish_rail.const import REBUILD_DELAY_SECONDS

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            matrix_path,
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild.asyncio.sleep",
            side_effect=tracking_sleep,
        ),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.sampled == 3
    assert pauses and all(p == REBUILD_DELAY_SECONDS for p in pauses)


async def test_rebuild_paces_after_failed_station(
    hass: HomeAssistant,
    matrix_path: Path,
) -> None:
    """A failed station still gets its pause before the next poll.

    Regression: the delay used to live after the success-path merge, so an
    ``async_get_station_by_code`` failure ``continue`` skipped it entirely and
    the next station was polled back-to-back after a burst of errors.
    """
    stations = [
        MagicMock(code="KENT", name="Cork Kent"),
        MagicMock(code="PEARS", name="Dublin Pearse"),
    ]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        side_effect=[
            IrishRailConnectionError("Station polling down"),
            [],
        ]
    )
    pauses: list[float] = []

    real_sleep = asyncio.sleep

    async def tracking_sleep(delay: float) -> None:
        pauses.append(delay)
        await real_sleep(0)

    from custom_components.irish_rail.const import REBUILD_DELAY_SECONDS

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            matrix_path,
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild.asyncio.sleep",
            side_effect=tracking_sleep,
        ),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.sampled == 1
    assert result.skipped == 1
    # One pause after the failed station and another after the healthy one.
    assert pauses == [REBUILD_DELAY_SECONDS, REBUILD_DELAY_SECONDS]


# ── Durability and error branches ───────────────────────────────────────────


def test_rebuild_result_error_branch_in_attributes() -> None:
    """``as_dict`` surfaces an error payload only when one exists."""
    assert "error" not in RebuildResult().as_dict()
    failed = RebuildResult(error="ValueError: boom").as_dict()
    assert failed["error"] == "ValueError: boom"


def test_load_base_document_repairs_malformed_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unparseable JSON degrades to a fresh skeleton with a warning."""
    with caplog.at_level(logging.WARNING):
        skeleton = _load_base_document("{definitely not json", "now")
    assert skeleton["stations"] == {}
    assert skeleton["generated"] == "now"
    assert "malformed" in caplog.text


def test_merge_station_repairs_corrupt_container_shapes() -> None:
    """Non-dict station entries and direction layers are rebuilt in place."""
    document = {"stations": {"PEARS": "garbage"}}
    touched, added = _merge_station(
        document, "PEARS", {normalize_direction_key("Northbound"): {"Bray"}}, "now"
    )
    assert (touched, added) == (1, 1)
    assert document["stations"]["PEARS"]["directions"]["northbound"] == ["Bray"]

    document_two = {"stations": {"TARA": {"directions": "also garbage"}}}
    touched, added = _merge_station(
        document_two, "TARA", {normalize_direction_key(None): {"Howth"}}, "now"
    )
    assert (touched, added) == (1, 1)
    assert document_two["stations"]["TARA"]["directions"]["_all"] == ["Howth"]


def test_dump_document_survives_temp_cleanup_failure(
    tmp_path: Path,
) -> None:
    """A failed temp-file cleanup never loses the already-replaced seed.

    The atomic dump writes to a sibling .tmp file, swaps it in with
    ``os.replace``, and only then best-effort removes the temp path. If that
    final unlink fails (e.g. the file is locked on Windows) the seed must not
    be lost or left truncated, and the error must not propagate.
    """
    target = tmp_path / "stops_matrix.json"
    document = {
        "schema_version": STOPS_STORE_VERSION,
        "generated": "now",
        "stations": {"PEARS": {"directions": {}}},
    }

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            target,
        ),
        patch("pathlib.Path.unlink", side_effect=OSError("locked")),
    ):
        _dump_document(document)

    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == STOPS_STORE_VERSION
    assert persisted["stations"]["PEARS"]["directions"] == {}


async def test_movement_lookup_failure_skips_train_without_failing(
    hass: HomeAssistant,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A movement-history failure warns, caches empty, and merges nothing."""
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )
    client.async_get_train_stops = AsyncMock(
        side_effect=IrishRailConnectionError("movements down")
    )

    def fake_scoped(movements, destination, station_code, station_name):
        return []

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild._MATRIX_PATH",
            tmp_path / "stops_matrix.json",
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild._scoped_journey_stops",
            side_effect=fake_scoped,
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.sampled == 1
    assert result.buckets_updated == 0
    assert result.stops_added == 0
    assert result.error is None
    assert "Movement lookup failed" in caplog.text
