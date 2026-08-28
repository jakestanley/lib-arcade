"""Docker control for the adapter's sibling game-server container.

Identifies the target container by its docker-compose project/service
labels, not a hardcoded name, so it keeps working if the project or
container naming ever changes.
"""

from __future__ import annotations

import docker
from docker.errors import DockerException, NotFound

from . import upnp
from .config import AdapterConfig

docker_client = docker.from_env()


def find_target_container(config: AdapterConfig):
    containers = docker_client.containers.list(
        all=True,
        filters={
            "label": [
                f"com.docker.compose.project={config.compose_project}",
                f"com.docker.compose.service={config.compose_service}",
            ]
        },
    )
    return containers[0] if containers else None


def sync_port_forward(config: AdapterConfig, should_be_open: bool) -> None:
    if not config.upnp_enabled or not config.forward_port:
        return
    upnp.ensure_mapping(
        config.forward_port,
        config.forward_protocol,
        config.server_description,
        should_be_open,
    )


def current_status(config: AdapterConfig) -> str:
    """running | stopped | unknown"""
    try:
        container = find_target_container(config)
    except DockerException:
        return "unknown"
    if container is None:
        return "unknown"
    container.reload()
    return "running" if container.status == "running" else "stopped"


def do_start(config: AdapterConfig) -> tuple[bool, str]:
    try:
        container = find_target_container(config)
        if container is None:
            return False, f"no container found for {config.compose_project}/{config.compose_service}"
        container.start()
    except (DockerException, NotFound) as exc:
        return False, str(exc)
    # Port-forwarding is a convenience layer on top of the actual start --
    # never let a UPnP failure affect this action's own success.
    sync_port_forward(config, should_be_open=True)
    return True, current_status(config)


def do_stop(config: AdapterConfig) -> tuple[bool, str]:
    try:
        container = find_target_container(config)
        if container is None:
            return False, f"no container found for {config.compose_project}/{config.compose_service}"
        # Match docker-compose.yml's stop_grace_period -- the SDK's own
        # default (10s) is shorter and risks SIGKILL before the game
        # finishes saving on shutdown.
        container.stop(timeout=config.stop_timeout_seconds)
    except (DockerException, NotFound) as exc:
        return False, str(exc)
    sync_port_forward(config, should_be_open=False)
    return True, current_status(config)
