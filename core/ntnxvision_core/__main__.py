"""CLI + sidecar entrypoint.

Two roles:

* `ntnxvision_core <command>` — a Typer CLI for headless use / testing.
* `ntnxvision_core sidecar`   — a stdio JSON-RPC server the Electron app drives.

The two share the same engine, so anything the UI can do is scriptable from the CLI.
"""

from __future__ import annotations

import getpass

import typer
from rich.console import Console
from rich.table import Table

from ntnxvision_core import __version__
from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.element_client import ElementCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.ndb_client import NdbCredentials
from ntnxvision_core.engine import collect_element_environment, collect_environment
from ntnxvision_core.exporters import export_environment
from ntnxvision_core.paths import exports_dir, snapshots_dir
from ntnxvision_core.store import SnapshotStore

app = typer.Typer(add_completion=False, help="NTNXVision — Nutanix inventory core.")
console = Console()


@app.command()
def version() -> None:
    """Print the core version."""
    console.print(f"NTNXVision core [bold]{__version__}[/bold]")


@app.command()
def inventory(
    host: str = typer.Option(..., help="Prism Central FQDN or IP"),
    username: str = typer.Option(..., help="PC username"),
    password: str = typer.Option(None, help="PC password (prompted if omitted)"),
    port: int = typer.Option(9440, help="PC port"),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS verification (lab only)"),
    name: str = typer.Option("environment", help="Snapshot name"),
    formats: str = typer.Option("excel", help="Comma-separated: excel,pdf,word"),
    ndb_host: str = typer.Option(None, help="Optional NDB/Era host to also inventory"),
    ndb_username: str = typer.Option(None, help="NDB username"),
    ndb_password: str = typer.Option(None, help="NDB password (prompted if host given)"),
) -> None:
    """Connect, collect the full platform inventory, snapshot it, and export."""
    pwd = password or getpass.getpass("Prism Central password: ")
    creds = PrismCredentials(host=host, username=username, password=pwd, port=port,
                             verify_ssl=not insecure)

    ndb_creds = None
    if ndb_host and ndb_username:
        ndb_pwd = ndb_password or getpass.getpass("NDB password: ")
        ndb_creds = NdbCredentials(host=ndb_host, username=ndb_username,
                                   password=ndb_pwd, verify_ssl=not insecure)

    def progress(idx: int, total: int, label: str, status: str) -> None:
        console.print(f"  [{idx + 1}/{total}] {label}: [dim]{status}[/dim]")

    try:
        console.print(f"[bold]Connecting[/bold] to {host} …")
        env = collect_environment(creds, progress=progress, ndb_creds=ndb_creds)
    except NutanixApiError as exc:
        console.print(f"[red]Collection failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    _finish(env, name, formats)


@app.command()
def cluster(
    host: str = typer.Option(..., help="Cluster (Prism Element) FQDN or IP"),
    username: str = typer.Option(..., help="Cluster username"),
    password: str = typer.Option(None, help="Cluster password (prompted if omitted)"),
    port: int = typer.Option(9440, help="Prism Element port"),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS verification (lab only)"),
    name: str = typer.Option("cluster", help="Snapshot name"),
    formats: str = typer.Option("excel", help="Comma-separated: excel,pdf,word"),
) -> None:
    """Connect directly to a cluster's Prism Element (v2.0 API, AOS 6.5–7.5) and inventory it."""
    pwd = password or getpass.getpass("Cluster password: ")
    creds = ElementCredentials(host=host, username=username, password=pwd, port=port,
                               verify_ssl=not insecure)

    def progress(idx: int, total: int, label: str, status: str) -> None:
        console.print(f"  [{idx + 1}/{total}] {label}: [dim]{status}[/dim]")

    try:
        console.print(f"[bold]Connecting[/bold] directly to cluster {host} …")
        env = collect_element_environment(creds, progress=progress)
    except NutanixApiError as exc:
        console.print(f"[red]Collection failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    _finish(env, name, formats)


def _finish(env: object, name: str, formats: str) -> None:
    """Shared tail: print warnings + summary, snapshot, and export."""
    for warning in env.warnings:  # type: ignore[attr-defined]
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    _print_summary(env)

    snap_path = SnapshotStore(snapshots_dir()).save(env, name)  # type: ignore[arg-type]
    console.print(f"Snapshot saved → [green]{snap_path}[/green]")

    for fmt in _parse_formats(formats):
        out = export_environment(env, fmt, exports_dir(), name)  # type: ignore[arg-type]
        console.print(f"{fmt.title()} exported → [green]{out}[/green]")


@app.command()
def export(
    snapshot: str = typer.Argument(..., help="Path to a .ntnxsnap.json snapshot"),
    formats: str = typer.Option("excel", help="Comma-separated: excel,pdf,word"),
    name: str = typer.Option(None, help="Output base name (defaults to snapshot name)"),
) -> None:
    """Re-export a saved snapshot to one or more formats (offline)."""
    env = SnapshotStore(snapshots_dir()).load(snapshot)
    base = name or "environment"
    for fmt in _parse_formats(formats):
        out = export_environment(env, fmt, exports_dir(), base)
        console.print(f"{fmt.title()} exported → [green]{out}[/green]")


def _parse_formats(formats: str) -> list[str]:
    return [f.strip() for f in formats.split(",") if f.strip()]


@app.command()
def sidecar() -> None:
    """Run the stdio JSON-RPC sidecar (used by the Electron app)."""
    from ntnxvision_core.sidecar import serve_stdio

    serve_stdio()


def _print_summary(env: object) -> None:  # Environment
    table = Table(title="Inventory summary")
    table.add_column("Object")
    table.add_column("Count", justify="right")
    for key, value in env.summary().items():  # type: ignore[attr-defined]
        table.add_row(key.replace("_", " ").title(), str(value))
    console.print(table)


if __name__ == "__main__":
    app()
