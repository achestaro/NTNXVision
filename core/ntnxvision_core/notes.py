"""Helpers for presenting collection notes and labels in reports.

Collector error notes can contain multi-line Pydantic validation traces; these helpers
condense them to a single readable line and prettify collector keys for documents.
"""

from __future__ import annotations

_LABELS = {
    "storage_containers": "Storage Containers",
    "vms": "Virtual Machines",
    "flow_networking": "Flow Virtual Networking",
    "flow_security": "Flow Network Security",
    "volume_groups": "Volume Groups",
    "xplay": "X-Play (Playbooks)",
    "ncm_reports": "NCM Reports",
    "data_protection": "Data Protection / DR",
    "iam": "IAM",
    "files": "Nutanix Files",
    "objects": "Nutanix Objects",
    "ndb": "NDB (Databases)",
    "lcm_entities": "LCM Firmware and Software",
    "lcm": "LCM Firmware and Software",
    "ovas": "OVAs",
    "pc_config": "Prism Central Configuration",
    "templates": "VM Templates",
    "alerts": "Health Alerts",
    "licenses": "Licensing",
}


def pretty_label(key: str) -> str:
    """Human-friendly label for a collector key.

    A combined run's keys are ``"<host>: <key>"``; the host is kept verbatim (title-casing
    it produced "Pc02.Demo.Local").
    """
    if ": " in key:
        host, _, inner = key.partition(": ")
        return f"{host} · {pretty_label(inner)}"
    return _LABELS.get(key, key.replace("_", " ").title())


def clean_error(status: str) -> str:
    """Condense a collector error note to a single readable line.

    Turns a multi-line Pydantic trace like
    'error: 5 validation errors for VirtualMachine\\ncategories...  Input should be...'
    into 'could not parse response (5 validation errors for VirtualMachine)'.
    """
    text = status[len("error:"):].strip() if status.startswith("error:") else status
    first_line = text.splitlines()[0].strip() if text else text
    if "validation error" in first_line:
        return f"could not parse the API response ({first_line})"
    if first_line.startswith("Not found"):
        return "endpoint not available on this Prism Central version"
    # Trim overly long messages.
    return first_line if len(first_line) <= 160 else first_line[:157] + "…"
