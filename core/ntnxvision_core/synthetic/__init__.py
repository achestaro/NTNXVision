"""Synthetic Nutanix estates — the way to exercise NTNXVision end to end without a lab.

Three layers, each usable on its own:

* `estate` — a deterministic generator of a canonical estate (clusters, hosts, VMs, DR,
  Flow, NUS, IAM, LCM, …) for a size profile: ``tiny``, ``medium`` (~1 200 VMs on 4
  clusters) or ``large`` (~6 700 VMs per Prism Central; three of them make the 20 000-VM
  estate). Every id is derived from a name, so the same profile always yields the same
  estate, and an ``epoch`` adds a week of drift for Compare.
* `render` — renders that estate as the JSON each API returns, shaped like the
  developers.nutanix.com definitions the collectors are written against: v4 (``data`` /
  ``metadata.totalAvailableResults`` envelopes, ``extId``, ``$objectType``), v3
  (``entities`` + ``metadata.total_matches``, spec/status), the v3 groups API, Prism
  Element v2.0 (``metadata.grand_total_entities``) and NDB/Era (bare arrays).
* `server` — a local HTTPS mock of Prism Central / Prism Element / NDB serving those
  payloads with basic auth, paging, self-signed TLS, 401, 404/503 namespaces, 500s on
  individual records and 429 rate limiting.

`demo` ties them together: it starts the mock server and routes the demo hostnames
(``pc01.demo.local`` …) to it, so the real connection layer and collectors run unchanged.
"""

from ntnxvision_core.synthetic.estate import (
    NdbEstate,
    PcEstate,
    PeEstate,
    build_ndb_estate,
    build_pc_estate,
    build_pe_estate,
)

__all__ = [
    "NdbEstate",
    "PcEstate",
    "PeEstate",
    "build_ndb_estate",
    "build_pc_estate",
    "build_pe_estate",
]
