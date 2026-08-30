"""Type definitions for the Irish Rail integration runtime data.

The :data:`IrishRailConfigEntry` alias is the official ``strict-typing`` way
to reference a config entry whose ``runtime_data`` is known to be an
:class:`IrishRailRuntimeData`. Using it throughout the integration means
``entry.runtime_data.client`` / ``entry.runtime_data.coordinator`` are
statically typed without a per-call ``cast`` or ``assert``, satisfying the
Platinum ``strict-typing`` rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .pyirishrail import IrishRailClient

if TYPE_CHECKING:
    # Imported only for the type alias below to avoid a circular import:
    # coordinator.py -> config_flow.py -> types.py -> coordinator.py.
    # The annotations are strings under ``from __future__ import annotations``
    # and ``TYPE_CHECKING`` is False at runtime, so the module is never
    # actually loaded through this path.
    from .coordinator import IrishRailDataUpdateCoordinator


@dataclass
class IrishRailRuntimeData:
    """Typed container holding the shared Irish Rail client and coordinator."""

    client: IrishRailClient
    coordinator: IrishRailDataUpdateCoordinator


# PEP 695 type alias. Mirrors the convention used by Home Assistant core
# integrations (e.g. ``type MyConfigEntry = ConfigEntry[MyRuntimeData]``)
# so mypy strict narrows ``entry.runtime_data`` to ``IrishRailRuntimeData``
# at every call site that uses this alias.
type IrishRailConfigEntry = ConfigEntry[IrishRailRuntimeData]
