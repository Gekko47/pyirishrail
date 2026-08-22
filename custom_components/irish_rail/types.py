"""Type definitions for the Irish Rail integration runtime data."""

from __future__ import annotations

from dataclasses import dataclass

from .api import IrishRailClient
from .coordinator import IrishRailDataUpdateCoordinator


@dataclass
class IrishRailRuntimeData:
    """Typed container holding the shared Irish Rail client and coordinator."""

    client: IrishRailClient
    coordinator: IrishRailDataUpdateCoordinator
