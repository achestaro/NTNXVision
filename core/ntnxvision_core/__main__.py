"""CLI + sidecar entrypoint.

Two roles:

* `ntnxvision_core <command>` — a Typer CLI for headless use / testing.
* `ntnxvision_core sidecar`   — a stdio JSON-RPC server the Electron app drives.

The two share the same engine, so anything the UI can do is scriptable from the CLI.
"""

from __future__ import annotations

import getpass
import os
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ntnxvision_core import __version__
from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.element_client import ElementCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.ndb_client import NdbCredentials
from ntnxvision_core.api.vcenter_client import VcenterCredentials
from ntnxvision_core.capacity import DEFAULT_VCPU_RATIO
from ntnxvision_core.engine import (
    collect_element_environment,
    collect_environment,
    combine_environments,
)
from ntnxvision_core.exporters import export_environment
from ntnxvision_core.models import Environment
from ntnxvision_core.paths import exports_dir, snapshots_dir
from ntnxvision_core.store import SnapshotStore

app = typer.Typer(add_completion=False, help="NTNXVision: Nutanix inventory core.")
console = Console()


@app.callback()
def _main(
    demo: bool = typer.Option(
        False, "--demo",
        help="Run against the built-in synthetic Nutanix estate (pc01.demo.local, "
             "pe01.demo.local, …) instead of the network. Same as NTNXVISION_DEMO=1.",
    ),
) -> None:
    if demo:
        os.environ["NTNXVISION_DEMO"] = "1"
    from ntnxvision_core.paths import demo_enabled

    if demo_enabled():
        from ntnxvision_core.synthetic import demo as demo_mode

        demo_mode.enable()


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
    vcenter_host: str = typer.Option(
        None, help="Optional vCenter of the ESXi clusters, for ESXi details Nutanix lacks"),
    vcenter_username: str = typer.Option(None, help="vCenter username"),
    vcenter_password: str = typer.Option(None, help="vCenter password (prompted if host given)"),
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

    vc_creds = None
    if vcenter_host and vcenter_username:
        vc_pwd = vcenter_password or getpass.getpass("vCenter password: ")
        vc_creds = VcenterCredentials(host=vcenter_host, username=vcenter_username,
                                      password=vc_pwd, verify_ssl=not insecure)

    def progress(idx: int, total: int, label: str, status: str) -> None:
        console.print(f"  [{idx + 1}/{total}] {label}: [dim]{status}[/dim]")

    try:
        console.print(f"[bold]Connecting[/bold] to {host} …")
        env = collect_environment(creds, progress=progress, ndb_creds=ndb_creds,
                                  vcenter_creds=vc_creds)
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


@app.command()
def inventory_multi(
    # Repeatable options use the Annotated form: a call in a *mutable*-annotated default
    # is a bugbear warning (and a real footgun outside Typer), scalars are exempt — hence
    # the mixed style with the single-target commands above.
    host: Annotated[list[str], typer.Option(
        "--host", help="Prism Central FQDN or IP; repeat per PC")],
    username: Annotated[list[str], typer.Option(
        "--username",
        help="PC username. Give it once to use for every --host, or once per --host")],
    password: Annotated[list[str] | None, typer.Option(
        "--password",
        help="PC password, paired like --username; prompted per host if omitted")] = None,
    port: int = typer.Option(9440, help="PC port (applies to every target)"),
    insecure: bool = typer.Option(False, "--insecure", help="Skip TLS verification (lab only)"),
    formats: str = typer.Option("excel", help="Comma-separated: excel,pdf,word"),
    combine: bool = typer.Option(
        True, "--combine/--no-combine",
        help="Also produce one merged snapshot/report across all targets",
    ),
    combined_name: str = typer.Option("combined-environment", help="Name for the merged run"),
    ndb_host: Annotated[list[str] | None, typer.Option(
        "--ndb-host",
        help="Optional NDB/Era host; pair per --host to also inventory NDB")] = None,
    ndb_username: Annotated[list[str] | None, typer.Option(
        "--ndb-username", help="NDB username")] = None,
    ndb_password: Annotated[list[str] | None, typer.Option(
        "--ndb-password", help="NDB password (prompted per NDB host if omitted)")] = None,
) -> None:
    """Inventory several Prism Centrals in one run, optionally combining them.

    Each target is isolated: one unreachable or misconfigured PC is reported and skipped
    without aborting the others: the same isolation the UI's multi-target mode gives.
    """
    usernames = _pair("--username", username, len(host))
    passwords = _pair("--password", password, len(host), allow_empty=True)
    ndb_hosts = _pair("--ndb-host", ndb_host, len(host), allow_empty=True)
    ndb_usernames = _pair("--ndb-username", ndb_username, len(host), allow_empty=True)
    ndb_passwords = _pair("--ndb-password", ndb_password, len(host), allow_empty=True)

    collected = []
    failures: list[tuple[str, str]] = []
    for i, target in enumerate(host):
        pwd = passwords[i] or getpass.getpass(f"Prism Central password for {target}: ")
        creds = PrismCredentials(host=target, username=usernames[i], password=pwd,
                                 port=port, verify_ssl=not insecure)
        ndb_creds = None
        if ndb_hosts[i] and ndb_usernames[i]:
            ndb_pwd = ndb_passwords[i] or getpass.getpass(f"NDB password for {ndb_hosts[i]}: ")
            ndb_creds = NdbCredentials(host=ndb_hosts[i], username=ndb_usernames[i],
                                       password=ndb_pwd, verify_ssl=not insecure)

        def progress(idx: int, total: int, label: str, status: str, _t: str = target) -> None:
            console.print(f"  [{_t}] [{idx + 1}/{total}] {label}: [dim]{status}[/dim]")

        console.print(f"[bold]Connecting[/bold] to {target} … "
                      f"({i + 1}/{len(host)})")
        try:
            env = collect_environment(creds, progress=progress, ndb_creds=ndb_creds)
        except Exception as exc:  # noqa: BLE001 — one target never aborts the rest
            console.print(f"[red]{target} failed:[/red] {exc}")
            failures.append((target, str(exc)))
            continue
        _finish(env, target, formats)
        collected.append(env)

    if combine and len(collected) >= 2:
        console.print(f"[bold]Combining[/bold] {len(collected)} environments …")
        _finish(combine_environments(collected), combined_name, formats)
    elif combine:
        console.print("[yellow]⚠ Combine skipped: it needs at least two successful "
                      "targets.[/yellow]")

    if failures:
        console.print(f"[red]{len(failures)} of {len(host)} target(s) failed.[/red]")
        raise typer.Exit(1)


def _pair(option: str, values: list[str] | None, count: int,
          allow_empty: bool = False) -> list[str]:
    """Line a repeatable option up with --host: one value for all, or one each.

    Anything else is a typo the user wants to hear about before a long collection run
    starts, not silently after it.
    """
    values = [v for v in (values or [])]
    if not values:
        if allow_empty:
            return [""] * count
        raise typer.BadParameter(f"{option} is required")
    if len(values) == 1:
        return values * count
    if len(values) != count:
        raise typer.BadParameter(
            f"{option} given {len(values)} time(s) for {count} --host value(s). "
            f"Pass it once for all hosts or once per host"
        )
    return values


def _finish(env: Environment, name: str, formats: str) -> None:
    """Shared tail: print warnings + summary, snapshot, and export."""
    for warning in env.warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    _print_summary(env)

    snap_path = SnapshotStore(snapshots_dir()).save(env, name)
    console.print(f"Snapshot saved → [green]{snap_path}[/green]")

    for fmt in _parse_formats(formats):
        out = export_environment(env, fmt, exports_dir(), name)
        console.print(f"{fmt.title()} exported → [green]{out}[/green]")


@app.command()
def export(
    snapshot: str = typer.Argument(..., help="Path to a .ntnxsnap.json snapshot"),
    formats: str = typer.Option("excel", help="Comma-separated: excel,pdf,word"),
    name: str = typer.Option(None, help="Output base name (defaults to snapshot name)"),
    vcpu_ratio: float = typer.Option(
        DEFAULT_VCPU_RATIO, "--vcpu-ratio",
        help="Target vCPU:pCPU ratio for the cluster capacity figures"),
) -> None:
    """Re-export a saved snapshot to one or more formats (offline)."""
    env = SnapshotStore(snapshots_dir()).load(snapshot)
    base = name or "environment"
    for fmt in _parse_formats(formats):
        out = export_environment(env, fmt, exports_dir(), base, {"vcpu_ratio": vcpu_ratio})
        console.print(f"{fmt.title()} exported → [green]{out}[/green]")


def _parse_formats(formats: str) -> list[str]:
    return [f.strip() for f in formats.split(",") if f.strip()]


@app.command("mock-server")
def mock_server(
    latency_ms: float = typer.Option(0.0, help="Artificial latency per request"),
) -> None:
    """Serve the synthetic estate on 127.0.0.1 (HTTPS) until Ctrl-C, for poking at the
    mock with curl. Send the target hostname in the X-NTNXVision-Demo-Host header."""
    import time

    from ntnxvision_core.synthetic.mockapi import MockApi
    from ntnxvision_core.synthetic.profiles import demo_targets, make_resolver
    from ntnxvision_core.synthetic.server import MockNutanixServer, demo_ca

    targets = demo_targets()
    server = MockNutanixServer(MockApi(make_resolver(targets), latency_ms=latency_ms))
    port = server.start()
    console.print(f"Mock Nutanix API on [bold]https://127.0.0.1:{port}[/bold] "
                  f"(CA: {demo_ca()})")
    for t in targets.values():
        console.print(f"  {t.host:<24} {t.kind}")
    console.print("Example: curl --cacert <CA> -u admin:demo -H "  # gitleaks:allow
                  "'X-NTNXVision-Demo-Host: pc01.demo.local' "
                  f"https://127.0.0.1:{port}/api/clustermgmt/v4.0/config/clusters")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


@app.command("smtp-sink")
def smtp_sink(
    port: int = typer.Option(0, help="Port on 127.0.0.1 (0 = any free port)"),
    out: str = typer.Option("", help="Write each received message to DIR/message-<n>.eml"),
    tls: str = typer.Option("none", help="none, starttls or ssl"),
    cert: str = typer.Option("ca", help="ca (issued by the demo CA) or self (self-signed)"),
    username: str = typer.Option("", help="Require AUTH with this username"),
    password: str = typer.Option("", help="...and this password"),
    reject: Annotated[list[str] | None, typer.Option(help="Refuse this recipient")] = None,
) -> None:
    """Run a local test SMTP server on 127.0.0.1 that keeps every message (never relays).
    For trying Settings > Email in demo mode and for the end-to-end tests. Prints
    "SMTP sink listening on 127.0.0.1:<port>" first."""
    import sys
    import time

    from ntnxvision_core.synthetic.smtpsink import SmtpSink

    sink = SmtpSink(port=port, tls=tls, cert=cert, username=username, password=password,
                    reject=list(reject or []), out_dir=out or None)
    bound = sink.start()
    sys.stdout.write(f"SMTP sink listening on 127.0.0.1:{bound}\n")
    sys.stdout.flush()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sink.stop()


@app.command()
def sidecar() -> None:
    """Run the stdio JSON-RPC sidecar (used by the Electron app)."""
    from ntnxvision_core.sidecar import serve_stdio

    serve_stdio()


def _print_summary(env: Environment) -> None:
    table = Table(title="Inventory summary")
    table.add_column("Object")
    table.add_column("Count", justify="right")
    for key, value in env.summary().items():
        table.add_row(key.replace("_", " ").title(), str(value))
    console.print(table)


if __name__ == "__main__":
    app()
