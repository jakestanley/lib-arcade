"""UPnP IGD port-forwarding helper.

Requires the adapter container to run with `network_mode: host` — UPnP
discovery (SSDP multicast) does not reliably work across Docker's default
bridge network. Confirmed empirically: `miniupnpc`'s discover() raises
"Exception: Success" (a mislabeled error from the underlying C library) on
the default bridge network, but succeeds immediately under host networking.

Every function here is soft-fail: it logs a warning and returns False
rather than raising. Port-forwarding is a convenience layer on top of the
actual start/stop action, never something that should block it.
"""

from __future__ import annotations

import logging

import miniupnpc

logger = logging.getLogger("arcade-upnp")


def _client(discover_delay_ms: int = 3000) -> miniupnpc.UPnP:
    u = miniupnpc.UPnP()
    u.discoverdelay = discover_delay_ms
    found = u.discover()
    if found == 0:
        raise RuntimeError("no UPnP IGD devices found")
    u.selectigd()
    return u


def open_port(port: int, protocol: str, description: str) -> bool:
    try:
        u = _client()
        u.addportmapping(port, protocol.upper(), u.lanaddr, port, description, "")
        logger.info(f"UPnP: opened {protocol.upper()} {port} -> {u.lanaddr}:{port}")
        return True
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        logger.warning(f"UPnP open_port({port}/{protocol}) failed: {exc}")
        return False


def close_port(port: int, protocol: str) -> bool:
    try:
        u = _client()
        u.deleteportmapping(port, protocol.upper())
        logger.info(f"UPnP: closed {protocol.upper()} {port}")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"UPnP close_port({port}/{protocol}) failed: {exc}")
        return False


def ensure_mapping(port: int, protocol: str, description: str, should_be_open: bool) -> bool:
    """Reconciliation helper — make the router's mapping match desired state."""
    if should_be_open:
        return open_port(port, protocol, description)
    return close_port(port, protocol)
