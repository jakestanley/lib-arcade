"""Docker control for the adapter's sibling game-server container.

Identifies the target container by its docker-compose project/service
labels, not a hardcoded name, so it keeps working if the project or
container naming ever changes.
"""

from __future__ import annotations

import docker
from docker.errors import DockerException, NotFound
from docker.utils import parse_repository_tag

from . import upnp
from .config import AdapterConfig

# Lazy, not constructed at import time: docker.from_env() fails immediately
# if there's no Docker socket reachable, which broke `import lib_arcade`
# itself inside CI's sandboxed test container (no socket there, correctly)
# -- caught by lib-arcade's own CI, not assumed.
docker_client = None


def _ensure_client():
    global docker_client
    if docker_client is None:
        docker_client = docker.from_env()
    return docker_client


def find_target_container(config: AdapterConfig):
    client = docker_client if docker_client is not None else _ensure_client()
    containers = client.containers.list(
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
    for protocol in config.forward_protocols:
        upnp.ensure_mapping(
            config.forward_port,
            protocol,
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


def do_exec(config: AdapterConfig, command: str) -> tuple[bool, str]:
    """Run a command inside the sibling game-server container.

    Generic Docker plumbing, same category as do_start/do_stop -- what
    command to actually run (RCON client, backup script, etc.) is entirely
    up to the caller.
    """
    try:
        container = find_target_container(config)
        if container is None:
            return False, f"no container found for {config.compose_project}/{config.compose_service}"
        exit_code, output = container.exec_run(command)
    except (DockerException, NotFound) as exc:
        return False, str(exc)
    if exit_code != 0:
        return False, output.decode(errors="replace") if isinstance(output, bytes) else str(output)
    return True, "ok"


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


def _pull_current_image(config: AdapterConfig):
    """Pull whatever image the target container is actually configured to
    run, read from the container's own resolved config rather than a
    separate per-adapter setting -- docker-compose.yml (which this
    process never has a copy of; some consumer repos deploy from a fully
    ephemeral CI checkout with no persistent copy on the host) is the only
    place that should decide which image/tag a service runs, and the
    container it already created is the one artifact that always reflects
    that decision. Returns (container, pulled_image), or (None, None) if
    there's no container to check at all.
    """
    container = find_target_container(config)
    if container is None:
        return None, None
    container.reload()
    repository, tag = parse_repository_tag(container.attrs["Config"]["Image"])
    client = docker_client if docker_client is not None else _ensure_client()
    pulled = client.images.pull(repository, tag=tag or "latest")
    return container, pulled


def check_for_update(config: AdapterConfig) -> bool:
    """Best-effort: is a newer image available than what the target
    container is currently running? Purely read-only -- pulls the image
    (so the layer cache is warm for do_update later) but never touches
    the container itself, so it's safe to poll on a timer regardless of
    whether the container is running or deliberately stopped."""
    try:
        container, pulled = _pull_current_image(config)
        if container is None:
            return False
        return pulled.id != container.image.id
    except (DockerException, NotFound):
        return False


def do_update(config: AdapterConfig) -> tuple[bool, str]:
    """Recreate the target container from whatever image is currently
    pulled. Copies the container's own existing resolved config (env,
    labels, entrypoint/command, full host_config -- volumes, network
    mode, restart policy, shm_size, ulimits, etc.) straight from its own
    `attrs`, the same way tools like Watchtower do it, since this process
    has no docker-compose.yml of its own to recreate from.

    Preserves whatever run state the container was already in: a stopped
    container is recreated but left stopped, a running one is restarted
    onto the new image -- mirrors do_start/do_stop's own rule of never
    overriding a deliberate stop. Verified live against a disposable
    throwaway container (env/labels/restart-policy/shm_size all survived
    the recreate, and the stopped case correctly left the new container
    un-started) before wiring this in -- not yet verified against a real
    game server container.
    """
    try:
        container, pulled = _pull_current_image(config)
        if container is None:
            return False, f"no container found for {config.compose_project}/{config.compose_service}"
        if pulled.id == container.image.id:
            return True, "already up to date"

        was_running = container.status == "running"
        name = container.attrs["Name"].lstrip("/")
        cfg = container.attrs["Config"]
        host_config = container.attrs["HostConfig"]
        client = docker_client if docker_client is not None else _ensure_client()

        container.remove(force=True)
        new_container_id = client.api.create_container(
            image=pulled.id,
            name=name,
            environment=cfg.get("Env"),
            labels=cfg.get("Labels"),
            entrypoint=cfg.get("Entrypoint"),
            command=cfg.get("Cmd"),
            host_config=host_config,
        )["Id"]
        if was_running:
            client.api.start(new_container_id)
    except (DockerException, NotFound) as exc:
        return False, str(exc)
    sync_port_forward(config, should_be_open=was_running)
    return True, "updated" + (" and restarted" if was_running else " (left stopped)")
