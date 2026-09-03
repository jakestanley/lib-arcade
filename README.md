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
    default_forward_protocols=("udp",),
)

if __name__ == "__main__":
    run_adapter(config)
```

`run_adapter` always provides `start`/`stop`. A consumer can add its own
named actions on top by passing `extra_actions` — a dict of zero-arg
handlers following the same `(ok, status_or_error)` contract `do_start`/
`do_stop` already return (a handler closes over whatever it needs itself,
e.g. an RCON client or `config`):

```python
def restart_round():
    ...
    return True, "restarting"

run_adapter(config, extra_actions={"restart_round": restart_round})
```

No portal or frontend changes are needed to pick up a new action — both
already render/proxy generically over whatever `actions` a server
registers with. `do_exec(config, command)` is also exported for the common
case of running a command inside the sibling game-server container (e.g.
invoking an image's own backup script) — generic Docker plumbing, same
category as `do_start`/`do_stop`; anything more specific (RCON, a game's
own HTTP API, etc.) belongs in the consumer repo, not here.

Installed as a git dependency tracking `main` directly (no version
pinning/bump mechanism — consumers rebuild periodically to pick up
changes). Public repo, so this needs no credentials at all:

```
lib-arcade @ git+https://github.com/jakestanley/lib-arcade.git@main
```

## Gotchas

These apply to every consumer (`arcade-palworld`, `arcade-minecraft`,
`arcade-cs2`, ...) since they're properties of this library, not of any one
game server. Consumer READMEs should link here rather than restate them.

- **Adapters are unauthenticated and have `docker.sock` mounted in** —
  root-equivalent host access, scoped to their own container, but trusting
  the homelab LAN/VPN with no auth of its own (same trust model as RCON).
  This is a single shared trust boundary across every consumer repo:
  whichever adapter is reachable and compromised gets host-level Docker
  control, not just its own game server's container. Don't expose
  `ARCADE_ADAPTER_PORT` outside the LAN. Standard pattern for control
  agents (Portainer, Watchtower use the same approach), but worth knowing
  before scoping this pattern to anything less trusted than a home LAN.
- **Tracks `main` directly, unpinned** (see Usage above) — there's no
  version-pinning or bump mechanism, so a consumer's next rebuild can
  silently pick up behavior changes with no record of which commit
  actually shipped. Consumers also have to work around Docker's build
  cache: since the `@main` pin in `requirements.txt` never changes, its
  content never invalidates the `pip install` layer, so a plain
  `docker compose up -d --build` won't pick up new commits on its own —
  each consumer needs its own `CACHEBUST` build arg to force that layer to
  actually re-run. Cheap for a single-maintainer homelab's iteration
  speed, but not reproducible: there's no way to know which `lib-arcade`
  commit a running adapter was built against without checking the image's
  build time.

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
