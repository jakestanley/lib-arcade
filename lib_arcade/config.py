"""AdapterConfig -- the values that differ between arcade adapters.

Everything else (HTTP server, Docker control, heartbeat loop, UPnP) is
shared and lives in this package. A consumer repo's own adapter.py just
builds one of these from its environment and calls run_adapter(config).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterConfig:
    server_id: str
    server_name: str
    server_description: str
    adapter_port: int
    arcade_base_url: str
    heartbeat_seconds: float
    adapter_base_url_override: str
    homelab_ca_file: str
    compose_project: str
    compose_service: str
    stop_timeout_seconds: int
    upnp_enabled: bool
    forward_port: int
    forward_protocol: str

    @classmethod
    def from_env(
        cls,
        *,
        default_server_id: str,
        default_server_name: str,
        default_server_description: str,
        default_adapter_port: int,
        default_compose_project: str,
        default_compose_service: str,
        default_stop_timeout_seconds: int,
        default_forward_protocol: str,
    ) -> "AdapterConfig":
        return cls(
            server_id=os.environ.get("ARCADE_SERVER_ID", default_server_id),
            server_name=os.environ.get("ARCADE_SERVER_NAME", default_server_name),
            server_description=os.environ.get(
                "ARCADE_SERVER_DESCRIPTION", default_server_description
            ),
            adapter_port=int(
                os.environ.get("ARCADE_ADAPTER_PORT", str(default_adapter_port))
            ),
            arcade_base_url=os.environ.get(
                "ARCADE_BASE_URL", "https://arcade.stanley.arpa"
            ).rstrip("/"),
            heartbeat_seconds=float(os.environ.get("ARCADE_HEARTBEAT_SECONDS", "30")),
            adapter_base_url_override=os.environ.get(
                "ARCADE_ADAPTER_BASE_URL", ""
            ).rstrip("/"),
            homelab_ca_file=os.environ.get(
                "HOMELAB_CA_FILE", "/etc/ssl/certs/homelab-ca.crt"
            ),
            compose_project=os.environ.get(
                "ARCADE_COMPOSE_PROJECT", default_compose_project
            ),
            compose_service=os.environ.get(
                "ARCADE_COMPOSE_SERVICE", default_compose_service
            ),
            stop_timeout_seconds=int(
                os.environ.get(
                    "ARCADE_STOP_TIMEOUT_SECONDS", str(default_stop_timeout_seconds)
                )
            ),
            upnp_enabled=os.environ.get("ARCADE_UPNP_ENABLED", "true").lower()
            == "true",
            forward_port=int(os.environ.get("SERVER_PORT", "0")),
            forward_protocol=os.environ.get(
                "ARCADE_FORWARD_PROTOCOL", default_forward_protocol
            ),
        )
