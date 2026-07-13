from pathlib import Path

import typer
from typing import List, Optional

from ..utils import compose, spire

app = typer.Typer(help="Manage SPIRE workload identities.")

PROJECT_DIR  = Path(__file__).parent.parent.parent
SERVICES_FILE = PROJECT_DIR / ".services"
TRUST_DOMAIN  = "example.org"

def _services() -> List[str]:
    if SERVICES_FILE.exists():
        return [s for s in SERVICES_FILE.read_text().split() if s]
    return []


@app.command()
def register(
    services: Optional[List[str]] = typer.Argument(None, help="Services to register. Defaults to all tracked gateways."),
) -> None:
    """Register workload entries in SPIRE for all tracked gateways."""
    targets = services or _services()
    if not targets:
        typer.echo("[error] No gateways tracked. Run 'myclawprint setup all' or 'myclawprint gateway add <N>' first.", err=True)
        raise typer.Exit(1)

    parent_id = spire.agent_spiffe_id()
    if not parent_id:
        typer.echo("[error] Could not determine agent SPIFFE ID. Is the agent running?", err=True)
        raise typer.Exit(1)

    typer.echo(f"→ Registering workloads (parent: {parent_id})")

    # Validate labels first
    errors = 0
    for svc in targets:
        label = compose.container_label(svc, "app")
        if label and label != svc:
            typer.echo(f"  [FAIL] {svc}: label app={label} does not match service name")
            errors += 1
    if errors:
        typer.echo("Fix labels in docker-compose.yml before registering.", err=True)
        raise typer.Exit(1)

    for svc in targets:
        spiffe_id = f"spiffe://{TRUST_DOMAIN}/ns/apps/sa/{svc}"
        result = spire.create_entry(
            parent_id=parent_id,
            spiffe_id=spiffe_id,
            selector=f"docker:label:app:{svc}",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            typer.echo(f"  [ok] {svc} → {spiffe_id}")
        elif "already exists" in combined:
            typer.echo(f"  [skip] {svc}: entry already registered")
        else:
            typer.echo(f"  [warn] {svc}: {combined.strip()}")


@app.command(name="list")
def list_identities() -> None:
    """List all registered SPIRE workload entries."""
    typer.echo(spire.list_entries())


@app.command()
def fetch(
    service: str = typer.Argument(..., help="Docker Compose service name."),
) -> None:
    """Fetch and display the X.509 SVID for a running container."""
    result = compose.exec(
        service,
        "/bin/spire-agent-tool", "api", "fetch", "x509",
        "-socketPath", "/opt/spire/sockets/agent.sock",
        capture=True,
        check=False,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        if "no identity issued" in combined or "PermissionDenied" in combined:
            typer.echo(
                f"[error] No SVID issued for '{service}'. "
                "Has 'myclawprint identity register' been run and is the agent attested?",
                err=True,
            )
        else:
            typer.echo(f"[error] {combined.strip()}", err=True)
        raise typer.Exit(1)
    typer.echo(combined)


@app.command()
def agent_id() -> None:
    """Print the current SPIRE agent SPIFFE ID."""
    aid = spire.agent_spiffe_id()
    if aid:
        typer.echo(aid)
    else:
        typer.echo("[error] Could not determine agent SPIFFE ID.", err=True)
        raise typer.Exit(1)


@app.command()
def delete_all() -> None:
    """Delete all registered SPIRE workload entries."""
    ids = spire.entry_ids()
    if not ids:
        typer.echo("No entries to delete.")
        return
    typer.confirm(f"Delete {len(ids)} entries?", abort=True)
    for eid in ids:
        result = spire.delete_entry(eid)
        typer.echo(f"  [ok] Deleted {eid}" if result.returncode == 0 else f"  [warn] {eid}: {result.stdout}")
