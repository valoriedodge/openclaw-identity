#!/usr/bin/env python3
"""
Add a new openclaw gateway service to docker-compose.yml.

Usage:
  python add-gateway.py <N>

Creates:
  - ~/.openclaw_docker_<N>/workspace
  - A new service entry in docker-compose.yml for openclaw-gateway-<N>
"""

import sys
import os
import re
from pathlib import Path

COMPOSE_FILE   = Path(__file__).parent / "docker-compose.yml"
SERVICES_FILE  = Path(__file__).parent / ".services"
BASE_PORT      = 18789


def gateway_name(n: int) -> str:
    return f"openclaw-gateway-{n}"


def workspace_dir(n: int) -> Path:
    return Path.home() / f".openclaw_docker_{n}"


def service_block(n: int) -> str:
    name = gateway_name(n)
    port_var = f"OPENCLAW_GATEWAY_{n}_PORT"
    host_port = BASE_PORT + n * 100
    home = workspace_dir(n)
    return f"""
  {name}:
    image: ghcr.io/openclaw/openclaw:latest
    command: ["node", "openclaw.mjs", "gateway", "--bind", "lan", "--port", "18789"]
    restart: unless-stopped
    environment:
      - SPIFFE_ENDPOINT_SOCKET=unix:///opt/spire/sockets/agent.sock
      - OPENCLAW_DISABLE_BONJOUR=true
      - SIEM_HOST=fluentd-logger
      - SIEM_PORT=24224
    volumes:
      - {home}:/home/node/.openclaw
      - {home}/workspace:/home/node/.openclaw/workspace
      - spire-agent-sockets:/opt/spire/sockets:ro
      - ./spire-agent-tool:/bin/spire-agent-tool:ro
    ports:
      - "${{{port_var}:-{host_port}}}:18789"
    labels:
      - "app={name}"
    cap_drop:
      - NET_RAW
      - NET_ADMIN
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "node", "-e", "fetch('http://127.0.0.1:18789/healthz').then(r => process.exit(r.ok ? 0 : 1))"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s
    depends_on:
      - spire-agent
      - fluentd-logger
"""


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python add-gateway.py <N>", file=sys.stderr)
        sys.exit(1)

    n = int(sys.argv[1])
    name = gateway_name(n)
    content = COMPOSE_FILE.read_text()

    if f"  {name}:" in content:
        print(f"[info] Service '{name}' already exists in docker-compose.yml — skipping.")
        sys.exit(0)

    # Create workspace directory
    ws = workspace_dir(n) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    print(f"[ok] Created {ws}")

    # Insert new service before the `volumes:` section at the end
    block = service_block(n)
    updated = re.sub(r'(\nvolumes:)', block + r'\1', content)

    if updated == content:
        print("[error] Could not find insertion point in docker-compose.yml", file=sys.stderr)
        sys.exit(1)

    COMPOSE_FILE.write_text(updated)
    print(f"[ok] Added '{name}' to docker-compose.yml")
    print(f"[ok] Host port: {BASE_PORT + n * 100} (override with {f'OPENCLAW_GATEWAY_{n}_PORT'})")

    # Track the service in .services
    existing = SERVICES_FILE.read_text().split() if SERVICES_FILE.exists() else []
    if name not in existing:
        with SERVICES_FILE.open("a") as f:
            f.write(f"{name}\n")
        print(f"[ok] Registered '{name}' in .services")


if __name__ == "__main__":
    main()
