from pathlib import Path

import typer
from typing import Dict, List

from ..utils import policy as pol

app = typer.Typer(help="Manage OPA tool permissions.")

TRUST_DOMAIN = "example.org"

DEFAULT_PERMISSIONS: Dict[str, List[str]] = {
    "openclaw-gateway":   ["exec", "read_file"],
    "openclaw-gateway-2": ["exec", "read_file", "write_file"],
}


@app.command()
def grant(
    identity: str = typer.Argument(..., help="Full SPIFFE ID or short service name."),
    tool: str     = typer.Argument(..., help="Tool name to grant."),
    no_validate: bool = typer.Option(False, "--no-validate", help="Skip OPA validation."),
) -> None:
    """Grant a tool permission to an identity."""
    identity = _resolve_identity(identity)
    content  = pol.load()
    tools    = pol.get_tools(content, identity)

    if tools is None:
        typer.echo(f"  [info] Identity '{identity}' not found — creating.")
        content = pol.add_identity(content, identity, [tool])
    elif tool in tools:
        typer.echo(f"  [info] '{tool}' is already granted to '{identity}'. No change.")
        return
    else:
        tools.append(tool)
        content = pol.set_tools(content, identity, tools)

    if not no_validate and not pol.validate():
        typer.echo("[error] OPA validation failed — policy not saved.", err=True)
        raise typer.Exit(1)

    pol.save(content)
    typer.echo(f"  [ok] Granted '{tool}' to '{identity}'")


@app.command()
def revoke(
    identity: str = typer.Argument(..., help="Full SPIFFE ID or short service name."),
    tool: str     = typer.Argument(..., help="Tool name to revoke."),
    no_validate: bool = typer.Option(False, "--no-validate", help="Skip OPA validation."),
) -> None:
    """Revoke a tool permission from an identity."""
    identity = _resolve_identity(identity)
    content  = pol.load()
    tools    = pol.get_tools(content, identity)

    if tools is None:
        typer.echo(f"[error] Identity '{identity}' not found in policy.", err=True)
        raise typer.Exit(1)
    if tool not in tools:
        typer.echo(f"  [info] '{tool}' is not granted to '{identity}'. No change.")
        return

    tools.remove(tool)
    content = pol.set_tools(content, identity, tools)

    if not no_validate and not pol.validate():
        typer.echo("[error] OPA validation failed — policy not saved.", err=True)
        raise typer.Exit(1)

    pol.save(content)
    typer.echo(f"  [ok] Revoked '{tool}' from '{identity}'")


@app.command(name="list")
def list_policy() -> None:
    """List all identities and their permitted tools."""
    content    = pol.load()
    identities = pol.all_identities(content)

    if not identities:
        typer.echo("No identities found in policy.")
        return

    for identity, tools in sorted(identities.items()):
        typer.echo(f"\n{identity}")
        for t in sorted(tools):
            typer.echo(f"  - {t}")


@app.command()
def seed(
    no_validate: bool = typer.Option(False, "--no-validate"),
) -> None:
    """Write default permissions for the built-in gateways."""
    content = pol.load()
    for svc, tools in DEFAULT_PERMISSIONS.items():
        identity = f"spiffe://{TRUST_DOMAIN}/ns/apps/sa/{svc}"
        for tool in tools:
            existing = pol.get_tools(content, identity)
            if existing is None:
                content = pol.add_identity(content, identity, [tool])
            elif tool not in existing:
                existing.append(tool)
                content = pol.set_tools(content, identity, existing)
        typer.echo(f"  [ok] {identity}: {sorted(tools)}")

    if not no_validate and not pol.validate():
        typer.echo("[error] OPA validation failed — policy not saved.", err=True)
        raise typer.Exit(1)

    pol.save(content)
    typer.echo("\nDefault permissions written to policy/openclaw.rego")


def _resolve_identity(identity: str) -> str:
    """Allow short service names like 'openclaw-gateway' in addition to full SPIFFE IDs."""
    if identity.startswith("spiffe://"):
        return identity
    return f"spiffe://{TRUST_DOMAIN}/ns/apps/sa/{identity}"
