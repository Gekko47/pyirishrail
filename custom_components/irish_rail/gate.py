"""Shared :class:`RequestGate` singleton per ``HomeAssistant`` instance.

The public ``api.irishrail.ie`` endpoints are shared infrastructure.
The integration polls several stations (one ``IrishRailClient`` per
config entry), runs the config flow's discovery lookups, fires the
stops-matrix rebuild button, and runs a periodic API-health probe —
all of which would each hold their own private gate if the client
constructor were left to its default, defeating the gate's purpose
(one process-wide rate budget against the unauthenticated API).

This module keeps a single :class:`pyirishrail.RequestGate` per
``HomeAssistant`` instance, stored on ``hass.data[DOMAIN]`` alongside
the existing health-monitor singleton, and every consumer the
integration creates passes it explicitly to its
``IrishRailClient(session, gate=gate)`` constructor.

The gate is created on first use and lives until the last loaded
entry unloads; ``async_release_request_gate`` drops it then so a
subsequent load gets a fresh gate. (In practice the state inside the
gate at unload time is always empty — the unload waits for the
coordinator and config-flow clients to drain before removing the
entry — but the explicit drop keeps the lifecycle symmetric with
``hass.data``-keyed singletons the integration already owns.)
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .pyirishrail import RequestGate

# Key under ``hass.data[DOMAIN]`` for the per-hass request-gate
# singleton; see module docstring for why the integration owns one
# shared gate.
REQUEST_GATE_INSTANCE = "request_gate"


def get_request_gate(hass: HomeAssistant) -> RequestGate | None:
    """Return the per-hass request gate singleton, if it exists.

    ``None`` is returned when no entry has set one up yet (e.g.
    before the first config entry is loaded, or after the last one
    has been unloaded).
    """
    gate = hass.data.setdefault(DOMAIN, {}).get(REQUEST_GATE_INSTANCE)
    if isinstance(gate, RequestGate):
        return gate
    return None


def async_get_request_gate(hass: HomeAssistant) -> RequestGate:
    """Return the per-hass request gate, creating it on first call.

    Idempotent: subsequent calls return the same instance, so every
    ``IrishRailClient`` the integration constructs is wired to the
    same gate and the public-API rate budget is shared.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    gate = domain_data.get(REQUEST_GATE_INSTANCE)
    if not isinstance(gate, RequestGate):
        gate = RequestGate()
        domain_data[REQUEST_GATE_INSTANCE] = gate
    return gate


def async_release_request_gate(hass: HomeAssistant) -> None:
    """Drop the per-hass request gate if present.

    Called when the last loaded entry unloads. A subsequent load gets
    a fresh gate, which is cheap (the gate's only state is an
    ``asyncio.Lock`` and two counters initialised on first acquire).
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.pop(REQUEST_GATE_INSTANCE, None)
