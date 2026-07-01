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
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.exporters import ExcelExporter
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
    excel: bool = typer.Option(True, help="Export an Excel workbook"),
) -> None:
    """Connect, collect a full inventory, snapshot it, and export to Excel."""
    pwd = password or getpass.getpass("Prism Central password: ")
    creds = PrismCredentials(host=host, username=username, password=pwd, port=port,
                             verify_ssl=not insecure)

    def progress(idx: int, total: int, label: str, status: str) -> None:
        console.print(f"  [{idx + 1}/{total}] {label}: [dim]{status}[/dim]")

    try:
        console.print(f"[bold]Connecting[/bold] to {host} …")
        env = collect_environment(creds, progress=progress)
    except NutanixApiError as exc:
        console.print(f"[red]Collection failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    _print_summary(env)

    snap_path = SnapshotStore(snapshots_dir()).save(env, name)
    console.print(f"Snapshot saved → [green]{snap_path}[/green]")

    if excel:
        out = exports_dir() / f"{name}.xlsx"
        ExcelExporter().export(env, out)
        console.print(f"Excel exported → [green]{out}[/green]")


@app.command()
def export(
    snapshot: str = typer.Argument(..., help="Path to a .ntnxsnap.json snapshot"),
    out: str = typer.Option(None, help="Output .xlsx path"),
) -> None:
    """Re-export a saved snapshot to Excel (offline)."""
    env = SnapshotStore(snapshots_dir()).load(snapshot)
    target = out or str(exports_dir() / "environment.xlsx")
    ExcelExporter().export(env, target)
    console.print(f"Excel exported → [green]{target}[/green]")


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
