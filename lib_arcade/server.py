"""HTTP server + heartbeat loop shared by every arcade adapter.

Exposes the shared arcade adapter contract (GET /arcade/info, POST
/arcade/actions/<action>), and periodically registers itself with the
arcade portal so it shows up as a managed server with start/stop
actions. See docs/ARCADE_CONTRACT.md (in homelab-standards /
homelab-arcade) for the protocol this implements.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import AdapterConfig
from .docker_control import current_status, do_start, do_stop, sync_port_forward

ACTIONS = ["start", "stop"]


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


def _build_handler(config: AdapterConfig, ssl_context: ssl.SSLContext | None):
    action_handlers = {
        "start": lambda: do_start(config),
        "stop": lambda: do_stop(config),
    }

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
                self._send_json(
                    200,
                    {
                        "id": config.server_id,
                        "name": config.server_name,
                        "description": config.server_description,
                        "actions": ACTIONS,
                        "status": current_status(config),
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
                ok, status_or_error = handler()
                if ok:
                    self._send_json(200, {"ok": True, "status": status_or_error})
                else:
                    self._send_json(500, {"ok": False, "error": status_or_error})
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def log_message(self, format: str, *args) -> None:  # quieter default logging
            print(f"[adapter] {self.address_string()} - {format % args}")

    return AdapterHandler


def _heartbeat_loop(config: AdapterConfig, ssl_context: ssl.SSLContext | None) -> None:
    register_url = f"{config.arcade_base_url}/api/register"
    base_url = adapter_base_url(config)
    while True:
        status = current_status(config)
        # Renew the UPnP lease while running -- some routers expire mappings
        # after a TTL, so this self-heals within one heartbeat interval
        # rather than needing a manual stop/start.
        if status == "running":
            sync_port_forward(config, should_be_open=True)
        payload = {
            "id": config.server_id,
            "name": config.server_name,
            "description": config.server_description,
            "base_url": base_url,
            "actions": ACTIONS,
            "status": status,
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


def run_adapter(config: AdapterConfig) -> None:
    # Internal HTTPS uses the homelab's private CA (see homelab-standards'
    # internal-ca-trust.md) -- never disable verification instead.
    import os

    ssl_context = None
    if os.path.isfile(config.homelab_ca_file):
        ssl_context = ssl.create_default_context(cafile=config.homelab_ca_file)

    # Reconcile immediately on boot -- an adapter restart while the game
    # server is already running (or already stopped) shouldn't have to
    # wait for a future start/stop action or the next heartbeat to get the
    # port-forward state right.
    sync_port_forward(config, should_be_open=current_status(config) == "running")

    threading.Thread(target=_heartbeat_loop, args=(config, ssl_context), daemon=True).start()

    handler = _build_handler(config, ssl_context)
    server = ThreadingHTTPServer(("0.0.0.0", config.adapter_port), handler)
    print(f"{config.server_name} arcade adapter listening on http://0.0.0.0:{config.adapter_port}")
    print(f"Controlling {config.compose_project}/{config.compose_service} via the Docker socket")
    print(f"Registering with {config.arcade_base_url} every {config.heartbeat_seconds}s as '{config.server_id}'")
    if config.upnp_enabled:
        print(f"UPnP port-forwarding enabled for {config.forward_protocol.upper()} {config.forward_port}")
    else:
        print("UPnP port-forwarding disabled (ARCADE_UPNP_ENABLED=false)")
    server.serve_forever()
