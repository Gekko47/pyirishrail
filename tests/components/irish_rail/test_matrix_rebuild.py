"""Runtime rebuild of the bundled "stops at" seed matrix.

The rebuild routes every observation through :class:`StopsMatrixStore` —
the same store the coordinator's live learning and the config flow's
lookup use. These tests verify that contract end-to-end: the rebuild
calls ``async_record`` for every (station, direction) bucket it
samples, the recorded data is immediately visible to ``async_lookup``,
and every outbound HTTP call crosses the gate as ``priority="background"``
so live polling can jump the queue.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.irish_rail.client import IrishRailClient
from custom_components.irish_rail.errors import IrishRailConnectionError
from custom_components.irish_rail.matrix_rebuild import (
    _REBUILD_PRIORITY,
    async_run_matrix_rebuild,
)
from custom_components.irish_rail.models import TrainMovement
from custom_components.irish_rail.store import (
    ALL_DIRECTIONS_KEY,
    get_stops_store,
    normalize_direction_key,
)


class _FakeMovement:
    """Attribute-compatible stand-in for a scoping result row."""

    def __init__(self, location: str) -> None:
        self.location = location


class _RecordingStore:
    """Drop-in stand-in for :class:`StopsMatrixStore` that records calls.

    Mirrors the real store's gap-fill union semantics: stops never
    disappear, new stops are unioned in, first-seen casing wins, and
    ``async_record`` returns ``True`` only when the bucket actually
    changed. Tests assert on the recorded call list to verify the
    rebuild hit the shared write path.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str | None, list[str]]] = []
        self._buckets: dict[tuple[str, str], list[str]] = {}

    async def async_record(
        self, station_code: str, direction: str | None, stops: list[str]
    ) -> bool:
        self.records.append((station_code, direction, list(stops)))
        key = (station_code, normalize_direction_key(direction))
        existing_map: dict[str, str] = {}
        existing = self._buckets.get(key, [])
        for stop in existing:
            if isinstance(stop, str) and stop:
                existing_map.setdefault(stop.casefold(), stop)
        for stop in stops:
            if isinstance(stop, str) and stop:
                existing_map.setdefault(stop.casefold(), stop)
        merged = sorted(existing_map.values(), key=str.casefold)
        if merged == existing:
            return False
        self._buckets[key] = merged
        return True

    async def async_lookup(
        self, station_code: str, direction: str | None
    ) -> list[str] | None:
        key = (station_code, normalize_direction_key(direction))
        result = self._buckets.get(key)
        return list(result) if result is not None else None


class _FlakyRecordingStore(_RecordingStore):
    """Recording store whose first ``async_record`` write fails.

    Used to exercise the rebuild's per-bucket persistence guard: the
    first bucket write raises, later writes flow through normally.
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def async_record(
        self, station_code: str, direction: str | None, stops: list[str]
    ) -> bool:
        self.calls += 1
        if self.calls == 1:
            raise OSError("storage went away")
        return await super().async_record(station_code, direction, stops)


def _client_mock(stations: list[MagicMock]) -> MagicMock:
    """Build a test client that pretends to be an :class:`IrishRailClient`.

    At runtime the returned object is a real
    :class:`IrishRailClient` instance with a stubbed
    ``aiohttp.ClientSession``, so the module-level
    ``patch("custom_components.irish_rail.client.IrishRailClient.scope_journey_stops", ...)``
    used by every test in this file actually reaches the client through
    normal class-level attribute lookup. A bare ``MagicMock()`` would
    shadow the patched method via its auto-generated child mock and the
    rebuild would silently observe an empty journey.

    The static return type is ``MagicMock`` rather than
    :class:`IrishRailClient` so test bodies can keep their existing
    direct attribute assignments (``client.async_get_station_by_code =
    AsyncMock(...)``) without mypy --strict's ``[method-assign]``
    complaint; the runtime contract is what matters for the rebuild's
    behavior, and the wider static type on the test bodies hides the
    fact that the runtime object is a real, structurally-typed client.
    ``cast`` to ``MagicMock`` keeps mypy happy for the return without
    affecting the object at runtime.
    """
    client = IrishRailClient(MagicMock())
    client.async_get_all_stations = AsyncMock(return_value=stations)  # type: ignore[method-assign]
    client.async_get_station_by_code = AsyncMock(return_value=[])  # type: ignore[method-assign]
    client.async_get_train_stops = AsyncMock(return_value=[])  # type: ignore[method-assign]
    return cast(MagicMock, client)


def _scoped_factory(
    stops: list[str],
) -> Callable[
    [list[TrainMovement], str | None, str | None, str | None],
    list[_FakeMovement],
]:
    """Build an :meth:`IrishRailClient.scope_journey_stops` stand-in returning the given stops."""

    def fake_scoped(
        movements: list[TrainMovement],
        destination: str | None,
        station_code: str | None,
        station_name: str | None,
    ) -> list[_FakeMovement]:
        return [_FakeMovement(stop) for stop in stops]

    return fake_scoped


async def test_rebuild_writes_every_station_through_stops_store(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Happy path: every sampled station reaches the shared store.

    Two stations, one healthy and one failing, exercise the full loop:
    the healthy station's observed direction bucket and the ``_all``
    union are both routed through ``get_stops_store(hass).async_record``;
    the failing station is skipped, never touching the store.
    """
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

    recording = _RecordingStore()

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=recording,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Craughill"]),
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.total_stations == 2
    assert result.sampled == 1
    assert result.skipped == 1
    assert result.buckets_updated == 2  # northbound + _all union
    assert result.stops_added == 2
    assert result.error is None
    # The documented heavy-request warning accompanies every run.
    assert any("samples every" in record.getMessage() for record in caplog.records)
    assert any("timed out" in record.getMessage() for record in caplog.records)

    # The healthy station's two buckets are recorded; the skipped one
    # never appears.
    recorded_codes = {code for code, _, _ in recording.records}
    assert recorded_codes == {"PEARS"}
    directions_for_pears = {
        direction for code, direction, _ in recording.records if code == "PEARS"
    }
    assert directions_for_pears == {
        normalize_direction_key("Northbound"),
        ALL_DIRECTIONS_KEY,
    }


async def test_rebuild_persistence_failure_isolated_per_bucket(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing ``async_record`` degrades to a warning, not a failed sweep.

    The first bucket write raises; the guard logs and continues, so the
    remaining bucket of the same station still lands and the rebuild
    finishes successfully with the outcome it did record.
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )

    store = _FlakyRecordingStore()

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=store,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Bray"]),
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.error is None
    # Two buckets attempted (direction + ``_all`` union): the first
    # failed and was logged, the second landed and was counted.
    assert store.calls == 2
    assert result.buckets_updated == 1
    assert result.stops_added == 1
    assert any(
        "Could not persist sampled stops" in record.getMessage()
        for record in caplog.records
    )


async def test_rebuild_output_visible_to_subsequent_lookup(
    hass: HomeAssistant,
) -> None:
    """A rebuild's output must reach the same lookup the config flow uses.

    Patches ``get_stops_store`` with a recording store and then
    performs an ``async_lookup`` after the rebuild — the exact call
    :func:`custom_components.irish_rail.config_flow` makes to populate
    the ``stops_at`` dropdown. A return of ``None`` here would be the
    original dual-file bug: data exists but is unreachable.
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )

    recording = _RecordingStore()

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=recording,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Howth"]),
        ),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.buckets_updated == 2

    # The very next call the config flow would make sees the rebuild's
    # output — no separate file the lookup has to discover.
    northbound = await recording.async_lookup("PEARS", "Northbound")
    assert northbound == ["Howth"]
    all_dirs = await recording.async_lookup("PEARS", None)
    assert all_dirs == ["Howth"]


async def test_rebuild_uses_background_priority_on_every_call(
    hass: HomeAssistant,
) -> None:
    """The rebuild must never displace live polling on a shared gate.

    Pins the contract by inspecting the ``priority`` kwarg on every
    client method the rebuild calls. Each method threads its
    ``priority`` through to ``_request`` which crosses the gate;
    a rebuild call without ``priority="background"`` would jump
    the queue ahead of stations the user is actively watching.
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = MagicMock()
    client.async_get_all_stations = AsyncMock(return_value=stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )
    client.async_get_train_stops = AsyncMock(
        return_value=[MagicMock(location="Bray")]
    )

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=_RecordingStore(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Howth"]),
        ),
    ):
        await async_run_matrix_rebuild(hass, client)

    # Every HTTP-touching method the rebuild calls must have been
    # invoked with priority="background". The client threads this
    # through to its gate via _request, so this is the contract
    # the gate actually sees.
    all_stations_kwargs = client.async_get_all_stations.await_args
    assert all_stations_kwargs is not None
    assert all_stations_kwargs.kwargs.get("priority") == _REBUILD_PRIORITY

    by_code_kwargs = client.async_get_station_by_code.await_args
    assert by_code_kwargs is not None
    assert by_code_kwargs.kwargs.get("priority") == _REBUILD_PRIORITY

    train_stops_kwargs = client.async_get_train_stops.await_args
    assert train_stops_kwargs is not None
    assert train_stops_kwargs.kwargs.get("priority") == _REBUILD_PRIORITY
    assert _REBUILD_PRIORITY == "background"


async def test_rebuild_with_existing_observations_unions(
    hass: HomeAssistant,
) -> None:
    """A re-run against an already-populated bucket is a gap-fill, not a clobber.

    The recording store is pre-seeded with one stop. The rebuild then
    observes the same stop plus a brand-new one. The bucket must end
    up with both, and the rebuild must report zero stops added for the
    already-known one (the store returns ``False`` on a no-op).
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )

    recording = _RecordingStore()
    # Pre-seed: Cherrywood is already known for PEARS Northbound.
    assert await recording.async_record(
        "PEARS", "Northbound", ["Cherrywood"]
    ) is True

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=recording,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Cherrywood", "Greystones"]),
        ),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    # Cherrywood is a no-op; Greystones joins in both the direction
    # bucket and the _all union. The final buckets contain both
    # stops, and the rebuild reports the total stops in each changed
    # bucket as ``stops_added`` (the user-facing attribute on the
    # button's entity).
    north = await recording.async_lookup("PEARS", "Northbound")
    assert north == ["Cherrywood", "Greystones"]
    all_dirs = await recording.async_lookup("PEARS", None)
    assert all_dirs == ["Cherrywood", "Greystones"]
    # Both the direction bucket and the _all bucket changed.
    assert result.buckets_updated == 2
    # ``stops_added`` is the sum of stops in each changed bucket:
    # 2 (Cherrywood, Greystones) × 2 buckets = 4.
    assert result.stops_added == 4


async def test_rebuild_persists_through_real_storage(
    hass: HomeAssistant,
) -> None:
    """The rebuild writes through HA's storage layer, not in-memory state.

    Uses the real :class:`StopsMatrixStore` (no patch on
    ``get_stops_store``) and a real second lookup that re-asks the
    store after the rebuild. The data must survive: this is the
    regression test for the original "two files, never reconciled"
    bug — a rebuild that only wrote to in-process state would not
    satisfy the config flow's next ``async_lookup`` after a restart.
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)
    client.async_get_station_by_code = AsyncMock(
        return_value=[
            MagicMock(code="E001", destination="Bray", direction="Northbound")
        ]
    )

    real_store = get_stops_store(hass)

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=real_store,
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory(["Dún Laoghaire"]),
        ),
    ):
        result = await async_run_matrix_rebuild(hass, client)

    assert result.buckets_updated >= 1

    # The real store (same singleton the config flow calls) sees the
    # rebuild's output. This is the contract the original dual-file
    # bug violated.
    persisted = await real_store.async_lookup("PEARS", "Northbound")
    assert persisted is not None
    assert "Dún Laoghaire" in persisted


async def test_rebuild_invalidates_bundled_seed_cache(
    hass: HomeAssistant,
) -> None:
    """The rebuild must clear the bundled-seed cache at the end.

    The cache exists so the config flow's fallback to the bundled
    seed is fast on subsequent lookups; a rebuild that improved the
    per-install matrix needs that cache cleared so the next
    config-flow lookup reads the freshest possible seed instead of
    serving a stale pre-rebuild snapshot.
    """
    stations = [MagicMock(code="PEARS", name="Dublin Pearse")]
    client = _client_mock(stations)

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=_RecordingStore(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
            side_effect=_scoped_factory([]),
        ),
        patch(
            "custom_components.irish_rail.matrix_rebuild.reset_bundled_seed_cache",
            autospec=True,
        ) as mock_reset,
    ):
        await async_run_matrix_rebuild(hass, client)

    mock_reset.assert_called_once()


async def test_movement_lookup_failure_skips_train_without_failing(
    hass: HomeAssistant,
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

    def fake_scoped(
        movements: list[TrainMovement],
        destination: str | None,
        station_code: str | None,
        station_name: str | None,
    ) -> list[TrainMovement]:
        return []

    with (
        patch(
            "custom_components.irish_rail.matrix_rebuild.get_stops_store",
            return_value=_RecordingStore(),
        ),
        patch(
            "custom_components.irish_rail.client.IrishRailClient.scope_journey_stops",
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
