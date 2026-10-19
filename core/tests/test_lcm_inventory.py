"""The optional LCM inventory: the one non-read request, and everything around it.

Covers routing (v4 only), the read-only guard and its narrow exception, the busy cases
(an inventory to join, an upgrade to report), 403, failure, timeout, cancel, run
metadata, the reports' provenance line, and that nothing happens unless asked.
"""

from __future__ import annotations

import ast
import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import requests

import ntnxvision_core
import ntnxvision_core.sidecar as sc
from ntnxvision_core.api import PrismCredentials, transport
from ntnxvision_core.api.errors import NutanixApiError, ReadOnlyViolation
from ntnxvision_core.cancel import CancelToken, RunCancelled
from ntnxvision_core.engine import collect_environment, combine_environments
from ntnxvision_core.lcm_inventory import (
    InventoryOptions,
    busy_sentence,
    note_line,
    provenance,
    task_ref,
)
from ntnxvision_core.models import LcmInventoryRun, LcmInventoryTarget
from ntnxvision_core.runinfo import RunTracker

PW = {"username": "admin", "password": "demo"}
FAST = InventoryOptions(poll_initial_s=0.02, poll_factor=1.5, poll_max_s=0.1)
INVENTORY = "/api/lifecycle/v4.0/operations/$actions/inventory"
GENESIS = "/PrismGateway/services/rest/v1/genesis"
GENESIS_URL = f"https://pc.example.lab:9440{GENESIS}"
CATALOGUE = "https://download.example.lab/lcm/2.0"


def _collect(host: str, **kw: Any) -> Any:
    return collect_environment(PrismCredentials(host=host, **PW), **kw)


def _lcm(demo: Any) -> Any:
    return demo.api.lcm


def _by_name(env: Any) -> dict[str, LcmInventoryTarget]:
    return {t.name: t for t in env.lcm_inventory.targets}


# ─── the read-only guard ───────────────────────────────────────────────


@pytest.mark.parametrize("method,url", [
    ("GET", "https://pc.example.lab:9440/api/clustermgmt/v4.0/config/clusters"),
    ("HEAD", "https://pc.example.lab:9440/"),
    ("POST", "https://pc.example.lab:9440/api/nutanix/v3/vms/list"),
    ("POST", "https://pc.example.lab:9440/api/nutanix/v3/groups"),
    ("POST", "https://vc.example.lab/api/session"),
    ("DELETE", "https://vc.example.lab/rest/com/vmware/cis/session"),
])
def test_guard_allows_reads(method: str, url: str) -> None:
    transport.check_request(method, url)


@pytest.mark.parametrize("method,url", [
    ("POST", "https://pc.example.lab:9440/api/vmm/v4.0/ahv/config/vms"),
    ("PUT", "https://pc.example.lab:9440/api/nutanix/v3/vms/abc"),
    ("DELETE", "https://pc.example.lab:9440/api/nutanix/v3/vms/abc"),
    ("PATCH", "https://pc.example.lab:9440/api/nutanix/v3/vms/list"),
    ("POST", "https://pc.example.lab:9440/api/lifecycle/v4.0/operations/$actions/upgrade"),
    ("POST", "https://pc.example.lab:9440/api/nutanix/v3/vms/list/extra"),
    # Without the permit, the inventory is refused like any write.
    ("POST", f"https://pc.example.lab:9440{INVENTORY}"),
    ("POST", "https://pc.example.lab:9440/api/lifecycle/v4.0/operations/%24actions/inventory"),
])
def test_guard_refuses_everything_else(method: str, url: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        transport.check_request(method, url)


def test_permit_allows_only_the_inventory_and_only_in_its_context() -> None:
    url = f"https://pc.example.lab:9440{INVENTORY}"
    with transport.lcm_inventory_permit():
        transport.check_request("POST", url)
        transport.check_request("POST", url.replace("v4.0", "v4.2"))
        with pytest.raises(ReadOnlyViolation):  # the permit is not a general write pass
            transport.check_request("POST", url.replace("inventory", "upgrade"))
        # Another thread (the collectors running alongside) does not inherit it.
        seen: list[BaseException] = []

        def other() -> None:
            try:
                transport.check_request("POST", url)
            except ReadOnlyViolation as exc:
                seen.append(exc)
        t = threading.Thread(target=other)
        t.start()
        t.join()
        assert len(seen) == 1
    with pytest.raises(ReadOnlyViolation):
        transport.check_request("POST", url)


def _rpc(**kw: Any) -> str:
    """A Genesis body; keyword overrides go into the value JSON (None drops a key)."""
    import json
    value: dict[str, Any] = {".oid": "LifeCycleManager", ".method": "lcm_framework_rpc",
                             ".kwargs": {"method_class": "LcmFramework",
                                         "method": "perform_inventory",
                                         "args": [CATALOGUE]}}
    for k, v in kw.items():
        if k.startswith("kw_"):
            if v is None:
                value[".kwargs"].pop(k[3:], None)
            else:
                value[".kwargs"][k[3:]] = v
        elif v is None:
            value.pop(k.replace("_", "."), None)
        else:
            value[k.replace("_", ".")] = v
    return json.dumps({"value": json.dumps(value)})


def _dup_value(first: str, second: str) -> str:
    """An outer body naming "value" twice."""
    return f'{{"value": {json.dumps(first)}, "value": {json.dumps(second)}}}'


def test_genesis_bodies_are_built_as_checked() -> None:
    body = transport.genesis_body("perform_inventory", [CATALOGUE])
    import json
    assert transport.genesis_rpc(json.dumps(body)) == "perform_inventory"
    assert transport.genesis_rpc(json.dumps(body).encode()) == "perform_inventory"
    assert json.loads(body["value"]) == {
        ".oid": "LifeCycleManager", ".method": "lcm_framework_rpc",
        ".kwargs": {"method_class": "LcmFramework", "method": "perform_inventory",
                    "args": [CATALOGUE]}}
    for read in ("get_config", "is_lcm_operation_in_progress"):
        assert transport.genesis_rpc(json.dumps(transport.genesis_body(read))) == read


CLUSTER = "0005f0a1-aaaa-bbbb-cccc-000000000001"


def _config_reply(url: str = CATALOGUE) -> bytes:
    import json
    return json.dumps({"value": json.dumps({".return": {"url": url}})}).encode()


def _in_fresh_context(fn: Any) -> Any:
    """Run `fn` in a copy of the current context, so recorded catalogues do not leak."""
    import contextvars
    return contextvars.copy_context().run(fn)


def test_legacy_inventory_passes_only_inside_the_permit() -> None:
    def body() -> None:
        proxied = f"{GENESIS_URL}?proxyClusterUuid={CLUSTER}"
        # Before LCM reported its catalogue, even the permit does not admit it.
        with transport.lcm_inventory_permit(), pytest.raises(ReadOnlyViolation):
            transport.check_request("POST", GENESIS_URL, _rpc())
        transport._remember_catalogue(GENESIS_URL, _config_reply())
        with transport.lcm_inventory_permit():
            transport.check_request("POST", GENESIS_URL, _rpc())
            transport.check_request("POST", GENESIS_URL, _rpc().encode())
            # Another LCM (the cluster through the proxy, another host) has not reported.
            for url in (proxied, "https://pc2.example.lab:9440" + GENESIS):
                with pytest.raises(ReadOnlyViolation):
                    transport.check_request("POST", url, _rpc())
        transport._remember_catalogue(proxied, _config_reply())
        with transport.lcm_inventory_permit():
            transport.check_request("POST", proxied, _rpc())
        with pytest.raises(ReadOnlyViolation):
            transport.check_request("POST", GENESIS_URL, _rpc())
    _in_fresh_context(body)


def test_legacy_inventory_is_bound_to_the_reported_catalogue() -> None:
    def body() -> None:
        transport._remember_catalogue(GENESIS_URL, _config_reply())
        with transport.lcm_inventory_permit():
            for other in ("https://download.example.lab/lcm/2.1", "http://198.51.100.9/x"):
                with pytest.raises(ReadOnlyViolation):
                    transport.check_request("POST", GENESIS_URL, _rpc(kw_args=[other]))
            # The binding is per context: another thread has none.
            seen: list[BaseException] = []

            def other_thread() -> None:
                try:
                    with transport.lcm_inventory_permit():
                        transport.check_request("POST", GENESIS_URL, _rpc())
                except ReadOnlyViolation as exc:
                    seen.append(exc)
            t = threading.Thread(target=other_thread)
            t.start()
            t.join()
            assert len(seen) == 1
    _in_fresh_context(body)


@pytest.mark.parametrize("reply", [
    _config_reply('https://download.example.lab/"x'),     # not a valid catalogue
    _config_reply("file:///etc/passwd"),
    b'{"value": "{\\".return\\": {\\"url\\": \\"https://a/1\\", \\"url\\": \\"https://a/2\\"}}"}',
    b'{"value": "x", "value": "y"}',
    b"\xff\xfe",
])
def test_bad_config_replies_record_nothing(reply: bytes) -> None:
    def body() -> None:
        transport._remember_catalogue(GENESIS_URL, reply)
        assert not transport._catalogues.get()
    _in_fresh_context(body)


def test_legacy_reads_pass_without_the_permit() -> None:
    import json
    for read in ("get_config", "is_lcm_operation_in_progress"):
        transport.check_request("POST", GENESIS_URL,
                                json.dumps(transport.genesis_body(read)))
        transport.check_request("POST", GENESIS_URL, _rpc(kw_method=read, kw_args=[]))


@pytest.mark.parametrize("body", [
    _rpc(_oid="ClusterManager"),
    _rpc(_method="cluster_rpc"),
    _rpc(kw_method_class="LcmUpgrade"),
    _rpc(kw_method="perform_update"),
    _rpc(kw_method="perform_upgrade"),
    _rpc(kw_method="set_config"),
    _rpc(kw_method="get_config", kw_args=["x"]),          # a read with arguments
    _rpc(kw_args=[CATALOGUE, CATALOGUE]),                 # two arguments
    _rpc(kw_args=[]),                                     # no catalogue
    _rpc(kw_args=["file:///etc/passwd"]),                 # not a catalogue URL
    _rpc(kw_args=[{"url": CATALOGUE}]),
    _rpc(kw_args=None),
    _rpc(kw_extra=1),                                     # extra kwargs key
    _rpc(_extra=1),                                       # extra key in the value
    _rpc(_kwargs=None, kwargs={"method_class": "LcmFramework",
                               "method": "perform_inventory", "args": [CATALOGUE]}),
    '{"value": "x", "other": 1}',
    # duplicate keys at every level (parsers disagree on which one wins)
    _dup_value(json.dumps({".oid": "X"}), json.loads(_rpc())["value"]),
    _dup_value(json.loads(_rpc())["value"], json.dumps({".oid": "X"})),
    json.dumps({"value": '{".oid": "LifeCycleManager", ".oid": "LifeCycleManager", '
                         '".method": "lcm_framework_rpc", ".kwargs": {"method_class": '
                         '"LcmFramework", "method": "get_config"}}'}),
    json.dumps({"value": '{".oid": "LifeCycleManager", ".method": "lcm_framework_rpc", '
                         '".kwargs": {"method_class": "LcmFramework", "method": '
                         '"stop_service", "method": "get_config"}}'}),
    json.dumps({"value": '{".oid": "LifeCycleManager", ".method": "lcm_framework_rpc", '
                         '".kwargs": {"method_class": "LcmFramework", "method": '
                         '"get_config", "method": "perform_update"}}'}),
    json.dumps({"value": '{".oid": "LifeCycleManager", ".method": "lcm_framework_rpc", '
                         '".kwargs": {"method_class": "LcmFramework", "method": '
                         '"perform_inventory", "args": [{"u": 1, "u": 2}]}}'}),
    json.dumps({"value": '{".oid": NaN}'}),
    "[]",
    # encodings and body types other than str / strict UTF-8 bytes
    _rpc(kw_method="get_config", kw_args=None).encode("utf-16"),
    _rpc(kw_method="get_config", kw_args=None).encode("utf-32"),
    bytearray(_rpc(kw_method="get_config", kw_args=None).encode()),
    memoryview(_rpc(kw_method="get_config", kw_args=None).encode()),
    "\ufeff" + _rpc(kw_method="get_config", kw_args=None),
    b"\xef\xbb\xbf" + _rpc(kw_method="get_config", kw_args=None).encode(),
    io.BytesIO(_rpc(kw_method="get_config", kw_args=None).encode()),
    (x for x in [_rpc(kw_method="get_config", kw_args=None).encode()]),
    json.dumps({"value": '{".oid": "LifeCycleManager", ".method": "lcm_framework_rpc", '
                         '".kwargs": {"method_class": "LcmFramework", "method": '
                         '"get_config", "args": null}}'}),     # args: null on a read
    # catalogue URLs: printable ASCII only, no quotes or backslashes
    _rpc(kw_args=["https://x/y z"]),
    _rpc(kw_args=["https://x/\u00e9"]),
    _rpc(kw_args=["https://x/\x00y"]),
    _rpc(kw_args=["https://x/a\\b"]),
    _rpc(kw_args=['https://x/a"b']),
    _rpc(kw_args=["https://x/a'b"]),
    _rpc(kw_args=["HTTPS://x/"]),
    _rpc(kw_args=["https://" + "a" * 501]),
    '{"value": {"not": "a string"}}',
    "value=%7B%7D",                                       # form encoded
    "not json",
    "",
    None,
])
def test_genesis_refuses_every_other_body(body: Any) -> None:
    with transport.lcm_inventory_permit(), pytest.raises(ReadOnlyViolation):
        transport.check_request("POST", GENESIS_URL, body)


@pytest.mark.parametrize("url", [
    f"{GENESIS_URL}?proxyClusterUuid=abc&x=1",
    f"{GENESIS_URL}?proxyClusterUuid=abc-123",            # not a uuid
    f"{GENESIS_URL}?proxyClusterUuid={CLUSTER}%0a",
    f"{GENESIS_URL}?other=1",
    f"{GENESIS_URL}?",
    f"{GENESIS_URL}#x",
    f"{GENESIS_URL}/extra",
    f"{GENESIS_URL};x=1",
    f"{GENESIS_URL}/",
    "https://pc.example.lab:9440//PrismGateway/services/rest/v1/genesis",
    "https://pc.example.lab:9440/PrismGateway/services/rest/v1/gen%65sis",
    "https://pc.example.lab:9440/PrismGateway/services/rest/v1%2Fgenesis",
    "https://pc.example.lab:9440/PrismGateway/services/rest/v1/Genesis",
    "https://pc.example.lab:9440/PrismGateway/services/rest/v2.0/genesis",
])
def test_genesis_refuses_other_urls(url: str) -> None:
    with transport.lcm_inventory_permit(), pytest.raises(ReadOnlyViolation):
        transport.check_request("POST", url, _rpc())


@pytest.mark.parametrize("method,body", [("GET", None), ("HEAD", None), ("GET", _rpc()),
                                         ("post", _rpc(kw_method="perform_update"))])
def test_genesis_is_never_reached_another_way(method: str, body: Any) -> None:
    with pytest.raises(ReadOnlyViolation):
        transport.check_request(method, GENESIS_URL, body)


def test_a_read_carries_no_body() -> None:
    with pytest.raises(ReadOnlyViolation):
        transport.check_request("GET", "https://pc.example.lab:9440/api/nutanix/v3/vms",
                                b'{"x": 1}')


def test_streamed_bodies_are_refused_not_crashed() -> None:
    prepared = requests.Request("POST", GENESIS_URL, data=iter([b"x"])).prepare()
    with pytest.raises(ReadOnlyViolation):
        transport.check_request("POST", GENESIS_URL, prepared.body)


def test_task_ids_must_be_uuids(monkeypatch: pytest.MonkeyPatch) -> None:
    """A status reply naming a non-uuid task: busy, unidentified; no task URL is built."""
    from ntnxvision_core.lcm_inventory import LegacyLcmInventory

    inv = LegacyLcmInventory(lambda: None, [])
    asked: list[str] = []

    class Conn:
        class creds:  # noqa: N801
            base = "https://pc.example.lab:9440"

        def get_json(self, url: str, **kw: Any) -> dict[str, Any]:
            asked.append(url)
            return {}

    for bad in ("../../vms/abc", "abc?x=1", "0005f0a1-aaaa-bbbb-cccc-00000000000g"):
        monkeypatch.setattr(inv, "_genesis", lambda *a, _b=bad, **k: _b)
        assert inv._read_status(Conn(), inv.run.targets[0])[0] == "OPERATION"
        with pytest.raises(NutanixApiError):
            inv._v3_task(Conn(), bad)
    assert asked == []


def test_guard_runs_before_anything_is_sent() -> None:
    """The session refuses before the adapter is reached: nothing leaves the process."""
    sent: list[str] = []

    class Adapter(requests.adapters.HTTPAdapter):
        def send(self, request: Any, **kw: Any) -> Any:  # pragma: no cover - must not run
            sent.append(request.url)
            raise AssertionError("sent")

    session = transport.new_session()
    session.mount("https://", Adapter())
    with pytest.raises(ReadOnlyViolation):
        session.post("https://pc.example.lab:9440/api/vmm/v4.0/ahv/config/vms", json={})
    assert sent == []


_PKG = Path(ntnxvision_core.__file__).parent
#: Modules allowed to issue a non-GET request, and why.
_NON_GET = {
    "api/connection.py",      # v3 list / groups POSTs (reads), via post_json and _send
    "api/vcenter_client.py",  # vSphere API session login / logout
    "lcm_inventory.py",       # the LCM inventory, inside the permit
}


def _sources() -> list[tuple[str, ast.AST]]:
    out = []
    for path in sorted(_PKG.rglob("*.py")):
        rel = path.relative_to(_PKG).as_posix()
        if rel.startswith("synthetic/"):
            continue  # the mock serves requests; it never sends any to a real system
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
    return out


def test_only_the_inventory_module_enters_the_permit() -> None:
    users = {rel for rel, tree in _sources() for node in ast.walk(tree)
             if isinstance(node, ast.Name | ast.Attribute)
             and (getattr(node, "id", None) or getattr(node, "attr", None))
             == "lcm_inventory_permit"}
    assert users == {"lcm_inventory.py"}


def test_the_permit_wraps_exactly_the_two_inventory_requests() -> None:
    """lcm_inventory.py: one permit block per driver (v4, Genesis), each in its
    `_start_inventory` and around exactly one `_send`; the Genesis one names the
    inventory method. No other module builds the inventory RPC."""
    tree = dict(_sources())["lcm_inventory.py"]
    blocks: list[tuple[str, ast.With]] = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.ClassDef):
            for item in fn.body:
                if isinstance(item, ast.FunctionDef):
                    for node in ast.walk(item):
                        if isinstance(node, ast.With) and any(
                                "lcm_inventory_permit" in ast.unparse(w.context_expr)
                                for w in node.items):
                            blocks.append((f"{fn.name}.{item.name}", node))
    assert sorted(name for name, _ in blocks) == [
        "LcmInventory._start_inventory", "LegacyLcmInventory._start_inventory"]
    for name, block in blocks:
        sends = [n for n in ast.walk(block) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "_send"]
        assert len(sends) == 1, name
        if name.startswith("Legacy"):
            assert "retry_429=False" in ast.unparse(sends[0])
    legacy = next(fn for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                  and fn.name == "_start_inventory"
                  and "GENESIS_INVENTORY_METHOD" in ast.unparse(fn))
    assert "genesis_body(GENESIS_INVENTORY_METHOD" in ast.unparse(legacy)
    users = {rel for rel, tree in _sources() if rel != "api/transport.py"
             and ("GENESIS_INVENTORY_METHOD" in ast.unparse(tree)
                  or "perform_inventory" in ast.unparse(tree))}
    assert users == {"lcm_inventory.py"}


def test_no_other_code_path_issues_a_non_get_request() -> None:
    offenders = set()
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = node.func.attr
                if name in ("post", "put", "patch", "delete") and rel not in _NON_GET \
                        and "session" in ast.unparse(node.func.value):
                    offenders.add(f"{rel}: {ast.unparse(node)[:60]}")
                if name in ("_send", "request") and node.args and \
                        isinstance(node.args[0], ast.Constant) and \
                        str(node.args[0].value).upper() not in ("GET", "HEAD") and \
                        rel not in _NON_GET:
                    offenders.add(f"{rel}: {ast.unparse(node)[:60]}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "post_json" and rel not in _NON_GET:
                url = ast.unparse(node.args[0]) if node.args else ""
                if "/list" not in url and "groups" not in url:
                    offenders.add(f"{rel}: {ast.unparse(node)[:60]}")
    assert offenders == set()


class _Recorder:
    """Records every request that passes the guard during a run."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str]] = []
        #: Genesis RPCs that passed: (path with query, LCM framework method).
        self.genesis: list[tuple[str, str | None]] = []
        real = transport.check_request

        def check(method: str | None, url: str | None, body: Any = None) -> None:
            real(method, url, body)
            from urllib.parse import unquote, urlsplit
            parts = urlsplit(url or "")
            path = unquote(parts.path)
            self.calls.append(((method or "GET").upper(), path))
            if path == GENESIS:
                self.genesis.append((f"{path}?{parts.query}" if parts.query else path,
                                     transport.genesis_rpc(body)))
        monkeypatch.setattr(transport, "check_request", check)

    def writes(self) -> list[tuple[str, str]]:
        """Non-read requests; a Genesis RPC is one unless it is a known read method."""
        reads = transport.GENESIS_READ_METHODS
        out = [(m, p) for m, p in self.calls if m != "GET" and not p.endswith("/list")
               and not p.endswith("/groups") and p != GENESIS]
        out += [("POST", p) for p, rpc in self.genesis if rpc not in reads]
        return out


def test_without_the_option_a_run_only_reads(demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder(monkeypatch)
    env = _collect("pc01.demo.local")
    assert rec.calls and rec.writes() == []
    assert env.lcm_inventory is None and "lcm_inventory" not in env.collection_notes
    assert _lcm(demo).started == []


def test_with_the_option_the_only_write_is_the_inventory(
        demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _lcm(demo).seconds = 0.2
    rec = _Recorder(monkeypatch)
    env = _collect("pc02.demo.local", lcm_inventory=FAST)
    writes = rec.writes()
    assert writes and {w for w in writes} == {("POST", INVENTORY)}
    # One per target: Prism Central plus each cluster.
    assert len(writes) == len(env.lcm_inventory.targets) == 1 + len(env.clusters)


# ─── routing and outcomes ──────────────────────────────────────────────


def test_success_joined_and_upgrade_on_pc01(demo: Any) -> None:
    """pc01: PC and two clusters idle (inventory started), one cluster with an inventory
    already running (joined), one with an upgrade running (skipped, details reported)."""
    _lcm(demo).seconds = 0.2
    events: list[dict[str, Any]] = []
    tracker = RunTracker(events.append, run_id="r", host="pc01.demo.local")
    env = _collect("pc01.demo.local", lcm_inventory=FAST, tracker=tracker)
    inv = env.lcm_inventory
    assert inv.requested and inv.api == "v4" and inv.duration_ms is not None
    statuses = {t.name: (t.status, t.joined) for t in inv.targets}
    assert statuses["Prism Central"] == ("succeeded", False)
    joined = [t for t in inv.targets if t.joined]
    assert len(joined) == 1 and joined[0].status == "succeeded" and joined[0].task_id
    busy = [t for t in inv.targets if t.status == "skipped_busy"]
    assert len(busy) == 1
    b = busy[0].busy
    assert b is not None and b.operation == "UPGRADE" and b.task_id
    assert b.percent is not None and b.started_at
    assert {(c.name, c.from_version, c.to_version) for c in b.components} >= {
        ("AOS", "7.0", "7.3")}
    assert busy[0].detail.startswith("upgrade in progress (AOS 7.0 to 7.3")
    # Every target that ran has its own task, and the PC's own LCM is addressed too.
    assert sum(1 for t in inv.targets if t.task_id and not t.joined and not t.busy) == \
        len(inv.targets) - 2 == inv.started_count
    # The started and joined inventories finished before LCM was read; the busy cluster
    # was never inventoried.
    done = {k for (h, k), _ in _lcm(demo).inventoried.items() if h == "pc01.demo.local"}
    assert busy[0].target_id not in done
    assert done == {t.target_id or "pc" for t in inv.targets if t.status == "succeeded"}
    note = env.collection_notes["lcm_inventory"]
    assert "joined running inventory, succeeded" in note
    assert "skipped: upgrade in progress (AOS 7.0 to 7.3" in note
    # Progress: typed events per change, and the LCM area's detail while it waits.
    inv_events = [e for e in events if e["type"] == "lcm_inventory"]
    assert inv_events and inv_events[-1]["inventory"]["targets"][0]["status"] == "succeeded"
    details = [e["area"]["detail"] for e in events
               if e["type"] == "area" and e["area"]["id"] == "lcm" and e["area"]["detail"]]
    assert any("LCM inventory running on" in d and "%" in d for d in details)
    started = next(e for e in events if e["type"] == "started")
    assert started["typical_seconds"] == 240 + 600
    # LCM ran last, after everything else.
    assert started["areas"][-1]["id"] == "lcm"


def test_below_v4_ga_uses_the_legacy_inventory(demo: Any,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """pc03 (pc.2024.1): the inventory goes through Genesis; the only writes are the
    perform_inventory RPCs, one per target, each with the exact body."""
    _lcm(demo).seconds = 0.2
    rec = _Recorder(monkeypatch)
    env = _collect("pc03.demo.local", lcm_inventory=FAST)
    inv = env.lcm_inventory
    assert inv.requested and inv.api == "genesis" and inv.targets
    assert {t.status for t in inv.targets} == {"succeeded"}
    assert all(t.task_id and ":" not in t.task_id for t in inv.targets)
    writes = rec.writes()
    assert {w[1].split("?")[0] for w in writes} == {GENESIS}
    assert len(writes) == len(inv.targets) == 1 + len(env.clusters) == len(_lcm(demo).started)
    # PC's own LCM without the proxy; every cluster through it, once.
    inv_paths = sorted(p for p, rpc in rec.genesis if rpc == "perform_inventory")
    assert inv_paths.count(GENESIS) == 1
    assert sorted(p.split("proxyClusterUuid=")[1] for p in inv_paths if "?" in p) == \
        sorted(c.uuid for c in env.clusters)
    # Status and config were read before each inventory; no v4 lifecycle request was made.
    methods = [rpc for _, rpc in rec.genesis]
    assert methods.count("is_lcm_operation_in_progress") == len(inv.targets)
    assert methods.count("get_config") == len(inv.targets)
    assert not any("lifecycle/v4" in p for _, p in rec.calls if _ != "GET")
    note = env.collection_notes["lcm_inventory"]
    assert note.startswith("ok: Prism Central: succeeded") and "legacy LCM interface" in note
    assert "—" not in note


def test_legacy_busy_joined_and_upgrade(demo: Any) -> None:
    host = "pc03.demo.local"
    probe = _collect(host)  # cluster uuids
    first, *rest = [c.uuid for c in probe.clusters]
    running = {"pc": "inventory", first: "upgrade"}
    _lcm(demo).script(host, running=running, seconds=0.3)
    env = _collect(host, lcm_inventory=FAST)
    t = {x.target_id or "pc": x for x in env.lcm_inventory.targets}
    assert t["pc"].joined and t["pc"].status == "succeeded"
    assert t[first].status == "skipped_busy" and t[first].busy is not None
    assert t[first].busy.operation == "UPGRADE" and t[first].busy.task_id
    assert t[first].detail.startswith("upgrade in progress (Upgrade root task")
    assert (host, first) not in _lcm(demo).started and (host, "pc") not in _lcm(demo).started
    assert all(t[c].status == "succeeded" for c in rest)


@pytest.mark.parametrize("outcome,status,text", [
    ("forbidden", "forbidden", "Perform Inventory permission"),
    ("status_forbidden", "forbidden", "LCM View Status"),
    ("failed", "failed", "precheck"),
])
def test_legacy_outcomes(demo: Any, outcome: str, status: str, text: str) -> None:
    host = "pc03.demo.local"
    _lcm(demo).script(host, outcome={"*": outcome}, seconds=0.1)
    env = _collect(host, lcm_inventory=FAST)
    targets = env.lcm_inventory.targets
    assert {x.status for x in targets} == {status}
    assert text in targets[0].detail and "\n" not in targets[0].detail
    if outcome != "failed":
        assert _lcm(demo).started == []


def test_legacy_unconfirmed_catalogue_is_refused_per_target(demo: Any) -> None:
    """LCM reports a catalogue the guard does not accept: no inventory is sent."""
    _lcm(demo).catalogue = "https://download.example.lab/lcm/2.0 x"
    env = _collect("pc03.demo.local", lcm_inventory=FAST)
    assert {t.status for t in env.lcm_inventory.targets} == {"failed"}
    assert "refused before it was sent" in env.lcm_inventory.targets[0].detail
    assert _lcm(demo).started == []


def test_unsupported_version_sends_nothing(demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither v4 lifecycle nor a legacy version (pc.2023.4): nothing is attempted."""
    rec = _Recorder(monkeypatch)
    env = _collect("legacy-pc.demo.local", lcm_inventory=FAST)
    assert rec.writes() == [] and rec.genesis == [] and _lcm(demo).started == []
    assert env.lcm_inventory.requested and not env.lcm_inventory.targets
    assert "no supported legacy LCM interface" in env.lcm_inventory.detail
    assert env.collection_notes["lcm_inventory"].startswith("skipped: the v4 lifecycle API")


def test_lcm_area_not_selected_is_a_note(demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder(monkeypatch)
    env = _collect("pc02.demo.local", lcm_inventory=FAST, areas=["clusters"])
    assert rec.writes() == [] and rec.genesis == [] and _lcm(demo).started == []
    assert env.lcm_inventory.requested and not env.lcm_inventory.targets
    assert env.lcm_inventory.detail.startswith("LCM area not selected")
    assert env.collection_notes["lcm_inventory"].startswith("skipped: LCM area not selected")


def test_an_error_outside_the_collectors_stops_the_polling(
        demo: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not a cancel: the run fails some other way; the worker must not poll on."""
    import ntnxvision_core.engine as engine

    host = "pc02.demo.local"
    _lcm(demo).script(host, outcome={"*": "stuck"}, seconds=60)
    made: list[Any] = []
    real_cls = engine.LcmInventory

    class Spy(real_cls):  # type: ignore[misc, valid-type]
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            made.append(self)
    monkeypatch.setattr(engine, "LcmInventory", Spy)
    real_run_area = engine._run_area
    calls = {"n": 0}

    def failing(*a: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            time.sleep(0.3)  # let the inventory start polling
            raise RuntimeError("boom")
        return real_run_area(*a, **kw)
    monkeypatch.setattr(engine, "_run_area", failing)
    with pytest.raises(RuntimeError):
        _collect(host, lcm_inventory=InventoryOptions(poll_initial_s=0.05, poll_max_s=0.1))
    assert made
    worker = made[0]._thread
    assert worker is not None
    worker.join(5.0)
    assert not worker.is_alive()
    assert {t.status for t in made[0].result().targets} == {"cancelled"}
    assert "collection ended before the inventory finished" in \
        made[0].result().targets[0].detail


def test_forbidden_is_a_note_not_a_failure(demo: Any) -> None:
    _lcm(demo).seconds = 0.2
    env = _collect("tiny.demo.local", lcm_inventory=FAST)
    assert {t.status for t in env.lcm_inventory.targets} == {"forbidden"}
    assert "Perform Inventory permission" in env.lcm_inventory.targets[0].detail
    assert env.lcm_entities and env.collection_notes["lcm_entities"].startswith("ok")


def test_status_forbidden(demo: Any) -> None:
    _lcm(demo).script("pc02.demo.local", outcome={"*": "status_forbidden"})
    env = _collect("pc02.demo.local", lcm_inventory=FAST)
    assert {t.status for t in env.lcm_inventory.targets} == {"forbidden"}
    assert _lcm(demo).started == []


def test_failure_carries_lcm_message(demo: Any) -> None:
    host = "pc02.demo.local"
    _lcm(demo).script(host, outcome={"pc": "failed"}, seconds=0.1)
    env = _collect(host, lcm_inventory=FAST)
    t = _by_name(env)["Prism Central"]
    assert t.status == "failed" and "precheck" in t.detail and "\n" not in t.detail
    assert all(x.status == "succeeded" for x in env.lcm_inventory.targets if x is not t)
    assert "Prism Central: failed" in env.collection_notes["lcm_inventory"]


def test_timeout_stops_waiting(demo: Any) -> None:
    host = "pc02.demo.local"
    _lcm(demo).script(host, outcome={"*": "stuck"}, seconds=0.5)
    opts = InventoryOptions(timeout_s=0.4, poll_initial_s=0.05, poll_max_s=0.1)
    t0 = time.monotonic()
    env = _collect(host, lcm_inventory=opts)
    assert time.monotonic() - t0 < 30
    assert {t.status for t in env.lcm_inventory.targets} == {"timed_out"}
    assert "keeps running on Prism Central" in env.lcm_inventory.targets[0].detail
    assert env.lcm_entities  # collected anyway, with the values LCM had


def test_cancel_stops_polling(demo: Any) -> None:
    host = "pc02.demo.local"
    _lcm(demo).script(host, outcome={"*": "stuck"}, seconds=60)
    token = CancelToken()
    threading.Timer(1.0, token.cancel).start()
    with pytest.raises(RunCancelled):
        _collect(host, lcm_inventory=InventoryOptions(poll_initial_s=0.05, poll_max_s=0.1),
                 cancel=token)
    hits = dict(demo.api.hits)
    time.sleep(0.5)
    polls = {k: v for k, v in demo.api.hits.items() if "config/tasks" in k[2]}
    assert polls == {k: v for k, v in hits.items() if "config/tasks" in k[2]}


def test_combined_runs_keep_every_pcs_targets(demo: Any) -> None:
    _lcm(demo).seconds = 0.2
    a = _collect("pc02.demo.local", lcm_inventory=FAST)
    b = _collect("tiny.demo.local", lcm_inventory=FAST)
    combined = combine_environments([a, b])
    inv = combined.lcm_inventory
    assert inv is not None and len(inv.targets) == len(a.lcm_inventory.targets) + \
        len(b.lcm_inventory.targets)
    assert {t.source_pc for t in inv.targets} == {"pc02.demo.local", "tiny.demo.local"}


# ─── sidecar: parameters, run metadata, reports ────────────────────────


def _run(data_dir: Path, host: str, name: str, **extra: Any) -> dict[str, Any]:
    return sc._m_inventory(None, {"host": host, **PW, "port": 9440, "insecure": False,
                                  "name": name, "formats": [],
                                  "export_dir": str(data_dir / "exports"), **extra})


@pytest.mark.parametrize("extra", [
    {"lcm_inventory": "yes"}, {"lcm_inventory": 1},
    {"lcm_inventory": True, "lcm_inventory_timeout_s": 5},
    {"lcm_inventory": True, "lcm_inventory_timeout_s": True},
])
def test_sidecar_refuses_bad_options(demo: Any, data_dir: Path, extra: dict[str, Any]) -> None:
    with pytest.raises(NutanixApiError):
        _run(data_dir, "pc02.demo.local", "bad", **extra)
    assert _lcm(demo).started == []


def test_sidecar_records_the_inventory_on_the_snapshot(demo: Any, data_dir: Path) -> None:
    _lcm(demo).seconds = 0.2
    off = _run(data_dir, "pc02.demo.local", "lcm off")
    assert off["lcm_inventory"] == {"requested": False}
    on = _run(data_dir, "pc02.demo.local", "lcm on", lcm_inventory=True,
              lcm_inventory_timeout_s=600, formats=["excel", "pdf", "word"])
    inv = on["lcm_inventory"]
    assert inv["requested"] and inv["timeout_s"] == 600
    assert all(t["status"] == "succeeded" and t["task_id"] and t["duration_ms"] is not None
               for t in inv["targets"])
    assert on["collection_notes"]["lcm_inventory"].startswith("ok: Prism Central: succeeded")
    detail = sc._m_get_snapshot(None, {"snapshot": on["snapshot_path"]})
    assert detail["run"]["lcm_inventory"]["targets"] == inv["targets"]
    # Reports: the provenance line says what was done, in all three formats.
    n = len(inv["targets"])
    line = f"an LCM inventory was run on {n} targets before collection"
    _assert_reports_say(on["exports"], line, on["snapshot_path"])
    off_files = sc._m_export(None, {"snapshot": off["snapshot_path"],
                                    "formats": ["excel", "pdf", "word"],
                                    "export_dir": str(data_dir / "off")})["paths"]
    _assert_reports_say(off_files, "read-only inventory snapshot; no configuration was changed",
                        off["snapshot_path"])


def _assert_reports_say(paths: dict[str, str], text: str, snapshot: str) -> None:
    """Word and Excel files as written; the PDF through the content both PDF and Word
    render (fpdf2 compresses its text streams)."""
    import re
    import zipfile

    from openpyxl import load_workbook

    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import build_report
    from ntnxvision_core.store import SnapshotStore

    with zipfile.ZipFile(paths["word"]) as z:
        xml = " ".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                       if n.endswith(".xml"))
    word = " ".join(re.sub(r"<[^>]+>", "", xml).split())
    assert text in word
    wb = load_workbook(paths["excel"])
    cells = " ".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows()
                     for c in row if c.value)
    assert text in cells
    env = SnapshotStore(Path(snapshot).parent).load(snapshot)
    cover = build_report(env, ExportOptions.coerce(None), eyebrow="x").cover
    assert cover.note is not None and text in cover.note
    assert Path(paths["pdf"]).stat().st_size > 10_000


def test_multi_pc_option_applies_to_every_target(demo: Any, data_dir: Path) -> None:
    _lcm(demo).seconds = 0.2
    res = sc._m_inventory_multi(None, {
        "targets": [{"host": h, **PW, "port": 9440, "insecure": False, "name": h[:4]}
                    for h in ("pc02.demo.local", "tiny.demo.local")],
        "formats": [], "export_dir": str(data_dir / "x"), "combine": True,
        "combined_name": "both", "lcm_inventory": True})
    assert all(t["lcm_inventory"]["requested"] for t in res["targets"])
    assert len(res["combined"]["lcm_inventory"]["targets"]) == sum(
        len(t["lcm_inventory"]["targets"]) for t in res["targets"])


# ─── wording ───────────────────────────────────────────────────────────


def test_wording_has_no_em_dash_and_reads_well() -> None:
    run = LcmInventoryRun(requested=True, targets=[
        LcmInventoryTarget(kind="prism_central", name="Prism Central", status="succeeded",
                           task_id="t1", duration_ms=125_000),
        LcmInventoryTarget(name="C1", status="timed_out", task_id="t2",
                           detail="Still running after 30 min"),
    ])
    assert note_line(run) == ("notes: Prism Central: succeeded in 2 min 5 s; C1: timed out "
                              "(Still running after 30 min)")
    assert provenance(run) == ("Generated from a read-only inventory snapshot; an LCM "
                               "inventory was run on 2 targets before collection, no "
                               "configuration was changed.")
    assert provenance(None) == ("Generated from a read-only inventory snapshot; no "
                                "configuration was changed.")
    assert provenance(run, None, snapshots=2).startswith(
        "Generated from read-only inventory snapshots; an LCM inventory was run on 2 targets")
    from ntnxvision_core.models import LcmBusyOperation
    assert busy_sentence(LcmBusyOperation(operation="PRECHECKS", percent=10)) == \
        "LCM prechecks in progress (10%)"
    assert "—" not in note_line(run) + provenance(run)
    assert task_ref("abc") == "ZXJnb24=:abc" and task_ref("ZXJnb24=:abc") == "ZXJnb24=:abc"
