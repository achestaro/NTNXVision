# Nutanix product icons

These are icons from the **Nutanix product icon set** (`Nutanix Product Icons 2026-09-24 05_12_15/`,
kept outside this repo). Only the colour, icon-only
variant (`Product-Icons-Digital-<Product>-Color.svg`) of the products NTNXVision draws is copied here.
Licence: Nutanix brand assets, for use in Nutanix-related tooling under the Nutanix brand guidelines; not for
redistribution outside the product.

**Generated, do not edit by hand.** Run `core/.venv/bin/python scripts/render-product-icons.py "<icon set folder>"`
from the repo root (needs `pip install pymupdf` for that one run; it is not a dependency). The script keeps the
original paths and colours, crops each viewBox to a square around the drawing, writes `<id>.svg` here and in
`app/src/renderer/assets/product-icons/`, and rasterises `<id>.png` (256 x 256, transparent) for the consumers
that cannot take SVG.

| File | Source file | Product | Used for |
|---|---|---|---|
| `prism.svg` / `.png` | `Product-Icons-Digital-Prism-Color.svg` | Nutanix Prism (Prism Central) | Prism Central, Catalog, IAM, Health, LCM, Licensing sections; Prism Central blocks in diagrams |
| `nci.svg` / `.png` | `Product-Icons-Digital-CloudInfrastructure-Color.svg` | Nutanix Cloud Infrastructure (AOS, AHV) | Logical / Physical Design, Workloads, Networking, Data Protection, Security (Flow) sections; cluster cards |
| `nus.svg` / `.png` | `Product-Icons-Digital-NUS-Color.svg` | Nutanix Unified Storage (Files, Objects, Volumes) | Unified Storage section |
| `ndb.svg` / `.png` | `Product-Icons-Digital-Database-Color.svg` | Nutanix Database Service | NDB section |
| `ncm.svg` / `.png` | `Product-Icons-Digital-CloudManger-Color.svg` | Nutanix Cloud Manager (Self-Service, Intelligent Operations) | Automation & Reporting (NCM) section |
| `nc2.svg` / `.png` | `Product-Icons-Digital-NC2-Color.svg` | Nutanix Cloud Clusters | NC2 cluster cards in diagrams |
| `nkp.svg` / `.png` | `Product-Icons-Digital-NKP-Color.svg` | Nutanix Kubernetes Platform | Diagrams editor icon picker |
| `nai.svg` / `.png` | `Product-Icons-Digital-NAI-Color.svg` | Nutanix Enterprise AI | Diagrams editor icon picker |

The product registry (names, which report section shows which icon, which products an estate contains) is
`ntnxvision_core/exporters/products.py`.
