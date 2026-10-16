"""Smoke-test a frozen (PyInstaller) sidecar binary — stdlib only, any Python 3.8+.

    python scripts/smoke-sidecar.py core/dist/ntnxvision-sidecar/ntnxvision-sidecar[.exe]
    python scripts/smoke-sidecar.py <binary> --ping-only

1. `ping` over stdio JSON-RPC must return {"pong": true}.
2. Unless --ping-only: a tiny synthetic snapshot is exported to Excel, PDF and Word through
   the frozen binary. This is what catches missing PyInstaller data files (python-docx
   templates, fpdf2 font metrics, openpyxl resources) that a ping never touches.
3. Unless --ping-only: in demo mode (NTNXVISION_DEMO=1) a connection test against the
   bundled mock Prism Central must succeed — which needs the demo TLS files packaged.

HOME / APPDATA are pointed at a throwaway directory so the real user's snapshots and
exports are never read or written. Exits 0 on success, 1 on any failure.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SNAPSHOT = {
    "collected_at": "2026-01-01T00:00:00Z",
    "prism_central": {"host": "pc.smoke.local", "version": "pc.2024.3"},
    "clusters": [
        {
            "uuid": "c1", "name": "cluster-01", "aos_version": "7.0",
            "hypervisor_types": ["AHV"], "num_nodes": 1, "redundancy_factor": 2,
        }
    ],
    "hosts": [
        {
            "uuid": "h1", "name": "node-1", "cluster_uuid": "c1", "cluster_name": "cluster-01",
            "num_cpu_cores": 32, "memory_bytes": 256 * 1024**3, "num_vms": 1,
        }
    ],
    "vms": [{"uuid": "v1", "name": "app-01", "cluster_name": "cluster-01", "power_state": "ON"}],
}


def rpc(proc: subprocess.Popen, req_id: int, method: str, params: dict) -> dict:
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                                 "params": params}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"sidecar closed stdout before answering {method!r}")
        msg = json.loads(line)
        if msg.get("id") == req_id:  # skip progress notifications
            if "error" in msg:
                raise RuntimeError(f"{method} failed: {msg['error']}")
            return msg["result"]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    binary = Path(sys.argv[1]).resolve()
    ping_only = "--ping-only" in sys.argv[2:]
    if not binary.is_file():
        print(f"FAIL: sidecar binary not found: {binary}")
        return 1

    with tempfile.TemporaryDirectory(prefix="ntnxvision-smoke-") as tmp:
        home = Path(tmp)
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
                   APPDATA=str(home / "AppData"))
        data = (home / "AppData" if os.name == "nt"
                else home / "Library" / "Application Support") / "NTNXVision"
        snaps = data / "snapshots"
        snaps.mkdir(parents=True)
        (snaps / "smoke.ntnxsnap.json").write_text(json.dumps(SNAPSHOT), encoding="utf-8")
        out_dir = home / "exports"

        proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=sys.stderr, text=True, encoding="utf-8", env=env)
        try:
            pong = rpc(proc, 1, "ping", {})
            if pong.get("pong") is not True:
                print(f"FAIL: unexpected ping result {pong}")
                return 1
            print(f"ok   ping -> {pong}")
            if not ping_only:
                res = rpc(proc, 2, "export", {
                    "snapshot": "smoke.ntnxsnap.json",
                    "formats": ["excel", "pdf", "word"],
                    "export_dir": str(out_dir),
                })
                if res.get("errors"):
                    print(f"FAIL: export errors {res['errors']}")
                    return 1
                for fmt, p in res["paths"].items():
                    size = Path(p).stat().st_size
                    if size == 0:
                        print(f"FAIL: {fmt} export is empty: {p}")
                        return 1
                    print(f"ok   export {fmt:<5} -> {Path(p).name} ({size} bytes)")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: {exc}")
            return 1
        finally:
            if proc.stdin:
                proc.stdin.close()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()

        if ping_only:
            print("sidecar smoke test passed")
            return 0
        demo_env = dict(env, NTNXVISION_DEMO="1", NTNXVISION_DEMO_LATENCY_MS="0")
        proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=sys.stderr, text=True, encoding="utf-8", env=demo_env)
        try:
            res = rpc(proc, 3, "test_connection", {
                "mode": "prism_central", "host": "pc01.demo.local", "port": 9440,
                "username": "admin", "password": "demo", "insecure": False,
            })
            if res.get("status") != "ok":
                print(f"FAIL: demo test_connection -> {res}")
                return 1
            print(f"ok   demo test_connection -> {res.get('message')}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: {exc}")
            return 1
        finally:
            if proc.stdin:
                proc.stdin.close()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("sidecar smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
