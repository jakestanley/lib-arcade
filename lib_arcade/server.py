"""HTTP server + heartbeat loop shared by every arcade adapter.

Exposes the shared arcade adapter contract (GET /arcade/info, POST
/arcade/actions/<action>), and periodically registers itself with the
arcade portal so it shows up as a managed server with start/stop
actions. See docs/ARCADE_CONTRACT.md (in homelab-standards /
homelab-arcade) for the protocol this implements.
"""

from __future__ import annotations

import inspect
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Union

from .config import AdapterConfig
from .docker_control import (
    check_for_update,
    current_status,
    do_start,
    do_stop,
    do_update,
    sync_port_forward,
)

# Contract for an action handler: either a zero-arg callable (closes over
# whatever it needs itself, e.g. do_start/do_stop/restart_round) or a
# one-arg callable taking the parsed JSON request body as a dict (for an
# action that needs input, e.g. apply_preset({"preset": "casual"})).
# Either shape returns (ok, status_or_error).
ActionHandler = Union[Callable[[], "tuple[bool, str]"], Callable[[dict], "tuple[bool, str]"]]

# A "rich" extra_actions entry, for an action the portal should render an
# input form for: {"handler": ..., "label": "Apply preset", "params": [...]}.
# `params` follows the arcade contract's fixed param-type vocabulary
# (enum/boolean/number/string) -- this library doesn't interpret it, only
# passes it through into the advertised action shape.
ActionSpec = dict[str, Any]

StatsFn = Callable[[], "list[dict[str, str]]"]


def _handler_accepts_body(handler: ActionHandler) -> bool:
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return False
    return len(sig.parameters) > 0


def _merge_actions(
    config: AdapterConfig,
    extra_actions: dict[str, ActionHandler | ActionSpec] | None,
) -> tuple[list[Union[str, dict]], dict[str, ActionHandler]]:
    """Build the full action_handlers dict (start/stop plus whatever a
    consumer adapter.py supplies) and the ordered actions list derived
    from it. Pulled out as a pure function so it's unit-testable without
    spinning up a real HTTP server.

    An extra_actions value is either a bare callable (advertised as a plain
    name, same as start/stop) or a dict shaped like
    {"handler": callable, "label": ..., "params": [...]} for an action that
    takes input -- advertised as the object form the arcade contract
    defines for parameterized actions.
    """
    action_handlers: dict[str, ActionHandler] = {
        "start": lambda: do_start(config),
        "stop": lambda: do_stop(config),
        # Baseline like start/stop, not opt-in -- every adapter's target
        # container is deployed from *some* image tag it doesn't fully
        # control the freshness of, so checking/applying an update is
        # always meaningful even where it's rarely used (e.g. a pinned
        # Minecraft version). Never applied automatically -- see
        # docker_control.do_update and the update_available heartbeat
        # field below, which only ever reports what's true, never acts on
        # it by itself.
        "update": lambda: do_update(config),
    }
    action_specs: dict[str, ActionSpec] = {}
    for name, entry in (extra_actions or {}).items():
        if isinstance(entry, dict):
            action_handlers[name] = entry["handler"]
            spec = {k: v for k, v in entry.items() if k != "handler"}
            if spec:
                action_specs[name] = spec
        else:
            action_handlers[name] = entry

    actions: list[Union[str, dict]] = [
        {"name": name, **action_specs[name]} if name in action_specs else name
        for name in action_handlers
    ]
    return actions, action_handlers


def detect_primary_ip() -> str:
    """Best-effort LAN IP detection, same trick homelab-arcade's portal uses."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def adapter_base_url(config: AdapterConfig) -> str:
    if config.adapter_base_url_override:
        return config.adapter_base_url_override
    return f"http://{detect_primary_ip()}:{config.adapter_port}"


def _build_handler(
    config: AdapterConfig,
    ssl_context: ssl.SSLContext | None,
    actions: list[Union[str, dict]],
    action_handlers: dict[str, ActionHandler],
    stats_fn: StatsFn | None,
    update_state: dict[str, bool],
):
    class AdapterHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/arcade/info":
                status = current_status(config)
                self._send_json(
                    200,
                    {
                        "id": config.server_id,
                        "name": config.server_name,
                        "description": config.server_description,
                        "actions": actions,
                        "stats": _safe_stats(stats_fn) if status == "running" else [],
                        "status": status,
                        "update_available": update_state["available"],
                    },
                )
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            parts = self.path.rstrip("/").split("/")
            # expect /arcade/actions/<action>
            if len(parts) == 4 and parts[1] == "arcade" and parts[2] == "actions":
                action = parts[3]
                handler = action_handlers.get(action)
                if handler is None:
                    self._send_json(404, {"ok": False, "error": f"unknown action: {action}"})
                    return
                content_length = int(self.headers.get("Content-Length", 0) or 0)
                raw_body = self.rfile.read(content_length) if content_length else b""
                try:
                    body = json.loads(raw_body) if raw_body else {}
                except json.JSONDecodeError:
                    self._send_json(400, {"ok": False, "error": "invalid JSON body"})
                    return
                ok, status_or_error = handler(body) if _handler_accepts_body(handler) else handler()
                if ok:
                    self._send_json(200, {"ok": True, "status": status_or_error})
                else:
                    self._send_json(500, {"ok": False, "error": status_or_error})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def log_message(self, format: str, *args) -> None:  # quieter default logging
            print(f"[adapter] {self.address_string()} - {format % args}")

    return AdapterHandler


def _safe_stats(stats_fn: StatsFn | None) -> list[dict[str, str]]:
    """Never let a stats-collection error (e.g. RCON unreachable while the
    game is mid-boot) take down the info endpoint or heartbeat loop --
    stats are best-effort, unlike status."""
    if stats_fn is None:
        return []
    try:
        return stats_fn() or []
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see above
        print(f"[adapter] stats_fn failed: {exc}")
        return []


def _heartbeat_loop(
    config: AdapterConfig,
    ssl_context: ssl.SSLContext | None,
    actions: list[Union[str, dict]],
    stats_fn: StatsFn | None,
    update_state: dict[str, bool],
) -> None:
    register_url = f"{config.arcade_base_url}/api/register"
    base_url = adapter_base_url(config)
    # 0.0 forces a check on the very first iteration (monotonic() is always
    # far larger), so /arcade/info has a real answer from the first
    # heartbeat rather than reporting stale "no" until the first interval
    # elapses.
    last_update_check = 0.0
    while True:
        status = current_status(config)
        # Renew the UPnP lease while running -- some routers expire mappings
        # after a TTL, so this self-heals within one heartbeat interval
        # rather than needing a manual stop/start.
        if status == "running":
            sync_port_forward(config, should_be_open=True)
        now = time.monotonic()
        if now - last_update_check >= config.update_check_seconds:
            # Deliberately decoupled from heartbeat_seconds -- see
            # AdapterConfig.update_check_seconds. Read-only (a docker pull),
            # safe regardless of status; never applies anything itself.
            update_state["available"] = check_for_update(config)
            last_update_check = now
        payload = {
            "id": config.server_id,
            "name": config.server_name,
            "description": config.server_description,
            "base_url": base_url,
            "actions": actions,
            "stats": _safe_stats(stats_fn) if status == "running" else [],
            "status": status,
            "update_available": update_state["available"],
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            register_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5, context=ssl_context):
                pass
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"[heartbeat] failed to register with {register_url}: {exc}")
        time.sleep(config.heartbeat_seconds)


def run_adapter(
    config: AdapterConfig,
    extra_actions: dict[str, ActionHandler | ActionSpec] | None = None,
    stats_fn: StatsFn | None = None,
) -> None:
    # Internal HTTPS uses the homelab's private CA (see homelab-standards'
    # internal-ca-trust.md) -- never disable verification instead.
    import os

    ssl_context = None
    if os.path.isfile(config.homelab_ca_file):
        ssl_context = ssl.create_default_context(cafile=config.homelab_ca_file)

    actions, action_handlers = _merge_actions(config, extra_actions)
    # Shared with the heartbeat loop, which is the only writer -- a plain
    # dict is enough (no lock needed) since CPython's GIL makes a single
    # key assignment atomic and this is the only kind of write it does.
    update_state: dict[str, bool] = {"available": False}

    # Reconcile immediately on boot -- an adapter restart while the game
    # server is already running (or already stopped) shouldn't have to
    # wait for a future start/stop action or the next heartbeat to get the
    # port-forward state right.
    sync_port_forward(config, should_be_open=current_status(config) == "running")

    threading.Thread(
        target=_heartbeat_loop,
        args=(config, ssl_context, actions, stats_fn, update_state),
        daemon=True,
    ).start()

    handler = _build_handler(config, ssl_context, actions, action_handlers, stats_fn, update_state)
    server = ThreadingHTTPServer(("0.0.0.0", config.adapter_port), handler)
    print(f"{config.server_name} arcade adapter listening on http://0.0.0.0:{config.adapter_port}")
    print(f"Controlling {config.compose_project}/{config.compose_service} via the Docker socket")
    print(f"Registering with {config.arcade_base_url} every {config.heartbeat_seconds}s as '{config.server_id}'")
    if config.upnp_enabled:
        protocols = "+".join(p.upper() for p in config.forward_protocols)
        print(f"UPnP port-forwarding enabled for {protocols} {config.forward_port}")
    else:
        print("UPnP port-forwarding disabled (ARCADE_UPNP_ENABLED=false)")
    server.serve_forever()
