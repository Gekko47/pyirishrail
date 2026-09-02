"""Type definitions for the Irish Rail integration runtime data.

``IrishRailConfigEntry`` is the ``strict-typing`` alias used throughout
the integration; ``IrishRailRuntimeData`` is its typed runtime data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .client import IrishRailClient

if TYPE_CHECKING:
    # Avoid a circular import (coordinator -> config_flow -> types ->
    # coordinator). The annotations are strings under
    # ``from __future__ import annotations`` and ``TYPE_CHECKING`` is False
    # at runtime, so this branch is never actually executed.
    from .coordinator import IrishRailDataUpdateCoordinator


@dataclass
class IrishRailRuntimeData:
    """Typed container holding the shared Irish Rail client and coordinator."""

    client: IrishRailClient
    coordinator: IrishRailDataUpdateCoordinator


# PEP 695 alias. Mirrors ``type MyConfigEntry = ConfigEntry[MyRuntimeData]``
# so mypy strict narrows ``entry.runtime_data`` to ``IrishRailRuntimeData``
# at every call site.
type IrishRailConfigEntry = ConfigEntry[IrishRailRuntimeData]
