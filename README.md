# lib-arcade

Shared code for `arcade.stanley.arpa` game-server adapters — the HTTP
server, Docker control (by compose project/service labels), the
heartbeat/registration loop, and the UPnP port-forwarding helper.
Previously hand copy+sed'd between `arcade-palworld` and
`arcade-minecraft`; this is the single source of truth instead.

See `homelab-arcade`'s `docs/ARCADE_CONTRACT.md` for the wire protocol
this implements, and `homelab-standards/PATTERNS/cross-host-server-control.md`
for the general pattern.

## Usage

A consumer repo's own `arcade/adapter.py` is a thin wrapper:

```python
from lib_arcade import AdapterConfig, run_adapter

config = AdapterConfig.from_env(
    default_server_id="arcade-palworld",
    default_server_name="Palworld",
    default_server_description="Palworld dedicated server (arcade-palworld)",
    default_adapter_port=8300,
    default_compose_project="arcade-palworld",
    default_compose_service="palworld",
    default_stop_timeout_seconds=30,
    default_forward_protocol="udp",
)

if __name__ == "__main__":
    run_adapter(config)
```

Installed as a git dependency tracking `main` directly (no version
pinning/bump mechanism — consumers rebuild periodically to pick up
changes). Public repo, so this needs no credentials at all:

```
lib-arcade @ git+https://github.com/jakestanley/lib-arcade.git@main
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e . 
.venv/bin/python -m unittest discover -s tests
```

## CI

Every push and PR runs the test suite. `main` is branch-protected —
merges require that check to pass, so the latest commit on `main` is
always validated by construction.
